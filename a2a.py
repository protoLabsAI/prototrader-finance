"""A2A card skills — what this agent advertises to PEER agents.

Typed: declaring `output_schema` + `result_mime` makes the executor's structured
finalizer enforce the shape, so a caller gets JSON it can parse rather than prose
it has to guess at.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"

A2A_SKILLS = [
    {
        "id": "quant-backtest",
        "name": "Backtest a strategy",
        "description": (
            "Run a vectorized backtest of a named strategy on one symbol and return "
            "headline risk/return metrics against buy-and-hold. Costs and slippage "
            "included; no look-ahead. Research output, not advice."
        ),
        "tags": ["finance", "quant", "backtest"],
        "examples": [
            "Backtest ma_cross on NVDA over 2y",
            "How would rsi_meanrev have done on SPY?",
        ],
        "result_mime": "application/json",
        "output_schema": {
            "type": "object",
            "required": ["symbol", "strategy", "metrics"],
            "properties": {
                "symbol": {"type": "string"},
                "strategy": {"type": "string"},
                "period": {"type": "string"},
                "data_source": {"type": "string", "enum": ["live", "cache", "seed"]},
                "metrics": {
                    "type": "object",
                    "properties": {
                        "cagr": {"type": "number"}, "sharpe": {"type": "number"},
                        "max_dd": {"type": "number"}, "total_return": {"type": "number"},
                        "bh_total_return": {"type": "number"}, "trades": {"type": "integer"},
                    },
                },
            },
        },
    },
    {
        "id": "market-read",
        "name": "Read an instrument",
        "description": (
            "A sourced read on one instrument — price and trend, fundamentals for "
            "equities, and the risks that cut against it. Evidence, never a "
            "recommendation to buy or sell."
        ),
        "tags": ["finance", "research"],
        "examples": ["What's the setup on XOM?", "Give me the bear case on TSLA"],
    },
]
