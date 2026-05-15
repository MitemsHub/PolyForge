from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.backtesting.advanced_backtester import AdvancedBacktester
from src.backtesting.optimizer import StrategyOptimizer
from src.core.models import TradeSignal


def test_advanced_backtester_synthetic_prices_and_simulation(settings) -> None:
    bt = AdvancedBacktester(settings)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2)
    prices = bt.load_historical_prices(market_id="m", token_ids=["t"], start=start, end=end, allow_synthetic=True)
    assert not prices.empty
    assert {"token_id", "price"}.issubset(set(prices.columns))

    ts = prices["timestamp"].min()
    sig = TradeSignal(
        strategy_id="bt",
        market_id="m",
        token_id="t",
        side="buy",
        confidence=0.6,
        edge_type="test",
        created_at=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else start,
        metadata={"qty": 2.0},
    )
    res = bt.simulate(prices=prices, signals=[sig], progress=False)
    assert res.equity_curve is not None
    assert len(res.trades) >= 1
    assert res.metrics is not None
    assert res.metrics.max_drawdown is not None
    bt.close()


def test_optimizer_grid_search_runs(settings) -> None:
    opt = StrategyOptimizer(settings)

    def evaluate(params):
        score = float(params["x"]) - float(params["y"])
        return score, {"score": score}

    result = opt.grid_search(
        param_grid={"x": [0.0, 1.0], "y": [0.0, 0.5]},
        evaluate=evaluate,
        objective="score",
    )
    assert result.best is not None
    opt.close()
