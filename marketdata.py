"""Market data with a memory — the layer that makes a demo survive bad wifi.

v0.1.0 sent every panel straight at ``yfinance``/``ccxt``. That is fine for an
agent answering a question and wrong for a dashboard: one rate-limit, one flaky
hotel network, and every chart in the demo is an error box.

Three tiers, newest first:

* **live** — the provider. Authoritative, slow, and allowed to fail.
* **cache** — whatever the last successful live call returned, written through to
  the plugin's own store (:mod:`store`). Survives restarts.
* **seed** — a dated snapshot of real bars committed to the repo (``seed/``), so a
  clean install with no network still renders a full dashboard.

Every read reports **which tier answered and how old it is** (:class:`Bars`), and
the UI shows it. Silently serving a nine-month-old bundled snapshot as if it were
this morning's tape is the one outcome worse than an error box — a viewer would
have no way to tell, and in a finance demo that is the difference between a stale
chart and a wrong claim.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import store

log = logging.getLogger("protoagent.plugins.prototrader-finance")

SEED_DIR = Path(__file__).parent / "seed"
_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# Trading days per period string — used to slice a long snapshot down to the
# window asked for, so one 5y seed serves every period the UI offers.
_PERIOD_DAYS = {
    "1mo": 21, "3mo": 63, "6mo": 126, "1y": 252,
    "2y": 504, "3y": 756, "5y": 1260, "max": 10_000,
}

# How old a cached frame may be before a *live*-preferring read bothers refetching.
# Daily bars change once a day; 6h keeps a demo snappy without serving yesterday.
CACHE_TTL_S = 6 * 3600


@dataclass(frozen=True)
class Bars:
    """An OHLCV frame plus where it came from — provenance is part of the value."""

    frame: pd.DataFrame
    symbol: str
    source: str          # "live" | "cache" | "seed"
    fetched_at: float    # epoch seconds the PROVIDER was last called
    note: str = ""       # why we fell back, when we did

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    @property
    def stale(self) -> bool:
        return self.source != "live" and self.age_s > CACHE_TTL_S

    def label(self) -> str:
        """One human line for the dashboard's provenance chip."""
        age = _humanize(self.age_s)
        if self.source == "live":
            return "live · just now"
        if self.source == "cache":
            return f"cached · {age} old"
        return f"bundled snapshot · {age} old"

    def to_meta(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "age_s": round(self.age_s),
            "stale": self.stale,
            "label": self.label(),
            "note": self.note,
            "rows": int(len(self.frame)),
        }


def _humanize(seconds: float) -> str:
    if seconds < 90:
        return "seconds"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172_800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86_400)}d"


def slug(symbol: str) -> str:
    """Filesystem-safe symbol: ``BTC/USD`` → ``BTC-USD``, always upper."""
    return symbol.strip().upper().replace("/", "-").replace(":", "-")


# ── on-disk format: gzipped CSV ──────────────────────────────────────────────
# CSV because it needs no extra dependency (parquet would drag in pyarrow) and
# stays diffable in review; gzipped because five years of daily bars is 90% air.

def _write_frame(path: Path, df: pd.DataFrame, fetched_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = df[_COLUMNS].copy()
    payload.index.name = "Date"
    # Prices to 4dp (sub-cent — lossless for anything this plugin computes) and
    # volume to int. Full float64 repr triples the on-disk size for digits that
    # only ever encode floating-point noise.
    if "Volume" in payload:
        payload["Volume"] = payload["Volume"].fillna(0).astype("int64")
    with gzip.open(tmp, "wt", newline="") as fh:
        fh.write(f"# fetched_at={fetched_at:.0f}\n")
        payload.to_csv(fh, float_format="%.4f")
    tmp.replace(path)  # atomic — a half-written cache file is never read


def _read_frame(path: Path) -> tuple[pd.DataFrame, float] | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt") as fh:
            first = fh.readline()
            fetched_at = 0.0
            if first.startswith("# fetched_at="):
                fetched_at = float(first.split("=", 1)[1].strip())
            else:
                fh.seek(0)
            df = pd.read_csv(fh, index_col=0, parse_dates=True)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        return df[[c for c in _COLUMNS if c in df.columns]], fetched_at or path.stat().st_mtime
    except Exception:
        log.exception("[prototrader-finance] unreadable frame at %s", path)
        return None


def _cache_path(symbol: str, interval: str) -> Path:
    return store.cache_dir() / f"{slug(symbol)}.{interval}.csv.gz"


def _seed_path(symbol: str) -> Path:
    return SEED_DIR / f"{slug(symbol)}.1d.csv.gz"


def _slice(df: pd.DataFrame, period: str) -> pd.DataFrame:
    n = _PERIOD_DAYS.get(period, _PERIOD_DAYS["2y"])
    return df.tail(n) if len(df) > n else df


# ── the seed snapshot ────────────────────────────────────────────────────────

def seed_manifest() -> dict:
    """Provenance for the bundled snapshot: what, from where, fetched when."""
    p = SEED_DIR / "MANIFEST.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        log.exception("[prototrader-finance] unreadable seed manifest")
        return {}


def seed_universe() -> list[str]:
    """Symbols the bundled snapshot covers — the demo universe."""
    return list(seed_manifest().get("symbols") or [])


# ── the one read path ────────────────────────────────────────────────────────

def _fetch_live(symbol: str, period: str, interval: str, exchange: str) -> pd.DataFrame:
    """Straight at the provider. Raises on anything short of real bars."""
    if "/" in symbol:
        import ccxt

        ex = getattr(ccxt, exchange.lower())({"enableRateLimit": True})
        tf = interval if interval in ("1m", "5m", "15m", "1h", "4h", "1d", "1w") else "1d"
        raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=min(_PERIOD_DAYS.get(period, 504), 1000))
        if not raw:
            raise RuntimeError(f"no data for {symbol!r} @ {exchange}")
        df = pd.DataFrame(raw, columns=["ts", *_COLUMNS])
        df.index = pd.to_datetime(df["ts"], unit="ms")
        return df[_COLUMNS]

    import yfinance as yf

    df = yf.Ticker(symbol).history(period=period, interval=interval)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {symbol!r}")
    df = df[_COLUMNS]
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    return df


def bars(
    symbol: str,
    period: str = "2y",
    interval: str = "1d",
    *,
    prefer: str = "live",
    exchange: str = "okx",
) -> Bars:
    """OHLCV for ``symbol``, from the freshest tier that can answer.

    Args:
        prefer: ``"live"`` hits the provider first and falls back on failure —
            what a tool call or an explicit refresh wants. ``"cache"`` answers
            from disk first and never blocks on the network — what a dashboard
            paint wants.

    Never raises for a symbol the seed covers; raises only when *no* tier has it.
    """
    sym = symbol.strip().upper()
    cached = _read_frame(_cache_path(sym, interval))

    if prefer == "cache" and cached is not None:
        return Bars(_slice(cached[0], period), sym, "cache", cached[1])

    try:
        df = _fetch_live(sym, period, interval, exchange)
        now = time.time()
        try:
            _write_frame(_cache_path(sym, interval), df, now)
        except Exception:
            log.exception("[prototrader-finance] could not cache %s", sym)
        return Bars(df, sym, "live", now)
    except Exception as exc:
        note = f"live fetch failed ({type(exc).__name__}: {exc})"
        log.warning("[prototrader-finance] %s for %s — falling back", note, sym)

    if cached is not None:
        return Bars(_slice(cached[0], period), sym, "cache", cached[1], note)

    seeded = _read_frame(_seed_path(sym))
    if seeded is not None:
        return Bars(_slice(seeded[0], period), sym, "seed", seeded[1], note)

    raise RuntimeError(
        f"no data for {sym!r} — live fetch failed and neither the cache nor the "
        f"bundled snapshot covers it. Seeded symbols: {', '.join(seed_universe()) or 'none'}"
    )


def panel(
    symbols: list[str],
    period: str = "3y",
    *,
    prefer: str = "live",
) -> tuple[pd.DataFrame, list[dict]]:
    """A close-price panel (rows = dates, cols = symbols) + per-symbol provenance.

    Fetches per symbol rather than batching so one dead ticker degrades to a
    missing column instead of an empty panel — a factor study over 11 of 12 names
    is still a factor study.
    """
    frames, meta = {}, []
    for s in symbols:
        try:
            b = bars(s, period, prefer=prefer)
            frames[b.symbol] = b.frame["Close"]
            meta.append(b.to_meta())
        except Exception as exc:
            log.warning("[prototrader-finance] dropping %s from panel: %s", s, exc)
            meta.append({"symbol": s.upper(), "source": "none", "note": str(exc)})
    if not frames:
        raise RuntimeError("no data for any symbol in the universe")
    close = pd.DataFrame(frames).dropna(how="all").ffill()
    return close, meta
