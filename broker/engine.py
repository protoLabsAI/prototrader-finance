"""Paper-trading broker for protoTrader (Slice 6 — gated execution).

A **simulated** broker with the full gated-execution stack so live trading can be
layered on later without re-plumbing the safety rails:

  mandate (master switch + per-order/exposure/daily limits, OFF by default)
    → kill-switch (a file the operator can `touch` to halt instantly)
    → per-order human approval (LangGraph interrupt — the task pauses as
      ``input-required`` until the operator types APPROVE)
    → simulated fill at a live quote (+ slippage)
    → append-only audit ledger.

Nothing trades until a mandate is configured AND ``enabled: true``. ``mode: live``
is intentionally NOT implemented — it refuses — so this slice cannot move real
money; a real broker connector is a separate, deliberate step.

State, mandate and audit resolve through :mod:`store` — the plugin's own
instance-scoped directory (``sdk.plugin_store``), so the dev sandbox and every
fleet member keep separate books and a restart never loses a fill.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import store

log = logging.getLogger("protoagent.plugins.broker")

# Cost model — a small, fixed friction so paper fills aren't free money.
_SLIPPAGE_BPS = 5.0       # 0.05% adverse on every fill
_COMMISSION_BPS = 1.0     # 0.01% per side


# Every path resolves through `store` — one module owns the "which directory?"
# question. See store.__doc__ for why v0.1.0's private-symbol import was a bug.

def _state_path() -> Path:
    return store.state_path()


def _audit_path() -> Path:
    return store.audit_path()


def _mandate_path() -> Path:
    return store.mandate_path(_CONFIG)


def _killswitch_path() -> Path:
    return store.killswitch_path()


# The plugin config section, set at register() time so the mandate can honour the
# operator's `broker_mandate_path` setting. Module-level because the engine is
# constructed per call and has nowhere else to carry it.
_CONFIG: dict = {}


# Last armed state seen by `Mandate.gate`, so the transition can be broadcast
# once rather than on every poll. None = not yet observed.
_last_armed: bool | None = None


def _note_armed(armed: bool) -> None:
    """Emit `mandate_armed` when, and only when, the gate's answer changes."""
    global _last_armed
    if _last_armed == armed:
        return
    was, _last_armed = _last_armed, armed
    if was is None:  # first observation is state, not a transition
        return
    from .. import events

    events.emit(events.MANDATE_ARMED, enabled=armed, mode="paper")


def set_config(config: dict | None) -> None:
    """Adopt the resolved plugin config (called from ``register``)."""
    global _CONFIG
    _CONFIG = dict(config or {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── mandate ──────────────────────────────────────────────────────────────────


@dataclass
class Mandate:
    enabled: bool = False
    mode: str = "paper"            # paper | live (live is refused)
    starting_cash: float = 100_000.0
    universe: list[str] = field(default_factory=list)  # empty = any symbol
    max_order_usd: float = 5_000.0
    max_position_pct: float = 20.0
    max_gross_exposure_pct: float = 100.0
    daily_order_cap: int = 10
    require_approval: bool = True

    @classmethod
    def load(cls) -> "Mandate":
        p = _mandate_path()
        if not p.exists():
            return cls()  # disabled by default → nothing trades
        try:
            import yaml
            raw = yaml.safe_load(p.read_text()) or {}
        except Exception as e:  # pragma: no cover
            log.warning("[broker] mandate unreadable (%s) — staying disabled", e)
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def gate(self) -> tuple[bool, str]:
        """Master gate independent of any single order.

        Also the one place that knows the armed state, so it is where the
        ``mandate_armed`` transition is broadcast from. The topic was declared in
        the manifest and subscribed to, but nothing ever emitted it — a mandate is
        a FILE an operator edits, so there is no code path that "arms" one to hook.
        Watching the gate's own answer change is the honest substitute.
        """
        if not self.enabled:
            _note_armed(False)
            return False, ("trading is DISABLED — no mandate in effect. Configure "
                           f"{_mandate_path().name} (enabled: true) to arm the paper broker.")
        if self.mode != "paper":
            return False, (f"mode {self.mode!r} is not supported — this build is paper-only. "
                           "A live broker connector is a separate, deliberate step.")
        halt = store.killswitch_engaged()
        if halt is not None:
            return False, (f"KILL-SWITCH engaged ({halt} present) — all trading halted. "
                           "Remove the file to resume.")
        _note_armed(True)
        return True, "armed (paper)"


# ── paper broker ─────────────────────────────────────────────────────────────


@dataclass
class _State:
    cash: float
    realized_pnl: float = 0.0
    positions: dict = field(default_factory=dict)   # symbol -> {qty, avg_price}
    orders: list = field(default_factory=list)
    order_seq: int = 0
    daily_date: str = ""
    daily_count: int = 0


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol  # ccxt format, e.g. BTC/USDT


def quote(symbol: str) -> float:
    """Last price for a fill. Live when reachable, else the freshest bar on disk.

    A paper fill marked at a cached close is honest — the audit ledger records the
    price and the mark's provenance travels with it via :func:`quote_meta`. Refusing
    to fill at all because the provider blinked would be worse: the demo stops, and
    the operator learns nothing about the gating stack that is the point of it.
    """
    return quote_meta(symbol)[0]


def quote_meta(symbol: str) -> tuple[float, str]:
    """``(price, provenance)`` — e.g. ``(612.4, "live")`` or ``(598.1, "cached · 3h old")``."""
    if _is_crypto(symbol):
        try:
            import ccxt

            ex = ccxt.okx()
            return float(ex.fetch_ticker(symbol)["last"]), "live"
        except Exception:
            log.warning("[broker] live crypto quote for %s failed — falling back", symbol)
    else:
        try:
            import yfinance as yf

            fi = yf.Ticker(symbol).fast_info
            px = fi.get("lastPrice") or fi.get("last_price")
            if px:
                return float(px), "live"
        except Exception:
            log.warning("[broker] live quote for %s failed — falling back", symbol)

    from .. import marketdata

    b = marketdata.bars(symbol, "1mo", prefer="cache")
    return float(b.frame["Close"].last()), b.label()


class PaperBroker:
    def __init__(self, mandate: Mandate):
        self.mandate = mandate
        self.state = self._load()

    # -- persistence --
    def _load(self) -> _State:
        p = _state_path()
        if p.exists():
            try:
                d = json.loads(p.read_text())
                return _State(**d)
            except Exception as e:  # pragma: no cover
                log.warning("[broker] state unreadable (%s) — reinitializing", e)
        return _State(cash=self.mandate.starting_cash)

    def _save(self) -> None:
        # Atomic write: a crash mid-write would otherwise truncate the state file
        # and the next load silently reinitializes to starting cash (wiping
        # positions + realized P&L). Write to a temp file, then os.replace.
        p = _state_path()
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state.__dict__, indent=2))
        os.replace(tmp, p)

    def _audit(self, event: dict) -> None:
        event = {"ts": _now(), **event}
        with _audit_path().open("a") as fh:
            fh.write(json.dumps(event) + "\n")

    # -- valuation --
    def equity(self, mark: dict | None = None) -> float:
        mark = mark or {}
        val = self.state.cash
        for sym, pos in self.state.positions.items():
            px = mark.get(sym, pos["avg_price"])
            val += pos["qty"] * px
        return val

    def gross_exposure(self, mark: dict | None = None) -> float:
        mark = mark or {}
        return sum(abs(p["qty"]) * mark.get(s, p["avg_price"])
                   for s, p in self.state.positions.items())

    # -- order validation (paper, long-only v1) --
    def validate(self, symbol: str, side: str, qty: float, price: float,
                 mark: dict | None = None) -> tuple[bool, str]:
        # ``mark`` carries live prices for OTHER held symbols so the exposure /
        # concentration caps value the existing book at market, not stale cost
        # basis. The order symbol is always marked at its order ``price``.
        m = self.mandate
        if qty <= 0:
            return False, "quantity must be positive"
        if m.universe and symbol not in m.universe:
            return False, f"{symbol} is outside the mandated universe {m.universe}"
        # daily cap
        today = _now()[:10]
        used = self.state.daily_count if self.state.daily_date == today else 0
        if used >= m.daily_order_cap:
            return False, f"daily order cap reached ({m.daily_order_cap}/day)"

        notional = qty * price
        if notional > m.max_order_usd:
            return False, (f"order ${notional:,.0f} exceeds the per-order cap "
                           f"${m.max_order_usd:,.0f}")

        if side == "sell":
            held = self.state.positions.get(symbol, {}).get("qty", 0)
            if qty > held + 1e-9:
                return False, (f"paper v1 is long-only — can't sell {qty} {symbol}, "
                               f"only {held} held")
            return True, "ok"

        # buy: cash + exposure + concentration
        # Commission is charged on the slipped price (see fill), so cost compounds
        # the two bps rather than summing them — match fill exactly so a buy
        # validated at the cash limit can't leave cash a hair below zero.
        cost = notional * (1 + _SLIPPAGE_BPS / 1e4) * (1 + _COMMISSION_BPS / 1e4)
        if cost > self.state.cash:
            return False, f"insufficient cash (${self.state.cash:,.0f}) for ${cost:,.0f}"
        marks = {**(mark or {}), symbol: price}
        eq = self.equity(marks)
        pos_val = self.state.positions.get(symbol, {}).get("qty", 0) * price + notional
        if eq > 0 and pos_val / eq * 100 > m.max_position_pct + 1e-9:
            return False, (f"would put {pos_val/eq*100:.0f}% in {symbol} — over the "
                           f"{m.max_position_pct:.0f}% per-name cap")
        new_gross = self.gross_exposure(marks) + notional
        if eq > 0 and new_gross / eq * 100 > m.max_gross_exposure_pct + 1e-9:
            return False, (f"would lift gross exposure to {new_gross/eq*100:.0f}% — over the "
                           f"{m.max_gross_exposure_pct:.0f}% cap")
        return True, "ok"

    # -- fill (mutates state) --
    def fill(self, symbol: str, side: str, qty: float, price: float, order_type: str) -> dict:
        slip = price * _SLIPPAGE_BPS / 1e4
        fill_px = price + slip if side == "buy" else price - slip
        commission = qty * fill_px * _COMMISSION_BPS / 1e4
        self.state.order_seq += 1
        oid = f"PT-{self.state.order_seq:04d}"
        realized = 0.0

        pos = self.state.positions.get(symbol, {"qty": 0.0, "avg_price": 0.0})
        if side == "buy":
            self.state.cash -= qty * fill_px + commission
            new_qty = pos["qty"] + qty
            pos["avg_price"] = (pos["qty"] * pos["avg_price"] + qty * fill_px) / new_qty
            pos["qty"] = new_qty
        else:  # sell (reduce long)
            realized = (fill_px - pos["avg_price"]) * qty - commission
            self.state.realized_pnl += realized
            self.state.cash += qty * fill_px - commission
            pos["qty"] -= qty
        if pos["qty"] <= 1e-9:
            self.state.positions.pop(symbol, None)
        else:
            self.state.positions[symbol] = pos

        today = _now()[:10]
        if self.state.daily_date != today:
            self.state.daily_date, self.state.daily_count = today, 0
        self.state.daily_count += 1

        order = {
            "id": oid, "ts": _now(), "symbol": symbol, "side": side, "qty": qty,
            "type": order_type, "fill_price": round(fill_px, 4),
            "notional": round(qty * fill_px, 2), "commission": round(commission, 2),
            "realized_pnl": round(realized, 2), "status": "filled",
        }
        self.state.orders.append(order)
        self._save()
        self._audit({"event": "fill", **order})
        # ADR 0039: broadcast the fill so the dashboard invalidates and any peer
        # plugin can react. Wrapped by events.emit — a bus failure never fails a fill.
        from .. import events, metrics

        events.emit(events.ORDER_FILLED, symbol=symbol, side=side, qty=qty,
                    price=round(fill_px, 4), notional=round(qty * fill_px, 2))
        metrics.snapshot_equity(_CONFIG)
        return order
