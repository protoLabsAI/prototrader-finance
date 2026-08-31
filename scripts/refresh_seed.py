#!/usr/bin/env python3
"""Regenerate the bundled demo snapshot in ``seed/``.

The seed is **real market data**, fetched once and committed so a clean install
demos offline. It is deliberately reproducible rather than a mystery blob: rerun
this script to re-date it, and the diff is just newer bars.

    python scripts/refresh_seed.py            # the standard demo universe
    python scripts/refresh_seed.py SPY NVDA   # just these

Requires network + ``yfinance`` (``pip install -r requirements.txt``).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "protoagent_plugin_prototrader_finance"  # the name the host loads it under


def _load_marketdata():
    """Import the plugin as the package the host builds, so relative imports work."""
    import importlib
    import importlib.util

    if PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[PKG] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"{PKG}.marketdata")

# The demo universe. Broad enough for a cross-sectional factor study (11 sectors
# represented), liquid enough that the curves are recognisable in a demo, and
# small enough that the snapshot stays a few hundred KB.
UNIVERSE = [
    "SPY", "QQQ",                                    # benchmarks
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", # mega-cap tech
    "AVGO", "TSLA",
    "JPM", "XOM", "JNJ", "WMT", "UNH", "LLY",        # sector spread
    "BTC-USD", "ETH-USD",                            # crypto
]

PERIOD = "5y"  # one long snapshot; marketdata._slice serves shorter windows from it


def main(symbols: list[str]) -> int:
    marketdata = _load_marketdata()

    seed_dir = marketdata.SEED_DIR
    seed_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = time.time()
    ok, failed = [], []

    for sym in symbols:
        try:
            df = marketdata._fetch_live(sym, PERIOD, "1d", "okx")
            marketdata._write_frame(marketdata._seed_path(sym), df, fetched_at)
            ok.append(sym)
            print(f"  ✓ {sym:<9} {len(df):>5} bars  {df.index[0].date()} → {df.index[-1].date()}")
        except Exception as exc:
            failed.append(sym)
            print(f"  ✗ {sym:<9} {type(exc).__name__}: {exc}")

    if not ok:
        print("nothing fetched — snapshot unchanged")
        return 1

    # MERGE, don't replace: a partial run (`refresh_seed.py SPY`) must not silently
    # shrink the demo universe to one symbol while the other snapshots sit on disk
    # unlisted. The manifest describes what `seed/` HOLDS, not what this run touched.
    on_disk = sorted(p.name.split(".")[0] for p in seed_dir.glob("*.csv.gz"))
    (seed_dir / "MANIFEST.json").write_text(
        json.dumps(
            {
                "source": "Yahoo Finance via yfinance",
                "fetched_at": fetched_at,
                "fetched_at_iso": datetime.fromtimestamp(fetched_at, timezone.utc).isoformat(),
                "period": PERIOD,
                "interval": "1d",
                "symbols": on_disk,
                "note": (
                    "Real historical bars, snapshotted for offline demos. NOT live data — "
                    "the dashboard labels anything served from here as a bundled snapshot "
                    "with its age. Regenerate with scripts/refresh_seed.py."
                ),
            },
            indent=2,
        )
        + "\n"
    )
    total = sum(p.stat().st_size for p in seed_dir.glob("*.csv.gz"))
    print(f"\n{len(ok)} symbols · {total / 1024:.0f} KB · manifest written")
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or UNIVERSE))
