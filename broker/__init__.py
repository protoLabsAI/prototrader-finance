"""Gated paper execution — mandate → kill-switch → per-order approval → simulated
fill → audit. OFF until a mandate exists; `mode: live` is deliberately not
implemented (cannot move real money).

A subpackage of the **prototrader-finance** bundle: `tools.get_broker_tools()` is
composed by the bundle's top-level `register()`.
"""
