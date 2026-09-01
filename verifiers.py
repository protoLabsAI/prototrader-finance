"""Goal + watch verifiers (ADR 0028 / ADR 0067) — ground truth for finance goals.

A goal like "get the paper book to +10%" is only worth setting if something can
*check* it against reality rather than against the model's own account of its
work. These are those checks: async ``(spec, ctx) -> VerifyResult``, reading the
same book and the same price tiers the dashboard reads.

``args`` are declarative data, validated here — no shell, no eval. This is the
only verifier type safe to set programmatically (ADR 0028 D3).

The same functions back **watches**: a watch polls a verifier on a cadence and
fires when it trips, which is what turns ``max_drawdown`` from a number on a
dashboard nobody is looking at into a tripwire that wakes the agent.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")


def _result(met: bool, reason: str, evidence: str = ""):
    from graph.goals import VerifyResult

    return VerifyResult(met=met, reason=reason, evidence=evidence)


def _equity_and_peak(config: dict | None = None) -> tuple[float, float, float]:
    """(equity, starting_cash, peak) for the REAL book — raises on the sample.

    Peak comes from the recorded metric series: drawdown needs a high-water mark,
    which a point-in-time verifier cannot know. `sdk.metric_history` is that
    memory; without it every plugin hand-rolls a JSON file for the same numbers.
    """
    from . import book as book_mod

    b = book_mod.load(config).require_real()
    marks, _ = book_mod.marks_for(b)
    equity = b.equity(marks)
    start = b.starting_cash or equity

    peak = equity
    try:
        from graph import sdk

        hist = sdk.metric_history("equity", plugin_id="prototrader-finance") or []
        vals = [float(v) for _, v in hist]
        if vals:
            peak = max(max(vals), equity)
    except Exception:
        log.debug("[prototrader-finance] no equity history — peak falls back to live equity")
    return equity, start, peak


async def portfolio_return(spec: dict, ctx) -> object:
    """Met when the paper book's total return reaches ``min_return`` (a fraction).

    ``{"type": "plugin", "check": "prototrader-finance:portfolio_return",
       "args": {"min_return": 0.10}}``
    """
    from . import book as book_mod

    target = float((spec.get("args") or {}).get("min_return", 0.10))
    try:
        equity, start, _ = _equity_and_peak()
    except book_mod.SampleBookError as e:
        return _result(False, str(e))
    except Exception as e:
        return _result(False, f"could not value the book: {e}")
    if not start:
        return _result(False, "no starting capital recorded — arm a mandate first")
    ret = (equity / start) - 1
    return _result(
        ret >= target,
        f"total return {ret:.2%} vs target {target:.2%}",
        evidence=f"equity={equity:.2f} start={start:.2f}",
    )


async def max_drawdown(spec: dict, ctx) -> object:
    """Met when drawdown from the high-water mark EXCEEDS ``limit`` — a tripwire.

    "Met" is deliberately the bad outcome: a watch fires on met, and the thing
    worth waking the agent for is the breach, not the calm.
    """
    from . import book as book_mod

    limit = abs(float((spec.get("args") or {}).get("limit", 0.15)))
    try:
        equity, _, peak = _equity_and_peak()
    except book_mod.SampleBookError as e:
        return _result(False, str(e))
    except Exception as e:
        return _result(False, f"could not value the book: {e}")
    dd = 0.0 if peak <= 0 else (equity / peak) - 1
    return _result(
        dd <= -limit,
        f"drawdown {dd:.2%} against a {limit:.0%} limit",
        evidence=f"equity={equity:.2f} peak={peak:.2f}",
    )


async def trading_halted(spec: dict, ctx) -> object:
    """Met when the kill-switch is engaged. Cheap, and the one state an operator
    most wants a notification for."""
    from . import store

    halt = store.killswitch_engaged()
    return _result(halt is not None, f"kill-switch {'ENGAGED' if halt else 'clear'}", evidence=str(halt or ""))


async def data_is_stale(spec: dict, ctx) -> object:
    """Met when the freshest bars are older than ``max_age_h`` hours.

    A demo box that lost its network keeps rendering — by design — off the cache
    and then the snapshot. That is the right behaviour and the wrong thing to be
    unaware of, so it is watchable.
    """
    max_age_h = float((spec.get("args") or {}).get("max_age_h", 48))
    symbol = str((spec.get("args") or {}).get("symbol", "SPY"))
    from . import marketdata

    try:
        b = marketdata.bars(symbol, "1mo", prefer="cache")
    except Exception as e:
        return _result(True, f"no data at all for {symbol}: {e}")
    age_h = b.age_s / 3600
    return _result(
        age_h > max_age_h,
        f"{symbol} data is {b.label()} ({age_h:.1f}h) against a {max_age_h:.0f}h limit",
        evidence=f"source={b.source} age_s={b.age_s:.0f}",
    )


async def factor_alive(spec: dict, ctx) -> object:
    """Met when a named factor's IC still clears ``min_ic`` on the current universe.

    The honest use of a factor study: not "did it work in the backtest" but "is it
    still working", checked on a cadence.
    """
    args = spec.get("args") or {}
    name = str(args.get("factor", "momentum_12_1"))
    min_ic = float(args.get("min_ic", 0.03))
    try:
        from . import marketdata
        from .factors import engine as fe

        universe = [s for s in (marketdata.seed_universe() or fe.DEFAULT_UNIVERSE)
                    if s not in ("SPY", "QQQ") and "-USD" not in s]
        # prefer="cache": this runs on a watch cadence, so a live re-fetch of the
        # whole universe every tick would be a self-inflicted rate limit.
        r = fe.evaluate(name, universe, str(args.get("period", "3y")), prefer="cache")
    except Exception as e:
        return _result(False, f"factor study failed: {e}")
    if r.get("error"):
        return _result(False, f"{name}: {r['error']}")
    ic = float(r.get("mean_ic") or 0.0)
    return _result(
        ic >= min_ic,
        f"{name} IC {ic:+.3f} vs {min_ic:+.3f} ({r.get('verdict')})",
        evidence=f"ir={r.get('ir')} hit_rate={r.get('hit_rate')} n={r.get('rebalances')}",
    )


VERIFIERS = {
    "portfolio_return": (portfolio_return, "Paper book total return has reached a target."),
    "max_drawdown": (max_drawdown, "Paper book drawdown has BREACHED a limit (tripwire)."),
    "trading_halted": (trading_halted, "The broker kill-switch is engaged (tripwire)."),
    "data_is_stale": (data_is_stale, "Market data has gone stale beyond a limit (tripwire)."),
    "factor_alive": (factor_alive, "A factor's information coefficient still clears a threshold."),
}
