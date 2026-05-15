# Security (Phase 8)

PolyForge is designed to handle real capital only when its security gates are configured correctly. This document describes the security model, threat model, wallet best practices, key management, and an operational checklist.

## Security Model (What PolyForge Enforces)

- Secrets are loaded from environment only (via `POLYFORGE_` settings).
- Sensitive settings are stored as `SecretStr` and are redacted in logs and audit payloads.
- Live execution is gated at multiple layers:
  - `POLYFORGE_TRADING_ENABLED=true`
  - `POLYFORGE_DRY_RUN=false`
  - `POLYFORGE_EXECUTE_ENABLED=true`
  - Additional runtime checks (wallet mode, balance floor, risk gates).
- API request rate limiting is enforced client-side for supported HTTP clients via `POLYFORGE_API_RATE_LIMIT_PER_S`.
- A local append-only, hash-chained audit log records configuration load, decisions, order previews/attempts/responses, and errors.

## Threat Model (What You Must Assume)

Consider the following realistic threats:

- Host compromise: malware/infostealer can read environment variables, process memory, browser sessions, or SSH agent.
- Log exfiltration: logs may be shipped off-host to a third-party pipeline; if logs contain secrets, those secrets are effectively public.
- Credential reuse: CLOB keys or bot tokens reused across environments can widen blast radius.
- Operator error: enabling live trading accidentally or deploying with a misconfigured wallet mode.
- Local tampering: an attacker with filesystem access may attempt to delete/modify local records to hide activity.

PolyForge mitigates but cannot fully eliminate these risks. Your operational security controls determine the real security level.

## Wallet Modes

Configure with `POLYFORGE_WALLET_MODE`:

- `hot`: PolyForge has a local signer (requires `POLYFORGE_WALLET_PRIVATE_KEY`). Highest convenience, highest blast radius.
- `proxy`: PolyForge does not hold a local private key for signing the same way; used for Safe/proxy-style flows where supported by the execution client. Requires `POLYFORGE_CLOB_FUNDER_ADDRESS` when trading is enabled.
- `cold`: No signing. Trading cannot be enabled in this mode (read-only and analysis only).

Recommended:

- Start in `cold` or `proxy` for infrastructure hardening and end-to-end dry-runs.
- Move to `hot` only after you can demonstrate:
  - no secrets in logs,
  - correct audit trail,
  - correct risk gates and sizing,
  - correct operational procedures.

## Key Management Best Practices

- Use a dedicated wallet and dedicated CLOB API credentials per environment (dev/staging/prod).
- Keep wallet balances minimal for hot wallets. Use external funding workflows.
- Rotate credentials:
  - CLOB keys: rotate on a schedule and immediately after any suspected leak.
  - Bot tokens (Telegram/Discord): rotate on a schedule; restrict bot permissions.
- Avoid placing secrets in:
  - git history,
  - Docker images,
  - `.env` files on shared hosts,
  - terminal scrollback or shell history.

## Secrets Handling

- PolyForge loads settings from environment and supports `.env` for development convenience.
- Redaction:
  - Settings fingerprints are computed from a redacted snapshot.
  - Audit payloads are redacted before writing.
  - Never log private keys or raw signatures.

If you use an external secret manager (recommended for production), set:

- `POLYFORGE_KEY_ENCRYPTION=true`
- Provide secret material to the process through the orchestrator (KMS/Secrets Manager/Vault → environment injection).

## Audit Log (Tamper Evident)

PolyForge writes a JSONL audit trail (default `./data/audit/audit.jsonl`) with hash chaining:

- Each record includes `prev_hash`.
- Each record includes `hash = sha256(record_without_hash)`.
- Appending is done with a process-level lock to reduce concurrency issues.

This makes silent modification harder to perform without detection. It does not protect against deletion; mitigate deletion by shipping audit logs off-host or storing them on append-only storage.

Operational recommendation:

- Mirror the audit file to an external system (object storage, SIEM) on a schedule.

## Operational Security Checklist

Before enabling live trading:

- Host hardening:
  - patch OS, disable password SSH, enforce MFA where possible,
  - restrict inbound ports to only what you need (dashboard can be behind VPN),
  - run as a non-root user in containers.
- Secrets hygiene:
  - ensure `.env` is not committed and not world-readable,
  - ensure shell history does not contain exported keys,
  - ensure logs do not contain secrets.
- Safety gates:
  - `POLYFORGE_DRY_RUN=true` for at least several full cycles,
  - verify risk limits and circuit breaker behavior,
  - verify minimum wallet balance floor behavior (`POLYFORGE_MIN_WALLET_BALANCE_USD`).
- Auditability:
  - verify audit chain integrity regularly,
  - capture the last audit hash and store it out-of-band.

After enabling live trading:

- Monitor:
  - error rate, rate limiting warnings, execution previews vs. fills,
  - drawdown/circuit breaker activations,
  - unexpected config drift (settings fingerprint changes).
- Backups:
  - back up DuckDB and/or Postgres regularly and validate restore.

## Quick Commands

- Container healthcheck:
  - `python -m src.main --healthcheck`
- Security/audit verification:
  - `./scripts/security_audit.sh`
