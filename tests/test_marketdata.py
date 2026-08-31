"""The three-tier read path — live → cache → bundled snapshot.

The point of these is the FALLBACK, not the happy path: a dashboard that goes
blank when the provider blinks is the failure this layer exists to prevent, and
a snapshot served silently as if it were live is the failure that would be worse.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from conftest import load


def _frame(n=30, start=100.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "Open": [start + i for i in range(n)],
            "High": [start + i + 1 for i in range(n)],
            "Low": [start + i - 1 for i in range(n)],
            "Close": [start + i + 0.5 for i in range(n)],
            "Volume": [1_000_000 + i for i in range(n)],
        },
        index=idx,
    )


# ── on-disk format ───────────────────────────────────────────────────────────

def test_frame_roundtrip_preserves_values_and_timestamp(isolated_home):
    md = load("marketdata")
    path = isolated_home / "rt.csv.gz"
    df = _frame()
    md._write_frame(path, df, 1_700_000_000.0)

    back, fetched_at = md._read_frame(path)
    assert fetched_at == 1_700_000_000.0
    assert list(back.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(back) == len(df)
    assert back["Close"].iloc[-1] == pytest.approx(df["Close"].iloc[-1], abs=1e-4)


def test_write_is_atomic(isolated_home):
    """A half-written cache file must never be readable — readers see old or new."""
    md = load("marketdata")
    path = isolated_home / "atomic.csv.gz"
    md._write_frame(path, _frame(), time.time())
    assert path.exists()
    assert not list(isolated_home.glob("*.tmp"))


def test_unreadable_frame_returns_none_not_raises(isolated_home):
    md = load("marketdata")
    bad = isolated_home / "corrupt.csv.gz"
    bad.write_bytes(b"not gzip at all")
    assert md._read_frame(bad) is None


def test_slug_normalizes_symbols():
    md = load("marketdata")
    assert md.slug("btc/usd") == "BTC-USD"
    assert md.slug(" spy ") == "SPY"


# ── the bundled snapshot ─────────────────────────────────────────────────────

def test_seed_manifest_describes_what_is_on_disk():
    md = load("marketdata")
    manifest = md.seed_manifest()
    assert manifest.get("symbols"), "the demo seed must ship a universe"
    for sym in manifest["symbols"]:
        assert md._seed_path(sym).is_file(), f"{sym} listed but not committed"


def test_every_committed_snapshot_is_listed():
    """The inverse: a snapshot on disk but absent from the manifest is invisible
    to seed_universe(), so the demo silently never offers it."""
    md = load("marketdata")
    on_disk = {p.name.split(".")[0] for p in md.SEED_DIR.glob("*.csv.gz")}
    assert on_disk == set(md.seed_universe())


def test_seed_serves_a_real_symbol_with_no_network(offline):
    md = offline
    b = md.bars("SPY", "1y")
    assert b.source == "seed"
    assert len(b.frame) > 200
    assert b.frame["Close"].iloc[-1] > 0


# ── the fallback chain ───────────────────────────────────────────────────────

def test_cache_preference_never_calls_the_provider(isolated_home, monkeypatch):
    md = load("marketdata")
    md._write_frame(md._cache_path("FAKE", "1d"), _frame(), time.time())

    calls = []
    monkeypatch.setattr(md, "_fetch_live", lambda *a, **k: calls.append(a) or _frame())
    b = md.bars("FAKE", "1mo", prefer="cache")
    assert b.source == "cache"
    assert calls == [], "prefer='cache' must not block a dashboard paint on the network"


def test_live_success_writes_through_to_cache(isolated_home, monkeypatch):
    md = load("marketdata")
    monkeypatch.setattr(md, "_fetch_live", lambda *a, **k: _frame())
    b = md.bars("FAKE", "1mo", prefer="live")
    assert b.source == "live"
    assert md._cache_path("FAKE", "1d").is_file(), "a live fetch must seed the cache"

    # ...and the cache now answers offline.
    monkeypatch.setattr(md, "_fetch_live", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert md.bars("FAKE", "1mo", prefer="live").source == "cache"


def test_falls_back_cache_then_seed(isolated_home, offline):
    md = offline
    assert md.bars("SPY", "1y").source == "seed"          # no cache yet
    md._write_frame(md._cache_path("SPY", "1d"), _frame(), time.time())
    assert md.bars("SPY", "1y").source == "cache"          # cache outranks seed


def test_unknown_symbol_offline_raises_with_the_universe(offline):
    md = offline
    with pytest.raises(RuntimeError) as exc:
        md.bars("NOSUCHTICKER")
    assert "NOSUCHTICKER" in str(exc.value)
    assert "SPY" in str(exc.value), "the error should name what IS available"


def test_period_slices_the_long_snapshot(offline):
    md = offline
    assert len(md.bars("SPY", "1y").frame) < len(md.bars("SPY", "5y").frame)
    assert len(md.bars("SPY", "1y").frame) <= 252


# ── provenance is part of the value ──────────────────────────────────────────

def test_provenance_labels_are_honest(isolated_home, monkeypatch):
    md = load("marketdata")
    monkeypatch.setattr(md, "_fetch_live", lambda *a, **k: _frame())
    assert md.bars("FAKE", "1mo").label() == "live · just now"

    old = md.Bars(_frame(), "FAKE", "seed", time.time() - 90 * 86_400)
    assert "bundled snapshot" in old.label() and "90d" in old.label()
    assert old.stale is True

    fresh_cache = md.Bars(_frame(), "FAKE", "cache", time.time() - 120)
    assert fresh_cache.label() == "cached · 2m old"
    assert fresh_cache.stale is False


def test_meta_is_json_serializable(offline):
    import json

    meta = offline.bars("SPY", "1y").to_meta()
    json.loads(json.dumps(meta))
    assert meta["source"] == "seed" and meta["rows"] > 0


# ── panels degrade one column at a time ──────────────────────────────────────

def test_panel_drops_a_dead_symbol_instead_of_failing(offline):
    close, meta = offline.panel(["SPY", "QQQ", "NOSUCHTICKER"], "1y")
    assert list(close.columns) == ["SPY", "QQQ"]
    assert {m["symbol"] for m in meta} == {"SPY", "QQQ", "NOSUCHTICKER"}
    assert next(m for m in meta if m["symbol"] == "NOSUCHTICKER")["source"] == "none"


def test_panel_raises_only_when_nothing_resolves(offline):
    with pytest.raises(RuntimeError, match="no data for any symbol"):
        offline.panel(["NOPE1", "NOPE2"], "1y")


def test_a_short_request_does_not_poison_a_long_one(isolated_home, monkeypatch):
    """The cache key is (symbol, interval) with no period, so a live fetch must
    always pull the SUPERSET window. Otherwise a "1y" read writes a 252-bar cache
    that a later "5y" read happily serves — `_slice` trims, it cannot extend, and
    the caller gets a fifth of the history it asked for with no error."""
    md = load("marketdata")
    asked = []

    def _fake(sym, period, interval, exchange):
        asked.append(period)
        return _frame(n=1400)

    monkeypatch.setattr(md, "_fetch_live", _fake)

    short = md.bars("FAKE", "1y", prefer="live")
    assert asked == [md.CACHE_PERIOD], "a live fetch must request the cache superset"
    assert len(short.frame) <= 252, "the caller still gets only the window it asked for"

    monkeypatch.setattr(md, "_fetch_live", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    long = md.bars("FAKE", "5y", prefer="cache")
    assert long.source == "cache"
    assert len(long.frame) > 252, "the cache held the superset, so 5y is still answerable"


def test_warm_skips_symbols_that_are_still_fresh(isolated_home, monkeypatch):
    """Paints never go live, so the lifecycle warm is the only thing refreshing the
    cache — but a laptop opened five times an hour must not refetch the universe
    each time."""
    md = load("marketdata")
    fetched = []
    monkeypatch.setattr(md, "_fetch_live", lambda s, *a, **k: fetched.append(s) or _frame())

    assert md.warm(["A", "B"]) == 2
    assert fetched == ["A", "B"]

    fetched.clear()
    assert md.warm(["A", "B"]) == 0, "a fresh cache must not be refetched"
    assert fetched == []


def test_warm_survives_a_dead_symbol(isolated_home, offline):
    """One unreachable name must not stop the rest of the universe warming."""
    assert offline.warm(["SPY", "NOSUCHTICKER"]) == 0  # offline: nothing refreshes, nothing raises
