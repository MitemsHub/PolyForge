from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from loguru import logger

from src.core.config import Settings
from src.security.secrets_manager import redact_dict


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class AuditLogger:
    path: Path
    _lock: Lock = Lock()

    def ensure_ready(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def get_last_hash(self) -> str:
        self.ensure_ready()
        try:
            with self.path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return "0" * 64
                step = min(size, 8192)
                f.seek(-step, 2)
                chunk = f.read().decode("utf-8", errors="ignore")
        except Exception:
            return "0" * 64

        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        for ln in reversed(lines):
            try:
                obj = json.loads(ln)
                h = str(obj.get("hash"))
                if len(h) == 64:
                    return h
            except Exception:
                continue
        return "0" * 64

    def append(self, event_type: str, payload: dict[str, Any]) -> str:
        safe_payload = redact_dict(payload)
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            prev_hash = self.get_last_hash()
            record = {"ts": ts, "type": event_type, "payload": safe_payload, "prev_hash": prev_hash}
            record_bytes = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
            record_hash = _sha256_hex(record_bytes)
            record["hash"] = record_hash
            line = json.dumps(record, sort_keys=True, default=str)
            self.ensure_ready()
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            return record_hash


_AUDIT_SINGLETON: AuditLogger | None = None


def get_audit_logger(settings: Settings) -> AuditLogger:
    global _AUDIT_SINGLETON
    if _AUDIT_SINGLETON is None:
        _AUDIT_SINGLETON = AuditLogger(path=Path(settings.audit_log_path))
        _AUDIT_SINGLETON.ensure_ready()
    return _AUDIT_SINGLETON


def audit_event(settings: Settings, event_type: str, payload: dict[str, Any]) -> str | None:
    try:
        audit = get_audit_logger(settings)
        h = audit.append(event_type, payload)
        return h
    except Exception as e:
        logger.warning("Audit append failed: {}", e)
        return None
