"""
gist_generator.py — Extract semantic gists from batches of episodic events.

The gist generator:
  1. Fetches unprocessed events from memory_log
  2. Groups them by source/session/time window
  3. Calls the configured LLM to extract facts, patterns, summaries
  4. Stores results in the gists table
  5. Marks events as processed
  6. Triggers Obsidian note creation if importance warrants it
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import psycopg

from ingestion.base import get_conn, CONFIG
from .llm_client import get_llm

log = logging.getLogger(__name__)

_LLM_SPEC = os.environ.get("LLM_GIST") or CONFIG.get("analysis", {}).get("llm_gist", "claude:claude-haiku-4-5-20251001")
_BATCH_SIZE = int(os.environ.get("ANALYSIS_BATCH_SIZE", 100))


GIST_SYSTEM = """You are a memory analyst. You receive a batch of raw episodic events and extract:
1. Key facts (concrete, verifiable statements)
2. Patterns (things that repeat or correlate)
3. A concise summary paragraph

Respond in JSON with this exact schema:
{
  "title": "<short descriptive title>",
  "summary": "<2-3 paragraph markdown summary>",
  "facts": ["<fact 1>", "<fact 2>", ...],
  "patterns": ["<pattern 1>", ...],
  "tags": ["<tag1>", "<tag2>", ...],
  "importance": <1-5 integer>,
  "gist_type": "summary" | "fact" | "pattern" | "event_analysis"
}"""


def _format_events_for_llm(events: list[dict]) -> str:
    lines = []
    for e in events:
        ts = e["event_time"].strftime("%Y-%m-%d %H:%M:%S") if e.get("event_time") else "?"
        source = e.get("source_name", "?")
        lines.append(f"[{ts}] [{source}] [{e.get('event_type','?')}] {e.get('content','')}")
    return "\n".join(lines)


async def generate_gist(events: list[dict]) -> dict:
    """Call LLM and parse a gist from a list of event dicts."""
    llm = get_llm(_LLM_SPEC)
    prompt = f"Extract a gist from these {len(events)} events:\n\n{_format_events_for_llm(events)}"
    try:
        return await llm.complete_json(prompt, system=GIST_SYSTEM)
    except Exception as e:
        log.error("LLM gist generation failed: %s", e)
        # Fallback: simple concatenation summary
        return {
            "title": f"Batch of {len(events)} events",
            "summary": "\n".join(e.get("content", "") for e in events[:5]),
            "facts": [],
            "patterns": [],
            "tags": [],
            "importance": max(e.get("importance", 1) for e in events),
            "gist_type": "summary",
        }


def _store_gist(
    gist_data: dict,
    events: list[dict],
    period_start: datetime,
    period_end: datetime,
) -> int:
    """Write gist to DB and mark events as processed. Returns gist id."""
    log_ids = [e["id"] for e in events]
    source_ids = list({e["source_id"] for e in events if e.get("source_id")})
    agent_ids = list({e["agent_id"] for e in events if e.get("agent_id")})

    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO gists
                (period_start, period_end, source_ids, agent_ids, log_ids,
                 gist_type, title, content, tags, importance, llm_model)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                period_start,
                period_end,
                source_ids,
                agent_ids,
                log_ids,
                gist_data.get("gist_type", "summary"),
                gist_data.get("title", "Untitled")[:500],
                gist_data.get("summary", ""),
                gist_data.get("tags", []),
                max(1, min(5, int(gist_data.get("importance", 3)))),
                _LLM_SPEC,
            ),
        ).fetchone()
        gist_id = row["id"]

        # Mark events as processed
        if log_ids:
            conn.execute(
                "UPDATE memory_log SET processed = TRUE, gist_id = %s WHERE id = ANY(%s)",
                (gist_id, log_ids),
            )

    return gist_id


def _fetch_unprocessed(limit: int = _BATCH_SIZE) -> list[dict]:
    """Fetch unprocessed events grouped by source, oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ml.id, ml.event_time, ml.event_type, ml.content,
                      ml.importance, ml.tags, ml.payload, ml.agent_id, ml.source_id,
                      a.name AS agent_name, s.name AS source_name
               FROM memory_log ml
               LEFT JOIN agents a  ON a.id = ml.agent_id
               LEFT JOIN sources s ON s.id = ml.source_id
               WHERE ml.processed = FALSE
               ORDER BY ml.recorded_at ASC
               LIMIT %s""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


async def run_batch(trigger_obsidian: bool = True) -> list[int]:
    """
    Main analysis loop:
    1. Fetch unprocessed events
    2. Group into logical batches (by source + time window)
    3. Generate and store a gist for each batch
    4. Optionally write Obsidian notes
    Returns list of created gist ids.
    """
    events = _fetch_unprocessed()
    if not events:
        log.debug("No unprocessed events.")
        return []

    log.info("Processing %d events", len(events))

    # Group by source — each source gets its own gist if > 5 events,
    # otherwise merge small sources into an "activity" gist.
    by_source: dict[str, list[dict]] = {}
    for e in events:
        key = e.get("source_name", "unknown")
        by_source.setdefault(key, []).append(e)

    gist_ids: list[int] = []

    for source, src_events in by_source.items():
        if not src_events:
            continue
        period_start = min(e["event_time"] for e in src_events)
        period_end = max(e["event_time"] for e in src_events)

        gist_data = await generate_gist(src_events)
        gist_id = _store_gist(gist_data, src_events, period_start, period_end)
        gist_ids.append(gist_id)
        log.info("Created gist %d for source=%s events=%d", gist_id, source, len(src_events))

        # Trigger Obsidian note for important gists
        if trigger_obsidian and int(gist_data.get("importance", 3)) >= 3:
            try:
                from obsidian.writer import write_analysis_note
                await write_analysis_note(gist_id, gist_data, src_events)
            except Exception as e:
                log.error("Obsidian write failed for gist %d: %s", gist_id, e)

    return gist_ids


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_batch())
