"""Behavioral / Shadow-Account tools (Slice 5)."""

from __future__ import annotations

import asyncio

from langchain_core.tools import tool


def _money(x):
    return f"${x:,.0f}" if x not in (None, float("inf")) else ("∞" if x == float("inf") else "n/a")


@tool
async def analyze_trade_journal(csv_text: str) -> str:
    """Analyze a trade journal (CSV) into a behavioral profile + bias flags.

    The CSV needs columns: symbol, side (buy/sell), qty, price, and ideally date.
    Pass the file's contents (read it first with your file tools, or paste it).
    Returns realized stats (win rate, profit factor, expectancy), hold-time
    asymmetry, and the biases that cost money (loss aversion, asymmetric losers,
    revenge sizing, cutting winners early).

    Args:
        csv_text: the journal CSV content.
    """
    def _run() -> str:
        try:
            from . import engine
        except ImportError:  # pragma: no cover
            import engine  # type: ignore
        try:
            p = engine.profile(csv_text)
            if "error" in p:
                return f"Error: {p['error']}"
            pf = "∞" if p["profit_factor"] in (None, float("inf")) else f"{p['profit_factor']:.2f}"
            lines = [
                f"**Trade journal — {p['trades']} round-trips** "
                f"({p['date_range'][0][:10]} → {p['date_range'][1][:10]}; {len(p['symbols'])} symbols)",
                "",
                "| metric | value |",
                "|---|---|",
                f"| win rate | {p['win_rate']*100:.0f}% |",
                f"| total realized P&L | {_money(p['total_pnl'])} |",
                f"| profit factor | {pf} |",
                f"| expectancy / trade | {_money(p['expectancy'])} |",
                f"| avg win / avg loss | {_money(p['avg_win'])} / {_money(p['avg_loss'])} |",
                f"| avg hold — wins / losses | "
                f"{p['avg_win_hold_days'] and round(p['avg_win_hold_days'],1)}d / "
                f"{p['avg_loss_hold_days'] and round(p['avg_loss_hold_days'],1)}d |",
                f"| largest win / loss | {_money(p['largest_win'])} / {_money(p['largest_loss'])} |",
                f"| max cumulative-P&L drawdown | {_money(p['max_pnl_drawdown'])} |",
            ]
            if p["flags"]:
                lines += ["", "**Behavioral flags:**"] + [f"- {f}" for f in p["flags"]]
            else:
                lines += ["", "_No major bias flags — the disciplined profile._"]
            lines += ["", "_A behavioral read of past trades — analysis, not advice._"]
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            return f"Error: analyze_trade_journal failed: {e}"

    return await asyncio.to_thread(_run)


def get_behavioral_tools() -> list:
    return [analyze_trade_journal]
