from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.core.config import Settings


def resolve_duckdb_path(db_url: str) -> Path:
    prefix = "duckdb:///"
    if db_url.startswith(prefix):
        return Path(db_url.removeprefix(prefix))
    if db_url.endswith(".duckdb"):
        return Path(db_url)
    return Path("./data/polyforge.duckdb")


@dataclass(frozen=True)
class DashboardStore:
    db_path: Path

    def connect(self) -> duckdb.DuckDBPyConnection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            return duckdb.connect(":memory:")
        try:
            return duckdb.connect(str(self.db_path), read_only=True)
        except Exception:
            snap = self.db_path.with_name(f"{self.db_path.stem}_dashboard.duckdb")
            if snap.exists():
                return duckdb.connect(str(snap), read_only=True)
            raise

    def query_df(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        try:
            con = self.connect()
        except Exception:
            return pd.DataFrame()
        try:
            try:
                if params is None:
                    return con.execute(sql).df()
                return con.execute(sql, params).df()
            except Exception:
                return pd.DataFrame()
        finally:
            con.close()

    def get_recent_cycles(self, limit: int = 200) -> pd.DataFrame:
        return self.query_df(
            """
            SELECT *
            FROM cycle_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            [int(limit)],
        )

    def get_cycle_signals(self, cycle_id: str, limit: int = 500) -> pd.DataFrame:
        return self.query_df(
            """
            SELECT *
            FROM cycle_signals
            WHERE cycle_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [cycle_id, int(limit)],
        )

    def get_portfolio_snapshots(self, limit: int = 2000) -> pd.DataFrame:
        return self.query_df(
            """
            SELECT *
            FROM portfolio_snapshots
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            [int(limit)],
        )

    def get_agent_messages(self, cycle_id: str, limit: int = 200) -> pd.DataFrame:
        return self.query_df(
            """
            SELECT *
            FROM agent_messages
            WHERE cycle_id = ?
            ORDER BY idx ASC
            LIMIT ?
            """,
            [cycle_id, int(limit)],
        )

    def get_agent_decision(self, cycle_id: str) -> dict[str, Any]:
        df = self.query_df("SELECT decision_json, execution_report_json FROM agent_decisions WHERE cycle_id = ?", [cycle_id])
        if df.empty:
            return {"decision": None, "execution_report": None}
        row = df.iloc[0].to_dict()
        return {"decision": row.get("decision_json"), "execution_report": row.get("execution_report_json")}


def launch_dashboard(settings: Settings, *, project_root: Path, use_snapshot_db: bool = False) -> subprocess.Popen[str]:
    app_path = project_root / "dashboard" / "app.py"
    if not app_path.exists():
        raise FileNotFoundError(str(app_path))

    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)
    if use_snapshot_db:
        base = resolve_duckdb_path(str(settings.db_url))
        snap = base.with_name(f"{base.stem}_dashboard.duckdb")
        env["POLYFORGE_DB_URL"] = f"duckdb:///{snap.as_posix()}"
    else:
        env["POLYFORGE_DB_URL"] = str(settings.db_url)

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(settings.dashboard_port),
        "--server.headless",
        "true",
    ]
    logger.info("Launching dashboard", command=" ".join(cmd))
    return subprocess.Popen(cmd, env=env, cwd=str(project_root))
