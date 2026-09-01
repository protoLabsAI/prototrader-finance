"""A very small time-series core — the reason this plugin needs no dependencies.

protoAgent's desktop build is a frozen PyInstaller app. A plugin dep declared
``scope: host`` is imported *in this process*, and a frozen host has no way to
install into its own site-packages — the managed Python runtime that ``install-deps``
targets serves ``execute_code`` children out of a separate one. So a plugin that
imports pandas in-process cannot be installed on the desktop at all: the installer
refuses up front rather than passing the gate and dying at the first tool call.

pandas earned its place while this was a source-install-only plugin, and then stopped:
what the engines actually asked of it was a one-dimensional rolling window, a shift, a
cumulative product and a correlation — over a few hundred rows of daily bars. None of
that needs a dataframe library, and paying a ~70 MB frozen dependency (plus an install
step the operator has to know about) for it is the wrong trade for a plugin whose job
is to be the SDK's poster child.

So: :class:`Series` for one column, :class:`Frame` for several sharing a date index,
and the handful of statistics the engines compute. Semantics follow pandas deliberately
and exactly — NaN skipping, ``min_periods``, ddof — because the point is that the
numbers do not move. ``tests/test_numeric_parity.py`` holds pandas to that: it runs
every operation here against the real thing on the bundled seed data and fails on a
disagreement past 1e-9. pandas stays a *dev* dependency for exactly that reason.

What this is NOT: a dataframe library. There is no join, no groupby, no resample, no
reindex-by-label beyond the one alignment the factor panel needs. Reach for one of
those and the honest move is to add the operation here, with a parity test, rather
than to widen this into a small pandas.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime

NaN = float("nan")


def isna(x: object) -> bool:
    """NaN-or-missing, the way pandas counts it. ``x != x`` is only true for NaN."""
    return x is None or (isinstance(x, float) and x != x)


def _f(x: object) -> float:
    """Coerce to float, mapping anything unparseable to NaN rather than raising —
    a single bad cell in a cached CSV must cost that cell, not the whole read."""
    if x is None or x == "":
        return NaN
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return NaN


# ── statistics ───────────────────────────────────────────────────────────────
# Each takes an already-NaN-free sequence; the Series methods do the skipping.
# ddof is explicit at every call because the engines genuinely need both: pandas'
# .std() is the sample deviation (ddof=1) while numpy's is the population one
# (ddof=0), and the original code used each in different places. Defaulting it
# here would silently pick a side and move published Sharpe and IR numbers.


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else NaN


def stdev(xs: Sequence[float], ddof: int = 1) -> float:
    n = len(xs)
    if n - ddof <= 0:
        return NaN
    mu = sum(xs) / n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - ddof))


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson r. ddof cancels in the ratio, so this matches numpy and pandas alike."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return NaN
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return NaN
    return sxy / math.sqrt(sxx * syy)


def rank(xs: Sequence[float]) -> list[float]:
    """1-based ranks, ties averaged — pandas' ``Series.rank()`` default method.

    Ties matter here rather than being an edge case: a rank-IC over a universe where
    several names share a factor value is exactly where 'min' and 'average' ranking
    disagree, and Spearman is defined on the averaged one.
    """
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0  # average of the 1-based positions i..j
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation — Pearson on the averaged ranks."""
    return pearson(rank(xs), rank(ys))


def percentile(xs: Sequence[float], p: float) -> float:
    """The ``p``-th percentile with linear interpolation — ``numpy.percentile``'s
    default method, so bootstrap confidence bounds keep their published values."""
    if not xs:
        return NaN
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * (p / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[int(pos)]
    return s[lo] * (hi - pos) + s[hi] * (pos - lo)


# ── one column ───────────────────────────────────────────────────────────────


class Series:
    """A float column carrying its own dates.

    Missing observations are NaN rather than a mask, matching what the CSV cache and
    a short-history ticker actually produce. Reductions skip them (pandas' ``skipna``
    default); window operations do not, because a window containing a gap has not
    seen ``n`` observations and pandas reports NaN there — a rolling mean that
    quietly averaged 19 of 20 bars would read as a real signal.
    """

    __slots__ = ("dates", "values")

    def __init__(self, dates: Sequence[datetime], values: Sequence[float]):
        if len(dates) != len(values):
            raise ValueError(f"dates/values length mismatch: {len(dates)} vs {len(values)}")
        self.dates: list[datetime] = list(dates)
        self.values: list[float] = [_f(v) for v in values]

    # -- basics --------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self) -> Iterator[float]:
        return iter(self.values)

    def __repr__(self) -> str:
        return f"Series({len(self)} rows)"

    def __getitem__(self, key: int | slice) -> float | Series:
        if isinstance(key, slice):
            return Series(self.dates[key], self.values[key])
        return self.values[key]

    def tolist(self) -> list[float]:
        return list(self.values)

    def last(self, default: float = NaN) -> float:
        """The final value — the price a mark, a quote and an equity readout all want."""
        return self.values[-1] if self.values else default

    def tail(self, n: int) -> Series:
        return self if n >= len(self) else Series(self.dates[-n:], self.values[-n:])

    def dropna(self) -> Series:
        keep = [i for i, v in enumerate(self.values) if not isna(v)]
        return Series([self.dates[i] for i in keep], [self.values[i] for i in keep])

    def notna(self) -> list[bool]:
        return [not isna(v) for v in self.values]

    def at(self, date: datetime, default: float = NaN) -> float:
        """Value on ``date``. Linear scan — the panels here are a few hundred rows,
        and an index would be one more thing to keep in sync."""
        try:
            return self.values[self.dates.index(date)]
        except ValueError:
            return default

    # -- elementwise ---------------------------------------------------------

    def map(self, fn: Callable[[float], float]) -> Series:
        return Series(self.dates, [NaN if isna(v) else fn(v) for v in self.values])

    def _combine(self, other: Series | float, fn: Callable[[float, float], float]) -> Series:
        if isinstance(other, Series):
            if len(other) != len(self):
                raise ValueError("series length mismatch")
            rhs = other.values
        else:
            rhs = [float(other)] * len(self)
        out = []
        for a, b in zip(self.values, rhs):
            out.append(NaN if isna(a) or isna(b) else fn(a, b))
        return Series(self.dates, out)

    def __add__(self, o): return self._combine(o, lambda a, b: a + b)
    def __radd__(self, o): return self._combine(o, lambda a, b: b + a)
    def __sub__(self, o): return self._combine(o, lambda a, b: a - b)
    def __rsub__(self, o): return self._combine(o, lambda a, b: b - a)
    def __mul__(self, o): return self._combine(o, lambda a, b: a * b)
    def __rmul__(self, o): return self._combine(o, lambda a, b: b * a)

    def __truediv__(self, o):
        # Division by zero is NaN, not ZeroDivisionError: it means "undefined here",
        # and one flat bar must not take down a whole study.
        return self._combine(o, lambda a, b: a / b if b else NaN)

    def __rtruediv__(self, o):
        return self._combine(o, lambda a, b: b / a if a else NaN)

    def __neg__(self) -> Series:
        return self.map(lambda v: -v)

    def abs(self) -> Series:
        return self.map(abs)

    def clip(self, lower: float | None = None, upper: float | None = None) -> Series:
        def c(v: float) -> float:
            if lower is not None:
                v = max(v, lower)
            if upper is not None:
                v = min(v, upper)
            return v

        return self.map(c)

    def replace_zero_with_nan(self) -> Series:
        return self.map(lambda v: NaN if v == 0 else v)

    # -- transforms ----------------------------------------------------------

    def shift(self, n: int = 1) -> Series:
        """Move values forward by ``n`` rows, padding the vacated end with NaN.
        Negative ``n`` shifts backwards (a forward return)."""
        if n == 0:
            return Series(self.dates, self.values)
        size = len(self)
        if abs(n) >= size:
            return Series(self.dates, [NaN] * size)
        if n > 0:
            return Series(self.dates, [NaN] * n + self.values[:-n])
        return Series(self.dates, self.values[-n:] + [NaN] * (-n))

    def diff(self, n: int = 1) -> Series:
        return self - self.shift(n)

    def pct_change(self, n: int = 1) -> Series:
        prev = self.shift(n)
        return self._combine(prev, lambda a, b: (a / b - 1.0) if b else NaN)

    def ffill(self) -> Series:
        out, carry = [], NaN
        for v in self.values:
            if not isna(v):
                carry = v
            out.append(carry)
        return Series(self.dates, out)

    def fillna(self, value: Series | float) -> Series:
        """Fill missing values from a scalar, or positionally from another Series.

        The Series form is not a convenience: ``turn.fillna(held.abs())`` seeds the
        first bar's turnover from the position opened on it, and passing a Series to
        a scalar-only fill would coerce to NaN — leaving the opening trade uncharged
        and every bootstrap total silently NaN. pandas accepts both, so this must too.
        """
        if isinstance(value, Series):
            if len(value) != len(self):
                raise ValueError("fillna series length mismatch")
            return Series(self.dates, [f if isna(v) else v for v, f in zip(self.values, value.values)])
        return Series(self.dates, [value if isna(v) else v for v in self.values])

    def cumprod(self) -> Series:
        """Running product, NaN-transparent: a NaN stays NaN in place and the
        product carries on — pandas' ``skipna=True`` default."""
        out, acc = [], 1.0
        for v in self.values:
            if isna(v):
                out.append(NaN)
                continue
            acc *= v
            out.append(acc)
        return Series(self.dates, out)

    def cummax(self) -> Series:
        out, acc = [], NaN
        for v in self.values:
            if isna(v):
                out.append(NaN)
                continue
            acc = v if isna(acc) else max(acc, v)
            out.append(acc)
        return Series(self.dates, out)

    def _rolling(self, n: int, fn: Callable[[list[float]], float]) -> Series:
        """Window of ``n`` with ``min_periods=n`` — the pandas default. A window
        holding any NaN is NaN, which is the whole reason a warmup period shows as
        blank rather than as a value computed from fewer bars than it claims."""
        out: list[float] = []
        for i in range(len(self)):
            if i + 1 < n:
                out.append(NaN)
                continue
            window = self.values[i + 1 - n : i + 1]
            out.append(NaN if any(isna(v) for v in window) else fn(window))
        return Series(self.dates, out)

    def rolling_mean(self, n: int) -> Series:
        return self._rolling(n, mean)

    def rolling_std(self, n: int, ddof: int = 1) -> Series:
        return self._rolling(n, lambda w: stdev(w, ddof))

    def rolling_max(self, n: int) -> Series:
        return self._rolling(n, max)

    def rolling_min(self, n: int) -> Series:
        return self._rolling(n, min)

    def ewm_mean(self, alpha: float) -> Series:
        """Exponentially weighted mean, ``adjust=False`` — the recursive form
        ``y[i] = a·x[i] + (1-a)·y[i-1]`` seeded by the first observation.

        NaN handling follows pandas' ``ignore_na=False``: output is NaN until the
        first real observation, and a NaN input afterwards holds the previous output
        rather than resetting it. That matters for RSI, whose input is a ``diff()``
        and therefore always starts with a NaN.
        """
        out: list[float] = []
        prev = NaN
        for v in self.values:
            if isna(v):
                out.append(prev)
                continue
            prev = v if isna(prev) else alpha * v + (1 - alpha) * prev
            out.append(prev)
        return Series(self.dates, out)

    def rank(self) -> Series:
        """Ranks over the non-NaN values, NaN kept in place (pandas' default)."""
        idx = [i for i, v in enumerate(self.values) if not isna(v)]
        ranks = rank([self.values[i] for i in idx])
        out = [NaN] * len(self)
        for i, r in zip(idx, ranks):
            out[i] = r
        return Series(self.dates, out)

    def where(self, mask: Sequence[bool], value: float) -> Series:
        """Set positions where ``mask`` is true to ``value`` — the assignment form
        ``pos[r < lo] = 1.0`` used to build a signal series."""
        out = list(self.values)
        for i, m in enumerate(mask):
            if m:
                out[i] = value
        return Series(self.dates, out)

    # -- comparisons produce masks, not series -------------------------------

    def lt(self, other: Series | float) -> list[bool]:
        return self._mask(other, lambda a, b: a < b)

    def gt(self, other: Series | float) -> list[bool]:
        return self._mask(other, lambda a, b: a > b)

    def le(self, other: Series | float) -> list[bool]:
        return self._mask(other, lambda a, b: a <= b)

    def ge(self, other: Series | float) -> list[bool]:
        return self._mask(other, lambda a, b: a >= b)

    def ne(self, other: Series | float) -> list[bool]:
        return self._mask(other, lambda a, b: a != b)

    def eq(self, other: Series | float) -> list[bool]:
        return self._mask(other, lambda a, b: a == b)

    def _mask(self, other: Series | float, fn: Callable[[float, float], bool]) -> list[bool]:
        rhs = other.values if isinstance(other, Series) else [float(other)] * len(self)
        # A comparison against NaN is False in pandas too — never a silent True.
        return [(not isna(a) and not isna(b) and fn(a, b)) for a, b in zip(self.values, rhs)]

    def select(self, mask: Sequence[bool]) -> Series:
        keep = [i for i, m in enumerate(mask) if m]
        return Series([self.dates[i] for i in keep], [self.values[i] for i in keep])

    # -- reductions (NaN-skipping, like pandas) ------------------------------

    def _clean(self) -> list[float]:
        return [v for v in self.values if not isna(v)]

    def count(self) -> int:
        return len(self._clean())

    def mean(self) -> float:
        return mean(self._clean())

    def std(self, ddof: int = 1) -> float:
        return stdev(self._clean(), ddof)

    def sum(self) -> float:
        return sum(self._clean())

    def min(self) -> float:
        c = self._clean()
        return min(c) if c else NaN

    def max(self) -> float:
        c = self._clean()
        return max(c) if c else NaN


# ── several columns on one date index ────────────────────────────────────────


class Frame:
    """Named float columns over a shared, ascending date index.

    Deliberately not a dataframe: columns are aligned by construction (they share
    ``dates``), so there is no join, no reindex-by-label and no partial alignment to
    get wrong. Building one from ragged inputs goes through :meth:`from_columns`,
    which does the union-of-dates alignment once, explicitly.
    """

    __slots__ = ("dates", "cols")

    def __init__(self, dates: Sequence[datetime], cols: dict[str, Sequence[float]]):
        self.dates: list[datetime] = list(dates)
        self.cols: dict[str, list[float]] = {}
        for name, values in cols.items():
            if len(values) != len(self.dates):
                raise ValueError(f"column {name!r} has {len(values)} rows, index has {len(self.dates)}")
            self.cols[name] = [_f(v) for v in values]

    @classmethod
    def from_columns(cls, columns: dict[str, Series]) -> Frame:
        """Align ragged Series onto the union of their dates, NaN where absent.

        This is the one alignment the plugin needs — a factor panel where a late-listed
        ticker starts halfway down — and doing it in one place is what lets everything
        downstream assume columns are already aligned.
        """
        if not columns:
            return cls([], {})
        dates = sorted({d for s in columns.values() for d in s.dates})
        out: dict[str, list[float]] = {}
        for name, s in columns.items():
            lookup = dict(zip(s.dates, s.values))
            out[name] = [lookup.get(d, NaN) for d in dates]
        return cls(dates, out)

    # -- basics --------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.dates)

    def __contains__(self, name: str) -> bool:
        return name in self.cols

    def __repr__(self) -> str:
        return f"Frame({len(self)} rows × {len(self.cols)} cols)"

    def __getitem__(self, name: str) -> Series:
        return Series(self.dates, self.cols[name])

    @property
    def columns(self) -> list[str]:
        return list(self.cols)

    @property
    def index(self) -> list[datetime]:
        return list(self.dates)

    @property
    def empty(self) -> bool:
        return not self.dates or not self.cols

    def get(self, name: str, default: Series | None = None) -> Series | None:
        return self[name] if name in self.cols else default

    def with_column(self, name: str, series: Series) -> Frame:
        cols = dict(self.cols)
        cols[name] = list(series.values)
        return Frame(self.dates, cols)

    def select_columns(self, names: Sequence[str]) -> Frame:
        return Frame(self.dates, {n: self.cols[n] for n in names if n in self.cols})

    def rows(self) -> Iterator[tuple[datetime, dict[str, float]]]:
        for i, d in enumerate(self.dates):
            yield d, {n: v[i] for n, v in self.cols.items()}

    def row_at(self, date: datetime) -> dict[str, float]:
        """One cross-section — the (date → value per ticker) slice a factor IC needs."""
        try:
            i = self.dates.index(date)
        except ValueError:
            return {}
        return {n: v[i] for n, v in self.cols.items()}

    # -- row selection -------------------------------------------------------

    def tail(self, n: int) -> Frame:
        if n >= len(self.dates):
            return self
        return Frame(self.dates[-n:], {k: v[-n:] for k, v in self.cols.items()})

    def head(self, n: int) -> Frame:
        return Frame(self.dates[:n], {k: v[:n] for k, v in self.cols.items()})

    def slice_rows(self, start: int | None = None, stop: int | None = None) -> Frame:
        s = slice(start, stop)
        return Frame(self.dates[s], {k: v[s] for k, v in self.cols.items()})

    def drop_all_nan_rows(self) -> Frame:
        """Drop rows where every column is missing — a market holiday one exchange
        observes and another does not, which otherwise becomes a blank panel row."""
        keep = [i for i in range(len(self.dates)) if any(not isna(v[i]) for v in self.cols.values())]
        return Frame([self.dates[i] for i in keep], {k: [v[i] for i in keep] for k, v in self.cols.items()})

    def reindex_ffill(self, dates: Sequence[datetime]) -> Frame:
        """Re-lay the frame on ``dates``, carrying the last known value forward."""
        out: dict[str, list[float]] = {}
        for name in self.cols:
            lookup = dict(zip(self.dates, self.cols[name]))
            carry = NaN
            col: list[float] = []
            for d in dates:
                v = lookup.get(d, NaN)
                if not isna(v):
                    carry = v
                col.append(carry)
            out[name] = col
        return Frame(dates, out)

    # -- columnwise transforms ----------------------------------------------

    def _apply(self, fn: Callable[[Series], Series]) -> Frame:
        return Frame(self.dates, {n: fn(self[n]).values for n in self.cols})

    def ffill(self) -> Frame:
        return self._apply(lambda s: s.ffill())

    def pct_change(self, n: int = 1) -> Frame:
        return self._apply(lambda s: s.pct_change(n))

    def shift(self, n: int = 1) -> Frame:
        return self._apply(lambda s: s.shift(n))

    def rolling_mean(self, n: int) -> Frame:
        return self._apply(lambda s: s.rolling_mean(n))

    def rolling_std(self, n: int, ddof: int = 1) -> Frame:
        return self._apply(lambda s: s.rolling_std(n, ddof))

    def map(self, fn: Callable[[float], float]) -> Frame:
        return self._apply(lambda s: s.map(fn))

    def _combine(self, other: Frame | float, fn: Callable[[Series, Series | float], Series]) -> Frame:
        if isinstance(other, Frame):
            return Frame(self.dates, {n: fn(self[n], other[n]).values for n in self.cols if n in other.cols})
        return Frame(self.dates, {n: fn(self[n], other).values for n in self.cols})

    def __truediv__(self, o): return self._combine(o, lambda a, b: a / b)
    def __sub__(self, o): return self._combine(o, lambda a, b: a - b)
    def __add__(self, o): return self._combine(o, lambda a, b: a + b)
    def __mul__(self, o): return self._combine(o, lambda a, b: a * b)
    def __neg__(self) -> Frame: return self._apply(lambda s: -s)
