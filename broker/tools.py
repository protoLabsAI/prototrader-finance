"""Broker tools (Slice 6) — gated paper execution.

Every order runs the gate chain: mandate → kill-switch → mandate limits →
**per-order human approval** (the task pauses as ``input-required`` until the
operator types APPROVE) → simulated fill → audit. Nothing here can move real
money: ``mode: live`` is refused by the engine.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from .engine import Mandate, PaperBroker, _killswitch_path, _mandate_path, quote

log = logging.getLogger("protoagent.plugins.broker")

_DEP_HINT = ("market data unavailable — install the finance extras: "
             "pip install -r requirements-finance.txt")


def _broker() -> PaperBroker:
    return PaperBroker(Mandate.load())


def _fmt_positions(broker: PaperBroker, mark: dict) -> str:
    if not broker.state.positions:
        return "  (flat — no open positions)"
    rows = []
    for sym, p in sorted(broker.state.positions.items()):
        px = mark.get(sym, p["avg_price"])
        upnl = (px - p["avg_price"]) * p["qty"]
        rows.append(f"  {sym:<10} {p['qty']:>10.4g} @ {p['avg_price']:>10.2f}  "
                    f"mark {px:>10.2f}  uPnL {upnl:>+10.2f}")
    return "\n".join(rows)


@tool
async def broker_account() -> str:
    """Show the paper trading account: armed/halted status, cash, equity, open
    positions (with unrealized P&L), realized P&L, and the active mandate limits.
    Read-only. Use this before trading to confirm the broker is armed and within
    its mandate."""
    import asyncio

    def _run() -> str:
        b = _broker()
        m = b.mandate
        armed, why = m.gate()
        status = "🟢 ARMED (paper)" if armed else f"🔴 OFF — {why}"
        mark: dict = {}
        for sym in list(b.state.positions):
            try:
                mark[sym] = quote(sym)
            except Exception:
                pass
        eq = b.equity(mark)
        gross = b.gross_exposure(mark)
        lines = [
            f"Paper broker: {status}",
            f"Cash:    ${b.state.cash:,.2f}",
            f"Equity:  ${eq:,.2f}   (gross exposure ${gross:,.0f} = {gross/eq*100 if eq else 0:.0f}%)",
            f"Realized P&L: ${b.state.realized_pnl:+,.2f}",
            "Positions:",
            _fmt_positions(b, mark),
            "",
            "Mandate:",
            f"  universe: {m.universe or 'any'}",
            f"  per-order cap: ${m.max_order_usd:,.0f} | per-name: {m.max_position_pct:.0f}% | "
            f"gross: {m.max_gross_exposure_pct:.0f}% | daily: {m.daily_order_cap}",
            f"  approval required: {m.require_approval} | kill-switch: "
            f"{'ENGAGED' if _killswitch_path().exists() else 'clear'}",
        ]
        if not _mandate_path().exists():
            lines.append(f"\n  ⚠ No mandate file — create {_mandate_path()} to arm "
                         "(see broker_mandate.example.yaml).")
        return "\n".join(lines)

    try:
        return await asyncio.to_thread(_run)
    except ImportError:
        return _DEP_HINT


@tool
async def broker_place_order(symbol: str, side: str, qty: float,
                             order_type: str = "market", limit_price: float | None = None) -> str:
    """Place a **paper** order — gated. Runs mandate + kill-switch + limit checks,
    then **pauses for explicit operator approval** before any fill. The order does
    NOT execute until the operator types APPROVE.

    Args:
        symbol: Equity/ETF ticker (e.g. ``AAPL``) or crypto in ccxt form (``BTC/USDT``).
        side: ``buy`` or ``sell``. Paper v1 is long-only (a sell reduces a long).
        qty: Quantity (shares / coins). Must be positive.
        order_type: ``market`` (fills at the live quote) or ``limit``.
        limit_price: Required for a limit order; the order fills only if marketable.

    This is simulated money. The agent must NOT call this without the operator
    having asked to place this specific order.
    """
    import asyncio

    side = side.lower().strip()
    symbol = symbol.strip().upper() if not symbol.strip().count("/") else symbol.strip().upper()
    if side not in ("buy", "sell"):
        return "side must be 'buy' or 'sell'."
    order_type = order_type.lower().strip()

    b = _broker()
    armed, why = b.mandate.gate()
    if not armed:
        return f"🔴 Order refused — {why}"

    # Price discovery (paper fill reference).
    try:
        px = await asyncio.to_thread(quote, symbol)
    except ImportError:
        return _DEP_HINT
    except Exception as e:
        return f"Could not get a quote for {symbol}: {e}"

    if order_type == "limit":
        if limit_price is None:
            return "a limit order needs limit_price."
        marketable = (side == "buy" and px <= limit_price) or (side == "sell" and px >= limit_price)
        if not marketable:
            return (f"limit {side} {qty} {symbol} @ {limit_price} is not marketable "
                    f"(last {px:.2f}). Paper v1 fills only marketable orders — not resting.")
        px = limit_price

    # Mark other held positions at live prices so the exposure/concentration caps
    # value an appreciated book correctly (not at stale cost basis). Quote failures
    # fall back to cost inside validate.
    marks: dict = {}
    for held_sym in list(b.state.positions):
        if held_sym == symbol:
            continue
        try:
            marks[held_sym] = await asyncio.to_thread(quote, held_sym)
        except Exception:
            pass
    ok, reason = b.validate(symbol, side, qty, px, mark=marks)
    if not ok:
        b._audit({"event": "rejected", "symbol": symbol, "side": side, "qty": qty,
                  "price": px, "reason": reason})
        return f"🔴 Order rejected by mandate — {reason}"

    notional = qty * px
    preview = (f"PAPER ORDER — approval required\n"
               f"  {side.upper()} {qty:g} {symbol} @ ~{px:,.2f}  (≈ ${notional:,.0f}, {order_type})\n"
               f"  cash ${b.state.cash:,.0f} → after ≈ "
               f"${b.state.cash - notional if side=='buy' else b.state.cash + notional:,.0f}\n"
               f"Reply APPROVE to execute, anything else to cancel.")

    if b.mandate.require_approval:
        from langgraph.types import interrupt
        answer = interrupt({"kind": "approval", "title": "Approve paper order?",
                            "description": preview,
                            "order": {"symbol": symbol, "side": side, "qty": qty,
                                      "price": px, "notional": notional}})
        reply = (answer if isinstance(answer, str) else str(answer)).strip().lower()
        if reply not in ("approve", "approved", "yes", "y", "confirm", "ok"):
            b._audit({"event": "cancelled", "symbol": symbol, "side": side, "qty": qty,
                      "price": px, "operator_reply": reply})
            return f"Order cancelled by operator (reply: {answer!r}). Nothing executed."

    order = await asyncio.to_thread(b.fill, symbol, side, qty, px, order_type)
    return (f"✅ FILLED {order['id']}: {side} {qty:g} {symbol} @ {order['fill_price']:,.4f} "
            f"(${order['notional']:,.2f}, commission ${order['commission']:.2f}"
            + (f", realized ${order['realized_pnl']:+,.2f}" if side == "sell" else "")
            + f"). Cash now ${b.state.cash:,.2f}.")


@tool
async def broker_orders(limit: int = 20) -> str:
    """Show recent paper orders (the audit trail of fills, newest last)."""
    import asyncio

    def _run() -> str:
        b = _broker()
        if not b.state.orders:
            return "No orders yet."
        rows = []
        for o in b.state.orders[-max(1, limit):]:
            rows.append(f"  {o['id']}  {o['ts'][:19]}  {o['side']:<4} {o['qty']:>8.4g} "
                        f"{o['symbol']:<10} @ {o['fill_price']:>10.2f}  ${o['notional']:>12,.2f}"
                        + (f"  rPnL {o['realized_pnl']:+,.2f}" if o.get('realized_pnl') else ""))
        return "Recent paper orders:\n" + "\n".join(rows)

    return await asyncio.to_thread(_run)


def get_broker_tools() -> list:
    return [broker_account, broker_place_order, broker_orders]
