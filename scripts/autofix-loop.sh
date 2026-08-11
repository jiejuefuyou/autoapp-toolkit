#!/usr/bin/env bash
# autofix-loop.sh — the 24/7 unmanned "AI fix -> deterministic judge -> retry" arm.
#
# This is the NATIVE replacement for the old pixel-clicking desktop-loop (DEPRECATED).
# It runs headless on this Mac (the only place the Xcode/simulator gate can run) and is
# meant to be fired by a launchd LaunchAgent (desktop-loop/com.autoapp.autofix.plist),
# which survives Claude Code sessions being closed — no GUI, no screen poking.
#
# Per app:  verify.sh --fast  ->  (fail)  claude -p <bounded fix>  ->  re-verify  ... <=3x
#           pass within budget -> commit locally (push only if AUTOFIX_PUSH=1)
#           still failing       -> write INBOX/AUTOFIX-ESCALATE-<app>.md, move on
#
# The deterministic judge (judge.py) is the gate; the AI never edits the spec/oracle to
# go green — only the implementation. Authority = each app's spec.json (INV-1).
#
# Env:
#   AUTOFIX_MAX_ATTEMPTS  fix attempts per app before escalating   (default 3)
#   AUTOFIX_PUSH=1        git push green commits (triggers TestFlight); default OFF
#   AUTOFIX_ONLY=<scheme> restrict the sweep to one app (debug)
set -uo pipefail
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.maestro/bin:/usr/bin:/bin:/usr/sbin:/sbin"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"        # .../repos  (toolkit's parent)
TK="$ROOT/autoapp-toolkit/scripts"
INBOX="$ROOT/autoapp-workspace/INBOX"
MAX="${AUTOFIX_MAX_ATTEMPTS:-3}"
LOG="$ROOT/autoapp-toolkit/logs/autofix.log"
mkdir -p "$(dirname "$LOG")"

# repo:scheme — the 8 apps under repos/
APPS=(
  "autoapp-altitude-now:AltitudeNow"
  "autoapp-days-until:DaysUntil"
  "autoapp-focusflow:FocusFlow"
  "autoapp-habithash:HabitHash"
  "autoapp-hello:AutoChoice"
  "autoapp-prompt-vault:PromptVault"
  "autoapp-tipjar-now:TipJarNow"
  "autoapp-waternow:WaterNow"
)

log(){ printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" | tee -a "$LOG"; }

# Hard prerequisite: the headless Claude binary. Fail loud (dogfood the missing-binary case)
# instead of silently doing nothing every minute.
if ! command -v claude >/dev/null 2>&1; then
  log "ABORT: 'claude' CLI not on PATH — install Claude Code CLI before enabling this loop."
  exit 3
fi

mkdir -p "$INBOX"

fix_prompt(){ # $1=scheme  $2=reducer
  cat <<EOF
You are fixing ONE iOS app so its behavioral-oracle gate passes. The deterministic judge
already ran and wrote .verify/verdict.json (verdict=fail) from .verify/result.xcresult;
the xcodebuild output is at .verify/build.log.

Authority = spec.json (the single source of truth). The failing oracle is the test suite
${2}ModelTests, which checks the real reducer ${2} against a hand-written reference model.

Make the MINIMAL, surgical change to the IMPLEMENTATION so the oracle passes:
- Never weaken, delete, or edit the oracle test or spec.json to go green.
- Read build.log + verdict.json to find the actual failure, then fix the reducer/view.
- Do NOT run tests, simctl, or any git command — the driver re-verifies after you finish.
- Touch only files needed for this fix.
EOF
}

overall_fail=0
for entry in "${APPS[@]}"; do
  repo="${entry%%:*}"; scheme="${entry##*:}"
  [ -n "${AUTOFIX_ONLY:-}" ] && [ "$AUTOFIX_ONLY" != "$scheme" ] && continue
  dir="$ROOT/$repo"
  [ -d "$dir" ] || { log "$scheme: SKIP (repo dir missing)"; continue; }
  cd "$dir" || continue

  # gate once
  if bash "$TK/verify.sh" "$scheme" --fast >/dev/null 2>&1; then
    log "$scheme: green (no fix needed)"; continue
  fi

  reducer="$(python3 -c 'import json;print(json.load(open("spec.json"))["core_loop"]["reducer"])' 2>/dev/null || echo "$scheme")"
  fixed=0
  for attempt in $(seq 1 "$MAX"); do
    log "$scheme: fail -> claude fix attempt $attempt/$MAX"
    claude -p "$(fix_prompt "$scheme" "$reducer")" >>"$LOG" 2>&1 || log "$scheme: claude exited non-zero (re-verifying anyway)"
    if bash "$TK/verify.sh" "$scheme" --fast >/dev/null 2>&1; then
      log "$scheme: GREEN after $attempt attempt(s)"
      git add -A && git commit -m "autofix: ${scheme} oracle green (attempt ${attempt})" >>"$LOG" 2>&1 || true
      if [ "${AUTOFIX_PUSH:-0}" = "1" ]; then
        log "$scheme: AUTOFIX_PUSH=1 -> git push"
        git push >>"$LOG" 2>&1 || log "$scheme: push failed (see log)"
      fi
      fixed=1; break
    fi
  done

  if [ "$fixed" -ne 1 ]; then
    overall_fail=1
    esc="$INBOX/AUTOFIX-ESCALATE-${scheme}.md"
    {
      echo "# AUTOFIX ESCALATION — ${scheme}"
      echo "_$(date '+%Y-%m-%d %H:%M:%S')_ — gate still fails after ${MAX} AI attempts. Human needed."
      echo
      echo "## verdict.json"; echo '```json'; cat "$dir/.verify/verdict.json" 2>/dev/null; echo '```'
      echo "## build.log (tail)"; echo '```'; tail -40 "$dir/.verify/build.log" 2>/dev/null; echo '```'
    } > "$esc"
    log "$scheme: ESCALATED -> $esc"
  fi
done

exit "$overall_fail"
