# Agents (LangGraph)

PolyForge uses LangGraph to implement stateful, auditable decision workflows. Agents are modeled as graph nodes that read from and write to a shared typed state. Tools are thin adapters around external calls (Polymarket APIs, DB, news/X search) and internal services (risk checks, execution planning).

## Why LangGraph

- Stateful graphs with explicit transitions
- Fine-grained control over branching, retries, and guardrails
- Persistence-friendly state representation
- Natural fit for “supervisor + specialists” architectures

## Core Agent Roles

### Researcher / Sentiment Agent

Responsibilities:

- Pull external context (news, X, polls) for event-specific signals
- Produce structured summaries and confidence indicators
- Emit “context risk” signals (e.g., breaking news, volatility regime shifts)

Outputs:

- event summaries
- sentiment scores and key drivers
- uncertainty and data freshness indicators

### Probability Agent (Calibration)

Responsibilities:

- Convert context + quantitative features into calibrated probability estimates
- Measure uncertainty and expected edge vs. market prices
- Maintain calibration diagnostics per category and timeframe

Outputs:

- `p_model`, uncertainty, and edge metrics
- entry/exit thresholds and rationale

### Whale Analyzer (Smart Money)

Responsibilities:

- Track top wallets and trades where data is available
- Maintain wallet quality scores and category expertise profiles
- Detect behaviors: early entry, chasing, scaling, exiting patterns

Outputs:

- wallet signals (who, what, when, confidence)
- candidate copy intents with constraints

### Risk & Portfolio Agent

Responsibilities:

- Enforce budgets and risk constraints
- Evaluate portfolio exposures, concentration, correlation groups
- Set global risk mode (NORMAL/CAUTION/DEGRADED/HALT)

Outputs:

- allow/clip/block decisions with rule provenance
- updated budgets and risk mode

### Executor Agent

Responsibilities:

- Convert intents into executable orders
- Choose order types (passive vs. aggressive limits), prices, and sizes
- Manage retries and reconciliation with strict idempotency rules

Outputs:

- execution plans and orders
- acknowledgements, fill updates, error reports

### Supervisor / Human-in-the-Loop

Responsibilities:

- Review high-stakes decisions (large sizes, stressed regimes, novel markets)
- Provide overrides and blocklists/allowlists
- Ensure operational safety and governance

## Shared State (Conceptual)

State should be minimal, structured, and serializable:

- **Market state**
  - market metadata, fees, resolution time
  - order book snapshots, last trade, mid price
  - data freshness and websocket sequence status
- **Signals**
  - probability estimates + uncertainty
  - whale signals + wallet scores
  - sentiment/context signals
- **Portfolio**
  - positions, exposures, budgets per strategy
  - realized/unrealized PnL, drawdown metrics
- **Decisions**
  - candidate intents from strategies
  - risk evaluation results
  - execution plans and final orders
- **Audit**
  - decision timestamps
  - input provenance (which tools were called)
  - rationale payloads for approvals/rejections

## Phase 3 Implementation (Current)

PolyForge Phase 3 implements a minimal Supervisor-routed LangGraph workflow with structured JSON outputs and dry-run safety.

### Graph Structure

Implemented in [graph.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/agents/graph.py) and [nodes.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/agents/nodes.py).

- Nodes:
  - `supervisor`: routes to the next node based on stage (and optionally LLM supervisor prompt)
  - `researcher`: enriches context and emits risk flags + queries (external search is placeholder)
  - `probability`: produces probability estimates and edge-vs-market for signals
  - `whale`: interprets whale-activity signals (best-effort, placeholder if Data API limited)
  - `risk`: calls the concrete RiskEngine (Phase 2) to approve/reject signals and size caps
  - `executor`: dry-run only; logs and stops (no order placement)
- Routing:
  - `START → supervisor → researcher → supervisor → probability → supervisor → whale → supervisor → risk → supervisor → executor → END`

Human-in-the-loop readiness:

- The graph is compiled with `interrupt_before=["executor"]` so a UI/operator can inspect and modify state before any execution step.

### State Schema

State is defined as a TypedDict with reducers in [state.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/agents/state.py):

- `messages`: LangChain message history (reduced via `add_messages`)
- `market_context`: market snapshot and scan metadata
- `signals`: `TradeSignal` list from the scanner
- `portfolio`: current `PortfolioState`
- `decisions`: appended `AgentDecision` artifacts
- `research_data`: per-node structured outputs
- `confidence_scores`: per-node confidence values

### Prompt Strategy

Prompts are centralized in [prompts.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/agents/prompts.py) and enforce:

- strict JSON output formats per agent role
- risk-first behavior (prefer abstain / conservative under uncertainty)
- confidence fields for calibration and down-weighting

### Provider Configuration

- Provider is controlled by `POLYFORGE_LLM_PROVIDER`:
  - `mock` (default): deterministic JSON outputs, no network calls
  - `openai`: uses `POLYFORGE_OPENAI_API_KEY` and `POLYFORGE_LLM_MODEL`

## Graph Patterns

### Pattern A: Supervisor + Specialists (Recommended)

1. Ingest/update market state
2. Run specialist agents (research, probability, whale)
3. Run strategies to generate intents
4. Risk agent gates or modifies intents
5. Executor places orders and reconciles
6. Monitoring emits alerts and stores artifacts

### Pattern B: Event-Driven Reactors

- WebSocket triggers (book updates, fills) drive micro-cycles
- Periodic macro-cycle runs (re-calibration, wallet scoring, reallocation)

## Tooling Contracts

Tools should be:

- deterministic where possible (API calls, DB queries)
- side-effect scoped (execution tools are the only ones that place orders)
- testable via mocks and fixtures

Typical tool categories:

- `get_markets()`, `get_order_book()`, `subscribe_books()`
- `get_positions()`, `get_fills()`, `write_decision_artifact()`
- `evaluate_risk(intents, portfolio, market_state)`
- `plan_orders(intents, book)`, `place_orders(orders)`, `cancel_all()`

## Guardrails

- Always run risk checks immediately before execution planning and again before sending orders if the book changed meaningfully.
- Require freshness checks for all market data used in a decision.
- On tool failures, default to:
  - block new risk
  - cancel open orders if market data is stale
  - notify ops channel

## Persistence & Observability

- Persist the full decision artifact per cycle:
  - inputs, signals, intents, risk decisions, execution outcomes
- Emit structured logs with correlation IDs:
  - cycle_id, market_id, strategy_id, order_id
- Maintain a replay mode:
  - feed historical snapshots to agents and compare outputs for regression testing
