---
name: evaluate-a-factor
description: >-
  Use this when the operator asks whether a factor / signal predicts returns —
  e.g. "does momentum work", "is low-vol still an edge", "rank these factors",
  "what's working in the market right now". Drives an evaluate → read-IC →
  context → honest verdict loop over the Alpha Zoo.
tools: [factor_eval, factor_zoo, stock_price_history, web_search, memory_recall, current_time]
---

# Evaluate a factor

Test whether a factor actually predicts cross-sectional returns — by its
**Information Coefficient**, not by story.

## 1. Scope
One factor → `factor_eval(factor, universe, period)`. "What's working / rank
them" → `factor_zoo(universe, period)`. Use the operator's universe if they have
one (a watchlist, a sector); else the default large-cap set. State the universe +
period — IC is **sample-specific**.

## 2. Read the IC honestly
- **mean IC** ~0.03+ with **hit rate** > 55% = a real, positive edge (**alive**).
- **rank-IC** corroborates (robust to outliers); a big gap from IC = a few names
  driving it.
- **IR** (= mean IC / std × √rebalances) is *consistency* — a factor with IC 0.05
  but IR 0.3 is erratic. Prefer IR for ranking.
- **reversed** (negative consistent IC) is a finding, not a failure — it says the
  *opposite* of the factor worked in this regime.

## 3. Context — why
A factor's IC is regime-dependent. When low-vol "reverses," it usually means a
high-beta/risk-on regime (e.g. an AI mega-cap melt-up). Use `web_search` to name
the regime, and say it: *"low-vol reversed because high-beta led this period."*
Don't present a sample IC as a timeless law.

## 4. Verdict
Lead with the factor's status (**alive / weak / reversed / dead**) + the number,
then *why* (regime), then the caveat: this is one universe + one window; a factor
alive here can be dead next year. Analysis, not advice — and definitely not a
guarantee the factor keeps working.

## Rules
- Always state the universe + period. Never imply an IC is universal.
- Small/erratic IC + few rebalances → call it inconclusive, not an edge.
- A reversed factor is information about the regime — surface it, don't bury it.
