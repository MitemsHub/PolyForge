from __future__ import annotations

from typing import Any
from typing_extensions import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from src.core.models import AgentDecision, PortfolioState, TradeSignal


class GraphState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]

    market_context: dict[str, Any]
    research_data: dict[str, Any]

    signals: list[TradeSignal]
    confidence_scores: dict[str, float]

    portfolio: PortfolioState
    decisions: list[AgentDecision]

    execution_enabled: bool
    execution_report: dict[str, Any]

    supervisor: dict[str, Any]
    errors: list[str]
