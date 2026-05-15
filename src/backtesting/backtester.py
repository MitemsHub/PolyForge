from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

import numpy as np
import pandas as pd
from loguru import logger

from src.core.models import Trade, TradeSignal


@dataclass(frozen=True)
class BacktestMetrics:
    win_rate: float
    profit_factor: float
    sharpe: float
    max_drawdown: float
    expectancy: float


class Backtester:
    """
    Simple historical backtester (Phase 2).

    This backtester is intentionally minimal:
    - It supports a basic event loop over timestamps
    - It applies slippage and fees
    - It computes common performance metrics

    Historical Polymarket market data is not guaranteed to be available via Gamma/Data APIs
    in all environments; `load_historical_data` is therefore best-effort and supports a
    synthetic sample mode used by `--backtest-sample`.
    """

    def __init__(self, fee_rate: float = 0.0, slippage_bps: int = 10) -> None:
        self._fee_rate = float(fee_rate)
        self._slippage_bps = int(slippage_bps)
        self._equity_curve: pd.Series | None = None
        self._trades: list[Trade] = []

    def load_historical_data(self, *, market_id: str | None = None, date_range: tuple[datetime, datetime] | None = None) -> pd.DataFrame:
        """
        Load historical prices for a market or date range.

        Phase 2 implementation:
        - Returns a placeholder synthetic series unless real data is injected.
        """
        if market_id is None and date_range is None:
            start = datetime.now(timezone.utc) - timedelta(days=14)
            end = datetime.now(timezone.utc)
        else:
            start, end = date_range if date_range is not None else (datetime.now(timezone.utc) - timedelta(days=14), datetime.now(timezone.utc))

        idx = pd.date_range(start=start, end=end, freq="30min", tz="UTC")
        rng = np.random.default_rng(42)
        steps = rng.normal(loc=0.0, scale=0.01, size=len(idx))
        price = np.clip(0.5 + np.cumsum(steps), 0.01, 0.99)
        df = pd.DataFrame({"timestamp": idx, "price": price}).set_index("timestamp")
        return df

    def simulate_trades(
        self,
        prices: pd.DataFrame,
        signals: list[TradeSignal],
        strategy_rules: Callable[[TradeSignal, pd.Timestamp, float], bool] | None = None,
        *,
        initial_cash: float = 10_000.0,
    ) -> pd.Series:
        """
        Simulate execution of signals over a price series.

        Execution model:
        - one position at a time (net exposure) for simplicity
        - buys increase position, sells decrease position
        - slippage applied against trader
        - fee applied as percentage of notional
        """
        if prices.empty:
            raise ValueError("prices dataframe is empty")

        cash = float(initial_cash)
        position_qty = 0.0
        avg_price = 0.0
        equity: list[float] = []
        timestamps: list[pd.Timestamp] = []

        signals_sorted = sorted(signals, key=lambda s: s.created_at)
        sig_idx = 0

        for ts, row in prices.iterrows():
            px = float(row["price"])

            while sig_idx < len(signals_sorted) and signals_sorted[sig_idx].created_at <= ts.to_pydatetime():
                sig = signals_sorted[sig_idx]
                sig_idx += 1

                if strategy_rules is not None and not strategy_rules(sig, ts, px):
                    continue

                qty = float(sig.metadata.get("qty", 1.0))
                slippage = self._slippage_bps / 10_000.0

                if sig.side == "buy":
                    fill_px = min(0.999, px * (1.0 + slippage))
                    notional = qty * fill_px
                    fee = notional * self._fee_rate
                    if cash < notional + fee:
                        continue
                    cash -= (notional + fee)

                    new_qty = position_qty + qty
                    if new_qty != 0:
                        avg_price = ((avg_price * position_qty) + (fill_px * qty)) / new_qty
                    position_qty = new_qty

                    self._trades.append(
                        Trade(
                            trade_id=None,
                            market_id=sig.market_id,
                            token_id=sig.token_id,
                            side="buy",
                            price=Decimal(str(fill_px)),
                            size=Decimal(str(qty)),
                            fee=Decimal(str(fee)),
                            timestamp=ts.to_pydatetime(),
                        )
                    )

                elif sig.side == "sell":
                    fill_px = max(0.001, px * (1.0 - slippage))
                    qty_to_sell = min(qty, position_qty)
                    if qty_to_sell <= 0:
                        continue
                    notional = qty_to_sell * fill_px
                    fee = notional * self._fee_rate
                    cash += (notional - fee)
                    position_qty -= qty_to_sell

                    self._trades.append(
                        Trade(
                            trade_id=None,
                            market_id=sig.market_id,
                            token_id=sig.token_id,
                            side="sell",
                            price=Decimal(str(fill_px)),
                            size=Decimal(str(qty_to_sell)),
                            fee=Decimal(str(fee)),
                            timestamp=ts.to_pydatetime(),
                        )
                    )

                    if position_qty == 0:
                        avg_price = 0.0

            equity_val = cash + (position_qty * px)
            equity.append(equity_val)
            timestamps.append(ts)

        curve = pd.Series(equity, index=pd.DatetimeIndex(timestamps, tz="UTC"), name="equity")
        self._equity_curve = curve
        return curve

    def compute_metrics(self) -> BacktestMetrics:
        if self._equity_curve is None or self._equity_curve.empty:
            raise RuntimeError("No equity curve to compute metrics from. Run simulate_trades first.")

        curve = self._equity_curve
        returns = curve.pct_change().dropna()
        if returns.empty:
            return BacktestMetrics(win_rate=0.0, profit_factor=0.0, sharpe=0.0, max_drawdown=0.0, expectancy=0.0)

        win_rate = float((returns > 0).mean())
        gains = returns[returns > 0].sum()
        losses = -returns[returns < 0].sum()
        profit_factor = float(gains / losses) if losses > 0 else float("inf")

        sharpe = float((returns.mean() / (returns.std(ddof=0) + 1e-12)) * np.sqrt(24 * 365))

        peak = curve.cummax()
        dd = (curve - peak) / peak
        max_dd = float(dd.min())

        expectancy = float(returns.mean())
        return BacktestMetrics(
            win_rate=win_rate,
            profit_factor=profit_factor,
            sharpe=sharpe,
            max_drawdown=max_dd,
            expectancy=expectancy,
        )

    def walk_forward_placeholder(self) -> None:
        logger.info("Walk-forward placeholder: split historical data into train/test folds and iterate.")

    def to_advanced(self, settings: Any, *, gamma: Any = None, data_api: Any = None) -> Any:
        from src.backtesting.advanced_backtester import AdvancedBacktester

        return AdvancedBacktester(settings, gamma=gamma, data_api=data_api)
