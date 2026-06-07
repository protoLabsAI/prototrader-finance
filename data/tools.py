"""Market-data tools for protoTrader (Slice 1).

No-auth fallback chain: **yfinance** (US equities/ETFs) + **ccxt** (crypto, public
exchange data). Blocking library calls run in a worker thread so they never freeze
the event loop. Every tool returns a compact human-readable string (the LLM reads
it) and degrades to a clean ``Error: …`` rather than raising — including a clear
"install requirements-finance.txt" message when the optional deps are absent.
"""

from __future__ import annotations

import asyncio

from langchain_core.tools import tool

_INSTALL_HINT = (
    "the finance data libraries aren't installed — run "
    "`pip install -r requirements-finance.txt` (yfinance + ccxt)."
)


def _fmt(n, *, money=False, pct=False) -> str:
    if n is None:
        return "n/a"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if pct:
        return f"{n * 100:.2f}%" if abs(n) < 1 else f"{n:.2f}%"
    if money:
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if abs(n) >= div:
                return f"${n / div:.2f}{unit}"
        return f"${n:,.2f}"
    return f"{n:,.2f}"


# ── US equities / ETFs (yfinance) ────────────────────────────────────────────

@tool
async def stock_quote(symbol: str) -> str:
    """Current quote + key stats for a US stock or ETF.

    Args:
        symbol: ticker, e.g. "NVDA", "SPY", "AAPL".
    """
    def _run() -> str:
        try:
            import yfinance as yf
        except ImportError:
            return f"Error: {_INSTALL_HINT}"
        try:
            t = yf.Ticker(symbol)
            fi = t.fast_info
            # yfinance FastInfo keys are camelCase (dayHigh, yearLow, marketCap…);
            # keep snake_case fallbacks for cross-version robustness.
            def g(camel, snake):
                v = fi.get(camel)
                return v if v is not None else fi.get(snake)
            last = g("lastPrice", "last_price")
            prev = g("previousClose", "previous_close")
            chg = (last - prev) / prev if (last and prev) else None
            lines = [
                f"**{symbol.upper()}** — {_fmt(last, money=True)}"
                + (f" ({_fmt(chg, pct=True)} vs prev close)" if chg is not None else ""),
                f"day range: {_fmt(g('dayLow', 'day_low'), money=True)}–{_fmt(g('dayHigh', 'day_high'), money=True)}",
                f"52w range: {_fmt(g('yearLow', 'year_low'), money=True)}–{_fmt(g('yearHigh', 'year_high'), money=True)}",
                f"market cap: {_fmt(g('marketCap', 'market_cap'), money=True)}  |  "
                f"volume: {_fmt(g('lastVolume', 'last_volume'))}",
            ]
            if not last:
                return f"Error: no quote for {symbol!r} (unknown ticker, or data source down)."
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"Error: quote for {symbol!r} failed: {e}"

    return await asyncio.to_thread(_run)


@tool
async def stock_price_history(symbol: str, period: str = "6mo", interval: str = "1d") -> str:
    """OHLCV history for a US stock/ETF, with a compact summary (for charting/backtest context).

    Args:
        symbol: ticker, e.g. "NVDA".
        period: 1d,5d,1mo,3mo,6mo,1y,2y,5y,ytd,max (default 6mo).
        interval: 1d,1wk,1mo (and intraday 1m..1h for short periods) (default 1d).
    """
    def _run() -> str:
        try:
            import yfinance as yf
        except ImportError:
            return f"Error: {_INSTALL_HINT}"
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval)
            if df is None or df.empty:
                return f"Error: no history for {symbol!r} ({period}/{interval})."
            first, last = df.iloc[0], df.iloc[-1]
            ret = (last["Close"] - first["Close"]) / first["Close"]
            hi, lo = df["High"].max(), df["Low"].min()
            tail = df.tail(5)[["Open", "High", "Low", "Close", "Volume"]]
            rows = "\n".join(
                f"  {ix.date()}  O {r.Open:.2f}  H {r.High:.2f}  L {r.Low:.2f}  C {r.Close:.2f}  V {int(r.Volume):,}"
                for ix, r in tail.iterrows()
            )
            return (
                f"**{symbol.upper()}** {period}/{interval} — {len(df)} bars\n"
                f"return: {_fmt(ret, pct=True)}  |  high {_fmt(hi, money=True)}  low {_fmt(lo, money=True)}\n"
                f"last 5 bars:\n{rows}"
            )
        except Exception as e:  # noqa: BLE001
            return f"Error: history for {symbol!r} failed: {e}"

    return await asyncio.to_thread(_run)


@tool
async def stock_fundamentals(symbol: str) -> str:
    """Key fundamentals for a US stock — sector, valuation, margins, growth.

    Args:
        symbol: ticker, e.g. "AAPL".
    """
    def _run() -> str:
        try:
            import yfinance as yf
        except ImportError:
            return f"Error: {_INSTALL_HINT}"
        try:
            info = yf.Ticker(symbol).info or {}
            if not info.get("symbol") and not info.get("longName"):
                return f"Error: no fundamentals for {symbol!r}."
            g = info.get
            return "\n".join([
                f"**{g('longName', symbol.upper())}** ({g('sector', 'n/a')} / {g('industry', 'n/a')})",
                f"market cap: {_fmt(g('marketCap'), money=True)}  |  price: {_fmt(g('currentPrice'), money=True)}",
                f"P/E (ttm): {_fmt(g('trailingPE'))}  fwd P/E: {_fmt(g('forwardPE'))}  P/B: {_fmt(g('priceToBook'))}",
                # profitMargins / revenueGrowth are reliable fractions in yfinance;
                # dividendYield / returnOnEquity have inconsistent units across
                # versions, so they're omitted rather than shown wrong.
                f"profit margin: {_fmt(g('profitMargins'), pct=True)}  |  rev growth (yoy): {_fmt(g('revenueGrowth'), pct=True)}",
                f"52w: {_fmt(g('fiftyTwoWeekLow'), money=True)}–{_fmt(g('fiftyTwoWeekHigh'), money=True)}  beta: {_fmt(g('beta'))}",
            ])
        except Exception as e:  # noqa: BLE001
            return f"Error: fundamentals for {symbol!r} failed: {e}"

    return await asyncio.to_thread(_run)


# ── Crypto (ccxt, public) ────────────────────────────────────────────────────

def _ccxt_exchange(name: str):
    import ccxt

    name = (name or "okx").lower()
    if not hasattr(ccxt, name):
        raise ValueError(f"unknown exchange {name!r}")
    return getattr(ccxt, name)({"enableRateLimit": True})


@tool
async def crypto_quote(symbol: str, exchange: str = "okx") -> str:
    """Current ticker for a crypto pair from a public exchange (no auth).

    Args:
        symbol: pair, e.g. "BTC/USDT", "ETH/USDT".
        exchange: ccxt exchange id, e.g. "okx", "binance", "coinbase" (default okx).
    """
    def _run() -> str:
        try:
            import ccxt  # noqa: F401
        except ImportError:
            return f"Error: {_INSTALL_HINT}"
        try:
            ex = _ccxt_exchange(exchange)
            t = ex.fetch_ticker(symbol)
            chg = t.get("percentage")
            return (
                f"**{symbol}** @ {exchange} — {_fmt(t.get('last'), money=True)}"
                + (f" ({_fmt(chg, pct=True) if chg is not None else ''} 24h)" if chg is not None else "")
                + f"\nbid {_fmt(t.get('bid'), money=True)} / ask {_fmt(t.get('ask'), money=True)}"
                f"  |  24h high {_fmt(t.get('high'), money=True)} low {_fmt(t.get('low'), money=True)}"
                f"  |  vol {_fmt(t.get('baseVolume'))}"
            )
        except Exception as e:  # noqa: BLE001
            return f"Error: crypto quote {symbol!r}@{exchange} failed: {e}"

    return await asyncio.to_thread(_run)


@tool
async def crypto_price_history(symbol: str, timeframe: str = "1d", limit: int = 90, exchange: str = "okx") -> str:
    """OHLCV history for a crypto pair (for charting/backtest context).

    Args:
        symbol: pair, e.g. "BTC/USDT".
        timeframe: 1m,5m,15m,1h,4h,1d,1w (default 1d).
        limit: number of bars (default 90, max ~1000).
        exchange: ccxt exchange id (default okx).
    """
    def _run() -> str:
        try:
            import ccxt  # noqa: F401
        except ImportError:
            return f"Error: {_INSTALL_HINT}"
        try:
            ex = _ccxt_exchange(exchange)
            ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=min(int(limit), 1000))
            if not ohlcv:
                return f"Error: no history for {symbol!r}@{exchange} ({timeframe})."
            first, last = ohlcv[0], ohlcv[-1]
            ret = (last[4] - first[4]) / first[4] if first[4] else None
            hi = max(r[2] for r in ohlcv)
            lo = min(r[3] for r in ohlcv)
            import datetime as dt
            rows = "\n".join(
                f"  {dt.datetime.utcfromtimestamp(r[0] / 1000).date()}  O {r[1]:.2f}  H {r[2]:.2f}  L {r[3]:.2f}  C {r[4]:.2f}  V {r[5]:.1f}"
                for r in ohlcv[-5:]
            )
            return (
                f"**{symbol}** @ {exchange} {timeframe} — {len(ohlcv)} bars\n"
                f"return: {_fmt(ret, pct=True)}  |  high {_fmt(hi, money=True)}  low {_fmt(lo, money=True)}\n"
                f"last 5 bars:\n{rows}"
            )
        except Exception as e:  # noqa: BLE001
            return f"Error: crypto history {symbol!r}@{exchange} failed: {e}"

    return await asyncio.to_thread(_run)


def get_finance_tools() -> list:
    """All finance-data tools, in display order."""
    return [
        stock_quote,
        stock_price_history,
        stock_fundamentals,
        crypto_quote,
        crypto_price_history,
    ]
