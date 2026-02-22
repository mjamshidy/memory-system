#!/usr/bin/env bash
# =============================================================================
# setup.sh — One-shot installer for memory-system
#
# Usage:
#   export BWS_ACCESS_TOKEN=<your-service-account-token>
#   cd ~/Workspace/memory-system
#   ./scripts/setup.sh
#
# What it does:
#   1. Verifies prerequisites (psql 18, python 3.11+, bws)
#   2. Validates BWS_ACCESS_TOKEN
#   3. Creates the PostgreSQL database
#   4. Applies all SQL schema migrations
#   5. Creates Python virtual environment and installs dependencies
#   6. Creates iCloud backup directory
#   7. Creates Obsidian Memory folder structure
#   8. Installs launchd services (with bws run wrapping each process)
#   9. Runs a quick smoke test
#
# =============================================================================

set -euo pipefail

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
[[ ! -f "$PSQL" ]] && PSQL="$(which psql 2>/dev/null || true)"
[[ -z "$PSQL" ]] && fail "psql not found. Install: brew install postgresql@18"
PG_VER=$("$PSQL" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
ok "psql ${PG_VER} found at ${PSQL}"

# Python 3.11+
PYTHON="$(which python3.11 2>/dev/null || which python3 2>/dev/null || true)"
[[ -z "$PYTHON" ]] && fail "Python 3.11+ not found"
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python ${PY_VER} found at ${PYTHON}"

# bws (Bitwarden Secrets Manager CLI)
BWS_BIN="${HOME}/.cargo/bin/bws"
[[ ! -f "$BWS_BIN" ]] && BWS_BIN="$(which bws 2>/dev/null || true)"
[[ -z "$BWS_BIN" ]] && fail "bws not found. Install: cargo install bws"
BWS_VER=$("$BWS_BIN" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
ok "bws ${BWS_VER} found at ${BWS_BIN}"

# =============================================================================
# 2. Bitwarden Secrets Manager
# =============================================================================
log "Checking Bitwarden Secrets Manager..."

if [[ -z "${BWS_ACCESS_TOKEN:-}" ]]; then
    echo ""
    warn "BWS_ACCESS_TOKEN is not set."
    warn "Create a service account token in Bitwarden Secrets Manager:"
    warn "  https://bitwarden.com/products/secrets-manager/"
    warn ""
    warn "Then export it:"
    warn "  export BWS_ACCESS_TOKEN=<your-token>"
    warn "  ./scripts/setup.sh"
    warn ""
    warn "Alternatively, add it to your shell profile (~/.zshrc):"
    warn "  echo 'export BWS_ACCESS_TOKEN=<your-token>' >> ~/.zshrc"
    echo ""
    read -rp "  Continue without BWS? Secrets will fall back to .env [y/N]: " cont
    [[ "$cont" != "y" && "$cont" != "Y" ]] && fail "Aborted. Set BWS_ACCESS_TOKEN and re-run."
    BWS_ACCESS_TOKEN=""
    warn "Continuing without BWS — .env will be used as fallback"
else
    # Validate the token works
    if "$BWS_BIN" secret list --access-token "$BWS_ACCESS_TOKEN" --output json >/dev/null 2>&1; then
        ok "BWS_ACCESS_TOKEN is valid"
    else
        fail "BWS_ACCESS_TOKEN is set but invalid. Check your service account token."
    fi
fi

# =============================================================================
# 3. Non-secret config (.env for paths and non-sensitive settings only)
# =============================================================================
log "Setting up config..."

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    ok ".env created from .env.example"
    warn ".env now only contains non-secret config (paths, ports, etc.)"
    warn "All API keys must be stored in Bitwarden Secrets Manager"
else
    ok ".env exists"
fi

# Source non-secret config
set -a
source "${PROJECT_DIR}/.env" 2>/dev/null || true
set +a

PG_DB="${PG_DB:-memory_system}"
PG_USER="${PG_USER:-$(whoami)}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"

# =============================================================================
# 4. PostgreSQL database
# =============================================================================
log "Setting up PostgreSQL database '${PG_DB}'..."

if "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "$PG_DB"; then
    ok "Database '${PG_DB}' already exists"
else
    "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d postgres \
        -c "CREATE DATABASE ${PG_DB} ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8' TEMPLATE template0;" \
        >/dev/null
    ok "Database '${PG_DB}' created"
fi

# =============================================================================
# 5. SQL schema
# =============================================================================
log "Applying SQL schema..."

for sql_file in "${PROJECT_DIR}/schema/"*.sql; do
    filename=$(basename "$sql_file")
    "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -f "$sql_file" >/dev/null
    ok "  ${filename}"
done

# =============================================================================
# 6. Python virtual environment
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

log "Installing Python dependencies..."
"$VENV_PIP" install --quiet --upgrade pip setuptools wheel
"$VENV_PIP" install --quiet -e "${PROJECT_DIR}"
ok "Python dependencies installed"

# =============================================================================
# 7. iCloud backup directory
# =============================================================================
log "Setting up iCloud backup directory..."

ICLOUD_ROOT="${HOME}/Library/Mobile Documents/com~apple~CloudDocs"
BACKUP_DIR="${ICLOUD_BACKUP_PATH:-${ICLOUD_ROOT}/Database Backups/memory-system}"

if [[ -d "$ICLOUD_ROOT" ]]; then
    mkdir -p "$BACKUP_DIR"
    ok "iCloud backup dir ready: ${BACKUP_DIR}"
else
    warn "iCloud Drive not found — using local backup path"
    mkdir -p "${PROJECT_DIR}/backups"
fi

# =============================================================================
# 8. Obsidian vault
# =============================================================================
log "Setting up Obsidian Memory folder structure..."

OBSIDIAN_VAULT="${OBSIDIAN_VAULT_PATH:-${HOME}/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault}"

if [[ -d "$OBSIDIAN_VAULT" ]]; then
    for folder in \
        "Memory/Events" "Memory/Analysis" \
        "Memory/Digests/Daily" "Memory/Digests/Weekly" \
        "Memory/Queries" "Memory/Alerts" \
        "Memory/Sources" "Memory/Concepts" "Memory/Agents"; do
        mkdir -p "${OBSIDIAN_VAULT}/${folder}"
    done
    ok "Obsidian Memory folders created"
else
    warn "Obsidian vault not found at ${OBSIDIAN_VAULT}"
    warn "Set OBSIDIAN_VAULT_PATH in .env to your vault path"
fi

# =============================================================================
# 9. launchd services
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

    sed \
        -e "s|BWS_BIN_PLACEHOLDER|${BWS_BIN}|g" \
        -e "s|VENV_PYTHON_PLACEHOLDER|${VENV_PYTHON}|g" \
        -e "s|PROJECT_DIR_PLACEHOLDER|${PROJECT_DIR}|g" \
        -e "s|BWS_ACCESS_TOKEN_PLACEHOLDER|${BWS_ACCESS_TOKEN:-}|g" \
        "$src" > "$dest"

    launchctl unload "$dest" 2>/dev/null || true
    if launchctl load "$dest" 2>/dev/null; then
        ok "Loaded com.${plist_name}"
    else
        warn "Could not load com.${plist_name} — check logs"
    fi
done

# =============================================================================
# 10. Smoke test
# =============================================================================
log "Running smoke tests..."

"$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" \
    -c "SELECT COUNT(*) FROM agents;" >/dev/null 2>&1 && ok "DB connection OK" || fail "DB connection failed"

"$VENV_PYTHON" -c "from ingestion.base import get_conn; print('OK')" >/dev/null 2>&1 \
    && ok "Python imports OK" || warn "Python import issue — check logs"

sleep 3
if curl -s -f http://localhost:8765/health >/dev/null 2>&1; then
    ok "Ingestion server live at http://localhost:8765"
else
    warn "Server not yet ready — check logs/server-error.log"
fi

# Check secrets status
if [[ -n "${BWS_ACCESS_TOKEN:-}" ]]; then
    log "Checking secrets in Bitwarden..."
    "$VENV_PYTHON" -c "
from secrets.loader import status
s = status()
missing = s['required_missing']
if missing:
    print('  ⚠️  Missing secrets: ' + ', '.join(missing))
    print('  Run: python -m secrets.register --check')
else:
    print('  ✅ All required secrets present in Bitwarden')
" 2>/dev/null || warn "Could not check secrets — run: python -m secrets.register --check"
fi

# =============================================================================
# Done
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  Setup complete!${RESET}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Register secrets in Bitwarden:${RESET}"
echo -e "  python -m secrets.register"
echo ""
echo -e "  ${BOLD}Check secrets status:${RESET}"
echo -e "  python -m secrets.register check"
echo ""
echo -e "  ${BOLD}Test ingestion:${RESET}"
echo -e "  curl http://localhost:8765/health"
echo -e "  curl -X POST http://localhost:8765/ingest/agent \\"
echo -e "       -H 'Content-Type: application/json' \\"
echo -e "       -d '{\"agent\":\"claude\",\"content\":\"Hello!\",\"event_type\":\"message\"}'"
echo ""
echo -e "  ${BOLD}Logs:${RESET}"
echo -e "  tail -f ${PROJECT_DIR}/logs/server.log"
echo ""
