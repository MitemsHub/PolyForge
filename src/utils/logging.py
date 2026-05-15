from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger

from src.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """
    Configure Loguru sinks for PolyForge.

    Production defaults:
    - console sink enabled
    - file sink enabled under settings.log_dir
    - `serialize=settings.log_json` to support structured logging pipelines

    Security notes:
    - `diagnose=False` avoids logging local variable values in tracebacks (can leak secrets).
    - Prefer binding context fields (cycle_id, market_id, strategy_id) explicitly.
    """
    logger.remove()

    level = settings.log_level.upper()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    file_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"

    logger.add(
        sys.stdout,
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        colorize=not settings.log_json,
        format=None if settings.log_json else console_format,
        serialize=settings.log_json,
    )

    logger.add(
        str(log_dir / "polyforge.log"),
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        rotation="50 MB",
        retention="14 days",
        compression="zip",
        format=None if settings.log_json else file_format,
        serialize=settings.log_json,
    )


def redact(value: Any) -> Any:
    """
    Best-effort redaction for logs and console output.

    - For SecretStr: returns 'REDACTED'
    - For strings that look like secrets: returns 'REDACTED'
    - Otherwise returns value unchanged
    """
    try:
        from pydantic import SecretStr

        if isinstance(value, SecretStr):
            return "REDACTED"
    except Exception:
        pass

    if isinstance(value, str):
        v = value.strip()
        if v.startswith("0x") and len(v) >= 42:
            return "REDACTED"
        if len(v) > 24 and any(k in v.lower() for k in ("secret", "private", "key", "token")):
            return "REDACTED"
    return value
