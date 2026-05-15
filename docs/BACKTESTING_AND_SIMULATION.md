# Backtesting & Simulation (Phase 7)

Phase 7 adds a rigorous backtesting and optimization engine designed to validate strategies before risking live capital.

## What’s Implemented

### Advanced Backtester

Implemented in [advanced_backtester.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/backtesting/advanced_backtester.py).

Capabilities:

- DuckDB caching for historical price series (`backtest_prices` table)
- Multi-token simulation (per-token positions, average price tracking)
- Slippage model:
  - base slippage (bps)
  - volume/impact component using a configurable impact coefficient
- Fee simulation:
  - trading fees (bps)
  - optional flat “gas” estimate per trade
- Monte Carlo drawdown risk via block bootstrap
- Walk-forward window generation + purged K-fold split helper for overfitting prevention

Metrics:

- Sharpe, Sortino, Calmar, CAGR
- Profit Factor, Win Rate, Expectancy
- Max Drawdown, Recovery Factor, Omega Ratio
- Monte Carlo percentiles for max drawdown and terminal returns

Notes:

- In environments without a reliable historical feed, the loader falls back to a synthetic series while still caching results. This keeps the pipeline testable and reproducible.

### Optimizer

Implemented in [optimizer.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/backtesting/optimizer.py).

Supported search modes:

- Grid search (deterministic)
- Random search (scikit-learn sampler, supports scipy distributions)
- Bayesian-style search (Gaussian Process + Expected Improvement)

Results persistence:

- DuckDB table `optimization_runs` stores run metadata and full trials.

### Strategy Evaluator

Implemented in [strategy_evaluator.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/strategies/strategy_evaluator.py).

Outputs:

- Markdown report: `reports/backtest_<strategy>_<timestamp>/report.md`
- HTML report (Plotly charts, dark theme): `reports/backtest_<strategy>_<timestamp>/report.html`

## Configuration (Env)

Backtesting knobs are configured via `POLYFORGE_` env vars:

- `POLYFORGE_BACKTEST_INITIAL_CAPITAL`
- `POLYFORGE_BACKTEST_SLIPPAGE_BPS`
- `POLYFORGE_BACKTEST_IMPACT_COEFF`
- `POLYFORGE_BACKTEST_FEE_BPS`
- `POLYFORGE_BACKTEST_GAS_USD`
- `POLYFORGE_BACKTEST_SEED`
- `POLYFORGE_BACKTEST_WALK_FORWARD_TRAIN_DAYS`
- `POLYFORGE_BACKTEST_WALK_FORWARD_TEST_DAYS`
- `POLYFORGE_BACKTEST_PURGED_KFOLD_SPLITS`
- `POLYFORGE_BACKTEST_PURGE_DAYS`
- `POLYFORGE_BACKTEST_MONTE_CARLO_PATHS`
- `POLYFORGE_BACKTEST_MONTE_CARLO_BLOCK_SIZE`
- `POLYFORGE_REPORTS_DIR`

## CLI Usage

Advanced backtest (writes report to `reports/`):

```bash
python -m src.main --backtest --strategy=ai-prob --period=2025-01-01:2026-05-01 --market-id=synthetic_market --token-ids=synthetic_token
```

Parameter optimization (random search; writes best summary to `reports/` and stores all trials in DuckDB):

```bash
python -m src.main --optimize --strategy=ai-prob --period=2025-01-01:2026-05-01 --optimize-iter=30
```

## Safety

Backtesting is pure simulation:

- No order placement
- No execution flags are consulted
- Reports are written to `reports/` and ignored by git by default
