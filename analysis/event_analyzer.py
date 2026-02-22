"""
event_analyzer.py — Event-triggered analysis.

Called immediately when a high-importance event arrives.
Produces an event card note in Obsidian and fires alerts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from ingestion.base import get_conn, insert_event, CONFIG
from .llm_client import get_llm

log = logging.getLogger(__name__)

_LLM_SPEC = os.environ.get("LLM_GIST") or CONFIG.get("analysis", {}).get("llm_gist", "claude:claude-haiku-4-5-20251001")
_ALERT_THRESHOLD = int(os.environ.get("IMPORTANCE_ALERT_THRESHOLD", 4))

ANALYSIS_SYSTEM = """You are an event analysis assistant. Analyze this event and provide:
1. A brief assessment of what happened
2. Potential implications or follow-up actions
3. Relevant context if apparent

Respond in JSON:
{
  "assessment": "<2-3 sentences about what this event means>",
  "implications": ["<implication 1>", ...],
  "follow_up": ["<action 1>", ...],
  "severity": "low" | "medium" | "high" | "critical",
  "tags": ["<tag1>", ...],
  "title": "<short event title>"
}"""


async def analyze_event(event_id: int) -> dict | None:
    """
    Fetch a single memory_log event by id and produce an analysis gist.
    Returns gist data dict or None if event not found.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT ml.*, a.name AS agent_name, s.name AS source_name
               FROM memory_log ml
               LEFT JOIN agents a ON a.id = ml.agent_id
               LEFT JOIN sources s ON s.id = ml.source_id
               WHERE ml.id = %s""",
            (event_id,),
        ).fetchone()
    if not row:
        log.warning("Event %d not found", event_id)
        return None

    event = dict(row)
    ts = event["event_time"].strftime("%Y-%m-%d %H:%M:%S")
    prompt = (
        f"Analyze this event:\n"
        f"Time: {ts}\n"
        f"Source: {event['source_name']}\n"
        f"Type: {event['event_type']}\n"
        f"Importance: {event['importance']}/5\n"
        f"Content: {event['content']}\n"
        f"Payload: {event.get('payload', {})}"
    )

    llm = get_llm(_LLM_SPEC)
    try:
        analysis = await llm.complete_json(prompt, system=ANALYSIS_SYSTEM)
    except Exception as e:
        log.error("Analysis failed for event %d: %s", event_id, e)
        return None

    # Store as a gist
    with get_conn() as conn:
        gist_row = conn.execute(
            """INSERT INTO gists
                (period_start, period_end, source_ids, log_ids, gist_type,
                 title, content, tags, importance, llm_model)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                event["event_time"],
                event["event_time"],
                [event["source_id"]] if event["source_id"] else [],
                [event_id],
                "event_analysis",
                analysis.get("title", f"Event {event_id} analysis")[:500],
                analysis.get("assessment", ""),
                analysis.get("tags", []),
                event["importance"],
                _LLM_SPEC,
            ),
        ).fetchone()
        gist_id = gist_row["id"]
        conn.execute(
            "UPDATE memory_log SET processed = TRUE, gist_id = %s WHERE id = %s",
            (gist_id, event_id),
        )

    analysis["_gist_id"] = gist_id
    analysis["_event"] = event

    # Write Obsidian event card for high-importance events
    if event["importance"] >= _ALERT_THRESHOLD:
        try:
            from obsidian.writer import write_event_card
            await write_event_card(gist_id, analysis, event)
        except Exception as e:
            log.error("Obsidian event card write failed: %s", e)

        # Dispatch alerts
        try:
            from notifications.dispatcher import dispatch_alert
            severity = analysis.get("severity", "medium")
            message = (
                f"🔔 *{analysis.get('title', 'Event Alert')}*\n\n"
                f"{analysis.get('assessment', '')}\n\n"
                f"Source: {event['source_name']} | Importance: {event['importance']}/5"
            )
            channels = CONFIG.get("notifications", {}).get(
                "channels_by_importance", {}
            ).get(str(event["importance"]), [])
            await dispatch_alert(message, channels=channels, metadata={"event_id": event_id})
        except Exception as e:
            log.error("Alert dispatch failed: %s", e)

    return analysis


async def process_new_high_importance_events() -> int:
    """Scan for unprocessed high-importance events and analyze them. Returns count."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id FROM memory_log
               WHERE processed = FALSE AND importance >= %s
               ORDER BY recorded_at ASC""",
            (_ALERT_THRESHOLD,),
        ).fetchall()

    count = 0
    for row in rows:
        result = await analyze_event(row["id"])
        if result:
            count += 1
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(process_new_high_importance_events())
