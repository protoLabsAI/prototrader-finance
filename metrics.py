"""The equity timeseries (`sdk.record_metric`).

Drawdown needs a high-water mark and the dashboard wants a trend, both of which
need PRIOR values a point-in-time payload cannot have. Before this seam every
plugin that wanted that hand-rolled a JSON file next to its state.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"


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
    # sdk.metric_history returns list[tuple[float, float]] — no dict branch needed.
    out = []
    for ts, val in rows:
        try:
            out.append({"ts": float(ts), "value": float(val)})
        except (TypeError, ValueError):
            continue
    return out[-limit:]


def snapshot_equity(config: dict | None = None) -> float | None:
    """Value the real book at current marks and record it. None if there isn't one.

    The sample book is refused by `Book.require_real`: the metric series carries no
    demo flag, so anything written there reads as real — in the Overview sparkline
    and in `max_drawdown`'s high-water mark alike.
    """
    from . import book as book_mod

    try:
        b = book_mod.load(config).require_real()
    except book_mod.SampleBookError:
        log.debug("[%s] sample book — equity not recorded", PLUGIN_ID)
        return None
    except Exception:
        log.exception("[%s] equity snapshot failed", PLUGIN_ID)
        return None

    marks, _ = book_mod.marks_for(b)
    equity = b.equity(marks)
    record_equity(equity)
    return equity
