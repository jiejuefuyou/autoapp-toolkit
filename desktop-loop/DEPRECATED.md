# ⛔ DEPRECATED — do not use / do not revive

This `desktop-loop/` is the **old** 24/7 driver: a PyAutoGUI script (`autoapp_loop.py`) that
clicks the Claude Code input box in VSCode every minute to "keep the loop alive". It is
**abandoned** and intentionally not wired:

- GUI-coupled and fragile (depends on window coordinates, screen-lock state, idle detection).
- Visibly flashes the screen; needs a no-popup gate that never fully worked.
- Its `cron-prompt.txt` doesn't even exist anymore.

## Use instead — the native, headless replacement

| old (here) | new (canonical) |
|---|---|
| `autoapp_loop.py` (PyAutoGUI clicks) | `../scripts/autofix-loop.sh` (headless `claude -p`) |
| `com.autoapp.loop.plist.template` | `../launchd/com.autoapp.autofix.plist` |

Loop = per app `verify.sh --fast` → on fail `claude -p` minimal fix → re-judge, ≤3x →
escalate to `INBOX/AUTOFIX-ESCALATE-<app>.md`. Fired by a launchd LaunchAgent (native macOS
scheduler, survives session close). Design: `autoapp-workspace/direction/SPEC_DRIVEN_DEVLOOP_MAC.md` §6.

Note: Claude Code `/schedule` (cloud routine) can't drive local Xcode, so it can't host this
build loop — it's only for the API-only ASC monitoring layer.

_Marked DEPRECATED 2026-06-29._
