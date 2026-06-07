"""Trade-journal analysis for protoTrader (Slice 5 — Shadow Account).

Parses a trade journal (CSV: date, symbol, side, qty, price), pairs fills into
round-trips (FIFO, longs and shorts), and computes a **behavioral profile** + the
bias flags that actually cost retail traders money. The agent (via the
shadow-account skill) turns the numbers into the narrative + the "what if you'd
followed your own best behavior" Shadow Account read.

Tolerant CSV: column names are matched loosely (symbol/ticker, side/action,
qty/shares/quantity, price/fill, date/datetime/time).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from statistics import mean


def _f(x, default=0.0):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return default


def _date(x):
    s = str(x).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y %H:%M",
                "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s.split("+")[0].strip(), fmt)
        except ValueError:
            continue
    return None


_COLS = {
    "symbol": ("symbol", "ticker", "instrument", "stock", "asset"),
    "side": ("side", "action", "type", "buy_sell", "direction"),
    "qty": ("qty", "quantity", "shares", "size", "amount", "units"),
    "price": ("price", "fill", "fill_price", "exec_price", "avg_price", "cost"),
    "date": ("date", "datetime", "time", "timestamp", "filled_at", "trade_date"),
}


def _pick(header: list[str]) -> dict:
    # Normalize spaces/hyphens → underscore so "Fill Price"/"Trade Date" match.
    def _norm(h: str) -> str:
        return h.lower().strip().replace(" ", "_").replace("-", "_")
    low = {_norm(h): h for h in header}
    out = {}
    for key, names in _COLS.items():
        for n in names:
            if n in low:
                out[key] = low[n]
                break
    return out


def parse_fills(csv_text: str) -> list[dict]:
    rdr = csv.DictReader(io.StringIO(csv_text.strip()))
    if not rdr.fieldnames:
        return []
    cmap = _pick(rdr.fieldnames)
    missing = {"symbol", "side", "qty", "price"} - set(cmap)
    if missing:
        raise ValueError(f"journal missing columns {sorted(missing)} (need symbol, side, qty, price[, date])")
    fills = []
    for row in rdr:
        side = str(row.get(cmap["side"], "")).strip().lower()
        sign = 1 if side in ("buy", "b", "long", "bot") else -1 if side in ("sell", "s", "short", "sld") else 0
        if sign == 0:
            continue
        fills.append({
            "symbol": str(row.get(cmap["symbol"], "")).strip().upper(),
            "sign": sign, "qty": abs(_f(row.get(cmap["qty"]))),
            "price": _f(row.get(cmap["price"])),
            "date": _date(row.get(cmap.get("date", ""), "")) if "date" in cmap else None,
        })
    return [f for f in fills if f["symbol"] and f["qty"] > 0 and f["price"] > 0]


def round_trips(fills: list[dict]) -> list[dict]:
    """FIFO-match fills into closed round-trips (handles long and short)."""
    from collections import defaultdict, deque

    books: dict[str, deque] = defaultdict(deque)  # open lots per symbol
    trips = []
    for f in fills:
        book = books[f["symbol"]]
        qty = f["qty"]
        # close opposing lots first
        while qty > 0 and book and book[0]["sign"] == -f["sign"]:
            lot = book[0]
            m = min(qty, lot["qty"])
            entry, exit_ = (lot, f) if lot["sign"] == 1 else (lot, f)
            direction = lot["sign"]  # +1 long, -1 short
            pnl = (f["price"] - lot["price"]) * m * direction
            hold = (f["date"] - lot["date"]).days if (f["date"] and lot["date"]) else None
            trips.append({
                "symbol": f["symbol"], "direction": "long" if direction == 1 else "short",
                "qty": m, "entry": lot["price"], "exit": f["price"],
                "pnl": pnl, "ret": (pnl / (lot["price"] * m)) if lot["price"] else 0.0,
                "hold_days": hold, "entry_date": lot["date"], "exit_date": f["date"],
            })
            lot["qty"] -= m
            qty -= m
            if lot["qty"] <= 1e-9:
                book.popleft()
        if qty > 0:  # open a new lot
            book.append({"sign": f["sign"], "qty": qty, "price": f["price"], "date": f["date"]})
    return trips


def profile(csv_text: str) -> dict:
    fills = parse_fills(csv_text)
    # Sort dated fills chronologically before FIFO pairing. Broker exports are
    # often grouped by symbol rather than by time; unsorted rows otherwise
    # mis-pair lots and produce negative hold-days. Undated rows keep file order
    # (their hold-days stay None).
    _dated = sorted((f for f in fills if f["date"] is not None), key=lambda f: f["date"])
    _undated = [f for f in fills if f["date"] is None]
    trips = round_trips(_dated + _undated)
    if not trips:
        return {"error": "no closed round-trips found (need matching buys and sells per symbol)"}
    pnls = [t["pnl"] for t in trips]
    wins = [t for t in trips if t["pnl"] > 0]
    losses = [t for t in trips if t["pnl"] < 0]
    win_hold = [t["hold_days"] for t in wins if t["hold_days"] is not None]
    loss_hold = [t["hold_days"] for t in losses if t["hold_days"] is not None]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    avg_win = mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = mean([t["pnl"] for t in losses]) if losses else 0.0
    n = len(trips)
    wr = len(wins) / n
    # cumulative-P&L drawdown
    cum, peak, maxdd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        maxdd = min(maxdd, cum - peak)

    flags = []
    if win_hold and loss_hold and mean(loss_hold) > 1.5 * mean(win_hold):
        flags.append(f"**Loss aversion** — you hold losers ~{mean(loss_hold):.0f}d vs winners ~{mean(win_hold):.0f}d. "
                     "Cutting losers and letting winners run would invert this.")
    if wins and losses and abs(avg_loss) > 1.3 * avg_win:
        flags.append(f"**Asymmetric losers** — avg loss ${abs(avg_loss):.0f} > avg win ${avg_win:.0f}. "
                     "Small wins, big losses — a single discipline (a stop) would help most.")
    if gross_loss > 0 and gross_win / gross_loss < 1.0:
        flags.append("**Negative edge** — profit factor < 1.0: total losses exceed total wins.")
    # revenge sizing: a trade right after a loss that's much larger than the avg
    avg_notional = mean([t["entry"] * t["qty"] for t in trips])
    revenge = 0
    for prev, cur in zip(trips, trips[1:]):
        if prev["pnl"] < 0 and (cur["entry"] * cur["qty"]) > 1.8 * avg_notional:
            revenge += 1
    if revenge >= 2:
        flags.append(f"**Revenge sizing** — {revenge} oversized trades right after a loss. "
                     "Size should be rules-based, not emotional.")
    if win_hold and mean(win_hold) < 2 and len(wins) > len(losses):
        flags.append("**Cutting winners early** — winners held very briefly; you may be banking small gains "
                     "and missing the trend.")

    return {
        "trades": n, "win_rate": wr, "total_pnl": sum(pnls),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,  # None = no losses (JSON-safe; avoids inf)
        "expectancy": mean(pnls),
        "avg_win_hold_days": mean(win_hold) if win_hold else None,
        "avg_loss_hold_days": mean(loss_hold) if loss_hold else None,
        "largest_win": max(pnls), "largest_loss": min(pnls),
        "max_pnl_drawdown": maxdd,
        "symbols": sorted({t["symbol"] for t in trips}),
        "date_range": [str(min((t["entry_date"] for t in trips if t["entry_date"]), default="")),
                       str(max((t["exit_date"] for t in trips if t["exit_date"]), default=""))],
        "flags": flags,
    }
