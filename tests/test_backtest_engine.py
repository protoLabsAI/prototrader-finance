"""Offline tests for the backtest engine (no network) — correctness + no look-ahead."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from conftest import load

# No pandas/numpy here on purpose — see the note in tests/test_marketdata.py.


def _engine():
    return load("backtest.engine")


def _days(n, start=datetime(2024, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def _synth(n=400, seed=1):
    rng = random.Random(seed)
    px, level = [], 100.0
    for _ in range(n):
        level *= 1 + rng.gauss(0.0005, 0.01)
        px.append(level)
    return load("numeric").Frame(
        _days(n),
        {"Open": px, "High": [p * 1.01 for p in px], "Low": [p * 0.99 for p in px],
         "Close": px, "Volume": [1e6] * n},
    )


def _sim_frame(cols):
    """A hand-built sim frame for the degenerate-window guards below."""
    n = len(next(iter(cols.values())))
    return load("numeric").Frame(_days(n), cols)


def test_buy_hold_matches_price_change():
    e = _engine()
    df = _synth()
    sim = e.simulate(df, e.signals(df, "buy_hold", {}), cost_bps=0, slippage_bps=0)
    px_change = df["Close"].last() / df["Close"][0] - 1
    # buy_hold strategy return ≈ price change (one entry, zero friction).
    assert abs(sim["equity"].last() - 1 - px_change) < 1e-6
    assert abs(sim["bh_equity"].last() - sim["equity"].last()) < 1e-6


def test_no_lookahead():
    e = _engine()
    df = _synth()
    pos = e.signals(df, "ma_cross", {"fast": 5, "slow": 20})
    sim = e.simulate(df, pos, cost_bps=0, slippage_bps=0)
    # The position acting on bar t must be the signal from t-1 (shifted), never t.
    assert sim["held"].tolist() == pos.shift(1).fillna(0.0).tolist()


def test_metrics_shape_and_drawdown():
    e = _engine()
    df = _synth()
    sim = e.simulate(df, e.signals(df, "breakout", {"lookback": 20}), 5, 2)
    m = e.metrics(sim, df.index)
    for k in ("total_return", "cagr", "sharpe", "sortino", "max_dd", "trades", "exposure"):
        assert k in m
    assert m["max_dd"] <= 0.0          # drawdown is non-positive
    assert 0.0 <= m["exposure"] <= 1.0


def test_friction_reduces_return():
    e = _engine()
    df = _synth()
    pos = e.signals(df, "ma_cross", {"fast": 5, "slow": 20})
    free = e.metrics(e.simulate(df, pos, 0, 0), df.index)["total_return"]
    costly = e.metrics(e.simulate(df, pos, 20, 10), df.index)["total_return"]
    assert costly <= free  # frictions can only hurt


def test_unknown_strategy_raises():
    e = _engine()
    with pytest.raises(ValueError):
        e.signals(_synth(), "nope", {})


def test_single_bar_metrics_are_finite():
    """A one-bar window must not leak NaN vol (std is undefined for n=1) — M1."""
    import json
    e = _engine()
    sim = _sim_frame({"ret": [0.01], "held": [1.0], "turn": [1.0], "strat": [0.01]})
    m = e.metrics(sim, _days(1))
    assert m["vol"] == 0.0
    json.dumps(m, allow_nan=False)  # no NaN/inf anywhere


def test_negative_equity_metrics_are_json_safe():
    """Equity driven <= 0 must not yield NaN CAGR or a sub -100% drawdown — H2/L3."""
    import json
    e = _engine()
    sim = _sim_frame({"ret": [0.0, -2.0], "held": [1.0, 1.0],
                      "turn": [1.0, 0.0], "strat": [0.0, -2.0]})
    m = e.metrics(sim, _days(2))
    assert m["cagr"] == 0.0          # guarded, not NaN (negative-base fractional power)
    assert m["max_dd"] >= -1.0       # floored at total ruin
    json.dumps(m, allow_nan=False)


def test_oos_slice_preserves_boundary_return():
    """Slicing the full sim (not re-simulating the slice) keeps the first OOS bar's
    return — so OOS total reconciles with the price move across the cut (M2)."""
    e = _engine()
    df = _synth(n=100)
    sim = e.simulate(df, e.signals(df, "buy_hold", {}), cost_bps=0, slippage_bps=0)
    cut = 70
    oos = e.metrics(sim.slice_rows(cut, None), df.index[cut:])
    expected = df["Close"].last() / df["Close"][cut - 1] - 1
    assert abs(oos["total_return"] - expected) < 1e-9  # boundary bar not zeroed
