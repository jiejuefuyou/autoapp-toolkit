#!/usr/bin/env bash
# asc-monitor.sh — run the Mac-native ASC state probe via the toolkit venv.
#   bash autoapp-toolkit/scripts/asc-monitor.sh [--app NAME]
set -euo pipefail
TK="$(cd "$(dirname "$0")/.." && pwd)"
PY="$TK/.venv/bin/python"
[ -x "$PY" ] || { echo "venv missing: $PY — run: python3 -m venv $TK/.venv && $TK/.venv/bin/pip install 'pyjwt[crypto]'" >&2; exit 1; }
exec "$PY" "$TK/scripts/asc_monitor.py" "$@"
