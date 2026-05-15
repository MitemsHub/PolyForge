from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from src.core.models import TradeSignal


def filter_signals_by_confidence(signals: Iterable[TradeSignal], threshold: float) -> list[TradeSignal]:
    return [s for s in signals if float(s.confidence) >= float(threshold)]


def compute_expected_value_usd(signal: TradeSignal, *, size: Decimal) -> Decimal | None:
    if signal.expected_edge is None:
        return None
    edge = Decimal(str(signal.expected_edge))
    return abs(edge) * size


def select_top_signals(signals: Iterable[TradeSignal], limit: int = 10) -> list[TradeSignal]:
    ranked = sorted(
        list(signals),
        key=lambda s: (float(s.confidence), float(s.expected_edge or 0.0)),
        reverse=True,
    )
    return ranked[:limit]
