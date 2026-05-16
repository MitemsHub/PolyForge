from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger
import tiktoken

from src.agents.graph import GraphBundle, build_graph, run_cycle
from src.agents.state import GraphState
from src.agents.tools import AgentToolbox
from src.core.config import Settings
from src.core.portfolio import Portfolio
from src.core.utils import filter_signals_by_confidence, select_top_signals
from src.data.clob_client import PolyClobClient
from src.data.data_api_client import DataAPIClient
from src.data.gamma_client import GammaClient
from src.execution.executor import TradeExecutor
from src.monitoring.alerts import AlertManager
from src.security.audit_logger import audit_event
from src.risk.risk_engine import RiskEngine
from src.strategies.scanner import MarketScanner


@dataclass(frozen=True)
class StageTiming:
    scanner_ms: int
    agent_ms: int
    executor_ms: int
    total_ms: int


@dataclass(frozen=True)
class CycleResult:
    started_at: str
    finished_at: str
    timings: StageTiming
    signal_count: int
    top_signals: list[dict[str, Any]]
    decision: dict[str, Any] | None
    execution_report: dict[str, Any] | None
    token_usage_estimate: int


class Orchestrator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        self._gamma = GammaClient(settings)
        self._data_api = DataAPIClient(settings)
        self._portfolio = Portfolio(settings)
        self._risk = RiskEngine(settings, self._portfolio)

        self._clob: PolyClobClient | None = None
        try:
            self._clob = PolyClobClient(settings)
        except Exception as e:
            logger.warning("CLOB client unavailable: {}", e)

        self._scanner = MarketScanner(settings, self._gamma, self._data_api, clob=self._clob)
        self._alerts = AlertManager(settings)

        tb = AgentToolbox(gamma=self._gamma, data_api=self._data_api, portfolio=self._portfolio, clob=self._clob)
        self._graph_bundle: GraphBundle = build_graph(settings, toolbox=tb, risk_engine=self._risk, interrupt_before_executor=not settings.execute_enabled)
        self._executor: TradeExecutor | None = None
        if self._clob is not None:
            self._executor = TradeExecutor(settings, gamma=self._gamma, clob=self._clob)

        self._db = duckdb.connect(str(self._duckdb_path(settings.db_url)))
        self._init_telemetry_schema()
        self._sync_dashboard_db_best_effort()

    def close(self) -> None:
        try:
            self._gamma.close()
        except Exception:
            pass
        try:
            self._data_api.close()
        except Exception:
            pass
        try:
            self._portfolio.close()
        except Exception:
            pass
        try:
            self._db.close()
        except Exception:
            pass

    @staticmethod
    def _duckdb_path(db_url: str) -> Path:
        prefix = "duckdb:///"
        if db_url.startswith(prefix):
            return Path(db_url.removeprefix(prefix))
        if db_url.endswith(".duckdb"):
            return Path(db_url)
        return Path("./data/polyforge.duckdb")

    def _init_telemetry_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS cycle_runs (
                cycle_id VARCHAR PRIMARY KEY,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                scanner_ms INTEGER,
                agent_ms INTEGER,
                executor_ms INTEGER,
                total_ms INTEGER,
                signal_count INTEGER,
                approved_count INTEGER,
                placed_count INTEGER,
                dry_run BOOLEAN,
                token_usage_estimate INTEGER
            );
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS cycle_signals (
                cycle_id VARCHAR,
                created_at TIMESTAMP,
                strategy_id VARCHAR,
                market_id VARCHAR,
                category VARCHAR,
                token_id VARCHAR,
                side VARCHAR,
                edge_type VARCHAR,
                confidence DOUBLE,
                expected_edge DOUBLE,
                suggested_price DOUBLE,
                metadata_json VARCHAR
            );
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_messages (
                cycle_id VARCHAR,
                idx INTEGER,
                role VARCHAR,
                content VARCHAR
            );
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_decisions (
                cycle_id VARCHAR PRIMARY KEY,
                decision_json VARCHAR,
                execution_report_json VARCHAR
            );
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                cycle_id VARCHAR PRIMARY KEY,
                timestamp TIMESTAMP,
                equity DOUBLE,
                cash DOUBLE,
                gross_exposure DOUBLE,
                position_count INTEGER,
                realized_pnl DOUBLE
            );
            """
        )

    async def orchestrate_cycle(self, *, execute: bool, run_agents: bool = True) -> CycleResult:
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()

        scanner_t0 = time.perf_counter()
        signals = self._scanner.generate_signals()
        scanner_ms = int((time.perf_counter() - scanner_t0) * 1000)

        for s in signals:
            self._portfolio.register_token(s.token_id, s.market_id, s.category)
            if s.suggested_price is not None:
                self._portfolio.update_mark_price(s.token_id, s.suggested_price)
            elif self._clob is not None:
                try:
                    mp = self._clob.get_mid_price(s.token_id)
                    if mp is not None:
                        self._portfolio.update_mark_price(s.token_id, mp)
                except Exception:
                    pass

        top = select_top_signals(signals, limit=10)
        hi = filter_signals_by_confidence(signals, self._settings.alert_on_high_confidence_threshold)
        if hi:
            self._alerts.signal_high_confidence(hi[:5])

        agent_ms = 0
        decision = None
        execution_report = None
        token_usage = 0

        final_messages: list[Any] = []
        if run_agents and "agents" in set(self._settings.enabled_strategies):
            agent_t0 = time.perf_counter()
            init_state: GraphState = {
                "messages": [],
                "market_context": {"timestamp": started.isoformat(), "signal_count": len(signals)},
                "signals": select_top_signals(signals, limit=50),
                "portfolio": self._portfolio.get_state(),
                "decisions": [],
                "research_data": {},
                "confidence_scores": {},
                "execution_enabled": bool(execute),
                "supervisor": {},
                "errors": [],
            }
            final_state = await run_cycle(self._graph_bundle, settings=self._settings, initial_state=init_state)
            agent_ms = int((time.perf_counter() - agent_t0) * 1000)

            decisions = final_state.get("decisions", [])
            decision = decisions[-1].model_dump(mode="json") if decisions else None
            execution_report = final_state.get("execution_report")

            final_messages = final_state.get("messages", []) or []
            token_usage = self._estimate_tokens(final_messages)
        else:
            decision = None

        executor_ms = 0
        placed_count = 0
        approved_count = 0
        dry_run = bool(self._settings.dry_run or not (self._settings.trading_enabled and self._settings.execute_enabled and execute))

        if isinstance(decision, dict):
            approved_count = len(decision.get("signals") or [])
        if isinstance(execution_report, dict):
            try:
                placed_count = int(execution_report.get("placed") or 0)
            except Exception:
                placed_count = 0

        total_ms = int((time.perf_counter() - t0) * 1000)
        finished = datetime.now(timezone.utc)

        cycle_id = decision.get("cycle_id") if isinstance(decision, dict) and decision.get("cycle_id") else str(int(started.timestamp()))
        try:
            self._persist_cycle_signals(str(cycle_id), signals, started)
            self._persist_portfolio_snapshot(str(cycle_id), started)
            self._persist_agent_artifacts(str(cycle_id), decision, execution_report, final_messages)

            self._db.execute(
                """
                INSERT OR REPLACE INTO cycle_runs(
                    cycle_id, started_at, finished_at, scanner_ms, agent_ms, executor_ms, total_ms,
                    signal_count, approved_count, placed_count, dry_run, token_usage_estimate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(cycle_id),
                    started,
                    finished,
                    scanner_ms,
                    agent_ms,
                    executor_ms,
                    total_ms,
                    len(signals),
                    approved_count,
                    placed_count,
                    bool(dry_run),
                    int(token_usage),
                ],
            )
            self._sync_dashboard_db_best_effort()
        except Exception as e:
            logger.warning("Telemetry write failed: {}", e)

        self._alerts.cycle_summary(
            {
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "signal_count": len(signals),
                "approved_count": approved_count,
                "placed_count": placed_count,
                "dry_run": dry_run,
                "timings_ms": {"scanner": scanner_ms, "agent": agent_ms, "executor": executor_ms, "total": total_ms},
            }
        )
        audit_event(
            self._settings,
            "cycle_summary",
            {
                "cycle_id": str(cycle_id),
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "signal_count": len(signals),
                "approved_count": approved_count,
                "placed_count": placed_count,
                "dry_run": bool(dry_run),
                "timings_ms": {"scanner": scanner_ms, "agent": agent_ms, "executor": executor_ms, "total": total_ms},
            },
        )

        return CycleResult(
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            timings=StageTiming(scanner_ms=scanner_ms, agent_ms=agent_ms, executor_ms=executor_ms, total_ms=total_ms),
            signal_count=len(signals),
            top_signals=[s.model_dump(mode="json") for s in top],
            decision=decision,
            execution_report=execution_report,
            token_usage_estimate=int(token_usage),
        )

    def _dashboard_db_path(self) -> Path:
        base = self._duckdb_path(self._settings.db_url)
        return base.with_name(f"{base.stem}_dashboard.duckdb")

    def _sync_dashboard_db_best_effort(self) -> None:
        try:
            self._sync_dashboard_db()
        except Exception as e:
            logger.debug("Dashboard DB sync skipped: {}", e)

    def _sync_dashboard_db(self) -> None:
        snap = self._dashboard_db_path()
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap_sql = snap.as_posix().replace("'", "''")

        attached = False
        try:
            self._db.execute(f"ATTACH '{snap_sql}' AS dash;")
            attached = True
            for tbl in (
                "cycle_runs",
                "cycle_signals",
                "agent_messages",
                "agent_decisions",
                "portfolio_snapshots",
                "positions",
                "trades",
                "token_registry",
                "portfolio_meta",
                "daily_equity",
            ):
                self._db.execute(f"CREATE OR REPLACE TABLE dash.{tbl} AS SELECT * FROM main.{tbl};")
        finally:
            if attached:
                try:
                    self._db.execute("DETACH dash;")
                except Exception:
                    pass

    def _persist_cycle_signals(self, cycle_id: str, signals: list[Any], started_at: datetime) -> None:
        try:
            self._db.execute("DELETE FROM cycle_signals WHERE cycle_id = ?", [cycle_id])
        except Exception:
            pass

        for s in signals[:500]:
            try:
                self._db.execute(
                    """
                    INSERT INTO cycle_signals(
                        cycle_id, created_at, strategy_id, market_id, category, token_id, side, edge_type,
                        confidence, expected_edge, suggested_price, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        cycle_id,
                        getattr(s, "created_at", started_at),
                        getattr(s, "strategy_id", None),
                        getattr(s, "market_id", None),
                        getattr(s, "category", None),
                        getattr(s, "token_id", None),
                        getattr(s, "side", None),
                        getattr(s, "edge_type", None),
                        float(getattr(s, "confidence", 0.0)),
                        float(getattr(s, "expected_edge", 0.0)) if getattr(s, "expected_edge", None) is not None else None,
                        float(getattr(s, "suggested_price", 0.0)) if getattr(s, "suggested_price", None) is not None else None,
                        json.dumps(getattr(s, "metadata", {}) or {}, default=str),
                    ],
                )
            except Exception:
                continue

    def _persist_agent_artifacts(
        self,
        cycle_id: str,
        decision: dict[str, Any] | None,
        execution_report: dict[str, Any] | None,
        messages: list[Any],
    ) -> None:
        try:
            self._db.execute("DELETE FROM agent_messages WHERE cycle_id = ?", [cycle_id])
        except Exception:
            pass

        for idx, m in enumerate(messages[:200]):
            role = getattr(m, "type", None) or getattr(m, "__class__", type("x", (), {})).__name__
            content = getattr(m, "content", "")
            try:
                self._db.execute(
                    "INSERT INTO agent_messages(cycle_id, idx, role, content) VALUES (?, ?, ?, ?)",
                    [cycle_id, int(idx), str(role), str(content)],
                )
            except Exception:
                continue

        try:
            self._db.execute(
                "INSERT OR REPLACE INTO agent_decisions(cycle_id, decision_json, execution_report_json) VALUES (?, ?, ?)",
                [
                    cycle_id,
                    json.dumps(decision or {}, default=str),
                    json.dumps(execution_report or {}, default=str),
                ],
            )
        except Exception:
            pass

    def _persist_portfolio_snapshot(self, cycle_id: str, ts: datetime) -> None:
        try:
            if self._settings.paper_trading_enabled and self._settings.paper_use_live_mid_prices and self._clob is not None:
                try:
                    for p in (self._portfolio.get_state().positions or []):
                        mp = self._clob.get_mid_price(p.token_id)
                        if mp is not None:
                            self._portfolio.update_mark_price(p.token_id, mp)
                except Exception:
                    pass

            equity = self._portfolio.compute_equity()
            exposure = self._portfolio.get_exposure_snapshot().total_value
            state = self._portfolio.get_state()
            realized = sum((p.realized_pnl or 0) for p in state.positions)

            self._db.execute(
                """
                INSERT OR REPLACE INTO portfolio_snapshots(
                    cycle_id, timestamp, equity, cash, gross_exposure, position_count, realized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    cycle_id,
                    ts,
                    float(equity),
                    float(state.cash_balance or 0),
                    float(exposure),
                    int(len(state.positions)),
                    float(realized),
                ],
            )
        except Exception:
            pass

    def _estimate_tokens(self, messages: list[Any]) -> int:
        text = "\n".join(getattr(m, "content", "") or "" for m in messages if hasattr(m, "content"))
        try:
            enc = tiktoken.encoding_for_model(self._settings.llm_model)
        except Exception:
            enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text))


async def run_once(settings: Settings, *, execute: bool) -> CycleResult:
    orch = Orchestrator(settings)
    try:
        return await orch.orchestrate_cycle(execute=execute, run_agents=True)
    finally:
        orch.close()
