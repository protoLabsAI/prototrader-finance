"""The paper book — one owner for "what does this account hold, and what is it worth?"

Before this module the answer lived in four places: `dashboard/api.py` (the view),
the old `seams.py` (the metric snapshot — since split into `metrics.py` and
friends), `verifiers.py` (goal grading) and `PaperBroker.equity` (the engine).
Three of them recomputed
``cash + sum(qty * mark)`` independently, and two reached into the *view* for a
private `_load_book` — a broker concept owned by an iframe's data layer.

That mattered beyond tidiness. When the fix landed to stop goal verifiers grading
the bundled sample book, it had to be applied twice in two files, and a third
caller (the metric snapshot) needed its own guard. One owner means one place to
be right.

Everything here is read-only. Writes stay in `broker.engine`, which owns the
mandate gate, the approval interrupt and the audit ledger.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("protoagent.plugins.prototrader-finance")


class SampleBookError(RuntimeError):
    """No real paper book exists — the Ledger is showing the bundled sample.

    Raised by anything that would report a figure AS THE OPERATOR'S OWN. Display
    surfaces catch it and label the book; anything grading a goal or writing to
    the host's metric series must refuse, because those destinations carry no
    demo flag and a number that arrives there reads as real.
    """


@dataclass(frozen=True)
class Book:
    """A paper account at a point in time."""

    cash: float = 0.0
    starting_cash: float = 0.0
    realized_pnl: float = 0.0
    positions: dict = field(default_factory=dict)   # symbol -> {qty, avg_price}
    orders: list = field(default_factory=list)
    demo: bool = False
    note: str = ""

    # ── valuation ────────────────────────────────────────────────────────────

    def invested(self, marks: dict) -> float:
        return sum(p["qty"] * marks.get(s, p["avg_price"]) for s, p in self.positions.items())

    def cost_basis(self) -> float:
        return sum(p["qty"] * p["avg_price"] for p in self.positions.values())

    def equity(self, marks: dict) -> float:
        """Cash plus positions marked to `marks`, falling back to cost where a
        mark is missing. THE definition — every caller uses this one."""
        return self.cash + self.invested(marks)

    def total_return(self, marks: dict) -> float | None:
        if not self.starting_cash:
            return None
        return (self.equity(marks) / self.starting_cash) - 1

    def require_real(self) -> "Book":
        """Self, or raise if this is the bundled sample. Call before reporting a
        figure as the operator's own."""
        if self.demo:
            raise SampleBookError(
                "no real paper book yet — the Ledger is showing the bundled sample. "
                "Arm a mandate and place a paper order before setting a return goal."
            )
        return self


def _demo_book() -> Book | None:
    from . import marketdata

    path = marketdata.SEED_DIR / "demo_portfolio.json"
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text())
    except Exception:
        log.exception("[prototrader-finance] unreadable demo portfolio")
        return None
    return Book(
        cash=float(d.get("cash", 0.0)),
        starting_cash=float(d.get("starting_cash", 0.0)),
        realized_pnl=float(d.get("realized_pnl", 0.0)),
        positions=d.get("positions") or {},
        orders=d.get("orders") or [],
        demo=True,
        note=d.get("note", ""),
    )


def load(config: dict | None = None) -> Book:
    """The real paper book, or the bundled sample when no fills exist yet.

    A clean install has no fills, which would leave the Ledger empty in exactly the
    demo it exists for — so the sample stands in, flagged `demo`. Display surfaces
    label it; :meth:`Book.require_real` is how everything else refuses it.
    """
    from . import store

    try:
        path = store.state_path()
        if path.exists():
            state = json.loads(path.read_text())
            if state.get("orders") or state.get("positions"):
                return Book(
                    cash=float(state.get("cash", 0.0)),
                    starting_cash=_starting_cash(config),
                    realized_pnl=float(state.get("realized_pnl", 0.0)),
                    positions=state.get("positions") or {},
                    orders=state.get("orders") or [],
                )
    except Exception:
        log.exception("[prototrader-finance] could not read the paper book")

    return _demo_book() or Book()


def _starting_cash(config: dict | None) -> float:
    from .broker import engine as broker

    try:
        return float(getattr(broker.Mandate.load(), "starting_cash", 0.0) or 0.0)
    except Exception:
        return 0.0


def marks_for(book: Book, *, prefer: str = "cache") -> tuple[dict, set]:
    """Current marks for the book's symbols, plus the data tiers they came from.

    Returns the tiers so a caller can disclose them — a position priced off a
    months-old snapshot is a fine thing to show and a bad thing to show silently.
    """
    from . import marketdata

    marks, sources = {}, set()
    for sym in book.positions:
        try:
            b = marketdata.bars(sym, "1mo", prefer=prefer)
            marks[sym] = float(b.frame["Close"].last())
            sources.add(b.source)
        except Exception:
            continue
    return marks, sources


# ── presentation shapes ──────────────────────────────────────────────────────

def position_rows(book: Book, marks: dict) -> list[dict]:
    rows = []
    for sym, p in book.positions.items():
        qty, avg = float(p.get("qty", 0)), float(p.get("avg_price", 0))
        mark = marks.get(sym, avg)
        rows.append(
            {
                "symbol": sym, "qty": qty,
                "avg_price": round(avg, 2), "mark": round(mark, 2),
                "value": round(qty * mark, 2), "pnl": round(qty * (mark - avg), 2),
                "pnl_pct": round((mark / avg) - 1, 4) if avg else None,
                "marked": sym in marks,
            }
        )
    rows.sort(key=lambda r: -(r["value"] or 0))
    return rows


def order_rows(book: Book) -> list[dict]:
    """One shape for the ledger, newest first.

    The live engine records ``fill_price``/``commission``; the bundled sample book
    records ``price``/``fee``. Reconciling here — rather than in the view — is the
    reason there is one place to get it wrong. v0.3.0 got it wrong in the view, so
    every REAL fill rendered its price and fee as dashes.
    """
    out = []
    for o in reversed(book.orders or []):
        price = o.get("price", o.get("fill_price"))
        qty = o.get("qty")
        out.append(
            {
                "id": o.get("id"), "ts": o.get("ts"), "symbol": o.get("symbol"),
                "side": o.get("side"), "qty": qty,
                "price": _r(price), "notional": _r(o.get("notional") or ((qty or 0) * (price or 0))),
                "fee": _r(o.get("fee", o.get("commission", 0.0))),
                "realized_pnl": _r(o.get("realized_pnl")),
                "status": o.get("status", "filled"), "demo": bool(o.get("demo")),
            }
        )
    return out


def _r(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None
