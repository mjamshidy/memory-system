#!/usr/bin/env bash
# =============================================================================
# backup.sh — Manual backup trigger
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

set -a
source "${PROJECT_DIR}/.env" 2>/dev/null || true
set +a

VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
[[ ! -f "$VENV_PYTHON" ]] && VENV_PYTHON="$(which python3)"

cd "$PROJECT_DIR"
"$VENV_PYTHON" -m sync.backup "$@"
