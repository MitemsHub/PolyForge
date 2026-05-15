# PolyForge

PolyForge is a production-grade, risk-first trading system for Polymarket (CLOB v2) designed to be safe-by-default, auditable, and deployable in Docker.

## Safety Disclaimer (Read First)

Trading involves substantial risk of loss, including the loss of all capital. This repository is provided for educational and research purposes and is not financial advice. Use dry-run and small amounts first. You are responsible for understanding Polymarket rules, fees, execution risks, and operational security.

## Features

- Risk-first execution gates (DRY_RUN, TRADING_ENABLED, EXECUTE_ENABLED, first-live confirmation)
- Wallet modes: hot/proxy/cold with explicit validation and safety constraints
- Hash-chained audit log (append-only JSONL) for config load, decisions, and execution attempts
- Client-side API rate limiting for supported HTTP clients
- LangGraph agent workflow with a built-in mock LLM for offline-safe testing
- Scanner + risk engine + executor pipeline with durable DuckDB persistence
- Advanced backtesting + optimization with report generation to reports/
- Read-only dashboard (Streamlit) for monitoring cycles, signals, and portfolio
- Docker multi-stage build + compose orchestration (scheduler + dashboard together)

## Architecture (High Level)

```mermaid
flowchart LR
  subgraph Data
    Gamma[Gamma API]
    DataAPI[Data API (optional)]
    CLOB[CLOB v2]
  end

  subgraph PolyForge
    Scanner[MarketScanner]
    Agents[LangGraph Agents]
    Risk[RiskEngine]
    Exec[TradeExecutor]
    Port[Portfolio (DuckDB)]
    Audit[Audit Log (hash-chained JSONL)]
    Dash[Dashboard (read-only)]
  end

  Gamma --> Scanner
  DataAPI --> Scanner
  Scanner --> Agents
  Agents --> Risk
  Risk --> Exec
  Exec --> CLOB
  Port <--> Scanner
  Port <--> Agents
  Port <--> Risk
  Port <--> Exec
  Audit <-.-> Scanner
  Audit <-.-> Agents
  Audit <-.-> Risk
  Audit <-.-> Exec
  Port --> Dash
```

## Getting Started (From Zero)

### 1) Clone

```bash
git clone <your-repo-url>
cd PolyForge
```

### 2) Local (Python)

Install:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Create config:

```bash
copy .env.example .env
```

Run a safe dry-run cycle:

```bash
python -m src.main --scan-only
```

Run validation:

```bash
python -m src.main --full-check
python -m src.main --test-system --test-cycles 3
```

### 3) Docker (Production-Like)

Build and run scheduler + dashboard together:

```bash
docker compose up -d --build
```

Optional infra services (Postgres + Redis):

```bash
docker compose --profile infra up -d --build
```

Healthcheck:

```bash
docker compose ps
docker compose exec polyforge python -m src.main --healthcheck
```

## CLI

Common commands:

```bash
python -m src.main --scan-only
python -m src.main --agent-cycle
python -m src.main --agent-cycle --execute
python -m src.main --run-forever
python -m src.main --dashboard
python -m src.main --run-forever --dashboard
python -m src.main --backtest --strategy=ai-prob
python -m src.main --optimize --strategy=ai-prob --optimize-iter=30
python -m src.main --full-check
python -m src.main --test-system --test-cycles 3
```

## Strategy Presets

Presets provide safe starting points and are only applied when explicitly enabled:

- `POLYFORGE_PRESET=conservative|balanced|aggressive`
- `POLYFORGE_APPLY_PRESET=true`

## Live Trading Warning

Live trading is blocked unless all gates are satisfied:

- `POLYFORGE_TRADING_ENABLED=true`
- `POLYFORGE_DRY_RUN=false`
- `POLYFORGE_EXECUTE_ENABLED=true`
- Wallet mode supports signing and required credentials are present

Read [SECURITY.md](docs/SECURITY.md) before enabling live trading.

## Testing

```bash
python -m pytest
```

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [STRATEGIES.md](docs/STRATEGIES.md)
- [RISK_MANAGEMENT.md](docs/RISK_MANAGEMENT.md)
- [AGENTS.md](docs/AGENTS.md)
- [EXECUTION.md](docs/EXECUTION.md)
- [API_INTEGRATIONS.md](docs/API_INTEGRATIONS.md)
- [DEPLOYMENT_AND_OPERATIONS.md](docs/DEPLOYMENT_AND_OPERATIONS.md)
- [SECURITY.md](docs/SECURITY.md)
- [DASHBOARD.md](docs/DASHBOARD.md)
- [BACKTESTING_AND_SIMULATION.md](docs/BACKTESTING_AND_SIMULATION.md)
- [ROADMAP.md](docs/ROADMAP.md)
