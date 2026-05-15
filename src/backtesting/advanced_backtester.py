from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd
from loguru import logger
from tqdm import tqdm

from src.core.config import Settings
from src.core.models import Trade, TradeSignal
from src.data.data_api_client import DataAPIClient
from src.data.gamma_client import GammaClient


@dataclass(frozen=True)
class AdvancedMetrics:
    sharpe: float
    sortino: float
    calmar: float
    profit_factor: float
    win_rate: float
    expectancy: float
    max_drawdown: float
    recovery_factor: float
    omega_ratio: float
    cagr: float


@dataclass(frozen=True)
class MonteCarloResult:
    paths: int
    horizon_steps: int
    max_drawdown_p50: float
    max_drawdown_p95: float
    terminal_return_p50: float
    terminal_return_p05: float


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    metrics: AdvancedMetrics
    monte_carlo: MonteCarloResult | None


def _resolve_duckdb_path(db_url: str) -> Path:
    prefix = "duckdb:///"
    if db_url.startswith(prefix):
        return Path(db_url.removeprefix(prefix))
    if db_url.endswith(".duckdb"):
        return Path(db_url)
    return Path("./data/polyforge.duckdb")


class AdvancedBacktester:
    def __init__(
        self,
        settings: Settings,
        *,
        gamma: GammaClient | None = None,
        data_api: DataAPIClient | None = None,
    ) -> None:
        self._settings = settings
        self._gamma = gamma
        self._data_api = data_api
        self._db_path = _resolve_duckdb_path(settings.db_url)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self._db_path))
        self._init_cache_schema()

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    def _init_cache_schema(self) -> None:
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_prices (
                market_id VARCHAR,
                token_id VARCHAR,
                timestamp TIMESTAMP,
                price DOUBLE,
                volume DOUBLE,
                source VARCHAR,
                PRIMARY KEY(market_id, token_id, timestamp)
            );
            """
        )

    def load_historical_prices(
        self,
        *,
        market_id: str,
        token_ids: list[str],
        start: datetime,
        end: datetime,
        freq: str = "15min",
        allow_synthetic: bool = True,
    ) -> pd.DataFrame:
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)

        cached = self._load_cached_prices(market_id=market_id, token_ids=token_ids, start=start, end=end)
        if cached is not None and not cached.empty:
            return cached

        df = self._fetch_prices_best_effort(
            market_id=market_id,
            token_ids=token_ids,
            start=start,
            end=end,
            freq=freq,
            allow_synthetic=allow_synthetic,
        )
        if not df.empty:
            self._write_cached_prices(df)
        return df

    def simulate(
        self,
        *,
        prices: pd.DataFrame,
        signals: list[TradeSignal],
        initial_capital: float | None = None,
        slippage_bps: int | None = None,
        impact_coeff: float | None = None,
        fee_bps: int | None = None,
        gas_usd: float | None = None,
        progress: bool = True,
    ) -> BacktestResult:
        if prices.empty:
            raise ValueError("prices dataframe is empty")

        initial_cash = float(initial_capital if initial_capital is not None else self._settings.backtest_initial_capital)
        base_slippage = int(slippage_bps if slippage_bps is not None else self._settings.backtest_slippage_bps)
        impact = float(impact_coeff if impact_coeff is not None else self._settings.backtest_impact_coeff)
        fee_rate = (int(fee_bps if fee_bps is not None else self._settings.backtest_fee_bps)) / 10_000.0
        gas = float(gas_usd if gas_usd is not None else self._settings.backtest_gas_usd)

        df = prices.copy()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

        required_cols = {"token_id", "price"}
        if not required_cols.issubset(set(df.columns)):
            raise ValueError(f"prices must include columns: {sorted(required_cols)}")

        signals_sorted = sorted(signals, key=lambda s: s.created_at)
        signals_by_ts: dict[pd.Timestamp, list[TradeSignal]] = {}
        for s in signals_sorted:
            ts = pd.Timestamp(s.created_at).tz_localize("UTC") if pd.Timestamp(s.created_at).tzinfo is None else pd.Timestamp(s.created_at).tz_convert("UTC")
            signals_by_ts.setdefault(ts, []).append(s)

        cash = initial_cash
        positions_qty: dict[str, float] = {}
        avg_price: dict[str, float] = {}
        trades: list[Trade] = []

        equity: list[float] = []
        idx: list[pd.Timestamp] = []

        it = df.groupby(level=0)
        iterator = tqdm(it, total=len(df.index.unique()), disable=not progress)
        for ts, slice_df in iterator:
            row_prices = {str(r["token_id"]): float(r["price"]) for _, r in slice_df.iterrows()}
            row_volume = {str(r["token_id"]): float(r.get("volume", 0.0) or 0.0) for _, r in slice_df.iterrows()}

            due_signals: list[TradeSignal] = []
            for sig_ts, sigs in list(signals_by_ts.items()):
                if sig_ts <= ts:
                    due_signals.extend(sigs)
                    del signals_by_ts[sig_ts]
            for sig in due_signals:
                px = row_prices.get(sig.token_id)
                if px is None:
                    continue

                qty = float(sig.metadata.get("qty") or 0.0)
                if qty <= 0 and sig.suggested_size is not None:
                    qty = float(sig.suggested_size)
                if qty <= 0:
                    qty = 1.0

                v = row_volume.get(sig.token_id, 0.0)
                notional_ref = qty * px
                vol_ratio = (notional_ref / (v + 1e-9)) if v > 0 else 0.0
                impact_bps = impact * min(0.25, vol_ratio) * 10_000.0
                total_slip_bps = base_slippage + int(max(0.0, impact_bps))
                slip = total_slip_bps / 10_000.0

                if sig.side == "buy":
                    fill_px = min(0.999, px * (1.0 + slip))
                    notional = qty * fill_px
                    fee = notional * fee_rate + gas
                    if cash < notional + fee:
                        continue
                    cash -= notional + fee

                    prev = positions_qty.get(sig.token_id, 0.0)
                    new = prev + qty
                    ap = avg_price.get(sig.token_id, fill_px)
                    if new > 0:
                        avg_price[sig.token_id] = ((ap * prev) + (fill_px * qty)) / new if prev > 0 else fill_px
                    positions_qty[sig.token_id] = new

                    trades.append(
                        Trade(
                            trade_id=None,
                            market_id=sig.market_id,
                            token_id=sig.token_id,
                            side="buy",
                            price=Decimal(str(fill_px)),
                            size=Decimal(str(qty)),
                            fee=Decimal(str(fee)),
                            timestamp=ts.to_pydatetime(),
                            raw={"edge_type": sig.edge_type, "slippage_bps": total_slip_bps},
                        )
                    )

                else:
                    fill_px = max(0.001, px * (1.0 - slip))
                    held = positions_qty.get(sig.token_id, 0.0)
                    qty_sell = min(qty, held)
                    if qty_sell <= 0:
                        continue
                    notional = qty_sell * fill_px
                    fee = notional * fee_rate + gas
                    cash += notional - fee
                    positions_qty[sig.token_id] = held - qty_sell
                    if positions_qty[sig.token_id] <= 0:
                        positions_qty.pop(sig.token_id, None)
                        avg_price.pop(sig.token_id, None)

                    trades.append(
                        Trade(
                            trade_id=None,
                            market_id=sig.market_id,
                            token_id=sig.token_id,
                            side="sell",
                            price=Decimal(str(fill_px)),
                            size=Decimal(str(qty_sell)),
                            fee=Decimal(str(fee)),
                            timestamp=ts.to_pydatetime(),
                            raw={"edge_type": sig.edge_type, "slippage_bps": total_slip_bps},
                        )
                    )

            value = 0.0
            for token_id, q in positions_qty.items():
                mp = row_prices.get(token_id)
                if mp is None:
                    continue
                value += q * mp

            equity.append(cash + value)
            idx.append(ts)

        curve = pd.Series(equity, index=pd.DatetimeIndex(idx, tz="UTC"), name="equity")
        metrics = self.compute_metrics(curve)
        mc = self.monte_carlo(curve) if self._settings.backtest_monte_carlo_paths > 0 else None
        return BacktestResult(equity_curve=curve, trades=trades, metrics=metrics, monte_carlo=mc)

    def compute_metrics(self, equity_curve: pd.Series) -> AdvancedMetrics:
        curve = equity_curve.dropna()
        if curve.empty:
            return AdvancedMetrics(
                sharpe=0.0,
                sortino=0.0,
                calmar=0.0,
                profit_factor=0.0,
                win_rate=0.0,
                expectancy=0.0,
                max_drawdown=0.0,
                recovery_factor=0.0,
                omega_ratio=0.0,
                cagr=0.0,
            )

        returns = curve.pct_change().dropna()
        if returns.empty:
            return AdvancedMetrics(
                sharpe=0.0,
                sortino=0.0,
                calmar=0.0,
                profit_factor=0.0,
                win_rate=0.0,
                expectancy=0.0,
                max_drawdown=0.0,
                recovery_factor=0.0,
                omega_ratio=0.0,
                cagr=0.0,
            )

        periods_per_year = self._infer_periods_per_year(returns.index)
        mean = float(returns.mean())
        std = float(returns.std(ddof=0) + 1e-12)
        sharpe = (mean / std) * float(np.sqrt(periods_per_year))

        downside = returns[returns < 0]
        downside_std = float(downside.std(ddof=0) + 1e-12) if not downside.empty else 0.0
        sortino = (mean / (downside_std + 1e-12)) * float(np.sqrt(periods_per_year)) if downside_std > 0 else float("inf")

        peak = curve.cummax()
        dd = (curve - peak) / peak.replace(0, np.nan)
        max_dd = float(dd.min()) if not dd.empty else 0.0

        total_return = float((curve.iloc[-1] / curve.iloc[0]) - 1.0) if curve.iloc[0] != 0 else 0.0
        years = max(1e-9, (curve.index[-1] - curve.index[0]).total_seconds() / (365.25 * 24 * 3600))
        cagr = float((curve.iloc[-1] / curve.iloc[0]) ** (1.0 / years) - 1.0) if curve.iloc[0] > 0 else 0.0

        calmar = float(cagr / abs(max_dd)) if max_dd < 0 else float("inf")

        gains = returns[returns > 0].sum()
        losses = -returns[returns < 0].sum()
        profit_factor = float(gains / losses) if losses > 0 else float("inf")
        win_rate = float((returns > 0).mean())
        expectancy = float(returns.mean())

        recovery = float(total_return / abs(max_dd)) if max_dd < 0 else float("inf")

        thr = 0.0
        above = (returns - thr)[returns > thr].sum()
        below = (thr - returns)[returns < thr].sum()
        omega = float(above / (below + 1e-12))

        return AdvancedMetrics(
            sharpe=float(sharpe),
            sortino=float(sortino),
            calmar=float(calmar),
            profit_factor=float(profit_factor),
            win_rate=float(win_rate),
            expectancy=float(expectancy),
            max_drawdown=float(max_dd),
            recovery_factor=float(recovery),
            omega_ratio=float(omega),
            cagr=float(cagr),
        )

    def monte_carlo(self, equity_curve: pd.Series) -> MonteCarloResult:
        paths = int(self._settings.backtest_monte_carlo_paths)
        if paths <= 0:
            raise ValueError("paths must be > 0")

        returns = equity_curve.pct_change().dropna().to_numpy(dtype=float)
        if returns.size == 0:
            return MonteCarloResult(paths=paths, horizon_steps=0, max_drawdown_p50=0.0, max_drawdown_p95=0.0, terminal_return_p50=0.0, terminal_return_p05=0.0)

        block = int(self._settings.backtest_monte_carlo_block_size)
        block = max(1, min(block, returns.size))
        rng = np.random.default_rng(int(self._settings.backtest_seed))

        horizon = returns.size
        max_dds = np.zeros(paths, dtype=float)
        terminal = np.zeros(paths, dtype=float)

        for i in range(paths):
            chunks = []
            while sum(len(c) for c in chunks) < horizon:
                start = int(rng.integers(0, max(1, returns.size - block + 1)))
                chunks.append(returns[start : start + block])
            sim = np.concatenate(chunks)[:horizon]
            eq = np.cumprod(1.0 + sim)
            peak = np.maximum.accumulate(eq)
            dd = (eq - peak) / peak
            max_dds[i] = float(np.min(dd))
            terminal[i] = float(eq[-1] - 1.0)

        return MonteCarloResult(
            paths=paths,
            horizon_steps=horizon,
            max_drawdown_p50=float(np.quantile(max_dds, 0.50)),
            max_drawdown_p95=float(np.quantile(max_dds, 0.95)),
            terminal_return_p50=float(np.quantile(terminal, 0.50)),
            terminal_return_p05=float(np.quantile(terminal, 0.05)),
        )

    def expanding_windows(
        self,
        *,
        start: datetime,
        end: datetime,
        train_days: int | None = None,
        test_days: int | None = None,
        step_days: int | None = None,
    ) -> list[tuple[datetime, datetime, datetime, datetime]]:
        train = int(train_days if train_days is not None else self._settings.backtest_walk_forward_train_days)
        test = int(test_days if test_days is not None else self._settings.backtest_walk_forward_test_days)
        step = int(step_days if step_days is not None else test)

        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)

        windows: list[tuple[datetime, datetime, datetime, datetime]] = []
        train_start = start
        test_start = start + timedelta(days=train)
        while test_start < end:
            train_end = test_start
            test_end = min(end, test_start + timedelta(days=test))
            windows.append((train_start, train_end, test_start, test_end))
            test_start = test_start + timedelta(days=step)
        return windows

    def purged_kfold(
        self,
        timestamps: pd.DatetimeIndex,
        *,
        splits: int | None = None,
        purge_days: int | None = None,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        k = int(splits if splits is not None else self._settings.backtest_purged_kfold_splits)
        purge = int(purge_days if purge_days is not None else self._settings.backtest_purge_days)
        if k < 2:
            raise ValueError("splits must be >= 2")

        ts = pd.DatetimeIndex(timestamps).sort_values()
        n = len(ts)
        fold_sizes = [n // k] * k
        for i in range(n % k):
            fold_sizes[i] += 1

        indices = np.arange(n)
        splits_out: list[tuple[np.ndarray, np.ndarray]] = []
        cur = 0
        for fold_size in fold_sizes:
            start_i = cur
            end_i = cur + fold_size
            test_idx = indices[start_i:end_i]
            test_start = ts[start_i]
            test_end = ts[end_i - 1]

            purge_delta = pd.Timedelta(days=purge)
            train_mask = (ts < (test_start - purge_delta)) | (ts > (test_end + purge_delta))
            train_idx = indices[train_mask]
            splits_out.append((train_idx, test_idx))
            cur = end_i
        return splits_out

    def _infer_periods_per_year(self, idx: pd.DatetimeIndex) -> float:
        if len(idx) < 2:
            return 365.0
        deltas = np.diff(idx.view("int64")) / 1e9
        median_s = float(np.median(deltas))
        if median_s <= 0:
            return 365.0
        return float((365.25 * 24 * 3600) / median_s)

    def _load_cached_prices(self, *, market_id: str, token_ids: list[str], start: datetime, end: datetime) -> pd.DataFrame | None:
        if not token_ids:
            return None
        placeholders = ", ".join(["?"] * len(token_ids))
        q = f"""
            SELECT market_id, token_id, timestamp, price, volume
            FROM backtest_prices
            WHERE market_id = ?
              AND token_id IN ({placeholders})
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        try:
            rows = self._con.execute(q, [market_id, *token_ids, start, end]).fetch_df()
        except Exception:
            return None
        if rows is None or rows.empty:
            return None
        rows["timestamp"] = pd.to_datetime(rows["timestamp"], utc=True)
        return rows

    def _write_cached_prices(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        tmp = df.copy()
        tmp["timestamp"] = pd.to_datetime(tmp["timestamp"], utc=True)
        for _, r in tmp.iterrows():
            try:
                self._con.execute(
                    """
                    INSERT OR REPLACE INTO backtest_prices(market_id, token_id, timestamp, price, volume, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(r["market_id"]),
                        str(r["token_id"]),
                        r["timestamp"].to_pydatetime(),
                        float(r["price"]),
                        float(r.get("volume", 0.0) or 0.0),
                        str(r.get("source", "unknown")),
                    ],
                )
            except Exception:
                continue

    def _fetch_prices_best_effort(
        self,
        *,
        market_id: str,
        token_ids: list[str],
        start: datetime,
        end: datetime,
        freq: str,
        allow_synthetic: bool,
    ) -> pd.DataFrame:
        if allow_synthetic:
            logger.warning("No reliable historical feed configured; using synthetic series", market_id=market_id)
            return self._synthetic_prices(market_id=market_id, token_ids=token_ids, start=start, end=end, freq=freq)
        return pd.DataFrame(columns=["market_id", "token_id", "timestamp", "price", "volume", "source"])

    def _synthetic_prices(self, *, market_id: str, token_ids: list[str], start: datetime, end: datetime, freq: str) -> pd.DataFrame:
        idx = pd.date_range(start=start, end=end, freq=freq, tz="UTC")
        rng = np.random.default_rng(int(self._settings.backtest_seed))

        rows: list[dict[str, Any]] = []
        for token_id in token_ids:
            steps = rng.normal(loc=0.0, scale=0.01, size=len(idx))
            price = np.clip(0.5 + np.cumsum(steps), 0.01, 0.99)
            volume = rng.lognormal(mean=8.0, sigma=0.8, size=len(idx))
            for ts, px, vol in zip(idx, price, volume, strict=False):
                rows.append(
                    {
                        "market_id": market_id,
                        "token_id": token_id,
                        "timestamp": ts.to_pydatetime(),
                        "price": float(px),
                        "volume": float(vol),
                        "source": "synthetic",
                    }
                )
        return pd.DataFrame(rows)

    def replay_agent_decisions(self, *, start: datetime, end: datetime, limit: int = 2000) -> pd.DataFrame:
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        try:
            df = self._con.execute(
                """
                SELECT cycle_id, started_at, finished_at, token_usage_estimate, dry_run
                FROM cycle_runs
                WHERE started_at >= ? AND started_at <= ?
                ORDER BY started_at ASC
                LIMIT ?
                """,
                [start, end, int(limit)],
            ).fetch_df()
        except Exception:
            return pd.DataFrame()
        return df
