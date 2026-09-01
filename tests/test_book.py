"""The paper book — one owner for valuation, and one place to refuse the sample.

Before `book.py` this logic lived in four places. The fix that stopped goal
verifiers grading the bundled sample book had to be written twice, in two files,
and a third caller needed its own guard. These pin the single owner.
"""

from __future__ import annotations

import json

import pytest

from conftest import load


@pytest.fixture
def book():
    return load("book")


def _real(isolated_home, **over):
    state = {"cash": 50_000.0, "realized_pnl": 1_000.0,
             "positions": {"AAPL": {"qty": 100, "avg_price": 200.0}},
             "orders": [{"id": "1", "symbol": "AAPL", "side": "buy", "qty": 100,
                         "fill_price": 200.0, "commission": 2.0}]}
    state.update(over)
    (isolated_home / "broker_paper.json").write_text(json.dumps(state))


def test_equity_is_defined_once(book, isolated_home):
    _real(isolated_home)
    b = book.load()
    assert b.equity({"AAPL": 250.0}) == 50_000.0 + 25_000.0
    assert b.invested({"AAPL": 250.0}) == 25_000.0
    assert b.cost_basis() == 20_000.0


def test_a_missing_mark_falls_back_to_cost(book, isolated_home):
    """A position with no fresh price must be held at cost, not dropped — dropping
    it would silently shrink the account."""
    _real(isolated_home)
    b = book.load()
    assert b.equity({}) == 50_000.0 + 20_000.0
    assert book.position_rows(b, {})[0]["marked"] is False


def test_total_return_measures_against_the_mandate_starting_cash(book, isolated_home):
    """Not against whatever the book happens to hold. `Mandate.starting_cash`
    defaults to 100k and is the same number `PaperBroker` seeds cash with, so the
    two agree even with no mandate file on disk."""
    _real(isolated_home)
    # 50k cash + 100 AAPL @ 200 = 70k against a 100k start.
    assert book.load().total_return({"AAPL": 200.0}) == pytest.approx(-0.30)


def test_total_return_is_none_without_a_starting_figure(book):
    """A book that never had starting capital has no return to report — better
    than dividing by zero or inventing a denominator."""
    assert book.Book(cash=100.0).total_return({}) is None


def test_the_sample_book_is_flagged_and_refusable(book, offline):
    """No real fills ⇒ the bundled sample stands in, so the Ledger isn't empty in
    the demo it exists for. Anything reporting a figure as the operator's OWN must
    refuse it."""
    b = book.load()
    assert b.demo is True and b.orders
    with pytest.raises(book.SampleBookError, match="bundled sample"):
        b.require_real()


def test_a_real_book_passes_require_real(book, isolated_home):
    _real(isolated_home)
    assert book.load().require_real().demo is False


def test_order_rows_normalize_both_shapes(book, isolated_home):
    """The engine writes fill_price/commission; the sample book writes price/fee.
    One reconciliation point, or the view renders dashes for a real fill."""
    _real(isolated_home)
    row = book.order_rows(book.load())[0]
    assert row["price"] == 200.0 and row["fee"] == 2.0

    demo_row = book.order_rows(book.load.__globals__["_demo_book"]())[0]
    assert demo_row["price"] and demo_row["fee"] is not None


def test_marks_report_the_tiers_they_came_from(book, offline, isolated_home):
    """A position priced off a months-old snapshot is fine to show and bad to show
    silently, so marks_for hands back the tiers for disclosure."""
    _real(isolated_home)
    marks, sources = book.marks_for(book.load())
    assert marks.get("AAPL")
    assert sources == {load("marketdata").Tier.SEED}


def test_nothing_recomputes_equity_outside_book():
    """The whole point of this module. If a second `cash + sum(...)` reappears,
    the next honesty fix will again have to be written in two places."""
    import re

    hits = []
    for mod in ("metrics", "verifiers", "dashboard.api", "chat", "lifecycle"):
        src = open(load(mod).__file__).read()
        if re.search(r"cash.*\+.*sum\(", src):
            hits.append(mod)
    assert not hits, f"equity recomputed outside book.py in: {hits}"
