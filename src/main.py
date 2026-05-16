from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from argparse import ArgumentParser
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from src.backtesting.backtester import Backtester
from src.core.config import get_settings
from src.core.logging import configure_logging
from src.core.portfolio import Portfolio
from src.data.clob_client import PolyClobClient
from src.data.data_api_client import DataAPIClient
from src.data.gamma_client import GammaClient
from src.monitoring.alerts import try_notify
from src.monitoring.dashboard_server import launch_dashboard
from src.monitoring.healthcheck import as_json as healthcheck_json
from src.monitoring.healthcheck import run_healthcheck
from src.monitoring.logger import emit_startup_banner
from src.orchestration.scheduler import PolyForgeScheduler
from src.core.utils import select_top_signals
from src.risk.risk_engine import RiskEngine
from src.security.audit_logger import audit_event, get_audit_logger
from src.security.secrets_manager import fingerprint_settings
from src.strategies.scanner import MarketScanner
from src.utils.logging import redact


def _safe_settings_log(settings: Any) -> dict[str, Any]:
    payload = settings.model_dump(mode="json")
    for k in list(payload.keys()):
        if "key" in k.lower() or "secret" in k.lower() or "token" in k.lower() or "pass" in k.lower():
            payload[k] = redact(payload[k])
    return payload


def _build_parser() -> ArgumentParser:
    p = ArgumentParser(prog="polyforge", description="PolyForge runner (Phase 9)")
    p.add_argument("--scan-only", action="store_true", help="Run scanner + risk evaluation only (default).")
    p.add_argument("--backtest-sample", action="store_true", help="Run a synthetic backtest sample and exit.")
    p.add_argument("--backtest", action="store_true", help="Run advanced backtest and write a report to reports/.")
    p.add_argument("--strategy", default="ai-prob", help="Backtest strategy: ai-prob|copy|mm|arb|scanner")
    p.add_argument("--period", default=None, help="Date range: YYYY-MM-DD:YYYY-MM-DD")
    p.add_argument("--market-id", default=None, help="Market id for backtesting (optional; synthetic default).")
    p.add_argument("--token-ids", default=None, help="Comma-separated token ids for backtesting (optional).")
    p.add_argument("--optimize", action="store_true", help="Run parameter optimization (writes results to DuckDB and reports/).")
    p.add_argument("--optimize-iter", default=30, type=int, help="Number of optimization trials.")
    p.add_argument("--agent-cycle", action="store_true", help="Run one LangGraph agent cycle (dry-run).")
    p.add_argument("--execute", action="store_true", help="Enable executor stage (still gated by config and DRY_RUN).")
    p.add_argument("--run-forever", action="store_true", help="Run the scheduler loop until stopped.")
    p.add_argument("--dashboard", action="store_true", help="Launch the Streamlit dashboard.")
    p.add_argument("--healthcheck", action="store_true", help="Run container healthcheck and exit.")
    p.add_argument("--full-check", action="store_true", help="Run pre-launch validation checklist and exit.")
    p.add_argument("--test-system", action="store_true", help="Run an end-to-end dry-run system test and exit.")
    p.add_argument("--test-cycles", default=3, type=int, help="Number of cycles for --test-system.")
    p.add_argument(
        "--paper-demo-cycles",
        default=0,
        type=int,
        help="Run N cycles back-to-back (useful for paper trading demos). Use with --dashboard to watch metrics move.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(override=False)
    settings = get_settings()
    configure_logging(settings)

    args = _build_parser().parse_args(argv)
    emit_startup_banner(settings)
    settings_fp = fingerprint_settings(settings)
    audit = get_audit_logger(settings)
    audit_hash = audit_event(
        settings,
        "config_loaded",
        {"settings_fingerprint": settings_fp, "env": settings.env, "wallet_mode": settings.wallet_mode},
    )

    logger.info("PolyForge starting (Phase 9)", **_safe_settings_log(settings))

    logger.info(
        "Safety status",
        dry_run=settings.dry_run,
        trading_enabled=settings.trading_enabled,
        execute_enabled=settings.execute_enabled,
        wallet_mode=settings.wallet_mode,
        llm_provider=settings.llm_provider,
        enabled_strategies=settings.enabled_strategies,
        settings_fingerprint=settings_fp,
        audit_last_hash=audit.get_last_hash(),
        audit_hash=audit_hash,
    )

    if args.healthcheck:
        res = run_healthcheck(settings)
        print(healthcheck_json(res))
        return 0 if res.ok else 2

    if args.full_check:
        from src.testing.validation import run_full_check

        res = run_full_check(settings)
        print(json.dumps({"ok": res.ok, "details": res.details}, indent=2, default=str))
        return 0 if res.ok else 2

    if args.test_system:
        from src.testing.system_tester import run_dry_run_system_test

        res = run_dry_run_system_test(settings, cycles=int(args.test_cycles))
        print(json.dumps(res.__dict__, indent=2, default=str))
        return 0 if res.ok else 2

    demo_thread: threading.Thread | None = None
    if int(args.paper_demo_cycles) > 0:
        from src.orchestration.orchestrator import run_once

        async def _run_demo() -> None:
            for _ in range(int(args.paper_demo_cycles)):
                await run_once(settings, execute=True)

        def _demo_runner() -> None:
            asyncio.run(_run_demo())

        demo_thread = threading.Thread(target=_demo_runner, name="polyforge-paper-demo", daemon=True)
        demo_thread.start()

    if settings.trading_enabled and settings.dry_run:
        logger.warning("Trading enabled but DRY_RUN is true: live order placement is blocked (simulation is allowed).")
    if settings.trading_enabled and not settings.dry_run:
        logger.warning("Trading enabled and DRY_RUN is false. Ensure POLYFORGE_EXECUTE_ENABLED is only enabled intentionally.")

    scheduler: PolyForgeScheduler | None = None
    scheduler_thread: threading.Thread | None = None

    if args.run_forever and args.dashboard:
        scheduler = PolyForgeScheduler(settings)

        def _runner() -> None:
            asyncio.run(scheduler.start_main_loop(execute=bool(args.execute)))

        scheduler_thread = threading.Thread(target=_runner, name="polyforge-scheduler", daemon=True)
        scheduler_thread.start()

    if args.dashboard:
        proc = launch_dashboard(
            settings,
            project_root=Path(__file__).resolve().parents[1],
            use_snapshot_db=bool(args.run_forever or (int(args.paper_demo_cycles) > 0)),
        )
        try:
            proc.wait()
        except KeyboardInterrupt:
            try:
                proc.terminate()
            except Exception:
                pass
            if scheduler is not None:
                scheduler.stop()
        return 0

    if args.run_forever:
        scheduler = PolyForgeScheduler(settings)
        try:
            asyncio.run(scheduler.start_main_loop(execute=bool(args.execute)))
        except KeyboardInterrupt:
            scheduler.stop()
        return 0

    if args.backtest or args.optimize:
        from datetime import timedelta

        from src.backtesting.advanced_backtester import AdvancedBacktester
        from src.backtesting.optimizer import StrategyOptimizer
        from src.strategies.strategy_evaluator import StrategyEvaluator, default_signal_generator

        def _parse_period(s: str | None) -> tuple[datetime, datetime]:
            if not s:
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=180)
                return start, end
            parts = [p.strip() for p in s.split(":")]
            if len(parts) != 2:
                raise ValueError("period must be YYYY-MM-DD:YYYY-MM-DD")
            start = datetime.fromisoformat(parts[0]).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(parts[1]).replace(tzinfo=timezone.utc)
            return start, end

        start, end = _parse_period(args.period)
        market_id = str(args.market_id or "synthetic_market")
        token_ids = [t.strip() for t in str(args.token_ids).split(",") if t.strip()] if args.token_ids else ["synthetic_token"]

        bt = AdvancedBacktester(settings)
        evaluator = StrategyEvaluator(settings, backtester=bt)
        reports_dir = Path(settings.reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)

        def _params_for_strategy(name: str) -> dict[str, Any]:
            name = name.strip().lower()
            if name == "ai-prob":
                return {"threshold": 0.03, "qty": 5.0}
            if name == "arb":
                return {"threshold": 0.02, "qty": 7.0}
            if name == "copy":
                return {"threshold": 0.04, "qty": 4.0}
            if name == "mm":
                return {"threshold": 0.01, "qty": 2.0}
            return {"threshold": 0.03, "qty": 5.0}

        base_params = _params_for_strategy(args.strategy)

        if args.optimize:
            from scipy.stats import uniform

            opt = StrategyOptimizer(settings)

            def _evaluate(params: dict[str, Any]) -> tuple[float, dict[str, Any]]:
                merged = {**base_params, **params}
                rep = evaluator.evaluate(
                    strategy=str(args.strategy),
                    market_id=market_id,
                    token_ids=token_ids,
                    start=start,
                    end=end,
                    signal_generator=default_signal_generator,
                    params=merged,
                )
                score = float(rep.metrics.get("sharpe") or 0.0)
                return score, rep.metrics

            result = opt.random_search(
                param_distributions={"threshold": uniform(0.005, 0.08), "qty": uniform(1.0, 20.0)},
                evaluate=_evaluate,
                n_iter=int(args.optimize_iter),
                objective="sharpe",
            )

            out_path = reports_dir / f"optimization_{result.run_id}.json"
            out_path.write_text(
                json.dumps(
                    {
                        "run_id": result.run_id,
                        "objective": result.objective,
                        "best": {"params": result.best.params, "score": result.best.score, "metrics": result.best.metrics},
                        "trial_count": len(result.trials),
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            logger.info("Optimization complete", run_id=result.run_id, best_score=result.best.score, best_params=result.best.params, output=str(out_path))
            opt.close()

        if args.backtest:
            rep = evaluator.evaluate(
                strategy=str(args.strategy),
                market_id=market_id,
                token_ids=token_ids,
                start=start,
                end=end,
                signal_generator=default_signal_generator,
                params=base_params,
            )
            logger.info("Backtest complete", metrics=rep.metrics, report_dir=str(rep.report_dir))

        bt.close()
        return 0

    gamma = GammaClient(settings)
    data_api = DataAPIClient(settings)
    portfolio = Portfolio(settings)
    risk = RiskEngine(settings, portfolio)

    if args.backtest_sample:
        from src.core.models import TradeSignal

        bt = Backtester(fee_rate=0.0, slippage_bps=10)
        prices = bt.load_historical_data()
        signals = [
            {
                "strategy_id": "backtest",
                "market_id": None,
                "token_id": "sample",
                "side": "buy",
                "confidence": 0.6,
                "edge_type": "sample",
                "created_at": prices.index[10].to_pydatetime(),
                "metadata": {"qty": 10.0},
            },
            {
                "strategy_id": "backtest",
                "market_id": None,
                "token_id": "sample",
                "side": "sell",
                "confidence": 0.6,
                "edge_type": "sample",
                "created_at": prices.index[-10].to_pydatetime(),
                "metadata": {"qty": 10.0},
            },
        ]
        tsigs = [TradeSignal.model_validate(s) for s in signals]
        curve = bt.simulate_trades(prices, tsigs, initial_cash=float(settings.initial_cash_balance))
        metrics = bt.compute_metrics()
        logger.info(
            "Backtest sample complete",
            final_equity=float(curve.iloc[-1]),
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            sharpe=metrics.sharpe,
            max_drawdown=metrics.max_drawdown,
            expectancy=metrics.expectancy,
        )
        gamma.close()
        data_api.close()
        portfolio.close()
        return 0

    clob: PolyClobClient | None = None
    try:
        clob = PolyClobClient(settings)
        logger.info("CLOB client initialized", init=clob.init_info)
    except Exception as e:
        logger.warning("CLOB client not available; scanner will use Gamma-only pricing where possible: {}", e)

    scanner = MarketScanner(settings, gamma, data_api, clob=clob)

    signals = scanner.generate_signals()

    if args.agent_cycle:
        from src.agents.graph import build_graph, run_cycle
        from src.agents.state import GraphState
        from src.agents.tools import AgentToolbox

        tb = AgentToolbox(gamma=gamma, data_api=data_api, portfolio=portfolio, clob=clob)
        if args.execute and not settings.execute_enabled:
            logger.warning("CLI --execute set but POLYFORGE_EXECUTE_ENABLED is false; executor will not run.")
        bundle = build_graph(
            settings,
            toolbox=tb,
            risk_engine=risk,
            interrupt_before_executor=not args.execute,
        )

        for sig in signals:
            portfolio.register_token(sig.token_id, sig.market_id, sig.category)
            if sig.suggested_price is not None:
                portfolio.update_mark_price(sig.token_id, sig.suggested_price)
            elif clob is not None:
                try:
                    mp = clob.get_mid_price(sig.token_id)
                    if mp is not None:
                        portfolio.update_mark_price(sig.token_id, mp)
                except Exception:
                    pass

        initial_state: GraphState = {
            "messages": [],
            "market_context": {"timestamp": datetime.now(timezone.utc).isoformat(), "signal_count": len(signals)},
            "signals": select_top_signals(signals, limit=50),
            "portfolio": portfolio.get_state(),
            "decisions": [],
            "research_data": {},
            "confidence_scores": {},
            "execution_enabled": bool(args.execute),
            "supervisor": {},
            "errors": [],
        }

        final_state = asyncio.run(run_cycle(bundle, settings=settings, initial_state=initial_state))
        decisions = final_state.get("decisions", [])
        final_decision = decisions[-1].model_dump(mode="json") if decisions else None

        messages = final_state.get("messages", [])
        reasoning_chain = [getattr(m, "content", "") for m in messages]
        execution_report = final_state.get("execution_report")

        logger.info(
            "Agent cycle completed (dry-run)",
            final_decision=final_decision,
            circuit_breaker=risk.circuit_breaker_status,
        )

        if execution_report is not None:
            try_notify(settings, f"PolyForge execution report (dry_run={settings.dry_run}): {json.dumps(execution_report)[:3500]}")
        if final_decision is not None:
            try_notify(settings, f"PolyForge decision: {json.dumps(final_decision)[:3500]}")

        print(
            json.dumps(
                {"final_decision": final_decision, "execution_report": execution_report, "reasoning_chain": reasoning_chain},
                indent=2,
            )
        )

        gamma.close()
        data_api.close()
        portfolio.close()
        return 0

    allowed: list[tuple[Any, Decimal]] = []
    blocked_reasons: Counter[str] = Counter()

    for sig in signals:
        portfolio.register_token(sig.token_id, sig.market_id, sig.category)
        if sig.suggested_price is not None:
            portfolio.update_mark_price(sig.token_id, sig.suggested_price)

        ok, reason = risk.check_trade_allowed(sig)
        if not ok:
            blocked_reasons[reason] += 1
            logger.info("Signal blocked", reason=reason, signal=sig.model_dump(mode="json"))
            continue

        size = risk.calculate_position_size(sig, portfolio.get_state())
        if size <= 0:
            blocked_reasons["size_zero_after_caps"] += 1
            logger.info("Signal blocked", reason="size_zero_after_caps", signal=sig.model_dump(mode="json"))
            continue

        allowed.append((sig, size))
        logger.info(
            "Signal allowed (dry-run)",
            token_id=sig.token_id,
            market_id=sig.market_id,
            category=sig.category,
            edge_type=sig.edge_type,
            confidence=float(sig.confidence),
            suggested_price=float(sig.suggested_price) if sig.suggested_price is not None else None,
            size=float(size),
        )

    metrics = risk.update_risk_metrics()
    exposure = risk.get_current_exposure()

    logger.info(
        "Cycle summary",
        total_signals=len(signals),
        allowed=len(allowed),
        blocked=len(signals) - len(allowed),
        blocked_reasons=dict(blocked_reasons),
        circuit_breaker=risk.circuit_breaker_status,
        risk_metrics=metrics.model_dump(mode="json"),
        exposure=exposure,
    )

    gamma.close()
    data_api.close()
    portfolio.close()

    logger.info("Phase 2 dry-run cycle completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
