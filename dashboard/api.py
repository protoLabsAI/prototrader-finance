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


def resolve_config(config) -> dict:
    """`config` may be a dict (a register-time snapshot) or a callable returning one.

    Handlers call this per request rather than closing over a dict, so a config
    edit in Settings takes effect without a restart — FastAPI cannot re-mount a
    router, so the snapshot a builder captured would otherwise be permanent.
    """
    try:
        return (config() if callable(config) else config) or {}
    except Exception:
        return {}


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
    """A module the data routes need would not import."""


def _deps():
    """Import the market-data modules, or raise a message an operator can act on.

    Deliberately NOT imported at router-BUILD time, and that stays true even though
    the reason has changed. It used to be that `requires_pip` is declared rather than
    auto-installed (ADR 0027 D4), so a perfectly normal install had no pandas — and an
    ImportError while building the router aborted the whole router group. The rail icon
    still appeared, the page still loaded, and every panel failed to fetch: a broken
    feature with nothing useful anywhere to say why.

    That specific failure is gone, because nothing here has a hard dependency any more.
    The lazy import stays because the property worth keeping was never "pandas might be
    missing" — it was that ONE bad import must cost one panel, not the whole view.

    So this raising now means a genuinely broken module, not a missing install. Live
    prices still want yfinance/ccxt, but those are optional and fail per-fetch, falling
    back to the cache and then the bundled snapshot — never here.
    """
    try:
        from .. import marketdata
        from ..broker import engine as broker

        return marketdata, broker
    except ImportError as e:
        raise DepsMissing(
            f"the market-data module failed to import ({e.name or e}) — this is a bug, "
            "not a missing dependency: the data routes need nothing beyond the standard "
            "library. Please report it with the server log."
        ) from e


def build_data_router(config: dict | None):
    """The gated data routes. See module docstring for the two invariants."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()

    def cfg() -> dict:
        return resolve_config(config)

    def default_symbol() -> str:
        return (cfg().get("default_benchmark") or "SPY").upper()

    def _prefer(refresh: int) -> str:
        return "live" if refresh else "cache"

    # ── reference data ───────────────────────────────────────────────────────

    @router.get("/strategies")
    async def _strategies():
        return JSONResponse({"ok": True, "strategies": STRATEGIES, "default_symbol": default_symbol()})

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
                "default_symbol": default_symbol(),
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
        marketdata, _ = _deps()
        from .. import book as book_mod

        acct = book_mod.load(cfg())
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
            last = float(close.last())
            marks[sym] = last
            sources.add(b.source)
            # Spark the last year only — two years of shape in 88px is mush.
            close_spark = close.tail(252)
            step = max(1, len(close_spark) // 90)
            rows.append(
                {
                    "symbol": sym,
                    "last": _round(last, 2),
                    "d1": _pct_change(close, 1),
                    "m1": _pct_change(close, 21),
                    "m6": _pct_change(close, 126),
                    "y1": _pct_change(close, 252),
                    "spark": [_round(v, 4) for v in close_spark[::step].tolist()][-90:],
                    "source": b.source,
                }
            )

        rows.sort(key=lambda r: (r["d1"] is None, -(r["d1"] or 0)))

        from .. import metrics

        invested = acct.invested(marks)
        return {
            "ok": True,
            "equity_history": metrics.equity_history(),
            "portfolio": {
                "demo": acct.demo,
                "equity": _round(acct.equity(marks), 2),
                "cash": _round(acct.cash, 2),
                "invested": _round(invested, 2),
                "unrealized_pnl": _round(invested - acct.cost_basis(), 2),
                "realized_pnl": _round(acct.realized_pnl, 2),
                "total_return": _round(acct.total_return(marks)),
                "positions": book_mod.position_rows(acct, marks),
                "note": acct.note,
            },
            "market": rows,
            "gate": _gate(),
            "provenance": _provenance(sources),
        }

    # ── backtest ─────────────────────────────────────────────────────────────

    @router.get("/backtest")
    async def _backtest(symbol: str = "", strategy: str = "ma_cross",
                        period: str = "2y", refresh: int = 0):
        """Strategy vs buy-and-hold equity curves + headline metrics."""
        symbol = (symbol or default_symbol()).strip()
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
                        period=period, sharpe=m.get("sharpe"), source=str(bars.source))
            # Record it as a retrievable fact. `sdk-parity.md` marked knowledge_add
            # ✅ from v0.3.0 while `remember_backtest` had ZERO callers — the exact
            # "documentation that reads as capability" failure the parity tests are
            # meant to prevent, missed because they only gated the contribution
            # table. The consumption table is gated now too.
            from .. import knowledge

            await knowledge.remember_backtest(bars.symbol, strategy, m, period, str(bars.source))
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
            _deps()
            from .. import book as book_mod

            acct = book_mod.load(cfg())
            marks, sources = book_mod.marks_for(acct, prefer=_prefer(refresh))
            return JSONResponse(
                {
                    "ok": True,
                    # The Ledger marks positions off the same tiered price path as
                    # every other pane, so it owes the same disclosure. Without this
                    # its marks sat under whatever chip the Overview last set — a
                    # months-old snapshot displayed beneath "live · just now".
                    "provenance": _provenance(sources),
                    "demo": acct.demo,
                    "note": acct.note,
                    "gate": _gate(),
                    "mandate": _mandate_summary(cfg()),
                    "cash": _round(acct.cash, 2),
                    "starting_cash": _round(acct.starting_cash, 2),
                    "realized_pnl": _round(acct.realized_pnl, 2),
                    "positions": book_mod.position_rows(acct, marks),
                    "orders": book_mod.order_rows(acct)[:100],
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
    prev = float(close[-n - 1])
    return _round((float(close.last()) / prev) - 1) if prev else None


def _provenance(sources: set) -> dict:
    """One honest line for a panel built from many symbols.

    Reports the WEAKEST tier that contributed, not the best: a strip where 25
    symbols are live and one came off the snapshot is not a live strip, and the
    viewer needs to know which claim they can make.
    """
    from ..marketdata import Tier

    labels = {Tier.SEED: "bundled snapshot", Tier.CACHE: "cached", Tier.LIVE: "live"}
    # Weakest first — reversed(Tier) is the ordering, declared once on the enum.
    for tier in (Tier.SEED, Tier.CACHE, Tier.LIVE):
        if tier in sources:
            return {"source": str(tier), "label": labels[tier], "mixed": len(sources) > 1}
    return {"source": "none", "label": "no data", "mixed": False}


def _gate() -> dict:
    """Whether the paper broker would accept an order right now, and why not."""
    from ..broker import engine as broker

    try:
        mandate = broker.Mandate.load()
        ok, why = mandate.gate()
        return {"armed": bool(ok), "reason": why}
    except Exception as e:
        return {"armed": False, "reason": f"mandate unreadable: {e}"}


def _mandate_summary(cfg: dict) -> dict:
    from .. import store
    from ..broker import engine as broker

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
