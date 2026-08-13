#!/usr/bin/env bash
# Run the Mac-native read-only ASC release-state probe with no third-party dependency.
set -euo pipefail

TOOLKIT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$TOOLKIT_ROOT/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "ASC monitor requires python3 (Xcode Command Line Tools provides it on macOS)." >&2
  exit 1
fi

exec "$PYTHON_BIN" -X utf8 "$TOOLKIT_ROOT/scripts/asc_monitor.py" "$@"
