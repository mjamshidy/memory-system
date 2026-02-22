"""
writer.py — Write notes to the Obsidian vault.

Note types:
  - Event card:      single high-importance event, cross-linked
  - Analysis report: gist from batch processing
  - Digest:          daily / weekly digest
  - Query result:    on-demand query answer
  - Alert:           critical event notification

All notes follow a consistent frontmatter + body structure.
The vault path is set in .env: OBSIDIAN_VAULT_PATH
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from ingestion.base import get_conn, CONFIG

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vault path resolution
# ---------------------------------------------------------------------------

def _vault_root() -> Path:
    p = os.environ.get("OBSIDIAN_VAULT_PATH")
    if not p:
        p = str(Path.home() / "Library" / "Mobile Documents" /
                "iCloud~md~obsidian" / "Documents" / "Obsidian Vault")
    return Path(p)


def _memory_root() -> Path:
    folder = os.environ.get("OBSIDIAN_MEMORY_FOLDER") or CONFIG.get("obsidian", {}).get("memory_root", "Memory")
    return _vault_root() / folder


def _note_path(subfolder: str, filename: str) -> Path:
    p = _memory_root() / subfolder / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def _frontmatter(
    note_type: str,
    title: str,
    tags: list[str],
    extra: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag_list = "\n".join(f"  - {t.lstrip('#')}" for t in tags)
    extra_yaml = ""
    if extra:
        for k, v in extra.items():
            if isinstance(v, list):
                items = "\n".join(f"  - {i}" for i in v)
                extra_yaml += f"{k}:\n{items}\n"
            else:
                extra_yaml += f"{k}: {v}\n"
    return f"""---
type: {note_type}
title: "{title}"
created: {now}
tags:
{tag_list}
{extra_yaml}---

"""


def _slug(text: str) -> str:
    """Convert text to a safe filename slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80].strip("-")


async def _write_note(path: Path, content: str) -> str:
    """Write content to path, track in DB. Returns vault-relative path."""
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)

    vault_rel = str(path.relative_to(_vault_root()))
    checksum = hashlib.sha256(content.encode()).hexdigest()

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO obsidian_notes (vault_path, note_type, title, checksum, updated_at)
               VALUES (%s, %s, %s, %s, NOW())
               ON CONFLICT (vault_path) DO UPDATE
               SET checksum = EXCLUDED.checksum, updated_at = NOW()""",
            (vault_rel, path.stem, path.stem, checksum),
        )

    log.info("Obsidian note written: %s", vault_rel)
    return vault_rel


# ---------------------------------------------------------------------------
# Note writers
# ---------------------------------------------------------------------------

async def write_event_card(
    gist_id: int,
    analysis: dict,
    event: dict,
) -> str:
    """Write an event card note for a single high-importance event."""
    ts = event["event_time"].strftime("%Y-%m-%d")
    title = analysis.get("title", f"Event {event['id']}")
    slug = _slug(title)
    filename = f"{ts}-{slug}.md"
    path = _note_path("Events", filename)

    source = event.get("source_name", "unknown")
    severity = analysis.get("severity", "medium")
    sev_emoji = {"low": "🟡", "medium": "🟠", "high": "🔴", "critical": "🚨"}.get(severity, "🟠")

    tags = (
        ["memory-system", f"type/event", f"source/{source}", f"severity/{severity}"]
        + analysis.get("tags", [])
    )

    implications = "\n".join(f"- {i}" for i in analysis.get("implications", []))
    follow_up = "\n".join(f"- [ ] {a}" for a in analysis.get("follow_up", []))

    body = (
        _frontmatter("event_card", title, tags, extra={
            "gist_id": gist_id,
            "event_id": event["id"],
            "source": source,
            "importance": event.get("importance", 3),
        })
        + f"# {sev_emoji} {title}\n\n"
        + f"> **{event['event_time'].strftime('%Y-%m-%d %H:%M:%S UTC')}** | "
        + f"Source: [[{source.title()}]] | Importance: {event.get('importance',3)}/5\n\n"
        + f"## What Happened\n{analysis.get('assessment', event.get('content',''))}\n\n"
        + (f"## Implications\n{implications}\n\n" if implications else "")
        + (f"## Follow-up Actions\n{follow_up}\n\n" if follow_up else "")
        + f"## Raw Event\n```\n{event.get('content','')}\n```\n\n"
        + f"## Links\n[[Analysis/Events]] | [[{source.title()}]]\n"
    )

    return await _write_note(path, body)


async def write_analysis_note(
    gist_id: int,
    gist_data: dict,
    events: list[dict],
) -> str:
    """Write an analysis report note for a batch gist."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d")
    title = gist_data.get("title", f"Analysis {now.strftime('%Y-%m-%d %H:%M')}")
    slug = _slug(title)
    filename = f"{ts}-{slug}.md"
    path = _note_path("Analysis", filename)

    tags = ["memory-system", "type/analysis"] + gist_data.get("tags", [])

    facts = "\n".join(f"- {f}" for f in gist_data.get("facts", []))
    patterns = "\n".join(f"- {p}" for p in gist_data.get("patterns", []))

    sources = list({e.get("source_name", "unknown") for e in events})
    source_links = " | ".join(f"[[{s.title()}]]" for s in sources)

    body = (
        _frontmatter("analysis", title, tags, extra={
            "gist_id": gist_id,
            "event_count": len(events),
            "sources": sources,
        })
        + f"# 🧠 {title}\n\n"
        + f"> Period: {now.strftime('%Y-%m-%d')} | Events: {len(events)} | Sources: {source_links}\n\n"
        + f"## Summary\n{gist_data.get('summary', '')}\n\n"
        + (f"## Key Facts\n{facts}\n\n" if facts else "")
        + (f"## Patterns\n{patterns}\n\n" if patterns else "")
        + f"## Sources\n{source_links}\n\n"
        + "## Links\n[[Analysis]] | [[Memory/Index]]\n"
    )

    return await _write_note(path, body)


async def write_digest_note(
    gist_id: int,
    content: str,
    target_date: date,
    period: str = "daily",
) -> str:
    """Write a daily or weekly digest note."""
    ts = target_date.strftime("%Y-%m-%d")
    if period == "weekly":
        filename = f"Weekly-{ts}.md"
        title = f"Weekly Digest — Week of {ts}"
        subfolder = "Digests/Weekly"
    else:
        filename = f"{ts}.md"
        title = f"Daily Digest — {ts}"
        subfolder = "Digests/Daily"

    path = _note_path(subfolder, filename)
    tags = ["memory-system", "type/digest", f"period/{period}", ts]

    body = (
        _frontmatter("digest", title, tags, extra={"gist_id": gist_id, "date": ts})
        + f"# 📋 {title}\n\n"
        + content
        + f"\n\n---\n*Generated by memory-system | [[Memory/Index]]*\n"
    )

    return await _write_note(path, body)


async def write_query_note(
    gist_id: int,
    question: str,
    answer: str,
    events: list[dict],
) -> str:
    """Write a query result note."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d-%H%M%S")
    slug = _slug(question)
    filename = f"{ts}-{slug}.md"
    path = _note_path("Queries", filename)

    title = f"Query: {question[:80]}"
    tags = ["memory-system", "type/query"]

    sources = list({e.get("source_name", "unknown") for e in events})
    source_links = " | ".join(f"[[{s.title()}]]" for s in sources)

    body = (
        _frontmatter("query_result", title, tags, extra={
            "gist_id": gist_id,
            "event_count": len(events),
        })
        + f"# 🔍 {question}\n\n"
        + f"> Asked: {now.strftime('%Y-%m-%d %H:%M UTC')} | Events searched: {len(events)}\n\n"
        + answer
        + f"\n\n---\nSources: {source_links}\n"
        + f"*[[Memory/Index]] | [[Queries]]*\n"
    )

    return await _write_note(path, body)


async def write_alert_note(
    title: str,
    content: str,
    source: str,
    importance: int,
    event_id: int | None = None,
) -> str:
    """Write an alert note for immediate Obsidian visibility."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d-%H%M%S")
    slug = _slug(title)
    filename = f"{ts}-{slug}.md"
    path = _note_path("Alerts", filename)

    tags = ["memory-system", "type/alert", f"source/{source}", f"importance/{importance}"]

    emoji = {1: "ℹ️", 2: "📌", 3: "⚠️", 4: "🔴", 5: "🚨"}.get(importance, "⚠️")

    body = (
        _frontmatter("alert", title, tags, extra={
            "importance": importance,
            "source": source,
            "event_id": event_id or "null",
        })
        + f"# {emoji} {title}\n\n"
        + f"> {now.strftime('%Y-%m-%d %H:%M:%S UTC')} | Source: [[{source.title()}]] | "
        + f"Importance: {importance}/5\n\n"
        + content
        + "\n\n---\n[[Memory/Alerts]] | [[Memory/Index]]\n"
    )

    return await _write_note(path, body)
