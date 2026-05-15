from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config import Settings


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("POLYFORGE_ENV", "dev")
    monkeypatch.setenv("POLYFORGE_DRY_RUN", "true")
    monkeypatch.setenv("POLYFORGE_TRADING_ENABLED", "false")
    monkeypatch.setenv("POLYFORGE_EXECUTE_ENABLED", "false")
    monkeypatch.setenv("POLYFORGE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("POLYFORGE_DB_URL", f"duckdb:///{(tmp_path / 'polyforge.duckdb').as_posix()}")
    monkeypatch.setenv("POLYFORGE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("POLYFORGE_AUDIT_LOG_PATH", str(tmp_path / "audit" / "audit.jsonl"))
    monkeypatch.setenv("POLYFORGE_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("POLYFORGE_PRESET", "conservative")
    monkeypatch.setenv("POLYFORGE_APPLY_PRESET", "false")
    return Settings()
