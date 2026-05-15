from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.core.models import Market
from src.strategies.scanner import MarketScanner


class FakeGamma:
    def __init__(self, markets: list[Market]) -> None:
        self._markets = markets

    def get_markets(self, _: dict | None = None) -> list[Market]:
        return list(self._markets)


class FakeDataAPI:
    def get_top_traders(self, limit: int = 10):
        raise RuntimeError("disabled")


def test_scan_mispricings_generates_two_signals(settings) -> None:
    m = Market(
        id="M1",
        category="C1",
        token_ids=["YES", "NO"],
        raw={"yesPrice": "0.48", "noPrice": "0.48"},
    )
    scanner = MarketScanner(settings, gamma=FakeGamma([m]), data_api=FakeDataAPI(), clob=None)
    signals = scanner.scan_mispricings()
    assert len(signals) == 2
    assert {s.token_id for s in signals} == {"YES", "NO"}
    assert all(s.side == "buy" for s in signals)


def test_scan_whale_activity_skips_when_data_api_unavailable(settings) -> None:
    m = Market(id="M1", category="C1", token_ids=["YES", "NO"], raw={"yesPrice": "0.5", "noPrice": "0.5"})
    scanner = MarketScanner(settings, gamma=FakeGamma([m]), data_api=FakeDataAPI(), clob=None)
    signals = scanner.scan_whale_activity()
    assert signals == []


def test_scan_high_volume_or_news_near_resolution(settings) -> None:
    now = datetime.now(timezone.utc)
    m = Market(
        id="M1",
        category="C1",
        token_ids=["YES", "NO"],
        end_date=now + timedelta(hours=max(1, settings.scanner_resolution_window_hours - 1)),
        raw={},
    )
    scanner = MarketScanner(settings, gamma=FakeGamma([m]), data_api=FakeDataAPI(), clob=None)
    signals = scanner.scan_high_volume_or_news()
    assert len(signals) == 1
    assert signals[0].edge_type == "attention"
