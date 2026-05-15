from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from src.core.config import Settings


@dataclass(frozen=True)
class HealthcheckResult:
    ok: bool
    details: dict[str, Any]


def _resolve_duckdb_path(db_url: str) -> Path:
    prefix = "duckdb:///"
    if db_url.startswith(prefix):
        return Path(db_url.removeprefix(prefix))
    if db_url.endswith(".duckdb"):
        return Path(db_url)
    return Path("./data/polyforge.duckdb")


def run_healthcheck(settings: Settings) -> HealthcheckResult:
    details: dict[str, Any] = {
        "dry_run": settings.dry_run,
        "trading_enabled": settings.trading_enabled,
        "execute_enabled": settings.execute_enabled,
        "wallet_mode": settings.wallet_mode,
        "db_url": settings.db_url,
    }

    ok = True
    db_path = _resolve_duckdb_path(settings.db_url)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(db_path))
        con.execute("SELECT 1")
        con.close()
        details["duckdb_ok"] = True
        details["duckdb_path"] = str(db_path)
    except Exception as e:
        ok = False
        details["duckdb_ok"] = False
        details["duckdb_error"] = str(e)

    try:
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".healthcheck_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)  # type: ignore[arg-type]
        details["log_dir_ok"] = True
        details["log_dir"] = str(log_dir)
    except Exception as e:
        ok = False
        details["log_dir_ok"] = False
        details["log_dir_error"] = str(e)

    return HealthcheckResult(ok=ok, details=details)


def as_json(result: HealthcheckResult) -> str:
    return json.dumps({"ok": result.ok, "details": result.details}, indent=2, default=str)
