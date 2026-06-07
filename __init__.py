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

    skills/ + workflows/ subdirs auto-discover (ADR 0027) — no call needed for them.
    """
    from .backtest.tools import get_backtest_tools
    from .behavioral.tools import get_behavioral_tools
    from .broker.tools import get_broker_tools
    from .dashboard import build_dashboard_router
    from .data.tools import get_finance_tools
    from .desk.subagents import desk_subagents
    from .factors.tools import get_factor_tools

    # Tools — market data → backtest → factors → behavioral → gated paper broker.
    n_tools = 0
    for factory in (
        get_finance_tools,
        get_backtest_tools,
        get_factor_tools,
        get_behavioral_tools,
        get_broker_tools,
    ):
        tools = list(factory())
        registry.register_tools(tools)
        n_tools += len(tools)

    # Subagents — the research desk the workflows compose and the lead delegates to.
    n_subagents = 0
    for cfg in desk_subagents():
        registry.register_subagent(cfg)
        n_subagents += 1

    # Console view (ADR 0026) — the Quant Desk dashboard page + its backtest API,
    # mounted at /plugins/prototrader-finance/…. Reads plugin config (ADR 0019).
    registry.register_router(build_dashboard_router(registry.config))

    log.info(
        "[prototrader-finance] registered %d tools + %d desk subagents + Quant Desk view "
        "(workflows/ + skills/ auto-discovered)",
        n_tools,
        n_subagents,
    )
