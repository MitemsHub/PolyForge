from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.monitoring.healthcheck import run_healthcheck
from src.security.secrets_manager import SecretsManager, fingerprint_settings


def _sha256_hex(data: bytes) -> str:
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
            if obj.get("prev_hash") != prev_hash:
                return False, str(obj.get("hash", "")), line_count
            record = {k: v for k, v in obj.items() if k != "hash"}
            record_hash = _sha256_hex(json.dumps(record, sort_keys=True, default=str).encode("utf-8"))
            if obj.get("hash") != record_hash:
                return False, str(obj.get("hash", "")), line_count
            prev_hash = record_hash
    return True, prev_hash, line_count


@dataclass(frozen=True)
class FullCheckResult:
    ok: bool
    details: dict[str, Any]


def run_full_check(settings: Settings) -> FullCheckResult:
    details: dict[str, Any] = {
        "env": settings.env,
        "dry_run": settings.dry_run,
        "trading_enabled": settings.trading_enabled,
        "execute_enabled": settings.execute_enabled,
        "wallet_mode": settings.wallet_mode,
        "preset": settings.preset,
        "apply_preset": settings.apply_preset,
    }

    try:
        SecretsManager(settings).validate_runtime()
        details["secrets_ok"] = True
    except Exception as e:
        details["secrets_ok"] = False
        details["secrets_error"] = str(e)

    try:
        details["settings_fingerprint"] = fingerprint_settings(settings)
    except Exception as e:
        details["settings_fingerprint_error"] = str(e)

    try:
        health = run_healthcheck(settings)
        details["healthcheck_ok"] = bool(health.ok)
        details["healthcheck_details"] = health.details
    except Exception as e:
        details["healthcheck_ok"] = False
        details["healthcheck_error"] = str(e)

    audit_path = Path(settings.audit_log_path)
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        ok_chain, last_hash, lines = verify_audit_chain(audit_path)
        details["audit_ok"] = bool(ok_chain)
        details["audit_log_path"] = str(audit_path)
        details["audit_lines"] = int(lines)
        details["audit_last_hash"] = str(last_hash)
    except Exception as e:
        details["audit_ok"] = False
        details["audit_error"] = str(e)

    ok = bool(details.get("secrets_ok")) and bool(details.get("healthcheck_ok")) and bool(details.get("audit_ok"))
    return FullCheckResult(ok=ok, details=details)

