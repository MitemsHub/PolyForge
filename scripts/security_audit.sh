#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python - <<'PY'
import json
import hashlib
from pathlib import Path

from src.core.config import get_settings
from src.security.secrets_manager import SecretsManager, fingerprint_settings


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_audit_chain(path: Path) -> tuple[bool, str, int]:
    if not path.exists():
        return True, "0" * 64, 0

    prev_hash = "0" * 64
    line_count = 0
    with path.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            line_count += 1
            obj = json.loads(ln)
            expected_prev = obj.get("prev_hash")
            if expected_prev != prev_hash:
                return False, str(obj.get("hash", "")), line_count
            record = {k: v for k, v in obj.items() if k != "hash"}
            record_bytes = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
            expected_hash = sha256_hex(record_bytes)
            if obj.get("hash") != expected_hash:
                return False, str(obj.get("hash", "")), line_count
            prev_hash = expected_hash
    return True, prev_hash, line_count


settings = get_settings()
SecretsManager(settings).validate_runtime()

fp = fingerprint_settings(settings)
audit_path = Path(settings.audit_log_path)
ok, last_hash, lines = verify_audit_chain(audit_path)

print(json.dumps({
    "ok": ok,
    "env": settings.env,
    "dry_run": settings.dry_run,
    "trading_enabled": settings.trading_enabled,
    "wallet_mode": settings.wallet_mode,
    "settings_fingerprint": fp,
    "audit_log_path": str(audit_path),
    "audit_lines": lines,
    "audit_last_hash": last_hash,
}, sort_keys=True))

if not ok:
    raise SystemExit(2)
PY
