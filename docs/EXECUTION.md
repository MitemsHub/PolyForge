# Execution Engine (Phase 4)

PolyForge Phase 4 connects agent decisions to protected order placement on Polymarket CLOB v2.

## Order Flow

1. **Scanner** produces `TradeSignal`s.
2. **Agent Graph** enriches context and runs the **Risk node** to approve and size signals.
3. **Executor** converts approved signals into limit orders (with tick-size rounding and slippage checks).
4. **Dry-run by default**: execution produces a full preview and optionally simulates trades into the Portfolio.

## Safety Checklist (Must Pass)

Execution will only send real orders when **all** are true:

- `POLYFORGE_TRADING_ENABLED=true`
- `POLYFORGE_DRY_RUN=false`
- `POLYFORGE_EXECUTE_ENABLED=true`
- CLI `--execute` flag is provided
- First live run confirmation:
  - either interactive confirmation (type the required phrase)
  - or set `POLYFORGE_LIVE_CONFIRM_ENV` equal to `POLYFORGE_LIVE_CONFIRM_PHRASE`

If any check fails, PolyForge will not place orders and will run in dry-run mode.

## Translation Rules (Signal → Order)

Implemented in:

- [order_builder.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/execution/order_builder.py)
- [executor.py](file:///c:/Users/USER/Desktop/Projects/PolyForge/src/execution/executor.py)

Key rules:

- **Limit-only**: Phase 4 uses limit orders for controlled execution.
- **Tick-size rounding**:
  - buys round down to the nearest tick
  - sells round up to the nearest tick
- **Order size caps**:
  - `POLYFORGE_MAX_ORDER_SIZE_USD` caps per-order notional
  - `POLYFORGE_MIN_ORDER_SIZE_USD` blocks dust orders
- **Slippage gate**:
  - estimated from order book depth
  - blocks orders when estimated slippage exceeds `POLYFORGE_MAX_ORDER_SLIPPAGE_BPS`
- **Post-only default**:
  - `POLYFORGE_DEFAULT_POST_ONLY=true` biases toward maker behavior where supported

## Emergency Controls

- `cancel_all_orders()` is exposed via the CLOB wrapper and can be used for emergency stops.

## Running Phase 4

Dry-run (safe, recommended):

```bash
python -m src.main --agent-cycle --execute
```

Live mode (dangerous, use only after full testing):

```bash
set POLYFORGE_TRADING_ENABLED=true
set POLYFORGE_DRY_RUN=false
set POLYFORGE_EXECUTE_ENABLED=true
set POLYFORGE_LIVE_CONFIRM_ENV=I_UNDERSTAND
python -m src.main --agent-cycle --execute
```

## Notes

- Exact CLOB v2 options (post-only, negRisk) are passed best-effort depending on the installed `py-clob-client-v2` interface.
- Portfolio updates are simulated on dry-run and will reflect fills only if the exchange reports them in later phases.
