# autoapp-toolkit

> The orchestration layer that lets one Claude Code agent ship and operate a multi-app iOS portfolio without losing context across sessions.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-blueviolet)](https://claude.com/claude-code)

This is the **scaffolding around the AI**, not the AI itself. It's the memory + state.yml + ADR + cross-repo verifier that turns "I prompted Claude to build an app" into "an autonomous agent reliably maintains a 4-app portfolio over months."

Built and battle-tested running [@jiejuefuyou's AutoApp portfolio](https://github.com/jiejuefuyou) — four iOS apps shipped end-to-end by one Claude Code agent under shared autonomy rules.

---

## Why this exists

A single Claude Code session has a 5-hour rolling token window. Without persistent state, the agent burns half its context every session re-deriving "where was I?" The other half then has to ship code — which means the agent ships less, slower, and starts over often.

The fix isn't a smarter model. It's an orchestration layer with five concrete artifacts:

| Artifact | What it solves |
|---|---|
| **`MEMORY.md` + memory entries** | Re-entry context. Agent reads this first, doesn't re-grep the whole repo. |
| **`state.yml`** | Single source of truth across N product repos. CI status, hard gates, candidate apps, current phase. |
| **`decisions.md`** (append-only ADR log) | Stops the agent from re-litigating settled choices. Every non-trivial decision gets one paragraph + a date. |
| **`verify_all.sh`** | One command runs across all your repos and checks ~30 ASC hard requirements (Privacy Manifest, encryption flag, metadata length, latest CI status, etc.). Catches drift before submission, not at fastlane-fail-time. |
| **`token_economy.md`** | Three-tier operating mode — Active / Conservative / Recovery. Tells the agent what to do when token budget is tight, and how to schedule itself to resume after the limit resets. |

The pattern is simple: **make the agent re-entrant by externalizing memory**. The model is fungible; the orchestration is not.

---

## What's in this repo

```
scripts/
  setup-asc-secrets.sh    — One command: populate 8 ASC secrets × N repos in the testflight environment
  verify_all.sh           — Cross-repo: 30+ ASC hard-requirement checks before submission
  asc_sales_report.sh     — Pull daily ASC sales TSV via API + parse into per-app units/proceeds
  asc_reviews_check.sh    — Incremental: snapshot + diff to see only NEW reviews
  dday_runbook.sh         — Launch-day: input slug + Store URL → output platform-specific posts (Reddit/HN/PH/小红书/即刻/Twitter) timed by timezone
  lint-metadata.sh        — Per-repo: validate ASC field-length limits before fastlane deliver
  init-new-app.sh         — Bootstrap a new app repo from one of your existing apps as a template (rename, sed, init git)

memory-templates/
  user-persona.md         — How the agent talks to you. Inject into subagent prompts to prevent style drift.
  feedback-autonomy.md    — Hard gates: what the agent CAN'T decide alone (your identity, payment, App Store submit).
  project-portfolio.md    — The "where am I" entry point.
  token-economy.md        — Active / Conservative / Recovery operating modes + rolling-window protocol.
  MEMORY.md.template      — The index that lives at ~/.claude/projects/<id>/memory/MEMORY.md

desktop-loop/
  autoapp_loop.py         — PyAutoGUI-based "body" for the agent. Solves Claude Code's hard limit:
                            CronCreate jobs are session-only and die when VSCode pauses. This script
                            runs via Windows Task Scheduler, simulates click+paste+Enter into the CC
                            input box. Idle-protected (silent skip when you're at the keyboard).
                            One-second pause via `pause.lock` file. 24/7 uses your Pro subscription
                            tokens, NOT the API.
  SETUP.md                — Coordinate measurement + Task Scheduler registration walkthrough.
  config.json.template    — Per-user config (CC input box xy, idle threshold, fire interval).

b2b-quiz/
  b2b-quiz.html           — Self-contained 5-question B2B qualification quiz.
                            3 tiers: early/mid/advanced. Routes 60%+ to "don't buy from me."
                            Pure HTML + vanilla JS, ~14 KB, no backend, no analytics.
                            MIT license.
  QUIZ-SCHEMA.md          — Branching logic docs + how-to-fork guide for your own service biz.

state.yml.template        — Single source of truth template. Copy + fill identity + apps.
```

What's **not** included (each user provides their own):
- App source code (you build the apps)
- ASC API keys (you generate; tooling consumes them safely)
- Signing certs (use [fastlane match](https://docs.fastlane.tools/actions/match/) with your own private cert repo)

---

## Quick start

### 1. Bootstrap your control directory

```sh
mkdir -p ~/myportfolio/{orchestrator,reports,repos}
cd ~/myportfolio
git clone https://github.com/jiejuefuyou/autoapp-toolkit.git toolkit

cp toolkit/state.yml.template orchestrator/state.yml
cp toolkit/memory-templates/*.md orchestrator/
# Edit state.yml: fill identity + first app block
```

### 2. Set up Claude Code memory layer

```sh
# Find your project's memory dir (Claude Code creates this automatically when you cd into a folder)
# It's typically ~/.claude/projects/<encoded-path>/memory/

mkdir -p ~/.claude/projects/$(pwd | tr '/\\:' '-')/memory
cp toolkit/memory-templates/MEMORY.md.template ~/.claude/projects/$(pwd | tr '/\\:' '-')/memory/MEMORY.md
cp toolkit/memory-templates/user-persona.md ~/.claude/projects/$(pwd | tr '/\\:' '-')/memory/
cp toolkit/memory-templates/feedback-autonomy.md ~/.claude/projects/$(pwd | tr '/\\:' '-')/memory/
cp toolkit/memory-templates/project-portfolio.md ~/.claude/projects/$(pwd | tr '/\\:' '-')/memory/
cp toolkit/memory-templates/token-economy.md ~/.claude/projects/$(pwd | tr '/\\:' '-')/memory/
```

Edit each memory file to reflect **your** preferences. The defaults are calibrated for a senior dev who hates filler and wants root-cause-first answers.

### 3. Have the agent scaffold your first app

In a Claude Code session, prompt:

> Read MEMORY.md, state.yml, and the autoapp-toolkit README. Then scaffold my first app per state.yml. Bootstrap from a public iOS template, set up XcodeGen project.yml, fastlane Fastfile, GitHub Actions on macos-15. Commit + push. Stop only at hard gates.

The agent will commit, push, and add follow-up tasks to its own backlog. You don't ask "what now?" — `state.yml` and `decisions.md` tell the agent itself what's next.

### 4. When Apple Developer is approved

```sh
# After your Apple Developer enrollment + ASC API key generation:
export ASC_KEY_ID=ABCD1234
export ASC_ISSUER_ID=12345678-1234-1234-1234-123456789012
export ASC_KEY_FILE=$HOME/AuthKey.p8
export TEAM_ID=ABCD1234
export GH_PAT=ghp_yourpattokenwithreposcope
export FASTLANE_USER=your-apple-id@example.com

bash toolkit/scripts/setup-asc-secrets.sh
```

This populates 8 secrets in each repo's `testflight` environment. After it completes, trigger `init_signing.yml` once per repo, then tag `v0.1.0` and you're on TestFlight in ~12 minutes.

### 5. Pre-submission audit

```sh
bash toolkit/scripts/verify_all.sh
```

Runs ~30 hard checks across all your repos:
- `INFOPLIST_KEY_ITSAppUsesNonExemptEncryption=NO` declared (avoids per-archive encryption prompt)
- Privacy Manifest exists
- Bundle ID matches state.yml
- en-US metadata: `name.txt`, `subtitle.txt`, `description.txt`, `keywords.txt` (length ≤ 100)
- All required fastlane workflow files exist
- Latest CI run is green

Catches drift before fastlane deliver does. ~5 seconds per audit, vs. 12 minutes per failed CI run.

### 6. Launch day

```sh
bash toolkit/scripts/dday_runbook.sh autochoice "https://apps.apple.com/app/autochoice/id1234567890"
```

Generates a single markdown file with:
- Recommended posting schedule by timezone (北京时间 9:00 → r/iOSProgramming, 10:00 → HN, 12:00 → PH, etc.)
- Pre-replaced platform-specific copy (English + 中文 if you have both templates)
- Post-launch 24h / 7d / 30d checklists

---

## The autonomy contract

The toolkit assumes a specific contract between you and the agent. Spelled out in `memory-templates/feedback-autonomy.md`:

**The agent decides**:
- All small implementation choices (variable names, file structure, error handling, refactor-vs-leave)
- Which apps to scaffold (with your opt-in to "go" on each)
- Per-app brand identity (icons, palette, copy tone)
- When to fix root-cause vs. just patch (it must fix root-cause if it sees one)

**The agent stops at**:
1. Identity / payment / banking / tax info — only you have these
2. First-time submission of any app — irreversible-ish, needs explicit ack
3. Major irreversible operations (force push, delete branches, multi-account scaling)
4. Any single app rejected ≥ 3 times

This isn't safety theater. It's where autonomy stops mattering and human judgment starts.

---

## What's deliberately not here

- **No agent runtime.** This isn't an agent framework. The toolkit assumes Claude Code (or Cursor, or any LLM-driven IDE that respects memory + state files). It externalizes state so the agent stays effective; the agent itself you bring.
- **No app templates.** Six different "free SwiftUI starter" repos already exist. The toolkit is what wraps them, not what they are.
- **No marketing automation.** `dday_runbook.sh` generates posts; you copy-paste them. Posting via official APIs costs money or violates ToS for all major platforms (HN/Reddit/PH); manual posting is the safe path.
- **No analytics SDK integration.** The portfolio that built this toolkit ships zero analytics. Your data is what you can pull from ASC's API.

---

## Battle-tested reference portfolio

Built and operated with this toolkit:

- [AutoChoice](https://github.com/jiejuefuyou/autoapp-hello) — decision wheel
- [AltitudeNow](https://github.com/jiejuefuyou/autoapp-altitude-now) — barometric altimeter
- [DaysUntil](https://github.com/jiejuefuyou/autoapp-days-until) — quiet countdown
- [PromptVault](https://github.com/jiejuefuyou/autoapp-prompt-vault) — offline AI prompt manager

All four pass `verify_all.sh` and are awaiting their first signed TestFlight build.

---



---

## Lessons learned (2026-05 update)

### `fastlane match` on macos-15 + GitHub Actions: use SSH deploy keys, not PATs

Fine-grained GitHub PATs fail unreliably on `macos-15` GitHub Actions runners
when used as basic-auth in HTTPS git URLs for `fastlane match`. The exact
failure: `git clone https://user:token@github.com/...` returns HTTP 400 from
runner, but works locally with the same PAT. We burned 9 fix iterations
(rotated PAT, x-access-token user, disable osxkeychain, unset extraheader,
explicit URL injection, etc.) before pivoting to SSH deploy keys.

The fix takes 5 minutes:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/match_deploy -N "" -C "fastlane-match-deploy"
PUB=$(cat ~/.ssh/match_deploy.pub)
gh api -X POST repos/myorg/match-storage/keys   -f title='fastlane-match-deploy' -F read_only=false -f key="$PUB"
SSH_B64=$(base64 -w 0 ~/.ssh/match_deploy)
echo "$SSH_B64" | gh secret set MATCH_SSH_KEY -R myorg/app1 --env testflight
# (repeat for each consuming repo)
```

Then in your `init_signing.yml`:

```yaml
- name: Setup SSH deploy key
  env:
    MATCH_SSH_KEY: ${{ secrets.MATCH_SSH_KEY }}
  run: |
    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    echo "$MATCH_SSH_KEY" | base64 -d > ~/.ssh/match_deploy
    chmod 600 ~/.ssh/match_deploy
    ssh-keyscan github.com >> ~/.ssh/known_hosts
    cat > ~/.ssh/config <<EOF
    Host github.com
      User git
      IdentityFile ~/.ssh/match_deploy
      IdentitiesOnly yes
    EOF

- name: fastlane match
  env:
    MATCH_GIT_URL: git@github.com:myorg/match-storage.git
    MATCH_GIT_BRANCH: main
  run: bundle exec fastlane init_signing
```

Full debugging journey + 4 bonus issues (cert ceiling, missing actions,
profile name with timestamp suffix, iPad orientation requirement) at
[autoapp/reports/devto-article-14-asc-ssh-deploy-key-paste-ready.md](https://github.com/jiejuefuyou/autoapp/blob/main/reports/devto-article-14-asc-ssh-deploy-key-paste-ready.md).

### Use `gh api` to do everything Apple's docs say "go to ASC web UI"

Almost all "click in ASC dashboard" steps have a GitHub or ASC API equivalent.
We've automated:
- Adding deploy keys to private repos (`gh api`)
- Creating internal beta groups (`v1/betaGroups` POST)
- Adding internal testers + linking builds (`v1/betaTesters` + `relationships/individualTesters`)
- Setting Test Information (`v1/betaAppLocalizations`)
- Setting content rights declaration (`PATCH apps/{id}`)

Reusable script: [autoapp/orchestrator/setup_asc_internal_tester.py](https://github.com/jiejuefuyou/autoapp/blob/main/orchestrator/setup_asc_internal_tester.py) — one command wires the entire TestFlight chain for any new app.

The principle: **if Apple's docs say "click here in ASC", check the ASC API
first**. 90% of the time there's an endpoint, and 10% of the time there isn't
but `gh api` handles the GitHub side regardless.


## License

MIT. Fork, modify, ship your own portfolio. If it's useful, link back to this repo so others find it.

## Contributing

Issues + PRs welcome. The toolkit is intentionally small — if you want to add a feature, ask first whether it belongs here or in the agent's prompt.

The maintainer's guideline: **anything that increases agent autonomy by reducing context-recreation cost** is in scope. Anything else probably isn't.
