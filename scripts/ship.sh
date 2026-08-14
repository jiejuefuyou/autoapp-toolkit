#!/usr/bin/env bash
# ship.sh — LOCAL release. The on-Mac replacement for testflight.yml (NO macOS Actions runner:
# a local Apple-Silicon Mac IS the better runner — no sim-runtime pinning, no 30-min job cap).
# Usage (from the app repo):  bash <toolkit>/scripts/ship.sh <AppName> <version>
#   1) full local gate (oracle + unit/UI + Maestro + judge)
#   2) local fastlane beta: match (autoapp-certs) -> build_app -> upload_to_testflight
set -uo pipefail
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.maestro/bin:/usr/bin:/bin:/usr/sbin:/sbin"
APP="${1:?usage: ship.sh <AppName> <version>}"; VER="${2:?version e.g. 1.0.0}"
TK="$(cd "$(dirname "$0")/.." && pwd)"

echo "== ship $APP $VER (local, no Actions) =="
echo "1) full local gate"
bash "$TK/scripts/verify.sh" "$APP" --full || { echo "❌ gate failed — not shipping"; exit 1; }

echo "2) local fastlane beta"
command -v fastlane >/dev/null || { echo "fastlane missing (brew install fastlane)"; exit 1; }
# Requires ASC env in the shell: ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_FILE/CONTENT, MATCH_PASSWORD.
# These are the SAME secrets the cloud testflight.yml consumed — here they live in your env/keychain.
: "${ASC_KEY_ID:?set ASC_KEY_ID}"; : "${MATCH_PASSWORD:?set MATCH_PASSWORD}"
bundle exec fastlane beta
echo "== upload completed; TestFlight tester/group/device install still unproven =="
echo "Run asc_testflight_readiness.py before any TestFlight install attempt."
