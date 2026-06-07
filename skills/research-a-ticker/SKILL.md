---
name: research-a-ticker
description: >-
  Use this whenever the operator asks you to research, analyze, or give a read on
  a specific stock, ETF, or crypto — e.g. "what's the setup on NVDA", "should I be
  watching SPY", "analyze BTC", "bull/bear case for AAPL". Drives a
  quote → trend → fundamentals/context → news → structured read with risks.
tools: [stock_quote, stock_price_history, stock_fundamentals, crypto_quote, crypto_price_history, web_search, fetch_url, memory_recall, memory_ingest, current_time]
---

# Research a ticker

A disciplined read on one instrument. **Analysis, not advice** — surface the
setup, the cases, the levels, and the risks; the operator decides.

## 0. Identify the instrument
Stock/ETF (e.g. `NVDA`, `SPY`) → use the `stock_*` tools. Crypto pair
(e.g. `BTC/USDT`) → use the `crypto_*` tools (default exchange `okx`). If the
operator gives a bare crypto name ("bitcoin"), map it to a `BASE/USDT` pair.

## 1. Price & trend (always)
`stock_quote` / `crypto_quote` for the current level, day & 52-week range.
`stock_price_history` / `crypto_price_history` for trend (default 6mo/1d):
note the return over the window, the high/low, and whether price is near the top
or bottom of its range. Don't invent indicators you didn't compute.

## 2. Context
- **Equities:** `stock_fundamentals` — valuation (P/E, P/B), margins, growth, sector.
  Is it cheap/expensive *relative to its own history and sector*? State the basis.
- **Crypto:** there are no fundamentals — lean on trend, volatility, and `web_search`
  for catalysts (protocol news, flows, regulation).

## 3. News & catalysts
`web_search` (and `fetch_url` to read a source) for recent, dated catalysts —
earnings, guidance, upgrades/downgrades, macro, protocol/regulatory events. Cite
sources with dates. Skip stale or unsourced rumor.

## 4. Synthesize — the read
Lead with a one-line **read** (constructive / neutral / cautious — *not* "buy/sell").
Then:
- **Setup:** where price sits in its range + trend, with the numbers.
- **Bull case / Bear case:** 2–3 bullets each, each tied to something you fetched.
- **Key levels:** support/resistance from the range (52w low/high, recent swing).
- **Risks:** what breaks the thesis (valuation, regime, liquidity, event risk).
- **What I'd watch:** the data point or level that would change the read.

## Rules
- Every claim ties to a tool result or a cited source. No hindsight, no made-up numbers.
- State assumptions (period, what "cheap" is relative to). A number without its basis is noise.
- Round sensibly. Flag thin data (illiquid name, short history) explicitly.
- If the operator has told you their holdings / risk tolerance, `memory_recall` it and
  tailor the read; if they share a new standing preference, `memory_ingest` it (hot).
- Close by reminding that this is analysis, not personalized investment advice.
