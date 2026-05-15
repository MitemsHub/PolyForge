from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger

from src.core.config import Settings
from src.core.models import PortfolioState, Position, Trade


@dataclass(frozen=True)
class ExposureSnapshot:
    total_value: Decimal
    per_market_value: dict[str, Decimal]
    per_category_value: dict[str, Decimal]


class Portfolio:
    """
    Portfolio state manager.

    - In-memory state for realtime decisions
    - Persistence to DuckDB for restart safety and backtesting reuse
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = self._resolve_duckdb_path(settings.db_url)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self._db_path))
        self._init_schema()

        self._state = PortfolioState(cash_balance=Decimal(str(settings.initial_cash_balance)))
        self._token_registry: dict[str, dict[str, str | None]] = {}
        self._daily_start_equity: dict[str, Decimal] = {}

        self.load()

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    @staticmethod
    def _resolve_duckdb_path(db_url: str) -> Path:
        prefix = "duckdb:///"
        if db_url.startswith(prefix):
            return Path(db_url.removeprefix(prefix))
        if db_url.endswith(".duckdb"):
            return Path(db_url)
        return Path("./data/polyforge.duckdb")

    def _init_schema(self) -> None:
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_meta (
                k VARCHAR PRIMARY KEY,
                v VARCHAR
            );
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                token_id VARCHAR PRIMARY KEY,
                size DOUBLE,
                avg_price DOUBLE,
                realized_pnl DOUBLE
            );
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                trade_id VARCHAR,
                timestamp TIMESTAMP,
                market_id VARCHAR,
                token_id VARCHAR,
                side VARCHAR,
                price DOUBLE,
                size DOUBLE,
                fee DOUBLE
            );
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS token_registry (
                token_id VARCHAR PRIMARY KEY,
                market_id VARCHAR,
                category VARCHAR
            );
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_equity (
                day DATE PRIMARY KEY,
                start_equity DOUBLE
            );
            """
        )

    def load(self) -> None:
        cash = self._get_meta_decimal("cash_balance", default=Decimal(str(self._settings.initial_cash_balance)))
        self._state.cash_balance = cash

        rows = self._con.execute("SELECT token_id, size, avg_price, realized_pnl FROM positions").fetchall()
        positions: list[Position] = []
        for token_id, size, avg_price, realized_pnl in rows:
            positions.append(
                Position(
                    token_id=str(token_id),
                    size=Decimal(str(size)),
                    avg_price=Decimal(str(avg_price)) if avg_price is not None else None,
                    realized_pnl=Decimal(str(realized_pnl)) if realized_pnl is not None else None,
                )
            )
        self._state.positions = positions

        reg_rows = self._con.execute("SELECT token_id, market_id, category FROM token_registry").fetchall()
        self._token_registry = {
            str(token_id): {"market_id": (str(market_id) if market_id is not None else None), "category": (str(category) if category is not None else None)}
            for token_id, market_id, category in reg_rows
        }

        equity_rows = self._con.execute("SELECT day, start_equity FROM daily_equity").fetchall()
        self._daily_start_equity = {str(d): Decimal(str(v)) for d, v in equity_rows if d is not None and v is not None}

    def save(self) -> None:
        self._set_meta_decimal("cash_balance", self._state.cash_balance or Decimal("0"))

        self._con.execute("DELETE FROM positions")
        for p in self._state.positions:
            self._con.execute(
                "INSERT INTO positions(token_id, size, avg_price, realized_pnl) VALUES (?, ?, ?, ?)",
                [
                    p.token_id,
                    float(p.size),
                    float(p.avg_price) if p.avg_price is not None else None,
                    float(p.realized_pnl) if p.realized_pnl is not None else None,
                ],
            )

        self._con.execute("DELETE FROM token_registry")
        for token_id, meta in self._token_registry.items():
            self._con.execute(
                "INSERT INTO token_registry(token_id, market_id, category) VALUES (?, ?, ?)",
                [token_id, meta.get("market_id"), meta.get("category")],
            )

        self._con.execute("DELETE FROM daily_equity")
        for day_str, start_equity in self._daily_start_equity.items():
            self._con.execute(
                "INSERT INTO daily_equity(day, start_equity) VALUES (?, ?)",
                [date.fromisoformat(day_str), float(start_equity)],
            )

    def get_state(self) -> PortfolioState:
        return self._state

    def set_cash_balance(self, cash: Decimal) -> None:
        self._state.cash_balance = cash
        self._state.updated_at = datetime.now(timezone.utc)
        self.save()

    def update_mark_price(self, token_id: str, price: Decimal) -> None:
        self._state.mark_prices[token_id] = price
        self._state.updated_at = datetime.now(timezone.utc)

    def register_token(self, token_id: str, market_id: str | None, category: str | None) -> None:
        current = self._token_registry.get(token_id)
        if current and current.get("market_id") == market_id and current.get("category") == category:
            return
        self._token_registry[token_id] = {"market_id": market_id, "category": category}
        self.save()

    def apply_trade(self, trade: Trade) -> None:
        """
        Update in-memory portfolio with a fill/trade.

        - Uses average price tracking.
        - Realized PnL is updated when reducing/closing positions.
        """
        pos = next((p for p in self._state.positions if p.token_id == trade.token_id), None)
        if pos is None:
            pos = Position(token_id=trade.token_id, size=Decimal("0"), avg_price=None, realized_pnl=Decimal("0"))
            self._state.positions.append(pos)

        notional = trade.price * trade.size
        cash = self._state.cash_balance or Decimal("0")
        if trade.side == "buy":
            cash = cash - notional - trade.fee
        else:
            cash = cash + notional - trade.fee
        self._state.cash_balance = cash

        signed_qty = trade.size if trade.side == "buy" else -trade.size
        prev_qty = pos.size
        new_qty = prev_qty + signed_qty

        if pos.avg_price is None:
            pos.avg_price = trade.price

        realized = pos.realized_pnl or Decimal("0")
        if prev_qty != 0 and (prev_qty > 0) != (signed_qty > 0):
            close_qty = min(abs(prev_qty), abs(signed_qty))
            pnl_per_unit = (trade.price - (pos.avg_price or trade.price)) * (Decimal("1") if prev_qty > 0 else Decimal("-1"))
            realized = realized + (pnl_per_unit * close_qty) - trade.fee
        else:
            realized = realized - trade.fee

        if new_qty == 0:
            pos.avg_price = None
        else:
            if (prev_qty > 0) == (signed_qty > 0):
                total_qty = abs(prev_qty) + abs(signed_qty)
                if total_qty > 0:
                    weighted = ((pos.avg_price or trade.price) * abs(prev_qty)) + (trade.price * abs(signed_qty))
                    pos.avg_price = weighted / total_qty

        pos.size = new_qty
        pos.realized_pnl = realized

        self._con.execute(
            "INSERT INTO trades(trade_id, timestamp, market_id, token_id, side, price, size, fee) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trade.trade_id,
                trade.timestamp,
                trade.market_id,
                trade.token_id,
                trade.side,
                float(trade.price),
                float(trade.size),
                float(trade.fee),
            ],
        )

        self._state.updated_at = datetime.now(timezone.utc)
        self.save()

    def compute_equity(self) -> Decimal:
        cash = self._state.cash_balance or Decimal("0")
        value = Decimal("0")
        for p in self._state.positions:
            mark = self._state.mark_prices.get(p.token_id)
            if mark is None:
                continue
            value += p.size * mark
        return cash + value

    def get_exposure_snapshot(self) -> ExposureSnapshot:
        per_market: dict[str, Decimal] = {}
        per_category: dict[str, Decimal] = {}
        total = Decimal("0")

        for p in self._state.positions:
            mark = self._state.mark_prices.get(p.token_id)
            if mark is None:
                continue
            pos_value = abs(p.size) * mark
            total += pos_value

            meta = self._token_registry.get(p.token_id, {})
            market_id = meta.get("market_id") or "unknown"
            category = meta.get("category") or "unknown"

            per_market[str(market_id)] = per_market.get(str(market_id), Decimal("0")) + pos_value
            per_category[str(category)] = per_category.get(str(category), Decimal("0")) + pos_value

        return ExposureSnapshot(total_value=total, per_market_value=per_market, per_category_value=per_category)

    def get_daily_start_equity(self, day_value: date) -> Decimal | None:
        return self._daily_start_equity.get(day_value.isoformat())

    def ensure_daily_start_equity(self, day_value: date, equity: Decimal) -> Decimal:
        key = day_value.isoformat()
        if key in self._daily_start_equity:
            return self._daily_start_equity[key]
        self._daily_start_equity[key] = equity
        self.save()
        logger.info("Daily start equity recorded", day=key, equity=float(equity))
        return equity

    def get_peak_equity(self) -> Decimal | None:
        row = self._con.execute("SELECT v FROM portfolio_meta WHERE k = ?", ["peak_equity"]).fetchone()
        if not row or row[0] is None:
            return None
        try:
            return Decimal(str(row[0]))
        except Exception:
            return None

    def update_peak_equity(self, equity: Decimal) -> Decimal:
        current = self.get_peak_equity()
        if current is None or equity > current:
            self._set_meta_decimal("peak_equity", equity)
            return equity
        return current

    def _get_meta_decimal(self, k: str, default: Decimal) -> Decimal:
        row = self._con.execute("SELECT v FROM portfolio_meta WHERE k = ?", [k]).fetchone()
        if not row or row[0] is None:
            return default
        try:
            return Decimal(str(row[0]))
        except Exception:
            return default

    def _set_meta_decimal(self, k: str, v: Decimal) -> None:
        self._con.execute(
            "INSERT INTO portfolio_meta(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            [k, str(v)],
        )

    def get_meta(self, key: str) -> str | None:
        row = self._con.execute("SELECT v FROM portfolio_meta WHERE k = ?", [key]).fetchone()
        if not row:
            return None
        return row[0]

    def set_meta(self, key: str, value: str) -> None:
        self._con.execute(
            "INSERT INTO portfolio_meta(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            [key, value],
        )
