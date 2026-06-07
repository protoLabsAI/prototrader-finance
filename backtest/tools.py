"""Backtest tools for protoTrader (Slice 2) — wrap the engine as LLM tools.

Returns a compact, honest report: in-sample vs out-of-sample, vs buy-and-hold,
with a bootstrap CI on the Sharpe so a thin/overfit result is visible.
"""

from __future__ import annotations

import asyncio

from langchain_core.tools import tool

_STRATEGIES = "ma_cross, rsi_meanrev, breakout, buy_hold"


def _pct(x) -> str:
    return f"{x * 100:+.1f}%" if x is not None else "n/a"


def _report(r: dict) -> str:
    f = r["full"]
    oos = r.get("out_of_sample") or {}
    isamp = r.get("in_sample") or {}
    ci = r.get("ci") or {}
    lines = [
        f"**Backtest — {r['symbol']} / {r['strategy']}** {r['params']}",
        f"{r['start']} → {r['end']} ({r['interval']}, {f['bars']} bars; "
        f"costs {r['cost_bps']}+{r['slippage_bps']}bps)",
        "",
        "| metric | strategy | buy & hold |",
        "|---|---|---|",
        f"| total return | {_pct(f['total_return'])} | {_pct(f['bh_total_return'])} |",
        f"| CAGR | {_pct(f['cagr'])} | — |",
        f"| Sharpe | {f['sharpe']:.2f} | — |",
        f"| Sortino | {f['sortino']:.2f} | — |",
        f"| max drawdown | {_pct(f['max_dd'])} | — |",
        f"| trades | {f['trades']} | 1 |",
        f"| exposure | {f['exposure'] * 100:.0f}% | 100% |",
    ]
    if isamp and oos:
        lines += [
            "",
            f"**In-sample vs out-of-sample** (a big gap = overfit): "
            f"IS Sharpe {isamp.get('sharpe', 0):.2f} / return {_pct(isamp.get('total_return'))}  →  "
            f"OOS Sharpe {oos.get('sharpe', 0):.2f} / return {_pct(oos.get('total_return'))}",
        ]
    if ci.get("sharpe_ci"):
        lo, hi = ci["sharpe_ci"]
        lines += [
            f"**Bootstrap CI:** Sharpe 90% CI [{lo:.2f}, {hi:.2f}], "
            f"P(Sharpe>0) = {ci.get('sharpe_p_gt_0', 0) * 100:.0f}%.",
        ]
    verdict = "beat" if f["total_return"] > f["bh_total_return"] else "trailed"
    lines += ["", f"_The strategy **{verdict}** buy-and-hold here. Past results "
                  f"don't guarantee future returns; this is analysis, not advice._"]
    return "\n".join(lines)


@tool
async def backtest_strategy(
    symbol: str,
    strategy: str = "ma_cross",
    params: dict | None = None,
    period: str = "2y",
    interval: str = "1d",
    cost_bps: float = 5.0,
    slippage_bps: float = 2.0,
) -> str:
    """Backtest a canonical strategy on a ticker or crypto pair, with realistic
    costs, an out-of-sample split, a buy-and-hold benchmark, and a bootstrap CI.

    Args:
        symbol: equity/ETF ticker (e.g. "SPY", "AAPL") or crypto pair (e.g. "BTC/USDT").
        strategy: one of ma_cross, rsi_meanrev, breakout, buy_hold.
        params: strategy params, e.g. {"fast":20,"slow":50} for ma_cross,
            {"period":14,"oversold":30,"overbought":55} for rsi_meanrev,
            {"lookback":20} for breakout.
        period: history window — 6mo,1y,2y,5y,max (default 2y).
        interval: bar size — 1d,1wk (default 1d).
        cost_bps: round-trip commission in bps charged on position changes (default 5).
        slippage_bps: slippage in bps per change (default 2).
    """
    def _run() -> str:
        try:
            from . import engine
        except ImportError:  # pragma: no cover
            import engine  # type: ignore
        try:
            r = engine.backtest(
                symbol, strategy, params or {}, period=period, interval=interval,
                cost_bps=cost_bps, slippage_bps=slippage_bps,
            )
            return _report(r)
        except Exception as e:  # noqa: BLE001
            return f"Error: backtest {symbol!r}/{strategy!r} failed: {e}"

    return await asyncio.to_thread(_run)


@tool
async def list_strategies() -> str:
    """List the backtest strategies and their parameters."""
    return (
        "Backtest strategies (use with `backtest_strategy`):\n"
        "- **ma_cross** — SMA crossover; params: fast (20), slow (50). Trend-following.\n"
        "- **rsi_meanrev** — RSI mean-reversion; params: period (14), oversold (30), overbought (55).\n"
        "- **breakout** — N-day high breakout / N-day low exit; params: lookback (20). Momentum.\n"
        "- **buy_hold** — baseline, always long.\n"
        "All backtests report strategy vs buy-and-hold, in/out-of-sample, and a bootstrap Sharpe CI."
    )


def get_backtest_tools() -> list:
    return [backtest_strategy, list_strategies]
