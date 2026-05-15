from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import json
from loguru import logger

from src.core.config import Settings
from src.core.models import Market, TradeSignal
from src.data.clob_client import PolyClobClient
from src.data.data_api_client import DataAPIClient
from src.data.gamma_client import GammaClient


class MarketScanner:
    """
    Phase 2 market scanner.

    Outputs TradeSignal objects that are evaluated by RiskEngine.
    """

    def __init__(
        self,
        settings: Settings,
        gamma: GammaClient,
        data_api: DataAPIClient,
        *,
        clob: PolyClobClient | None = None,
    ) -> None:
        self._settings = settings
        self._gamma = gamma
        self._data_api = data_api
        self._clob = clob

    def scan_mispricings(self) -> list[TradeSignal]:
        """
        Detect basic YES/NO parity deviations.

        Rule:
        - If YES + NO < min_sum: buy both legs (arb_parity)
        - If YES + NO > max_sum: sell both legs (arb_parity) (reduce-only logic is handled by RiskEngine/Portfolio later)
        """
        markets = self._gamma.get_markets({"limit": self._settings.scanner_market_limit, "active": True})
        signals: list[TradeSignal] = []

        for m in markets:
            yes_price, no_price = self._extract_yes_no_prices(m)
            if yes_price is None or no_price is None:
                continue

            s = float(yes_price + no_price)
            if s < self._settings.scanner_mispricing_min_sum:
                side: str = "buy"
            elif s > self._settings.scanner_mispricing_max_sum:
                side = "sell"
            else:
                continue

            deviation = abs(s - 1.0)
            confidence = min(1.0, max(0.2, deviation / 0.02))

            for token_id, px in [(m.token_ids[0], yes_price), (m.token_ids[1], no_price)]:
                signals.append(
                    TradeSignal(
                        strategy_id="scanner",
                        market_id=m.id,
                        category=m.category,
                        token_id=token_id,
                        side=side,  # type: ignore[arg-type]
                        edge_type="arb_parity",
                        confidence=confidence,
                        expected_edge=(1.0 - s) / 2.0 if side == "buy" else (s - 1.0) / 2.0,
                        suggested_price=px,
                        rationale=f"YES+NO parity deviation: sum={s:.4f}",
                        metadata={"yes_no_sum": s, "deviation": deviation},
                    )
                )

        logger.info("scan_mispricings completed", signals=len(signals))
        return signals

    def scan_whale_activity(self) -> list[TradeSignal]:
        """
        Scan top traders and recent large trades (best-effort).

        If Data API isn't configured, returns an empty list.
        """
        try:
            top_traders = self._data_api.get_top_traders(limit=10)
        except Exception as e:
            logger.debug("scan_whale_activity skipped: {}", e)
            return []

        threshold = self._settings.scanner_whale_trade_usd_threshold
        signals: list[TradeSignal] = []

        for trader in top_traders:
            wallet = trader.get("wallet") or trader.get("address")
            if not wallet:
                continue

            try:
                trades = self._data_api.get_wallet_trades(str(wallet), limit=50)
            except Exception:
                continue

            for t in trades:
                notional = self._extract_trade_notional_usd(t)
                if notional is None or notional < threshold:
                    continue

                token_id = t.get("token_id") or t.get("tokenId") or t.get("outcomeTokenId")
                side = t.get("side") or t.get("action")
                if not token_id or side not in {"buy", "sell"}:
                    continue

                m_id = t.get("market_id") or t.get("marketId")
                confidence = min(1.0, 0.4 + (notional / (threshold * 5)))

                signals.append(
                    TradeSignal(
                        strategy_id="scanner",
                        market_id=str(m_id) if m_id is not None else None,
                        token_id=str(token_id),
                        side=side,  # type: ignore[arg-type]
                        edge_type="whale_activity",
                        confidence=float(confidence),
                        expected_edge=None,
                        suggested_price=self._extract_trade_price(t),
                        rationale=f"Large trader activity: wallet={wallet} notional_usd={notional:.2f}",
                        metadata={"wallet": str(wallet), "notional_usd": float(notional)},
                    )
                )

        logger.info("scan_whale_activity completed", signals=len(signals))
        return signals

    def scan_high_volume_or_news(self) -> list[TradeSignal]:
        """
        Basic attention filters: volume, and proximity to resolution.

        This produces informational signals by default (confidence low, no expected edge).
        """
        markets = self._gamma.get_markets({"limit": self._settings.scanner_market_limit, "active": True})
        signals: list[TradeSignal] = []

        now = datetime.now(timezone.utc)
        window = timedelta(hours=self._settings.scanner_resolution_window_hours)

        for m in markets:
            near_resolution = m.end_date is not None and (m.end_date - now) <= window
            vol = self._extract_market_volume(m)
            if not near_resolution and vol is None:
                continue

            if vol is not None and vol <= 0 and not near_resolution:
                continue

            reason_parts: list[str] = []
            if near_resolution:
                reason_parts.append("near_resolution")
            if vol is not None:
                reason_parts.append(f"volume={vol:.2f}")

            signals.append(
                TradeSignal(
                    strategy_id="scanner",
                    market_id=m.id,
                    category=m.category,
                    token_id=m.token_ids[0] if m.token_ids else (m.id or "unknown"),
                    side="buy",
                    edge_type="attention",
                    confidence=0.15,
                    expected_edge=None,
                    rationale="; ".join(reason_parts),
                    metadata={"near_resolution": near_resolution, "volume": vol},
                )
            )

        logger.info("scan_high_volume_or_news completed", signals=len(signals))
        return signals

    def generate_signals(self) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        signals.extend(self.scan_mispricings())
        signals.extend(self.scan_whale_activity())
        signals.extend(self.scan_high_volume_or_news())
        return signals

    def _extract_yes_no_prices(self, market: Market) -> tuple[Decimal | None, Decimal | None]:
        if len(market.token_ids) < 2:
            return None, None

        if self._clob is not None:
            yes = self._clob.get_mid_price(market.token_ids[0])
            no = self._clob.get_mid_price(market.token_ids[1])
            if yes is not None and no is not None:
                return yes, no

        raw = market.raw or {}
        for key in ("outcomePrices", "outcome_prices", "outcomePrice", "outcome_price"):
            v = raw.get(key)
            if v is None:
                continue
            if isinstance(v, list) and len(v) >= 2:
                try:
                    return Decimal(str(v[0])), Decimal(str(v[1]))
                except Exception:
                    pass
            if isinstance(v, str):
                s = v.strip()
                if s.startswith("["):
                    try:
                        parsed = json.loads(s)
                        if isinstance(parsed, list) and len(parsed) >= 2:
                            return Decimal(str(parsed[0])), Decimal(str(parsed[1]))
                    except Exception:
                        pass

        for key_yes, key_no in (("yesPrice", "noPrice"), ("yes_price", "no_price")):
            if key_yes in raw and key_no in raw:
                try:
                    return Decimal(str(raw[key_yes])), Decimal(str(raw[key_no]))
                except Exception:
                    pass

        outcomes = raw.get("outcomes")
        if isinstance(outcomes, list) and len(outcomes) >= 2:
            pxs: list[Decimal] = []
            for o in outcomes[:2]:
                if not isinstance(o, dict):
                    continue
                p = o.get("price") or o.get("mid") or o.get("probability")
                if p is None:
                    continue
                try:
                    pxs.append(Decimal(str(p)))
                except Exception:
                    continue
            if len(pxs) >= 2:
                return pxs[0], pxs[1]

        return None, None

    @staticmethod
    def _extract_market_volume(market: Market) -> float | None:
        raw = market.raw or {}
        for key in ("volume", "volumeUsd", "volumeUSD", "volume_24h", "volume24h"):
            v = raw.get(key)
            if v is None:
                continue
            try:
                return float(v)
            except Exception:
                continue
        return None

    @staticmethod
    def _extract_trade_notional_usd(trade: dict[str, Any]) -> float | None:
        for key in ("notional", "notionalUsd", "notionalUSD", "amountUsd", "amountUSD", "valueUsd", "valueUSD"):
            v = trade.get(key)
            if v is None:
                continue
            try:
                return float(v)
            except Exception:
                continue

        price = MarketScanner._extract_trade_price(trade)
        size = trade.get("size") or trade.get("quantity") or trade.get("amount")
        if price is not None and size is not None:
            try:
                return float(price) * float(size)
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_trade_price(trade: dict[str, Any]) -> Decimal | None:
        for key in ("price", "avgPrice", "avg_price", "fillPrice", "fill_price"):
            v = trade.get(key)
            if v is None:
                continue
            try:
                return Decimal(str(v))
            except Exception:
                continue
        return None
