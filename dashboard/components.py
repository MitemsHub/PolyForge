from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def safety_banner(*, dry_run: bool, trading_enabled: bool, execute_enabled: bool) -> None:
    if trading_enabled and (not dry_run):
        st.error("LIVE TRADING ENABLED. Dashboard is read-only. Verify execution safety gates immediately.")
    else:
        st.info(f"Safety: DRY_RUN={dry_run} | TRADING_ENABLED={trading_enabled} | EXECUTE_ENABLED={execute_enabled}")


def risk_gauge(label: str, value: float, *, min_value: float = 0.0, max_value: float = 1.0) -> None:
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            title={"text": label},
            gauge={
                "axis": {"range": [min_value, max_value]},
                "bar": {"color": "#00d18f"},
                "steps": [
                    {"range": [min_value, (min_value + max_value) * 0.5], "color": "#1f2937"},
                    {"range": [(min_value + max_value) * 0.5, max_value], "color": "#111827"},
                ],
            },
        )
    )
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=40, b=10), template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)


def market_table(df: pd.DataFrame, *, height: int = 420) -> None:
    if df.empty:
        st.write("No data.")
        return
    st.dataframe(df, use_container_width=True, height=height)


def signal_card(signal_row: dict[str, Any], *, decision_json: str | None = None, messages_df: pd.DataFrame | None = None) -> None:
    title = f"{signal_row.get('edge_type')} | {signal_row.get('side')} | conf={signal_row.get('confidence')}"
    with st.expander(title, expanded=False):
        st.json(signal_row)
        if decision_json:
            st.subheader("Decision / Execution")
            try:
                st.json(json.loads(decision_json))
            except Exception:
                st.code(decision_json)
        if messages_df is not None and not messages_df.empty:
            st.subheader("Agent Reasoning Chain")
            for _, r in messages_df.iterrows():
                st.markdown(f"**{r.get('role')}**")
                st.code(str(r.get("content", ""))[:6000])


def alert_log(lines: list[str]) -> None:
    if not lines:
        st.write("No logs found.")
        return
    st.code("\n".join(lines[-400:]))
