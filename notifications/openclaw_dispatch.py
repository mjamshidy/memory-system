"""
openclaw_dispatch.py — Send messages via OpenClaw.

OpenClaw is already connected to Telegram, WhatsApp, and iMessage.
We call its local REST API to dispatch through its channels.

API (inferred from OpenClaw's local-first design):
  POST http://localhost:3000/api/dispatch
  {
    "channel": "whatsapp" | "imessage" | "telegram" | "slack" | ...,
    "to": "<phone_or_id>",
    "message": "...",
    "type": "text" | "alert"
  }

Adjust OPENCLAW_API_URL and endpoint in .env if your setup differs.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)


class OpenClawDispatcher:
    """Dispatch messages through OpenClaw's channel integrations."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.base_url = (base_url or os.environ.get("OPENCLAW_API_URL", "http://localhost:3000")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENCLAW_API_KEY", "")
        self.dispatch_endpoint = os.environ.get("OPENCLAW_DISPATCH_ENDPOINT", "/api/dispatch")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def send(
        self,
        message: str,
        channel: str = "telegram",
        to: str | None = None,
        msg_type: str = "text",
        extra: dict | None = None,
    ) -> bool:
        """
        Send a message via OpenClaw.
        channel: 'whatsapp', 'imessage', 'telegram', 'slack', 'discord', etc.
        to: recipient identifier (phone number, username, chat_id, etc.)
        Returns True on success.
        """
        payload: dict[str, Any] = {
            "channel": channel,
            "message": message,
            "type": msg_type,
            **({"to": to} if to else {}),
            **(extra or {}),
        }

        url = f"{self.base_url}{self.dispatch_endpoint}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.is_success:
                    return True
                log.error("OpenClaw dispatch failed [%s]: %s", resp.status_code, resp.text[:200])
                return False
        except httpx.ConnectError:
            log.warning("OpenClaw not reachable at %s — skipping dispatch", self.base_url)
            return False
        except Exception as e:
            log.error("OpenClaw dispatch error: %s", e)
            return False

    async def send_alert(self, message: str, channels: list[str] | None = None) -> dict[str, bool]:
        """
        Send an alert to multiple OpenClaw channels.
        Returns dict of {channel: success}.
        """
        targets = channels or ["telegram"]
        results = {}
        for ch in targets:
            results[ch] = await self.send(message, channel=ch, msg_type="alert")
        return results

    async def health_check(self) -> bool:
        """Check if OpenClaw is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/health", headers=self._headers())
                return resp.is_success
        except Exception:
            return False


_dispatcher: OpenClawDispatcher | None = None

def get_dispatcher() -> OpenClawDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = OpenClawDispatcher()
    return _dispatcher
