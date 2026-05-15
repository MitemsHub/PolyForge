from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb
import numpy as np
from loguru import logger
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern
from sklearn.model_selection import ParameterGrid, ParameterSampler

from src.core.config import Settings


@dataclass(frozen=True)
class OptimizationTrial:
    params: dict[str, Any]
    score: float
    metrics: dict[str, Any]


@dataclass(frozen=True)
class OptimizationResult:
    run_id: str
    started_at: str
    finished_at: str
    objective: str
    best: OptimizationTrial
    trials: list[OptimizationTrial]


def _resolve_duckdb_path(db_url: str) -> Path:
    prefix = "duckdb:///"
    if db_url.startswith(prefix):
        return Path(db_url.removeprefix(prefix))
    if db_url.endswith(".duckdb"):
        return Path(db_url)
    return Path("./data/polyforge.duckdb")


class StrategyOptimizer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = _resolve_duckdb_path(settings.db_url)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self._db_path))
        self._init_schema()

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    def _init_schema(self) -> None:
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS optimization_runs (
                run_id VARCHAR PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                objective VARCHAR,
                best_score DOUBLE,
                best_params_json VARCHAR,
                trials_json VARCHAR
            );
            """
        )

    def grid_search(
        self,
        *,
        param_grid: dict[str, list[Any]],
        evaluate: Callable[[dict[str, Any]], tuple[float, dict[str, Any]]],
        objective: str = "score",
        max_trials: int | None = None,
    ) -> OptimizationResult:
        grid = list(ParameterGrid(param_grid))
        if max_trials is not None:
            grid = grid[: int(max_trials)]
        return self._run_search(grid, evaluate=evaluate, objective=objective)

    def random_search(
        self,
        *,
        param_distributions: dict[str, Any],
        evaluate: Callable[[dict[str, Any]], tuple[float, dict[str, Any]]],
        n_iter: int = 50,
        objective: str = "score",
        seed: int | None = None,
    ) -> OptimizationResult:
        rng = np.random.default_rng(int(seed if seed is not None else self._settings.backtest_seed))
        sampler = list(ParameterSampler(param_distributions, n_iter=int(n_iter), random_state=int(rng.integers(0, 2**31 - 1))))
        return self._run_search(sampler, evaluate=evaluate, objective=objective)

    def bayesian_search(
        self,
        *,
        bounds: dict[str, tuple[float, float]],
        evaluate: Callable[[dict[str, Any]], tuple[float, dict[str, Any]]],
        n_init: int = 10,
        n_iter: int = 30,
        objective: str = "score",
        seed: int | None = None,
    ) -> OptimizationResult:
        rng = np.random.default_rng(int(seed if seed is not None else self._settings.backtest_seed))
        keys = list(bounds.keys())
        low = np.array([bounds[k][0] for k in keys], dtype=float)
        high = np.array([bounds[k][1] for k in keys], dtype=float)

        def sample(n: int) -> np.ndarray:
            return low + (high - low) * rng.random((n, len(keys)))

        X = []
        y = []
        trials: list[OptimizationTrial] = []

        for x in sample(int(n_init)):
            params = {k: float(v) for k, v in zip(keys, x, strict=False)}
            score, metrics = evaluate(params)
            X.append(x)
            y.append(score)
            trials.append(OptimizationTrial(params=params, score=float(score), metrics=metrics))

        kernel = Matern(nu=2.5)
        gp = GaussianProcessRegressor(kernel=kernel, alpha=1e-6, normalize_y=True, random_state=int(rng.integers(0, 2**31 - 1)))

        for _ in range(int(n_iter)):
            X_arr = np.array(X, dtype=float)
            y_arr = np.array(y, dtype=float)
            gp.fit(X_arr, y_arr)

            cand = sample(256)
            mu, std = gp.predict(cand, return_std=True)
            best = float(np.max(y_arr))
            z = (mu - best) / (std + 1e-12)
            ei = (mu - best) * norm.cdf(z) + std * norm.pdf(z)
            x_next = cand[int(np.argmax(ei))]

            params = {k: float(v) for k, v in zip(keys, x_next, strict=False)}
            score, metrics = evaluate(params)
            X.append(x_next)
            y.append(score)
            trials.append(OptimizationTrial(params=params, score=float(score), metrics=metrics))

        return self._finalize(trials, objective=objective)

    def _run_search(
        self,
        param_sets: list[dict[str, Any]],
        *,
        evaluate: Callable[[dict[str, Any]], tuple[float, dict[str, Any]]],
        objective: str,
    ) -> OptimizationResult:
        trials: list[OptimizationTrial] = []
        for params in param_sets:
            score, metrics = evaluate(params)
            trials.append(OptimizationTrial(params=dict(params), score=float(score), metrics=metrics))
        return self._finalize(trials, objective=objective)

    def _finalize(self, trials: list[OptimizationTrial], *, objective: str) -> OptimizationResult:
        if not trials:
            raise ValueError("No trials executed")

        best = max(trials, key=lambda t: t.score)
        run_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        finished = datetime.now(timezone.utc)

        try:
            self._con.execute(
                """
                INSERT OR REPLACE INTO optimization_runs(
                    run_id, started_at, finished_at, objective, best_score, best_params_json, trials_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    started,
                    finished,
                    objective,
                    float(best.score),
                    json.dumps(best.params, default=str),
                    json.dumps([{"params": t.params, "score": t.score, "metrics": t.metrics} for t in trials], default=str),
                ],
            )
        except Exception as e:
            logger.warning("Failed to persist optimization run: {}", e)

        return OptimizationResult(
            run_id=run_id,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            objective=objective,
            best=best,
            trials=trials,
        )
