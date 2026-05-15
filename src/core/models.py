from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator


class Model(BaseModel):
    """
    Base model for PolyForge domain objects.

    We default to `extra="ignore"` to be resilient to upstream API changes while we
    stabilize schemas. For trading and persistence flows, prefer explicit parsing
    and/or stricter models.
    """

    model_config = ConfigDict(extra="ignore")


class MarketStatus(str, Enum):
    active = "active"
    closed = "closed"
    resolved = "resolved"
    unknown = "unknown"


class Market(Model):
    """
    Canonical market representation used across the system.

    Gamma API and CLOB responses vary. This model is intentionally permissive in Phase 1.
    """

    id: str | None = Field(default=None, description="Gamma market id (string).")
    condition_id: str | None = Field(default=None, description="CLOB condition id when available.")
    slug: str | None = None
    question: str | None = None
    category: str | None = None
    status: MarketStatus = MarketStatus.unknown
    end_date: datetime | None = None

    outcomes: list[str] = Field(default_factory=list)
    token_ids: list[str] = Field(default_factory=list, description="Outcome token IDs used for CLOB trading.")

    raw: dict[str, Any] = Field(default_factory=dict, description="Raw upstream payload for debugging.")


OrderSide = Literal["buy", "sell"]
TimeInForce = Literal["GTC", "FOK", "FAK"]


class Order(Model):
    """
    Normalized order representation.

    Prices are probabilities in [0, 1] for Polymarket outcomes.
    """

    order_id: str | None = None
    token_id: str
    side: OrderSide
    price: Decimal
    size: Decimal
    tif: TimeInForce = "GTC"
    created_at: datetime | None = None
    status: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("price")
    @classmethod
    def _validate_price(cls, v: Decimal) -> Decimal:
        if v <= 0 or v >= 1:
            raise ValueError("Order price must be in (0, 1) for outcome probabilities")
        return v

    @field_validator("size")
    @classmethod
    def _validate_size(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Order size must be positive")
        return v


class Position(Model):
    """
    Portfolio position for a specific outcome token.
    """

    token_id: str
    size: Decimal
    avg_price: Decimal | None = None
    realized_pnl: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TradeSignal(Model):
    """
    Strategy output: a proposed trade intent (not an executable order).

    TradeSignals are evaluated by the risk engine and transformed into Orders by
    the execution layer. Strategies never place orders directly.
    """

    strategy_id: str
    market_id: str | None = None
    category: str | None = None
    token_id: str
    side: OrderSide
    edge_type: str | None = Field(
        default=None,
        description="Classifier for signal edge source (e.g., arb_parity, whale_copy, volume_spike).",
    )

    confidence: float = Field(ge=0.0, le=1.0)
    expected_edge: float | None = Field(
        default=None, description="Expected edge vs. market probability, in probability points."
    )

    suggested_price: Decimal | None = None
    suggested_size: Decimal | None = None

    max_slippage_bps: int = Field(default=50, ge=0, le=10_000)
    rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Trade(Model):
    """
    Normalized fill/trade event (used for portfolio updates and backtesting).
    """

    trade_id: str | None = None
    market_id: str | None = None
    token_id: str
    side: OrderSide
    price: Decimal
    size: Decimal
    fee: Decimal = Decimal("0")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = Field(default_factory=dict)

class RiskMetrics(Model):
    """
    Snapshot of risk metrics used for gating and monitoring.
    """

    gross_exposure: Decimal = Decimal("0")
    net_exposure: Decimal = Decimal("0")
    max_drawdown: Decimal | None = None
    var_95: Decimal | None = None
    slippage_estimate_bps: int | None = None
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PortfolioState(Model):
    """
    Portfolio state used by strategies, risk checks, and allocation.
    """

    cash_balance: Decimal | None = None
    positions: list[Position] = Field(default_factory=list)
    open_orders: list[Order] = Field(default_factory=list)
    mark_prices: dict[str, Decimal] = Field(
        default_factory=dict, description="Latest mark prices by token_id (for valuation)."
    )
    risk: RiskMetrics = Field(default_factory=RiskMetrics)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentDecision(Model):
    """
    Decision artifact produced by an agent workflow.
    """

    cycle_id: str
    approved: bool
    signals: list[TradeSignal] = Field(default_factory=list)
    planned_orders: list[Order] = Field(default_factory=list)
    risk: RiskMetrics | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
