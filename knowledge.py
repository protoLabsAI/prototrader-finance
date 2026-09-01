"""Writing finance findings back into core's knowledge store (`sdk.knowledge_add`).

A backtest the agent ran an hour ago should be recallable next week without
re-running it. A plugin must never open core's `knowledge.db` directly.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"


async def remember_backtest(symbol: str, strategy: str, metrics: dict, period: str,
                            source: str = "") -> None:
    """Record one backtest as a retrievable fact.

    ``source`` is the DATA TIER the run used. It belongs in the stored text: a
    result computed off a months-old bundled snapshot recalled a week later, with
    nothing saying so, is a number with no way to judge it.
    """
    try:
        from graph import sdk

        tier = f" (data: {source})" if source else ""
        await sdk.knowledge_add(
            f"Backtest {strategy} on {symbol} over {period}{tier}: "
            f"CAGR {metrics.get('cagr', 0):.2%}, Sharpe {metrics.get('sharpe', 0):.2f}, "
            f"max drawdown {metrics.get('max_dd', 0):.2%}, "
            f"total return {metrics.get('total_return', 0):.2%} "
            f"vs buy-and-hold {metrics.get('bh_total_return', 0):.2%}, "
            f"{metrics.get('trades', 0)} trades.",
            domain="prototrader-backtests",
            heading=f"{symbol} · {strategy} · {period}",
        )
    except Exception:
        log.debug("[%s] backtest not recorded to knowledge (no host?)", PLUGIN_ID)
