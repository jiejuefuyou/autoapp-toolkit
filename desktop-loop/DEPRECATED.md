# DEPRECATED — do not use or revive

`desktop-loop/` is the retired PyAutoGUI driver that clicked the Claude Code UI on a
timer. It is retained only for historical reference and is not an active automation
entry point.

Current Mac development uses each app repository's deterministic `verify.sh`,
pre-push gate, and explicit `ship.sh` flow. Cross-machine ownership and current entry
points are defined by `dev-workspace/WORKSPACE.md`; do not enable the old scheduled
desktop loop.

The unpublished June 2026 automation experiments were preserved without promotion on
the Git branch `archive/mac-wip-20260630`.
