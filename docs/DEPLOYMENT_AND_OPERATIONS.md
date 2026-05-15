# Deployment & Operations (Phase 8)

This document covers how to run PolyForge as a production-grade loop with scheduling, alerting, persistence, safe execution gates, and container-based deployment.

## Modes

PolyForge supports multiple run modes via CLI:

- Single run scanner + risk evaluation:
  - `python -m src.main --scan-only`
- Single agent cycle (dry-run):
  - `python -m src.main --agent-cycle`
- Agent cycle with executor stage enabled (still gated by config and DRY_RUN):
  - `python -m src.main --agent-cycle --execute`
- Scheduler loop:
  - `python -m src.main --run-forever`

## Scheduler

The scheduler runs a repeating cycle using asyncio and durable components:

- Scanner → Agent Graph → Risk → Executor (if enabled)
- Portfolio persists to DuckDB to survive restarts
- LangGraph uses `thread_id` for persistent state continuity

Configuration:

- `POLYFORGE_CYCLE_INTERVAL_MINUTES`
- `POLYFORGE_SCANNER_INTERVAL_MINUTES`
- `POLYFORGE_AGENT_INTERVAL_MINUTES`
- `POLYFORGE_ENABLED_STRATEGIES` (comma-separated), e.g. `scanner,agents`

## Alerting

Alert transports:

- Telegram:
  - `POLYFORGE_TELEGRAM_BOT_TOKEN`
  - `POLYFORGE_TELEGRAM_CHAT_ID`
- Discord (optional):
  - `POLYFORGE_DISCORD_WEBHOOK_URL`

Rate limiting:

- `POLYFORGE_ALERT_RATE_LIMIT_PER_MINUTE`

## Execution Safety Checklist

Real orders are blocked unless all are true:

- `POLYFORGE_TRADING_ENABLED=true`
- `POLYFORGE_DRY_RUN=false`
- `POLYFORGE_EXECUTE_ENABLED=true`
- CLI `--execute` is provided (for agent-cycle mode)
- First live run confirmation is satisfied:
  - interactive confirmation phrase, or
  - `POLYFORGE_LIVE_CONFIRM_ENV` equals `POLYFORGE_LIVE_CONFIRM_PHRASE`

Additional execution controls:

- `POLYFORGE_MAX_ORDER_SIZE_USD`
- `POLYFORGE_MIN_ORDER_SIZE_USD`
- `POLYFORGE_MAX_ORDER_SLIPPAGE_BPS`

## Operational Recommendations

- Run on an always-on machine/VPS with stable network.
- Keep logs in JSON mode in production:
  - `POLYFORGE_LOG_JSON=true`
- Start with:
  - LLM provider `mock` for validation
  - dry-run execution previews
  - minimal enabled strategies (e.g., `scanner,agents`)
- Add monitoring around:
  - drawdown breaches
  - cycle error rate
  - websocket lag (later phase)
  - fill quality vs. preview

## Docker Deployment

PolyForge ships with a container setup intended for production-like operation.

Prerequisites:

- Docker Engine + Docker Compose
- A `.env` file (copy from `.env.example`)

Run scheduler + dashboard together:

```bash
docker compose up -d --build
```

Optional infrastructure (Postgres + Redis, profile-based):

```bash
docker compose --profile infra up -d --build
```

Healthcheck:

```bash
docker compose ps
docker compose logs -n 200 polyforge
```

The container healthcheck runs:

- `python -m src.main --healthcheck`

Pre-launch validation (recommended before enabling live trading):

- `python -m src.main --full-check`
- `python -m src.main --test-system --test-cycles 3`

Volumes:

- `./data` → `/app/data` (DuckDB, audit log, caches)
- `./reports` → `/app/reports` (backtest/optimization reports)
- `./logs` → `/app/logs` (application logs; set via `POLYFORGE_LOG_DIR=/app/logs`)

## VPS Recommendations

- Prefer a dedicated VM or bare-metal host.
- Use a minimal, well-supported OS image and keep it patched.
- Put the dashboard behind a VPN, private network, or reverse proxy with authentication.
- Restrict inbound ports; do not expose internal databases publicly.

## Monitoring Stack

Recommended baseline:

- JSON logs in production (`POLYFORGE_LOG_JSON=true`) shipped to a centralized sink.
- Container healthchecks + restart policy (`unless-stopped`).
- Alerts enabled for:
  - drawdown and circuit breaker triggers,
  - repeated execution failures,
  - unexpected config drift (settings fingerprint changes).

## Backup Strategy

State to back up:

- DuckDB database: `./data/polyforge.duckdb`
- Audit log: `./data/audit/audit.jsonl`
- Reports: `./reports/` (optional but useful for postmortems)

Suggested approach:

- Run `./scripts/backup_db.sh` on a schedule.
- Store backups off-host (object storage) and periodically test restores.

## Security Notes

- Read [SECURITY.md](SECURITY.md) before enabling live trading.
- Keep `POLYFORGE_DRY_RUN=true` until you have validated end-to-end behavior, including audit integrity and balance floors.
