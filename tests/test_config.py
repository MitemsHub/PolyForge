from __future__ import annotations

import pytest

from src.core.config import Settings


def test_wallet_mode_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYFORGE_WALLET_MODE", "invalid")
    with pytest.raises(ValueError):
        Settings()


def test_cold_wallet_blocks_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYFORGE_DRY_RUN", "false")
    monkeypatch.setenv("POLYFORGE_TRADING_ENABLED", "true")
    monkeypatch.setenv("POLYFORGE_WALLET_MODE", "cold")
    with pytest.raises(ValueError):
        Settings()


def test_hot_wallet_requires_private_key_for_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYFORGE_DRY_RUN", "false")
    monkeypatch.setenv("POLYFORGE_TRADING_ENABLED", "true")
    monkeypatch.setenv("POLYFORGE_WALLET_MODE", "hot")
    monkeypatch.delenv("POLYFORGE_WALLET_PRIVATE_KEY", raising=False)
    with pytest.raises(ValueError):
        Settings()


def test_proxy_wallet_requires_funder_address_for_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYFORGE_DRY_RUN", "false")
    monkeypatch.setenv("POLYFORGE_TRADING_ENABLED", "true")
    monkeypatch.setenv("POLYFORGE_WALLET_MODE", "proxy")
    monkeypatch.delenv("POLYFORGE_CLOB_FUNDER_ADDRESS", raising=False)
    with pytest.raises(ValueError):
        Settings()


def test_preset_application(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYFORGE_PRESET", "aggressive")
    monkeypatch.setenv("POLYFORGE_APPLY_PRESET", "true")
    s = Settings()
    assert s.apply_preset is True
    assert s.preset == "aggressive"
    assert s.max_order_size_usd >= 500
    assert s.max_market_exposure_pct >= 0.10
