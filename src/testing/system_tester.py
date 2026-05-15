from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.agents.graph import build_graph, run_cycle
from src.agents.state import GraphState
from src.agents.tools import AgentToolbox
from src.core.config import Settings
from src.core.models import AgentDecision, Market, TradeSignal
from src.core.utils import select_top_signals
from src.core.portfolio import Portfolio
from src.execution.executor import TradeExecutor
from src.risk.risk_engine import RiskEngine
from src.security.audit_logger import audit_event
from src.strategies.scanner import MarketScanner


@dataclass(frozen=True)
class SystemTestResult:
    ok: bool
    cycles: int
    signal_counts: list[int]
    approved_counts: list[int]
    errors: list[str]


class FakeGamma:
    def __init__(self, markets: list[Market]) -> None:
        self._markets = markets

    def get_markets(self, _: dict[str, Any] | None = None) -> list[Market]:
        return list(self._markets)

    def get_market_by_id(self, market_id: str) -> Market:
        for m in self._markets:
            if m.id == market_id:
                return m
        return Market(id=market_id, token_ids=["T0", "T1"], raw={})

    def close(self) -> None:
        return


class FakeDataAPI:
    def get_top_traders(self, limit: int = 10) -> list[dict[str, Any]]:
        return []

    def get_wallet_trades(self, wallet: str, limit: int = 50) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        return


class FakeClob:
    def __init__(self, *, order_books: dict[str, dict[str, Any]] | None = None) -> None:
        self._order_books = order_books or {}

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        if token_id in self._order_books:
            return self._order_books[token_id]
        if str(token_id).endswith("1"):
            return {"bids": [{"price": "0.47", "size": "100"}], "asks": [{"price": "0.49", "size": "100"}]}
        if str(token_id).endswith("2"):
            return {"bids": [{"price": "0.51", "size": "100"}], "asks": [{"price": "0.53", "size": "100"}]}
        return {"bids": [{"price": "0.49", "size": "100"}], "asks": [{"price": "0.51", "size": "100"}]}

    def get_mid_price(self, token_id: str) -> Decimal | None:
        book = self.get_order_book(token_id)
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None
        return (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / Decimal("2")

    def post_order_args(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"ok": False, "blocked": True, "reason": "dry_run_or_trading_disabled"}

    def cancel_all_orders(self) -> dict[str, Any]:
        return {"ok": False, "blocked": True, "reason": "dry_run_or_trading_disabled"}

    def get_open_orders(self) -> list[dict[str, Any]]:
        return []

    def get_fills(self) -> list[dict[str, Any]]:
        return []

    def get_balance(self) -> dict[str, Any]:
        return {"available": "0"}


def _default_markets() -> list[Market]:
    now = datetime.now(timezone.utc)
    return [
        Market(
            id="M1",
            category="test",
            end_date=now,
            token_ids=["YES1", "NO1"],
            raw={"yesPrice": "0.48", "noPrice": "0.48", "tickSize": "0.001"},
        ),
        Market(
            id="M2",
            category="test",
            end_date=now,
            token_ids=["YES2", "NO2"],
            raw={"yesPrice": "0.52", "noPrice": "0.52", "tickSize": "0.001"},
        ),
    ]


def run_dry_run_system_test(settings: Settings, *, cycles: int = 3) -> SystemTestResult:
    st_settings = settings.model_copy(update={"dry_run": True, "trading_enabled": False})

    markets = _default_markets()
    gamma = FakeGamma(markets)
    data_api = FakeDataAPI()
    clob = FakeClob()

    portfolio = Portfolio(st_settings)
    risk = RiskEngine(st_settings, portfolio)
    scanner = MarketScanner(st_settings, gamma=gamma, data_api=data_api, clob=clob)

    tb = AgentToolbox(gamma=gamma, data_api=data_api, portfolio=portfolio, clob=clob)
    bundle = build_graph(st_settings, toolbox=tb, risk_engine=risk, interrupt_before_executor=True)

    executor = TradeExecutor(st_settings, gamma=gamma, clob=clob)

    signal_counts: list[int] = []
    approved_counts: list[int] = []
    errors: list[str] = []

    for i in range(int(cycles)):
        try:
            signals = scanner.generate_signals()
            signal_counts.append(len(signals))

            for s in signals:
                portfolio.register_token(s.token_id, s.market_id, s.category)
                if s.suggested_price is not None:
                    portfolio.update_mark_price(s.token_id, s.suggested_price)

            init_state: GraphState = {
                "messages": [],
                "market_context": {"timestamp": datetime.now(timezone.utc).isoformat(), "cycle_idx": i},
                "signals": select_top_signals(signals, limit=50),
                "portfolio": portfolio.get_state(),
                "decisions": [],
                "research_data": {},
                "confidence_scores": {},
                "execution_enabled": False,
                "supervisor": {},
                "errors": [],
            }
            final_state = asyncio.run(run_cycle(bundle, settings=st_settings, initial_state=init_state))
            decisions = final_state.get("decisions", []) or []
            last = decisions[-1] if decisions else None
            if last is None:
                approved_counts.append(0)
            else:
                approved_counts.append(len(getattr(last, "signals", []) or []))

            if isinstance(last, AgentDecision):
                result = executor.execute_decision(last, portfolio, risk)
                audit_event(st_settings, "system_test_cycle", {"cycle_idx": i, "placed": result.placed, "skipped": result.skipped})
        except Exception as e:
            errors.append(str(e))

    portfolio.close()
    ok = len(errors) == 0
    return SystemTestResult(ok=ok, cycles=int(cycles), signal_counts=signal_counts, approved_counts=approved_counts, errors=errors)
