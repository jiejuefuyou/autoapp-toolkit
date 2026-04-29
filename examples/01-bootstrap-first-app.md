# Example 1: Bootstrap your first app from scratch

This walks through scaffolding your first iOS app under autoapp-toolkit's autonomy contract. End state: a public GitHub repo with green CI, ready for ASC API key when Apple Developer enrollment finishes.

**Time to complete**: ~1 hour (most of it Apple Developer signup; agent work is ~10 min)

**Prerequisites**: Claude Code (or Cursor) installed; macOS or Windows; `gh` CLI authenticated.

---

## Step 1 — Create the control directory

```sh
mkdir -p ~/myportfolio/{orchestrator,reports,repos,toolkit}
cd ~/myportfolio
git clone https://github.com/jiejuefuyou/autoapp-toolkit.git toolkit
```

## Step 2 — Set up memory + state

```sh
# state.yml — single source of truth
cp toolkit/state.yml.template orchestrator/state.yml

# memory files — agent reads these on every session
PROJECT_ID=$(pwd | tr '/\\:' '-')
mkdir -p ~/.claude/projects/$PROJECT_ID/memory
cp toolkit/memory-templates/MEMORY.md.template ~/.claude/projects/$PROJECT_ID/memory/MEMORY.md
cp toolkit/memory-templates/*.md ~/.claude/projects/$PROJECT_ID/memory/
```

Edit `orchestrator/state.yml`:

```yaml
identity:
  apple_id: your-apple-developer-email@example.com
  github: your-github-username
  bundle_id_prefix: com.yourname

apps:
  - slug: my-first-app
    repo: your-github-username/my-first-app
    bundle_id: com.yourname.myfirstapp
    display_name: MyFirstApp
    pitch: One-liner of what this app does.
    category: Productivity
    monetization: one-time IAP $2.99
    stage: 00_idea
    blocked_by:
      - apple_developer_enrollment_pending
```

Edit `~/.claude/projects/$PROJECT_ID/memory/user-persona.md` to reflect your communication style.

Edit `~/.claude/projects/$PROJECT_ID/memory/feedback-autonomy.md` if your hard-gates differ from the default 4.

## Step 3 — Open Claude Code in this directory

```sh
cd ~/myportfolio
claude
```

The agent's first action on a new session: read `~/.claude/projects/$PROJECT_ID/memory/MEMORY.md`. You don't have to prompt it to do this — it happens automatically.

## Step 4 — Prompt the agent to scaffold

```
Read state.yml. The first app in apps[] is `my-first-app`. Scaffold it:

- Bootstrap from a minimal SwiftUI iOS 17+ template (XcodeGen-based)
- Set up Fastfile + Matchfile + Snapfile for fastlane match signing
- Add .github/workflows/ci.yml with Swift unit tests on macos-15
- Add .github/workflows/testflight.yml triggered on tag v*
- Add .github/workflows/release.yml gated by an explicit "submit-for-review" string input
- Implement a single SwiftUI screen with a placeholder view + a settings sheet
- Add StoreKit 2 boilerplate for one-time non-consumable IAP
- Privacy Manifest with zero data collection declared
- Add scripts/lint-metadata.sh and wire into ci.yml

Push to a new public repo named `my-first-app` under my GitHub username.

Stop only at hard gates per feedback-autonomy.md.
```

The agent will:
1. Create the directory structure
2. Run `gh repo create your-github-username/my-first-app --public --source=. --push`
3. Wait for CI to go green
4. Update state.yml::apps[0].stage to `02_scaffold_done`
5. Append a one-paragraph entry to decisions.md describing the template choices it made
6. Tell you what's left (i.e., Apple Developer enrollment)

## Step 5 — Pre-submission audit

```sh
bash toolkit/scripts/verify_all.sh
```

You should see something like:

```
═══ my-first-app (com.yourname.myfirstapp) ═══
  ✓ project.yml exists
  ✓ ITSAppUsesNonExemptEncryption=NO declared
  ✓ Bundle ID matches expected
  ✓ PrivacyInfo.xcprivacy exists
  ✓ App icon PNG exists
  ✓ ... (~25 more checks)
  ! en-US screenshots present (warn — auto-regenerated)
  ✓ latest CI: completed/success

═══ Summary ═══
  PASS 28   WARN 1   FAIL 0
  ✅ All hard requirements met.
```

The 1 warn is screenshots; they auto-regenerate from UITests later.

## Step 6 — When Apple Developer is approved

You receive an email from Apple. Generate an ASC API key per the [INBOX/00-apple-developer.md](https://github.com/jiejuefuyou/autoapp-toolkit/blob/main/examples/00-apple-developer.md) format. Then:

```sh
export ASC_KEY_ID=ABCD1234
export ASC_ISSUER_ID=12345678-1234-1234-1234-123456789012
export ASC_KEY_FILE=$HOME/AuthKey.p8
export TEAM_ID=ABCD1234
export GH_PAT=ghp_yourpattokenwithreposcope
export FASTLANE_USER=your-apple-id@example.com

bash toolkit/scripts/setup-asc-secrets.sh
```

This populates 8 secrets in your repo's `testflight` environment. Then trigger init_signing:

```sh
gh workflow run init_signing.yml -R your-github-username/my-first-app -f type=appstore
```

After ~10 min, certs and profile are in your `match` storage. Tag a version:

```sh
cd ~/myportfolio/repos/my-first-app
git tag v0.1.0
git push origin v0.1.0
```

`testflight.yml` triggers, builds, signs, uploads. ~12 min later it's on TestFlight.

## Step 7 — TestFlight install + accept

Open TestFlight on your iPhone. Install your app. Run the flows. If broken: tell the agent what's broken; it fixes; tag v0.1.1; cycle repeats.

If working: prompt the agent:

```
TestFlight build accepts. Submit the App Store version for review.
```

The agent runs:

```sh
gh workflow run release.yml -R your-github-username/my-first-app \
  -f confirm=submit-for-review
```

The `confirm` input is intentionally a literal magic string — accident protection on the irreversible action.

Apple reviews. 24-48 hours. App goes live. You earn money.

---

## Common tweaks

**The agent didn't push to the right repo name.**
Edit state.yml::apps[0].repo, then prompt: "state.yml updated; rename the local repo dir + the GitHub remote."

**I want a different IAP price.**
The default is $2.99 (Tier 3). To change: edit the StoreKitConfiguration.storekit displayPrice + ASC will re-validate on submission.

**I want the app rejected.**
Submit a generic, derivative app with no offline-first wedge. Apple will reject under 4.3 (spam). The agent's rejection-response template will write a polite reply but won't fix the fundamental issue. Don't ship apps you wouldn't pay $3 for yourself.

---

## Where to read more

- [README](../README.md) — the operator contract + why the toolkit exists
- [02-multi-app-portfolio.md](02-multi-app-portfolio.md) — scaling to 4+ apps with one agent
- [03-when-to-stop.md](03-when-to-stop.md) — the rejection chain + 30-day data review

If something in this walkthrough fails, [open an issue](https://github.com/jiejuefuyou/autoapp-toolkit/issues). PRs to fix walkthroughs are especially welcome.
