"""Factor evaluation engine for protoTrader (Slice 4) — a tractable "Alpha Zoo".

Computes a curated set of **price/volume factors** (no fundamentals needed) over a
universe and scores each by **Information Coefficient** — the cross-sectional
correlation between the factor today and forward returns — the standard test of
whether a factor predicts.

Metrics per factor:
- **IC** (Pearson) and **rank-IC** (Spearman), averaged across rebalance dates.
- **IR** = mean(IC) / std(IC) × √(rebalances/yr) — consistency, not just size.
- **hit rate** — % of periods the IC had the expected sign.
- **verdict** — alive / weak / reversed / dead.

Factors are *standardized* by sign so a positive IC = "the factor works as
intended" (e.g. low-vol is stored as −volatility, so positive IC = low-vol wins).
"""

from __future__ import annotations

import math

from ..numeric import Frame, isna, mean, pearson, spearman, stdev

# A small diversified default universe (large caps across sectors) — enough
# cross-section for an IC to mean something; override with your own list.
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "V", "UNH",
    "XOM", "JNJ", "PG", "HD", "KO", "PEP", "CVX", "MRK", "WMT", "COST",
]

FACTORS = {
    "momentum_12_1": "12-month return skipping the last month (classic momentum).",
    "reversal_1m": "negative 1-month return (short-term mean reversion).",
    "low_vol": "negative trailing 21-day volatility (low-volatility anomaly).",
    "trend_200d": "percent above the 200-day moving average (trend).",
    "volume_trend": "5-day vs 60-day average volume (participation).",
}


def fetch_panel(tickers: list[str], period: str = "3y", *, prefer: str = "live") -> Frame:
    """Adjusted close panel (rows = dates, cols = tickers)."""
    return fetch_panel_meta(tickers, period, prefer=prefer)[0]


def fetch_panel_meta(tickers: list[str], period: str = "3y", *, prefer: str = "live"):
    """Close panel + per-symbol provenance — one symbol failing drops a column,
    not the study (:func:`marketdata.panel`)."""
    from .. import marketdata

    return marketdata.panel(tickers, period, prefer=prefer)


def _vol_panel(tickers: list[str], period: str = "3y", *, prefer: str = "live"):
    """Close panel + a volume panel (for volume_trend).

    Volume comes from the same cached/seeded OHLCV frames as close, so a
    volume-based factor degrades exactly like a price-based one.
    """
    from .. import marketdata

    close, _ = marketdata.panel(tickers, period, prefer=prefer)
    vols = {}
    for t in close.columns:
        try:
            vols[t] = marketdata.bars(t, period, prefer="cache").frame["Volume"]
        except Exception:
            continue
    vol = Frame.from_columns(vols).reindex_ffill(close.index) if vols else None
    return close, vol


def compute_factor(close: Frame, factor: str, vol: Frame | None = None) -> Frame:
    """Factor value per (date, ticker), sign-standardized so +IC = factor works."""
    f = (factor or "").lower()
    if f in ("momentum_12_1", "momentum", "mom"):
        return close.shift(21) / close.shift(252) - 1
    if f in ("reversal_1m", "reversal", "rev"):
        return -(close / close.shift(21) - 1)
    if f in ("low_vol", "lowvol", "vol"):
        return -(close.pct_change().rolling_std(21))
    if f in ("trend_200d", "trend"):
        return close / close.rolling_mean(200) - 1
    if f in ("volume_trend", "volume"):
        if vol is None:
            raise ValueError("volume_trend needs the volume panel")
        return vol.rolling_mean(5) / vol.rolling_mean(60) - 1
    raise ValueError(f"unknown factor {factor!r} — try: {', '.join(FACTORS)}")


def evaluate(factor: str, universe: list[str] | None = None, period: str = "3y",
             horizon: int = 21, step: int = 21, *, prefer: str = "live") -> dict:
    """IC-evaluate one factor across the universe. horizon/step in trading days.

    ``prefer`` is threaded down to the panel fetch. It exists because omitting it
    was not a missing feature but a silent one: the dashboard asked for
    ``prefer="cache"`` when fetching provenance, then called this, which defaulted
    to ``"live"`` and issued 154 provider calls on a single paint — while the chip
    on screen described the *other* fetch. A study whose numbers and whose
    stated source come from different requests is worse than no chip at all.
    """
    universe = universe or DEFAULT_UNIVERSE
    if (factor or "").lower() in ("volume_trend", "volume"):
        close, vol = _vol_panel(universe, period, prefer=prefer)
    else:
        close, vol = fetch_panel(universe, period, prefer=prefer), None
    fac = compute_factor(close, factor, vol)
    fwd = close.shift(-horizon) / close - 1

    dates = close.index[252::step]              # leave a year of warmup
    ics, rics = [], []
    for d in dates:
        x, y = fac.row_at(d), fwd.row_at(d)
        # One fixed ticker order for both legs — a correlation between two
        # differently-ordered cross-sections would be a number about nothing.
        names = [t for t in close.columns if not isna(x.get(t)) and not isna(y.get(t))]
        if len(names) < 5:
            continue
        xv = [x[t] for t in names]
        yv = [y[t] for t in names]
        if stdev(xv) == 0 or stdev(yv) == 0:
            continue
        ics.append(pearson(xv, yv))
        rics.append(spearman(xv, yv))

    if not ics:
        return {"factor": factor, "error": "not enough cross-sectional data"}
    mean_ic = mean(ics)
    # ddof=0 here, deliberately: this was `numpy.ndarray.std()`, the population
    # deviation, while the Sharpe denominator in the backtest engine is pandas'
    # sample one. Unifying them would quietly move every published IR.
    ic_sd = stdev(ics, ddof=0)
    ir = float(mean_ic / ic_sd * math.sqrt(252 / step)) if ic_sd > 0 else 0.0
    hit = sum(1 for v in ics if v > 0) / len(ics)
    # Factors are sign-standardized so +IC = "works as intended". So "alive" needs
    # a *positive*, consistent IC; a strong *negative* IC means it reversed.
    verdict = (
        "alive" if mean_ic >= 0.03 and hit >= 0.55 else
        "reversed" if mean_ic <= -0.03 and hit <= 0.45 else
        "weak" if mean_ic >= 0.015 else
        "dead"
    )
    return {
        "factor": factor, "universe_size": len(universe), "period": period,
        "horizon_days": horizon, "rebalances": len(ics),
        "mean_ic": mean_ic, "mean_rank_ic": mean(rics),
        "ir": ir, "hit_rate": hit, "verdict": verdict,
    }


def evaluate_all(universe: list[str] | None = None, period: str = "3y", *,
                 prefer: str = "live") -> list[dict]:
    """Run every factor, sorted by |IR| (strongest first)."""
    out = []
    for name in FACTORS:
        try:
            out.append(evaluate(name, universe, period, prefer=prefer))
        except Exception as e:  # noqa: BLE001
            out.append({"factor": name, "error": str(e)})
    return sorted(out, key=lambda r: abs(r.get("ir", 0) or 0), reverse=True)
