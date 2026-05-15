from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import tool
from loguru import logger

from src.core.models import PortfolioState
from src.core.portfolio import Portfolio
from src.data.clob_client import PolyClobClient
from src.data.data_api_client import DataAPIClient
from src.data.gamma_client import GammaClient


@dataclass(frozen=True)
class AgentToolbox:
    gamma: GammaClient
    data_api: DataAPIClient
    portfolio: Portfolio
    clob: PolyClobClient | None = None


def build_tools(tb: AgentToolbox) -> list[Any]:
    @tool("get_market_details")
    def get_market_details(market_id: str) -> dict[str, Any]:
        """Fetch a market by id from Gamma."""
        market = tb.gamma.get_market_by_id(market_id)
        return market.model_dump(mode="json")

    @tool("get_order_book")
    def get_order_book(token_id: str) -> dict[str, Any]:
        """Fetch raw order book from CLOB (if configured)."""
        if tb.clob is None:
            return {"ok": False, "reason": "clob_not_configured"}
        return tb.clob.get_order_book(token_id)

    @tool("get_whale_trades")
    def get_whale_trades(wallet: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent trades for a wallet (Data API, best-effort)."""
        return tb.data_api.get_wallet_trades(wallet, limit=limit)

    @tool("search_x_news")
    def search_x_news(query: str) -> dict[str, Any]:
        """
        Placeholder for external search (X/news).

        Phase 3 keeps this disabled by default to avoid uncontrolled external IO.
        """
        logger.info("search_x_news called (placeholder)", query=query)
        return {"ok": False, "reason": "external_search_not_configured", "query": query, "results": []}

    @tool("calculate_probability_estimate")
    def calculate_probability_estimate(event_description: str) -> dict[str, Any]:
        """
        Placeholder for probability calculation.

        Phase 3 uses the Probability node to drive model estimates. This tool is provided for
        future hybrid flows where a node delegates probability estimation as a tool call.
        """
        logger.info("calculate_probability_estimate called (placeholder)")
        return {"ok": False, "reason": "use_probability_node", "event_description": event_description}

    @tool("get_current_portfolio")
    def get_current_portfolio() -> dict[str, Any]:
        """Return the current portfolio state snapshot."""
        state: PortfolioState = tb.portfolio.get_state()
        return state.model_dump(mode="json")

    return [
        get_market_details,
        get_order_book,
        get_whale_trades,
        search_x_news,
        calculate_probability_estimate,
        get_current_portfolio,
    ]
