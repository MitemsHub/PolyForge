# API Integrations

PolyForge integrates with Polymarket’s public and trading APIs and optionally with external data sources. This document describes the intended integration boundaries and operational considerations.

## Polymarket Gamma API (Discovery + Metadata)

Use Gamma for:

- market and event discovery
- metadata (title, category, end time, outcomes)
- filtering and scanning (new markets, trending markets)
- building a canonical market registry for strategy selection

Typical data stored:

- market identifiers, outcome tokens, fee model
- resolution timestamps and market status
- category taxonomy for correlation/risk buckets

Operational considerations:

- Use caching and ETag/If-Modified-Since where possible
- Persist “market registry” snapshots for reproducibility of backtests

## Polymarket CLOB v2 (Execution + Market Data)

Use CLOB v2 for:

- order books, midpoints, and recent trades
- order placement and cancellation
- fills and reconciliation
- WebSockets for live updates

Recommended approach:

- Maintain a WebSocket subscriber per market set (or a multiplexed connection)
- Track sequence numbers and enforce freshness guarantees
- Degrade safely if the subscriber falls behind or disconnects

### Authentication / Signing

- Execution requires a signer (private key or wallet integration).
- Never store private keys in the repo.
- Production deployments should use a secret manager and strict access controls.

### Idempotency & Reconciliation

PolyForge should:

- attach idempotency keys to order placement calls when supported
- reconcile open orders and positions periodically
- treat API success without local confirmation as “unknown state” and resolve via reconciliation

### Rate Limits & Retries

- Use exponential backoff with jitter for transient failures.
- Avoid retry storms by implementing circuit breakers.
- Prefer WebSocket for realtime updates and REST for reconciliation and occasional snapshots.

## Polymarket “Data API” (Positions/Trades/Leaderboards) (Optional)

Where available, use for:

- wallet tracking and whale analytics
- leaderboard-based discovery of high-signal traders
- trade history to compute wallet score features

Risk notes:

- Smart-money signals can be noisy and regime-dependent.
- Enforce freshness windows to avoid copying stale trades.
- Protect against “bait” and adverse selection by requiring price drift checks.

## External Signals (Optional)

### News / Web Search

Use for:

- event context and breaking updates
- structured extraction of “what changed” and “how likely it affects outcome”

Operational guidance:

- store sources and timestamps
- treat external data as uncertain; require conservative thresholds

### X/Twitter Semantic Search

Use for:

- sentiment and narrative signals
- tracking key accounts relevant to categories

Operational guidance:

- monitor for spam/manipulation
- use robust filters and recency weighting

### Polls / Forecast Aggregators

Use for:

- sanity checks and priors for political markets
- model calibration inputs

Operational guidance:

- version and persist poll snapshots used in forecasts
- apply uncertainty-aware fusion logic (avoid overfitting)

### On-chain (Polygon) Signals

Use for:

- wallet behavior analysis
- transfer patterns and activity around major events

Operational guidance:

- treat as auxiliary, not primary edge
- avoid leaking sensitive wallet relationships in logs

## Storage Integrations

PolyForge is designed to support:

- DuckDB for local iteration, fast research, portable backtests
- Postgres for production persistence and dashboards

Suggested data sets:

- market registry snapshots (Gamma)
- order book snapshots / derived features (CLOB)
- fills, orders, positions (execution history)
- wallet/trader features and scores
- decision artifacts (inputs → intents → risk decision → orders → outcomes)

## Observability Integration Points

PolyForge should emit:

- structured logs (JSON-friendly)
- metrics (latency, fill quality, slippage, error rates)
- alerts on:
  - websocket lag/disconnect
  - reconciliation mismatches
  - drawdown/exposure breaches
  - repeated order rejections

## Testing & Sandbox Strategy

- Use mock clients for unit tests (Gamma, CLOB, external signals)
- Record/replay:
  - Gamma snapshots for deterministic market selection
  - order book snapshots for strategy regression tests
- Implement a simulation mode for execution to validate slippage and risk logic before real trading
