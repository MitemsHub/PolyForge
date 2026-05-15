from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from loguru import logger

from src.core.config import Settings
from src.core.models import Order, TradeSignal


@dataclass(frozen=True)
class OrderPreview:
    token_id: str
    side: str
    limit_price: Decimal
    size: Decimal
    tif: str
    post_only: bool
    tick_size: Decimal | None
    estimated_slippage_bps: int | None
    estimated_notional_usd: Decimal
    expected_edge_points: Decimal | None
    expected_value_usd: Decimal | None
    reasons: list[str]


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def round_price_to_tick(price: Decimal, tick_size: Decimal, side: str) -> Decimal:
    if tick_size <= 0:
        return price
    q = (price / tick_size).quantize(Decimal("1"), rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING)
    out = q * tick_size
    if out <= 0:
        return tick_size
    if out >= 1:
        return Decimal("0.999")
    return out


def estimate_slippage_bps(order_book: dict[str, Any], *, side: str, size: Decimal) -> int | None:
    levels_key = "asks" if side == "buy" else "bids"
    levels = order_book.get(levels_key) or []
    if not isinstance(levels, list) or not levels:
        return None

    remaining = size
    notional = Decimal("0")
    filled = Decimal("0")
    top_price = None

    for level in levels:
        if not isinstance(level, dict):
            continue
        px = _to_decimal(level.get("price"))
        qty = _to_decimal(level.get("size") or level.get("quantity"))
        if px is None or qty is None or px <= 0 or qty <= 0:
            continue
        if top_price is None:
            top_price = px

        take = min(remaining, qty)
        notional += take * px
        filled += take
        remaining -= take
        if remaining <= 0:
            break

    if filled <= 0 or top_price is None:
        return None

    vwap = notional / filled
    slippage = (vwap - top_price) / top_price if side == "buy" else (top_price - vwap) / top_price
    bps = int(max(0, slippage * Decimal("10000")))
    return bps


def build_order_preview(
    settings: Settings,
    *,
    signal: TradeSignal,
    size: Decimal,
    tick_size: Decimal | None,
    order_book: dict[str, Any] | None,
) -> OrderPreview:
    reasons: list[str] = []
    side = signal.side
    tif = settings.default_time_in_force
    post_only = settings.default_post_only

    price = signal.suggested_price
    if price is None:
        price = _to_decimal(signal.metadata.get("limit_price"))
    if price is None:
        price = Decimal("0.5")
        reasons.append("missing_price_defaulted")

    if tick_size is not None:
        price = round_price_to_tick(price, tick_size, side)

    notional = size * price

    if float(notional) > settings.max_order_size_usd:
        reasons.append("clipped_max_order_size_usd")
        notional_cap = Decimal(str(settings.max_order_size_usd))
        size = notional_cap / price
        notional = size * price

    if float(notional) < settings.min_order_size_usd:
        reasons.append("below_min_order_size_usd")

    slippage_bps = None
    if order_book is not None:
        slippage_bps = estimate_slippage_bps(order_book, side=side, size=size)
        if slippage_bps is not None and slippage_bps > settings.max_order_slippage_bps:
            reasons.append("slippage_exceeds_limit")

    edge = Decimal(str(signal.expected_edge)) if signal.expected_edge is not None else None
    expected_value = (abs(edge) * size) if edge is not None else None

    return OrderPreview(
        token_id=signal.token_id,
        side=side,
        limit_price=price,
        size=size,
        tif=tif,
        post_only=post_only,
        tick_size=tick_size,
        estimated_slippage_bps=slippage_bps,
        estimated_notional_usd=notional,
        expected_edge_points=edge,
        expected_value_usd=expected_value,
        reasons=reasons,
    )


def preview_to_order(preview: OrderPreview) -> Order:
    return Order(
        token_id=preview.token_id,
        side=preview.side,  # type: ignore[arg-type]
        price=preview.limit_price,
        size=preview.size,
        tif=preview.tif,  # type: ignore[arg-type]
    )


def extract_tick_size(market_raw: dict[str, Any]) -> Decimal | None:
    for k in ("tickSize", "tick_size", "tick"):
        v = market_raw.get(k)
        d = _to_decimal(v)
        if d is not None and d > 0:
            return d
    return None


def extract_neg_risk(market_raw: dict[str, Any]) -> bool | None:
    for k in ("negRisk", "neg_risk", "negativeRisk", "negative_risk"):
        if k in market_raw:
            v = market_raw.get(k)
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in {"1", "true", "yes"}
    return None


def log_order_preview(preview: OrderPreview) -> None:
    logger.info(
        "Order preview",
        token_id=preview.token_id,
        side=preview.side,
        price=float(preview.limit_price),
        size=float(preview.size),
        notional=float(preview.estimated_notional_usd),
        expected_edge=float(preview.expected_edge_points) if preview.expected_edge_points is not None else None,
        expected_value=float(preview.expected_value_usd) if preview.expected_value_usd is not None else None,
        tif=preview.tif,
        post_only=preview.post_only,
        tick_size=float(preview.tick_size) if preview.tick_size is not None else None,
        slippage_bps=preview.estimated_slippage_bps,
        reasons=preview.reasons,
    )
