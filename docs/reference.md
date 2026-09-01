# Reference

Everything this plugin exposes, with the shapes. The README is the map; this is the
detail. **A test (`tests/test_docs.py`) checks every list here against what
`register()` actually contributes** — because this repo has already shipped a doc
that claimed a capability with no code behind it.

## Tools (13)

Bound to the agent when the plugin is enabled. All return formatted text.

### Market data

| Tool | Arguments | Returns |
|---|---|---|
| `stock_quote` | `symbol` | Last price, day/52w range, market cap, volume |
| `stock_price_history` | `symbol`, `period`, `interval` | OHLCV summary with trend and volatility |
| `stock_fundamentals` | `symbol` | Valuation, margins, growth, balance-sheet basics |
| `crypto_quote` | `symbol` (e.g. `BTC/USDT`), `exchange` | Last price, 24h change and volume |
| `crypto_price_history` | `symbol`, `timeframe`, `limit`, `exchange` | OHLCV summary |

### Research

| Tool | Arguments | Returns |
|---|---|---|
| `backtest_strategy` | `symbol`, `strategy`, `period`, `params` | CAGR, Sharpe (with a bootstrap CI), max drawdown, trades, exposure, vs buy-and-hold |
| `list_strategies` | — | The four built-ins: `ma_cross`, `rsi_meanrev`, `breakout`, `buy_hold` |
| `factor_eval` | `factor`, `universe`, `period` | Information coefficient, rank IC, IR, hit rate, verdict |
| `factor_zoo` | `universe`, `period` | Every factor ranked by \|IR\| |
| `analyze_trade_journal` | `csv_text` | Round-trip stats and behavioural flags from a fills CSV |

### Paper broker (gated)

| Tool | Arguments | Returns |
|---|---|---|
| `broker_place_order` | `symbol`, `side`, `qty`, `order_type` | Refuses unless a mandate is armed; otherwise pauses for human approval, then a simulated fill |
| `broker_orders` | — | Recent orders from the audit ledger |
| `broker_account` | — | Cash, positions marked to market, exposure, and the gate/kill-switch state |

Backtests are net of 5bps cost and 2bps slippage on turnover, and signals are
computed only on data available at the time — no look-ahead.

## Events (7)

Auto-namespaced `prototrader-finance.*`. Subscribe by topic; never import this
plugin from another one.

| Topic | Payload | Emitted when |
|---|---|---|
| `backtest_completed` | `{symbol, strategy, period, sharpe, source}` | A backtest finishes (dashboard or `/quant`) |
| `factor_study_completed` | `{period, universe_size, best_factor, best_ic}` | The factor zoo finishes |
| `order_filled` | `{symbol, side, qty, price, notional}` | A paper order fills. `price` is the FILL price, not the requested one |
| `mandate_armed` | `{enabled, mode}` | The gate's armed state *changes*. Not emitted on first observation — that's state, not a transition |
| `trading_halted` | `{watch}` | The kill-switch tripwire fires |
| `drawdown_breach` | `{watch}` | The drawdown tripwire fires |
| `data_refreshed` | `{reason, symbols}` | A lifecycle warm refreshed prices. `reason` is `app_loaded` or `system_wake` |

`source` on `backtest_completed` is the data tier — `live`, `cache` or `seed`.

**Subscribed** (own bus): `order_filled` and `mandate_armed` invalidate the
dashboard's memoized gate, so a change made through a *tool* shows up in the view.

## Goal verifiers (5)

Referenced as `{"type": "plugin", "check": "prototrader-finance:<name>", "args": {…}}`.
`args` are declarative data, validated by the verifier — no shell, no eval.

| Verifier | Args (defaults) | Met when |
|---|---|---|
| `portfolio_return` | `min_return: 0.10` | The real book's total return reaches the target |
| `max_drawdown` | `limit: 0.15` | Drawdown from the high-water mark **exceeds** the limit — a tripwire, so "met" is the bad outcome |
| `trading_halted` | — | The kill-switch is engaged |
| `data_is_stale` | `max_age_h: 48`, `symbol: "SPY"` | The freshest bars are older than the limit |
| `factor_alive` | `factor: "momentum_12_1"`, `min_ic: 0.03`, `period: "3y"` | The factor's IC still clears the threshold |

`portfolio_return` and `max_drawdown` **refuse the bundled sample book** and report
why. Grading a return goal against a book that ships already up 5.6% would mark it
achieved before a single order was placed.

The drawdown high-water mark comes from the `sdk.record_metric` equity series, so
it needs recorded history — a fresh install has none, and the verifier says so
rather than inventing a peak.

## Settings (ADR 0019)

Console → **Settings → protoTrader Finance**. Section `prototrader_finance`.

| Key | Type | Default | What |
|---|---|---|---|
| `default_benchmark` | string | `SPY` | Benchmark for the dashboard and backtests |
| `market_data_api_key` | secret | — | Optional premium provider key; blank uses the keyless path |
| `broker_mandate_path` | string | — | Mandate file. Blank uses the plugin's own store — the Ledger tab shows the exact path |

Routers read these through `live_config()`, so an edit takes effect without a restart.

## HTTP routes

| Route | Auth | What |
|---|---|---|
| `GET /plugins/prototrader-finance/dashboard` | public | The view page. Chrome only — no data (an iframe navigation carries no bearer) |
| `GET /api/plugins/prototrader-finance/universe` | bearer | Seeded symbols + snapshot provenance |
| `GET …/strategies` | bearer | Strategy names + the configured default symbol |
| `GET …/overview` | bearer | Book, positions, market strip, equity history |
| `GET …/backtest?symbol=&strategy=&period=` | bearer | Equity curves + metrics |
| `GET …/factors?period=` | bearer | The factor zoo |
| `GET …/ledger` | bearer | Gate, mandate, positions, fills |
| `POST /api/config/test-prototrader_finance` | bearer | ADR 0029 "Test connection" — reports which data tier is reachable |

Every data route takes `?refresh=1` to force a live fetch. Without it a request is
served from cache or the bundled snapshot and **never touches the network** — see
[operating.md](./operating.md#data-freshness).

Failures return `{"ok": false, "error": "…"}` at HTTP **200**, so one panel degrades
to a readable message instead of the whole surface going blank. A missing dependency
additionally sets `"needs_deps": true`.

## A2A card skills

Advertised on the agent card to peer agents.

- **`quant-backtest`** — typed: declares `output_schema` + `result_mime`, so the
  executor's structured finalizer enforces JSON a caller can parse.
- **`market-read`** — a sourced read on one instrument. Evidence, not a
  recommendation.

## Subagents, skills, workflows

**Subagents** (`task` delegation): `market-analyst`, `quant`, `risk-manager`.

**Skills** (auto-loaded `SKILL.md`, reach chat *and* `task()` *and* scheduled turns):
`research-a-ticker`, `backtest-a-strategy`, `evaluate-a-factor`, `place-a-paper-trade`,
`shadow-account`.

**Workflows**: `quant-desk` (idea → backtest → risk → go/no-go) and
`investment-committee` (bull/bear debate → risk → PM synthesis).

## Chat command

`/quant <SYMBOL> [strategy] [period]` — a desk read in chat. Registered as a chat
command, not a tool, because that seam is deliberately **not model-invokable**: the
agent already has the same engines as tools, so this is the operator's shortcut.
