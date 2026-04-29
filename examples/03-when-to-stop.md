# Example 3: The rejection chain — when to stop trying

Apple rejects an app. The agent autonomously tries to fix it under feedback-autonomy.md's clause:

> 4. Same app rejected ≥ 3 times — autonomy budget exhausted; human reviews the rejection chain and decides

This walkthrough is what happens between attempt #1 and the moment you decide whether to keep iterating or pull the app.

---

## Attempt #1 — the agent reads the rejection

When ASC sends a rejection, the message goes to the agent's monitor:

```sh
bash toolkit/scripts/asc_reviews_check.sh
# → Detects new entry in app's review history; flags as rejection
```

The agent reads the rejection text, classifies it against `reports/rejection-response-templates.md`, and proposes a fix. Common categories:

| Apple Guideline | Agent's first move |
|---|---|
| **4.0 Design (minimum functionality)** | Adds a "Tips & Examples" sub-screen with 8-10 concrete usage scenarios |
| **3.1.1 In-App Purchase** | Verifies StoreKit 2 product config; adds explicit error reporting if `Product.products(for:)` returns empty |
| **5.1.1 Privacy: Data Collection** | Re-runs `nm -gU <app>` to verify no networking symbols; updates ASC App Privacy questionnaire if mismatched |
| **2.1 Performance: App Completeness** | Asks Apple for the specific device + iOS version where the issue reproduced; cannot reproduce without that detail |

The agent makes the fix, tags `v0.1.1`, re-submits. Each step gets a one-line entry in `decisions.md`.

---

## Attempt #2 — same guideline, different fix depth

If Apple rejects again under the same guideline:

The agent's rejection-response-templates says "upgrade fix depth." Examples:
- 4.0 second rejection → add an actual demo video to ASC review notes (you record it; the agent writes the script)
- 3.1.1 second rejection → add a Sandbox-tester credential explicitly in review notes
- 5.1.1 second rejection → fix the manifest if it's actually wrong; otherwise escalate

The agent re-submits. State.yml gets an annotation:

```yaml
apps:
  - slug: my-app
    rejection_count: 2
    rejection_chain:
      - 2026-05-15: Guideline 4.0 — minimum functionality (added Tips section)
      - 2026-05-22: Guideline 4.0 — still flagged, added demo video
```

---

## Attempt #3 — the autonomy gate

If Apple rejects a third time on the same guideline, the agent **stops**.

It writes a diagnostic report to `reports/rejection-diagnosis-{slug}-{date}.md` containing:

1. Rejection text (verbatim across all 3 attempts)
2. The fixes attempted, in order
3. The agent's read on whether the issue is solvable within the current product
4. Recommendations: either (a) fundamental scope change, or (b) pull the app and write the autopsy

Then it pings you:

> "App `<slug>` has been rejected 3 times on Guideline 4.0. The fixes I tried weren't enough. The diagnostic is at `reports/rejection-diagnosis-...`. Per feedback-autonomy.md, I'm stopping; you decide whether to scope-change or pull."

This is the right shape. The agent isn't giving up at the first failure (autonomy works). The agent isn't iterating forever on a fundamentally-flawed product (autonomy ends at #3). The decision to scope-change vs. pull is yours.

---

## Scope-change vs. pull — your decision

**Scope-change**: keep the slug + repo, but rewrite the product to address the structural rejection cause. Example: a single-screen utility flagged as 4.0 → add a real second feature (not just "Tips"). This is essentially a v0.2.0 with a different product brief.

**Pull**: archive the repo. Write the autopsy. Move the candidate apps from `state.yml::candidate_apps` up the priority ladder.

How to decide:
- If you can articulate _why_ the rejection cause is solvable with a 1-2 week scope-change, do that.
- If you've spent 3 attempts and the rejection feels structural, pull it. The opportunity cost of fixing the wrong app is higher than the cost of writing it off.

---

## Writing the autopsy

If you pull, the autopsy goes in `reports/postmortem-{slug}-{date}.md`. The agent writes the first draft based on `decisions.md` + the rejection chain + 30-day data (if any). You edit.

The autopsy is shareable. Substack readers love post-mortems. The next person attempting a similar app benefits from your data. This is itself accumulated value, even when the app is dead.

Public post-mortem template:

```markdown
# {App name} — public autopsy

## What it was
{one paragraph}

## Why I thought it would work
{decision excerpt from decisions.md}

## What actually happened
- {rejection chain summary}
- {30-day data if applicable}

## What I'd do differently
{2-3 lessons that translate to other apps}

## Code is open source at {repo URL}
```

---

## When the autopsy is wrong

Sometimes you write the autopsy, archive the repo, and 6 months later realize the lesson was wrong. That's fine. Update the autopsy with a "Update 6mo later" section.

Honest learning is more valuable than performative closure. The point of writing it down is to remember the wrong thing you believed in 2026 — so 2027-you doesn't repeat it.

---

## See also

- [01-bootstrap-first-app.md](01-bootstrap-first-app.md) — start
- [02-multi-app-portfolio.md](02-multi-app-portfolio.md) — scale
- `reports/rejection-response-templates.md` (in your portfolio's reports dir, not in this toolkit) — per-guideline templates the agent reads on rejection
