"""The Quant Desk console view — two routers at two prefixes.

The split is plugin-view rule 1/2 and is not cosmetic:

* :func:`build_dashboard_router` serves the **page** on the public
  ``/plugins/prototrader-finance`` prefix. A browser iframe navigation cannot
  carry an Authorization header, so a page mounted behind the bearer gate renders
  as a blank surface with no error anywhere.
* :func:`build_data_router` serves the **data** under
  ``/api/plugins/prototrader-finance``, which inherits the operator bearer gate.
  Before that split, anyone who could reach the port could run backtests and read
  the paper book without a token.

The host dedupes routers by ``(plugin_id, prefix)``, so two registrations at two
prefixes is the supported shape.
"""

from __future__ import annotations

from .api import build_data_router

__all__ = ["build_dashboard_router", "build_data_router"]


def build_dashboard_router(config: dict | None):
    """The PAGE router — public prefix. Chrome only; everything it fetches is gated."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    from .page import render

    from .api import resolve_config

    router = APIRouter()

    @router.get("/dashboard")
    async def _dashboard():  # the path the manifest's views[] declares
        return HTMLResponse(render(resolve_config(config)))

    return router
