# protoTrader Finance

A **full-bundle [protoAgent](https://github.com/protoLabsAI/protoAgent) plugin** —
natural-language trading *research* in one installable package. It turns a
protoAgent into a quant desk: market data, strategy backtesting, factor /alpha
evaluation, behavioral diagnostics, and gated paper execution — plus a **Quant
Desk dashboard** in the console.

Research-primary. The paper broker is **OFF until a mandate exists**, every order
is HITL-gated, and a kill-switch halts it instantly — distribution never relaxes
that. `mode: live` is deliberately not implemented (this plugin cannot move real
money).

> This is the **standalone, installable** form of protoTrader's finance layer. It
> demonstrates *every* protoAgent contribution type in a single repo (the
> `plugin-devkit` pattern): tools, subagents, workflows, skills, a console view,
> and config/secrets/settings.

## See it running

**[protoTrader](https://github.com/protoLabsAI/protoTrader)** was this plugin's
original host. That fork is now **deprecated** (it diverged from upstream and was
superseded), so treat it as history rather than a starting point — install this
plugin into a current protoAgent instead.

To see the console view with no host at all:

```bash
python scripts/preview.py     # http://127.0.0.1:7899/plugins/prototrader-finance/dashboard
```

## What it contributes

| Surface | What |
|---|---|
| **Tools** (13) | `stock_quote` · `stock_price_history` · `stock_fundamentals` · `crypto_quote` · `crypto_price_history` · `backtest_strategy` · `list_strategies` · `factor_eval` · `factor_zoo` · `analyze_trade_journal` · `broker_place_order` · `broker_orders` · `broker_account` |
| **Subagents** | the research **desk** — `market-analyst`, `quant`, `risk-manager` (the lead delegates via `task`) |
| **Workflows** | `quant-desk` (idea → backtest → risk → go/no-go) · `investment-committee` (bull/bear debate → risk → PM synthesis) |
| **Skills** | `research-a-ticker` · `backtest-a-strategy` · `evaluate-a-factor` · `place-a-paper-trade` · `shadow-account` |
| **Console view** | **Quant Desk** — one rail icon, four panes: Overview (book + market strip), Backtest, Factors, Ledger |
| **Chat command** | `/quant <SYMBOL> [strategy] [period]` — a desk read in chat, user-only (not model-invokable) |
| **Goal verifiers** (5) | `portfolio_return` · `max_drawdown` · `trading_halted` · `data_is_stale` · `factor_alive` |
| **Events** | 7 topics (`backtest_completed`, `order_filled`, `drawdown_breach`, …) other plugins can subscribe to |
| **A2A card skills** | `quant-backtest` (typed output schema) · `market-read` |
| **Config/secrets/settings** | default benchmark · optional market-data key · broker-mandate path (ADR 0019) |

Every seam it uses — and every one it deliberately doesn't, with the reason — is
in **[docs/sdk-parity.md](./docs/sdk-parity.md)**.

## It works offline

Every panel renders with no network. Prices resolve **live → cache → bundled
snapshot**, and `seed/` ships real 5-year daily bars for 26 symbols so a clean
install demos on a plane.

The tier is never hidden: the dashboard shows a provenance chip (`live`,
`cached · 3h old`, `bundled snapshot · 40d old`), and a panel built from many
symbols reports the *weakest* tier that contributed. Serving a months-old
snapshot as if it were this morning's tape would be worse than an error box.

Regenerate the snapshot with `python scripts/refresh_seed.py`.

## Install

Requires a protoAgent host **≥ v0.78.0** (watches + watch hooks, ADR 0067 — the
highest floor among the seams it uses).

```bash
# 1. Fetch the plugin (clones + pins a SHA in plugins.lock; does NOT run code).
python -m server plugin install https://github.com/protoLabsAI/prototrader-finance --ref main

# 2. Install its declared deps (explicit — install never auto-pip-installs).
python -m server plugin install-deps prototrader-finance

# 3. Enable it (this is the trust decision) and restart.
#    Add `prototrader-finance` to plugins.enabled, or:
python -m server plugin enable prototrader-finance
```

Or from the console: **Settings → Plugins → paste the URL → review → install → enable**.

**install ≠ enable ≠ trust.** Installing only fetches code; enabling runs it
in-process with the agent's privileges. Review before enabling. (For *untrusted*
code, use an MCP server instead — sandboxed, out-of-process.)

## Safety — the paper broker

- **OFF by default.** With no `broker_mandate.yaml` (or `enabled: false`) every
  order is refused. Copy `broker/broker_mandate.example.yaml` into the plugin's
  own store (the Ledger tab shows the exact path) and arm it deliberately, or
  point the `broker_mandate_path` setting somewhere else.
- **Gated:** mandate → kill-switch (a `TRADING_HALT` file, honoured in **both**
  the plugin store and the host config dir — a halt file in the "wrong" place
  must still stop trading) → per-order human
  approval → simulated fill → audit log.
- **Paper only.** No live trading path exists.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # 100+ tests: no network, no protoAgent host
ruff check .
```

The suite is **host-free** — `requirements-dev.txt` deliberately excludes
`yfinance`/`ccxt`, and CI asserts that `import graph` fails, so the claim can't
silently rot. It loads the plugin as the same synthetic package the host builds
(`tests/conftest.py`), so relative imports and `register()` are exercised the way
production runs them rather than through a test-only module graph.

## License

MIT — see [LICENSE](./LICENSE).
