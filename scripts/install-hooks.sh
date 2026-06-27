#!/usr/bin/env bash
# Install the LOCAL spec-driven pre-push gate into autoapp app repos.
# Usage: install-hooks.sh [repo-dir ...]    (default: every sibling autoapp-* repo with a spec.json)
set -uo pipefail
TK="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(cd "$TK/.." && pwd)"
targets=("$@")
if [ ${#targets[@]} -eq 0 ]; then
  for d in "$PARENT"/autoapp-*; do [ -f "$d/spec.json" ] && targets+=("$d"); done
fi
for repo in "${targets[@]}"; do
  [ -d "$repo/.git" ] || { echo "[skip] not a git repo: $repo"; continue; }
  cp "$TK/scripts/pre-push" "$repo/.git/hooks/pre-push"
  chmod +x "$repo/.git/hooks/pre-push"
  echo "[ok] pre-push gate installed → $(basename "$repo")"
done
echo "Done. A failing local judge verdict now blocks 'git push' (override: git push --no-verify)."
