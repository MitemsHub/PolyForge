# Strategies

PolyForge is designed as a multi-strategy system. Each strategy produces trade intents, not direct orders. The portfolio allocator and risk engine are responsible for capital budgeting and risk gating before any execution happens.

## Strategy Interface (Conceptual)

All strategies should conform to a common shape:

- **Inputs**
  - Market metadata (events, outcomes, fee model)
  - Live order book snapshots and recent trades
  - Portfolio state (positions, exposure, budgets)
  - Optional auxiliary signals (news, X sentiment, polls, on-chain)
- **Outputs**
  - A ranked list of trade intents:
    - instrument (market/outcome/token)
    - side (buy/sell)
    - size (in shares or notional)
    - limit price policy (absolute/relative)
    - time-in-force policy
    - confidence + expected edge
    - required constraints (max slippage, min liquidity)
    - rationale + provenance (what data drove the signal)

The risk engine may:

- reject intents,
- clip sizes,
- tighten prices,
- enforce cooldowns,
- or halt a strategy entirely.

## 1) AI Probability Arbitrage (Core Edge)

**Goal**: Trade when market-implied probability deviates materially from a calibrated probability estimate with a sufficient margin-of-safety.

### Signal Sources

- Market-implied probability from best bid/ask and midpoint
- Ensemble forecast:
  - statistical calibration (historical base rates, category priors)
  - optional LLM-driven qualitative synthesis (news/polls)
  - optional external predictors (poll aggregation, on-chain signals)

### Core Logic

1. Compute market probability `p_mkt`.
2. Compute model probability `p_model` and uncertainty `σ` (or a credible interval).
3. Define an entry threshold:
   - `edge = p_model - p_mkt`
   - require `edge > edge_min` and `edge > k * σ`
4. Enforce liquidity and spread constraints.
5. Produce intent with conservative sizing, scaling with confidence and liquidity.

### Exits

- Edge mean reversion: edge falls below exit threshold
- Time decay / event proximity rules
- Hard risk limits: drawdown or market-level stop

### Failure Modes & Mitigations

- Model overconfidence → require uncertainty-aware thresholds and backtest calibration
- Regime shifts near event time → tighten limits, shrink sizes, avoid low-liquidity markets

## 2) Filtered Smart-Money Copy Trading

**Goal**: Selectively follow high-signal wallets while protecting against herding, late entries, and adverse selection.

### Wallet Scoring (Examples)

Track wallets and compute:

- win rate and profit factor (by category and recency windows)
- size-adjusted performance (avoid “lucky small accounts”)
- consistency (variance of returns)
- behavior signals:
  - early entry vs. chasing
  - size discipline
  - category specialization

### Copy Logic

- Only copy when:
  - wallet score exceeds threshold
  - market liquidity and spread are acceptable
  - entry is not late (price drift constraint)
  - intent aligns with global risk posture
- Apply overrides:
  - reduce size on correlated exposure
  - block specific market categories
  - enforce maximum wallet concentration

### Failure Modes & Mitigations

- Whale exits before you → enforce “freshness” windows and price drift checks
- Copying into thin books → strict minimum liquidity and max slippage constraints

## 3) Automated Market Making (MM)

**Goal**: Capture spread and rebates (if applicable) while controlling inventory and tail risk.

### Quoting Approach

- Build mid from order book and recent trades
- Determine spread:
  - base spread from volatility and liquidity
  - widen under uncertainty (news shocks, event proximity)
- Inventory control:
  - skew quotes away from inventory extremes
  - reduce-only behavior when near exposure caps

### Risk Controls

- Inventory and concentration limits per market/category
- Cancel/requote cadence rules to avoid over-trading
- Kill switch on:
  - abnormal spreads
  - stale books
  - websocket lag
  - rapid adverse price moves

### Failure Modes & Mitigations

- Toxic flow → widen spreads, reduce size, or temporarily halt quoting
- Getting stuck with inventory → inventory-aware skew + hard exposure caps

## 4) Arbitrage (Safety Net + Opportunistic)

**Goal**: Exploit price inconsistencies with high probability of convergence, while accounting for execution risk and fees.

### Intra-Market Checks

- YES/NO parity and implied bounds
- Cross-outcome consistency where applicable (multi-outcome markets)
- Microstructure anomalies:
  - crossed books
  - stale top-of-book
  - unusual imbalance

### Cross-Market / Cross-Venue (Optional)

- Similar outcomes across related markets/events
- Cross-platform signals when data is available

### Execution Constraints

- Require sufficient depth at target prices
- Prefer atomic-ish execution patterns where possible:
  - place hedging leg first only if it’s low risk
  - otherwise use conservative partial fills and strict slippage caps

## Portfolio Allocation

PolyForge expects explicit budgets (example only):

- 40% AI probability arb
- 30% filtered copy-trading
- 20% market making
- 10% arbitrage

The allocator should support:

- rebalancing schedules (daily/weekly)
- strategy-level drawdown limits
- dynamic budget adjustments based on performance and regime

## Backtesting & Evaluation Guidance

- Always separate:
  - signal generation evaluation
  - execution simulation (slippage, partial fills)
  - portfolio/risk evaluation
- Track:
  - hit rate, edge capture, average slippage
  - max drawdown, tail losses, concentration metrics
  - strategy correlations
