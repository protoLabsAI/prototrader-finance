---
name: backtest-a-strategy
description: >-
  Use this whenever the operator wants to test a trading idea on history — e.g.
  "backtest a 20/50 moving-average cross on SPY", "does RSI mean-reversion work
  on AAPL", "how would a breakout strategy have done on BTC". Drives a
  specify → backtest → benchmark → check-robustness → honest verdict loop.
tools: [backtest_strategy, list_strategies, stock_price_history, crypto_price_history, web_search, memory_recall, current_time]
---

# Backtest a strategy

Turn a trading idea into an honest, reproducible result. **The job is not to find
a pretty curve — it's to tell the operator whether an edge is real.**

## 1. Specify
Pin down: instrument (ticker or `BASE/USDT`), strategy + parameters, period,
bar interval, and assumed costs. If the operator was vague, pick sensible defaults
and *state them*. Use `list_strategies` if unsure what's available.

## 2. Backtest
`backtest_strategy(symbol, strategy, params, period, cost_bps, slippage_bps)`.
Always include realistic frictions — a frictionless backtest is a fantasy.

## 3. Benchmark & read the result
The tool already reports **strategy vs buy-and-hold** and **in-sample vs
out-of-sample**. Read them honestly:
- Did it actually **beat buy-and-hold**? A strategy that trails a do-nothing
  benchmark is not an edge, however good its Sharpe looks.
- Is the **OOS Sharpe** close to in-sample? A big drop = overfit → say so.
- Is the **bootstrap Sharpe CI** comfortably above 0, or does it straddle 0?
  Few trades + a wide CI = not significant, regardless of the point estimate.

## 4. Robustness (when it matters)
For a result the operator might act on, don't stop at one run:
- Vary the parameters a little — does the edge survive, or is it a single lucky
  setting (curve-fit)?
- Try a different period / a second instrument.
- Watch trade count: < ~30 trades → treat any conclusion as tentative.

## 5. Verdict
Lead with a one-line verdict: **edge / no edge / inconclusive** — and *why*, in
the numbers. Then the metrics table, the assumptions, and what would make you more
confident (more data, more trades, OOS holding up).

## Rules
- No look-ahead, no frictionless fantasies, no cherry-picked period. The tool
  enforces the first two; you enforce the last.
- A backtest is evidence about the *past*. Say so. This is analysis, not advice,
  and not a prediction.
- If the operator has a strategy or risk preference on file, `memory_recall` it.
