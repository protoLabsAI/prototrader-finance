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

import csv
import gzip
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from . import store
from .numeric import NaN, Frame, isna

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

# A live fetch ALWAYS pulls this window, whatever period was asked for, and the
# result is sliced down afterwards. The cache key is (symbol, interval) with no
# period in it, so without this a "1y" fetch would poison every later "5y" read:
# `_slice` can trim a frame but never extend one, and the caller would silently
# get one year of bars back from a five-year request.
CACHE_PERIOD = "5y"


class Tier(StrEnum):
    """Which tier answered a price read.

    A `str` enum, so it still serialises to `"live"`/`"cache"`/`"seed"` on the wire
    and every existing comparison keeps working — but the four places that switch
    on it can no longer disagree about the spelling, and `Tier.SEED` in a traceback
    beats a bare string.

    Ordered weakest-last on purpose: `_provenance` reports the WEAKEST tier that
    contributed to a multi-symbol panel, and that ordering lives here rather than
    being re-listed at the call site.
    """

    LIVE = "live"
    CACHE = "cache"
    SEED = "seed"


@dataclass(frozen=True)
class Bars:
    """An OHLCV frame plus where it came from — provenance is part of the value."""

    frame: Frame
    symbol: str
    source: Tier
    fetched_at: float    # epoch seconds the PROVIDER was last called
    note: str = ""       # why we fell back, when we did

    def __post_init__(self):
        # Coerce whatever was passed into a real Tier. Callers (and JSON round
        # trips) hand in plain strings, and `"cache" is Tier.CACHE` is False — so
        # identity checks in label()/stale() would silently take the wrong branch
        # and report a fresh cache as a bundled snapshot. Making the type real here
        # beats loosening every comparison to `==`.
        object.__setattr__(self, "source", Tier(self.source))

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    @property
    def stale(self) -> bool:
        return self.source is not Tier.LIVE and self.age_s > CACHE_TTL_S

    def label(self) -> str:
        """One human line for the dashboard's provenance chip."""
        age = _humanize(self.age_s)
        if self.source is Tier.LIVE:
            return "live · just now"
        if self.source is Tier.CACHE:
            return f"cached · {age} old"
        return f"bundled snapshot · {age} old"

    def to_meta(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": str(self.source),
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

def _fmt_date(d: datetime) -> str:
    """Midnight writes as a bare date, anything else keeps its time — the format the
    committed snapshots already use (equity bars carry an 04:00 session stamp, crypto
    bars do not), so a regenerated file stays diffable against the one it replaces."""
    if (d.hour, d.minute, d.second, d.microsecond) == (0, 0, 0, 0):
        return d.strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _write_frame(path: Path, frame: Frame, fetched_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    cols = [c for c in _COLUMNS if c in frame]
    with gzip.open(tmp, "wt", newline="") as fh:
        fh.write(f"# fetched_at={fetched_at:.0f}\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Date", *cols])
        for i, d in enumerate(frame.dates):
            row = [_fmt_date(d)]
            for c in cols:
                v = frame.cols[c][i]
                # Prices to 4dp (sub-cent — lossless for anything this plugin
                # computes) and volume to int. A full float repr triples the
                # on-disk size for digits that only encode floating-point noise.
                if c == "Volume":
                    row.append(str(0 if isna(v) else int(v)))
                else:
                    row.append("" if isna(v) else f"{v:.4f}")
            w.writerow(row)
    tmp.replace(path)  # atomic — a half-written cache file is never read


def _parse_date(raw: str) -> datetime | None:
    """``fromisoformat`` covers both shapes the snapshots use. A row whose date will
    not parse is dropped rather than defaulted: a bar at the wrong instant is worse
    than a missing bar, because every window downstream would silently include it."""
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _read_frame(path: Path) -> tuple[Frame, float] | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", newline="") as fh:
            first = fh.readline()
            fetched_at = 0.0
            if first.startswith("# fetched_at="):
                fetched_at = float(first.split("=", 1)[1].strip())
            else:
                fh.seek(0)
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header:
                return None
            cols = [c for c in header[1:] if c in _COLUMNS]
            picks = [(header.index(c), c) for c in cols]
            dates: list[datetime] = []
            data: dict[str, list[float]] = {c: [] for c in cols}
            for row in reader:
                if not row or len(row) < len(header):
                    continue
                when = _parse_date(row[0])
                if when is None:
                    continue
                dates.append(when)
                for idx, name in picks:
                    cell = row[idx].strip()
                    data[name].append(NaN if not cell else float(cell))
        if not dates:
            return None
        return Frame(dates, data), fetched_at or path.stat().st_mtime
    except Exception:
        log.exception("[prototrader-finance] unreadable frame at %s", path)
        return None


def _cache_path(symbol: str, interval: str) -> Path:
    return store.cache_dir() / f"{slug(symbol)}.{interval}.csv.gz"


def _seed_path(symbol: str) -> Path:
    return SEED_DIR / f"{slug(symbol)}.1d.csv.gz"


def _slice(frame: Frame, period: str) -> Frame:
    n = _PERIOD_DAYS.get(period, _PERIOD_DAYS["2y"])
    return frame.tail(n)


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

def _fetch_live(symbol: str, period: str, interval: str, exchange: str) -> Frame:
    """Straight at the provider. Raises on anything short of real bars.

    Both providers are converted to a :class:`~numeric.Frame` right here, at the
    boundary. yfinance hands back a pandas DataFrame and depends on pandas to exist —
    but only *this* branch does, and it only runs when yfinance is installed, so the
    plugin still imports and renders its bundled snapshot on a host that has neither.
    That is the whole reason the seam is at the fetch and not deeper in.
    """
    if "/" in symbol:
        import ccxt

        ex = getattr(ccxt, exchange.lower())({"enableRateLimit": True})
        tf = interval if interval in ("1m", "5m", "15m", "1h", "4h", "1d", "1w") else "1d"
        raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=min(_PERIOD_DAYS.get(period, 504), 1000))
        if not raw:
            raise RuntimeError(f"no data for {symbol!r} @ {exchange}")
        # ccxt yields plain lists: [ms, open, high, low, close, volume].
        dates = [datetime.utcfromtimestamp(row[0] / 1000.0) for row in raw]
        return Frame(dates, {c: [row[i + 1] for row in raw] for i, c in enumerate(_COLUMNS)})

    import yfinance as yf

    df = yf.Ticker(symbol).history(period=period, interval=interval)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {symbol!r}")
    index = df.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    dates = [d.to_pydatetime() for d in index]
    return Frame(dates, {c: df[c].tolist() for c in _COLUMNS})


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

    if prefer == "cache":
        # Cache, then the bundled snapshot — and only then the network. Checking
        # the cache alone was not enough: on a CLEAN INSTALL there is no cache, so
        # every "don't block the paint" read fell through to a live fetch. That is
        # exactly the state a first demo is in, so the one case the seed exists for
        # was the one case it wasn't reached.
        if cached is not None:
            return Bars(_slice(cached[0], period), sym, Tier.CACHE, cached[1])
        seeded = _read_frame(_seed_path(sym))
        if seeded is not None:
            return Bars(_slice(seeded[0], period), sym, Tier.SEED, seeded[1])

    try:
        # Fetch the superset, cache it whole, hand back the requested slice.
        df = _fetch_live(sym, CACHE_PERIOD, interval, exchange)
        now = time.time()
        try:
            _write_frame(_cache_path(sym, interval), df, now)
        except Exception:
            log.exception("[prototrader-finance] could not cache %s", sym)
        return Bars(_slice(df, period), sym, Tier.LIVE, now)
    except Exception as exc:
        note = f"live fetch failed ({type(exc).__name__}: {exc})"
        log.warning("[prototrader-finance] %s for %s — falling back", note, sym)

    if cached is not None:
        return Bars(_slice(cached[0], period), sym, Tier.CACHE, cached[1], note)

    seeded = _read_frame(_seed_path(sym))
    if seeded is not None:
        return Bars(_slice(seeded[0], period), sym, Tier.SEED, seeded[1], note)

    raise RuntimeError(
        f"no data for {sym!r} — live fetch failed and neither the cache nor the "
        f"bundled snapshot covers it. Seeded symbols: {', '.join(seed_universe()) or 'none'}"
    )


def is_fresh(symbol: str, interval: str = "1d") -> bool:
    """True when the cached frame is new enough that refetching would be waste."""
    hit = _read_frame(_cache_path(symbol, interval))
    return hit is not None and (time.time() - hit[1]) < CACHE_TTL_S


def warm(symbols: list[str], period: str = CACHE_PERIOD) -> int:
    """Refresh the cache for `symbols`, skipping any still-fresh. Returns the count.

    Paints deliberately never go live, so without a warm a networked host would
    keep serving the bundled snapshot until someone pressed Refresh. This is the
    other half of that trade: fetch on the lifecycle events instead of on the paint.

    Skipping fresh symbols is what keeps it from becoming a wake-storm — a laptop
    opened five times an hour would otherwise refetch the whole universe each time.
    """
    n = 0
    for sym in symbols:
        if is_fresh(sym):
            continue
        try:
            if bars(sym, period, prefer="live").source is Tier.LIVE:
                n += 1
        except Exception:
            continue
    return n


def panel(
    symbols: list[str],
    period: str = "3y",
    *,
    prefer: str = "live",
) -> tuple[Frame, list[dict]]:
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
    close = Frame.from_columns(frames).drop_all_nan_rows().ffill()
    return close, meta
