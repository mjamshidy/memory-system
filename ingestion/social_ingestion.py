"""
social_ingestion.py — Social platform event ingestion.

Handles:
  - Telegram (via bot webhook or polling)
  - Twitter/X (via API v2 filtered stream or search)
  - GitHub (webhook events)
  - Generic webhook (any platform that can POST JSON)

For platforms that go through OpenClaw (WhatsApp, iMessage),
events arrive at /ingest/openclaw endpoint and are handled in server.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .base import BaseIngester, insert_event

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class TelegramIngester(BaseIngester):
    """Ingest Telegram messages (from bot webhook update objects)."""

    source_name = "telegram"
    source_type = "social"

    def ingest(self, update: dict) -> list[int]:  # type: ignore[override]
        """Process a Telegram Update object."""
        ids = []

        msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
        if not msg:
            return ids

        chat = msg.get("chat", {})
        sender = msg.get("from", {})
        text = msg.get("text") or msg.get("caption") or ""
        ts = msg.get("date")
        event_time = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        sender_name = " ".join(filter(None, [
            sender.get("first_name", ""),
            sender.get("last_name", ""),
            f'(@{sender.get("username")})' if sender.get("username") else "",
        ])).strip() or "Unknown"

        chat_name = chat.get("title") or chat.get("username") or str(chat.get("id", ""))
        content = f"Telegram [{chat_name}] {sender_name}: {text}"

        payload = {
            "update_id": update.get("update_id"),
            "message_id": msg.get("message_id"),
            "chat_id": chat.get("id"),
            "chat_name": chat_name,
            "from_id": sender.get("id"),
            "from_name": sender_name,
            "text": text,
            "has_media": any(
                k in msg for k in ("photo", "video", "document", "audio", "sticker", "voice")
            ),
        }

        event_id = self.store(
            content=content,
            event_type="telegram_message",
            event_time=event_time,
            importance=2,
            tags=["telegram", f"chat:{chat.get('id', 'unknown')}"],
            payload=payload,
        )
        ids.append(event_id)
        return ids


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

class GitHubIngester(BaseIngester):
    """Ingest GitHub webhook events."""

    source_name = "github"
    source_type = "social"

    _EVENT_IMPORTANCE: dict[str, int] = {
        "push":              2,
        "pull_request":      3,
        "issues":            3,
        "issue_comment":     2,
        "release":           4,
        "workflow_run":      2,
        "security_advisory": 5,
        "deployment":        3,
    }

    def ingest(self, data: dict) -> list[int]:  # type: ignore[override]
        """Process one GitHub webhook payload."""
        event_type = data.get("_github_event", "push")  # set by server from X-GitHub-Event header
        repo = data.get("repository", {}).get("full_name", "unknown")
        sender = data.get("sender", {}).get("login", "unknown")
        importance = self._EVENT_IMPORTANCE.get(event_type, 2)

        # Build human-readable content
        content = self._build_content(event_type, data, repo, sender)
        tags = ["github", f"repo:{repo}", f"event:{event_type}"]

        event_id = self.store(
            content=content,
            event_type=f"github_{event_type}",
            importance=importance,
            tags=tags,
            payload=data,
        )
        return [event_id]

    def _build_content(self, event_type: str, data: dict, repo: str, sender: str) -> str:
        if event_type == "push":
            commits = len(data.get("commits", []))
            branch = data.get("ref", "").replace("refs/heads/", "")
            return f"GitHub: {sender} pushed {commits} commit(s) to {repo}/{branch}"
        elif event_type == "pull_request":
            pr = data.get("pull_request", {})
            action = data.get("action", "")
            return f"GitHub PR #{pr.get('number')} {action}: {pr.get('title', '')} in {repo}"
        elif event_type in ("issues", "issue_comment"):
            issue = data.get("issue", {})
            action = data.get("action", "")
            return f"GitHub issue #{issue.get('number')} {action}: {issue.get('title', '')} in {repo}"
        elif event_type == "release":
            release = data.get("release", {})
            return f"GitHub release {release.get('tag_name', '')} published in {repo}"
        else:
            return f"GitHub {event_type} event in {repo} by {sender}"


# ---------------------------------------------------------------------------
# Generic webhook ingester (fallback for any JSON-posting source)
# ---------------------------------------------------------------------------

class GenericWebhookIngester(BaseIngester):
    """
    Fallback ingester for any source that POSTs JSON to /ingest/generic.
    The payload must include at minimum:
      { "source": "...", "content": "...", "event_type": "..." }
    """

    source_name = "generic"
    source_type = "webhook"

    def ingest(self, data: dict) -> list[int]:  # type: ignore[override]
        source = data.get("source", "generic")
        content = data.get("content") or str(data)
        event_type = data.get("event_type", "generic_event")
        importance = int(data.get("importance", 3))
        tags = data.get("tags", [])
        payload = data.get("payload", data)

        # Temporarily override source_name for storage
        orig = self.source_name
        self.source_name = source
        event_id = self.store(
            content=content,
            event_type=event_type,
            importance=importance,
            tags=tags,
            payload=payload,
        )
        self.source_name = orig
        return [event_id]
