"""
supabase_sync.py — Periodic sync to Supabase.

Strategy: pg_dump local → gzip → restore to Supabase (full sync every N hours).
Simple, reliable, no replication setup required.

For a production setup with millions of events, switch to logical replication.

Usage:
  python -m sync.supabase_sync
  python -m sync.supabase_sync --dry-run
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import typer

from ingestion.base import insert_event

log = logging.getLogger(__name__)
app = typer.Typer(help="Sync local PostgreSQL to Supabase")


def _pg_dump_local() -> bytes:
    """pg_dump the local memory_system DB and return the SQL bytes."""
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    user = os.environ.get("PG_USER", os.environ.get("USER", ""))
    db = os.environ.get("PG_DB", "memory_system")
    pw = os.environ.get("PG_PASSWORD", "")

    pg_dump = shutil.which("pg_dump") or "/usr/local/opt/postgresql@18/bin/pg_dump"
    cmd = [pg_dump, "-h", host, "-p", port, "-U", user, "--format=plain", "--no-password", db]

    env = os.environ.copy()
    if pw:
        env["PGPASSWORD"] = pw

    proc = subprocess.run(cmd, capture_output=True, env=env, check=True)
    return proc.stdout


def _pg_restore_to_supabase(sql_bytes: bytes) -> None:
    """Restore SQL dump to Supabase."""
    supabase_url = os.environ.get("SUPABASE_DB_URL")
    if not supabase_url:
        raise ValueError("SUPABASE_DB_URL not set in .env")

    psql = shutil.which("psql") or "/usr/local/opt/postgresql@18/bin/psql"

    # Write to a temp file (psql reads from stdin unreliably for large dumps)
    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as f:
        f.write(sql_bytes)
        tmp_path = f.name

    try:
        # First drop and recreate the DB schema on Supabase
        # (we use --clean flag to DROP before CREATE)
        cmd = [psql, supabase_url, "-f", tmp_path, "--quiet"]
        proc = subprocess.run(cmd, capture_output=True, check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode()[:500]
            raise RuntimeError(f"psql restore failed: {err}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def run_sync(dry_run: bool = False) -> dict:
    """
    Dump local DB → restore to Supabase.
    Returns {"success": bool, "size_bytes": int, ...}
    """
    log.info("Starting Supabase sync...")
    try:
        sql_bytes = _pg_dump_local()
        size = len(sql_bytes)
        log.info("Dump size: %.1f MB", size / 1024 / 1024)

        if dry_run:
            log.info("[DRY RUN] Would restore %d bytes to Supabase", size)
            return {"success": True, "size_bytes": size, "dry_run": True}

        _pg_restore_to_supabase(sql_bytes)
        log.info("Supabase sync complete")

        _log("sync_success: Supabase sync completed (%d bytes)" % size, 2)
        return {"success": True, "size_bytes": size}

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode()[:500] if e.stderr else str(e)
        log.error("pg_dump failed: %s", err)
        _log(f"sync_failed: {err}", 5)
        return {"success": False, "error": err}

    except Exception as e:
        log.error("Supabase sync error: %s", e)
        _log(f"sync_failed: {e}", 5)
        return {"success": False, "error": str(e)}


def _log(content: str, importance: int = 2) -> None:
    try:
        insert_event(
            content=content, event_type="system", source_name="system",
            importance=importance, tags=["sync", "supabase"],
        )
    except Exception:
        pass


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Sync local database to Supabase."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not os.environ.get("SUPABASE_DB_URL"):
        typer.echo("⚠️  SUPABASE_DB_URL not set — skipping sync", err=True)
        raise typer.Exit(0)

    result = run_sync(dry_run=dry_run)
    if result["success"]:
        typer.echo(f"✅ Supabase sync OK ({result.get('size_bytes', 0)} bytes)")
    else:
        typer.echo(f"❌ Supabase sync FAILED: {result.get('error')}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
