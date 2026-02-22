#!/usr/bin/env bash
# =============================================================================
# teardown.sh — Clean uninstall of memory-system launchd services
# Does NOT delete the database or data files.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'

log()  { echo -e "\033[0;34m[teardown]\033[0m $*"; }
ok()   { echo -e "${GREEN}  ✅ $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️  $*${RESET}"; }

LAUNCHD_DIR="${HOME}/Library/LaunchAgents"

SERVICES=(
    "com.memory.server"
    "com.memory.backup"
    "com.memory.sync"
    "com.memory.digest"
    "com.memory.analysis"
)

log "Unloading launchd services..."
for svc in "${SERVICES[@]}"; do
    plist="${LAUNCHD_DIR}/${svc}.plist"
    if [[ -f "$plist" ]]; then
        launchctl unload "$plist" 2>/dev/null && ok "Unloaded ${svc}" || warn "Could not unload ${svc}"
        rm -f "$plist"
        ok "Removed ${plist}"
    else
        warn "${plist} not found — skipping"
    fi
done

echo ""
warn "Database and data files were NOT deleted."
warn "To fully remove: dropdb memory_system"
echo ""
ok "Teardown complete."
