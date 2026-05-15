from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from src.agents.graph import build_graph, run_cycle
from src.agents.tools import AgentToolbox
from src.core.models import Market, TradeSignal
from src.core.portfolio import Portfolio
from src.risk.risk_engine import RiskEngine


class FakeGamma:
    def get_market_by_id(self, market_id: str) -> Market:
        return Market(id=market_id, token_ids=["YES", "NO"], raw={"tickSize": "0.001"})


class FakeDataAPI:
    def get_wallet_trades(self, wallet: str, limit: int = 50):
        return []


class FakeClob:
    def get_order_book(self, token_id: str):
        return {"bids": [{"price": "0.49", "size": "100"}], "asks": [{"price": "0.51", "size": "100"}]}


def test_agent_graph_runs_with_mock_llm(settings) -> None:
    portfolio = Portfolio(settings)
    risk = RiskEngine(settings, portfolio)
    tb = AgentToolbox(gamma=FakeGamma(), data_api=FakeDataAPI(), portfolio=portfolio, clob=FakeClob())
    bundle = build_graph(settings, toolbox=tb, risk_engine=risk, interrupt_before_executor=True)

    signals = [
        TradeSignal(
            strategy_id="scanner",
            market_id="M1",
            category="C1",
            token_id="YES",
            side="buy",
            confidence=0.8,
            expected_edge=0.04,
            suggested_price=Decimal("0.5"),
        )
    ]
    portfolio.register_token("YES", "M1", "C1")
    portfolio.update_mark_price("YES", Decimal("0.5"))

    initial_state = {
        "messages": [],
        "market_context": {"timestamp": datetime.now(timezone.utc).isoformat(), "signal_count": len(signals)},
        "signals": signals,
        "portfolio": portfolio.get_state(),
        "decisions": [],
        "research_data": {},
        "confidence_scores": {},
        "execution_enabled": False,
        "supervisor": {},
        "errors": [],
    }

    final_state = asyncio.run(run_cycle(bundle, settings=settings, initial_state=initial_state))
    decisions = final_state.get("decisions", []) or []
    assert len(decisions) >= 1
    assert getattr(decisions[-1], "cycle_id", None) is not None
    portfolio.close()
