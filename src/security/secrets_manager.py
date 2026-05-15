from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic import SecretStr

from src.core.config import Settings, get_settings


SENSITIVE_KEYWORDS = ("key", "secret", "token", "pass", "private", "signature")


def redact_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return "***REDACTED***"
    if isinstance(value, str):
        if len(value) <= 6:
            return "***REDACTED***"
        return value[:2] + "***REDACTED***" + value[-2:]
    return "***REDACTED***"


def redact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if any(x in k.lower() for x in SENSITIVE_KEYWORDS):
            out[k] = redact_value(v)
            continue
        if isinstance(v, dict):
            out[k] = redact_dict(v)
            continue
        if isinstance(v, list):
            out[k] = [redact_dict(i) if isinstance(i, dict) else i for i in v]
            continue
        out[k] = v
    return out


def fingerprint_settings(settings: Settings) -> str:
    payload = settings.model_dump(mode="json")
    redacted = redact_dict(payload)
    raw = json.dumps(redacted, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SecretsManager:
    settings: Settings

    def validate_runtime(self) -> None:
        if self.settings.key_encryption and not self.settings.key_encryption_password:
            raise ValueError("KEY_ENCRYPTION is true but KEY_ENCRYPTION_PASSWORD is missing")

    def safe_settings_snapshot(self) -> dict[str, Any]:
        return redact_dict(self.settings.model_dump(mode="json"))


def load_settings_strict() -> Settings:
    settings = get_settings()
    SecretsManager(settings).validate_runtime()
    logger.info("Settings loaded", settings=SecretsManager(settings).safe_settings_snapshot())
    return settings
