"""Offline tests for the trade-journal analyzer (Slice 5)."""

from __future__ import annotations

import importlib.util


def _engine():
    spec = importlib.util.spec_from_file_location(
        "beh_engine", "behavioral/engine.py",
        submodule_search_locations=["behavioral"],
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# Classic trap: high win rate, but small quick wins and big slow losses.
_BIASED = """date,symbol,side,qty,price
2026-01-02,AAPL,buy,100,180
2026-01-04,AAPL,sell,100,183
2026-01-05,NVDA,buy,50,120
2026-01-06,NVDA,sell,50,122
2026-01-08,TSLA,buy,40,250
2026-02-20,TSLA,sell,40,210
2026-02-21,TSLA,buy,120,205
2026-03-15,TSLA,sell,120,180
2026-03-16,AAPL,buy,80,190
2026-03-18,AAPL,sell,80,192
"""


def test_round_trips_and_pnl():
    e = _engine()
    p = e.profile(_BIASED)
    assert p["trades"] == 5
    # 3 small wins + 2 big losses → high win rate but net negative.
    assert p["win_rate"] == 0.6
    assert p["total_pnl"] < 0
    assert p["profit_factor"] < 1.0


def test_detects_loss_aversion_and_asymmetry():
    e = _engine()
    p = e.profile(_BIASED)
    flags = " ".join(p["flags"]).lower()
    assert "loss aversion" in flags          # holds losers longer
    assert "asymmetric" in flags             # avg loss >> avg win
    assert p["avg_loss_hold_days"] > p["avg_win_hold_days"]


def test_tolerant_columns_and_sides():
    e = _engine()
    csv = ("Ticker,Action,Shares,Fill Price,Trade Date\n"
           "msft,BOT,10,400,2026-01-02\n"
           "MSFT,SLD,10,420,2026-01-10\n")
    p = e.profile(csv)
    assert p["trades"] == 1
    assert p["total_pnl"] == (420 - 400) * 10


def test_no_trips_is_handled():
    e = _engine()
    p = e.profile("symbol,side,qty,price\nAAPL,buy,10,100\n")  # open, never closed
    assert "error" in p


def test_missing_columns_raises():
    e = _engine()
    import pytest
    with pytest.raises(ValueError):
        e.parse_fills("foo,bar\n1,2\n")


def test_no_loss_profit_factor_is_json_safe():
    """All-winners journal → profit_factor must be JSON-safe (None), never inf."""
    import json
    e = _engine()
    p = e.profile(
        "date,symbol,side,qty,price\n"
        "2026-01-02,AAPL,buy,10,100\n"
        "2026-01-03,AAPL,sell,10,110\n"
    )
    assert p["profit_factor"] is None  # was float("inf") — breaks strict JSON
    json.dumps(p, allow_nan=False)     # would raise on inf/nan


def test_unsorted_fills_are_sorted_before_pairing():
    """Rows out of chronological order (sell before its buy) must still pair as a
    long round-trip with a non-negative hold time (M4)."""
    e = _engine()
    p = e.profile(
        "date,symbol,side,qty,price\n"
        "2026-03-01,AAPL,sell,10,120\n"   # appears first in file order
        "2026-01-01,AAPL,buy,10,100\n"
    )
    assert p["trades"] == 1
    assert p["total_pnl"] == 200.0                     # long +$20/sh, not a short
    assert p["avg_win_hold_days"] == 59                # Jan 1 → Mar 1, never negative
