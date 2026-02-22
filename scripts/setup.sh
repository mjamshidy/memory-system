#!/usr/bin/env bash
# =============================================================================
# setup.sh — One-shot installer for memory-system
#
# Usage:
#   cd ~/Workspace/memory-system
#   ./scripts/setup.sh
#
# What it does:
#   1. Verifies prerequisites (psql 18, python 3.11+)
#   2. Creates .env from .env.example if missing
#   3. Creates the PostgreSQL database
#   4. Applies all SQL schema migrations
#   5. Creates Python virtual environment and installs dependencies
#   6. Creates iCloud backup directory
#   7. Creates Obsidian Memory folder structure
#   8. Installs launchd services
#   9. Runs a quick smoke test
#
# =============================================================================

set -euo pipefail

# ---- Colors -----------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${BLUE}[setup]${RESET} $*"; }
ok()   { echo -e "${GREEN}  ✅ $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️  $*${RESET}"; }
fail() { echo -e "${RED}  ❌ $*${RESET}"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Memory System — Setup${RESET}"
echo -e "${BOLD}  Project: ${PROJECT_DIR}${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════════${RESET}"
echo ""

# =============================================================================
# 1. Prerequisites
# =============================================================================
log "Checking prerequisites..."

# PostgreSQL 18
PG_BIN="/usr/local/opt/postgresql@18/bin"
PSQL="${PG_BIN}/psql"
PGDUMP="${PG_BIN}/pg_dump"

if [[ ! -f "$PSQL" ]]; then
    PSQL="$(which psql 2>/dev/null || true)"
fi
[[ -z "$PSQL" ]] && fail "psql not found. Install: brew install postgresql@18"
PG_VER=$("$PSQL" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
ok "psql ${PG_VER} found at ${PSQL}"

# Python 3.11+
PYTHON="$(which python3.11 2>/dev/null || which python3 2>/dev/null || true)"
[[ -z "$PYTHON" ]] && fail "Python 3.11+ not found"
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python ${PY_VER} found at ${PYTHON}"

# =============================================================================
# 2. .env file
# =============================================================================
log "Setting up .env..."

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    warn ".env created from .env.example — please fill in your API keys before using AI features"
else
    ok ".env already exists"
fi

# Source the .env
set -a
# shellcheck disable=SC1091
source "${PROJECT_DIR}/.env" 2>/dev/null || true
set +a

PG_DB="${PG_DB:-memory_system}"
PG_USER="${PG_USER:-$(whoami)}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"

# =============================================================================
# 3. PostgreSQL database
# =============================================================================
log "Setting up PostgreSQL database '${PG_DB}'..."

# Check if DB already exists
if "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "$PG_DB"; then
    ok "Database '${PG_DB}' already exists"
else
    "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d postgres \
        -c "CREATE DATABASE ${PG_DB} ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8' TEMPLATE template0;" \
        >/dev/null
    ok "Database '${PG_DB}' created"
fi

# =============================================================================
# 4. SQL schema
# =============================================================================
log "Applying SQL schema..."

for sql_file in "${PROJECT_DIR}/schema/"*.sql; do
    filename=$(basename "$sql_file")
    log "  Applying ${filename}..."
    "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -f "$sql_file" >/dev/null
    ok "  ${filename} applied"
done

# =============================================================================
# 5. Python virtual environment
# =============================================================================
log "Setting up Python virtual environment..."

VENV_DIR="${PROJECT_DIR}/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created at ${VENV_DIR}"
else
    ok "Virtual environment already exists"
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

log "Installing Python dependencies (this may take a minute)..."
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet -e "${PROJECT_DIR}"
ok "Python dependencies installed"

# =============================================================================
# 6. iCloud backup directory
# =============================================================================
log "Setting up iCloud backup directory..."

ICLOUD_ROOT="${HOME}/Library/Mobile Documents/com~apple~CloudDocs"
BACKUP_DIR="${ICLOUD_BACKUP_PATH:-${ICLOUD_ROOT}/Database Backups/memory-system}"

if [[ -d "$ICLOUD_ROOT" ]]; then
    mkdir -p "$BACKUP_DIR"
    ok "iCloud backup dir: ${BACKUP_DIR}"
else
    warn "iCloud Drive not found at ${ICLOUD_ROOT} — backup will use local path"
    mkdir -p "${PROJECT_DIR}/backups"
    # Update .env to use local backup path
    if grep -q "ICLOUD_BACKUP_PATH=" "${PROJECT_DIR}/.env"; then
        sed -i '' "s|ICLOUD_BACKUP_PATH=.*|ICLOUD_BACKUP_PATH=${PROJECT_DIR}/backups|" "${PROJECT_DIR}/.env"
    fi
fi

# =============================================================================
# 7. Obsidian vault memory folder
# =============================================================================
log "Setting up Obsidian Memory folder structure..."

OBSIDIAN_VAULT="${OBSIDIAN_VAULT_PATH:-${HOME}/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault}"
MEMORY_ROOT="${OBSIDIAN_VAULT}/Memory"

if [[ -d "$OBSIDIAN_VAULT" ]]; then
    for folder in \
        "Memory/Events" \
        "Memory/Analysis" \
        "Memory/Digests/Daily" \
        "Memory/Digests/Weekly" \
        "Memory/Queries" \
        "Memory/Alerts" \
        "Memory/Sources" \
        "Memory/Concepts" \
        "Memory/Agents"; do
        mkdir -p "${OBSIDIAN_VAULT}/${folder}"
    done
    ok "Obsidian Memory folder structure created"
else
    warn "Obsidian vault not found at ${OBSIDIAN_VAULT}"
    warn "Set OBSIDIAN_VAULT_PATH in .env to your vault path"
fi

# =============================================================================
# 8. launchd services
# =============================================================================
log "Installing launchd services..."

LAUNCHD_DIR="${HOME}/Library/LaunchAgents"
mkdir -p "$LAUNCHD_DIR"

PLIST_NAMES=(
    "memory.server"
    "memory.backup"
    "memory.sync"
    "memory.digest"
    "memory.analysis"
)

for plist_name in "${PLIST_NAMES[@]}"; do
    src="${PROJECT_DIR}/launchd/${plist_name}.plist"
    dest="${LAUNCHD_DIR}/com.${plist_name}.plist"

    # Substitute placeholders
    sed \
        -e "s|VENV_PYTHON_PLACEHOLDER|${VENV_PYTHON}|g" \
        -e "s|PROJECT_DIR_PLACEHOLDER|${PROJECT_DIR}|g" \
        "$src" > "$dest"

    # Unload if already loaded (ignore errors)
    launchctl unload "$dest" 2>/dev/null || true

    # Load
    if launchctl load "$dest" 2>/dev/null; then
        ok "Loaded ${plist_name}"
    else
        warn "Could not load ${plist_name} (may need to restart or check logs)"
    fi
done

# =============================================================================
# 9. Smoke test
# =============================================================================
log "Running smoke tests..."

# Test DB connection
"$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" \
    -c "SELECT COUNT(*) FROM agents;" >/dev/null 2>&1 && ok "DB connection OK" || fail "DB connection failed"

# Test Python import
"$VENV_PYTHON" -c "from ingestion.base import get_conn; print('imports OK')" 2>/dev/null \
    && ok "Python imports OK" || warn "Python imports have issues — check .env and dependencies"

# Test server (brief check)
sleep 2
if curl -s -f http://localhost:8765/health >/dev/null 2>&1; then
    ok "Ingestion server responding at http://localhost:8765"
else
    warn "Ingestion server not yet ready — check logs/server-error.log"
fi

# =============================================================================
# Done!
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  Setup complete!${RESET}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  1. Edit ${PROJECT_DIR}/.env — fill in your API keys"
echo -e "  2. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for alerts"
echo -e "  3. Set SUPABASE_DB_URL for remote backup"
echo -e "  4. Set ANTHROPIC_API_KEY (or other LLM keys) for analysis"
echo ""
echo -e "  ${BOLD}Test it:${RESET}"
echo -e "  curl http://localhost:8765/health"
echo -e "  curl -X POST http://localhost:8765/ingest/agent \\"
echo -e "       -H 'Content-Type: application/json' \\"
echo -e "       -d '{\"agent\":\"claude\",\"content\":\"Hello memory!\",\"event_type\":\"message\"}'"
echo ""
echo -e "  ${BOLD}Logs:${RESET}"
echo -e "  tail -f ${PROJECT_DIR}/logs/server.log"
echo -e "  tail -f ${PROJECT_DIR}/logs/analysis.log"
echo ""
