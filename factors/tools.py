"""Factor-evaluation tools (Slice 4) — the Alpha Zoo, IC-scored."""

from __future__ import annotations

import asyncio

from langchain_core.tools import tool


def _row(r: dict) -> str:
    if "error" in r:
        return f"  {r['factor']:16} ERROR: {r['error']}"
    return (f"  {r['factor']:16} IC {r['mean_ic']:+.4f}  rankIC {r['mean_rank_ic']:+.4f}  "
            f"IR {r['ir']:+.2f}  hit {r['hit_rate']*100:.0f}%  ({r['rebalances']} rebals)  → **{r['verdict']}**")


@tool
async def factor_eval(factor: str, universe: list[str] | None = None, period: str = "3y") -> str:
    """Evaluate one factor by Information Coefficient over a universe.

    Args:
        factor: one of momentum_12_1, reversal_1m, low_vol, trend_200d, volume_trend.
        universe: list of tickers (default: a 20-name diversified large-cap set).
        period: history window (default 3y).
    """
    def _run() -> str:
        try:
            from . import engine
        except ImportError:  # pragma: no cover
            import engine  # type: ignore
        try:
            r = engine.evaluate(factor, universe, period)
            if "error" in r:
                return f"Error: {r['error']}"
            return (f"**Factor: {r['factor']}** — {engine.FACTORS.get(r['factor'],'')}\n"
                    f"universe {r['universe_size']} names, {r['period']}, "
                    f"{r['horizon_days']}d forward, {r['rebalances']} rebalances\n\n"
                    + _row(r) + "\n\n"
                    "_IC = cross-sectional corr(factor, forward return). +IC means the factor "
                    "predicts as intended; IR is its consistency. Analysis, not advice._")
        except Exception as e:  # noqa: BLE001
            return f"Error: factor_eval {factor!r} failed: {e}"

    return await asyncio.to_thread(_run)


@tool
async def factor_zoo(universe: list[str] | None = None, period: str = "3y") -> str:
    """Score ALL bundled factors by IC/IR over a universe, strongest first.

    Args:
        universe: list of tickers (default: the 20-name large-cap set).
        period: history window (default 3y).
    """
    def _run() -> str:
        try:
            from . import engine
        except ImportError:  # pragma: no cover
            import engine  # type: ignore
        try:
            rows = engine.evaluate_all(universe, period)
            body = "\n".join(_row(r) for r in rows)
            return (f"**Alpha Zoo** — {len(rows)} factors over "
                    f"{len(universe or engine.DEFAULT_UNIVERSE)} names, {period}, ranked by |IR|:\n\n"
                    f"{body}\n\n_alive = positive consistent IC; reversed = it predicts the wrong "
                    "way; weak/dead = no edge in this sample. IC is sample-specific — a factor can "
                    "be alive in one regime and dead in another._")
        except Exception as e:  # noqa: BLE001
            return f"Error: factor_zoo failed: {e}"

    return await asyncio.to_thread(_run)


def get_factor_tools() -> list:
    return [factor_eval, factor_zoo]
