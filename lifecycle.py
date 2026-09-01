"""Lifecycle hooks (ADR 0074) — warming the price cache.

Paints deliberately never go live, so without a warm a networked host would keep
serving the bundled snapshot until someone pressed Refresh. This is the other half
of that trade: fetch on lifecycle events instead of on the paint.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"

from . import events, metrics  # noqa: E402

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

            # The WHOLE universe, not the first 8: since paints never go live, an
            # un-warmed symbol shows the bundled snapshot indefinitely. `warm()`
            # skips anything still fresh, so this is cheap on a repeat wake.
            refreshed = marketdata.warm(marketdata.seed_universe() or [])
            metrics.snapshot_equity(config)
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
