"""protoTrader Finance — the full-bundle finance plugin (the plugin-devkit pattern).

ONE plugin, every protoAgent contribution type:

- **tools** — market data, vectorized backtest, factor IC, behavioral journal, and
  the gated paper broker (the `data` / `backtest` / `factors` / `behavioral` /
  `broker` subpackages, each a `get_*_tools()` factory),
- **subagents** — the 3-role research desk (`desk.subagents`) the lead agent
  delegates to via `task`,
- **workflows** — `quant-desk` + `investment-committee` (the `workflows/` subdir,
  auto-discovered, ADR 0027),
- **skills** — the finance SKILL.md set (the `skills/` subdir, auto-discovered),
- **console view** — the Quant Desk dashboard, an equity-curve backtester served
  at `/plugins/prototrader-finance/dashboard` (ADR 0026),
- **config / secrets / settings** — declared in the manifest (ADR 0019).

Consolidates protoTrader's six finance plugins + two global workflows into one
self-contained, git-URL-installable bundle. Research-primary; the paper broker is
OFF until a mandate exists and every order is HITL-gated — distribution never
relaxes that safety model (in-process plugins run with full agent authority).
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")


def register(registry) -> None:
    """Wire the whole finance bundle into the agent (ADR 0018). Called once at load.

    Every group is wrapped: a plugin that half-loads with a logged error beats one
    whose tools vanish because a watch could not be armed. `skills/` + `workflows/`
    auto-discover (ADR 0027) — no call needed for them.

    The seams NOT used, each with a standing rationale, are in docs/sdk-parity.md.
    """
    from . import a2a, chat, conn_test, events, lifecycle, verifiers, watch_hooks
    from .backtest.tools import get_backtest_tools
    from .behavioral.tools import get_behavioral_tools
    from .broker import engine as broker_engine
    from .broker.tools import get_broker_tools
    from .dashboard import build_dashboard_router, build_data_router
    from .data.tools import get_finance_tools
    from .desk.subagents import desk_subagents
    from .factors.tools import get_factor_tools

    counts: dict[str, int] = {}

    def group(name: str, fn):
        """Run one contribution group; log and continue if it fails."""
        try:
            counts[name] = fn() or 0
        except Exception:
            counts[name] = 0
            log.exception("[prototrader-finance] %s failed to register", name)

    # The paper broker resolves its mandate path from the plugin config
    # (`broker_mandate_path`), so hand it the resolved section before any tool runs.
    broker_engine.set_config(registry.config)
    events.bind(registry)

    # ── tools: market data → backtest → factors → behavioural → gated broker ──
    # Per-factory, not one loop: a factory raising mid-loop used to abort the whole
    # group AFTER earlier factories had already registered — leaving a partial
    # toolset live while the log said "0 tools". Now each reports its own outcome
    # and the others still land.
    for factory in (get_finance_tools, get_backtest_tools, get_factor_tools,
                    get_behavioral_tools, get_broker_tools):
        def _register(f=factory):
            tools = list(f())
            registry.register_tools(tools)
            return len(tools)

        group(f"tools:{factory.__name__.removeprefix('get_').removesuffix('_tools')}", _register)

    # ── subagents: the research desk the lead delegates to via task() ─────────
    def _subagents():
        n = 0
        for cfg in desk_subagents():
            registry.register_subagent(cfg)
            n += 1
        return n

    group("subagents", _subagents)

    # Routers get `live_config` (a callable), not `registry.config` (a register-time
    # SNAPSHOT). FastAPI can't re-mount a router, so a handler closed over the
    # snapshot serves stale config until a restart — an operator changing their
    # benchmark in Settings would see no effect and reasonably conclude it broke.
    def cfg():
        try:
            return registry.live_config()
        except Exception:
            return registry.config  # older host: the snapshot is all there is

    # ── console view: PAGE public, DATA gated (plugin-view rules 1 + 2) ───────
    # Three SEPARATE groups on purpose. Bundled, one router raising took the other
    # two with it — and the way that showed up was a rail icon whose every panel
    # failed to fetch, with the actual cause logged once at load and nowhere near
    # the symptom.
    group("page_router", lambda: (
        registry.register_router(build_dashboard_router(cfg)) or 1))
    group("data_router", lambda: (
        registry.register_router(build_data_router(cfg),
                                 prefix="/api/plugins/prototrader-finance") or 1))
    # ADR 0029 "Test connection" — the console looks for the exact convention path
    # /api/config/test-<config_section>, so the route carries the full path and the
    # router registers with an empty prefix (the same shape core's chat-surface
    # wirer uses, and the one case the prefix-conformance warning skips).
    group("test_router", lambda: (
        registry.register_router(conn_test.build_test_router(cfg), prefix="") or 1))

    # ── chat command: user-only, deliberately NOT an agent tool ──────────────
    group("chat_command", lambda: (
        registry.register_chat_command("quant", chat.make_quant_command(registry.config)) or 1))

    # ── goal + watch verifiers (ADR 0028): ground truth for finance goals ─────
    def _verifiers():
        for name, (fn, desc) in verifiers.VERIFIERS.items():
            registry.register_goal_verifier(name, fn, desc)
        return len(verifiers.VERIFIERS)

    group("verifiers", _verifiers)

    # ── watch hooks + standing tripwires (ADR 0067) ──────────────────────────
    def _watches():
        on_met, on_stalled = watch_hooks.make_watch_hooks()
        registry.register_watch_hook(on_met=on_met, on_stalled=on_stalled)
        return watch_hooks.arm_tripwires(registry.config)

    group("tripwires", _watches)

    # ── lifecycle hooks (ADR 0074): warm the cache at boot and after sleep ────
    def _lifecycle():
        on_loaded, on_wake = lifecycle.make_lifecycle_hooks(registry.config)
        registry.register_lifecycle_hook(on_app_loaded=on_loaded, on_system_wake=on_wake)
        return 2

    group("lifecycle", _lifecycle)

    # ── A2A card skills: what PEER agents see advertised, typed ───────────────
    def _a2a():
        for spec in a2a.A2A_SKILLS:
            registry.register_a2a_skill(spec)
        return len(a2a.A2A_SKILLS)

    group("a2a_skills", _a2a)

    # ── own-bus subscriptions (ADR 0039) ─────────────────────────────────────
    group("subscriptions", lambda: (events.subscribe(registry) or 2))

    tool_total = sum(v for k, v in counts.items() if k.startswith("tools:"))
    summary = [f"{tool_total} tools"] + [
        f"{v} {k}" for k, v in counts.items() if v and not k.startswith("tools:")
    ]
    log.info(
        "[prototrader-finance] registered %s (workflows/ + skills/ auto-discovered)",
        ", ".join(summary),
    )
