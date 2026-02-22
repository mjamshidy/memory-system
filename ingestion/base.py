"""
base.py — Shared DB connection, config loader, and BaseIngester ABC.
All ingestion modules import from here.
"""
from __future__ import annotations

import os
import uuid
import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
import yaml
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    """Load config.yaml. Path defaults to ../config/config.yaml relative to this file."""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")
    with open(os.path.abspath(path)) as f:
        return yaml.safe_load(f)


CONFIG = load_config()


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def get_pg_url() -> str:
    url = os.environ.get("PG_URL")
    if url:
        return url
    user = os.environ.get("PG_USER", os.environ.get("USER", ""))
    password = os.environ.get("PG_PASSWORD", "")
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    db = os.environ.get("PG_DB", "memory_system")
    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return f"postgresql://{user}@{host}:{port}/{db}"


@contextmanager
def get_conn(autocommit: bool = False):
    """Yield a psycopg connection. Commits on success, rolls back on error."""
    conn = psycopg.connect(get_pg_url(), row_factory=dict_row, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ID resolution helpers
# ---------------------------------------------------------------------------

def resolve_agent_id(name: str) -> uuid.UUID | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM agents WHERE name = %s", (name,)).fetchone()
        return uuid.UUID(str(row["id"])) if row else None


def resolve_source_id(name: str) -> uuid.UUID | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM sources WHERE name = %s", (name,)).fetchone()
        return uuid.UUID(str(row["id"])) if row else None


def ensure_agent(name: str, description: str = "") -> uuid.UUID:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agents (name, description) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
            (name, description),
        )
        row = conn.execute("SELECT id FROM agents WHERE name = %s", (name,)).fetchone()
        return uuid.UUID(str(row["id"]))


def ensure_source(name: str, source_type: str = "webhook", description: str = "") -> uuid.UUID:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sources (name, source_type, description)
               VALUES (%s, %s, %s)
               ON CONFLICT (name) DO NOTHING""",
            (name, source_type, description),
        )
        row = conn.execute("SELECT id FROM sources WHERE name = %s", (name,)).fetchone()
        return uuid.UUID(str(row["id"]))


# ---------------------------------------------------------------------------
# Core write function
# ---------------------------------------------------------------------------

def insert_event(
    *,
    content: str,
    event_type: str,
    source_name: str,
    agent_name: str | None = None,
    session_id: uuid.UUID | None = None,
    event_time: datetime | None = None,
    importance: int = 3,
    tags: list[str] | None = None,
    payload: dict | None = None,
) -> int:
    """Insert one event into memory_log. Returns the new row id."""
    source_id = resolve_source_id(source_name)
    if source_id is None:
        source_id = ensure_source(source_name)

    agent_id = None
    if agent_name:
        agent_id = resolve_agent_id(agent_name)
        if agent_id is None:
            agent_id = ensure_agent(agent_name)

    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO memory_log
                (event_time, session_id, agent_id, source_id, event_type,
                 content, payload, importance, tags)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                event_time or datetime.now(timezone.utc),
                session_id,
                agent_id,
                source_id,
                event_type,
                content,
                psycopg.types.json.Jsonb(payload or {}),
                max(1, min(5, importance)),
                tags or [],
            ),
        ).fetchone()
        return row["id"]


# ---------------------------------------------------------------------------
# BaseIngester
# ---------------------------------------------------------------------------

class BaseIngester(ABC):
    """
    Abstract base class for all ingesters.
    Subclasses implement `ingest()` and call `self.store()` for each event.
    """

    source_name: str = "unknown"
    source_type: str = "webhook"

    def __init__(self):
        self.source_id = ensure_source(self.source_name, self.source_type)

    def store(
        self,
        content: str,
        event_type: str,
        *,
        agent_name: str | None = None,
        session_id: uuid.UUID | None = None,
        event_time: datetime | None = None,
        importance: int = 3,
        tags: list[str] | None = None,
        payload: dict | None = None,
    ) -> int:
        """Convenience wrapper around insert_event for subclasses."""
        return insert_event(
            content=content,
            event_type=event_type,
            source_name=self.source_name,
            agent_name=agent_name,
            session_id=session_id,
            event_time=event_time,
            importance=importance,
            tags=tags or [],
            payload=payload or {},
        )

    @abstractmethod
    def ingest(self, data: Any) -> list[int]:
        """Process data and return list of inserted memory_log ids."""
        ...
