---
name: Autonomy gates — what the agent can / cannot decide alone
description: Without explicit gates the agent over-asks on small things and under-asks on irreversible ones. Define the gates here.
type: feedback
---

**Rule**: in autonomous-portfolio mode, the agent makes 100% of intermediate decisions (selection, monetization tier, technical, iteration) without asking. The agent stops ONLY at:

1. **Identity / payment / banking / tax info** — only the human has these
2. **First-time submission of any app to App Store Review** — irreversible-ish; needs human ack
3. **Major irreversible operations** — deleting accounts, force-pushing to main, deleting branches, multi-account scaling
4. **Same app rejected ≥ 3 times** — autonomy budget exhausted; human reviews the rejection chain and decides

**Why**: <write your reasoning here. Concrete is better than abstract — link to a specific past incident if available.>

**How to apply**:
- When the agent has a plan, it executes; it does not check in mid-execution
- Decisions + rationale are written to `decisions.md` (post-hoc audit trail), not pre-asked
- At hard gates: agent stops, writes a clear "here's what I need from you" message, and yields to the human
- Doesn't apply to ordinary coding sessions — only to the autonomous-portfolio mode
