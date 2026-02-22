"""
backup.py — iCloud backup of the PostgreSQL database.

Runs a pg_dump, compresses it, and writes it to:
  ~/Library/Mobile Documents/com~apple~CloudDocs/Database Backups/memory-system/

iCloud Drive automatically syncs this to the cloud.

Usage:
  python -m sync.backup
  python -m sync.backup --dry-run
"""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import typer

from ingestion.base import get_conn, CONFIG, insert_event

log = logging.getLogger(__name__)
app = typer.Typer(help="PostgreSQL → iCloud backup")


def _icloud_backup_dir() -> Path:
    icloud_root = (
        Path.home()
        / "Library"
        / "Mobile Documents"
        / "com~apple~CloudDocs"
    )
    folder = os.environ.get("ICLOUD_BACKUP_PATH")
    if folder:
        return Path(folder)
    db_name = os.environ.get("PG_DB", "memory_system")
    return icloud_root / "Database Backups" / db_name


def _pg_url() -> str:
    from ingestion.base import get_pg_url
    return get_pg_url()


def run_backup(dry_run: bool = False, verbose: bool = False) -> dict:
    """
    Execute pg_dump and write compressed backup to iCloud.
    Returns: {"success": bool, "path": str, "size_bytes": int}
    """
    backup_dir = _icloud_backup_dir()
    ts = datetime.now(timezone.utc)
    filename = f"memory_system_{ts.strftime('%Y%m%d_%H%M%S')}.sql.gz"
    dest = backup_dir / filename

    log.info("Starting backup → %s", dest)

    if dry_run:
        typer.echo(f"[DRY RUN] Would write: {dest}")
        return {"success": True, "path": str(dest), "size_bytes": 0}

    backup_dir.mkdir(parents=True, exist_ok=True)

    # Build pg_dump command
    db = os.environ.get("PG_DB", "memory_system")
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    user = os.environ.get("PG_USER", os.environ.get("USER", ""))
    pw = os.environ.get("PG_PASSWORD", "")

    pg_dump = shutil.which("pg_dump") or "/usr/local/opt/postgresql@18/bin/pg_dump"
    cmd = [pg_dump, "-h", host, "-p", port, "-U", user, "--format=plain", "--no-password", db]

    env = os.environ.copy()
    if pw:
        env["PGPASSWORD"] = pw

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            env=env,
            check=True,
        )
        dump_data = proc.stdout

        with gzip.open(dest, "wb", compresslevel=9) as gz:
            gz.write(dump_data)

        size = dest.stat().st_size
        log.info("Backup complete: %s (%.1f MB)", dest, size / 1024 / 1024)

        # Prune old backups
        keep_days = int(os.environ.get("BACKUP_KEEP_DAYS", 30))
        _prune_old_backups(backup_dir, keep_days)

        # Log success event
        _log_system_event(f"backup_success: {filename} ({size} bytes)")

        return {"success": True, "path": str(dest), "size_bytes": size}

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode()[:500] if e.stderr else "unknown error"
        log.error("pg_dump failed: %s", err)
        _log_system_event(f"backup_failed: {err}", importance=5)
        return {"success": False, "path": "", "size_bytes": 0, "error": err}

    except Exception as e:
        log.error("Backup error: %s", e)
        _log_system_event(f"backup_failed: {e}", importance=5)
        return {"success": False, "path": "", "size_bytes": 0, "error": str(e)}


def _prune_old_backups(backup_dir: Path, keep_days: int) -> int:
    """Delete backups older than keep_days. Returns count deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    deleted = 0
    for f in backup_dir.glob("memory_system_*.sql.gz"):
        if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < cutoff:
            f.unlink()
            log.info("Pruned old backup: %s", f.name)
            deleted += 1
    return deleted


def _log_system_event(content: str, importance: int = 2) -> None:
    """Write a system event to memory_log."""
    try:
        insert_event(
            content=content,
            event_type="system",
            source_name="system",
            importance=importance,
            tags=["backup", "system"],
        )
    except Exception as e:
        log.warning("Could not log system event: %s", e)


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run a database backup to iCloud."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = run_backup(dry_run=dry_run, verbose=verbose)
    if result["success"]:
        typer.echo(f"✅ Backup OK: {result.get('path')} ({result.get('size_bytes',0)} bytes)")
    else:
        typer.echo(f"❌ Backup FAILED: {result.get('error')}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
