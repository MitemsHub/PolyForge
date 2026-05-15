from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.core.config import Settings
from src.core.logging import configure_logging
from src.security.audit_logger import get_audit_logger
from src.security.secrets_manager import fingerprint_settings


@dataclass(frozen=True)
class StartupBanner:
    dry_run: bool
    trading_enabled: bool
    execute_enabled: bool
    wallet_mode: str
    log_json: bool
    log_dir: str
    audit_log_path: str
    settings_fingerprint: str
    audit_last_hash: str


def emit_startup_banner(settings: Settings) -> StartupBanner:
    configure_logging(settings)
    audit = get_audit_logger(settings)
    banner = StartupBanner(
        dry_run=settings.dry_run,
        trading_enabled=settings.trading_enabled,
        execute_enabled=settings.execute_enabled,
        wallet_mode=settings.wallet_mode,
        log_json=settings.log_json,
        log_dir=str(settings.log_dir),
        audit_log_path=str(settings.audit_log_path),
        settings_fingerprint=fingerprint_settings(settings),
        audit_last_hash=audit.get_last_hash(),
    )
    logger.info(
        "Startup banner",
        dry_run=banner.dry_run,
        trading_enabled=banner.trading_enabled,
        execute_enabled=banner.execute_enabled,
        wallet_mode=banner.wallet_mode,
        log_json=banner.log_json,
        log_dir=banner.log_dir,
        audit_log_path=banner.audit_log_path,
        settings_fingerprint=banner.settings_fingerprint,
        audit_last_hash=banner.audit_last_hash,
    )
    return banner
