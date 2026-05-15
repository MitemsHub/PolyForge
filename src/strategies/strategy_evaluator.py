from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import plotly.express as px
import plotly.io as pio
from loguru import logger

from src.backtesting.advanced_backtester import AdvancedBacktester, BacktestResult
from src.core.config import Settings
from src.core.models import TradeSignal


@dataclass(frozen=True)
class EvaluationReport:
    strategy: str
    period: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    report_dir: Path
    markdown_path: Path
    html_path: Path


class StrategyEvaluator:
    def __init__(self, settings: Settings, *, backtester: AdvancedBacktester) -> None:
        self._settings = settings
        self._bt = backtester

    def evaluate(
        self,
        *,
        strategy: str,
        market_id: str,
        token_ids: list[str],
        start: datetime,
        end: datetime,
        signal_generator: Callable[[pd.DataFrame, dict[str, Any]], list[TradeSignal]],
        params: dict[str, Any] | None = None,
    ) -> EvaluationReport:
        params = dict(params or {})
        prices = self._bt.load_historical_prices(market_id=market_id, token_ids=token_ids, start=start, end=end, freq="15min", allow_synthetic=True)
        signals = signal_generator(prices, params)
        result = self._bt.simulate(prices=prices, signals=signals, initial_capital=self._settings.backtest_initial_capital, progress=True)

        report_dir = self._build_report_dir(strategy=strategy)
        report_dir.mkdir(parents=True, exist_ok=True)

        period = f"{start.date().isoformat()}:{end.date().isoformat()}"
        metrics = {
            **result.metrics.__dict__,
            "trade_count": len(result.trades),
            "market_id": market_id,
            "token_ids": token_ids,
        }

        md_path = report_dir / "report.md"
        html_path = report_dir / "report.html"

        md_path.write_text(self._render_markdown(strategy=strategy, period=period, params=params, result=result), encoding="utf-8")
        html_path.write_text(self._render_html(strategy=strategy, period=period, params=params, result=result), encoding="utf-8")

        logger.info("Report written", markdown=str(md_path), html=str(html_path))
        return EvaluationReport(
            strategy=strategy,
            period=period,
            params=params,
            metrics=metrics,
            report_dir=report_dir,
            markdown_path=md_path,
            html_path=html_path,
        )

    def _build_report_dir(self, *, strategy: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = Path(self._settings.reports_dir)
        return base / f"backtest_{strategy}_{ts}"

    def _render_markdown(self, *, strategy: str, period: str, params: dict[str, Any], result: BacktestResult) -> str:
        m = result.metrics
        lines = [
            f"# PolyForge Backtest Report",
            "",
            f"- Strategy: {strategy}",
            f"- Period: {period}",
            f"- Trades: {len(result.trades)}",
            "",
            "## Metrics",
            "",
            f"- Sharpe: {m.sharpe:.3f}",
            f"- Sortino: {m.sortino:.3f}",
            f"- Calmar: {m.calmar:.3f}",
            f"- CAGR: {m.cagr:.3%}",
            f"- Max DD: {m.max_drawdown:.3%}",
            f"- Profit Factor: {m.profit_factor:.3f}",
            f"- Win Rate: {m.win_rate:.3%}",
            f"- Expectancy: {m.expectancy:.6f}",
            f"- Recovery Factor: {m.recovery_factor:.3f}",
            f"- Omega Ratio: {m.omega_ratio:.3f}",
            "",
            "## Parameters",
            "",
            "```json",
            json.dumps(params, indent=2, default=str),
            "```",
        ]
        if result.monte_carlo is not None:
            mc = result.monte_carlo
            lines.extend(
                [
                    "",
                    "## Monte Carlo",
                    "",
                    f"- Paths: {mc.paths}",
                    f"- Horizon steps: {mc.horizon_steps}",
                    f"- Max DD p50: {mc.max_drawdown_p50:.3%}",
                    f"- Max DD p95: {mc.max_drawdown_p95:.3%}",
                    f"- Terminal return p50: {mc.terminal_return_p50:.3%}",
                    f"- Terminal return p05: {mc.terminal_return_p05:.3%}",
                ]
            )
        return "\n".join(lines) + "\n"

    def _render_html(self, *, strategy: str, period: str, params: dict[str, Any], result: BacktestResult) -> str:
        curve = result.equity_curve.copy()
        df = pd.DataFrame({"timestamp": curve.index, "equity": curve.values})
        df["drawdown"] = (df["equity"] / df["equity"].cummax()) - 1.0

        fig_equity = px.line(df, x="timestamp", y="equity", title="Equity Curve", template="plotly_dark")
        fig_dd = px.area(df, x="timestamp", y="drawdown", title="Drawdown", template="plotly_dark")

        equity_html = pio.to_html(fig_equity, include_plotlyjs="cdn", full_html=False)
        dd_html = pio.to_html(fig_dd, include_plotlyjs=False, full_html=False)

        m = result.metrics
        metrics_tbl = pd.DataFrame(
            [
                {"metric": "Sharpe", "value": m.sharpe},
                {"metric": "Sortino", "value": m.sortino},
                {"metric": "Calmar", "value": m.calmar},
                {"metric": "CAGR", "value": m.cagr},
                {"metric": "Max Drawdown", "value": m.max_drawdown},
                {"metric": "Profit Factor", "value": m.profit_factor},
                {"metric": "Win Rate", "value": m.win_rate},
                {"metric": "Expectancy", "value": m.expectancy},
                {"metric": "Recovery Factor", "value": m.recovery_factor},
                {"metric": "Omega Ratio", "value": m.omega_ratio},
                {"metric": "Trades", "value": len(result.trades)},
            ]
        ).to_html(index=False)

        mc_html = ""
        if result.monte_carlo is not None:
            mc = result.monte_carlo
            mc_tbl = pd.DataFrame(
                [
                    {"metric": "Paths", "value": mc.paths},
                    {"metric": "Horizon steps", "value": mc.horizon_steps},
                    {"metric": "Max DD p50", "value": mc.max_drawdown_p50},
                    {"metric": "Max DD p95", "value": mc.max_drawdown_p95},
                    {"metric": "Terminal return p50", "value": mc.terminal_return_p50},
                    {"metric": "Terminal return p05", "value": mc.terminal_return_p05},
                ]
            ).to_html(index=False)
            mc_html = f"<h2>Monte Carlo</h2>{mc_tbl}"

        return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>PolyForge Backtest Report</title>
    <style>
      body {{ background: #0b1220; color: #e5e7eb; font-family: ui-sans-serif, system-ui; padding: 16px; }}
      h1, h2 {{ color: #ffffff; }}
      table {{ width: 100%; border-collapse: collapse; }}
      th, td {{ border: 1px solid #1f2937; padding: 8px; }}
      th {{ background: #111827; }}
      pre {{ background: #111827; padding: 12px; overflow-x: auto; }}
      a {{ color: #60a5fa; }}
    </style>
  </head>
  <body>
    <h1>PolyForge Backtest Report</h1>
    <p><b>Strategy</b>: {strategy} &nbsp; | &nbsp; <b>Period</b>: {period}</p>
    <h2>Metrics</h2>
    {metrics_tbl}
    <h2>Charts</h2>
    {equity_html}
    {dd_html}
    {mc_html}
    <h2>Parameters</h2>
    <pre>{json.dumps(params, indent=2, default=str)}</pre>
  </body>
</html>
""".strip()


def default_signal_generator(prices: pd.DataFrame, params: dict[str, Any]) -> list[TradeSignal]:
    df = prices.copy()
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")

    threshold = float(params.get("threshold", 0.03))
    qty = float(params.get("qty", 5.0))

    out: list[TradeSignal] = []
    for token_id, g in df.groupby("token_id"):
        g = g.sort_index()
        series = g["price"].astype(float)
        mean = series.rolling(96, min_periods=48).mean()
        edge = mean - series

        buy_ts = edge[edge > threshold].index
        sell_ts = edge[edge < -threshold].index

        for ts in buy_ts[:200]:
            out.append(
                TradeSignal(
                    strategy_id="advanced_backtest",
                    market_id=str(g["market_id"].iloc[0]) if "market_id" in g.columns else None,
                    token_id=str(token_id),
                    side="buy",
                    confidence=0.6,
                    edge_type="mean_reversion",
                    expected_edge=float(edge.loc[ts]),
                    suggested_price=Decimal(str(series.loc[ts])),
                    created_at=ts.to_pydatetime(),
                    metadata={"qty": qty},
                )
            )
        for ts in sell_ts[:200]:
            out.append(
                TradeSignal(
                    strategy_id="advanced_backtest",
                    market_id=str(g["market_id"].iloc[0]) if "market_id" in g.columns else None,
                    token_id=str(token_id),
                    side="sell",
                    confidence=0.6,
                    edge_type="mean_reversion",
                    expected_edge=float(-edge.loc[ts]),
                    suggested_price=Decimal(str(series.loc[ts])),
                    created_at=ts.to_pydatetime(),
                    metadata={"qty": qty},
                )
            )
    return sorted(out, key=lambda s: s.created_at)

