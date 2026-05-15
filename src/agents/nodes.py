from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Coroutine

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from src.agents.prompts import (
    PROBABILITY_SYSTEM_PROMPT,
    RESEARCHER_SYSTEM_PROMPT,
    SUPERVISOR_SYSTEM_PROMPT,
    WHALE_SYSTEM_PROMPT,
)
from src.agents.state import GraphState
from src.agents.tools import AgentToolbox
from src.core.config import Settings
from src.core.models import AgentDecision, TradeSignal
from src.execution.executor import TradeExecutor
from src.risk.risk_engine import RiskEngine


class ResearcherOutput(BaseModel):
    summary: str
    key_facts: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ProbabilityEstimate(BaseModel):
    token_id: str
    p_model: float = Field(ge=0.0, le=1.0)
    p_low: float = Field(ge=0.0, le=1.0)
    p_high: float = Field(ge=0.0, le=1.0)
    edge_vs_market: float
    notes: str | None = None


class ProbabilityOutput(BaseModel):
    probability_estimates: list[ProbabilityEstimate] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class WhaleFinding(BaseModel):
    wallet: str
    signal_strength: float = Field(ge=0.0, le=1.0)
    notes: str | None = None


class WhaleOutput(BaseModel):
    wallet_findings: list[WhaleFinding] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SupervisorOutput(BaseModel):
    next: str
    notes: str | None = None


class RiskApproval(BaseModel):
    signal_id: str
    max_size: float = Field(ge=0.0)
    reason: str


class RiskRejection(BaseModel):
    signal_id: str
    reason: str


class RiskOutput(BaseModel):
    approved: list[RiskApproval] = Field(default_factory=list)
    rejected: list[RiskRejection] = Field(default_factory=list)

class ExecutorOutput(BaseModel):
    ok: bool
    dry_run: bool
    message: str
    placed: int = 0
    skipped: int = 0
    errors: int = 0
    execution_report: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class AgentRuntime:
    settings: Settings
    toolbox: AgentToolbox
    risk_engine: RiskEngine
    llm: Any


def _is_mock_llm(llm: Any) -> bool:
    return bool(getattr(llm, "_polyforge_mock", False))


@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=4))
async def _ainvoke_json(llm: Any, *, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
    ]
    resp = await llm.ainvoke(messages)
    content = getattr(resp, "content", resp)
    if not isinstance(content, str):
        raise ValueError("LLM response content is not a string")
    return json.loads(content)


def build_nodes(rt: AgentRuntime) -> dict[str, Callable[[GraphState], Coroutine[Any, Any, dict[str, Any]]]]:
    async def researcher_node(state: GraphState) -> dict[str, Any]:
        logger.info("researcher_node input", keys=list(state.keys()))

        signals = state.get("signals", [])
        payload = {
            "signals": [s.model_dump(mode="json") for s in signals[:25]],
            "market_context": state.get("market_context", {}),
        }

        if _is_mock_llm(rt.llm):
            out = {
                "summary": "Mock research summary based on available signals.",
                "key_facts": [],
                "risk_flags": ["No external sources configured"],
                "queries": [],
                "confidence": 0.2,
            }
        else:
            out = await _ainvoke_json(rt.llm, system_prompt=RESEARCHER_SYSTEM_PROMPT, user_payload=payload)

        parsed = ResearcherOutput.model_validate(out)
        out = parsed.model_dump(mode="json")
        logger.info("researcher_node output", output=out)
        return {
            "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
            "research_data": {**state.get("research_data", {}), "researcher": out},
            "confidence_scores": {**state.get("confidence_scores", {}), "researcher": parsed.confidence},
            "supervisor": {**state.get("supervisor", {}), "stage": "researcher"},
        }

    async def probability_agent_node(state: GraphState) -> dict[str, Any]:
        logger.info("probability_agent_node input", keys=list(state.keys()))

        signals = state.get("signals", [])
        payload = {
            "signals": [s.model_dump(mode="json") for s in signals[:25]],
            "research": state.get("research_data", {}).get("researcher", {}),
            "market_context": state.get("market_context", {}),
        }

        if _is_mock_llm(rt.llm):
            estimates = []
            for s in signals[:25]:
                p_mkt = float(s.suggested_price) if s.suggested_price is not None else 0.5
                edge = float(s.expected_edge) if s.expected_edge is not None else 0.0
                p_model = min(0.99, max(0.01, p_mkt + (edge if s.side == "buy" else -edge)))
                estimates.append(
                    {
                        "token_id": s.token_id,
                        "p_model": p_model,
                        "p_low": max(0.01, p_model - 0.08),
                        "p_high": min(0.99, p_model + 0.08),
                        "edge_vs_market": p_model - p_mkt,
                        "notes": "mock_estimate",
                    }
                )
            out = {"probability_estimates": estimates, "confidence": 0.25}
        else:
            out = await _ainvoke_json(rt.llm, system_prompt=PROBABILITY_SYSTEM_PROMPT, user_payload=payload)

        parsed = ProbabilityOutput.model_validate(out)
        out = parsed.model_dump(mode="json")

        token_to_edge: dict[str, float] = {}
        token_to_row: dict[str, dict[str, Any]] = {}
        for row in out.get("probability_estimates", []) or []:
            token_id = str(row["token_id"])
            token_to_edge[token_id] = float(row.get("edge_vs_market", 0.0))
            token_to_row[token_id] = dict(row)

        updated_signals: list[TradeSignal] = []
        for s in signals:
            edge = token_to_edge.get(s.token_id)
            if edge is None:
                updated_signals.append(s)
                continue
            updated_signals.append(
                s.model_copy(
                    update={
                        "expected_edge": edge,
                        "metadata": {**s.metadata, "edge_vs_market": edge, "probability": token_to_row.get(s.token_id, {})},
                    }
                )
            )

        logger.info("probability_agent_node output", output=out)
        return {
            "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
            "signals": updated_signals,
            "research_data": {**state.get("research_data", {}), "probability": out},
            "confidence_scores": {**state.get("confidence_scores", {}), "probability": parsed.confidence},
            "supervisor": {**state.get("supervisor", {}), "stage": "probability"},
        }

    async def whale_analyzer_node(state: GraphState) -> dict[str, Any]:
        logger.info("whale_analyzer_node input", keys=list(state.keys()))

        whale_signals = [s for s in state.get("signals", []) if s.edge_type == "whale_activity"][:25]
        payload = {
            "signals": [s.model_dump(mode="json") for s in whale_signals],
            "market_context": state.get("market_context", {}),
        }

        if _is_mock_llm(rt.llm):
            out = {"wallet_findings": [], "confidence": 0.1}
        else:
            out = await _ainvoke_json(rt.llm, system_prompt=WHALE_SYSTEM_PROMPT, user_payload=payload)

        parsed = WhaleOutput.model_validate(out)
        out = parsed.model_dump(mode="json")
        logger.info("whale_analyzer_node output", output=out)
        return {
            "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
            "research_data": {**state.get("research_data", {}), "whales": out},
            "confidence_scores": {**state.get("confidence_scores", {}), "whale": parsed.confidence},
            "supervisor": {**state.get("supervisor", {}), "stage": "whale"},
        }

    async def risk_node(state: GraphState) -> dict[str, Any]:
        logger.info("risk_node input", keys=list(state.keys()))

        approved: list[TradeSignal] = []
        rejected: list[dict[str, Any]] = []

        for idx, sig in enumerate(state.get("signals", [])[:50]):
            ok, reason = rt.risk_engine.check_trade_allowed(sig)
            if not ok:
                rejected.append({"signal_id": f"{sig.strategy_id}:{idx}", "reason": reason})
                continue

            size = rt.risk_engine.calculate_position_size(sig, rt.toolbox.portfolio.get_state())
            if size <= 0:
                rejected.append({"signal_id": f"{sig.strategy_id}:{idx}", "reason": "size_zero_after_caps"})
                continue

            approved.append(sig.model_copy(update={"metadata": {**sig.metadata, "max_size": float(size)}}))

        cycle_id = str(uuid.uuid4())
        decision = AgentDecision(
            cycle_id=cycle_id,
            approved=len(approved) > 0,
            signals=approved,
            planned_orders=[],
            risk=rt.risk_engine.update_risk_metrics(),
            notes=f"approved={len(approved)} rejected={len(rejected)}",
            created_at=datetime.now(timezone.utc),
        )

        out = {
            "approved": [
                {"signal_id": f"scanner:{i}", "max_size": float(s.metadata.get("max_size", 0.0)), "reason": "ok"}
                for i, s in enumerate(approved)
            ],
            "rejected": rejected,
        }
        out = RiskOutput.model_validate(out).model_dump(mode="json")
        logger.info("risk_node output", approved=len(approved), rejected=len(rejected))

        return {
            "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
            "decisions": [*state.get("decisions", []), decision],
            "research_data": {**state.get("research_data", {}), "risk": out},
            "supervisor": {**state.get("supervisor", {}), "stage": "risk"},
        }

    async def supervisor_node(state: GraphState) -> dict[str, Any]:
        logger.info("supervisor_node input", keys=list(state.keys()))

        stage = (state.get("supervisor", {}) or {}).get("stage")
        if stage is None:
            next_step = "researcher"
        elif stage == "researcher":
            next_step = "probability"
        elif stage == "probability":
            next_step = "whale"
        elif stage == "whale":
            next_step = "risk"
        elif stage == "risk":
            last = state.get("decisions", [])[-1] if state.get("decisions") else None
            execution_enabled = bool(state.get("execution_enabled", False))
            if execution_enabled and last is not None and getattr(last, "approved", False):
                next_step = "executor"
            else:
                next_step = "end"
        else:
            next_step = "end"

        out = {"next": next_step, "notes": f"stage={stage}"}
        if not _is_mock_llm(rt.llm) and stage is not None:
            payload = {"stage": stage, "state_keys": list(state.keys())}
            try:
                out = await _ainvoke_json(rt.llm, system_prompt=SUPERVISOR_SYSTEM_PROMPT, user_payload=payload)
            except Exception:
                pass
        parsed = SupervisorOutput.model_validate(out)
        out = parsed.model_dump(mode="json")
        logger.info("supervisor_node output", output=out)
        return {"supervisor": {**state.get("supervisor", {}), **out}}

    async def executor_node(state: GraphState) -> dict[str, Any]:
        logger.info("executor_node input", keys=list(state.keys()))
        last = state.get("decisions", [])[-1] if state.get("decisions") else None
        if last is None:
            out = ExecutorOutput(ok=True, dry_run=True, message="No decision to execute").model_dump(mode="json")
            return {
                "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
                "execution_report": out,
                "supervisor": {**state.get("supervisor", {}), "stage": "executor"},
            }

        exec_enabled = bool(state.get("execution_enabled", False))
        if not exec_enabled:
            out = ExecutorOutput(ok=True, dry_run=True, message="Execution disabled").model_dump(mode="json")
            return {
                "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
                "execution_report": out,
                "supervisor": {**state.get("supervisor", {}), "stage": "executor"},
            }

        clob = rt.toolbox.clob
        if clob is None:
            out = ExecutorOutput(ok=False, dry_run=True, message="CLOB client not configured").model_dump(mode="json")
            return {
                "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
                "execution_report": out,
                "supervisor": {**state.get("supervisor", {}), "stage": "executor"},
            }

        executor = TradeExecutor(rt.settings, gamma=rt.toolbox.gamma, clob=clob)
        result = executor.execute_decision(last, rt.toolbox.portfolio, rt.risk_engine)

        out = ExecutorOutput(
            ok=True,
            dry_run=result.dry_run,
            message="Execution completed",
            placed=result.placed,
            skipped=result.skipped,
            errors=result.errors,
            execution_report={
                "cycle_id": result.cycle_id,
                "dry_run": result.dry_run,
                "placed": result.placed,
                "skipped": result.skipped,
                "errors": result.errors,
                "orders": result.orders,
                "trades": [t.model_dump(mode="json") for t in result.trades],
            },
        ).model_dump(mode="json")

        return {
            "messages": [AIMessage(content=json.dumps(out, ensure_ascii=False))],
            "execution_report": out,
            "portfolio": rt.toolbox.portfolio.get_state(),
            "supervisor": {**state.get("supervisor", {}), "stage": "executor"},
        }

    return {
        "researcher": researcher_node,
        "probability": probability_agent_node,
        "whale": whale_analyzer_node,
        "risk": risk_node,
        "supervisor": supervisor_node,
        "executor": executor_node,
    }
