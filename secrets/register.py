"""
secrets/register.py — Interactively register all memory-system secrets
into Bitwarden Secrets Manager.

Usage:
  export BWS_ACCESS_TOKEN=<your-service-account-token>
  python -m secrets.register

  # Or supply a specific project ID:
  python -m secrets.register --project-id <uuid>

  # List current secrets (no values printed):
  python -m secrets.register --list

  # Check which secrets are missing:
  python -m secrets.register --check

What it does:
  - Reads existing secrets from Bitwarden (by key name)
  - For each REQUIRED_SECRETS entry not yet in Bitwarden, prompts for the value
  - Creates or updates the secret via `bws secret create` / `bws secret edit`
  - Non-interactive mode (--env-file): reads values from a local .env and pushes them all
"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from .loader import REQUIRED_SECRETS, OPTIONAL_SECRETS, BWS_BIN, _bws_access_token, _fetch_from_bws

app = typer.Typer(help="Register memory-system secrets in Bitwarden Secrets Manager")

ALL_SECRETS = REQUIRED_SECRETS + OPTIONAL_SECRETS

# Human-readable descriptions for each secret
SECRET_DESCRIPTIONS: dict[str, str] = {
    "ANTHROPIC_API_KEY":       "Anthropic API key (sk-ant-...)",
    "OPENAI_API_KEY":          "OpenAI API key (sk-...)",
    "GOOGLE_API_KEY":          "Google API key for Gemini (AIza...)",
    "TELEGRAM_BOT_TOKEN":      "Telegram bot token (from @BotFather)",
    "TELEGRAM_CHAT_ID":        "Your Telegram chat/user ID for alerts",
    "TELEGRAM_ADMIN_CHAT_ID":  "Admin chat ID (can be same as TELEGRAM_CHAT_ID)",
    "OPENCLAW_API_KEY":        "OpenClaw local API key",
    "OPENCLAW_WEBHOOK_SECRET": "Secret for verifying OpenClaw webhook payloads",
    "HAOS_WEBHOOK_SECRET":     "Secret for verifying HAOS webhook payloads",
    "HAOS_LONG_LIVED_TOKEN":   "Home Assistant long-lived access token",
    "SUPABASE_DB_URL":         "Supabase PostgreSQL connection string",
    "SUPABASE_ANON_KEY":       "Supabase anonymous (public) API key",
    "SUPABASE_SERVICE_ROLE_KEY": "Supabase service role key (admin)",
    "PG_PASSWORD":             "Local PostgreSQL password (leave blank if using peer auth)",
    "INGESTION_SECRET":        "Shared secret for ingestion server auth header",
    "GITHUB_WEBHOOK_SECRET":   "Secret for verifying GitHub webhook signatures",
    "PG_URL":                  "Full PostgreSQL connection URL (optional override)",
    "SUPABASE_URL":            "Supabase project URL (https://xxx.supabase.co)",
    "OLLAMA_BASE_URL":         "Ollama base URL for local LLM (default: http://localhost:11434)",
}


def _run_bws(*args: str, input_text: str | None = None) -> dict | list:
    """Run a bws command and return parsed JSON."""
    token = _bws_access_token()
    if not token:
        typer.echo("❌ BWS_ACCESS_TOKEN is not set.", err=True)
        raise typer.Exit(1)

    cmd = [BWS_BIN, *args, "--output", "json", "--access-token", token]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=20,
    )
    if result.returncode != 0:
        typer.echo(f"❌ bws error: {result.stderr.strip()}", err=True)
        raise typer.Exit(1)
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def _get_projects() -> list[dict]:
    return _run_bws("project", "list")  # type: ignore


def _create_secret(key: str, value: str, project_id: str) -> dict:
    return _run_bws("secret", "create", key, value, project_id)  # type: ignore


def _update_secret(secret_id: str, key: str, value: str, project_id: str) -> dict:
    return _run_bws("secret", "edit", secret_id, "--key", key, "--value", value, "--project-id", project_id)  # type: ignore


def _pick_project(project_id: str | None) -> str:
    """Resolve project ID — prompt user to pick if not provided."""
    if project_id:
        return project_id

    projects = _get_projects()
    if not projects:
        typer.echo("❌ No projects found in Bitwarden. Create one first.", err=True)
        raise typer.Exit(1)

    if len(projects) == 1:
        pid = projects[0]["id"]
        typer.echo(f"Using project: {projects[0]['name']} ({pid})")
        return pid

    typer.echo("\nAvailable projects:")
    for i, p in enumerate(projects):
        typer.echo(f"  [{i}] {p['name']} ({p['id']})")
    idx = typer.prompt("Select project number", type=int, default=0)
    return projects[idx]["id"]


@app.command("register")
def register(
    project_id: str | None = typer.Option(None, "--project-id", "-p", help="Bitwarden project UUID"),
    env_file: Path | None = typer.Option(None, "--env-file", "-e", help="Push all values from a .env file"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing secrets"),
    skip_empty: bool = typer.Option(True, "--skip-empty/--no-skip-empty", help="Skip secrets left blank"),
):
    """Register all required secrets into Bitwarden Secrets Manager."""
    if not _bws_access_token():
        typer.echo("❌ BWS_ACCESS_TOKEN is not set. Export it first.", err=True)
        raise typer.Exit(1)

    pid = _pick_project(project_id)
    typer.echo(f"\n📦 Project ID: {pid}")

    # Fetch existing secrets
    typer.echo("Fetching existing secrets from Bitwarden...")
    existing: dict[str, dict] = {}
    try:
        secrets_list = _run_bws("secret", "list", pid)
        for s in secrets_list:  # type: ignore
            existing[s["key"]] = s
        typer.echo(f"  Found {len(existing)} existing secret(s)")
    except Exception:
        typer.echo("  No existing secrets (or empty project)")

    # Load values from .env file if provided
    dotenv_values: dict[str, str] = {}
    if env_file and env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                dotenv_values[k.strip()] = v.strip().strip('"').strip("'")
        typer.echo(f"  Loaded {len(dotenv_values)} values from {env_file}")

    typer.echo("")
    created = updated = skipped = 0

    for key in ALL_SECRETS:
        is_required = key in REQUIRED_SECRETS
        desc = SECRET_DESCRIPTIONS.get(key, "")
        label = f"{'[required]' if is_required else '[optional]'} {key}"

        if key in existing and not overwrite:
            typer.echo(f"  ⏭  {label} — already exists (use --overwrite to update)")
            skipped += 1
            continue

        # Determine value
        if key in dotenv_values:
            value = dotenv_values[key]
            typer.echo(f"  📄 {label} — from .env file")
        else:
            # Interactive prompt
            typer.echo(f"\n  {label}")
            if desc:
                typer.echo(f"     {desc}")
            if key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                       "TELEGRAM_BOT_TOKEN", "SUPABASE_DB_URL", "SUPABASE_SERVICE_ROLE_KEY",
                       "PG_PASSWORD", "INGESTION_SECRET", "HAOS_LONG_LIVED_TOKEN"):
                value = getpass.getpass(f"  Value (hidden): ")
            else:
                value = typer.prompt(f"  Value", default="", show_default=False)

        if not value and skip_empty:
            typer.echo(f"  ⏭  {key} — skipped (empty)")
            skipped += 1
            continue

        try:
            if key in existing:
                _update_secret(existing[key]["id"], key, value, pid)
                typer.echo(f"  ✅ Updated: {key}")
                updated += 1
            else:
                _create_secret(key, value, pid)
                typer.echo(f"  ✅ Created: {key}")
                created += 1
        except Exception as e:
            typer.echo(f"  ❌ Failed {key}: {e}", err=True)

    typer.echo(f"\n{'='*50}")
    typer.echo(f"Created: {created} | Updated: {updated} | Skipped: {skipped}")
    typer.echo("")
    typer.echo("Next: ensure BWS_ACCESS_TOKEN is available to launchd services.")
    typer.echo("Run: python -m secrets.register --check")


@app.command("check")
def check(
    project_id: str | None = typer.Option(None, "--project-id", "-p"),
):
    """Check which required secrets are present in Bitwarden."""
    pid = _pick_project(project_id) if not project_id else project_id
    existing = _fetch_from_bws(pid)

    typer.echo("\n🔑 Secret Status\n")
    missing = []
    for key in REQUIRED_SECRETS:
        if key in existing:
            typer.echo(f"  ✅ {key}")
        else:
            typer.echo(f"  ❌ {key}  ← MISSING")
            missing.append(key)

    typer.echo("\n📌 Optional secrets:")
    for key in OPTIONAL_SECRETS:
        mark = "✅" if key in existing else "○ "
        typer.echo(f"  {mark} {key}")

    typer.echo(f"\nTotal in Bitwarden: {len(existing)}")
    if missing:
        typer.echo(f"Missing required: {missing}")
        raise typer.Exit(1)
    else:
        typer.echo("All required secrets are present ✅")


@app.command("list")
def list_secrets(
    project_id: str | None = typer.Option(None, "--project-id", "-p"),
):
    """List all secrets in Bitwarden (key names only, no values)."""
    pid = project_id or _pick_project(None)
    secrets = _run_bws("secret", "list", pid)
    typer.echo(f"\n{'Key':<35} {'ID'}")
    typer.echo("-" * 75)
    for s in secrets:  # type: ignore
        typer.echo(f"{s['key']:<35} {s['id']}")


if __name__ == "__main__":
    app()
