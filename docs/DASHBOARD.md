# Dashboard (Phase 6)

PolyForge Phase 6 adds a read-only Streamlit dashboard that acts as the command center for realtime visibility.

## Features

- Navigation:
  - Overview, Markets, Signals, Portfolio, Agents, Logs, Settings
- Safety-first visibility:
  - Safety status banner (DRY_RUN/TRADING_ENABLED/EXECUTE_ENABLED)
  - Red banner when live trading is enabled
- Portfolio:
  - Equity curve (from DuckDB snapshots)
  - Positions table (token_id, size, avg_price, realized PnL, market/category)
- Signals:
  - Recent cycle signals
  - Expandable signal cards with agent decision and reasoning chain
- Agents:
  - Latest decision artifact
  - Latest execution report
  - Full reasoning chain (messages)
- Logs:
  - Tail of most recent log file under `data/logs`
- Performance:
  - Equity curve and drawdown (Plotly, dark theme)

## Running

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the dashboard:

```bash
python -m src.main --dashboard
```

Run the scheduler and dashboard together:

```bash
python -m src.main --run-forever --dashboard
```

## Data Sources

The dashboard is read-only and queries DuckDB:

- `cycle_runs`, `cycle_signals`, `agent_messages`, `agent_decisions`, `portfolio_snapshots`
- `positions`, `token_registry`, `trades` (portfolio persistence tables)

If tables do not exist yet, the dashboard renders empty states until cycles are executed.

## Config

Environment variables:

- `POLYFORGE_DASHBOARD_PORT` (default 8501)
- `POLYFORGE_DASHBOARD_AUTO_REFRESH_SECONDS` (default 45)

## Notes

- Auto-refresh uses Streamlit’s built-in refresh when available, and always supports manual “Refresh now”.
- The dashboard is intentionally read-only in Phase 6 (no execution controls).
