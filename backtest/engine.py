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

import numpy as np
import pandas as pd

_INSTALL = "install the finance deps: `pip install -r requirements-finance.txt`"


# ── data ─────────────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, period: str = "2y", interval: str = "1d",
                exchange: str = "okx") -> pd.DataFrame:
    """OHLCV as a DatetimeIndexed DataFrame (Open/High/Low/Close/Volume).
    Crypto when ``symbol`` contains '/', else an equity/ETF ticker."""
    if "/" in symbol:
        try:
            import ccxt
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(_INSTALL) from e
        ex = getattr(ccxt, exchange.lower())({"enableRateLimit": True})
        tf = interval if interval in ("1m", "5m", "15m", "1h", "4h", "1d", "1w") else "1d"
        limit = {"2y": 730, "1y": 365, "6mo": 180, "5y": 1800, "max": 1000}.get(period, 730)
        raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=min(limit, 1000))
        if not raw:
            raise RuntimeError(f"no data for {symbol!r} @ {exchange}")
        df = pd.DataFrame(raw, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
        df.index = pd.to_datetime(df["ts"], unit="ms")
        return df[["Open", "High", "Low", "Close", "Volume"]]
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(_INSTALL) from e
    df = yf.Ticker(symbol).history(period=period, interval=interval)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {symbol!r}")
    return df[["Open", "High", "Low", "Close", "Volume"]]


# ── strategies → target position (0/1 long-flat, or -1/0/1) ──────────────────

def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def signals(df: pd.DataFrame, strategy: str, params: dict) -> pd.Series:
    """Target position series in {0,1} (long/flat) on the bar's close."""
    c = df["Close"]
    s = (strategy or "").lower()
    p = params or {}
    if s in ("buy_hold", "buyhold", "bh"):
        pos = pd.Series(1.0, index=c.index)
    elif s in ("ma_cross", "macross", "sma_cross"):
        fast, slow = int(p.get("fast", 20)), int(p.get("slow", 50))
        pos = (_sma(c, fast) > _sma(c, slow)).astype(float)
    elif s in ("rsi_meanrev", "rsi", "rsi_meanreversion"):
        n = int(p.get("period", 14)); lo = float(p.get("oversold", 30)); hi = float(p.get("overbought", 55))
        r = _rsi(c, n)
        pos = pd.Series(np.nan, index=c.index)
        pos[r < lo] = 1.0
        pos[r > hi] = 0.0
        pos = pos.ffill().fillna(0.0)
    elif s in ("breakout", "donchian", "momentum"):
        n = int(p.get("lookback", 20))
        hi = c.rolling(n).max(); lo = c.rolling(n).min()
        pos = pd.Series(np.nan, index=c.index)
        pos[c >= hi] = 1.0
        pos[c <= lo] = 0.0
        pos = pos.ffill().fillna(0.0)
    else:
        raise ValueError(f"unknown strategy {strategy!r} — try ma_cross, rsi_meanrev, breakout, buy_hold")
    return pos.fillna(0.0).clip(-1, 1)


# ── simulate + metrics ───────────────────────────────────────────────────────

def _periods_per_year(idx: pd.DatetimeIndex) -> float:
    """Self-calibrating: actual bars ÷ years spanned. Auto-adjusts equities
    (~252 trading-day bars/yr) vs crypto (~365) vs intraday, without a hardcoded
    annualization factor."""
    if len(idx) < 3:
        return 252.0
    span_years = (idx[-1] - idx[0]).days / 365.25
    if span_years <= 0:
        return 252.0
    return max(1.0, len(idx) / span_years)


def simulate(df: pd.DataFrame, pos: pd.Series, cost_bps: float = 5.0,
             slippage_bps: float = 2.0) -> pd.DataFrame:
    """Strategy bar-returns with frictions. Position is shifted one bar (no
    look-ahead); cost+slippage charged on |Δposition|."""
    ret = df["Close"].pct_change().fillna(0.0)
    held = pos.shift(1).fillna(0.0)                       # act next bar
    turn = held.diff().abs().fillna(held.abs())          # entries/exits
    friction = turn * ((cost_bps + slippage_bps) / 1e4)
    strat = held * ret - friction
    out = pd.DataFrame({"ret": ret, "held": held, "turn": turn, "strat": strat})
    out["equity"] = (1 + out["strat"]).cumprod()
    out["bh_equity"] = (1 + out["ret"]).cumprod()
    return out


def _max_dd(equity: pd.Series) -> float:
    if not len(equity):
        return 0.0
    peak = equity.cummax()
    dd = float(((equity / peak) - 1).min())
    return max(dd, -1.0)  # floor at -100%; negative equity can't read below total ruin


def metrics(sim: pd.DataFrame, idx: pd.DatetimeIndex) -> dict:
    r = sim["strat"]
    ppy = _periods_per_year(idx)
    n = len(r)
    # Rebase equity within THIS window from the per-bar strat returns, rather than
    # reading the full cumulative curve. Lets a sliced sim (IS/OOS) be measured on
    # its own window — the boundary bar's return is preserved (it was computed on
    # the full frame) instead of zeroed by re-simulating the slice — and keeps a
    # non-positive equity from poisoning CAGR/vol/drawdown (NaN → JSON hazard).
    eq = (1 + r).cumprod()
    final_eq = float(eq.iloc[-1]) if n else 1.0
    total = final_eq - 1.0 if n else 0.0
    years = max(n / ppy, 1e-9)
    cagr = float(final_eq ** (1 / years) - 1) if (n and final_eq > 0) else 0.0
    rstd = float(r.std()) if n > 1 else 0.0  # std is undefined (NaN) for n<=1
    vol = float(rstd * np.sqrt(ppy)) if np.isfinite(rstd) else 0.0
    sharpe = float(r.mean() / rstd * np.sqrt(ppy)) if rstd > 0 else 0.0
    downside = r[r < 0].std()
    sortino = float(r.mean() / downside * np.sqrt(ppy)) if downside and downside > 0 else 0.0
    # trades = entries (0/neg → positive held)
    held = sim["held"]
    entries = int(((held > 0) & (held.shift(1) <= 0)).sum())
    bar_win = float((r[sim["turn"] == 0] > 0).mean()) if (sim["turn"] == 0).any() else float((r > 0).mean())
    exposure = float((held != 0).mean())
    bh_total = float((1 + sim["ret"]).cumprod().iloc[-1] - 1) if n else 0.0
    return {
        "total_return": total, "cagr": cagr, "vol": vol, "sharpe": sharpe,
        "sortino": sortino, "max_dd": _max_dd(eq), "trades": entries,
        "bar_win_rate": bar_win, "exposure": exposure, "bars": n,
        "bh_total_return": bh_total,
    }


def bootstrap_ci(sim: pd.DataFrame, idx: pd.DatetimeIndex, n_boot: int = 500,
                 block: int = 5, seed: int = 7) -> dict:
    """Stationary-bootstrap CI on Sharpe + total return (resample return blocks)."""
    r = sim["strat"].to_numpy()
    n = len(r)
    if n < 20:
        return {}
    rng = np.random.default_rng(seed)
    ppy = _periods_per_year(idx)
    sharpes, totals = [], []
    n_blocks = int(np.ceil(n / block))
    for _ in range(n_boot):
        starts = rng.integers(0, n, n_blocks)
        sample = np.concatenate([np.take(r, range(s, s + block), mode="wrap") for s in starts])[:n]
        sd = sample.std()
        sharpes.append(sample.mean() / sd * np.sqrt(ppy) if sd > 0 else 0.0)
        totals.append(float(np.prod(1 + sample) - 1))
    q = lambda a, p: float(np.percentile(a, p))  # noqa: E731
    return {
        "sharpe_ci": (q(sharpes, 5), q(sharpes, 95)),
        "total_return_ci": (q(totals, 5), q(totals, 95)),
        "sharpe_p_gt_0": float(np.mean(np.array(sharpes) > 0)),
    }


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
    is_m = metrics(sim.iloc[:cut], df.index[:cut]) if cut > 20 else {}
    oos_m = metrics(sim.iloc[cut:], df.index[cut:]) if (len(df) - cut) > 20 else {}
    return {
        "symbol": symbol, "strategy": strategy, "params": params or {},
        "period": period, "interval": interval,
        "cost_bps": cost_bps, "slippage_bps": slippage_bps,
        "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
        "full": full, "in_sample": is_m, "out_of_sample": oos_m,
        "ci": bootstrap_ci(sim, df.index),
    }
