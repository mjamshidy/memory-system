"""
graph.py — Manage the Obsidian graph structure.

Maintains:
  - Memory/Index.md: master index of all memory sections
  - Memory/Sources/*.md: one note per data source
  - Memory/Concepts/*.md: concept nodes
  - Updates backlinks and concept associations in the DB
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from ingestion.base import get_conn, CONFIG
from .writer import _vault_root, _memory_root, _note_path, _frontmatter, _write_note

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Index note
# ---------------------------------------------------------------------------

INDEX_TEMPLATE = """# 🧠 Memory System — Index

> Auto-generated index. Do not edit manually.
> Last updated: {updated}

## Sections

- [[Memory/Digests/Daily|Daily Digests]]
- [[Memory/Digests/Weekly|Weekly Digests]]
- [[Memory/Analysis|Analysis Reports]]
- [[Memory/Events|Event Cards]]
- [[Memory/Queries|Query Results]]
- [[Memory/Alerts|Alerts]]
- [[Memory/Agents|Agent Interactions]]

## Sources
{source_links}

## Concepts
{concept_links}

## Stats
{stats}
"""


async def update_index() -> str:
    """Regenerate Memory/Index.md with current stats and links."""
    with get_conn() as conn:
        sources = conn.execute(
            "SELECT name, source_type FROM sources WHERE active = TRUE ORDER BY name"
        ).fetchall()
        concepts = conn.execute("SELECT name FROM concepts ORDER BY name").fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM memory_log").fetchone()["n"]
        notes = conn.execute("SELECT COUNT(*) AS n FROM obsidian_notes").fetchone()["n"]
        gists = conn.execute("SELECT COUNT(*) AS n FROM gists").fetchone()["n"]

    source_links = "\n".join(
        f"- [[Memory/Sources/{s['name'].title()}]] ({s['source_type']})"
        for s in sources
    )
    concept_links = "\n".join(f"- [[Memory/Concepts/{c['name']}]]" for c in concepts)
    stats = (
        f"- Total events: {total}\n"
        f"- Obsidian notes: {notes}\n"
        f"- Gists generated: {gists}"
    )

    content = INDEX_TEMPLATE.format(
        updated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        source_links=source_links or "*(none yet)*",
        concept_links=concept_links or "*(none yet)*",
        stats=stats,
    )

    path = _memory_root() / "Index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)

    log.info("Memory index updated: %s", path)
    return str(path)


# ---------------------------------------------------------------------------
# Source notes
# ---------------------------------------------------------------------------

SOURCE_TEMPLATE = """---
type: source_index
source: {name}
---

# 📡 {title}

> Source type: {source_type} | Status: Active

## Recent Events
{recent_events}

## Links
[[Memory/Index]] | [[Memory/Analysis]]
"""


async def update_source_note(source_name: str) -> str:
    """Create or update the index note for a specific source."""
    with get_conn() as conn:
        src = conn.execute(
            "SELECT * FROM sources WHERE name = %s", (source_name,)
        ).fetchone()
        if not src:
            return ""

        recent = conn.execute(
            """SELECT ml.event_time, ml.content, ml.importance
               FROM memory_log ml
               JOIN sources s ON s.id = ml.source_id
               WHERE s.name = %s
               ORDER BY ml.event_time DESC
               LIMIT 10""",
            (source_name,),
        ).fetchall()

    events_text = "\n".join(
        f"- [{e['event_time'].strftime('%Y-%m-%d %H:%M')}] {e['content'][:100]}"
        for e in recent
    ) or "*(no events yet)*"

    content = SOURCE_TEMPLATE.format(
        name=source_name,
        title=src["name"].replace("_", " ").title(),
        source_type=src["source_type"],
        recent_events=events_text,
    )

    path = _note_path("Sources", f"{source_name.title()}.md")
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)

    return str(path)


# ---------------------------------------------------------------------------
# Concept notes
# ---------------------------------------------------------------------------

async def ensure_concept_note(concept_name: str, description: str = "") -> str:
    """Create a concept node note if it doesn't exist."""
    path = _note_path("Concepts", f"{concept_name}.md")
    if path.exists():
        return str(path)

    content = (
        f"---\ntype: concept\nconcept: {concept_name}\n---\n\n"
        f"# {concept_name}\n\n"
        f"{description or '> *Concept node — automatically created*'}\n\n"
        f"## Related Notes\n*(links will appear here as events are processed)*\n\n"
        f"## Links\n[[Memory/Index]]\n"
    )
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)

    log.info("Created concept note: %s", concept_name)
    return str(path)
