"""Watch hooks + the standing tripwires (ADR 0067)."""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"

from . import events  # noqa: E402

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
