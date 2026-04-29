# Example 2: Scale to a multi-app portfolio with one agent

You shipped one app. It's making some money. You want a second app — and a third, and a fourth — without burning out or losing track. This walkthrough shows how the toolkit makes it sustainable.

**Time investment**: 30-60 min of decisions per new app, with the agent doing the rest.

---

## The trap most indie devs fall into

After the first app:
- App #2 is "easy" — you reuse the build pipeline. Signed and shipped in a week.
- App #3 stalls. You've forgotten which decisions were specific to #1 vs. portable. The agent re-asks "which template should I use?" because it doesn't have your history.
- By #4, you've copy-pasted three different `Fastfile`s, each subtly different. Bugs start showing up that say "wait, why is this here in app #2 but not in #4?"

This is what `decisions.md` exists to prevent. Every non-trivial choice gets one paragraph + a date. When the agent re-enters at app #4, it reads `decisions.md` and inherits the prior. No re-litigation.

---

## When to add a new app

The rule of thumb in `feedback-autonomy.md`: don't scaffold a new app while a previous one is mid-rejection-cycle. App Store Connect's spam-developer heuristic dislikes >1 fresh app from the same account in a 7-day window for new accounts.

Concrete trigger:
- App #N has been on the App Store for 30+ days
- You've reviewed the 30-day data; it's not a disaster
- You have a candidate brief in `reports/concept-*.md` for App #(N+1)

Prompt the agent:

```
state.yml::candidate_apps has 3 candidates. Per reports/aso-baseline-*.md
and the 30-day data on app #N, recommend which to scaffold next.
```

The agent reads the candidate briefs, the ASO baseline, and the recent sales/reviews data, and suggests one. It will explain its reasoning in the chat (and append the rationale to decisions.md if you accept).

---

## Avoid 4.3 (spam developer) flags

Apple's 4.3 heuristic looks for: same UI, same monetization, same description style across apps from one account. Mitigations the toolkit encourages:

1. **Different categories.** AutoChoice = Lifestyle. AltitudeNow = Health & Fitness. DaysUntil = Productivity. PromptVault = Productivity. The repeat is intentional but minor — Productivity has many sub-niches (calendar / to-do / countdown / prompt-manager are different mechanisms).

2. **Different mechanisms.** Each app's primary interaction is different: spin a wheel; read a sensor; manage a list; transform a template. Surface-level UI looks similar (single-screen, list, settings sheet) but mechanism differs.

3. **Different copy tone.** Each fastlane/metadata/en-US/description.txt is rewritten for the new product, not auto-translated from the previous. The agent does this rewrite on scaffold.

4. **Stagger submissions.** Don't submit 4 apps in 7 days from a fresh account. Wait 14+ days between submissions if the account is < 90 days old.

5. **Verifiable privacy posture as a unifier.** All apps share zero-network, zero-analytics, zero-third-party-SDKs. Reviewers see the consistency as a deliberate brand, not as template laziness.

`reports/rejection-response-templates.md` (in the parent reports/ dir) has full templates for each Apple guideline if 4.3 hits anyway.

---

## Cross-cutting changes (the hard part)

When you change something in one app, you usually want it in all the others. Without orchestration, this is where divergence creeps in.

**Example: you discover a Swift 6 strict-concurrency warning in IAPManager.**

Without orchestration:
- You fix it in app #1
- You forget to fix it in apps #2, #3, #4
- Three months later, when Xcode upgrades and the warning becomes an error, you have three broken builds

With autoapp-toolkit:

```
Found a Swift 6 strict-concurrency warning in IAPManager — the listenerTask
needs nonisolated(unsafe) for deinit access. Apply the same fix to the
IAPManager in all 4 product repos. Commit + push each.
```

The agent applies the fix consistently across all 4 repos in one pass. It writes one decisions.md entry — "2026-04-29 — IAPManager `@MainActor` adoption: same fix in all 4 product repos because they all share the same IAP Service template" — that prevents the question from coming up again.

---

## When apps stop deserving the portfolio slot

Hard rule: if an app's 90-day data is below break-even (revenue < $99/year-prorated), it doesn't deserve the maintenance burden of being in the portfolio.

```
Run state.yml audit. Compute 90-day revenue / cost ratio for each app.
If any app shows < $99/year prorated, recommend pulling it from the
active portfolio (move state.yml::apps[i] to state.yml::archived_apps).
```

Apps in `archived_apps` stay on the App Store (you don't pull them — that hurts existing users) but stop receiving update cycles. Their slot in the portfolio opens for App #(N+1).

This decision is yours, not the agent's. The agent recommends; you decide. (Per feedback-autonomy.md: "human decides whether to ship this app at all" — and pulling it from active is the inverse of shipping it.)

---

## When the agent has been in your portfolio for 6 months

Realistically, after 6 months the orchestration layer has accumulated habits.

- decisions.md has 30-50 entries
- state.yml has 4-8 active apps + 5-10 candidates
- The agent has scaffolded ~12 things, fixed bugs across all of them, and built ~4 cross-cutting tools

At this point: review.

**Read decisions.md end-to-end.** Are there patterns of decisions you'd reverse? Do they need to be rewritten into the autonomy contract?

**Rewrite feedback-autonomy.md.** Six months in, you know better what the agent is good at vs. where you have to intervene. Update the gates.

**Prune state.yml.** Apps that aren't earning, candidates that no longer match user demand — archive them.

This is the part nobody says: maintaining an autonomous agent's working environment is itself a job. It's a much smaller job than maintaining 8 codebases by hand, but it's a real one.

---

## When you give up on the experiment

If after 6-12 months the portfolio isn't profitable + the agent isn't saving you time:

1. Open-source everything. The toolkit is already MIT; open-source the apps too.
2. Write the post-mortem on Substack. Real numbers. What worked. What didn't.
3. Archive the GitHub repos with a final commit pinning the source state.

The post-mortem is itself accumulated value. Other people building toward this pattern will learn from your specific failures.

---

## See also

- [01-bootstrap-first-app.md](01-bootstrap-first-app.md) — start here if you don't have any app yet
- [03-when-to-stop.md](03-when-to-stop.md) — the rejection chain protocol
- [README](../README.md) — toolkit overview
