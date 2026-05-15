# PolyForge Architecture

PolyForge is a hybrid intelligent trading system for Polymarket designed to operate multiple strategies concurrently under a strict risk-first framework. The system is modular by design to support:

- Real-time and historical data ingestion
- Strategy research, simulation, and production execution
- Agentic decision workflows with auditability
- Safe, policy-driven execution with kill-switches
- Monitoring, alerting, and post-trade analysis

## Design Goals

- **Risk-first**: Risk checks must be enforced centrally and are not optional.
- **Modularity**: Clear boundaries between data, intelligence, strategy, risk, and execution.
- **Determinism where it matters**: Execution and risk logic should be deterministic and testable, even if research uses probabilistic models.
- **Auditability**: Decisions are explainable, reproducible, and linked to the state used at decision time.
- **Graceful degradation**: If AI or external signals are unavailable, the system reduces exposure or falls back to conservative modes.

## High-Level System View

PolyForge is structured as layered components:

1. **Data & Ingestion**
   - Gamma API for markets/events/metadata discovery
   - CLOB v2 REST + WebSockets for order books, live prices, fills
   - Optional external sources: news, X/Twitter search, polls, on-chain (Polygon)
   - Local persistence (DuckDB/Postgres) for history, backtests, evaluation

2. **Intelligence Layer (Agent Workflows)**
   - LangGraph orchestrates agent nodes
   - Shared state captures: market snapshots, signals, portfolio, decisions, rationales
   - Tools encapsulate external calls and internal services (DB, risk checks, execution)

3. **Strategy Layer**
   - Strategies produce proposed actions (orders/intents) and metadata
   - Strategies are budgeted and evaluated independently
   - A portfolio allocator resolves conflicts and manages capital allocation

4. **Risk Engine**
   - Central authority for pre-trade and runtime constraints
   - Global limits (drawdown, leverage-like exposure, concentration)
   - Market-level constraints (liquidity, slippage, inventory, correlation groups)
   - Operational constraints (API health, latency, degraded mode)

5. **Execution Layer**
   - Order planning (limit vs. passive, size, timing)
   - Slippage-aware sizing and order book impact checks
   - Idempotency keys and reconciliation (orders/fills/positions)
   - Safety controls: cancel-all, stop trading, reduce-only

6. **Monitoring & Ops**
   - Structured logs and metrics
   - Alerts (Telegram/Discord) for risk events and anomalies
   - Dashboard hooks (Streamlit/Gradio optional)
   - Post-trade analytics and incident reports

## Data Flow (Decision Cycle)

1. Ingestion updates:
   - Market metadata, order books, trades, wallet/trader activity
2. Strategy signals:
   - Each strategy emits a ranked list of trade intents with confidence and constraints
3. Portfolio allocator:
   - Allocates capital budgets, resolves overlaps, and produces a candidate action set
4. Risk gate:
   - Rejects or modifies actions (downsize, widen limits, block markets)
5. Execution planning:
   - Converts intents into executable orders with price/size/time policies
6. Execution:
   - Sends orders via CLOB client; tracks acknowledgements, fills, errors
7. Reconciliation:
   - Confirms positions and PnL; updates state store; triggers monitoring/alerts

## Module Boundaries

- `src/core/`
  - Configuration models, shared state models, common utilities
- `src/data/`
  - API clients (Gamma, CLOB), WebSocket handlers, persistence layer
- `src/agents/`
  - LangGraph graphs, nodes, tool adapters, agent policies
- `src/strategies/`
  - Strategy implementations (AI prob arb, copy-trading, MM, arb)
- `src/risk/`
  - Risk rules, limit definitions, portfolio constraints, kill-switch logic
- `src/execution/`
  - Order creation, slippage models, order management, reconciliation
- `src/monitoring/`
  - Logging/metrics, alerting hooks, dashboards, runbooks

## State & Persistence

PolyForge separates state into:

- **Realtime state**: latest market snapshots, websocket sequence numbers, open orders
- **Portfolio state**: positions, exposures, risk budgets, realized/unrealized PnL
- **Decision state**: candidate intents, risk evaluation outputs, execution plans

Recommended persistence approach:

- Use DuckDB for local research/backtesting storage and fast iteration
- Use Postgres for multi-run persistence, dashboards, and multi-process deployments
- Store decision artifacts (inputs, outputs, rationale) for auditability and debugging

## Reliability & Degraded Mode

PolyForge should continue operating safely when components fail:

- If external signals (news/X) are unavailable: reduce confidence, shrink sizing, tighten limits
- If websocket falls behind: pause trading and resync
- If API errors spike: cancel open orders, switch to reduce-only, or halt
- If risk engine cannot compute: block all new risk

## Security Model

- Never commit keys; use environment variables and secret managers in production
- Prefer proxy wallets or Safe-style setups when available
- Support read-only “observer mode” for monitoring and research
- Separate roles:
  - Market data access
  - Trading signer access
  - Ops/monitoring access

## Deployment Reference

- Run on always-on VPS for stability and consistent connectivity
- Keep a strict separation between:
  - research/backtesting environments
  - production trading environment
- Use process supervision (systemd/Docker) and persistent logging

## Testing Strategy

- Unit tests for risk rules and sizing logic
- Integration tests with mocked API clients and deterministic fixtures
- Simulation mode for execution and reconciliation logic
