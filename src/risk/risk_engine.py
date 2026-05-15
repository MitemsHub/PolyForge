from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from src.core.config import Settings
from src.core.models import PortfolioState, RiskMetrics, Trade, TradeSignal
from src.core.portfolio import ExposureSnapshot, Portfolio


@dataclass
class CircuitBreakerState:
    active: bool = False
    reason: str | None = None
    activated_at: datetime | None = None


class RiskEngine:
    """
    Risk engine for PolyForge.

    Phase 2 objectives:
    - Enforce hard exposure caps (per-market, correlated group, portfolio)
    - Apply volatility-adjusted, Kelly-inspired sizing with strict clipping
    - Maintain a drawdown-based circuit breaker
    """

    def __init__(self, settings: Settings, portfolio: Portfolio) -> None:
        self._settings = settings
        self._portfolio = portfolio
        self._circuit = CircuitBreakerState()

    @property
    def circuit_breaker_status(self) -> dict[str, Any]:
        return {
            "active": self._circuit.active,
            "reason": self._circuit.reason,
            "activated_at": self._circuit.activated_at.isoformat() if self._circuit.activated_at else None,
            "cooldown_s": self._settings.circuit_breaker_cooldown_s,
        }

    def _equity(self, portfolio: PortfolioState) -> Decimal:
        equity = self._portfolio.compute_equity()
        if equity <= 0:
            cash = portfolio.cash_balance or Decimal("0")
            return cash if cash > 0 else Decimal("0")
        return equity

    def _exposure(self) -> ExposureSnapshot:
        return self._portfolio.get_exposure_snapshot()

    def get_current_exposure(self) -> dict[str, Any]:
        snap = self._exposure()
        equity = self._equity(self._portfolio.get_state())
        equity_f = float(equity) if equity != 0 else 0.0

        per_market_pct = {
            k: (float(v) / equity_f if equity_f else 0.0) for k, v in snap.per_market_value.items()
        }
        per_category_pct = {
            k: (float(v) / equity_f if equity_f else 0.0) for k, v in snap.per_category_value.items()
        }
        total_pct = float(snap.total_value) / equity_f if equity_f else 0.0

        return {
            "equity": float(equity),
            "total_exposure_value": float(snap.total_value),
            "total_exposure_pct": total_pct,
            "per_market_value": {k: float(v) for k, v in snap.per_market_value.items()},
            "per_market_pct": per_market_pct,
            "per_category_value": {k: float(v) for k, v in snap.per_category_value.items()},
            "per_category_pct": per_category_pct,
        }

    def daily_drawdown_check(self) -> tuple[bool, str]:
        """
        Enforce drawdown checks and update circuit breaker.

        Returns:
            (ok, reason)
        """
        now = datetime.now(timezone.utc).date()
        equity = self._portfolio.compute_equity()
        start = self._portfolio.ensure_daily_start_equity(now, equity)
        peak = self._portfolio.update_peak_equity(equity)

        if start <= 0:
            return True, "no_start_equity"

        drawdown = (equity - start) / start
        if drawdown <= -Decimal(str(self._settings.max_daily_drawdown_pct)):
            self._activate_circuit("daily_drawdown_limit_breached")
            logger.error(
                "Daily drawdown breach",
                day=now.isoformat(),
                start_equity=float(start),
                equity=float(equity),
                drawdown_pct=float(drawdown),
            )
            return False, "daily_drawdown_limit_breached"

        if peak > 0:
            total_dd = (equity - peak) / peak
            if total_dd <= -Decimal(str(self._settings.max_total_drawdown_pct)):
                self._activate_circuit("total_drawdown_limit_breached")
                logger.error(
                    "Total drawdown breach",
                    peak_equity=float(peak),
                    equity=float(equity),
                    drawdown_pct=float(total_dd),
                )
                return False, "total_drawdown_limit_breached"

        return True, "ok"

    def _activate_circuit(self, reason: str) -> None:
        if self._circuit.active:
            return
        self._circuit.active = True
        self._circuit.reason = reason
        self._circuit.activated_at = datetime.now(timezone.utc)

    def _circuit_allows(self) -> tuple[bool, str]:
        if not self._circuit.active:
            return True, "ok"

        activated_at = self._circuit.activated_at
        if activated_at is None:
            return False, "circuit_breaker_active"

        elapsed = (datetime.now(timezone.utc) - activated_at).total_seconds()
        if elapsed >= self._settings.circuit_breaker_cooldown_s:
            self._circuit = CircuitBreakerState()
            return True, "circuit_breaker_cooldown_elapsed"

        return False, "circuit_breaker_active"

    def check_trade_allowed(self, signal: TradeSignal) -> tuple[bool, str]:
        """
        Pre-trade validation gate.

        Returns:
            (allowed, reason)
        """
        ok, reason = self._circuit_allows()
        if not ok:
            logger.warning("Risk blocked trade (circuit breaker)", reason=reason, signal=signal.model_dump())
            return False, reason

        dd_ok, dd_reason = self.daily_drawdown_check()
        if not dd_ok:
            logger.warning("Risk blocked trade (drawdown)", reason=dd_reason, signal=signal.model_dump())
            return False, dd_reason

        if signal.confidence < 0.05:
            return False, "confidence_too_low"

        size = self.calculate_position_size(signal, self._portfolio.get_state())
        if size <= 0:
            return False, "size_zero_after_caps"

        return True, "ok"

    def calculate_position_size(self, signal: TradeSignal, portfolio: PortfolioState) -> Decimal:
        """
        Determine a position size for a signal.

        Mechanics:
        - Compute equity and existing exposures
        - Compute Kelly-inspired target fraction (half-Kelly style), clipped
        - Apply volatility adjustment (signal.metadata['volatility'])
        - Clip by per-trade risk budget and hard caps
        """
        equity = self._equity(portfolio)
        if equity <= 0:
            logger.warning("Equity unavailable for sizing; returning 0", equity=float(equity))
            return Decimal("0")

        price = signal.suggested_price
        if price is None:
            price = portfolio.mark_prices.get(signal.token_id)
        if price is None or price <= 0:
            logger.warning("Missing mark/suggested price; returning 0", token_id=signal.token_id)
            return Decimal("0")

        expected_edge = Decimal(str(signal.expected_edge)) if signal.expected_edge is not None else None
        p = price
        var = p * (Decimal("1") - p)
        var = var if var > Decimal("0.0001") else Decimal("0.0001")

        kelly = Decimal("0")
        if expected_edge is not None:
            kelly = (abs(expected_edge) / var) * Decimal("0.5")
        kelly = min(max(kelly, Decimal("0")), Decimal(str(self._settings.max_market_exposure_pct)))

        vol = Decimal(str(signal.metadata.get("volatility", 1.0)))
        vol = vol if vol > Decimal("0.1") else Decimal("0.1")
        kelly = kelly / vol

        base_risk = Decimal(str(self._settings.risk_per_trade_pct))
        base_risk = min(max(base_risk, Decimal(str(self._settings.risk_min_per_trade_pct))), Decimal(str(self._settings.risk_max_per_trade_pct)))

        conf_scale = Decimal(str(0.5 + 0.5 * float(signal.confidence)))
        risk_budget_value = equity * base_risk * conf_scale
        kelly_value = equity * kelly

        snap = self._exposure()
        exposure_market = snap.per_market_value.get(signal.market_id or "unknown", Decimal("0"))
        exposure_category = snap.per_category_value.get(signal.category or "unknown", Decimal("0"))

        market_cap_value = equity * Decimal(str(self._settings.max_market_exposure_pct))
        corr_cap_value = equity * Decimal(str(self._settings.max_correlated_exposure_pct))

        remaining_market = market_cap_value - exposure_market
        remaining_corr = corr_cap_value - exposure_category

        target_value = min(kelly_value, risk_budget_value, remaining_market, remaining_corr)
        if target_value <= 0:
            logger.info(
                "Sizing clipped to zero",
                market_id=signal.market_id,
                category=signal.category,
                exposure_market=float(exposure_market),
                exposure_category=float(exposure_category),
                remaining_market=float(remaining_market),
                remaining_corr=float(remaining_corr),
            )
            return Decimal("0")

        size = target_value / p

        hard_size_cap = signal.suggested_size
        if hard_size_cap is not None and hard_size_cap > 0:
            size = min(size, hard_size_cap)

        logger.info(
            "Position sizing",
            token_id=signal.token_id,
            market_id=signal.market_id,
            category=signal.category,
            price=float(p),
            expected_edge=float(expected_edge) if expected_edge is not None else None,
            confidence=float(signal.confidence),
            vol=float(vol),
            equity=float(equity),
            target_value=float(target_value),
            size=float(size),
        )
        return size

    def update_portfolio_after_trade(self, trade: Trade) -> None:
        self._portfolio.apply_trade(trade)
        logger.info("Portfolio updated after trade", trade=trade.model_dump(mode="json"))

    def update_risk_metrics(self) -> RiskMetrics:
        snap = self._exposure()
        portfolio = self._portfolio.get_state()
        equity = self._equity(portfolio)

        gross = snap.total_value
        net = Decimal("0")
        for p in portfolio.positions:
            mark = portfolio.mark_prices.get(p.token_id)
            if mark is None:
                continue
            net += p.size * mark

        metrics = RiskMetrics(gross_exposure=gross, net_exposure=net, last_updated=datetime.now(timezone.utc))
        portfolio.risk = metrics
        return metrics
