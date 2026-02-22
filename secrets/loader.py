"""
secrets/loader.py — Load secrets from Bitwarden Secrets Manager into os.environ.

Call `load()` once at process startup (before any other imports that read env vars).
If `bws run` was already used to launch the process, secrets are already in the
environment and this is a no-op for each key that's already set.

Priority (highest to lowest):
  1. Already in os.environ (set by `bws run` or the shell)
  2. Fetched fresh from `bws secret list` (requires BWS_ACCESS_TOKEN)
  3. .env file fallback (dev/offline only)

Only BWS_ACCESS_TOKEN needs to be present in the environment.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

BWS_BIN = shutil.which("bws") or str(Path.home() / ".cargo" / "bin" / "bws")

# All secret key names as they appear in Bitwarden Secrets Manager.
# Values are fetched by key name and injected into os.environ under the same name.
REQUIRED_SECRETS: list[str] = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ADMIN_CHAT_ID",
    "OPENCLAW_API_KEY",
    "OPENCLAW_WEBHOOK_SECRET",
    "HAOS_WEBHOOK_SECRET",
    "HAOS_LONG_LIVED_TOKEN",
    "SUPABASE_DB_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "PG_PASSWORD",
    "INGESTION_SECRET",
    "GITHUB_WEBHOOK_SECRET",
]

# Non-secret config that may also live in Bitwarden for portability
OPTIONAL_SECRETS: list[str] = [
    "PG_URL",
    "SUPABASE_URL",
    "OLLAMA_BASE_URL",
]

_loaded = False


def _bws_access_token() -> str | None:
    return os.environ.get("BWS_ACCESS_TOKEN")


def _fetch_from_bws(project_id: str | None = None) -> dict[str, str]:
    """
    Call `bws secret list` and return {key: value} for all secrets.
    Optionally filter by project_id.
    """
    token = _bws_access_token()
    if not token:
        return {}

    cmd = [BWS_BIN, "secret", "list", "--output", "json", "--access-token", token]
    if project_id:
        cmd.append(project_id)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        secrets_list = json.loads(result.stdout)
        # bws returns a list of {"key": "...", "value": "...", "id": "...", ...}
        return {item["key"]: item["value"] for item in secrets_list if "key" in item}
    except subprocess.TimeoutExpired:
        log.error("bws timed out fetching secrets")
        return {}
    except subprocess.CalledProcessError as e:
        log.error("bws error: %s", e.stderr.strip())
        return {}
    except json.JSONDecodeError as e:
        log.error("bws returned invalid JSON: %s", e)
        return {}
    except FileNotFoundError:
        log.error("bws binary not found at %s", BWS_BIN)
        return {}


def _load_dotenv_fallback() -> dict[str, str]:
    """Load .env as last-resort fallback (dev / offline mode)."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return {}
    result = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            result[key] = val
    return result


def load(
    project_id: str | None = None,
    force: bool = False,
    quiet: bool = False,
) -> dict[str, str]:
    """
    Load secrets into os.environ. Safe to call multiple times (no-op after first call).

    Parameters
    ----------
    project_id : str, optional
        Bitwarden project UUID to filter secrets. If None, fetches all accessible secrets.
    force : bool
        Re-fetch even if already loaded.
    quiet : bool
        Suppress info-level log messages.

    Returns
    -------
    dict of {key: source} where source is 'env', 'bws', or 'dotenv'.
    """
    global _loaded
    if _loaded and not force:
        return {}

    sources: dict[str, str] = {}

    # Step 1: what's already in the environment (e.g. set by `bws run`)
    already_set = {k for k in (REQUIRED_SECRETS + OPTIONAL_SECRETS) if os.environ.get(k)}
    for k in already_set:
        sources[k] = "env"

    # Step 2: fetch from bws for anything not already set
    missing = [k for k in (REQUIRED_SECRETS + OPTIONAL_SECRETS) if k not in already_set]
    if missing and _bws_access_token():
        if not quiet:
            log.info("Fetching %d secrets from Bitwarden Secrets Manager...", len(missing))
        bws_secrets = _fetch_from_bws(project_id)
        for key, value in bws_secrets.items():
            if key not in os.environ:  # don't override already-set env vars
                os.environ[key] = value
                sources[key] = "bws"
        fetched = [k for k in missing if k in bws_secrets]
        still_missing = [k for k in missing if k not in bws_secrets and k in REQUIRED_SECRETS]
        if not quiet:
            log.info("Loaded %d secrets from bws", len(fetched))
            if still_missing:
                log.warning("Secrets not found in Bitwarden: %s", still_missing)
    elif missing and not _bws_access_token():
        log.warning("BWS_ACCESS_TOKEN not set — falling back to .env")

    # Step 3: .env fallback for anything still missing
    env_vals = _load_dotenv_fallback()
    dotenv_loaded = 0
    for key, value in env_vals.items():
        if key not in os.environ:
            os.environ[key] = value
            sources[key] = "dotenv"
            dotenv_loaded += 1

    if dotenv_loaded and not quiet:
        log.info("Loaded %d values from .env fallback", dotenv_loaded)

    _loaded = True
    return sources


def get(key: str, default: str = "") -> str:
    """Get a secret by name, loading from bws if not yet in environment."""
    if not _loaded:
        load(quiet=True)
    return os.environ.get(key, default)


def status() -> dict:
    """Return a status dict showing which secrets are present (no values exposed)."""
    if not _loaded:
        load(quiet=True)
    present = []
    missing = []
    for k in REQUIRED_SECRETS:
        (present if os.environ.get(k) else missing).append(k)
    return {
        "bws_configured": bool(_bws_access_token()),
        "bws_bin": BWS_BIN,
        "required_present": present,
        "required_missing": missing,
        "all_required_present": len(missing) == 0,
    }
