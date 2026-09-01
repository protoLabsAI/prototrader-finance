"""pandas as an oracle: every operation in ``numeric.py`` must still agree with it.

The plugin dropped pandas as a *runtime* dependency because a frozen desktop host
cannot install one imported in-process. The risk that trade creates is numeric drift —
a rolling window off by one bar, a ddof silently changed from 1 to 0, an EWM that
reseeds on a NaN — none of which any behavioural test would notice, because they all
produce plausible Sharpe ratios.

So pandas stays a **dev** dependency and this file is the reason: it runs the real
thing beside the replacement on the bundled seed data, and fails on any disagreement
past 1e-9. That is the difference between claiming the numbers did not move and
measuring it. When this file and pandas disagree, ``numeric.py`` is wrong.

The one deliberate exception is documented at ``test_bootstrap_rng_is_not_pandas_parity``
below: the block bootstrap draws from a different generator now, and no amount of care
reproduces numpy's PCG64 stream from the standard library.
"""

from __future__ import annotations

import gzip
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import load

ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "seed"
RTOL = 1e-9


@pytest.fixture(scope="module")
def num():
    return load("numeric")


def _seed_frame(symbol: str = "SPY") -> pd.DataFrame:
    """Real bars, not synthetic ones: the seed carries genuine holidays, splits and
    flat days, which is where window and NaN semantics actually differ."""
    path = SEED_DIR / f"{symbol}.1d.csv.gz"
    if not path.is_file():
        pytest.skip(f"no bundled snapshot for {symbol}")
    with gzip.open(path, "rt") as fh:
        first = fh.readline()
        if not first.startswith("# fetched_at="):
            fh.seek(0)
        return pd.read_csv(fh, index_col=0, parse_dates=True)


def _close(num, df: pd.DataFrame):
    return num.Series(list(df.index.to_pydatetime()), df["Close"].tolist())


def assert_same(ours, theirs: pd.Series, what: str, rtol: float = RTOL) -> None:
    """Elementwise agreement including WHERE the NaNs are.

    NaN placement is checked first and separately: a warmup window that produced a
    number where pandas produces NaN is the single most likely way this rewrite goes
    wrong, and comparing only the finite values would step straight over it.
    """
    mine = ours.tolist() if hasattr(ours, "tolist") else list(ours)
    yours = theirs.tolist()
    assert len(mine) == len(yours), f"{what}: length {len(mine)} vs {len(yours)}"
    mine_na = [i for i, v in enumerate(mine) if v is None or v != v]
    yours_na = [i for i, v in enumerate(yours) if v != v]
    assert mine_na == yours_na, (
        f"{what}: NaN positions differ — ours {mine_na[:6]}… theirs {yours_na[:6]}…"
    )
    for i, (a, b) in enumerate(zip(mine, yours)):
        if b != b:
            continue
        assert a == pytest.approx(b, rel=rtol, abs=1e-12), f"{what}: row {i} — {a} vs {b}"


# ── Series transforms ────────────────────────────────────────────────────────


@pytest.mark.parametrize("n", [1, 2, 21, 252])
def test_shift_matches_pandas(num, n):
    df = _seed_frame()
    s, c = _close(num, df), df["Close"]
    assert_same(s.shift(n), c.shift(n), f"shift({n})")
    assert_same(s.shift(-n), c.shift(-n), f"shift(-{n})")


def test_diff_and_pct_change_match_pandas(num):
    df = _seed_frame()
    s, c = _close(num, df), df["Close"]
    assert_same(s.diff(), c.diff(), "diff")
    assert_same(s.pct_change(), c.pct_change(fill_method=None), "pct_change")
    assert_same(s.pct_change(21), c.pct_change(21, fill_method=None), "pct_change(21)")


@pytest.mark.parametrize("n", [2, 5, 20, 21, 60, 200])
def test_rolling_windows_match_pandas(num, n):
    """Includes ddof=1 on the deviation — pandas' default, and the one the Sharpe
    denominator and the low-vol factor both depend on."""
    df = _seed_frame()
    s, c = _close(num, df), df["Close"]
    assert_same(s.rolling_mean(n), c.rolling(n).mean(), f"rolling_mean({n})")
    # pandas accumulates rolling variance online (Welford); this is the two-pass
    # form, which is at least as accurate. They agree to ~1e-9 relative and the
    # residue is float noise, so the deviation check gets a looser tolerance —
    # still tighter by six orders of magnitude than a wrong ddof or a window that
    # is off by a bar, which is what this assertion is actually for.
    assert_same(s.rolling_std(n), c.rolling(n).std(), f"rolling_std({n})", rtol=1e-7)
    assert_same(s.rolling_max(n), c.rolling(n).max(), f"rolling_max({n})")
    assert_same(s.rolling_min(n), c.rolling(n).min(), f"rolling_min({n})")


@pytest.mark.parametrize("period", [14, 9, 21])
def test_ewm_matches_pandas_including_the_leading_nan(num, period):
    """RSI feeds ``diff()`` into the EWM, so the input always starts with a NaN —
    exactly the case where ``adjust``/``ignore_na`` choices diverge."""
    df = _seed_frame()
    d = df["Close"].diff()
    ours = _close(num, df).diff()
    alpha = 1 / period
    assert_same(ours.ewm_mean(alpha), d.ewm(alpha=alpha, adjust=False).mean(), f"ewm({period})")
    up_ours = ours.clip(lower=0).ewm_mean(alpha)
    up_theirs = d.clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    assert_same(up_ours, up_theirs, f"ewm of clipped ({period})")


def test_cumprod_cummax_and_fills_match_pandas(num):
    df = _seed_frame()
    r = df["Close"].pct_change(fill_method=None).fillna(0.0)
    ours = _close(num, df).pct_change().fillna(0.0)
    assert_same((ours + 1.0).cumprod(), (1 + r).cumprod(), "cumprod")
    assert_same((ours + 1.0).cumprod().cummax(), (1 + r).cumprod().cummax(), "cummax")
    assert_same(_close(num, df).ffill(), df["Close"].ffill(), "ffill")


def test_clip_and_abs_match_pandas(num):
    df = _seed_frame()
    s, d = _close(num, df).diff(), df["Close"].diff()
    assert_same(s.clip(lower=0), d.clip(lower=0), "clip(lower=0)")
    assert_same(s.clip(upper=0), d.clip(upper=0), "clip(upper=0)")
    assert_same(s.abs(), d.abs(), "abs")
    assert_same(s.clip(-1, 1), d.clip(-1, 1), "clip(-1,1)")


def test_rank_matches_pandas_including_ties(num):
    """Ties are the point: a factor panel where several names share a value is where
    'average' and 'min' ranking part company, and Spearman is defined on the former."""
    values = [3.0, 1.0, 2.0, 3.0, 3.0, 1.0, 5.0]
    ours = num.Series(list(range(len(values))), values).rank()
    assert_same(ours, pd.Series(values).rank(), "rank with ties")


# ── reductions ───────────────────────────────────────────────────────────────


def test_reductions_match_pandas_with_each_ddof(num):
    """pandas' ``.std()`` is ddof=1 and numpy's is ddof=0. The engines used both —
    Sharpe from pandas, the IC information ratio from numpy — so both must survive."""
    df = _seed_frame()
    r = df["Close"].pct_change(fill_method=None).dropna()
    ours = _close(num, df).pct_change().dropna()
    assert ours.mean() == pytest.approx(r.mean(), rel=RTOL)
    assert ours.std(ddof=1) == pytest.approx(r.std(), rel=RTOL)
    assert ours.std(ddof=0) == pytest.approx(float(np.std(r.to_numpy())), rel=RTOL)
    assert ours.min() == pytest.approx(r.min(), rel=RTOL)
    assert ours.max() == pytest.approx(r.max(), rel=RTOL)
    assert ours.count() == int(r.count())


def test_correlations_match_numpy_and_pandas(num):
    df = _seed_frame()
    a = df["Close"].pct_change(fill_method=None).dropna().to_numpy()[:400]
    b = df["Volume"].pct_change(fill_method=None).dropna().to_numpy()[:400]
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    assert num.pearson(list(a), list(b)) == pytest.approx(float(np.corrcoef(a, b)[0, 1]), rel=RTOL)
    theirs = pd.Series(a).rank().corr(pd.Series(b).rank())
    assert num.spearman(list(a), list(b)) == pytest.approx(float(theirs), rel=RTOL)


@pytest.mark.parametrize("p", [5, 25, 50, 75, 95])
def test_percentile_matches_numpy(num, p):
    """Linear interpolation, numpy's default — bootstrap CI bounds are read off this."""
    df = _seed_frame()
    xs = df["Close"].pct_change(fill_method=None).dropna().tolist()
    assert num.percentile(xs, p) == pytest.approx(float(np.percentile(xs, p)), rel=RTOL)


# ── Frame / panel ────────────────────────────────────────────────────────────


def _panel(num, symbols=("SPY", "AAPL", "MSFT")):
    cols, series = {}, {}
    for sym in symbols:
        df = _seed_frame(sym)
        cols[sym] = df["Close"]
        series[sym] = num.Series(list(df.index.to_pydatetime()), df["Close"].tolist())
    return num.Frame.from_columns(series), pd.DataFrame(cols)


def test_panel_alignment_matches_pandas(num):
    """Ragged symbol histories align onto the union of dates, NaN where absent —
    the same thing ``pd.DataFrame(dict_of_series)`` does."""
    ours, theirs = _panel(num)
    assert ours.columns == list(theirs.columns)
    assert len(ours) == len(theirs)
    for col in theirs.columns:
        assert_same(ours[col], theirs[col], f"panel column {col}")


def test_panel_transforms_match_pandas(num):
    ours, theirs = _panel(num)
    ours, theirs = ours.drop_all_nan_rows().ffill(), theirs.dropna(how="all").ffill()
    for col in theirs.columns:
        assert_same(ours.pct_change()[col], theirs.pct_change(fill_method=None)[col], f"pct_change {col}")
        assert_same(ours.shift(21)[col], theirs.shift(21)[col], f"shift {col}")
        assert_same(ours.rolling_std(21)[col], theirs.rolling(21).std()[col], f"rolling_std {col}")
        assert_same(ours.rolling_mean(200)[col], theirs.rolling(200).mean()[col], f"rolling_mean {col}")


def test_panel_cross_section_matches_a_pandas_row(num):
    """``.loc[date]`` — the per-rebalance slice the factor IC is computed over."""
    ours, theirs = _panel(num)
    ours, theirs = ours.drop_all_nan_rows().ffill(), theirs.dropna(how="all").ffill()
    date = theirs.index[len(theirs) // 2]
    row = ours.row_at(date.to_pydatetime())
    for col, value in theirs.loc[date].items():
        mine = row[col]
        if value != value:
            assert mine != mine, f"{col}: expected NaN"
        else:
            assert mine == pytest.approx(value, rel=RTOL), col


# ── engines end to end ───────────────────────────────────────────────────────


def test_backtest_metrics_match_a_pandas_reimplementation(num):
    """The engine's headline numbers, recomputed independently in pandas.

    Written against the metric definitions rather than against the engine's own
    helpers, so it is a genuine second opinion — a shared bug would have to occur
    twice, in two libraries, the same way.
    """
    engine = load("backtest.engine")
    df = _seed_frame()
    frame = num.Frame(
        list(df.index.to_pydatetime()),
        {c: df[c].tolist() for c in ("Open", "High", "Low", "Close", "Volume")},
    )
    pos = engine.signals(frame, "ma_cross", {"fast": 20, "slow": 50})
    sim = engine.simulate(frame, pos, cost_bps=5.0, slippage_bps=2.0)
    m = engine.metrics(sim, frame.index)

    c = df["Close"]
    p_fast, p_slow = c.rolling(20).mean(), c.rolling(50).mean()
    p_pos = (p_fast > p_slow).astype(float).fillna(0.0).clip(-1, 1)
    p_ret = c.pct_change(fill_method=None).fillna(0.0)
    p_held = p_pos.shift(1).fillna(0.0)
    p_turn = p_held.diff().abs().fillna(p_held.abs())
    p_strat = p_held * p_ret - p_turn * (7.0 / 1e4)
    eq = (1 + p_strat).cumprod()

    span_years = (df.index[-1] - df.index[0]).days / 365.25
    ppy = max(1.0, len(df) / span_years)
    assert m["total_return"] == pytest.approx(float(eq.iloc[-1]) - 1.0, rel=1e-9)
    assert m["sharpe"] == pytest.approx(float(p_strat.mean() / p_strat.std() * math.sqrt(ppy)), rel=1e-9)
    assert m["vol"] == pytest.approx(float(p_strat.std() * math.sqrt(ppy)), rel=1e-9)
    assert m["max_dd"] == pytest.approx(float(((eq / eq.cummax()) - 1).min()), rel=1e-9)
    assert m["exposure"] == pytest.approx(float((p_held != 0).mean()), rel=1e-9)
    assert m["trades"] == int(((p_held > 0) & (p_held.shift(1) <= 0)).sum())


def test_factor_values_match_a_pandas_reimplementation(num):
    """Factor construction is where an off-by-one shift hides: every definition here
    is a lag, and a factor accidentally reading the present is the classic silent
    look-ahead that makes a dead signal look alive."""
    fe = load("factors.engine")
    ours, theirs = _panel(num, ("SPY", "AAPL", "MSFT", "AMZN"))
    ours, theirs = ours.drop_all_nan_rows().ffill(), theirs.dropna(how="all").ffill()

    cases = {
        "momentum_12_1": theirs.shift(21) / theirs.shift(252) - 1,
        "reversal_1m": -(theirs / theirs.shift(21) - 1),
        "low_vol": -(theirs.pct_change(fill_method=None).rolling(21).std()),
        "trend_200d": theirs / theirs.rolling(200).mean() - 1,
    }
    for factor, expected in cases.items():
        got = fe.compute_factor(ours, factor)
        for col in theirs.columns:
            assert_same(got[col], expected[col], f"{factor}/{col}")


def test_bootstrap_rng_is_not_pandas_parity(num):
    """The one place the numbers legitimately move, stated rather than hidden.

    The block bootstrap resampled with ``numpy.random.default_rng`` (PCG64). The
    standard library cannot reproduce that stream, so the confidence interval a given
    seed produces is different now. It is still a bootstrap CI of the same statistic
    over the same returns, so what must hold is the property, not the digits: the
    interval brackets the point estimate and is reproducible for a fixed seed.
    """
    engine = load("backtest.engine")
    df = _seed_frame()
    frame = num.Frame(
        list(df.index.to_pydatetime()),
        {c: df[c].tolist() for c in ("Open", "High", "Low", "Close", "Volume")},
    )
    sim = engine.simulate(frame, engine.signals(frame, "buy_hold", {}), 0, 0)
    a = engine.bootstrap_ci(sim, frame.index, n_boot=200, seed=7)
    b = engine.bootstrap_ci(sim, frame.index, n_boot=200, seed=7)
    assert a == b, "a fixed seed must still give a reproducible interval"
    lo, hi = a["sharpe_ci"]
    assert lo < hi
    point = engine.metrics(sim, frame.index)["sharpe"]
    assert lo <= point <= hi, "the interval must bracket the point estimate it describes"
