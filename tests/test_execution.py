from __future__ import annotations

from decimal import Decimal

from src.core.models import AgentDecision, Market, TradeSignal
from src.core.portfolio import Portfolio
from src.execution.executor import TradeExecutor
from src.execution.order_builder import build_order_preview, round_price_to_tick
from src.risk.risk_engine import RiskEngine


class FakeGamma:
    def get_market_by_id(self, market_id: str) -> Market:
        return Market(id=market_id, token_ids=["T1"], raw={"tickSize": "0.01"})


class FakeClob:
    def get_order_book(self, token_id: str):
        return {"bids": [{"price": "0.49", "size": "10"}], "asks": [{"price": "0.51", "size": "10"}]}

    def post_order_args(self, *_args, **_kwargs):
        return {"ok": False, "blocked": True, "reason": "dry_run_or_trading_disabled"}

    def cancel_all_orders(self):
        return {"ok": False, "blocked": True, "reason": "dry_run_or_trading_disabled"}

    def get_open_orders(self):
        return []

    def get_fills(self):
        return []

    def get_balance(self):
        return {"available": "0"}


def test_round_price_to_tick() -> None:
    assert round_price_to_tick(Decimal("0.503"), Decimal("0.01"), "buy") == Decimal("0.50")
    assert round_price_to_tick(Decimal("0.503"), Decimal("0.01"), "sell") == Decimal("0.51")


def test_build_order_preview_clips_max_order(settings) -> None:
    sig = TradeSignal(
        strategy_id="s",
        market_id="M1",
        category="C1",
        token_id="T1",
        side="buy",
        confidence=0.9,
        expected_edge=0.03,
        suggested_price=Decimal("0.5"),
    )
    preview = build_order_preview(
        settings,
        signal=sig,
        size=Decimal("100000"),
        tick_size=Decimal("0.01"),
        order_book={"asks": [{"price": "0.5", "size": "1"}], "bids": [{"price": "0.49", "size": "1"}]},
    )
    assert "clipped_max_order_size_usd" in preview.reasons
    assert float(preview.estimated_notional_usd) <= settings.max_order_size_usd + 1e-6


def test_executor_dry_run_path(settings) -> None:
    portfolio = Portfolio(settings)
    risk = RiskEngine(settings, portfolio)
    executor = TradeExecutor(settings, gamma=FakeGamma(), clob=FakeClob())

    sig = TradeSignal(
        strategy_id="s",
        market_id="M1",
        category="C1",
        token_id="T1",
        side="buy",
        confidence=0.8,
        expected_edge=0.03,
        suggested_price=Decimal("0.5"),
    )
    portfolio.register_token(sig.token_id, sig.market_id, sig.category)
    portfolio.update_mark_price(sig.token_id, Decimal("0.5"))

    decision = AgentDecision(cycle_id="c1", approved=True, signals=[sig], planned_orders=[])
    result = executor.execute_decision(decision, portfolio, risk)
    assert result.dry_run is True
    assert result.errors == 0
    portfolio.close()
