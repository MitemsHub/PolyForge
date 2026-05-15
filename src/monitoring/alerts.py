from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from loguru import logger

from src.core.config import Settings


@dataclass(frozen=True)
class TelegramNotifier:
    bot_token: str
    chat_id: str

    def send_message(self, text: str, *, parse_mode: str | None = None, disable_web_page_preview: bool = True) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": disable_web_page_preview}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()


@dataclass(frozen=True)
class DiscordNotifier:
    webhook_url: str

    def send_message(self, text: str) -> dict[str, Any]:
        resp = requests.post(self.webhook_url, json={"content": text}, timeout=15)
        resp.raise_for_status()
        if resp.content:
            try:
                return resp.json()
            except Exception:
                return {"ok": True}
        return {"ok": True}


def get_telegram_notifier(settings: Settings) -> TelegramNotifier | None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    return TelegramNotifier(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
    )


def get_discord_notifier(settings: Settings) -> DiscordNotifier | None:
    if not settings.discord_webhook_url:
        return None
    return DiscordNotifier(webhook_url=str(settings.discord_webhook_url))


@dataclass
class AlertRateLimiter:
    per_minute: int
    window_start: datetime = datetime.now(timezone.utc)
    sent_in_window: int = 0

    def allow(self) -> bool:
        if self.per_minute <= 0:
            return False
        now = datetime.now(timezone.utc)
        if now - self.window_start >= timedelta(minutes=1):
            self.window_start = now
            self.sent_in_window = 0
        if self.sent_in_window >= self.per_minute:
            return False
        self.sent_in_window += 1
        return True


class AlertManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._telegram = get_telegram_notifier(settings)
        self._discord = get_discord_notifier(settings)
        self._limiter = AlertRateLimiter(per_minute=settings.alert_rate_limit_per_minute)

    def notify_text(self, text: str) -> None:
        if not self._limiter.allow():
            return
        if self._telegram is not None:
            try:
                self._telegram.send_message(text)
            except Exception as e:
                logger.warning("Telegram notify failed: {}", e)
        if self._discord is not None:
            try:
                self._discord.send_message(text)
            except Exception as e:
                logger.warning("Discord notify failed: {}", e)

    def cycle_summary(self, summary: dict[str, Any]) -> None:
        msg = f"PolyForge cycle summary: {summary}"
        self.notify_text(msg[:3500])

    def signal_high_confidence(self, signals: list[Any]) -> None:
        msg = f"PolyForge high-confidence signals: {signals}"
        self.notify_text(msg[:3500])

    def drawdown_alert(self, payload: dict[str, Any]) -> None:
        msg = f"PolyForge drawdown alert: {payload}"
        self.notify_text(msg[:3500])

    def daily_pnl_report(self, payload: dict[str, Any]) -> None:
        msg = f"PolyForge daily PnL: {payload}"
        self.notify_text(msg[:3500])


def try_notify(settings: Settings, text: str) -> None:
    AlertManager(settings).notify_text(text)
