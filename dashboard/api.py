"""The Quant Desk data API — everything the console view renders.

Mounted under ``/api/plugins/prototrader-finance`` so every route inherits the
operator bearer gate (plugin-view rule 2). The *page* is served separately on the
public prefix, because an iframe navigation carries no Authorization header.

Two rules hold across every endpoint here:

* **A dashboard paint never blocks on the network.** Reads default to
  ``prefer="cache"``, which answers from disk (cache → bundled snapshot) and never
  calls a provider. ``?refresh=1`` opts into a live fetch. A demo should not be at
  the mercy of a captive portal.
* **A failure is data, not a 500.** Every handler returns ``{"ok": false, "error"}``
  with HTTP 200 so the page can render a readable message in place of one panel
  instead of showing a blank surface.
"""

from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("protoagent.plugins.prototrader-finance")

STRATEGIES = ["ma_cross", "rsi_meanrev", "breakout", "buy_hold"]

# Small TTL memo: the overview reads ~26 gzipped frames, and a tab switch should
# not redo that. Short enough that ?refresh=1 still feels live.
_MEMO: dict[str, tuple[float, object]] = {}
_MEMO_TTL_S = 20.0


def _memo(key: str, build, ttl: float = _MEMO_TTL_S):
    hit = _MEMO.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    val = build()
    _MEMO[key] = (time.time(), val)
    return val


def _clear_memo() -> None:
    _MEMO.clear()


def _err(exc: Exception) -> dict:
    """A failure the page can render. A missing dependency is an *expected* state
    with a known fix, so it reports the fix rather than a class name."""
    if isinstance(exc, DepsMissing):
        return {"ok": False, "error": str(exc), "needs_deps": True}
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _round(x, n=4):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


class DepsMissing(RuntimeError):
    """The optional market-data stack isn't installed."""


def _deps():
    """Import the pandas-backed modules, or raise a message an operator can act on.

    Deliberately NOT imported at router-BUILD time. `requires_pip` is declared, not
    auto-installed (ADR 0027 D4), so a perfectly normal install has no pandas — and
    an ImportError while building the router killed the whole router group. The rail
    icon still appeared, the page still loaded, and every panel failed to fetch: a
    broken feature with nothing useful anywhere to say why.

    Now the router always mounts and each endpoint answers with the fix.
    """
    try:
        from .. import marketdata
        from ..broker import engine as broker

        return marketdata, broker
    except ImportError as e:
        raise DepsMissing(
            f"the market-data stack isn't installed ({e.name or e}). Run: "
            "`python -m server plugin install-deps prototrader-finance` "
            "(installs pandas/numpy/yfinance/ccxt), then restart."
        ) from e


def build_data_router(config: dict | None):
    """The gated data routes. See module docstring for the two invariants."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()
    cfg = config or {}
    default_symbol = (cfg.get("default_benchmark") or "SPY").upper()

    def _prefer(refresh: int) -> str:
        return "live" if refresh else "cache"

    # ── reference data ───────────────────────────────────────────────────────

    @router.get("/strategies")
    async def _strategies():
        return JSONResponse({"ok": True, "strategies": STRATEGIES, "default_symbol": default_symbol})

    @router.get("/universe")
    async def _universe():
        """What the bundled snapshot covers, and how old it is."""
        try:
            marketdata, _ = _deps()
        except DepsMissing as e:
            return JSONResponse(_err(e))
        m = marketdata.seed_manifest()
        return JSONResponse(
            {
                "ok": True,
                "symbols": marketdata.seed_universe(),
                "default_symbol": default_symbol,
                "seed": {
                    "source": m.get("source"),
                    "fetched_at_iso": m.get("fetched_at_iso"),
                    "period": m.get("period"),
                    "note": m.get("note"),
                },
            }
        )

    # ── overview ─────────────────────────────────────────────────────────────

    @router.get("/overview")
    async def _overview(refresh: int = 0):
        """Headline book + a market strip for the whole demo universe."""
        try:
            if refresh:
                _clear_memo()
            _deps()
            return JSONResponse(_memo(f"overview:{refresh}", lambda: _build_overview(_prefer(refresh))))
        except DepsMissing as e:
            log.warning("[prototrader-finance] overview: %s", e)
            return JSONResponse(_err(e))
        except Exception as e:
            log.exception("[prototrader-finance] overview failed")
            return JSONResponse(_err(e))

    def _build_overview(prefer: str) -> dict:
        marketdata, broker = _deps()
        book = _load_book(broker, cfg)
        marks, rows, sources = {}, [], set()

        for sym in marketdata.seed_universe():
            try:
                # 2y, not 1y: the 1Y column compares against the bar 252 sessions
                # back, which a 252-bar frame does not contain — the whole column
                # rendered as dashes.
                b = marketdata.bars(sym, "2y", prefer=prefer)
            except Exception:
                continue
            close = b.frame["Close"]
            last = float(close.iloc[-1])
            marks[sym] = last
            sources.add(b.source)
            # Spark the last year only — two years of shape in 88px is mush.
            close_spark = close.iloc[-252:]
            step = max(1, len(close_spark) // 90)
            rows.append(
                {
                    "symbol": sym,
                    "last": _round(last, 2),
                    "d1": _pct_change(close, 1),
                    "m1": _pct_change(close, 21),
                    "m6": _pct_change(close, 126),
                    "y1": _pct_change(close, 252),
                    "spark": [_round(v, 4) for v in close_spark.iloc[::step].tolist()][-90:],
                    "source": b.source,
                }
            )

        rows.sort(key=lambda r: (r["d1"] is None, -(r["d1"] or 0)))
        equity = book["cash"] + sum(
            p["qty"] * marks.get(s, p["avg_price"]) for s, p in book["positions"].items()
        )
        invested = sum(p["qty"] * marks.get(s, p["avg_price"]) for s, p in book["positions"].items())
        cost = sum(p["qty"] * p["avg_price"] for s, p in book["positions"].items())

        from .. import seams

        return {
            "ok": True,
            "equity_history": seams.equity_history(),
            "portfolio": {
                "demo": book["demo"],
                "equity": _round(equity, 2),
                "cash": _round(book["cash"], 2),
                "invested": _round(invested, 2),
                "unrealized_pnl": _round(invested - cost, 2),
                "realized_pnl": _round(book["realized_pnl"], 2),
                "total_return": _round((equity / book["starting_cash"]) - 1) if book["starting_cash"] else None,
                "positions": _position_rows(book, marks),
                "note": book.get("note"),
            },
            "market": rows,
            "gate": _gate(broker),
            "provenance": _provenance(sources),
        }

    # ── backtest ─────────────────────────────────────────────────────────────

    @router.get("/backtest")
    async def _backtest(symbol: str = default_symbol, strategy: str = "ma_cross",
                        period: str = "2y", refresh: int = 0):
        """Strategy vs buy-and-hold equity curves + headline metrics."""
        if strategy not in STRATEGIES:
            return JSONResponse({"ok": False, "error": f"unknown strategy {strategy!r}"})
        try:
            _deps()
            from ..backtest import engine

            bars = engine.fetch_bars(symbol, period=period, prefer=_prefer(refresh))
            df = bars.frame
            pos = engine.signals(df, strategy, {})
            sim = engine.simulate(df, pos)
            m = engine.metrics(sim, df.index)
            keys = ("cagr", "sharpe", "max_dd", "total_return", "bh_total_return", "trades", "exposure")
            from .. import events

            events.emit(events.BACKTEST_COMPLETED, symbol=bars.symbol, strategy=strategy,
                        period=period, sharpe=m.get("sharpe"), source=bars.source)
            return JSONResponse(
                {
                    "ok": True,
                    "symbol": bars.symbol,
                    "strategy": strategy,
                    "start": str(df.index[0].date()),
                    "end": str(df.index[-1].date()),
                    "dates": [str(d.date()) for d in df.index],
                    "equity": [_round(x) for x in sim["equity"]],
                    "benchmark": [_round(x) for x in sim["bh_equity"]],
                    "metrics": {k: _round(m.get(k)) if isinstance(m.get(k), (int, float)) else m.get(k) for k in keys},
                    "provenance": bars.to_meta(),
                }
            )
        except DepsMissing as e:
            log.warning("[prototrader-finance] backtest: %s", e)
            return JSONResponse(_err(e))
        except Exception as e:
            log.exception("[prototrader-finance] backtest failed")
            return JSONResponse(_err(e))

    # ── factors ──────────────────────────────────────────────────────────────

    @router.get("/factors")
    async def _factors(period: str = "3y", refresh: int = 0):
        """The factor zoo: information coefficient per factor across the universe."""
        try:
            if refresh:
                _clear_memo()
            _deps()
            return JSONResponse(
                _memo(f"factors:{period}:{refresh}", lambda: _build_factors(period, _prefer(refresh)), ttl=120.0)
            )
        except DepsMissing as e:
            log.warning("[prototrader-finance] factor study: %s", e)
            return JSONResponse(_err(e))
        except Exception as e:
            log.exception("[prototrader-finance] factor study failed")
            return JSONResponse(_err(e))

    def _build_factors(period: str, prefer: str) -> dict:
        marketdata, _ = _deps()
        from ..factors import engine as fe

        universe = marketdata.seed_universe() or fe.DEFAULT_UNIVERSE
        # Benchmarks and crypto aren't cross-sectional equity names — a factor
        # study over SPY/QQQ/BTC alongside single stocks measures nothing.
        universe = [s for s in universe if s not in ("SPY", "QQQ") and "-USD" not in s]
        close, meta = fe.fetch_panel_meta(universe, period, prefer=prefer)

        rows = []
        for name, blurb in fe.FACTORS.items():
            try:
                r = fe.evaluate(name, list(close.columns), period, prefer=prefer)
            except Exception as e:  # one bad factor must not empty the table
                r = {"factor": name, "error": str(e)}
            r["description"] = blurb
            rows.append(r)
        rows.sort(key=lambda r: abs(r.get("ir") or 0), reverse=True)

        from .. import events

        best = next((r for r in rows if not r.get("error")), {})
        events.emit(events.FACTOR_STUDY_COMPLETED, period=period,
                    universe_size=len(close.columns), best_factor=best.get("factor"),
                    best_ic=best.get("mean_ic"))

        return {
            "ok": True,
            "period": period,
            "universe": list(close.columns),
            "factors": rows,
            "provenance": _provenance({m.get("source") for m in meta if m.get("source")}),
        }

    # ── ledger ───────────────────────────────────────────────────────────────

    @router.get("/ledger")
    async def _ledger(refresh: int = 0):
        """The paper book: gate status, positions, and the fill history."""
        try:
            marketdata, broker = _deps()
            book = _load_book(broker, cfg)
            marks, sources = {}, set()
            for sym in book["positions"]:
                try:
                    b = marketdata.bars(sym, "1mo", prefer=_prefer(refresh))
                    marks[sym] = float(b.frame["Close"].iloc[-1])
                    sources.add(b.source)
                except Exception:
                    continue
            return JSONResponse(
                {
                    "ok": True,
                    # The Ledger marks positions off the same tiered price path as
                    # every other pane, so it owes the same disclosure. Without this
                    # its marks sat under whatever chip the Overview last set — a
                    # months-old snapshot displayed beneath "live · just now".
                    "provenance": _provenance(sources),
                    "demo": book["demo"],
                    "note": book.get("note"),
                    "gate": _gate(broker),
                    "mandate": _mandate_summary(broker, cfg),
                    "cash": _round(book["cash"], 2),
                    "starting_cash": _round(book["starting_cash"], 2),
                    "realized_pnl": _round(book["realized_pnl"], 2),
                    "positions": _position_rows(book, marks),
                    "orders": _order_rows(book["orders"])[:100],
                }
            )
        except DepsMissing as e:
            log.warning("[prototrader-finance] ledger: %s", e)
            return JSONResponse(_err(e))
        except Exception as e:
            log.exception("[prototrader-finance] ledger failed")
            return JSONResponse(_err(e))

    return router


# ── shared helpers ───────────────────────────────────────────────────────────

def _pct_change(close, n: int):
    if len(close) <= n:
        return None
    prev = float(close.iloc[-n - 1])
    return _round((float(close.iloc[-1]) / prev) - 1) if prev else None


def _provenance(sources: set) -> dict:
    """One honest line for a panel built from many symbols.

    Reports the WEAKEST tier that contributed, not the best: a strip where 25
    symbols are live and one came off the snapshot is not a live strip, and the
    viewer needs to know which claim they can make.
    """
    for tier, label in (("seed", "bundled snapshot"), ("cache", "cached"), ("live", "live")):
        if tier in sources:
            return {"source": tier, "label": label, "mixed": len(sources) > 1}
    return {"source": "none", "label": "no data", "mixed": False}


def _gate(broker) -> dict:
    """Whether the paper broker would accept an order right now, and why not."""
    try:
        mandate = broker.Mandate.load()
        ok, why = mandate.gate()
        return {"armed": bool(ok), "reason": why}
    except Exception as e:
        return {"armed": False, "reason": f"mandate unreadable: {e}"}


def _mandate_summary(broker, cfg: dict) -> dict:
    from .. import store

    try:
        m = broker.Mandate.load()
        return {
            "path": str(store.mandate_path(cfg)),
            "exists": store.mandate_path(cfg).exists(),
            "enabled": bool(getattr(m, "enabled", False)),
            "mode": getattr(m, "mode", "paper"),
            "max_order_usd": getattr(m, "max_order_usd", None),
            "max_position_pct": getattr(m, "max_position_pct", None),
            "max_gross_exposure_pct": getattr(m, "max_gross_exposure_pct", None),
            "daily_order_cap": getattr(m, "daily_order_cap", None),
            "killswitch": str(store.killswitch_engaged() or ""),
        }
    except Exception as e:
        return {"error": str(e)}


def _load_book(broker, cfg: dict) -> dict:
    """The real paper book, or the bundled demo book when there isn't one yet.

    A clean install has no fills, which would leave the Ledger tab empty in exactly
    the demo it exists for. The fallback is flagged ``demo: True`` all the way to
    the UI, which labels it — a sample book that reads as a real one would be a
    lie about someone's money.
    """
    from .. import marketdata, store

    try:
        if store.state_path().exists():
            state = json.loads(store.state_path().read_text())
            if state.get("orders") or state.get("positions"):
                return {
                    "demo": False,
                    "cash": float(state.get("cash", 0.0)),
                    "starting_cash": float(getattr(broker.Mandate.load(), "starting_cash", 0.0) or 0.0),
                    "realized_pnl": float(state.get("realized_pnl", 0.0)),
                    "positions": state.get("positions") or {},
                    "orders": state.get("orders") or [],
                }
    except Exception:
        log.exception("[prototrader-finance] could not read the paper book")

    demo_path = marketdata.SEED_DIR / "demo_portfolio.json"
    if demo_path.is_file():
        try:
            d = json.loads(demo_path.read_text())
            d["demo"] = True
            return d
        except Exception:
            log.exception("[prototrader-finance] unreadable demo portfolio")

    return {"demo": False, "cash": 0.0, "starting_cash": 0.0, "realized_pnl": 0.0,
            "positions": {}, "orders": []}


def _order_rows(orders: list) -> list[dict]:
    """One shape for the ledger, newest first.

    The live engine records ``fill_price``/``commission``; the bundled demo book
    records ``price``/``fee``. The view read the latter, so every REAL fill would
    have rendered its price and fee as dashes — a ledger quietly missing the two
    numbers that matter most.
    """
    out = []
    for o in reversed(orders or []):
        price = o.get("price", o.get("fill_price"))
        qty = o.get("qty")
        out.append(
            {
                "id": o.get("id"),
                "ts": o.get("ts"),
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "qty": qty,
                "price": _round(price, 2),
                "notional": _round(o.get("notional") or ((qty or 0) * (price or 0)), 2),
                "fee": _round(o.get("fee", o.get("commission", 0.0)), 2),
                "realized_pnl": _round(o.get("realized_pnl"), 2),
                "status": o.get("status", "filled"),
                "demo": bool(o.get("demo")),
            }
        )
    return out


def _position_rows(book: dict, marks: dict) -> list[dict]:
    rows = []
    for sym, p in (book.get("positions") or {}).items():
        qty, avg = float(p.get("qty", 0)), float(p.get("avg_price", 0))
        mark = marks.get(sym, avg)
        rows.append(
            {
                "symbol": sym,
                "qty": qty,
                "avg_price": _round(avg, 2),
                "mark": _round(mark, 2),
                "value": _round(qty * mark, 2),
                "pnl": _round(qty * (mark - avg), 2),
                "pnl_pct": _round((mark / avg) - 1) if avg else None,
                "marked": sym in marks,
            }
        )
    rows.sort(key=lambda r: -(r["value"] or 0))
    return rows
