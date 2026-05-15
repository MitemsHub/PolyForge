from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from loguru import logger

from src.core.config import Settings
from src.data.clob_client import PolyClobClient


@dataclass(frozen=True)
class WalletStatus:
    mode: str
    trading_enabled: bool
    dry_run: bool
    signer_available: bool
    funder_address: str | None
    balance_snapshot: dict[str, Any] | None


class WalletManager:
    def __init__(self, settings: Settings, *, clob: PolyClobClient | None = None) -> None:
        self._settings = settings
        self._clob = clob

    def signer_available(self) -> bool:
        if self._settings.wallet_mode == "cold":
            return False
        if self._settings.wallet_mode == "proxy":
            return bool(self._settings.clob_funder_address)
        return self._settings.wallet_private_key is not None

    def get_status(self) -> WalletStatus:
        balance = None
        if self._clob is not None:
            try:
                balance = self._clob.get_balance()
            except Exception:
                balance = None

        return WalletStatus(
            mode=self._settings.wallet_mode,
            trading_enabled=self._settings.trading_enabled,
            dry_run=self._settings.dry_run,
            signer_available=self.signer_available(),
            funder_address=self._settings.clob_funder_address,
            balance_snapshot=balance,
        )

    def ensure_min_balance(self) -> tuple[bool, str]:
        if self._settings.min_wallet_balance_usd <= 0:
            return True, "no_min_balance_configured"
        if self._clob is None:
            return False, "clob_not_configured"

        try:
            bal = self._clob.get_balance()
        except Exception:
            return False, "balance_unavailable"

        value = self._extract_usd_like_balance(bal)
        if value is None:
            return False, "balance_parse_failed"
        if value < Decimal(str(self._settings.min_wallet_balance_usd)):
            return False, "balance_below_minimum"
        return True, "ok"

    def rotation_hint(self) -> dict[str, Any]:
        return {
            "mode": self._settings.wallet_mode,
            "key_encryption": self._settings.key_encryption,
            "recommendation": "Use external secret manager rotation; restart PolyForge to pick up rotated env secrets.",
        }

    def _extract_usd_like_balance(self, balance_snapshot: dict[str, Any]) -> Decimal | None:
        candidates = []
        for k in ("available", "balance", "collateral", "usd", "usdc"):
            if k in balance_snapshot:
                candidates.append(balance_snapshot.get(k))
        for v in candidates:
            try:
                if v is None:
                    continue
                return Decimal(str(v))
            except Exception:
                continue
        return None
