"""The console view: the four plugin-view rules, and the API the page calls.

Tested against the routers as `register()` actually mounts them, not against the
rules restated in a fixture. A previous plugin bug in this org passed a suite that
asserted the path the rules *said* to use while the router served a different one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import ROOT, load

PUBLIC = "/plugins/prototrader-finance"
GATED = "/api/plugins/prototrader-finance"


@pytest.fixture
def client():
    dash = load("dashboard")
    app = FastAPI()
    app.include_router(dash.build_dashboard_router({"default_benchmark": "SPY"}), prefix=PUBLIC)
    app.include_router(dash.build_data_router({"default_benchmark": "SPY"}), prefix=GATED)
    return TestClient(app)


@pytest.fixture
def page_html(client):
    return client.get(f"{PUBLIC}/dashboard").text


# ── the four rules ───────────────────────────────────────────────────────────

def test_rule1_page_is_served_at_the_declared_path(client):
    r = client.get(f"{PUBLIC}/dashboard")
    assert r.status_code == 200
    assert "Quant" in r.text and "<title>" in r.text


def test_rule2_data_is_gated_and_the_page_is_not(client):
    """The page must NOT be under /api (an iframe load carries no bearer) and the
    data must be (or anyone reaching the port reads the book without a token)."""
    assert client.get(f"{GATED}/dashboard").status_code == 404
    assert client.get(f"{PUBLIC}/overview").status_code == 404


def test_rule3_page_is_slug_aware(page_html):
    """Through the fleet proxy the iframe loads at /agents/<slug>/plugins/... — an
    absolute path there hits the HUB agent, not this one."""
    assert 'location.pathname.split("/plugins/")[0]' in page_html
    assert "window.__base" in page_html
    # No absolute /_ds or /api literal that skips the base.
    for bad in ('href="/_ds/', "src='/_ds/", 'import("/_ds/', 'fetch("/api/'):
        assert bad not in page_html, f"hardcoded absolute path {bad!r} breaks the fleet proxy"


def test_rule4_uses_the_ds_kit_rather_than_hand_rolling(page_html):
    assert "/_ds/plugin-kit.css" in page_html
    assert "/_ds/plugin-kit.js" in page_html
    assert "kit.apiFetch" in page_html
    assert "initPluginView" in page_html
    # The kit owns the handshake and the theme map; hand-rolling either is the bug
    # this rule exists to prevent.
    assert ':root{--pl-' not in page_html.replace(" ", "")
    assert 'addEventListener("message"' not in page_html


def test_page_carries_no_hardcoded_colors(page_html):
    """Every colour must come from a --pl-* token, or the view ignores the
    operator's theme and looks wrong in half of all consoles."""
    import re

    style = page_html.split("<style>", 1)[1].split("</style>", 1)[0]
    hits = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)", style)
    assert not hits, f"hardcoded colours in the page stylesheet: {hits}"


# ── the data API ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/strategies", "/universe", "/overview", "/ledger"])
def test_endpoints_answer_offline(client, offline, path):
    """Every panel must render with no provider reachable — that is the whole
    point of the seed tier, and the state a demo is most likely to be in."""
    d = client.get(GATED + path).json()
    assert d["ok"] is True, d


def test_overview_reports_provenance(client, offline):
    d = client.get(f"{GATED}/overview").json()
    assert d["provenance"]["source"] == "seed"
    assert d["market"], "the market strip should be populated from the snapshot"
    assert all("spark" in r for r in d["market"])


def test_backtest_offline_runs_off_the_snapshot(client, offline):
    d = client.get(f"{GATED}/backtest?symbol=SPY&strategy=ma_cross&period=1y").json()
    assert d["ok"] is True
    assert len(d["equity"]) == len(d["dates"]) == len(d["benchmark"])
    assert d["provenance"]["source"] == "seed"


def test_a_bad_request_is_data_not_a_500(client, offline):
    """A broken panel must degrade to a readable message, never a blank surface."""
    for path in ("/backtest?strategy=nope", "/backtest?symbol=NOSUCHTICKER"):
        r = client.get(GATED + path)
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert r.json()["error"]


def test_ledger_flags_the_sample_book(client, offline):
    """On a clean install the ledger falls back to the bundled book. If that ever
    stops being labelled, the view is asserting someone owns positions they don't."""
    d = client.get(f"{GATED}/ledger").json()
    assert d["demo"] is True
    assert "NOT real fills" in (d["note"] or "")
    assert d["gate"]["armed"] is False


def test_real_state_beats_the_sample_book(client, offline, isolated_home):
    import json

    load("store")
    (isolated_home / "broker_paper.json").write_text(json.dumps(
        {"cash": 1000.0, "realized_pnl": 5.0, "positions": {"AAPL": {"qty": 3, "avg_price": 100.0}},
         "orders": [{"id": "1", "symbol": "AAPL", "side": "buy", "qty": 3, "price": 100.0}]}))
    d = client.get(f"{GATED}/ledger").json()
    assert d["demo"] is False
    assert d["cash"] == 1000.0
    assert [p["symbol"] for p in d["positions"]] == ["AAPL"]


def test_manifest_declares_exactly_one_view():
    """One rail icon with tabs, not four icons — the console rail is shared space
    and every extra surface is one more thing to maintain."""
    import yaml

    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert len(manifest["views"]) == 1
    assert manifest["views"][0]["path"] == f"{PUBLIC}/dashboard"


def test_ledger_normalizes_both_order_shapes(client, offline, isolated_home):
    """The live engine writes fill_price/commission; the bundled book writes
    price/fee. The view reads ONE shape, so both must arrive normalized — the
    alternative is a real fill rendering its price and fee as dashes."""
    import json

    load("store")
    (isolated_home / "broker_paper.json").write_text(json.dumps({
        "cash": 5000.0, "realized_pnl": 12.0, "positions": {},
        "orders": [{"id": "o1", "ts": "2026-08-30T00:00:00Z", "symbol": "AAPL", "side": "buy",
                    "qty": 10, "fill_price": 200.25, "commission": 0.2,
                    "notional": 2002.5, "status": "filled"}]}))
    o = client.get(f"{GATED}/ledger").json()["orders"][0]
    assert o["price"] == 200.25, "engine fill_price must surface as price"
    assert o["fee"] == 0.2, "engine commission must surface as fee"
    assert o["notional"] == 2002.5


# ── the offline promise, measured rather than asserted ───────────────────────

def _count_live(monkeypatch, md):
    """Record every provider call instead of making one."""
    calls = []

    def _boom(symbol, *a, **k):
        calls.append(symbol)
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(md, "_fetch_live", _boom)
    return calls


def test_a_default_paint_makes_zero_provider_calls(client, isolated_home, monkeypatch):
    """"A dashboard paint never blocks on the network" — counted, not trusted.

    v0.3.0 asserted this and broke it twice: /factors issued 154 live calls because
    factors.evaluate() had no `prefer` parameter, and every pane went live-first on
    a CLEAN INSTALL because prefer="cache" checked only the cache, never the seed.
    A test that merely asserts `ok is True` passes in both cases — the fallback
    still produces data, just after hammering a provider that isn't there.
    """
    md = load("marketdata")
    for path in ("/universe", "/overview", "/ledger", "/factors", "/backtest?symbol=SPY&period=1y"):
        calls = _count_live(monkeypatch, md)
        d = client.get(GATED + path).json()
        assert d["ok"] is True, (path, d)
        assert calls == [], f"{path} made {len(calls)} provider calls on a default paint: {calls[:6]}"


def test_refresh_is_the_only_thing_that_goes_live(client, isolated_home, monkeypatch):
    """...and ?refresh=1 must still reach the provider, or Refresh is a lie."""
    md = load("marketdata")
    calls = _count_live(monkeypatch, md)
    client.get(f"{GATED}/overview?refresh=1")
    assert calls, "refresh=1 should attempt a live fetch"


def test_every_data_pane_reports_its_provenance(client, offline):
    """Each pane marks prices off the tiered path, so each owes the disclosure.
    The Ledger shipped without one, leaving its marks under whatever chip the
    Overview had last set."""
    for path in ("/overview", "/factors", "/ledger"):
        d = client.get(GATED + path).json()
        assert d.get("provenance"), f"{path} returns no provenance"
        assert d["provenance"]["source"] in ("live", "cache", "seed", "none")


def test_the_page_sets_the_chip_from_every_pane(page_html):
    """A payload carrying provenance the page never reads is the same bug."""
    for loader in ("loadOverview", "loadBacktest", "loadFactors", "loadLedger"):
        body = page_html.split(f"async function {loader}", 1)[1].split("\nasync function", 1)[0]
        assert "setProv(" in body, f"{loader} never updates the provenance chip"
