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
case "$MODE" in
  --fast|--full) ;;
  *) echo "unsupported mode: $MODE (expected --fast or --full)"; exit 2 ;;
esac
# --fast runs only the behavioral-oracle suite, so whole-app coverage isn't meaningful
# (the coverage gate belongs to --full, which runs the entire test target). Default
# accordingly; an explicit floor arg still overrides.
if [ "$MODE" = "--full" ]; then FLOOR="${3:-0.55}"; else FLOOR="${3:-0}"; fi
TK="$(cd "$(dirname "$0")/.." && pwd)"     # toolkit root (judge.py lives here)
REPO="$(pwd)"                               # MUST be invoked from the app repo root
# Keep the fast gate on the newest installed portfolio simulator, while the full
# StoreKit transaction oracle runs on the known-good iOS 18.4 runtime. Xcode
# 26.6 + iOS 26.5 can load the StoreKit catalogue but never deliver the
# Ask-to-Buy response, leaving xcodebuild hung indefinitely. Both destinations
# remain explicit and overrideable for future runtime qualification.
if [ "$MODE" = "--full" ]; then
  SIM="${IOS_FULL_SIMULATOR:-iPhone 16 Pro}"
  SIM_RUNTIME="${IOS_FULL_RUNTIME:-18.4}"
else
  SIM="${IOS_QUICK_SIMULATOR:-iPhone 17 Pro}"
  SIM_RUNTIME="${IOS_QUICK_RUNTIME:-}"
fi
SIM_UDID="$(xcrun simctl list devices available -j | python3 -c '
import json
import sys

name, runtime = sys.argv[1:]
devices = json.load(sys.stdin).get("devices", {})
matches = []
for runtime_key, entries in devices.items():
    if runtime and not runtime_key.endswith("iOS-" + runtime.replace(".", "-")):
        continue
    matches.extend(
        item["udid"] for item in entries
        if item.get("isAvailable") and item.get("name") == name
    )
if len(matches) != 1:
    print(
        f"expected exactly one available simulator name={name!r} runtime={runtime or 'any'!r}; found {len(matches)}",
        file=sys.stderr,
    )
    raise SystemExit(2)
print(matches[0])
' "$SIM" "$SIM_RUNTIME")" || exit $?
[ -n "$SIM_UDID" ] || { echo "failed to resolve simulator: $SIM ($SIM_RUNTIME)"; exit 2; }
DEST="platform=iOS Simulator,id=$SIM_UDID"
XCTEST_TIMEOUT_ARGS=()
if [ "$MODE" = "--full" ]; then
  # A single Apple-framework regression must produce a bounded red receipt,
  # never consume an entire local/launchd run.
  XCTEST_TIMEOUT_ARGS=(
    -test-timeouts-enabled YES
    -default-test-execution-time-allowance 60
    -maximum-test-execution-time-allowance 120
  )
fi
V="$REPO/.verify"; rm -rf "$V"; mkdir -p "$V"
DD="$V/DerivedData"
say(){ printf '\n\033[1m[verify:%s] %s\033[0m\n' "$APP" "$*"; }
[ -f "$REPO/spec.json" ] || { echo "no spec.json in $REPO"; exit 2; }
REDUCER="$(python3 -c 'import json;print(json.load(open("spec.json"))["core_loop"]["reducer"])' 2>/dev/null)"
ORACLE="${REDUCER}ModelTests"

# 1) single-source lints (carried in each app's scripts/)
say "1. lint"
if [ -f scripts/lint_modal_env.py ]; then
  python3 scripts/lint_modal_env.py "$APP" || exit $?
fi
if [ -f scripts/lint_paywall_loadstate.py ]; then
  python3 scripts/lint_paywall_loadstate.py "$APP" --selftest || exit $?
fi

# 2) build + test -> .xcresult  (fast = oracle suite only, avoids the slow/flaky StoreKit units)
say "2. build + test ($MODE) — $SIM ${SIM_RUNTIME:+iOS $SIM_RUNTIME }[$SIM_UDID]"
xcodegen generate >/dev/null 2>&1
xcrun simctl boot "$SIM_UDID" >/dev/null 2>&1 || true
RB="$V/result.xcresult"
ONLY=(-only-testing:"${APP}Tests/${ORACLE}")
[ "$MODE" = "--full" ] && ONLY=()
xcodebuild test -scheme "$APP" ${ONLY[@]+"${ONLY[@]}"} -destination "$DEST" \
  ${XCTEST_TIMEOUT_ARGS[@]+"${XCTEST_TIMEOUT_ARGS[@]}"} \
  -derivedDataPath "$DD" \
  -resultBundlePath "$RB" -enableCodeCoverage YES CODE_SIGNING_ALLOWED=NO \
  >"$V/build.log" 2>&1 || say "xcodebuild test returned non-zero (judge inspects the .xcresult)"

# 3) e2e_sim (full only) — local Maestro, no runner
MA=()
if [ "$MODE" = "--full" ] && [ -d maestro ]; then
  say "3. maestro e2e"
  # Install the product built by THIS invocation. Searching global DerivedData
  # made the old gate nondeterministic: `find | head -1` could install a stale
  # build from another runtime/repository clone.
  APPBIN="$DD/Build/Products/Debug-iphonesimulator/$APP.app"
  [ -d "$APPBIN" ] || { echo "built simulator app not found: $APPBIN" >&2; exit 2; }
  BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APPBIN/Info.plist" 2>/dev/null)"
  [ -n "$BUNDLE_ID" ] || { echo "CFBundleIdentifier missing from $APPBIN" >&2; exit 2; }
  if xcrun simctl get_app_container "$SIM_UDID" "$BUNDLE_ID" app >/dev/null 2>&1; then
    xcrun simctl uninstall "$SIM_UDID" "$BUNDLE_ID" >/dev/null 2>&1 || exit $?
  fi
  xcrun simctl install "$SIM_UDID" "$APPBIN" >/dev/null 2>&1 || exit $?
  mkdir -p "$V/maestro"
  if maestro test maestro/ --udid "$SIM_UDID" \
      --format junit --output "$V/maestro/report.xml" \
      --debug-output "$V/maestro/debug" --flatten-debug-output \
      >"$V/maestro.log" 2>&1
  then
    :
  else
    say "maestro test returned non-zero (judge inspects the JUnit report)"
  fi
  MA=(--maestro "$V/maestro" --flows maestro/)
fi

# 4) judge -> verdict.json  (the deterministic gate; identical judge.py as the cloud version used)
say "4. judge"
SK="$(find . -name '*.storekit' -not -path './.verify/*' 2>/dev/null | head -1)"
NOCOV=""; [ "$MODE" = "--fast" ] && NOCOV="--no-coverage"   # single-suite coverage isn't meaningful
JUDGE_SPEC="$REPO/spec.json"
if [ "$MODE" = "--fast" ]; then
  JUDGE_SPEC="$V/fast-spec.json"
  if ! python3 - "$REPO/spec.json" "$JUDGE_SPEC" "$ORACLE" <<'PY'
import json
import sys
from pathlib import Path

source, target, oracle = map(Path, sys.argv[1:])
spec = json.loads(source.read_text(encoding="utf-8"))
spec["required_suites"] = [str(oracle)]
spec["coverage_floor"] = 0
target.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
PY
  then
    echo "failed to derive the fast-mode judge spec" >&2
    exit 2
  fi
fi
python3 "$TK/scripts/judge.py" --spec "$JUDGE_SPEC" --xcresult "$RB" \
  ${SK:+--storekit "$SK"} --coverage-floor "$FLOOR" $NOCOV ${MA[@]+"${MA[@]}"} --out "$V/verdict.json"
EC=$?
echo; cat "$V/verdict.json" 2>/dev/null; echo
[ $EC -eq 0 ] && say "VERDICT: pass ✅" || say "VERDICT: fail ❌ (push blocked)"
exit $EC
