from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.core.models import Market
from src.orchestration.orchestrator import Orchestrator


def test_full_cycle_simulation_offline(settings, monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    markets = [
        Market(id="M1", category="C1", token_ids=["YES", "NO"], raw={"yesPrice": "0.48", "noPrice": "0.48"}),
        Market(id="M2", category="C1", token_ids=["YES2", "NO2"], raw={"yesPrice": "0.50", "noPrice": "0.50"}),
    ]

    from src.data.gamma_client import GammaClient
    from src.data.data_api_client import DataAPIClient
    from src.data.clob_client import PolyClobClient
    from src.monitoring.alerts import AlertManager

    monkeypatch.setattr(PolyClobClient, "__init__", lambda self, _settings: (_ for _ in ()).throw(RuntimeError("no clob")))

    monkeypatch.setattr(GammaClient, "get_markets", lambda self, _params=None: markets)
    monkeypatch.setattr(GammaClient, "get_market_by_id", lambda self, market_id: next((m for m in markets if m.id == market_id), markets[0]))
    monkeypatch.setattr(DataAPIClient, "get_top_traders", lambda self, limit=10: [])
    monkeypatch.setattr(DataAPIClient, "get_wallet_trades", lambda self, wallet, limit=50: [])

    monkeypatch.setattr(AlertManager, "cycle_summary", lambda self, payload: None)
    monkeypatch.setattr(AlertManager, "signal_high_confidence", lambda self, signals: None)

    orch = Orchestrator(settings)
    res = asyncio.run(orch.orchestrate_cycle(execute=False, run_agents=True))
    assert res.signal_count >= 0
    orch.close()
