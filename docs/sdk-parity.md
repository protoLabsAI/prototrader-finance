# Plugin SDK parity

What this plugin uses from the protoAgent plugin SDK, and — more usefully — what
it **deliberately doesn't**, with a reason for each.

A reference plugin that adopted every seam whether or not it needed one would be
worse than useless: it would teach the wrong lesson. Every ✅ below has a real
finance job. Every ⛔ has a standing rationale, so the next person doesn't spend
an afternoon rediscovering why it doesn't fit.

The contract halves are: **contribution** (`PluginRegistry.register_*` — what the
plugin adds) and **consumption** (`graph.sdk` — what it calls back into core).
See `docs/reference/plugin-registry-api.md` and `plugin-sdk-api.md` in the host.

## Contribution — `registry.*`

| Seam | | Use here |
|---|---|---|
| `register_tools` | ✅ | 13 tools: market data, backtest, factor IC, behavioural journal, gated paper broker. |
| `register_subagent` | ✅ | The research desk — `market-analyst`, `quant`, `risk-manager` — that the lead delegates to via `task()` and the workflows compose. |
| `register_router` | ✅ | Three: the public view **page**, the gated **data** API, and the ADR 0029 connection test on `/api/config/`. |
| `register_chat_command` | ✅ | `/quant <SYMBOL> [strategy] [period]` — a desk read in chat. Registered as a command, not a tool, precisely because the seam is **not** model-invokable: it's the operator's shortcut, and the agent already has the same engines. |
| `register_goal_verifier` | ✅ | 5. `portfolio_return` grounds "get the book to +10%" in the actual book; `max_drawdown` / `trading_halted` / `data_is_stale` are tripwires; `factor_alive` re-checks a factor's IC. Without these a finance goal is graded on the model's own account of its work. |
| `register_watch_hook` | ✅ | `on_met` turns a tripped tripwire into a bus event; `on_stalled` distinguishes "evidence stopped moving" from "verifier broke" — for `data_is_stale` the stall *is* the signal. |
| `register_lifecycle_hook` | ✅ | `on_app_loaded` + `on_system_wake` warm the price cache. The first thing anyone does is open the dashboard; a cold cache means that first paint is a stale badge. |
| `register_a2a_skill` | ✅ | `quant-backtest` (typed: `output_schema` + `result_mime`, so the executor's structured finalizer enforces JSON a peer can parse) and `market-read`. |
| `emit` / `on` + `emits:`/`subscribes:` | ✅ | 7 topics. Own-bus subscriptions invalidate the dashboard's memoized gate when a *tool* arms a mandate or fills an order — otherwise the view stays stale until the TTL expires. |
| `register_skill_dir` | ✅ | Auto-discovered from `skills/` (ADR 0027). Calling it explicitly would just re-add the same path the loader already found. |
| `register_workflow_dir` | ✅ | Same, from `workflows/`. |
| `register_surface` | ⛔ | No background loop to run. Prices are pulled on demand and cached; a polling surface would burn provider quota to keep data nobody is looking at warm. The cache warm is a lifecycle hook, which is the right size for it. |
| `register_mcp_server` | ⛔ | MCP is for **untrusted, out-of-process** code. These tools are trusted in-process Python that needs the plugin's own store and cache; putting them behind MCP would add a process boundary and buy nothing. |
| `register_middleware` | ⛔ | Nothing here needs to see or alter every turn. A middleware that existed only to log finance tool calls would duplicate core telemetry. |
| `register_late_tool_factory` | ⛔ | The tool set is static — it doesn't depend on runtime state that's unavailable at register time. |
| `register_knowledge_store` | ⛔ | This plugin is a knowledge *producer*, not a store implementation. It writes through `sdk.knowledge_add` and lets core own retrieval. |
| `register_embedder` | ⛔ | No finance-specific embedding need; text here is short and English. |
| `register_thread_id_resolver` | ⛔ | One resolver wins process-wide, so a plugin claiming it makes itself incompatible with every other plugin that might. Not worth it for anything this plugin wants. |
| `register_goal_hook` | ⛔ | Terminal-state reactions belong to whoever *set* the goal. A finance plugin that fired its own follow-up when any goal completed would be acting outside its remit. The watch hooks cover what this plugin genuinely owns. |
| `public_paths` | ⛔ | The view page is already public via the prefix split; nothing else here should skip the bearer. No inbound webhook. |
| `federation_paths` | ⛔ | No peer needs to reach the paper book with only a federation credential. Lowering the tier on a route that reads positions is the wrong default. |

## Consumption — `graph.sdk`

| Seam | | Use here |
|---|---|---|
| `plugin_store` | ✅ | Every durable byte — paper book, audit ledger, price cache — via `store.py`. Replaces v0.1.0's private `graph.config_io._live_config_dir` import (see that module's docstring for how that failed silently). |
| `record_metric` / `metric_history` | ✅ | The equity series. Drawdown needs a high-water mark and the dashboard wants a trend, both of which need *prior* values that a point-in-time payload can't have. |
| `knowledge_add` | ✅ | A completed backtest becomes a retrievable fact, so "how did ma_cross do on NVDA" doesn't mean re-running it. |
| `create_watch` | ✅ | Standing tripwires with stable ids (so a reload replaces rather than duplicates), armed **only when the broker is armed** — watching the drawdown of a book that can't trade is noise. |
| `metric_last`, `list_watches`, `clear_watch` | ⛔ | Available and unneeded: the tripwires are a fixed set with fixed ids, so there's nothing to enumerate or revoke dynamically. |
| `run_subagent` | ⛔ | The desk subagents are *registered*, and the lead delegates through `task()`. Calling `run_subagent` from a tool would bypass the tool fences the desk's allowlists exist to enforce. |
| `complete` | ⛔ | No one-shot classification step; every judgement here is arithmetic, and arithmetic that a model does instead is arithmetic done worse. |
| `schedule_recurring` | ⛔ | Tempting for a daily equity snapshot, but equity is recorded on the events that actually change it (a fill) plus each lifecycle warm. A timer would mostly record the same number and pin a background job for it. |
| `start_goal_loop` / `react_on` | ⛔ | This is a research surface an operator drives, not an autonomous trading loop. An OODA loop here would be a loop whose actions are gated behind human approval on every order — i.e. not a loop. |
| `spawn_background` | ⛔ | Nothing runs long enough to need it. The slowest call is a factor study (~1s on cached data). |
| `supervise`, `Knobs`, `DecisionLog` | ⛔ | All three serve a background *engine* — a watchdog, a live-tunable control surface, an audit trail of autonomous decisions. There is no engine: the paper broker only acts on an explicit, human-approved order, and it has its own append-only ledger. |
| `gateway_client` | ⛔ | No direct model calls; the subagents go through core. |
| `config()` | ⛔ | `registry.config` already delivers the resolved section at register time. |

## The safety model is not negotiable

Distribution never relaxes it. The paper broker is OFF until a mandate file exists
and sets `enabled: true`; the kill-switch is honoured in **both** the plugin store
and the host config dir (an operator reaching for it is having a bad day, and a
halt file in the "wrong" directory must fail safe); every order takes human
approval; `mode: live` is not implemented and refuses.

The dashboard participates in that: with no mandate present it renders the
limit values muted and labelled *not in force*, because showing `$5,000 max order`
in plain text next to "Present: no" reads as a limit that applies when nothing
applies at all.

## Data honesty

Every price the plugin serves carries its tier — `live`, `cache`, or the bundled
`seed` snapshot — and the view shows it as a chip. A panel built from many symbols
reports the **weakest** tier that contributed, not the best: a strip where 25
names are live and one came off the snapshot is not a live strip.

The bundled sample book is flagged `demo: true` all the way to the UI and labelled
there. A sample book that read as a real one would be a lie about someone's money.
