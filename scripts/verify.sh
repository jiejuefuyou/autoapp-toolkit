#!/usr/bin/env bash
# verify.sh — the LOCAL deterministic gate. On-Mac replacement for ci-reusable.yml.
# The AWS "implementation pipeline" (lint -> build/test -> e2e_sim -> judge) run as ONE
# local pass in the app's working tree. Exit 0 iff the judge verdict == pass.
#
#   Run from inside an app repo:  bash <toolkit>/scripts/verify.sh <AppName> [--fast|--full] [floor]
#   --fast (default): lints + the behavioral-oracle suite + judge        (~seconds; the pre-push gate)
#   --full          : + full unit/UI tests + Maestro core_loop/purchase  (~minutes; pre-tag / pre-ship)
#
# Self-contained PATH: git hooks run in a minimal env, so we hard-set the toolchain.
set -uo pipefail
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.maestro/bin:/usr/bin:/bin:/usr/sbin:/sbin"
JH="$(/usr/bin/find "$HOME/.local" -maxdepth 3 -type d -name Home -path '*jdk-21*' 2>/dev/null | head -1)"
[ -n "$JH" ] && { export JAVA_HOME="$JH"; export PATH="$JH/bin:$PATH"; }
export MAESTRO_CLI_NO_ANALYTICS=1

APP="${1:?usage: verify.sh <AppName> [--fast|--full] [coverage_floor]}"
MODE="${2:---fast}"
# --fast runs only the behavioral-oracle suite, so whole-app coverage isn't meaningful
# (the coverage gate belongs to --full, which runs the entire test target). Default
# accordingly; an explicit floor arg still overrides.
if [ "$MODE" = "--full" ]; then FLOOR="${3:-0.55}"; else FLOOR="${3:-0}"; fi
TK="$(cd "$(dirname "$0")/.." && pwd)"     # toolkit root (judge.py lives here)
REPO="$(pwd)"                               # MUST be invoked from the app repo root
DEST="platform=iOS Simulator,name=iPhone 17 Pro"; SIM="iPhone 17 Pro"
V="$REPO/.verify"; rm -rf "$V"; mkdir -p "$V"
say(){ printf '\n\033[1m[verify:%s] %s\033[0m\n' "$APP" "$*"; }
[ -f "$REPO/spec.json" ] || { echo "no spec.json in $REPO"; exit 2; }
REDUCER="$(python3 -c 'import json;print(json.load(open("spec.json"))["core_loop"]["reducer"])' 2>/dev/null)"
ORACLE="${REDUCER}ModelTests"

# 1) single-source lints (carried in each app's scripts/)
say "1. lint"
[ -f scripts/lint_modal_env.py ] && python3 scripts/lint_modal_env.py "$APP" || true
[ -f scripts/lint_paywall_loadstate.py ] && python3 scripts/lint_paywall_loadstate.py "$APP" --selftest || true

# 2) build + test -> .xcresult  (fast = oracle suite only, avoids the slow/flaky StoreKit units)
say "2. build + test ($MODE)"
xcodegen generate >/dev/null 2>&1
xcrun simctl boot "$SIM" >/dev/null 2>&1 || true
RB="$V/result.xcresult"
ONLY=(-only-testing:"${APP}Tests/${ORACLE}")
[ "$MODE" = "--full" ] && ONLY=()
xcodebuild test -scheme "$APP" ${ONLY[@]+"${ONLY[@]}"} -destination "$DEST" \
  -resultBundlePath "$RB" -enableCodeCoverage YES CODE_SIGNING_ALLOWED=NO \
  >"$V/build.log" 2>&1 || say "xcodebuild test returned non-zero (judge inspects the .xcresult)"

# 3) e2e_sim (full only) — local Maestro, no runner
MA=()
if [ "$MODE" = "--full" ] && [ -d maestro ]; then
  say "3. maestro e2e"
  APPBIN="$(find "$HOME/Library/Developer/Xcode/DerivedData" -type d -name "$APP.app" -path '*Debug-iphonesimulator*' 2>/dev/null | head -1)"
  [ -n "$APPBIN" ] && xcrun simctl install booted "$APPBIN" >/dev/null 2>&1
  mkdir -p "$V/maestro"
  maestro test maestro/ --format junit --output "$V/maestro/report.xml" >"$V/maestro.log" 2>&1 || true
  MA=(--maestro "$V/maestro" --flows maestro/)
fi

# 4) judge -> verdict.json  (the deterministic gate; identical judge.py as the cloud version used)
say "4. judge"
SK="$(find . -name '*.storekit' -not -path './.verify/*' 2>/dev/null | head -1)"
NOCOV=""; [ "$MODE" = "--fast" ] && NOCOV="--no-coverage"   # single-suite coverage isn't meaningful
python3 "$TK/scripts/judge.py" --spec spec.json --xcresult "$RB" \
  ${SK:+--storekit "$SK"} --coverage-floor "$FLOOR" $NOCOV ${MA[@]+"${MA[@]}"} --out "$V/verdict.json"
EC=$?
echo; cat "$V/verdict.json" 2>/dev/null; echo
[ $EC -eq 0 ] && say "VERDICT: pass ✅" || say "VERDICT: fail ❌ (push blocked)"
exit $EC
