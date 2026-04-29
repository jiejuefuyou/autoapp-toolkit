# Contributing

Welcome. This is a small toolkit; contribution should be small too.

## What's in scope

The toolkit's job is to **increase agent autonomy by reducing context-recreation cost**. Anything that makes that better fits.

Examples:
- New memory templates for use cases I haven't covered (mobile games, tvOS apps, etc.)
- New scripts that automate steps the agent currently asks the human about
- Better defaults in `state.yml.template` for common starting points
- Bug fixes in any of the existing scripts

## What's not in scope

- iOS app templates (use existing community starters; the toolkit wraps templates, doesn't be one)
- Marketing automation (the platforms charge / break TOS / shouldn't be automated)
- Per-user customization (config goes in `state.yml`, not in code)
- Integration with specific commercial AI products beyond Claude Code / Cursor (we keep it agent-neutral)

## How to propose a change

1. Open an issue first describing the change. Even small additions deserve a 2-line discussion to make sure scope alignment.
2. If maintainer agrees: PR.
3. PR description should explain:
   - What problem this solves (concrete user pain)
   - Why it belongs in the toolkit vs. in user-specific config
   - Any breaking changes for existing users

## Style

- Shell scripts: bash 4+, set -euo pipefail, comments at the top
- Markdown: keep it terse; the README is dense for a reason
- No emoji in code or commit messages
- Commit messages: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`)

## What you'll get

- Public credit (CHANGELOG mention)
- A line in the maintainer's annual "open-source thanks" Substack issue if your contribution shipped
- For substantial work: a co-maintainer invite if you want it

## Decision-making

Maintainer (jiejuefuyou) is the BDFL until contribution volume warrants splitting. The decisions.md pattern in this toolkit is itself how I make decisions; expect ADR-style writeups for non-trivial changes.

## A specific ask

If you fork this toolkit to run your own portfolio:
- Add yourself to a `users.md` file (PR welcome) so other forkers can find peer-validated examples
- If your portfolio's first month makes money, consider writing a blog post about it. The maintainer will link to it. It's a slow-loop way of building this community.
