# Risk Management

PolyForge is risk-first by design: every strategy is a suggestion engine, and the risk engine is the final authority that can approve, modify, or block actions. Risk is treated as a product feature, not an afterthought.

## Principles

- **Centralized risk gate**: No strategy can bypass pre-trade checks.
- **Budgeted risk**: Capital allocation, exposure, and drawdown are explicitly budgeted per strategy and portfolio.
- **Conservatism under uncertainty**: When signal quality or data integrity degrades, the system reduces risk or halts.
- **Explainability**: Every block/clip decision should be attributable to a specific rule and input state.
- **Fail-safe defaults**: When in doubt, block new risk.

## Risk Domains

### 1) Market Risk

- Directional exposure (net YES/NO exposure, net notional)
- Concentration:
  - per market
  - per event/category
  - per correlated cluster
- Tail risk:
  - binary event discontinuities
  - event-time volatility

### 2) Liquidity & Execution Risk

- Spread and depth constraints
- Maximum allowed slippage (per strategy and per market)
- Partial fill risk and stale book risk
- Order placement discipline (limit-first with clear price policies)

### 3) Model & Signal Risk

- Overconfidence controls:
  - uncertainty-aware thresholds
  - calibration checks
- Drift controls:
  - detect degraded predictive performance
  - reduce strategy budgets dynamically

### 4) Operational Risk

- API/WebSocket health (sequence gaps, lag, error spikes)
- Key management and signer safety
- Rate limits and retries (avoid “retry storms”)
- Incident response: cancel-all, halt trading, reduce-only mode

## Core Controls

### Portfolio-Level Limits (Examples)

- Max portfolio drawdown (daily/weekly/monthly)
- Max per-strategy drawdown
- Max gross exposure (sum of absolute exposures)
- Max net exposure (directional bias cap)
- Max category concentration

### Market-Level Limits (Examples)

- Max exposure per market and per outcome token
- Minimum top-of-book depth required to trade
- Maximum spread allowed for entry
- Event-time rules (tighten limits as resolution approaches)

### Order-Level Checks (Pre-Trade Gate)

For each proposed order (from an intent), enforce:

- **Position sizing**:
  - size ≤ remaining strategy budget
  - size ≤ per-market and per-category caps
  - size adjusted for confidence and liquidity
- **Liquidity**:
  - depth at target price supports size within slippage cap
  - avoid trading into thin books
- **Price sanity**:
  - reject orders far from fair value estimate
  - enforce minimum expected edge after fees and slippage
- **Correlation**:
  - check existing exposures in correlated markets
- **Operational health**:
  - websocket must be synced and recent
  - trading client health must be OK

## Phase 2: Implemented Rules

Phase 2 introduces a concrete, configurable risk engine. All limits are loaded from environment/config (POLYFORGE_*), and all decisions are logged.

### Hard Caps

- **Per-market exposure cap**: `POLYFORGE_MAX_MARKET_EXPOSURE_PCT` (default 0.10)
- **Correlated exposure cap**: `POLYFORGE_MAX_CORRELATED_EXPOSURE_PCT` (default 0.30)
  - Phase 2 uses `TradeSignal.category` as a correlation proxy
- **Per-trade risk budget** (target band 2–5%):
  - `POLYFORGE_RISK_PER_TRADE_PCT` (default 0.03)
  - clipped to [`POLYFORGE_RISK_MIN_PER_TRADE_PCT` (default 0.02), `POLYFORGE_RISK_MAX_PER_TRADE_PCT` (default 0.05)]

### Drawdown Controls & Circuit Breaker

- **Daily drawdown limit**: `POLYFORGE_MAX_DAILY_DRAWDOWN_PCT` (default 0.05)
  - recorded against the day’s starting equity snapshot persisted in DuckDB
- **Total drawdown limit**: `POLYFORGE_MAX_TOTAL_DRAWDOWN_PCT` (default 0.20)
  - reserved for later phases where longer equity history is tracked/replayed
- **Circuit breaker cooldown**: `POLYFORGE_CIRCUIT_BREAKER_COOLDOWN_S` (default 3600s)
  - while active, new risk is blocked until cooldown elapses

### Sizing Logic (Kelly-Inspired + Volatility Adjustment)

Sizing is computed from:

- **Equity** = cash + marked-to-market value of positions
- **Kelly-inspired fraction** (half-Kelly style):
  - uses `TradeSignal.expected_edge` and `p*(1-p)` where `p` is the mark/suggested probability price
- **Volatility adjustment**:
  - divides by `TradeSignal.metadata["volatility"]` when provided (defaults to 1.0)
- **Hard clipping**:
  - clipped by per-trade risk budget and remaining per-market / correlated cap headroom

## Phase 4: Execution-Specific Risk Rules

Phase 4 adds execution-time gates that sit alongside (not instead of) the risk engine:

- **Global execution gating**:
  - no real orders unless `POLYFORGE_TRADING_ENABLED=true`, `POLYFORGE_DRY_RUN=false`, `POLYFORGE_EXECUTE_ENABLED=true`
  - first live run requires explicit confirmation (interactive or env override)
- **Per-order notional caps**:
  - `POLYFORGE_MAX_ORDER_SIZE_USD` caps the notional per order
  - `POLYFORGE_MIN_ORDER_SIZE_USD` blocks dust orders
- **Order book slippage gate**:
  - estimated from top-of-book depth
  - block if estimated slippage exceeds `POLYFORGE_MAX_ORDER_SLIPPAGE_BPS`
- **Limit-only discipline**:
  - Phase 4 uses limit orders to avoid uncontrolled market impact
  - prices are rounded to tick size conservatively (buy down, sell up)

## Sizing Framework (Conceptual)

Sizing should be monotonic in edge and bounded by liquidity and risk budgets:

- Base size from budget * risk fraction
- Multiply by confidence scaling (capped)
- Clip by liquidity depth and slippage constraint
- Apply correlation and concentration haircuts

In practice, PolyForge should support:

- conservative Kelly-style sizing with strong caps
- fixed-fraction sizing for copy-trading/MM
- inventory-aware sizing for market making

## Risk States & Kill Switches

PolyForge should track a global risk state:

- **NORMAL**: strategies run within budgets
- **CAUTION**: reduce sizes, raise thresholds, tighten slippage
- **DEGRADED**: block new risk, allow reduce-only actions
- **HALT**: cancel open orders, no new orders

Triggers (examples):

- drawdown breaches
- repeated execution errors
- websocket lag or sequence gaps
- abnormal market microstructure (crossed books, extreme spreads)

## Monitoring & Alerts

Minimum alerting coverage:

- drawdown and exposure breaches
- strategy performance deterioration
- API/WebSocket failures and lag
- repeated order rejections or reconciliation mismatches
- abnormal slippage or fill quality

## Incident Response Runbook (Minimal)

1. Enter **HALT** if risk checks fail or data integrity is uncertain.
2. Cancel all open orders.
3. Reconcile positions and confirm exposure.
4. Diagnose root cause:
   - data integrity
   - execution errors
   - model drift
5. Re-enable trading gradually:
   - start in **DEGRADED** (reduce-only)
   - then **CAUTION**
   - then **NORMAL** after stability window

## Validation & Testing

- Unit tests for each risk rule and sizing function
- Replay tests on historical order book snapshots
- Chaos testing for websocket dropouts and API errors
- Regression tests for “risk bypass” scenarios
