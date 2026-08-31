"""The rest of the host seams this plugin uses, and what each one buys.

Grouped here rather than scattered so the reference value is legible: one file
that shows a chat command, A2A card skills, lifecycle hooks, watch hooks, the
metric timeseries, the knowledge store, and the ADR 0029 connection test, each
with a real finance job to do. The honest ⛔ list — seams deliberately NOT used —
lives in ``docs/sdk-parity.md``.

Everything here is defensive by construction. A seam is an enhancement; a plugin
whose *tools* stop working because a metric write failed would be a bad trade.
"""

from __future__ import annotations

import logging

from . import events

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"


# ── metric timeseries (#1632) ────────────────────────────────────────────────
# Drawdown needs a high-water mark and the dashboard wants an equity sparkline,
# both of which need PRIOR values. Before this seam every plugin that wanted that
# hand-rolled a JSON file next to its state.

def record_equity(value: float) -> None:
    try:
        from graph import sdk

        sdk.record_metric("equity", float(value), plugin_id=PLUGIN_ID)
    except Exception:
        log.debug("[%s] equity metric not recorded (no host?)", PLUGIN_ID)


def equity_history(limit: int = 180) -> list[dict]:
    """[{ts, value}] oldest-first, for the Overview sparkline."""
    try:
        from graph import sdk

        rows = sdk.metric_history("equity", plugin_id=PLUGIN_ID) or []
    except Exception:
        return []
    out = []
    for row in rows:
        try:
            ts, val = (row[0], row[1]) if isinstance(row, (list, tuple)) else (row.get("ts"), row.get("value"))
            out.append({"ts": float(ts), "value": float(val)})
        except Exception:
            continue
    return out[-limit:]


def snapshot_equity(config: dict | None = None) -> float | None:
    """Value the book at current marks and record it. Returns the equity, or None."""
    try:
        from . import marketdata
        from .broker import engine as broker
        from .dashboard.api import _load_book

        book = _load_book(broker, config or {})
        marks = {}
        for sym in book["positions"]:
            try:
                marks[sym] = float(marketdata.bars(sym, "1mo", prefer="cache").frame["Close"].iloc[-1])
            except Exception:
                continue
        equity = book["cash"] + sum(p["qty"] * marks.get(s, p["avg_price"]) for s, p in book["positions"].items())
        record_equity(equity)
        return equity
    except Exception:
        log.exception("[%s] equity snapshot failed", PLUGIN_ID)
        return None


# ── knowledge store ──────────────────────────────────────────────────────────
# A backtest the agent ran an hour ago should be recallable next week without
# re-running it. `sdk.knowledge_add` is the retrievable-facts seam; a plugin must
# never open core's knowledge.db directly.

async def remember_backtest(symbol: str, strategy: str, metrics: dict, period: str) -> None:
    try:
        from graph import sdk

        await sdk.knowledge_add(
            f"Backtest {strategy} on {symbol} over {period}: "
            f"CAGR {metrics.get('cagr', 0):.2%}, Sharpe {metrics.get('sharpe', 0):.2f}, "
            f"max drawdown {metrics.get('max_dd', 0):.2%}, "
            f"total return {metrics.get('total_return', 0):.2%} "
            f"vs buy-and-hold {metrics.get('bh_total_return', 0):.2%}, "
            f"{metrics.get('trades', 0)} trades.",
            domain="prototrader-backtests",
            heading=f"{symbol} · {strategy} · {period}",
        )
    except Exception:
        log.debug("[%s] backtest not recorded to knowledge (no host?)", PLUGIN_ID)


# ── chat command (user-only, not an agent tool) ──────────────────────────────

def make_quant_command(config: dict | None):
    """``/quant <SYMBOL> [strategy] [period]`` — a one-line desk read, in chat.

    Registered as a chat command rather than a tool on purpose: it is a *user*
    affordance, and the seam is explicitly not model-invokable. The agent already
    has the same engines as tools; this is the operator's shortcut.
    """
    default_symbol = ((config or {}).get("default_benchmark") or "SPY").upper()

    async def handler(rest: str, session_id: str):
        parts = (rest or "").split()
        symbol = (parts[0] if parts else default_symbol).upper()
        strategy = parts[1] if len(parts) > 1 else "ma_cross"
        period = parts[2] if len(parts) > 2 else "2y"
        try:
            from .backtest import engine

            bars = engine.fetch_bars(symbol, period=period, prefer="cache")
            df = bars.frame
            sim = engine.simulate(df, engine.signals(df, strategy, {}))
            m = engine.metrics(sim, df.index)
        except Exception as e:
            return (f"**/quant {symbol}** failed — {type(e).__name__}: {e}\n\n"
                    f"Try one of: {', '.join(_universe()) or 'no seeded symbols'}")

        events.emit(events.BACKTEST_COMPLETED, symbol=symbol, strategy=strategy,
                    period=period, sharpe=m.get("sharpe"), source=bars.source)
        edge = (m.get("total_return") or 0) - (m.get("bh_total_return") or 0)
        return (
            f"**{symbol} · {strategy} · {period}** — {bars.label()}\n\n"
            f"| CAGR | Sharpe | Max DD | Total | vs B&H | Trades |\n"
            f"|---|---|---|---|---|---|\n"
            f"| {m.get('cagr', 0):.1%} | {m.get('sharpe', 0):.2f} | {m.get('max_dd', 0):.1%} "
            f"| {m.get('total_return', 0):.1%} | {edge:+.1%} | {m.get('trades', 0)} |\n\n"
            f"*Research output, not advice. Net of 5bps cost + 2bps slippage.*"
        )

    return handler


def _universe() -> list:
    try:
        from . import marketdata

        return marketdata.seed_universe()
    except Exception:
        return []


# ── A2A card skills ──────────────────────────────────────────────────────────
# What this agent advertises to PEER agents on its card. Typed: declaring
# output_schema + result_mime makes the executor's structured finalizer enforce
# the shape, so a caller gets JSON it can parse rather than prose it must guess at.

A2A_SKILLS = [
    {
        "id": "quant-backtest",
        "name": "Backtest a strategy",
        "description": (
            "Run a vectorized backtest of a named strategy on one symbol and return "
            "headline risk/return metrics against buy-and-hold. Costs and slippage "
            "included; no look-ahead. Research output, not advice."
        ),
        "tags": ["finance", "quant", "backtest"],
        "examples": [
            "Backtest ma_cross on NVDA over 2y",
            "How would rsi_meanrev have done on SPY?",
        ],
        "result_mime": "application/json",
        "output_schema": {
            "type": "object",
            "required": ["symbol", "strategy", "metrics"],
            "properties": {
                "symbol": {"type": "string"},
                "strategy": {"type": "string"},
                "period": {"type": "string"},
                "data_source": {"type": "string", "enum": ["live", "cache", "seed"]},
                "metrics": {
                    "type": "object",
                    "properties": {
                        "cagr": {"type": "number"}, "sharpe": {"type": "number"},
                        "max_dd": {"type": "number"}, "total_return": {"type": "number"},
                        "bh_total_return": {"type": "number"}, "trades": {"type": "integer"},
                    },
                },
            },
        },
    },
    {
        "id": "market-read",
        "name": "Read an instrument",
        "description": (
            "A sourced read on one instrument — price and trend, fundamentals for "
            "equities, and the risks that cut against it. Evidence, never a "
            "recommendation to buy or sell."
        ),
        "tags": ["finance", "research"],
        "examples": ["What's the setup on XOM?", "Give me the bear case on TSLA"],
    },
]


# ── lifecycle hooks (ADR 0074) ───────────────────────────────────────────────

def make_lifecycle_hooks(config: dict | None):
    """Warm the cache at boot and after a laptop sleeps.

    Both matter for the same reason: the first thing anyone does with this plugin
    is open the dashboard, and a cold cache means that first paint comes off the
    bundled snapshot with a stale badge. Warming is best-effort and never blocks
    boot.
    """

    async def _warm(reason: str):
        try:
            from . import marketdata

            refreshed = 0
            for sym in (marketdata.seed_universe() or [])[:8]:  # benchmarks first, not all 26
                try:
                    if marketdata.bars(sym, "2y", prefer="live").source == "live":
                        refreshed += 1
                except Exception:
                    continue
            snapshot_equity(config)
            if refreshed:
                events.emit(events.DATA_REFRESHED, reason=reason, symbols=refreshed)
            log.info("[%s] warmed %d symbols (%s)", PLUGIN_ID, refreshed, reason)
        except Exception:
            log.exception("[%s] cache warm failed (%s)", PLUGIN_ID, reason)

    async def on_app_loaded(*_a, **_k):
        await _warm("app_loaded")

    async def on_system_wake(*_a, **_k):
        await _warm("system_wake")

    return on_app_loaded, on_system_wake


# ── watch hooks (ADR 0067) ───────────────────────────────────────────────────

def make_watch_hooks():
    """Turn a tripped tripwire into an event other plugins can hear."""

    async def on_met(watch):
        wid = str(getattr(watch, "id", "") or "")
        log.warning("[%s] watch tripped: %s", PLUGIN_ID, wid)
        if "drawdown" in wid:
            events.emit(events.DRAWDOWN_BREACH, watch=wid)
        elif "halt" in wid:
            events.emit(events.TRADING_HALTED, watch=wid)

    async def on_stalled(watch):
        # A stalled watch means the verifier's evidence stopped moving — for
        # `data_is_stale` that is itself the signal, not a malfunction.
        log.info("[%s] watch stalled: %s", PLUGIN_ID, getattr(watch, "id", ""))

    return on_met, on_stalled


def arm_tripwires(config: dict | None) -> int:
    """Standing tripwires, armed at load with stable ids so a reload replaces
    rather than duplicates them.

    Only armed when the broker is actually armed: watching the drawdown of a book
    that cannot trade is noise, and a plugin that fills an operator's watch list
    on install has made itself annoying rather than useful.
    """
    try:
        from graph import sdk

        from .broker import engine as broker

        armed, _ = broker.Mandate.load().gate()
    except Exception:
        return 0
    if not armed:
        log.info("[%s] broker disarmed — no tripwires armed", PLUGIN_ID)
        return 0

    specs = [
        ("ptf-drawdown", "paper book drawdown breaches 15%", f"{PLUGIN_ID}:max_drawdown",
         {"limit": 0.15}, "The paper book just breached a 15% drawdown. Review the open "
         "positions and the mandate; consider halting."),
        ("ptf-halt", "the broker kill-switch is engaged", f"{PLUGIN_ID}:trading_halted",
         {}, "The trading kill-switch was engaged. Confirm this was intentional."),
    ]
    n = 0
    for wid, condition, verifier, args, prompt in specs:
        try:
            sdk.create_watch(condition=condition, verifier=verifier, verifier_args=args,
                             watch_id=wid, interval_s=900, run_prompt=prompt, repeat=True)
            n += 1
        except Exception:
            log.exception("[%s] could not arm watch %s", PLUGIN_ID, wid)
    return n


# ── connection test (ADR 0029) ───────────────────────────────────────────────

def build_test_router(config: dict | None):
    """``POST /api/config/test-prototrader_finance`` — the Settings "Test connection".

    The full path lives on the ROUTE and the router registers with ``prefix=""``,
    matching core's own chat-surface wirer. Registering it under ``prefix="/api"``
    works but trips the registry's "routes SHOULD live under /plugins/<id>/"
    warning on every boot — and an empty prefix is the case that check skips.

    Reports which data tier is actually reachable. That is the single most useful
    thing to know before a demo, and the answer "the snapshot, because the
    provider is unreachable" is a *pass* with a caveat rather than a failure —
    the plugin genuinely does work in that state.
    """
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()

    @router.post("/api/config/test-prototrader_finance")
    async def _test():
        try:
            from . import marketdata
        except ImportError as e:
            return JSONResponse({"ok": False, "detail": (
                f"the market-data stack isn't installed ({e.name or e}). Run "
                "`python -m server plugin install-deps prototrader-finance`, then restart.")})

        symbol = ((config or {}).get("default_benchmark") or "SPY").upper()
        try:
            live = marketdata.bars(symbol, "1mo", prefer="live")
        except Exception as e:
            return JSONResponse({"ok": False, "detail": f"no data for {symbol}: {e}"})
        n = len(marketdata.seed_universe())
        if live.source == "live":
            return JSONResponse({"ok": True, "detail": (
                f"Live provider reachable — {symbol} at "
                f"{live.frame['Close'].iloc[-1]:,.2f}. Bundled snapshot covers {n} symbols.")})
        return JSONResponse({"ok": True, "detail": (
            f"Live provider unreachable; serving {live.label()}. Every view still "
            f"renders from the {n}-symbol bundled snapshot — install yfinance/ccxt "
            f"and check the network for live prices.")})

    return router
