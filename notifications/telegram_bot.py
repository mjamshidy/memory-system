"""
telegram_bot.py — Telegram notification client.

Outbound: send messages/alerts to configured chat.
Inbound:  Telegram bot webhook (handled by ingestion server /ingest/telegram).

Usage:
  from notifications.telegram_bot import TelegramNotifier
  notifier = TelegramNotifier()
  await notifier.send("Hello from memory-system!")
  await notifier.send_markdown("**Alert**: motion detected")
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

_MAX_MSG_LEN = 4096
_TG_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    """Async Telegram message sender (no polling, just sends)."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
    ):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not self.token:
            log.warning("TELEGRAM_BOT_TOKEN not set — Telegram notifications disabled")
        if not self.chat_id:
            log.warning("TELEGRAM_CHAT_ID not set — Telegram notifications disabled")

    def _url(self, method: str) -> str:
        return _TG_API.format(token=self.token, method=method)

    async def send(
        self,
        text: str,
        parse_mode: str = "Markdown",
        chat_id: str | None = None,
        disable_notification: bool = False,
    ) -> bool:
        """Send a text message. Returns True on success."""
        if not self.token or not self.chat_id:
            log.debug("Telegram not configured — skipping send")
            return False

        cid = chat_id or self.chat_id
        # Telegram has a 4096 char limit — truncate if necessary
        if len(text) > _MAX_MSG_LEN:
            text = text[:_MAX_MSG_LEN - 20] + "\n…*(truncated)*"

        payload: dict[str, Any] = {
            "chat_id": cid,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self._url("sendMessage"), json=payload)
                if not resp.is_success:
                    log.error("Telegram sendMessage failed: %s — %s", resp.status_code, resp.text)
                    return False
            return True
        except Exception as e:
            log.error("Telegram send error: %s", e)
            return False

    async def send_markdown(self, text: str, chat_id: str | None = None) -> bool:
        return await self.send(text, parse_mode="MarkdownV2", chat_id=chat_id)

    async def send_silent(self, text: str) -> bool:
        return await self.send(text, disable_notification=True)

    async def set_webhook(self, webhook_url: str) -> dict:
        """Register the bot webhook URL with Telegram."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self._url("setWebhook"),
                json={"url": webhook_url, "allowed_updates": ["message", "channel_post"]},
            )
            return resp.json()

    async def delete_webhook(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self._url("deleteWebhook"))
            return resp.json()


# Module-level singleton
_notifier: TelegramNotifier | None = None

def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
