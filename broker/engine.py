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

State + mandate + audit live in the live config dir (``PROTOAGENT_CONFIG_DIR``),
so they're per-agent and survive restarts.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("protoagent.plugins.broker")

# Cost model — a small, fixed friction so paper fills aren't free money.
_SLIPPAGE_BPS = 5.0       # 0.05% adverse on every fill
_COMMISSION_BPS = 1.0     # 0.01% per side


def _config_dir() -> Path:
    try:
        from graph.config_io import _live_config_dir
        return _live_config_dir()
    except Exception:  # pragma: no cover - fallback for standalone tests
        import os
        return Path(os.environ.get("PROTOAGENT_CONFIG_DIR", "config")).expanduser()


def _state_path() -> Path:
    return _config_dir() / "broker_paper.json"


def _audit_path() -> Path:
    return _config_dir() / "broker_audit.jsonl"


def _mandate_path() -> Path:
    return _config_dir() / "broker_mandate.yaml"


def _killswitch_path() -> Path:
    return _config_dir() / "TRADING_HALT"


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
        """Master gate independent of any single order."""
        if not self.enabled:
            return False, ("trading is DISABLED — no mandate in effect. Configure "
                           f"{_mandate_path().name} (enabled: true) to arm the paper broker.")
        if self.mode != "paper":
            return False, (f"mode {self.mode!r} is not supported — this build is paper-only. "
                           "A live broker connector is a separate, deliberate step.")
        if _killswitch_path().exists():
            return False, (f"KILL-SWITCH engaged ({_killswitch_path().name} present) — all "
                           "trading halted. Remove the file to resume.")
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
    """Live last price. Equities/ETFs via yfinance; crypto (BTC/USDT) via ccxt."""
    if _is_crypto(symbol):
        import ccxt
        ex = ccxt.okx()
        return float(ex.fetch_ticker(symbol)["last"])
    import yfinance as yf
    fi = yf.Ticker(symbol).fast_info
    px = fi.get("lastPrice") or fi.get("last_price")
    if not px:
        hist = yf.Ticker(symbol).history(period="1d")
        px = float(hist["Close"].iloc[-1])
    return float(px)


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
        return order
