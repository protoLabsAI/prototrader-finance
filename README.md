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

**Docs:** [reference.md](./docs/reference.md) (tools, event payloads, verifier
args, settings, routes) · [operating.md](./docs/operating.md) (arming the broker,
data freshness, troubleshooting) · [sdk-parity.md](./docs/sdk-parity.md) (every
SDK seam used, and every one deliberately skipped, with the reason).

## It works offline

Every panel renders with no network. Prices resolve **live → cache → bundled
snapshot**, and `seed/` ships real 5-year daily bars for 26 symbols so a clean
install demos on a plane.

The tier is never hidden: the dashboard shows a provenance chip (`live`,
`cached · 3h old`, `bundled snapshot · 40d old`), and a panel built from many
symbols reports the *weakest* tier that contributed. Serving a months-old
snapshot as if it were this morning's tape would be worse than an error box.

**A paint never touches the network** — that's the point, and it means a panel can
say "bundled snapshot" on a machine with perfectly good internet. Live data comes
from the **Refresh** button, a `?refresh=1` request, or the lifecycle warm that runs
at app load and after the machine wakes. See
[docs/operating.md](./docs/operating.md#data-freshness) — this is the thing most
likely to look like a bug and not be one.

Regenerate the snapshot with `python scripts/refresh_seed.py`.

## Install

Requires a protoAgent host **≥ v0.78.0** (watches + watch hooks, ADR 0067 — the
highest floor among the seams it uses).

```bash
# 1. Fetch it (clones + pins a SHA in plugins.lock; does NOT run code).
#    Pin a release rather than `main` — the lock records the SHA either way, but a
#    tag is what you can reason about later.
python -m server plugin install https://github.com/protoLabsAI/prototrader-finance --ref v0.4.1

# 2. Install its deps — declared, never auto-installed.
#    pandas/numpy are REQUIRED; yfinance/ccxt are optional (without them every view
#    still renders from the bundled snapshot, you just can't fetch live prices).
python -m server plugin install-deps prototrader-finance

# 3. Enable it — this is the trust decision — and restart.
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

Step-by-step: [docs/operating.md](./docs/operating.md#arming-the-paper-broker).

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # 100+ tests: no network, no protoAgent host
ruff check .
```

The suite is **host-free**: `requirements-dev.txt` carries no provider SDKs, and CI
asserts that `import graph` fails, so the claim can't silently rot.

It uses protoAgent's own harness — `tests/_plugin_testkit.py` is
`graph/plugins/testkit.py` vendored verbatim, exactly as the scaffolder does — so
the plugin loads as the same synthetic package the host builds and `register()` is
driven through a `FakeRegistry` that is parity-tested against the real one. Refresh
it with:

```bash
cp ~/dev/protoAgent/graph/plugins/testkit.py tests/_plugin_testkit.py
```

`tests/test_docs.py` checks this README and `docs/reference.md` against what
`register()` actually contributes, so a documented tool, verifier, event or skill
that no longer exists fails the build.

## Repo map

| Path | What |
|---|---|
| `__init__.py` | `register()` — the single seam the host calls |
| `book.py` | The paper book: valuation, and the one place that refuses the sample |
| `marketdata.py` | The live → cache → snapshot read path, and provenance |
| `store.py` | Every "which directory?" question, via `sdk.plugin_store()` |
| `data/ backtest/ factors/ behavioral/ broker/` | Tool groups; each engine is pure and separately tested |
| `dashboard/` | The console view — `api.py` (gated data), `page.py` (the page) |
| `metrics/knowledge/chat/a2a/lifecycle/watch_hooks/conn_test.py` | One SDK seam each |
| `verifiers.py` `events.py` | Goal/watch verifiers; the event contract |
| `seed/` | The bundled snapshot + sample book |
| `scripts/` | `preview.py` (view with no host) · `refresh_seed.py` |

## Releases

Tagged releases with notes:
[github.com/protoLabsAI/prototrader-finance/releases](https://github.com/protoLabsAI/prototrader-finance/releases).
Install a tag, not `main` — `plugins.lock` records the SHA either way, but a tag is
what you can reason about later.

## License

MIT — see [LICENSE](./LICENSE).
