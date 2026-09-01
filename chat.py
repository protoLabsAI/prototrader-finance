"""The `/quant` chat command — a user affordance, not a model tool.

Registered as a chat command precisely because that seam is NOT model-invokable:
the agent already has the same engines as tools; this is the operator's shortcut.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"

from . import events  # noqa: E402

def make_quant_command(config: dict | None):
    """``/quant <SYMBOL> [strategy] [period]`` — a one-line desk read, in chat.

    Registered as a chat command rather than a tool on purpose: it is a *user*
    affordance, and the seam is explicitly not model-invokable. The agent already
    has the same engines as tools; this is the operator's shortcut.
    """
    default_symbol = ((config or {}).get("default_benchmark") or "SPY").upper()

    async def handler(rest: str, session_id: str):
        parts = (rest or "").split()
        symbol = (parts[0] if parts else default_symbol).upper()
        strategy = parts[1] if len(parts) > 1 else "ma_cross"
        period = parts[2] if len(parts) > 2 else "2y"
        try:
            from .backtest import engine

            bars = engine.fetch_bars(symbol, period=period, prefer="cache")
            df = bars.frame
            sim = engine.simulate(df, engine.signals(df, strategy, {}))
            m = engine.metrics(sim, df.index)
        except Exception as e:
            return (f"**/quant {symbol}** failed — {type(e).__name__}: {e}\n\n"
                    f"Try one of: {', '.join(_universe()) or 'no seeded symbols'}")

        events.emit(events.BACKTEST_COMPLETED, symbol=symbol, strategy=strategy,
                    period=period, sharpe=m.get("sharpe"), source=str(bars.source))
        from . import knowledge

        await knowledge.remember_backtest(symbol, strategy, m, period, str(bars.source))
        edge = (m.get("total_return") or 0) - (m.get("bh_total_return") or 0)
        return (
            f"**{symbol} · {strategy} · {period}** — {bars.label()}\n\n"
            f"| CAGR | Sharpe | Max DD | Total | vs B&H | Trades |\n"
            f"|---|---|---|---|---|---|\n"
            f"| {m.get('cagr', 0):.1%} | {m.get('sharpe', 0):.2f} | {m.get('max_dd', 0):.1%} "
            f"| {m.get('total_return', 0):.1%} | {edge:+.1%} | {m.get('trades', 0)} |\n\n"
            f"*Research output, not advice. Net of 5bps cost + 2bps slippage.*"
        )

    return handler


def _universe() -> list:
    try:
        from . import marketdata

        return marketdata.seed_universe()
    except Exception:
        return []
