from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import random

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from src.core.config import Settings
from src.core.models import AgentDecision, Order, Trade, TradeSignal
from src.core.portfolio import Portfolio
from src.data.clob_client import PolyClobClient
from src.data.gamma_client import GammaClient
from src.execution.order_builder import (
    OrderPreview,
    build_order_preview,
    extract_neg_risk,
    extract_tick_size,
    log_order_preview,
    preview_to_order,
)
from src.risk.risk_engine import RiskEngine
from src.security.audit_logger import audit_event
from src.security.wallet_manager import WalletManager


@dataclass(frozen=True)
class ExecutionResult:
    cycle_id: str
    dry_run: bool
    placed: int
    skipped: int
    errors: int
    orders: list[dict[str, Any]]
    trades: list[Trade]


class TradeExecutor:
    def __init__(self, settings: Settings, *, gamma: GammaClient, clob: PolyClobClient) -> None:
        self._settings = settings
        self._gamma = gamma
        self._clob = clob

    def execute_decision(self, agent_decision: AgentDecision, portfolio: Portfolio, risk_engine: RiskEngine) -> ExecutionResult:
        cycle_id = agent_decision.cycle_id
        dry_run = self._settings.dry_run or not self._settings.trading_enabled

        audit_event(
            self._settings,
            "executor_decision",
            {"cycle_id": cycle_id, "approved": agent_decision.approved, "signal_count": len(agent_decision.signals)},
        )

        orders_out: list[dict[str, Any]] = []
        trades: list[Trade] = []
        placed = 0
        skipped = 0
        errors = 0

        if not agent_decision.signals:
            return ExecutionResult(cycle_id=cycle_id, dry_run=True, placed=0, skipped=0, errors=0, orders=[], trades=[])

        live_allowed = self._settings.execute_enabled and self._settings.trading_enabled and (not self._settings.dry_run)
        if live_allowed:
            ok_bal, bal_reason = WalletManager(self._settings, clob=self._clob).ensure_min_balance()
            if not ok_bal:
                audit_event(self._settings, "wallet_balance_block", {"cycle_id": cycle_id, "reason": bal_reason})
                logger.error("Wallet balance check failed; aborting execution", reason=bal_reason)
                return ExecutionResult(cycle_id=cycle_id, dry_run=True, placed=0, skipped=len(agent_decision.signals), errors=0, orders=[], trades=[])

            if not self._confirm_first_live_run(portfolio):
                logger.error("Live trading confirmation failed; aborting execution")
                return ExecutionResult(cycle_id=cycle_id, dry_run=True, placed=0, skipped=len(agent_decision.signals), errors=0, orders=[], trades=[])

        for sig in agent_decision.signals:
            try:
                ok, reason = risk_engine.check_trade_allowed(sig)
                if not ok:
                    skipped += 1
                    orders_out.append({"signal": sig.model_dump(mode="json"), "skipped": True, "reason": reason})
                    audit_event(self._settings, "order_blocked_by_risk", {"cycle_id": cycle_id, "reason": reason, "token_id": sig.token_id, "market_id": sig.market_id})
                    continue

                max_size = sig.metadata.get("max_size")
                size = Decimal(str(max_size)) if max_size is not None else risk_engine.calculate_position_size(sig, portfolio.get_state())
                if size <= 0:
                    skipped += 1
                    orders_out.append({"signal": sig.model_dump(mode="json"), "skipped": True, "reason": "size_zero"})
                    continue

                preview, order, extra, order_book = self.build_order_from_signal(sig, size=size)
                log_order_preview(preview)
                audit_event(
                    self._settings,
                    "order_preview",
                    {
                        "cycle_id": cycle_id,
                        "token_id": preview.token_id,
                        "side": preview.side,
                        "limit_price": str(preview.limit_price),
                        "size": str(preview.size),
                        "notional_usd": str(preview.estimated_notional_usd),
                        "slippage_bps": preview.estimated_slippage_bps,
                        "reasons": preview.reasons,
                    },
                )

                if "below_min_order_size_usd" in preview.reasons:
                    skipped += 1
                    orders_out.append({"signal": sig.model_dump(mode="json"), "skipped": True, "reason": "below_min_order_size_usd", "preview": preview.__dict__})
                    audit_event(self._settings, "order_skipped", {"cycle_id": cycle_id, "reason": "below_min_order_size_usd", "token_id": sig.token_id})
                    continue

                if "slippage_exceeds_limit" in preview.reasons:
                    skipped += 1
                    orders_out.append({"signal": sig.model_dump(mode="json"), "skipped": True, "reason": "slippage_exceeds_limit", "preview": preview.__dict__})
                    audit_event(self._settings, "order_skipped", {"cycle_id": cycle_id, "reason": "slippage_exceeds_limit", "token_id": sig.token_id})
                    continue

                agent_decision.planned_orders.append(order)

                if dry_run or not live_allowed:
                    trade = self._simulate_trade(sig, preview, order_book=order_book)
                    if trade.size > 0:
                        trades.append(trade)
                        if self._settings.paper_trading_enabled:
                            portfolio.apply_trade(trade)
                            if self._settings.paper_use_live_mid_prices and trade.raw.get("mid_price") is not None:
                                try:
                                    portfolio.update_mark_price(sig.token_id, Decimal(str(trade.raw["mid_price"])))
                                except Exception:
                                    pass
                        orders_out.append({"signal": sig.model_dump(mode="json"), "dry_run": True, "preview": preview.__dict__, "paper_fill": trade.raw})
                    else:
                        skipped += 1
                        orders_out.append({"signal": sig.model_dump(mode="json"), "dry_run": True, "preview": preview.__dict__, "skipped": True, "reason": "no_fill"})
                    audit_event(
                        self._settings,
                        "order_simulated",
                        {"cycle_id": cycle_id, "token_id": sig.token_id, "trade_id": trade.trade_id, "paper_trading": bool(self._settings.paper_trading_enabled)},
                    )
                    continue

                audit_event(self._settings, "order_attempt", {"cycle_id": cycle_id, "order": order.model_dump(mode="json")})
                resp = self.place_order(order, extra_kwargs=extra)
                placed += 1
                orders_out.append({"signal": sig.model_dump(mode="json"), "dry_run": False, "preview": preview.__dict__, "response": resp})
                audit_event(self._settings, "order_response", {"cycle_id": cycle_id, "token_id": sig.token_id, "response": resp})
            except Exception as e:
                errors += 1
                logger.exception("Execution error")
                orders_out.append({"signal": sig.model_dump(mode="json"), "error": str(e)})
                audit_event(self._settings, "order_error", {"cycle_id": cycle_id, "error": str(e)})

        return ExecutionResult(
            cycle_id=cycle_id,
            dry_run=bool(dry_run or not live_allowed),
            placed=placed,
            skipped=skipped,
            errors=errors,
            orders=orders_out,
            trades=trades,
        )

    def build_order_from_signal(self, signal: TradeSignal, *, size: Decimal) -> tuple[OrderPreview, Order, dict[str, Any], dict[str, Any] | None]:
        market_raw: dict[str, Any] = {}
        if signal.market_id:
            try:
                m = self._gamma.get_market_by_id(signal.market_id)
                market_raw = m.raw
            except Exception:
                market_raw = {}

        tick = extract_tick_size(market_raw)
        neg_risk = extract_neg_risk(market_raw)

        order_book = None
        try:
            order_book = self._clob.get_order_book(signal.token_id)
        except Exception:
            order_book = None

        preview = build_order_preview(
            self._settings,
            signal=signal,
            size=size,
            tick_size=tick,
            order_book=order_book,
        )

        order = preview_to_order(preview)
        extra: dict[str, Any] = {"post_only": preview.post_only}
        if neg_risk is not None:
            extra["neg_risk"] = neg_risk
        return preview, order, extra, order_book

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=3))
    def place_order(self, order: Order, *, extra_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._settings.dry_run or not self._settings.trading_enabled:
            return {"ok": False, "blocked": True, "reason": "dry_run_or_trading_disabled", "order": order.model_dump(mode="json")}

        order_args, order_type = self._to_order_args(order)
        resp = self._clob.post_order_args(order_args, order_type, **(extra_kwargs or {}))
        logger.info("Order placed", response=resp)
        return resp

    def cancel_all_orders(self) -> dict[str, Any]:
        resp = self._clob.cancel_all_orders()
        logger.warning("Cancel-all executed", response=resp)
        audit_event(self._settings, "cancel_all", {"response": resp})
        return resp

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._clob.get_open_orders()

    def get_fills(self) -> list[dict[str, Any]]:
        return self._clob.get_fills()

    def _to_order_args(self, order: Order) -> tuple[Any, Any]:
        from py_clob_client_v2 import OrderArgs, OrderType, Side

        side = Side.BUY if order.side == "buy" else Side.SELL
        order_args = OrderArgs(
            token_id=order.token_id,
            price=float(order.price),
            side=side,
            size=float(order.size),
        )
        order_type = getattr(OrderType, order.tif, OrderType.GTC)
        return order_args, order_type

    def _confirm_first_live_run(self, portfolio: Portfolio) -> bool:
        confirmed = (portfolio.get_meta("live_confirmed") or "").strip().lower() == "true"
        if confirmed:
            return True

        phrase = self._settings.live_confirm_phrase
        env_val = (self._settings.live_confirm_env or "").strip()
        if env_val == phrase:
            portfolio.set_meta("live_confirmed", "true")
            return True

        try:
            prompt = f"Type '{phrase}' to confirm FIRST LIVE RUN (anything else aborts): "
            val = input(prompt).strip()
        except EOFError:
            return False

        if val != phrase:
            return False

        portfolio.set_meta("live_confirmed", "true")
        return True

    def _simulate_trade(self, signal: TradeSignal, preview: OrderPreview, *, order_book: dict[str, Any] | None) -> Trade:
        rng = random.Random(f"{signal.market_id}:{signal.token_id}:{preview.side}:{datetime.now(timezone.utc).isoformat()}")

        bids = ((order_book or {}).get("bids") or []) if isinstance(order_book, dict) else []
        asks = ((order_book or {}).get("asks") or []) if isinstance(order_book, dict) else []

        best_bid = Decimal(str(bids[0]["price"])) if bids else None
        best_ask = Decimal(str(asks[0]["price"])) if asks else None
        bid_sz = Decimal(str(bids[0]["size"])) if bids else None
        ask_sz = Decimal(str(asks[0]["size"])) if asks else None

        mid: Decimal | None = None
        spread: Decimal | None = None
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / Decimal("2")
            spread = best_ask - best_bid

        if mid is None:
            mid = preview.limit_price
        if spread is None:
            spread = Decimal("0")

        slip = (mid * Decimal(str(self._settings.paper_slippage_bps)) / Decimal("10000")).copy_abs()
        half_spread = (spread / Decimal("2")).copy_abs()

        if preview.side == "buy":
            fill_px = mid + half_spread + slip
            if fill_px > preview.limit_price:
                fill_px = Decimal("0")
        else:
            fill_px = mid - half_spread - slip
            if fill_px < preview.limit_price:
                fill_px = Decimal("0")

        requested = preview.size
        top_cap = ask_sz if preview.side == "buy" else bid_sz
        cap = top_cap if top_cap is not None else requested
        cap = max(Decimal("0"), cap)

        frac = 1.0
        if rng.random() < float(self._settings.paper_partial_fill_probability):
            lo = float(self._settings.paper_partial_fill_min)
            hi = float(self._settings.paper_partial_fill_max)
            frac = max(0.0, min(1.0, rng.uniform(lo, hi)))

        filled = min(requested * Decimal(str(frac)), cap) if fill_px > 0 else Decimal("0")
        filled = max(Decimal("0"), filled)

        notional = fill_px * filled
        fee_rate = Decimal(str(self._settings.fees_bps)) / Decimal("10000")
        fee = notional * fee_rate

        return Trade(
            trade_id=f"dryrun:{uuid.uuid4()}",
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            price=fill_px,
            size=filled,
            fee=fee,
            timestamp=datetime.now(timezone.utc),
            raw={
                "dry_run": True,
                "paper_trading": bool(self._settings.paper_trading_enabled),
                "requested_size": str(requested),
                "filled_size": str(filled),
                "fill_fraction": float(frac),
                "mid_price": str(mid),
                "best_bid": str(best_bid) if best_bid is not None else None,
                "best_ask": str(best_ask) if best_ask is not None else None,
                "paper_slippage_bps": int(self._settings.paper_slippage_bps),
                "fees_bps": int(self._settings.fees_bps),
                "edge_type": signal.edge_type,
                "rationale": signal.rationale,
            },
        )
