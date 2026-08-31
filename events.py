"""The plugin's event API (ADR 0039) — what it broadcasts, and what it listens for.

Topics are the plugin's *public* interface: another plugin subscribes by name and
never imports this one (a cross-plugin import is the thing the bus exists to
prevent). The host auto-namespaces on publish, so ``emit("order_filled")`` reaches
subscribers as ``prototrader-finance.order_filled``.

They are declared in the manifest under ``emits:``/``subscribes:`` as well, so an
operator can see the wiring without reading code — and the console lights the rail
icon's notification dot on a topic for free.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

# Kept in lockstep with the manifest's `emits:` list — a test pins that.
BACKTEST_COMPLETED = "backtest_completed"
FACTOR_STUDY_COMPLETED = "factor_study_completed"
ORDER_FILLED = "order_filled"
MANDATE_ARMED = "mandate_armed"
TRADING_HALTED = "trading_halted"
DRAWDOWN_BREACH = "drawdown_breach"
DATA_REFRESHED = "data_refreshed"

TOPICS = (
    BACKTEST_COMPLETED,
    FACTOR_STUDY_COMPLETED,
    ORDER_FILLED,
    MANDATE_ARMED,
    TRADING_HALTED,
    DRAWDOWN_BREACH,
    DATA_REFRESHED,
)

_registry = None


def bind(registry) -> None:
    """Adopt the registry so module-level code can publish. Called from register()."""
    global _registry
    _registry = registry


def emit(topic: str, **data) -> None:
    """Publish one topic. Never raises: a telemetry side-channel must not be able
    to fail a fill or a backtest."""
    if _registry is None:
        return
    try:
        _registry.emit(topic, data)
    except Exception:
        log.exception("[prototrader-finance] emit(%s) failed", topic)


def subscribe(registry) -> None:
    """Own-bus reactions — the housekeeping this plugin does for itself.

    Both handlers exist so state that a *tool* changed still reaches the parts of
    the plugin that cache it. Without them, arming a mandate through the broker
    tool would leave the dashboard's memoized gate stale until it expired.
    """

    def _invalidate(_data=None):
        try:
            from .dashboard import api

            api._clear_memo()
        except Exception:
            log.exception("[prototrader-finance] could not invalidate the dashboard memo")

    registry.on(f"{registry.plugin_id}.{ORDER_FILLED}", lambda d=None: _invalidate(d))
    registry.on(f"{registry.plugin_id}.{MANDATE_ARMED}", lambda d=None: _invalidate(d))
