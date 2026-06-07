---
name: place-a-paper-trade
description: >-
  Use this ONLY when the operator explicitly asks to place, size, or manage a
  simulated/paper order — e.g. "buy 10 shares of AAPL in the paper account",
  "paper-trade this setup", "check my paper positions", "what's my paper P&L".
  Drives a confirm-armed → size → preview → operator-approval → fill loop. Never
  initiates a trade on your own.
tools: [broker_account, broker_place_order, broker_orders, stock_quote, crypto_quote, current_time]
---

# Place a paper trade

protoTrader can *simulate* execution so a thesis can be tracked as real positions
and P&L. This is **paper money behind a hard gate** — and it is the only kind of
order this build can place. Your job is to execute the operator's instruction
faithfully and safely, not to decide to trade.

## Ground rules (non-negotiable)
- **Never place an order the operator didn't explicitly ask for.** No autonomous
  trading, no "I went ahead and bought." You propose sizing; they approve fills.
- This is **simulated**. Say so. It is not advice and not a live order.
- Every order pauses for the operator to type **APPROVE**. That gate is the
  product — never try to route around it.

## 1. Confirm the broker is armed
Call `broker_account`. If it shows 🔴 OFF (no mandate / disabled / kill-switch),
**stop** and tell the operator exactly why and how to arm it (create
`broker_mandate.yaml` from the example with `enabled: true`, or remove
`TRADING_HALT`). Do not attempt orders against a disabled broker.

## 2. Size it against the mandate
Get a live quote (`stock_quote` / `crypto_quote`). Translate the operator's intent
into `symbol / side / qty`. Sanity-check it against the mandate limits shown by
`broker_account` (per-order cap, per-name %, gross %, daily cap) *before* placing —
if it will clearly breach a limit, say so and propose a size that fits.

## 3. Place → approve → fill
Call `broker_place_order`. It returns a preview and **pauses** for approval. Relay
the preview plainly (side, qty, symbol, ~price, notional, cash after). The operator
types APPROVE to fill or anything else to cancel — you do not approve on their
behalf. Report the fill id, fill price, and resulting cash/position.

## 4. Manage & review
- `broker_account` — positions, equity, realized + unrealized P&L, mandate status.
- `broker_orders` — the fill/audit trail.
A `sell` reduces a long (paper v1 is long-only). To halt everything instantly, tell
the operator they can `touch config/TRADING_HALT`.

## Rules recap
- Paper only. Explicit-ask only. Operator approves every fill.
- Tie sizing to the mandate and to a live quote — never a guessed price.
- This is execution *plumbing*, not a recommendation to trade.
