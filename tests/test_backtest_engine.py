"""Offline tests for the backtest engine (no network) — correctness + no look-ahead."""

from __future__ import annotations

import importlib.util

import numpy as np
import pandas as pd
import pytest


def _engine():
    spec = importlib.util.spec_from_file_location(
        "bt_engine", "backtest/engine.py",
        submodule_search_locations=["backtest"],
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _synth(n=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    px = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    return pd.DataFrame({"Open": px, "High": px * 1.01, "Low": px * 0.99,
                         "Close": px, "Volume": 1e6}, index=idx)


def test_buy_hold_matches_price_change():
    e = _engine()
    df = _synth()
    sim = e.simulate(df, e.signals(df, "buy_hold", {}), cost_bps=0, slippage_bps=0)
    px_change = df["Close"].iloc[-1] / df["Close"].iloc[0] - 1
    # buy_hold strategy return ≈ price change (one entry, zero friction).
    assert abs(sim["equity"].iloc[-1] - 1 - px_change) < 1e-6
    assert abs(sim["bh_equity"].iloc[-1] - sim["equity"].iloc[-1]) < 1e-6


def test_no_lookahead():
    e = _engine()
    df = _synth()
    pos = e.signals(df, "ma_cross", {"fast": 5, "slow": 20})
    sim = e.simulate(df, pos, cost_bps=0, slippage_bps=0)
    # The position acting on bar t must be the signal from t-1 (shifted), never t.
    assert (sim["held"] == pos.shift(1).fillna(0.0)).all()


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
    sim = pd.DataFrame({"ret": [0.01], "held": [1.0], "turn": [1.0], "strat": [0.01]})
    m = e.metrics(sim, pd.date_range("2024-01-01", periods=1, freq="D"))
    assert m["vol"] == 0.0
    json.dumps(m, allow_nan=False)  # no NaN/inf anywhere


def test_negative_equity_metrics_are_json_safe():
    """Equity driven <= 0 must not yield NaN CAGR or a sub -100% drawdown — H2/L3."""
    import json
    e = _engine()
    sim = pd.DataFrame({"ret": [0.0, -2.0], "held": [1.0, 1.0],
                        "turn": [1.0, 0.0], "strat": [0.0, -2.0]})
    m = e.metrics(sim, pd.date_range("2024-01-01", periods=2, freq="D"))
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
    oos = e.metrics(sim.iloc[cut:], df.index[cut:])
    expected = df["Close"].iloc[-1] / df["Close"].iloc[cut - 1] - 1
    assert abs(oos["total_return"] - expected) < 1e-9  # boundary bar not zeroed
