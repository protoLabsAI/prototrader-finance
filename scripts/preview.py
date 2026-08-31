#!/usr/bin/env python3
"""Serve the Quant Desk view standalone, with no protoAgent host.

    python scripts/preview.py                 # http://127.0.0.1:7899/plugins/prototrader-finance/dashboard
    PROTOAGENT_REPO=~/dev/protoAgent python scripts/preview.py

Mounts the two routers at the SAME two prefixes the host uses, so what you see is
what the console iframes — including the public-page / gated-data split. The DS
plugin-kit is served from a local protoAgent checkout when one is available; the
page's own fallback handles its absence, which is worth seeing too.

This is a dev tool, not a product surface: no auth, loopback only.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "protoagent_plugin_prototrader_finance"
PREFIX = "/api/plugins/prototrader-finance"


def _load():
    if PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[PKG] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"{PKG}.dashboard")


def build_app(config: dict | None = None):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    import importlib

    dash = _load()
    seams = importlib.import_module(f"{PKG}.seams")
    app = FastAPI(title="Quant Desk preview")
    # The same three routers at the same three prefixes register() uses — if the
    # preview mounted a subset, a route could 404 here and be fine in production
    # (or the reverse), which defeats the point of previewing.
    app.include_router(dash.build_dashboard_router(config), prefix="/plugins/prototrader-finance")
    app.include_router(dash.build_data_router(config), prefix=PREFIX)
    app.include_router(seams.build_test_router(config), prefix="/api")

    ds = Path(os.environ.get("PROTOAGENT_REPO", "~/dev/protoAgent")).expanduser() / "apps/web/public/_ds"
    if ds.is_dir():
        app.mount("/_ds", StaticFiles(directory=str(ds)), name="ds")
        print(f"  DS plugin-kit: {ds}")
    else:
        print(f"  DS plugin-kit: NOT FOUND at {ds} — the page's fallback shim will run (unstyled)")
    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "7899"))
    print(f"\n  Quant Desk → http://127.0.0.1:{port}/plugins/prototrader-finance/dashboard\n")
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
