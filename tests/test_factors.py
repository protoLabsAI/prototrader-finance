"""Offline tests for the factor engine — compute + IC verdict on synthetic data."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from conftest import load

# No pandas/numpy here on purpose — see the note in tests/test_marketdata.py.


def _engine():
    return load("factors.engine")


def _mean_reverting_panel(n_tickers=12, n_days=900, seed=3):
    """A panel where each name is an Ornstein–Uhlenbeck process around 100 — prices
    revert, so short-term reversal (buy the recent loser) genuinely predicts."""
    numeric = load("numeric")
    rng = random.Random(seed)
    day = datetime(2022, 1, 1)
    dates = [day + timedelta(days=i) for i in range(n_days)]
    cols = {}
    theta = 0.05  # daily pull toward the mean
    for k in range(n_tickers):
        p = [100.0]
        for _ in range(1, n_days):
            p.append(p[-1] + theta * (100.0 - p[-1]) + rng.gauss(0, 1.5))
        cols[f"T{k}"] = [max(v, 1.0) for v in p]
    return numeric.Frame(dates, cols)


def test_compute_each_factor_shape():
    e = _engine()
    close = _mean_reverting_panel()
    for f in ("momentum_12_1", "reversal_1m", "low_vol", "trend_200d"):
        fac = e.compute_factor(close, f)
        assert len(fac) == len(close) and fac.columns == close.columns


def test_unknown_factor_raises():
    e = _engine()
    with pytest.raises(ValueError):
        e.compute_factor(_mean_reverting_panel(), "nope")


def test_reversal_predicts_on_mean_reverting_data(monkeypatch):
    e = _engine()
    panel = _mean_reverting_panel()
    # Avoid the network: feed the synthetic panel to evaluate().
    monkeypatch.setattr(e, "fetch_panel", lambda tickers, period="3y", **kw: panel)
    r = e.evaluate("reversal_1m", universe=list(panel.columns), period="3y", horizon=5, step=10)
    assert "error" not in r
    # On genuinely mean-reverting data, short-term reversal should have +IC.
    assert r["mean_ic"] > 0
    assert r["verdict"] in ("alive", "weak")


def test_momentum_not_alive_on_mean_reverting_data(monkeypatch):
    """Sign-standardized: momentum is the wrong model for mean-reverting data, so
    it should not score 'alive' (positive consistent IC)."""
    e = _engine()
    panel = _mean_reverting_panel()
    monkeypatch.setattr(e, "fetch_panel", lambda tickers, period="3y", **kw: panel)
    r = e.evaluate("momentum_12_1", universe=list(panel.columns), period="3y", horizon=5, step=10)
    assert r.get("verdict") != "alive"
