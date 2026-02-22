"""
file_import.py — Bulk import of account data exports.

Supports:
  - Google Takeout (JSON/HTML/MBOX)
  - Twitter/X archive (tweets.js)
  - Facebook JSON export
  - Generic CSV
  - Generic JSONL (one JSON object per line)

Usage (CLI):
  python -m ingestion.file_import --format twitter --file ~/Downloads/tweets.js
  python -m ingestion.file_import --format csv --file data.csv --source my_bank --event-type bank_transaction
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import typer

from .base import insert_event

log = logging.getLogger(__name__)
app = typer.Typer(help="Bulk import account data exports into memory_log.")


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_twitter(path: Path) -> Iterator[dict]:
    """Parse Twitter archive tweets.js."""
    text = path.read_text(encoding="utf-8")
    # tweets.js starts with 'window.YTD.tweet.part0 = '
    text = re.sub(r"^window\.\S+ = ", "", text.strip())
    tweets = json.loads(text)
    for item in tweets:
        tweet = item.get("tweet", item)
        ts = tweet.get("created_at", "")
        try:
            event_time = datetime.strptime(ts, "%a %b %d %H:%M:%S +0000 %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            event_time = datetime.now(timezone.utc)
        yield {
            "content": f"Tweet: {tweet.get('full_text', tweet.get('text', ''))}",
            "event_type": "social_post",
            "source": "twitter",
            "event_time": event_time,
            "importance": 2,
            "tags": ["twitter", "import"],
            "payload": {"tweet_id": tweet.get("id_str"), "text": tweet.get("full_text")},
        }


def _parse_jsonl(path: Path, source: str, event_type: str) -> Iterator[dict]:
    """Parse JSONL — one JSON object per line."""
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                content = obj.get("content") or obj.get("text") or obj.get("message") or str(obj)
                ts = obj.get("timestamp") or obj.get("created_at") or obj.get("date")
                event_time = _parse_ts(ts)
                yield {
                    "content": content,
                    "event_type": event_type,
                    "source": source,
                    "event_time": event_time,
                    "importance": int(obj.get("importance", 2)),
                    "tags": obj.get("tags", []) + ["import"],
                    "payload": obj,
                }
            except json.JSONDecodeError as e:
                log.warning("Skipping line %d: %s", i + 1, e)


def _parse_csv(path: Path, source: str, event_type: str) -> Iterator[dict]:
    """Parse a generic CSV. Looks for content/text/message column."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            content_col = next(
                (k for k in ("content", "text", "message", "description", "body") if k in row),
                None,
            )
            content = row.get(content_col, str(dict(row))) if content_col else str(dict(row))
            ts = row.get("timestamp") or row.get("date") or row.get("created_at")
            event_time = _parse_ts(ts)
            yield {
                "content": content,
                "event_type": event_type,
                "source": source,
                "event_time": event_time,
                "importance": 2,
                "tags": ["import", source],
                "payload": dict(row),
            }


def _parse_ts(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%a %b %d %H:%M:%S +0000 %Y",
    ):
        try:
            dt = datetime.strptime(str(ts), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@app.command()
def run(
    format: str = typer.Option(..., "--format", "-f", help="twitter | jsonl | csv"),
    file: Path = typer.Option(..., "--file", help="Path to data file"),
    source: str = typer.Option("file_import", "--source", "-s", help="Source name in DB"),
    event_type: str = typer.Option("import", "--event-type", help="event_type value"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse only, do not write to DB"),
    limit: int = typer.Option(0, "--limit", help="Max records (0 = unlimited)"),
):
    """Import data from a file into memory_log."""
    if not file.exists():
        typer.echo(f"File not found: {file}", err=True)
        raise typer.Exit(1)

    parsers = {
        "twitter": lambda: _parse_twitter(file),
        "jsonl":   lambda: _parse_jsonl(file, source, event_type),
        "csv":     lambda: _parse_csv(file, source, event_type),
    }

    if format not in parsers:
        typer.echo(f"Unknown format: {format}. Choose: {', '.join(parsers)}", err=True)
        raise typer.Exit(1)

    count = 0
    errors = 0
    for record in parsers[format]():
        if limit and count >= limit:
            break
        if dry_run:
            print(f"[DRY] {record['event_time']} | {record['source']} | {record['content'][:80]}")
        else:
            try:
                insert_event(**record)
            except Exception as e:
                log.error("Failed to insert record %d: %s", count, e)
                errors += 1
        count += 1

    typer.echo(f"Processed {count} records. Errors: {errors}.")


if __name__ == "__main__":
    app()
