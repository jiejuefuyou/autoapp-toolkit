# B2B Quiz Schema — Fork This For Your Own Service Business

> Part of [autoapp-toolkit](https://github.com/jiejuefuyou/autoapp-toolkit) | Live demo: https://jiejuefuyou.github.io/b2b-quiz.html

## Overview

A 5-question branching quiz that routes takers into 3 tiers. Tier A gets a paid intake recommendation. Tier B gets a self-serve product recommendation. Tier C gets a free checklist. The goal is qualification-first, not conversion-first.

**Key design principle**: Route ≥60% of takers to "don't buy from me." High-quality Tier A leads convert at 3-5x the rate of unqualified cold calls.

---

## Quiz Flow

```
Q1: How many AI tools does your team use repeatedly each week?
  0-2 tools     → 0 pts
  3-5 tools     → 1 pt
  6+ tools     → 2 pts

Q2: What is your company's current AI tool maturity level?
  Not sure how → 0 pts
  Some tools   → 1 pt
  Systematic   → 2 pts

Q3: How much time does your team spend on repetitive tasks per week?
  <1 hr        → 0 pts
  1-3 hrs      → 1 pt
  3+ hrs       → 2 pts

Q4: When AI can save 50-70% of your time, what is your biggest concern?
  Wrong output  → 0 pts
  Can't verify  → 1 pt
  Can't calc ROI → 2 pts

Q5: What is your company's AI maturity stage?
  Solo, AI assists only  → 0 pts
  Small team, systematic → 1 pt
  Already structured     → 2 pts

Total: 0-10 pts

Tier A: 0-3 pts  (buy prompts / early stage)
Tier B: 4-6 pts  (needs coaching / mid stage)
Tier C: 7-10 pts (needs retainer / advanced)
```

---

## How to Fork For Your Service

### Step 1: Copy `b2b-quiz.html`

Edit the `QUESTIONS` array and `TIERS` array in the `<script>` section.

### Step 2: Customize Questions

Each question needs:
- `text`: the question text
- `options[]`: answer choices with `label` (display text) and `value` (0/1/2 points)

### Step 3: Customize Tiers

Each tier needs:
- `min` / `max`: point range
- `tier`: display name (e.g. "Tier A")
- `title`: result title
- `cssClass`: CSS class for color coding
- `scoreText`: e.g. "Your score: 0-3 / 10"
- `desc`: paragraph description
- `nextItems[]`: array of next-step suggestions
- `cta`: button text
- `ctaHref`: link (mailto: or URL)

### Step 4: Customize CTAs

Replace Gumroad links and mailto: addresses with your own.

### Step 5: Host

GitHub Pages (free) or any static host. No backend required.

---

## Tech Notes

- Pure HTML + vanilla JS (~14 KB total page weight)
- No framework, no build step, no analytics
- State serialized in URL params (results are shareable)
- Social share buttons built-in (X/Twitter, LinkedIn, copy to clipboard)
- Results breakdown grid shows all 3 tier options after completion
- Mobile-responsive, dark theme matching the jiejuefuyou site aesthetic

---

## License

MIT. If you fork it, please keep the footer attribution link. That's the only request.
