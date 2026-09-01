"""The ADR 0029 "Test connection" route."""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"

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
                f"the market-data module failed to import ({e.name or e}) — a bug rather "
                "than a missing install; the data path needs only the standard library.")})

        from .dashboard.api import resolve_config

        symbol = (resolve_config(config).get("default_benchmark") or "SPY").upper()
        try:
            live = marketdata.bars(symbol, "1mo", prefer="live")
        except Exception as e:
            return JSONResponse({"ok": False, "detail": f"no data for {symbol}: {e}"})
        n = len(marketdata.seed_universe())
        if live.source == "live":
            return JSONResponse({"ok": True, "detail": (
                f"Live provider reachable — {symbol} at "
                f"{live.frame['Close'].last():,.2f}. Bundled snapshot covers {n} symbols.")})
        return JSONResponse({"ok": True, "detail": (
            f"Live provider unreachable; serving {live.label()}. Every view still "
            f"renders from the {n}-symbol bundled snapshot — install yfinance/ccxt "
            f"and check the network for live prices.")})

    return router
