"""
server.py — FastAPI ingestion server.

Listens on INGESTION_PORT (default 8765) and routes webhooks from:
  POST /ingest/agent       — AI agent memory writes
  POST /ingest/haos        — Home Assistant events
  POST /ingest/telegram    — Telegram bot webhook
  POST /ingest/github      — GitHub webhook
  POST /ingest/openclaw    — OpenClaw events
  POST /ingest/generic     — Any JSON payload

Also provides:
  GET  /health             — Health check
  GET  /stats              — Quick DB stats
  POST /query              — On-demand semantic search

Run:
  uvicorn ingestion.server:app --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Header, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .base import insert_event, get_conn, CONFIG
from .haos_ingestion import HAOSIngester
from .social_ingestion import TelegramIngester, GitHubIngester, GenericWebhookIngester

log = logging.getLogger(__name__)

app = FastAPI(
    title="Memory System — Ingestion Server",
    description="Append-only episodic memory ingestion endpoint",
    version="0.1.0",
)

_INGESTION_SECRET = os.environ.get("INGESTION_SECRET", "")

# Singleton ingesters (thread-safe — they hold no mutable state after __init__)
_haos = HAOSIngester()
_telegram = TelegramIngester()
_github = GitHubIngester()
_generic = GenericWebhookIngester()


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def _check_secret(secret: str | None) -> None:
    if not _INGESTION_SECRET:
        return  # not configured — open
    if secret != _INGESTION_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AgentEventRequest(BaseModel):
    agent: str
    event_type: str = "message"
    content: str
    session_id: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    tags: list[str] = []
    payload: dict = {}
    event_time: str | None = None  # ISO format


class QueryRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    source: str | None = None
    event_type: str | None = None
    importance_min: int = Field(default=1, ge=1, le=5)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memory_log").fetchone()
        return {"status": "ok", "total_events": row["n"]}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


@app.get("/stats")
async def stats(x_secret: str | None = Header(None, alias="X-Secret")):
    _check_secret(x_secret)
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM memory_log").fetchone()["n"]
        unprocessed = conn.execute(
            "SELECT COUNT(*) AS n FROM memory_log WHERE processed = FALSE"
        ).fetchone()["n"]
        by_source = conn.execute(
            """SELECT s.name, COUNT(*) AS n
               FROM memory_log ml JOIN sources s ON s.id = ml.source_id
               GROUP BY s.name ORDER BY n DESC"""
        ).fetchall()
    return {
        "total_events": total,
        "unprocessed_events": unprocessed,
        "by_source": [dict(r) for r in by_source],
    }


@app.post("/ingest/agent")
async def ingest_agent(
    req: AgentEventRequest,
    x_secret: str | None = Header(None, alias="X-Secret"),
):
    _check_secret(x_secret)
    source_map = {
        "claude":   "claude_api",
        "codex":    "codex_api",
        "gemini":   "gemini_api",
        "openclaw": "openclaw",
    }
    source_name = source_map.get(req.agent, f"{req.agent}_api")

    event_time = None
    if req.event_time:
        try:
            event_time = datetime.fromisoformat(req.event_time)
        except ValueError:
            pass

    import uuid
    sid = uuid.UUID(req.session_id) if req.session_id else None

    event_id = insert_event(
        content=req.content,
        event_type=req.event_type,
        source_name=source_name,
        agent_name=req.agent,
        session_id=sid,
        event_time=event_time,
        importance=req.importance,
        tags=req.tags,
        payload=req.payload,
    )
    return {"status": "ok", "id": event_id}


@app.post("/ingest/haos")
async def ingest_haos(
    request: Request,
    x_haos_secret: str | None = Header(None, alias="X-HAOS-Secret"),
):
    body = await request.body()
    if not _haos.verify_signature(body, x_haos_secret or ""):
        raise HTTPException(status_code=401, detail="Invalid HAOS signature")
    data = await request.json()
    ids = _haos.ingest(data)
    return {"status": "ok", "ids": ids}


@app.post("/ingest/telegram")
async def ingest_telegram(
    request: Request,
    x_secret: str | None = Header(None, alias="X-Secret"),
):
    _check_secret(x_secret)
    data = await request.json()
    ids = _telegram.ingest(data)
    return {"status": "ok", "ids": ids}


@app.post("/ingest/github")
async def ingest_github(
    request: Request,
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_hub_signature: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    body = await request.body()
    # Verify GitHub signature if secret configured
    gh_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if gh_secret and x_hub_signature:
        expected = "sha256=" + hmac.new(gh_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature):
            raise HTTPException(status_code=401, detail="Invalid GitHub signature")

    data = await request.json()
    data["_github_event"] = x_github_event or "unknown"
    ids = _github.ingest(data)
    return {"status": "ok", "ids": ids}


@app.post("/ingest/openclaw")
async def ingest_openclaw(
    request: Request,
    x_openclaw_secret: str | None = Header(None, alias="X-OpenClaw-Secret"),
):
    """
    Receive events from OpenClaw (heartbeat, skill output, user messages
    from WhatsApp/iMessage/etc that OpenClaw has already received).
    """
    expected = os.environ.get("OPENCLAW_WEBHOOK_SECRET", "")
    if expected and x_openclaw_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid OpenClaw secret")

    data = await request.json()
    # OpenClaw sends: { "event_type": "...", "content": "...", "channel": "whatsapp", ... }
    channel = data.get("channel", "openclaw")
    event_id = insert_event(
        content=data.get("content", str(data)),
        event_type=data.get("event_type", "openclaw_event"),
        source_name=channel if channel in ("telegram", "whatsapp", "imessage") else "openclaw",
        agent_name="openclaw",
        importance=int(data.get("importance", 3)),
        tags=data.get("tags", []) + ["openclaw"],
        payload=data,
    )
    return {"status": "ok", "id": event_id}


@app.post("/ingest/generic")
async def ingest_generic(
    request: Request,
    x_secret: str | None = Header(None, alias="X-Secret"),
):
    _check_secret(x_secret)
    data = await request.json()
    ids = _generic.ingest(data)
    return {"status": "ok", "ids": ids}


@app.post("/query")
async def query_memory(
    req: QueryRequest,
    x_secret: str | None = Header(None, alias="X-Secret"),
):
    """On-demand semantic search of the episodic log."""
    _check_secret(x_secret)

    filters = ["ml.importance >= %s"]
    params: list[Any] = [req.importance_min]

    if req.source:
        filters.append("s.name = %s")
        params.append(req.source)
    if req.event_type:
        filters.append("ml.event_type = %s")
        params.append(req.event_type)

    filters.append("ml.content ILIKE %s")
    params.append(f"%{req.query}%")
    params.append(req.limit)

    sql = f"""
        SELECT ml.id, ml.event_time, ml.event_type, ml.content,
               ml.importance, ml.tags, a.name AS agent_name, s.name AS source_name
        FROM memory_log ml
        LEFT JOIN agents a  ON a.id = ml.agent_id
        LEFT JOIN sources s ON s.id = ml.source_id
        WHERE {" AND ".join(filters)}
        ORDER BY ml.event_time DESC
        LIMIT %s
    """
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return {"results": [dict(r) for r in rows], "count": len(rows)}


# ---------------------------------------------------------------------------
# CLI entry point (used by launchd)
# ---------------------------------------------------------------------------

import typer

cli_app = typer.Typer()

@cli_app.command()
def serve(
    host: str = typer.Option(os.environ.get("INGESTION_HOST", "0.0.0.0")),
    port: int = typer.Option(int(os.environ.get("INGESTION_PORT", "8765"))),
    reload: bool = typer.Option(False),
):
    import uvicorn
    uvicorn.run("ingestion.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli_app()
