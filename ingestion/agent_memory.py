"""
agent_memory.py — MemoryClient SDK

Import this in any AI agent to give it persistent episodic memory.

Usage:
    from ingestion.agent_memory import MemoryClient

    mem = MemoryClient(agent="claude", session_label="code-review-session")

    # Write
    mem.log("User asked about X, I answered with Y")
    mem.log_action("searched_web", {"query": "X", "results": ["A", "B"]})
    mem.log_observation("User seems focused on performance")

    # Read — semantic search across the episodic log
    past = mem.recall("previous discussions about X", limit=5)
    for entry in past:
        print(entry["event_time"], entry["content"])

    # End session
    mem.end_session()
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .base import get_conn, insert_event, ensure_agent, ensure_source, get_pg_url

log = logging.getLogger(__name__)

# Map agent string → source name in sources table
_AGENT_SOURCE_MAP = {
    "claude":   "claude_api",
    "codex":    "codex_api",
    "gemini":   "gemini_api",
    "openclaw": "openclaw",
}


class MemoryClient:
    """
    Thread-safe, session-scoped memory client for AI agents.

    Parameters
    ----------
    agent : str
        One of 'claude', 'codex', 'gemini', 'openclaw', or a custom name.
    session_label : str, optional
        Human-readable label for this session (e.g. task description).
    context : dict, optional
        Extra metadata stored with the session.
    """

    def __init__(
        self,
        agent: str,
        session_label: str = "",
        context: dict | None = None,
    ):
        self.agent = agent
        self.session_label = session_label
        self._agent_id: uuid.UUID = ensure_agent(agent)
        self._source_name: str = _AGENT_SOURCE_MAP.get(agent, f"{agent}_api")
        self._source_id: uuid.UUID = ensure_source(self._source_name, "agent")
        self._session_id: uuid.UUID = self._create_session(context or {})

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _create_session(self, context: dict) -> uuid.UUID:
        ctx = {"label": self.session_label, **context}
        with get_conn() as conn:
            row = conn.execute(
                """INSERT INTO sessions (agent_id, source_id, context)
                   VALUES (%s, %s, %s) RETURNING id""",
                (
                    self._agent_id,
                    self._source_id,
                    psycopg.types.json.Jsonb(ctx),
                ),
            ).fetchone()
            return uuid.UUID(str(row["id"]))

    def end_session(self) -> None:
        """Mark the session as ended."""
        with get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = NOW() WHERE id = %s",
                (self._session_id,),
            )
        log.debug("Session %s ended", self._session_id)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def log(
        self,
        content: str,
        *,
        importance: int = 3,
        tags: list[str] | None = None,
        payload: dict | None = None,
    ) -> int:
        """Log a plain text observation or message exchange."""
        return insert_event(
            content=content,
            event_type="message",
            source_name=self._source_name,
            agent_name=self.agent,
            session_id=self._session_id,
            importance=importance,
            tags=tags or [],
            payload=payload or {},
        )

    def log_action(
        self,
        action: str,
        payload: dict | None = None,
        *,
        content: str = "",
        importance: int = 3,
        tags: list[str] | None = None,
    ) -> int:
        """Log a tool call or action the agent took."""
        text = content or f"Action: {action} — {payload}"
        return insert_event(
            content=text,
            event_type="action",
            source_name=self._source_name,
            agent_name=self.agent,
            session_id=self._session_id,
            importance=importance,
            tags=(tags or []) + [f"action:{action}"],
            payload={"action": action, **(payload or {})},
        )

    def log_observation(
        self,
        observation: str,
        *,
        importance: int = 2,
        tags: list[str] | None = None,
        payload: dict | None = None,
    ) -> int:
        """Log a passive observation (context, state change, etc.)."""
        return insert_event(
            content=observation,
            event_type="observation",
            source_name=self._source_name,
            agent_name=self.agent,
            session_id=self._session_id,
            importance=importance,
            tags=tags or [],
            payload=payload or {},
        )

    def log_conversation(
        self,
        user_message: str,
        assistant_response: str,
        *,
        importance: int = 3,
        tags: list[str] | None = None,
    ) -> int:
        """Log a full conversation turn."""
        content = f"User: {user_message}\n\nAssistant ({self.agent}): {assistant_response}"
        return insert_event(
            content=content,
            event_type="message",
            source_name=self._source_name,
            agent_name=self.agent,
            session_id=self._session_id,
            importance=importance,
            tags=tags or [],
            payload={
                "user": user_message,
                "assistant": assistant_response,
                "agent": self.agent,
            },
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        *,
        limit: int = 10,
        source: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        importance_min: int = 1,
    ) -> list[dict]:
        """
        Search the episodic log for entries matching *query* (full-text).
        Returns list of dicts with keys: id, event_time, event_type,
        content, payload, importance, tags, agent_name, source_name.
        """
        filters = ["ml.importance >= %s"]
        params: list[Any] = [importance_min]

        if source:
            filters.append("s.name = %s")
            params.append(source)
        if event_type:
            filters.append("ml.event_type = %s")
            params.append(event_type)
        if since:
            filters.append("ml.event_time >= %s")
            params.append(since)

        # Full-text similarity (trigram)
        filters.append("ml.content ILIKE %s")
        params.append(f"%{query}%")

        where = " AND ".join(filters)
        params.append(limit)

        sql = f"""
            SELECT
                ml.id, ml.event_time, ml.event_type, ml.content,
                ml.payload, ml.importance, ml.tags,
                a.name AS agent_name, s.name AS source_name
            FROM memory_log ml
            LEFT JOIN agents a  ON a.id = ml.agent_id
            LEFT JOIN sources s ON s.id = ml.source_id
            WHERE {where}
            ORDER BY ml.event_time DESC
            LIMIT %s
        """
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def recent(self, limit: int = 20, source: str | None = None) -> list[dict]:
        """Return the N most recent log entries."""
        params: list[Any] = []
        where = ""
        if source:
            where = "WHERE s.name = %s"
            params.append(source)
        params.append(limit)
        sql = f"""
            SELECT
                ml.id, ml.event_time, ml.event_type, ml.content,
                ml.importance, ml.tags, a.name AS agent_name, s.name AS source_name
            FROM memory_log ml
            LEFT JOIN agents a  ON a.id = ml.agent_id
            LEFT JOIN sources s ON s.id = ml.source_id
            {where}
            ORDER BY ml.event_time DESC
            LIMIT %s
        """
        with get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def session_history(self) -> list[dict]:
        """Return all entries logged in the current session."""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT id, event_time, event_type, content, importance, tags
                   FROM memory_log
                   WHERE session_id = %s
                   ORDER BY event_time ASC""",
                (self._session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Context window helper
    # ------------------------------------------------------------------

    def context_window(self, max_chars: int = 8000) -> str:
        """
        Return a compact text representation of recent memory suitable for
        injecting into an LLM context window.
        """
        entries = self.recent(limit=50)
        lines = []
        total = 0
        for e in reversed(entries):
            line = f"[{e['event_time'].strftime('%Y-%m-%d %H:%M')} {e['source_name']}] {e['content']}"
            total += len(line)
            if total > max_chars:
                break
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.end_session()
