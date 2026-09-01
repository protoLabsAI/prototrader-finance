"""A small, honest vectorized backtest engine for protoTrader (Slice 2).

Design choices that keep results trustworthy (the persona's job is to not lie
with backtests):

- **No look-ahead.** A signal computed on bar *t*'s close is applied to bar
  *t+1*'s return (positions are shifted one bar before P&L).
- **Realistic frictions.** Per-trade cost + slippage charged on every position
  *change* (entries and exits), in bps of notional.
- **Out-of-sample split.** Metrics are reported in-sample and out-of-sample so an
  overfit curve is visible.
- **Uncertainty.** A stationary bootstrap of bar returns gives a CI on the Sharpe
  and total return — a pretty point estimate on 20 trades is not a signal.

Data: yfinance for equities/ETFs, ccxt for crypto pairs (``BASE/QUOTE``).
"""

from __future__ import annotations

import math
import random
from datetime import datetime

from ..numeric import NaN, Frame, Series, isna, percentile


# ── data ─────────────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, period: str = "2y", interval: str = "1d",
                exchange: str = "okx", *, prefer: str = "live") -> Frame:
    """OHLCV as a date-indexed :class:`~numeric.Frame` (Open/High/Low/Close/Volume).

    Delegates to :mod:`marketdata`, so a failed provider call falls back to the
    cache and then the bundled snapshot instead of raising. Callers that need to
    know *which* tier answered should use :func:`fetch_bars` and read
    ``.source`` — a backtest run against a nine-month-old snapshot is still a
    valid backtest, but the caller has to be able to say so.
    """
    return fetch_bars(symbol, period, interval, exchange, prefer=prefer).frame


def fetch_bars(symbol: str, period: str = "2y", interval: str = "1d",
               exchange: str = "okx", *, prefer: str = "live"):
    """The same fetch, keeping the provenance envelope (:class:`marketdata.Bars`)."""
    from .. import marketdata

    return marketdata.bars(symbol, period, interval, prefer=prefer, exchange=exchange)


# ── strategies → target position (0/1 long-flat, or -1/0/1) ──────────────────

def _sma(s: Series, n: int) -> Series:
    return s.rolling_mean(n)


def _rsi(s: Series, n: int = 14) -> Series:
    d = s.diff()
    up = d.clip(lower=0).ewm_mean(1 / n)
    dn = (-d.clip(upper=0)).ewm_mean(1 / n)
    # A zero average loss would divide by zero; NaN means "undefined here", which
    # reads through to a flat position rather than an infinite RSI.
    rs = up / dn.replace_zero_with_nan()
    return 100 - 100 / (1 + rs)


def signals(df: Frame, strategy: str, params: dict) -> Series:
    """Target position series in {0,1} (long/flat) on the bar's close."""
    c = df["Close"]
    s = (strategy or "").lower()
    p = params or {}
    if s in ("buy_hold", "buyhold", "bh"):
        pos = Series(c.dates, [1.0] * len(c))
    elif s in ("ma_cross", "macross", "sma_cross"):
        fast, slow = int(p.get("fast", 20)), int(p.get("slow", 50))
        # A comparison against a warmup NaN is False, so the position is flat until
        # both averages exist — the same thing `(fast > slow).astype(float)` did.
        crossed = _sma(c, fast).gt(_sma(c, slow))
        pos = Series(c.dates, [1.0 if x else 0.0 for x in crossed])
    elif s in ("rsi_meanrev", "rsi", "rsi_meanreversion"):
        n = int(p.get("period", 14)); lo = float(p.get("oversold", 30)); hi = float(p.get("overbought", 55))
        r = _rsi(c, n)
        # Entries then exits, then carry the last decision forward: between the two
        # thresholds the position is whatever it already was, which is what makes
        # this mean-reversion rather than a per-bar signal.
        pos = Series(c.dates, [NaN] * len(c))
        pos = pos.where(r.lt(lo), 1.0)
        pos = pos.where(r.gt(hi), 0.0)
        pos = pos.ffill().fillna(0.0)
    elif s in ("breakout", "donchian", "momentum"):
        n = int(p.get("lookback", 20))
        hi_band = c.rolling_max(n); lo_band = c.rolling_min(n)
        pos = Series(c.dates, [NaN] * len(c))
        pos = pos.where(c.ge(hi_band), 1.0)
        pos = pos.where(c.le(lo_band), 0.0)
        pos = pos.ffill().fillna(0.0)
    else:
        raise ValueError(f"unknown strategy {strategy!r} — try ma_cross, rsi_meanrev, breakout, buy_hold")
    return pos.fillna(0.0).clip(-1, 1)


# ── simulate + metrics ───────────────────────────────────────────────────────

def _periods_per_year(idx: list[datetime]) -> float:
    """Self-calibrating: actual bars ÷ years spanned. Auto-adjusts equities
    (~252 trading-day bars/yr) vs crypto (~365) vs intraday, without a hardcoded
    annualization factor."""
    if len(idx) < 3:
        return 252.0
    span_years = (idx[-1] - idx[0]).days / 365.25
    if span_years <= 0:
        return 252.0
    return max(1.0, len(idx) / span_years)


def simulate(df: Frame, pos: Series, cost_bps: float = 5.0,
             slippage_bps: float = 2.0) -> Frame:
    """Strategy bar-returns with frictions. Position is shifted one bar (no
    look-ahead); cost+slippage charged on |Δposition|."""
    ret = df["Close"].pct_change().fillna(0.0)
    held = pos.shift(1).fillna(0.0)                       # act next bar
    turn = held.diff().abs().fillna(held.abs())          # entries/exits
    friction = turn * ((cost_bps + slippage_bps) / 1e4)
    strat = held * ret - friction
    out = Frame(df.dates, {"ret": ret.values, "held": held.values,
                           "turn": turn.values, "strat": strat.values})
    out = out.with_column("equity", (strat + 1.0).cumprod())
    out = out.with_column("bh_equity", (ret + 1.0).cumprod())
    return out


def _max_dd(equity: Series) -> float:
    if not len(equity):
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak - 1.0).min()
    if isna(dd):
        return 0.0
    return max(float(dd), -1.0)  # floor at -100%; negative equity can't read below total ruin


def _fraction_true(mask: list[bool]) -> float:
    """Mean of a boolean series — pandas counts a False for every row, including
    the ones where a NaN made the comparison false."""
    return (sum(1 for m in mask if m) / len(mask)) if mask else 0.0


def metrics(sim: Frame, idx: list[datetime]) -> dict:
    r = sim["strat"]
    ppy = _periods_per_year(idx)
    n = len(r)
    # Rebase equity within THIS window from the per-bar strat returns, rather than
    # reading the full cumulative curve. Lets a sliced sim (IS/OOS) be measured on
    # its own window — the boundary bar's return is preserved (it was computed on
    # the full frame) instead of zeroed by re-simulating the slice — and keeps a
    # non-positive equity from poisoning CAGR/vol/drawdown (NaN → JSON hazard).
    eq = (r + 1.0).cumprod()
    final_eq = float(eq.last()) if n else 1.0
    total = final_eq - 1.0 if n else 0.0
    years = max(n / ppy, 1e-9)
    cagr = float(final_eq ** (1 / years) - 1) if (n and final_eq > 0) else 0.0
    rstd = r.std() if n > 1 else 0.0  # the deviation is undefined for n <= 1
    vol = float(rstd * math.sqrt(ppy)) if not isna(rstd) else 0.0
    sharpe = float(r.mean() / rstd * math.sqrt(ppy)) if (not isna(rstd) and rstd > 0) else 0.0
    downside = r.select(r.lt(0)).std()
    sortino = float(r.mean() / downside * math.sqrt(ppy)) if (not isna(downside) and downside > 0) else 0.0
    # trades = entries (0/neg → positive held)
    held = sim["held"]
    prev_held = held.shift(1)
    entries = sum(1 for a, b in zip(held.gt(0), prev_held.le(0)) if a and b)
    flat = sim["turn"].eq(0)
    bar_win = _fraction_true(r.select(flat).gt(0)) if any(flat) else _fraction_true(r.gt(0))
    exposure = _fraction_true(held.ne(0))
    bh_total = float((sim["ret"] + 1.0).cumprod().last() - 1) if n else 0.0
    return {
        "total_return": total, "cagr": cagr, "vol": vol, "sharpe": sharpe,
        "sortino": sortino, "max_dd": _max_dd(eq), "trades": entries,
        "bar_win_rate": bar_win, "exposure": exposure, "bars": n,
        "bh_total_return": bh_total,
    }


def bootstrap_ci(sim: Frame, idx: list[datetime], n_boot: int = 500,
                 block: int = 5, seed: int = 7) -> dict:
    """Stationary-bootstrap CI on Sharpe + total return (resample return blocks).

    The generator is the standard library's Mersenne Twister rather than numpy's
    PCG64, so a given seed no longer reproduces the pre-0.5.0 interval digit for
    digit. It is the same estimator over the same returns and remains deterministic
    per seed; the sampling stream simply comes from somewhere else.
    """
    r = sim["strat"].values
    n = len(r)
    if n < 20:
        return {}
    rng = random.Random(seed)
    ppy = _periods_per_year(idx)
    sharpes, totals = [], []
    n_blocks = int(math.ceil(n / block))
    for _ in range(n_boot):
        sample: list[float] = []
        for _ in range(n_blocks):
            start = rng.randrange(n)
            # Wrap at the end — a stationary bootstrap treats the series as a circle,
            # so the last bars are resampled as often as the first.
            sample.extend(r[(start + k) % n] for k in range(block))
        sample = sample[:n]
        sd = _pstdev(sample)
        mu = sum(sample) / len(sample)
        sharpes.append(mu / sd * math.sqrt(ppy) if sd > 0 else 0.0)
        totals.append(math.prod(1 + x for x in sample) - 1)
    return {
        "sharpe_ci": (percentile(sharpes, 5), percentile(sharpes, 95)),
        "total_return_ci": (percentile(totals, 5), percentile(totals, 95)),
        "sharpe_p_gt_0": _fraction_true([s > 0 for s in sharpes]),
    }


def _pstdev(xs: list[float]) -> float:
    """Population deviation (ddof=0) — what ``numpy.ndarray.std()`` returned here.
    The Sharpe below is a bootstrap statistic, not the reported one, so it keeps
    numpy's convention rather than borrowing the sample deviation used in metrics()."""
    n = len(xs)
    if n < 1:
        return 0.0
    mu = sum(xs) / n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / n)


def backtest(symbol: str, strategy: str, params: dict | None = None,
             period: str = "2y", interval: str = "1d", cost_bps: float = 5.0,
             slippage_bps: float = 2.0, oos_frac: float = 0.3,
             exchange: str = "okx") -> dict:
    """Full run: fetch → signals → simulate → metrics (full + IS/OOS) + bootstrap CI."""
    df = fetch_ohlcv(symbol, period=period, interval=interval, exchange=exchange)
    pos = signals(df, strategy, params or {})
    sim = simulate(df, pos, cost_bps=cost_bps, slippage_bps=slippage_bps)
    full = metrics(sim, df.index)
    cut = int(len(df) * (1 - oos_frac))
    # Slice the already-computed sim (don't re-simulate the slices): re-simulating
    # recomputes pct_change on the slice, which zeros the first OOS bar's return
    # and the carried-in position, distorting the IS/OOS honesty split.
    is_m = metrics(sim.slice_rows(None, cut), df.index[:cut]) if cut > 20 else {}
    oos_m = metrics(sim.slice_rows(cut, None), df.index[cut:]) if (len(df) - cut) > 20 else {}
    return {
        "symbol": symbol, "strategy": strategy, "params": params or {},
        "period": period, "interval": interval,
        "cost_bps": cost_bps, "slippage_bps": slippage_bps,
        "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
        "full": full, "in_sample": is_m, "out_of_sample": oos_m,
        "ci": bootstrap_ci(sim, df.index),
    }
