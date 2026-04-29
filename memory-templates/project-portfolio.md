---
name: Portfolio project state (autoapp-toolkit user)
description: What project the agent is operating in. Updated each session.
type: project
---

**Project**: <name>
**Phase**: 0 / 1 / 2 / 3 (see state.yml for definitions)

**Identity** (do not duplicate across files; canonical in state.yml):
- Apple Developer ID: see state.yml
- GitHub: see state.yml
- Bundle ID prefix: see state.yml

**Apps in flight**: see `state.yml::apps`
**Candidate apps**: see `state.yml::candidate_apps`
**Hard gates pending**: see `state.yml::hard_gates`

**On re-entry, read this order**:
1. `MEMORY.md` (memory index)
2. `orchestrator/state.yml` (single source of truth)
3. `orchestrator/RESUME.md` (last session's tail)
4. `orchestrator/decisions.md` (full ADR log if needed)

Don't grep the whole codebase first. Memory + state.yml + RESUME tells you where you are.
