"""
digest.py — Periodic digest generation.

Produces:
  - Daily digest at 07:00 (all activity from yesterday)
  - Weekly summary every Monday (full week analysis)
  - On-demand for any time range

Triggered by launchd (memory.digest.plist).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from ingestion.base import get_conn, CONFIG
from .llm_client import get_llm

log = logging.getLogger(__name__)

_LLM_SPEC = os.environ.get("LLM_DIGEST") or CONFIG.get("analysis", {}).get("llm_digest", "claude:claude-sonnet-4-6")

DIGEST_SYSTEM = """You are a personal intelligence analyst with access to a person's complete daily activity log.
Write a concise, insightful digest in Markdown format. Be analytical, not just descriptive.

Structure:
## Summary
2-3 sentence overview of the day/period.

## Key Events
Bulleted list of the most important events.

## Patterns & Observations
Trends you notice across sources.

## Action Items
Things the person might want to follow up on.

## Stats
Brief stats (event count by source, etc.).

Use rich Markdown. Wikilinks like [[Concept]] are OK for Obsidian."""


def _fetch_events_for_period(start: datetime, end: datetime) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ml.id, ml.event_time, ml.event_type, ml.content,
                      ml.importance, ml.tags, a.name AS agent_name, s.name AS source_name
               FROM memory_log ml
               LEFT JOIN agents a  ON a.id = ml.agent_id
               LEFT JOIN sources s ON s.id = ml.source_id
               WHERE ml.event_time >= %s AND ml.event_time < %s
               ORDER BY ml.event_time ASC""",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]


def _events_to_prompt(events: list[dict]) -> str:
    lines = []
    for e in events:
        ts = e["event_time"].strftime("%H:%M:%S")
        lines.append(
            f"[{ts}] [{e.get('source_name','?')}] [{e.get('event_type','?')}] "
            f"[imp:{e.get('importance',2)}] {e.get('content','')}"
        )
    return "\n".join(lines)


async def generate_daily_digest(target_date: date | None = None) -> dict:
    """
    Generate and store a daily digest for target_date (defaults to yesterday).
    Returns: {"gist_id": int, "content": str, "obsidian_path": str}
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    events = _fetch_events_for_period(start, end)

    if not events:
        log.info("No events for %s — skipping digest", target_date)
        return {"gist_id": None, "content": "", "obsidian_path": None}

    log.info("Generating daily digest for %s (%d events)", target_date, len(events))
    llm = get_llm(_LLM_SPEC)

    stats = f"Total events: {len(events)}"
    prompt = (
        f"Generate a daily digest for {target_date}.\n\n"
        f"Stats: {stats}\n\n"
        f"Events:\n{_events_to_prompt(events)}"
    )

    content = await llm.complete(prompt, system=DIGEST_SYSTEM)

    # Store as gist
    log_ids = [e["id"] for e in events]
    source_ids = list({e.get("source_id") for e in events if e.get("source_id")})
    agent_ids = list({e.get("agent_id") for e in events if e.get("agent_id")})

    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO gists
                (period_start, period_end, source_ids, agent_ids, log_ids,
                 gist_type, title, content, tags, importance, llm_model)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                start, end,
                source_ids, agent_ids, log_ids,
                "digest",
                f"Daily Digest — {target_date}",
                content,
                ["digest", "daily", str(target_date)],
                3,
                _LLM_SPEC,
            ),
        ).fetchone()
        gist_id = row["id"]

    # Write to Obsidian
    obsidian_path = None
    try:
        from obsidian.writer import write_digest_note
        obsidian_path = await write_digest_note(gist_id, content, target_date, "daily")
    except Exception as e:
        log.error("Obsidian digest write failed: %s", e)

    # Send to Telegram
    try:
        from notifications.dispatcher import dispatch_alert
        # Truncate for Telegram (4096 char limit)
        tg_msg = f"📋 *Daily Digest — {target_date}*\n\n{content[:3800]}"
        await dispatch_alert(tg_msg, channels=["telegram"])
    except Exception as e:
        log.error("Telegram digest send failed: %s", e)

    return {"gist_id": gist_id, "content": content, "obsidian_path": obsidian_path}


async def generate_weekly_digest(target_week_start: date | None = None) -> dict:
    """Generate a weekly summary. target_week_start defaults to last Monday."""
    today = date.today()
    if target_week_start is None:
        target_week_start = today - timedelta(days=today.weekday() + 7)

    start = datetime.combine(target_week_start, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=7)

    events = _fetch_events_for_period(start, end)
    if not events:
        return {"gist_id": None, "content": "", "obsidian_path": None}

    log.info("Generating weekly digest for week of %s (%d events)", target_week_start, len(events))
    llm = get_llm(_LLM_SPEC)
    prompt = (
        f"Generate a weekly summary for the week of {target_week_start} to {target_week_start + timedelta(days=6)}.\n\n"
        f"Total events: {len(events)}\n\n"
        f"Events:\n{_events_to_prompt(events)}"
    )
    content = await llm.complete(prompt, system=DIGEST_SYSTEM)

    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO gists
                (period_start, period_end, source_ids, log_ids, gist_type,
                 title, content, tags, importance, llm_model)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                start, end,
                list({e.get("source_id") for e in events if e.get("source_id")}),
                [e["id"] for e in events],
                "digest",
                f"Weekly Digest — Week of {target_week_start}",
                content,
                ["digest", "weekly", str(target_week_start)],
                3,
                _LLM_SPEC,
            ),
        ).fetchone()
        gist_id = row["id"]

    obsidian_path = None
    try:
        from obsidian.writer import write_digest_note
        obsidian_path = await write_digest_note(gist_id, content, target_week_start, "weekly")
    except Exception as e:
        log.error("Obsidian weekly digest write failed: %s", e)

    return {"gist_id": gist_id, "content": content, "obsidian_path": obsidian_path}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode == "weekly":
        asyncio.run(generate_weekly_digest())
    else:
        asyncio.run(generate_daily_digest())
