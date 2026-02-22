#!/usr/bin/env bash
# =============================================================================
# status.sh — Quick health check of the memory system
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

set -a
source "${PROJECT_DIR}/.env" 2>/dev/null || true
set +a

PG_DB="${PG_DB:-memory_system}"
PG_USER="${PG_USER:-$(whoami)}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PSQL="/usr/local/opt/postgresql@18/bin/psql"
[[ ! -f "$PSQL" ]] && PSQL="$(which psql 2>/dev/null)"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Memory System Status"
echo "═══════════════════════════════════════════════════"
echo ""

# PostgreSQL
echo "📦 PostgreSQL"
if "$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -c "SELECT 1" >/dev/null 2>&1; then
    TOTAL=$("$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -t -c "SELECT COUNT(*) FROM memory_log;" 2>/dev/null | tr -d ' ')
    UNPROC=$("$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -t -c "SELECT COUNT(*) FROM memory_log WHERE processed = FALSE;" 2>/dev/null | tr -d ' ')
    GISTS=$("$PSQL" -U "$PG_USER" -h "$PG_HOST" -p "$PG_PORT" -d "$PG_DB" -t -c "SELECT COUNT(*) FROM gists;" 2>/dev/null | tr -d ' ')
    echo "  ✅ Connected to ${PG_DB}@${PG_HOST}:${PG_PORT}"
    echo "  Total events:       ${TOTAL}"
    echo "  Unprocessed events: ${UNPROC}"
    echo "  Gists generated:    ${GISTS}"
else
    echo "  ❌ Cannot connect to ${PG_DB}@${PG_HOST}:${PG_PORT}"
fi

echo ""

# Ingestion server
echo "🌐 Ingestion Server (port 8765)"
if curl -s -f http://localhost:8765/health >/dev/null 2>&1; then
    HEALTH=$(curl -s http://localhost:8765/health 2>/dev/null)
    echo "  ✅ Running — ${HEALTH}"
else
    echo "  ❌ Not responding"
fi

echo ""

# launchd services
echo "⚙️  LaunchD Services"
SERVICES=(
    "com.memory.server"
    "com.memory.backup"
    "com.memory.sync"
    "com.memory.digest"
    "com.memory.analysis"
)
for svc in "${SERVICES[@]}"; do
    if launchctl list "$svc" >/dev/null 2>&1; then
        PID=$(launchctl list "$svc" 2>/dev/null | grep -E '"PID"' | grep -oE '[0-9]+' || echo "-")
        echo "  ✅ ${svc} (PID: ${PID})"
    else
        echo "  ❌ ${svc} — not loaded"
    fi
done

echo ""

# iCloud backup
echo "☁️  iCloud Backups"
ICLOUD_ROOT="${HOME}/Library/Mobile Documents/com~apple~CloudDocs"
BACKUP_DIR="${ICLOUD_BACKUP_PATH:-${ICLOUD_ROOT}/Database Backups/memory-system}"
if [[ -d "$BACKUP_DIR" ]]; then
    LATEST=$(ls -t "${BACKUP_DIR}"/*.sql.gz 2>/dev/null | head -1 || echo "none")
    COUNT=$(ls "${BACKUP_DIR}"/*.sql.gz 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    echo "  ✅ Backup dir: ${BACKUP_DIR}"
    echo "  Backups found: ${COUNT}"
    echo "  Latest:        $(basename "${LATEST}")"
else
    echo "  ❌ Backup dir not found: ${BACKUP_DIR}"
fi

echo ""

# Recent logs
echo "📋 Recent Logs (last 5 lines each)"
for log_file in server analysis backup sync; do
    lf="${PROJECT_DIR}/logs/${log_file}.log"
    if [[ -f "$lf" ]]; then
        echo ""
        echo "  --- ${log_file}.log ---"
        tail -5 "$lf" | sed 's/^/    /'
    fi
done

echo ""
echo "═══════════════════════════════════════════════════"
