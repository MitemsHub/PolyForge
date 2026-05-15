from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import duckdb
import tiktoken
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger

from src.agents.nodes import AgentRuntime, build_nodes
from src.agents.state import GraphState
from src.agents.tools import AgentToolbox, build_tools
from src.core.config import Settings
from src.risk.risk_engine import RiskEngine

"""
Mermaid view (high-level):

graph TD
  START --> supervisor
  supervisor -->|researcher| researcher
  supervisor -->|probability| probability
  supervisor -->|whale| whale
  supervisor -->|risk| risk
  supervisor -->|executor| executor
  supervisor -->|end| END
  researcher --> supervisor
  probability --> supervisor
  whale --> supervisor
  risk --> supervisor
  executor --> END
"""


class MockChatModel:
    _polyforge_mock = True

    async def ainvoke(self, messages: list[Any], **_: Any) -> AIMessage:
        last = messages[-1].content if messages else ""
        try:
            payload = json.loads(last) if isinstance(last, str) else {}
        except Exception:
            payload = {}

        sys = messages[0].content if messages else ""
        if isinstance(sys, str) and "Researcher agent" in sys:
            out = {
                "summary": "Mock research: external search disabled; using provided state only.",
                "key_facts": [],
                "risk_flags": ["No external sources configured"],
                "queries": [],
                "confidence": 0.2,
            }
        elif isinstance(sys, str) and "Probability agent" in sys:
            signals = payload.get("signals", []) or []
            est = []
            for s in signals[:25]:
                token_id = s.get("token_id")
                p_mkt = float(s.get("suggested_price") or 0.5)
                est.append(
                    {
                        "token_id": token_id,
                        "p_model": p_mkt,
                        "p_low": max(0.01, p_mkt - 0.1),
                        "p_high": min(0.99, p_mkt + 0.1),
                        "edge_vs_market": 0.0,
                        "notes": "mock_no_edge",
                    }
                )
            out = {"probability_estimates": est, "confidence": 0.2}
        elif isinstance(sys, str) and "Whale Analyzer" in sys:
            out = {"wallet_findings": [], "confidence": 0.1}
        elif isinstance(sys, str) and "Supervisor agent" in sys:
            stage = payload.get("stage")
            out = {"next": "end", "notes": f"mock_supervisor stage={stage}"}
        else:
            out = {"ok": True}
        return AIMessage(content=json.dumps(out, ensure_ascii=False))


def _count_tokens(text: str, model: str) -> int:
    try:
        enc = tiktoken.encoding_for_model(model)
    except Exception:
        enc = tiktoken.get_encoding("o200k_base")
    return len(enc.encode(text))


def _summarize_if_needed(messages: list[Any], *, model: str, max_tokens: int) -> list[Any]:
    if not messages:
        return messages
    text = "\n".join(getattr(m, "content", "") or "" for m in messages if hasattr(m, "content"))
    if _count_tokens(text, model) <= max_tokens:
        return messages
    keep = messages[-10:]
    summary = AIMessage(content="Conversation truncated for token budget. Keeping last messages only.")
    return [summary, *keep]


def _build_llm(settings: Settings) -> Any:
    provider = settings.llm_provider.strip().lower()
    if provider == "mock":
        return MockChatModel()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else None,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


class DuckDBSaver(MemorySaver):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self._db_path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_checkpoints (
                thread_id VARCHAR NOT NULL,
                checkpoint_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                PRIMARY KEY(thread_id, checkpoint_id)
            );
            """
        )

    def put(self, config: dict[str, Any], checkpoint: Any, metadata: Any) -> dict[str, Any]:
        out = super().put(config, checkpoint, metadata)
        try:
            thread_id = str(out["configurable"]["thread_id"])
            checkpoint_id = str(out["configurable"]["checkpoint_id"])
            payload = {"config": out, "checkpoint": checkpoint, "metadata": metadata}
            self._con.execute(
                "INSERT OR REPLACE INTO agent_checkpoints(thread_id, checkpoint_id, payload_json, created_at) VALUES (?, ?, ?, now())",
                [thread_id, checkpoint_id, json.dumps(payload, default=str)],
            )
        except Exception as e:
            logger.warning("Failed to persist checkpoint to DuckDB: {}", e)
        return out

    async def aput(self, config: dict[str, Any], checkpoint: Any, metadata: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata)


@dataclass(frozen=True)
class GraphBundle:
    app: Any
    tools: list[Any]


def build_graph(
    settings: Settings,
    *,
    toolbox: AgentToolbox,
    risk_engine: RiskEngine,
    interrupt_before_executor: bool = True,
) -> GraphBundle:
    llm = _build_llm(settings)
    tools = build_tools(toolbox)

    rt = AgentRuntime(settings=settings, toolbox=toolbox, risk_engine=risk_engine, llm=llm)
    nodes = build_nodes(rt)

    builder: StateGraph = StateGraph(GraphState)
    builder.add_node("supervisor", nodes["supervisor"])
    builder.add_node("researcher", nodes["researcher"])
    builder.add_node("probability", nodes["probability"])
    builder.add_node("whale", nodes["whale"])
    builder.add_node("risk", nodes["risk"])
    builder.add_node("executor", nodes["executor"])

    builder.add_edge(START, "supervisor")

    def _route(state: GraphState) -> str:
        nxt = (state.get("supervisor", {}) or {}).get("next", "researcher")
        if nxt == "executor":
            if not bool(state.get("execution_enabled", False)):
                return END
            if not (settings.execute_enabled and settings.trading_enabled):
                return END
            return "executor"
        if nxt in {"researcher", "probability", "whale", "risk"}:
            return nxt
        return END

    builder.add_conditional_edges("supervisor", _route)

    for n in ("researcher", "probability", "whale", "risk"):
        builder.add_edge(n, "supervisor")
    builder.add_edge("executor", END)

    if settings.agent_checkpointer.strip().lower() == "duckdb":
        db_path = Path("./data/polyforge.duckdb")
        checkpointer: Any = DuckDBSaver(db_path)
    else:
        checkpointer = MemorySaver()

    app = builder.compile(checkpointer=checkpointer, interrupt_before=["executor"] if interrupt_before_executor else [])
    return GraphBundle(app=app, tools=tools)


async def run_cycle(
    bundle: GraphBundle,
    *,
    settings: Settings,
    initial_state: GraphState,
) -> GraphState:
    msgs = initial_state.get("messages", [])
    initial_state["messages"] = _summarize_if_needed(
        msgs, model=settings.llm_model, max_tokens=min(settings.llm_max_tokens, 2000)
    )

    config = {"configurable": {"thread_id": settings.agent_thread_id}}
    out: GraphState = await bundle.app.ainvoke(initial_state, config=config)
    return out
