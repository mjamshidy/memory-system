"""
query_engine.py — On-demand query engine.

Takes a natural-language question, retrieves relevant episodic events,
and produces a structured answer + Obsidian note.

Usage:
  python -m analysis.query_engine "What happened with HAOS last week?"
  python -m analysis.query_engine "Summarize all my GitHub activity"
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone

from ingestion.base import get_conn, CONFIG
from .llm_client import get_llm

log = logging.getLogger(__name__)

_LLM_SPEC = os.environ.get("LLM_QUERY") or CONFIG.get("analysis", {}).get("llm_query", "claude:claude-sonnet-4-6")

QUERY_SYSTEM = """You are a personal memory query assistant.
You have been given a user's question and relevant events from their memory log.
Answer the question clearly and concisely in Markdown.
Cite specific events when possible (mention source and time).
End with ## Related Concepts as a list of [[Wikilinks]] to relevant topics."""


def _extract_keywords(query: str) -> list[str]:
    """Naive keyword extraction from the query string."""
    stopwords = {"what", "when", "where", "who", "how", "did", "do", "is", "are",
                 "the", "a", "an", "and", "or", "for", "in", "on", "at", "to",
                 "my", "me", "i", "with", "all", "any", "of", "from", "about"}
    words = re.findall(r"\b\w{3,}\b", query.lower())
    return [w for w in words if w not in stopwords][:10]


def _search_events(keywords: list[str], limit: int = 50) -> list[dict]:
    """Full-text search using trigram matching on combined keywords."""
    if not keywords:
        return []

    conditions = " OR ".join(["ml.content ILIKE %s"] * len(keywords))
    params = [f"%{kw}%" for kw in keywords] + [limit]

    sql = f"""
        SELECT ml.id, ml.event_time, ml.event_type, ml.content,
               ml.importance, ml.tags, a.name AS agent_name, s.name AS source_name
        FROM memory_log ml
        LEFT JOIN agents a  ON a.id = ml.agent_id
        LEFT JOIN sources s ON s.id = ml.source_id
        WHERE {conditions}
        ORDER BY ml.event_time DESC
        LIMIT %s
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


async def answer_query(
    question: str,
    write_to_obsidian: bool = True,
    notify: bool = False,
) -> dict:
    """
    Answer a natural-language query using the episodic log.

    Returns:
        {
          "question": str,
          "answer": str,          # Markdown answer
          "event_count": int,
          "gist_id": int | None,
          "obsidian_path": str | None,
        }
    """
    keywords = _extract_keywords(question)
    events = _search_events(keywords)
    log.info("Query: %r — found %d relevant events", question, len(events))

    if not events:
        answer = f"No events found in memory related to: *{question}*"
        return {
            "question": question, "answer": answer,
            "event_count": 0, "gist_id": None, "obsidian_path": None,
        }

    events_text = "\n".join(
        f"[{e['event_time'].strftime('%Y-%m-%d %H:%M')}] [{e.get('source_name','?')}] {e.get('content','')}"
        for e in events
    )
    prompt = f"Question: {question}\n\nRelevant memory events:\n{events_text}"

    llm = get_llm(_LLM_SPEC)
    answer = await llm.complete(prompt, system=QUERY_SYSTEM)

    # Store as gist
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO gists
                (period_start, period_end, log_ids, gist_type,
                 title, content, tags, importance, llm_model)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                now, now,
                [e["id"] for e in events],
                "query_result",
                f"Query: {question[:200]}",
                answer,
                ["query"] + keywords,
                3,
                _LLM_SPEC,
            ),
        ).fetchone()
        gist_id = row["id"]

    obsidian_path = None
    if write_to_obsidian:
        try:
            from obsidian.writer import write_query_note
            obsidian_path = await write_query_note(gist_id, question, answer, events)
        except Exception as e:
            log.error("Obsidian query note write failed: %s", e)

    if notify:
        try:
            from notifications.dispatcher import dispatch_alert
            short = answer[:2000]
            await dispatch_alert(f"🔍 *Query: {question[:100]}*\n\n{short}", channels=["telegram"])
        except Exception as e:
            log.error("Notify failed: %s", e)

    return {
        "question": question,
        "answer": answer,
        "event_count": len(events),
        "gist_id": gist_id,
        "obsidian_path": obsidian_path,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What happened recently?"
    result = asyncio.run(answer_query(question, notify=True))
    print(f"\n{'='*60}")
    print(f"Question: {result['question']}")
    print(f"Events searched: {result['event_count']}")
    print(f"Obsidian: {result.get('obsidian_path')}")
    print(f"\n{result['answer']}")
