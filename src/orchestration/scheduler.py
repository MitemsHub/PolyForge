from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.core.config import Settings
from src.orchestration.orchestrator import Orchestrator


@dataclass
class CycleStats:
    total: int = 0
    success: int = 0
    failure: int = 0
    last_error: str | None = None


class PolyForgeScheduler:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop = asyncio.Event()
        self._orch = Orchestrator(settings)
        self._stats = CycleStats()

        self._last_scanner_ts: float = 0.0
        self._last_agent_ts: float = 0.0

    async def start_main_loop(self, interval_minutes: int | None = None, *, execute: bool = False) -> None:
        interval = int(interval_minutes or self._settings.cycle_interval_minutes)
        logger.info("Scheduler starting", interval_minutes=interval, execute=execute)

        try:
            while not self._stop.is_set():
                await self.run_full_cycle(execute=execute)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=max(1, interval) * 60)
                except asyncio.TimeoutError:
                    continue
        finally:
            await self.graceful_shutdown()

    async def run_full_cycle(self, *, execute: bool) -> None:
        now = time.time()
        run_scanner = (now - self._last_scanner_ts) >= (self._settings.scanner_interval_minutes * 60)
        run_agent = (now - self._last_agent_ts) >= (self._settings.agent_interval_minutes * 60)

        if not run_scanner and not run_agent:
            return

        self._stats.total += 1
        try:
            started = datetime.now(timezone.utc).isoformat()
            logger.info("Cycle start", cycle=self._stats.total, started_at=started, run_scanner=run_scanner, run_agent=run_agent)

            result = await self._orch.orchestrate_cycle(
                execute=execute and self._settings.execute_enabled,
                run_agents=run_agent,
            )

            self._stats.success += 1
            self._stats.last_error = None

            self._last_scanner_ts = now
            if run_agent:
                self._last_agent_ts = now

            logger.info(
                "Cycle end",
                cycle=self._stats.total,
                finished_at=result.finished_at,
                timings=result.timings.__dict__,
                signal_count=result.signal_count,
                token_usage_estimate=result.token_usage_estimate,
            )
        except Exception as e:
            self._stats.failure += 1
            self._stats.last_error = str(e)
            await self.handle_cycle_error(e)

    async def handle_cycle_error(self, err: Exception) -> None:
        logger.exception("Cycle error")
        await asyncio.sleep(2)

    async def graceful_shutdown(self) -> None:
        logger.info("Scheduler shutting down", stats=self._stats.__dict__)
        self._orch.close()

    def stop(self) -> None:
        self._stop.set()
