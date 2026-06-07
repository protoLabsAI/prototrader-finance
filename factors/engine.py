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

import numpy as np
import pandas as pd

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


def fetch_panel(tickers: list[str], period: str = "3y") -> pd.DataFrame:
    """Adjusted close panel (rows = dates, cols = tickers) via a batched download."""
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("install requirements-finance.txt (yfinance)") from e
    data = yf.download(tickers, period=period, interval="1d",
                       auto_adjust=True, progress=False, group_by="column")
    if data is None or len(data) == 0:
        raise RuntimeError("no data for the universe")
    close = data["Close"] if "Close" in data else data
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close.dropna(how="all").ffill()


def _vol_panel(tickers: list[str], period: str = "3y"):
    """Close panel + a volume panel (for volume_trend)."""
    import yfinance as yf
    data = yf.download(tickers, period=period, interval="1d",
                       auto_adjust=True, progress=False, group_by="column")
    close = data["Close"] if "Close" in data else data
    vol = data["Volume"] if "Volume" in data else None
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close.dropna(how="all").ffill(), (vol.ffill() if vol is not None else None)


def compute_factor(close: pd.DataFrame, factor: str, vol: pd.DataFrame | None = None) -> pd.DataFrame:
    """Factor value per (date, ticker), sign-standardized so +IC = factor works."""
    rets = close.pct_change()
    f = (factor or "").lower()
    if f in ("momentum_12_1", "momentum", "mom"):
        return close.shift(21) / close.shift(252) - 1
    if f in ("reversal_1m", "reversal", "rev"):
        return -(close / close.shift(21) - 1)
    if f in ("low_vol", "lowvol", "vol"):
        return -(rets.rolling(21).std())
    if f in ("trend_200d", "trend"):
        return close / close.rolling(200).mean() - 1
    if f in ("volume_trend", "volume"):
        if vol is None:
            raise ValueError("volume_trend needs the volume panel")
        return vol.rolling(5).mean() / vol.rolling(60).mean() - 1
    raise ValueError(f"unknown factor {factor!r} — try: {', '.join(FACTORS)}")


def evaluate(factor: str, universe: list[str] | None = None, period: str = "3y",
             horizon: int = 21, step: int = 21) -> dict:
    """IC-evaluate one factor across the universe. horizon/step in trading days."""
    universe = universe or DEFAULT_UNIVERSE
    if (factor or "").lower() in ("volume_trend", "volume"):
        close, vol = _vol_panel(universe, period)
    else:
        close, vol = fetch_panel(universe, period), None
    fac = compute_factor(close, factor, vol)
    fwd = close.shift(-horizon) / close - 1

    dates = close.index[252::step]              # leave a year of warmup
    ics, rics = [], []
    for d in dates:
        x = fac.loc[d]
        y = fwd.loc[d]
        m = x.notna() & y.notna()
        if m.sum() < 5:
            continue
        xv, yv = x[m], y[m]
        if xv.std() == 0 or yv.std() == 0:
            continue
        ics.append(float(np.corrcoef(xv, yv)[0, 1]))
        rics.append(float(pd.Series(xv).rank().corr(pd.Series(yv).rank())))

    if not ics:
        return {"factor": factor, "error": "not enough cross-sectional data"}
    ics = np.array(ics); rics = np.array(rics)
    mean_ic = float(ics.mean())
    ir = float(mean_ic / ics.std() * np.sqrt(252 / step)) if ics.std() > 0 else 0.0
    hit = float((ics > 0).mean())
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
        "mean_ic": mean_ic, "mean_rank_ic": float(rics.mean()),
        "ir": ir, "hit_rate": hit, "verdict": verdict,
    }


def evaluate_all(universe: list[str] | None = None, period: str = "3y") -> list[dict]:
    """Run every factor, sorted by |IR| (strongest first)."""
    out = []
    for name in FACTORS:
        try:
            out.append(evaluate(name, universe, period))
        except Exception as e:  # noqa: BLE001
            out.append({"factor": name, "error": str(e)})
    return sorted(out, key=lambda r: abs(r.get("ir", 0) or 0), reverse=True)
