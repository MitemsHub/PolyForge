from __future__ import annotations

from decimal import Decimal

from src.core.models import TradeSignal
from src.core.portfolio import Portfolio
from src.risk.risk_engine import RiskEngine


def test_position_sizing_positive(settings) -> None:
    portfolio = Portfolio(settings)
    risk = RiskEngine(settings, portfolio)

    sig = TradeSignal(
        strategy_id="t",
        market_id="M1",
        category="C1",
        token_id="T1",
        side="buy",
        confidence=0.8,
        expected_edge=0.04,
        suggested_price=Decimal("0.5"),
        metadata={"volatility": 1.0},
    )
    portfolio.register_token(sig.token_id, sig.market_id, sig.category)
    portfolio.update_mark_price(sig.token_id, sig.suggested_price or Decimal("0.5"))

    size = risk.calculate_position_size(sig, portfolio.get_state())
    assert size > 0
    portfolio.close()


def test_exposure_caps_clip_to_zero(settings) -> None:
    portfolio = Portfolio(settings)
    risk = RiskEngine(settings, portfolio)

    sig = TradeSignal(
        strategy_id="t",
        market_id="M1",
        category="C1",
        token_id="T1",
        side="buy",
        confidence=0.9,
        expected_edge=0.05,
        suggested_price=Decimal("0.5"),
        metadata={"volatility": 1.0},
    )
    portfolio.register_token(sig.token_id, sig.market_id, sig.category)
    portfolio.update_mark_price(sig.token_id, sig.suggested_price or Decimal("0.5"))

    existing_exposure_value = Decimal("2000")
    existing_size = existing_exposure_value / Decimal("0.5")
    portfolio.apply_trade(
        trade=_trade(token_id="T1", side="buy", price=Decimal("0.5"), size=existing_size),
    )

    size = risk.calculate_position_size(sig, portfolio.get_state())
    assert size == 0
    portfolio.close()


def _trade(*, token_id: str, side: str, price: Decimal, size: Decimal):
    from src.core.models import Trade

    return Trade(
        trade_id="t",
        market_id="M1",
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        fee=Decimal("0"),
    )
