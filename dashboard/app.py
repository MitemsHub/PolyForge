from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.components import alert_log, market_table, risk_gauge, safety_banner, signal_card
from src.core.config import get_settings
from src.monitoring.dashboard_server import DashboardStore, resolve_duckdb_path


def _get_store() -> DashboardStore:
    settings = get_settings()
    db_path = resolve_duckdb_path(settings.db_url)
    return DashboardStore(db_path=db_path)


def _autorefresh(seconds: int) -> None:
    fn = getattr(st, "autorefresh", None)
    if callable(fn):
        fn(interval=seconds * 1000, key="polyforge_autorefresh")


def _try_parse_json(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if not (s.startswith("{") or s.startswith("[")):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _render_jsonish(value: Any) -> None:
    parsed = _try_parse_json(value)
    if parsed is not None:
        st.json(parsed)
        return
    if value is None:
        st.write("None")
        return
    st.code(str(value)[:8000])


@st.cache_data(ttl=5, show_spinner=False)
def _recent_cycles_df(db_path: str) -> pd.DataFrame:
    return DashboardStore(Path(db_path)).get_recent_cycles(limit=300)


@st.cache_data(ttl=5, show_spinner=False)
def _portfolio_snapshots_df(db_path: str) -> pd.DataFrame:
    return DashboardStore(Path(db_path)).get_portfolio_snapshots(limit=5000)


@st.cache_data(ttl=5, show_spinner=False)
def _positions_df(db_path: str) -> pd.DataFrame:
    store = DashboardStore(Path(db_path))
    return store.query_df(
        """
        SELECT p.token_id, p.size, p.avg_price, p.realized_pnl, r.market_id, r.category
        FROM positions p
        LEFT JOIN token_registry r
        ON p.token_id = r.token_id
        """
    )


@st.cache_data(ttl=5, show_spinner=False)
def _cycle_signals_df(db_path: str, cycle_id: str) -> pd.DataFrame:
    return DashboardStore(Path(db_path)).get_cycle_signals(cycle_id, limit=500)


@st.cache_data(ttl=5, show_spinner=False)
def _agent_messages_df(db_path: str, cycle_id: str) -> pd.DataFrame:
    return DashboardStore(Path(db_path)).get_agent_messages(cycle_id, limit=200)


@st.cache_data(ttl=5, show_spinner=False)
def _agent_decision_raw(db_path: str, cycle_id: str) -> dict[str, Any]:
    return DashboardStore(Path(db_path)).get_agent_decision(cycle_id)


def _latest_cycle_id(cycles: pd.DataFrame) -> str | None:
    if cycles.empty:
        return None
    v = cycles.iloc[0].get("cycle_id")
    if v is None:
        return None
    return str(v)


def _compute_drawdown(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    peak = series.cummax()
    dd = (series - peak) / peak.replace(0, 1)
    return dd


def _read_recent_log_lines(log_dir: Path, limit: int = 400) -> list[str]:
    if not log_dir.exists():
        return []
    candidates = list(log_dir.glob("*.log")) + list(log_dir.glob("*.jsonl")) + list(log_dir.glob("*.txt"))
    if not candidates:
        return []
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        lines = latest.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-limit:]
    except Exception:
        return []


def _page_overview(settings: Any, db_path: Path) -> None:
    st.header("Overview")
    safety_banner(
        dry_run=settings.dry_run,
        trading_enabled=settings.trading_enabled,
        execute_enabled=settings.execute_enabled,
        paper_trading_enabled=bool(getattr(settings, "paper_trading_enabled", False)),
    )

    cycles = _recent_cycles_df(str(db_path))
    snaps = _portfolio_snapshots_df(str(db_path))
    positions = _positions_df(str(db_path))
    cid = _latest_cycle_id(cycles)
    decision_raw = _agent_decision_raw(str(db_path), cid) if cid else {"decision": None, "execution_report": None}
    decision = _try_parse_json(decision_raw.get("decision")) or {}
    planned_orders = decision.get("planned_orders") if isinstance(decision, dict) else None
    planned_order_count = len(planned_orders) if isinstance(planned_orders, list) else 0

    c1, c2, c3, c4 = st.columns(4)
    last_equity = float(snaps.iloc[-1]["equity"]) if not snaps.empty else 0.0
    last_cash = float(snaps.iloc[-1]["cash"]) if not snaps.empty else 0.0
    last_exposure = float(snaps.iloc[-1]["gross_exposure"]) if not snaps.empty else 0.0
    cycle_count = int(cycles.shape[0]) if not cycles.empty else 0
    c1.metric("Equity", f"${last_equity:,.2f}")
    c2.metric("Cash", f"${last_cash:,.2f}")
    c3.metric("Gross Exposure", f"${last_exposure:,.2f}")
    c4.metric("Cycles (recent)", f"{cycle_count}", help="These are cycles written to DuckDB. Equity/cash remain flat in dry-run unless paper trading is enabled.")

    c5, c6, c7, c8 = st.columns(4)
    last_cycle_ts = str(cycles.iloc[0].get("started_at")) if not cycles.empty else "n/a"
    last_cycle_mode = str(cycles.iloc[0].get("mode")) if not cycles.empty else "n/a"
    last_cycle_exec = str(cycles.iloc[0].get("execute")) if not cycles.empty else "n/a"
    c5.metric("Latest cycle_id", str(cid) if cid else "n/a")
    c6.metric("Latest started_at", last_cycle_ts)
    c7.metric("Mode", last_cycle_mode)
    c8.metric("Planned orders", f"{planned_order_count}")

    if not snaps.empty:
        snaps2 = snaps.copy()
        snaps2["timestamp"] = pd.to_datetime(snaps2["timestamp"])
        snaps2["drawdown"] = _compute_drawdown(snaps2["equity"].astype(float))

        fig_equity = px.line(snaps2, x="timestamp", y="equity", title="Equity Curve", template="plotly_dark")
        fig_dd = px.area(snaps2, x="timestamp", y="drawdown", title="Drawdown", template="plotly_dark")
        st.plotly_chart(fig_equity, use_container_width=True)
        st.plotly_chart(fig_dd, use_container_width=True)

    st.subheader("Active Positions")
    market_table(positions, height=320)


def _page_markets(db_path: Path) -> None:
    st.header("Markets")
    cycles = _recent_cycles_df(str(db_path))
    cid = _latest_cycle_id(cycles)
    if cid is None:
        st.write("No cycle telemetry found yet. Run a cycle to populate DuckDB.")
        return
    df = _cycle_signals_df(str(db_path), cid)
    st.caption(f"Latest cycle_id: {cid}")
    market_table(df, height=560)


def _page_signals(settings: Any, db_path: Path) -> None:
    st.header("Signals")
    cycles = _recent_cycles_df(str(db_path))
    if cycles.empty:
        st.write("No cycle telemetry found yet.")
        return

    cycle_ids = [str(x) for x in cycles["cycle_id"].head(50).tolist()]
    selected = st.selectbox("Cycle", cycle_ids, index=0)

    sig_df = _cycle_signals_df(str(db_path), selected)
    msgs_df = _agent_messages_df(str(db_path), selected)
    decision_raw = _agent_decision_raw(str(db_path), selected)
    decision_json = decision_raw.get("decision")

    if sig_df.empty:
        st.write("No signals for this cycle.")
        return

    for _, row in sig_df.head(30).iterrows():
        signal_card(row.to_dict(), decision_json=decision_json, messages_df=msgs_df)

    st.subheader("Signals Table")
    market_table(sig_df, height=480)


def _page_portfolio(db_path: Path) -> None:
    st.header("Portfolio")
    snaps = _portfolio_snapshots_df(str(db_path))
    positions = _positions_df(str(db_path))

    if not snaps.empty:
        snaps2 = snaps.copy()
        snaps2["timestamp"] = pd.to_datetime(snaps2["timestamp"])
        fig = px.line(snaps2, x="timestamp", y="equity", title="Equity", template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Positions")
    market_table(positions, height=520)


def _page_agents(settings: Any, db_path: Path) -> None:
    st.header("Agents")
    cycles = _recent_cycles_df(str(db_path))
    cid = _latest_cycle_id(cycles)
    if cid is None:
        st.write("No agent telemetry found yet.")
        return

    decision_raw = _agent_decision_raw(str(db_path), cid)
    msgs_df = _agent_messages_df(str(db_path), cid)
    sig_df = _cycle_signals_df(str(db_path), cid)
    decision = _try_parse_json(decision_raw.get("decision")) or {}
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
    planned_orders = decision.get("planned_orders") if isinstance(decision, dict) else None
    planned_order_count = len(planned_orders) if isinstance(planned_orders, list) else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cycle", str(cid))
    c2.metric("Signals", f"{int(sig_df.shape[0])}")
    c3.metric("Approved", "yes" if approved else "no")
    c4.metric("Planned orders", f"{planned_order_count}")

    c5, c6 = st.columns(2)
    with c5:
        risk_gauge("High-confidence threshold", float(settings.alert_on_high_confidence_threshold))
    with c6:
        risk_gauge("Dry-run", 1.0 if settings.dry_run else 0.0)

    st.subheader("Latest Decision")
    _render_jsonish(decision_raw.get("decision"))

    st.subheader("Latest Execution Report")
    _render_jsonish(decision_raw.get("execution_report"))

    st.subheader("Reasoning Chain")
    if msgs_df.empty:
        st.write("No messages recorded.")
    else:
        for _, r in msgs_df.iterrows():
            st.markdown(f"**{r.get('role')}**")
            _render_jsonish(r.get("content", ""))


def _page_logs(settings: Any) -> None:
    st.header("Logs")
    log_dir = Path(str(settings.log_dir))
    lines = _read_recent_log_lines(log_dir)
    alert_log(lines)


def _page_settings(settings: Any, db_path: Path) -> None:
    st.header("Settings")
    st.subheader("Runtime")
    st.json(
        {
            "env": settings.env,
            "dry_run": settings.dry_run,
            "trading_enabled": settings.trading_enabled,
            "execute_enabled": settings.execute_enabled,
            "paper_trading_enabled": bool(getattr(settings, "paper_trading_enabled", False)),
            "llm_provider": settings.llm_provider,
            "enabled_strategies": settings.enabled_strategies,
            "dashboard_port": settings.dashboard_port,
            "auto_refresh_seconds": settings.dashboard_auto_refresh_seconds,
            "db_path": str(db_path),
        }
    )


def main() -> None:
    settings = get_settings()
    store = _get_store()
    db_path = store.db_path

    st.set_page_config(page_title="PolyForge Dashboard", layout="wide", initial_sidebar_state="expanded")
    st.markdown(
        """
        <style>
        [data-testid="stDeployButton"] { display: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _autorefresh(int(settings.dashboard_auto_refresh_seconds))

    st.sidebar.title("PolyForge")
    page = st.sidebar.radio(
        "Navigation",
        ["Overview", "Markets", "Signals", "Portfolio", "Agents", "Logs", "Settings"],
        index=0,
    )

    st.sidebar.caption("Controls")
    if st.sidebar.button("Refresh now"):
        st.rerun()

    if page == "Overview":
        _page_overview(settings, db_path)
    elif page == "Markets":
        _page_markets(db_path)
    elif page == "Signals":
        _page_signals(settings, db_path)
    elif page == "Portfolio":
        _page_portfolio(db_path)
    elif page == "Agents":
        _page_agents(settings, db_path)
    elif page == "Logs":
        _page_logs(settings)
    elif page == "Settings":
        _page_settings(settings, db_path)


if __name__ == "__main__":
    main()
