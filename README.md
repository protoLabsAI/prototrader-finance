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

## See it running — a working finance agent

Want a complete, working example of an agent built around this plugin?
**[protoTrader](https://github.com/protoLabsAI/protoTrader)** is a natural-language
trading research agent that installs this plugin as its finance layer — it's the
reference host. It consumes this repo exactly the way you would (`plugin install`
+ a pinned `plugins.lock`), enables it, and ships the surrounding agent (the A2A
server, the React console the Quant Desk view renders in, persona, evals, the
release pipeline). Read it to see how a finance agent is wired end to end, or fork
it as a starting point.

## What it contributes

| Surface | What |
|---|---|
| **Tools** (13) | `stock_quote` · `stock_price_history` · `stock_fundamentals` · `crypto_quote` · `crypto_price_history` · `backtest_strategy` · `list_strategies` · `factor_eval` · `factor_zoo` · `analyze_trade_journal` · `broker_place_order` · `broker_orders` · `broker_account` |
| **Subagents** | the research **desk** — `market-analyst`, `quant`, `risk-manager` (the lead agent delegates via `task`) |
| **Workflows** | `quant-desk` (idea → backtest → risk → go/no-go) · `investment-committee` (bull/bear debate → risk → PM synthesis) |
| **Skills** | `research-a-ticker` · `backtest-a-strategy` · `evaluate-a-factor` · `place-a-paper-trade` · `shadow-account` |
| **Console view** | **Quant Desk** — a left-rail dashboard that backtests a strategy and charts its equity curve vs buy-and-hold (ADR 0026) |
| **Config/secrets/settings** | default benchmark · optional market-data key · broker-mandate path (ADR 0019) |

## Install

Requires a protoAgent host **≥ v0.21.0** (ADR 0026 console views + ADR 0027
git-URL install).

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

## The Quant Desk dashboard

Once enabled, a **Quant Desk** icon appears in the console's left rail. It serves
a self-contained page (no build step — vanilla JS + inline SVG) that calls the
plugin's own backtest API and renders the strategy vs buy-and-hold equity curve
plus CAGR / Sharpe / max-drawdown. It uses the ADR 0026 `postMessage` handshake to
receive the console's bearer token + theme tokens, so it's authenticated and
on-brand without a token in the URL.

## Safety — the paper broker

- **OFF by default.** With no `broker_mandate.yaml` (or `enabled: false`) every
  order is refused. Copy `broker/broker_mandate.example.yaml` into the host config
  dir and arm it deliberately.
- **Gated:** mandate → kill-switch (`TRADING_HALT` file) → per-order human
  approval → simulated fill → audit log.
- **Paper only.** No live trading path exists.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q          # offline engine tests — no network, no host framework
```

The `tests/` here cover the pure compute (backtest / broker / factor / behavioral
engines, loaded by path). The host-integration tests (loading through the
protoAgent plugin loader) live in the protoAgent host / the protoTrader fork.

## License

MIT — see [LICENSE](./LICENSE).
