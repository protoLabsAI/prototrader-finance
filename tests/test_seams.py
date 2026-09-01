"""The seam surface — what this plugin contributes to the host, pinned.

The point of these is that the CONTRACT can't drift silently: a topic renamed in
code but not the manifest, a verifier that stops being registered, an A2A skill
missing a field the card build hard-indexes. Each of those fails at boot or, worse,
degrades to a capability that is simply absent with nothing in the logs.
"""

from __future__ import annotations

import inspect

import pytest
import yaml

from conftest import ROOT, load, plugin


@pytest.fixture
def manifest():
    return yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


@pytest.fixture
def registered(registry):
    plugin.register(registry)
    return registry


# ── events ───────────────────────────────────────────────────────────────────

def test_emitted_topics_match_the_manifest(manifest):
    """A topic that exists in code but not `emits:` is invisible to operators and
    to any plugin that would have subscribed."""
    assert set(load("events").TOPICS) == set(manifest["emits"])


def test_subscriptions_are_declared_and_namespaced(registered, manifest):
    assert set(registered.handlers) == set(manifest["subscribes"])
    for topic in registered.handlers:
        assert topic.startswith("prototrader-finance.")


def test_emit_is_safe_with_no_registry():
    """Telemetry must never be able to fail a fill."""
    ev = load("events")
    ev.bind(None)
    ev.emit(ev.ORDER_FILLED, symbol="AAPL")  # must not raise


# ── verifiers ────────────────────────────────────────────────────────────────

def test_all_verifiers_registered_with_descriptions(registered):
    """Names are passed UNQUALIFIED; the real `PluginRegistry` prefixes them with
    `<plugin-id>:`. The host's own FakeRegistry does not apply that prefix (its
    parity contract covers method signatures, not this behaviour), so asserting a
    namespaced key here would test the fake rather than the plugin."""
    v = load("verifiers")
    assert set(registered.verifiers) == set(v.VERIFIERS)
    assert all(":" not in n for n in registered.verifiers), "pass bare names; the host namespaces"
    for name in registered.verifiers:
        assert registered.verifier_meta[name]["description"].strip(), \
            f"{name} has no description — unpickable in the console goal creator"


def test_verifiers_are_async_two_arg(registered):
    for name, fn in registered.verifiers.items():
        assert inspect.iscoroutinefunction(fn), f"{name} must be async (spec, ctx)"
        assert len(inspect.signature(fn).parameters) == 2


def test_verifier_shape_is_documented():
    """Args are declarative data, never shell or eval (ADR 0028 D3)."""
    src = open(load("verifiers").__file__).read()
    for banned in ("eval(", "exec(", "subprocess", "os.system"):
        assert banned not in src


async def _run(fn, args=None):
    return await fn({"args": args or {}}, None)


def test_tripwire_verifiers_answer_offline(offline, isolated_home):
    """A verifier that needs the network is a verifier that lies when the network
    is down — it would report "not met" for a breach it simply couldn't see."""
    import asyncio

    v = load("verifiers")
    halted = asyncio.run(_run(v.trading_halted))
    assert halted.met is False and "clear" in halted.reason

    (isolated_home / "TRADING_HALT").touch()
    assert asyncio.run(_run(v.trading_halted)).met is True


def test_book_verifiers_refuse_to_grade_the_sample_book(offline, isolated_home):
    """With no real fills the Ledger falls back to the bundled sample book, which
    ships already up 5.6%. Grading a return goal against it would mark the goal
    achieved before a single order was placed — a confident number about money
    nobody has."""
    import asyncio

    v = load("verifiers")
    for fn, args in ((v.portfolio_return, {"min_return": 0.01}), (v.max_drawdown, {"limit": 0.15})):
        r = asyncio.run(_run(fn, args))
        assert r.met is False
        assert "sample" in r.reason, f"{fn.__name__} graded the demo book: {r.reason}"


def test_book_verifiers_grade_a_real_book(offline, isolated_home):
    """...and once real state exists they measure it."""
    import asyncio
    import json

    load("store")
    (isolated_home / "broker_paper.json").write_text(json.dumps(
        {"cash": 110_000.0, "realized_pnl": 10_000.0, "positions": {}, "orders": [{"id": "1"}]}))
    v = load("verifiers")
    r = asyncio.run(_run(v.portfolio_return, {"min_return": 0.05}))
    assert "total return" in r.reason, r.reason


def test_sample_book_equity_is_never_recorded_as_a_metric(offline, isolated_home):
    """The metric series has no demo flag, so anything written there reads as real
    — including in the Overview sparkline and in max_drawdown's high-water mark."""
    assert load("metrics").snapshot_equity({}) is None, "the sample book's equity must not be recorded"


def test_stale_data_verifier_trips_on_the_snapshot(offline):
    """Served off a months-old bundled snapshot IS the stale case."""
    import asyncio

    v = load("verifiers")
    r = asyncio.run(_run(v.data_is_stale, {"max_age_h": 0.001, "symbol": "SPY"}))
    assert r.met is True
    assert "snapshot" in r.reason or "cached" in r.reason


# ── A2A card skills ──────────────────────────────────────────────────────────

def test_a2a_skills_carry_every_hard_indexed_field(registered):
    """The card build hard-indexes id/name/description — a missing one is a
    KeyError at boot, attributed to the host rather than to this plugin."""
    assert registered.a2a_skills
    for spec in registered.a2a_skills:
        assert spec["id"] and spec["name"] and spec["description"]
    ids = [s["id"] for s in registered.a2a_skills]
    assert len(ids) == len(set(ids))


def test_typed_a2a_skill_declares_both_halves(registered):
    """output_schema without result_mime isn't enforced by the finalizer."""
    for spec in registered.a2a_skills:
        if "output_schema" in spec:
            assert spec.get("result_mime"), f"{spec['id']} has a schema but no result_mime"


# ── the rest of the seam surface ─────────────────────────────────────────────

def test_chat_command_registered_and_user_only(registered):
    assert "quant" in registered.chat_commands
    assert inspect.iscoroutinefunction(registered.chat_commands["quant"])
    tool_names = {t.name for t in registered.tools}
    assert "quant" not in tool_names, "/quant is a user affordance, not a model-invokable tool"


def test_chat_command_answers_offline(registered, offline):
    import asyncio

    reply = asyncio.run(registered.chat_commands["quant"]("SPY ma_cross 1y", "sess"))
    assert "SPY" in reply and "Sharpe" in reply
    assert "not advice" in reply


def test_chat_command_degrades_readably(registered, offline):
    import asyncio

    reply = asyncio.run(registered.chat_commands["quant"]("NOSUCHTICKER", "sess"))
    assert "failed" in reply and "SPY" in reply, "an error should name what IS available"


def test_lifecycle_and_watch_hooks_registered(registered):
    assert registered.lifecycle_hooks and registered.watch_hooks
    on_app_loaded, _on_agent_active, on_system_wake = registered.lifecycle_hooks[0]
    assert on_app_loaded and on_system_wake
    on_met, _expired, on_stalled, _changed = registered.watch_hooks[0]
    assert on_met and on_stalled


def test_test_connection_route_matches_the_config_section(registered, manifest):
    """ADR 0029: the console looks for /api/config/test-<config_section> exactly."""
    assert manifest["test"] is True
    served = {f"{p or ''}{r.path}" for p, router in registered.routers for r in router.routes}
    assert f"/api/config/test-{manifest['config_section']}" in served
    # Registered with an EMPTY prefix, not "/api": a non-conforming prefix logs a
    # registry warning on every boot, and "" is the case that check skips.
    prefixes = [p for p, _ in registered.routers]
    assert "" in prefixes and "/api" not in prefixes


def test_seams_never_hard_fail_without_a_host(registered):
    """Every SDK-backed seam is an enhancement. Host-free, they must no-op, not
    raise — this whole suite runs with no protoAgent present and register() ran."""
    assert load("metrics").equity_history() == []
    load("metrics").record_equity(1.0)          # no host: swallowed
    assert load("watch_hooks").arm_tripwires({}) == 0


# ── the parity doc must describe reality ─────────────────────────────────────

def _parity_rows() -> dict[str, str]:
    """{seam: mark} parsed from docs/sdk-parity.md's tables."""
    rows = {}
    for line in (ROOT / "docs" / "sdk-parity.md").read_text().splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[1] not in ("✅", "⛔"):
            continue
        for seam in cells[0].replace("`", "").split(" / "):
            rows[seam.strip()] = cells[1]
    return rows


def test_every_claimed_contribution_seam_is_actually_registered(registered):
    """A ✅ in the doc for a seam nothing calls is the failure mode this file
    exists to prevent: documentation that reads as capability."""
    claimed = {s for s, mark in _parity_rows().items() if mark == "✅"}
    evidence = {
        "register_tools": registered.tools,
        "register_subagent": registered.subagents,
        "register_router": registered.routers,
        "register_chat_command": registered.chat_commands,
        "register_goal_verifier": registered.verifiers,
        "register_watch_hook": registered.watch_hooks,
        "register_lifecycle_hook": registered.lifecycle_hooks,
        "register_a2a_skill": registered.a2a_skills,
        "emit": True,  # exercised by test_emit_is_safe_with_no_registry
        "on": registered.handlers,
    }
    for seam, got in evidence.items():
        if seam in claimed:
            assert got, f"docs/sdk-parity.md marks {seam} ✅ but register() contributed nothing"


def test_consumption_seams_marked_used_have_a_caller(registered):
    """The ✅ rows under "Consumption" name `graph.sdk` calls. A ✅ whose call site
    doesn't exist is documentation that reads as capability — v0.3.0 marked
    `knowledge_add` ✅ for a writer with ZERO callers, and the contribution-only
    gate below couldn't see it."""
    import re

    claimed = {s for s, mark in _parity_rows().items() if mark == "✅"}
    sources = "\n".join(
        open(load(m).__file__).read()
        for m in ("metrics", "knowledge", "chat", "a2a", "lifecycle", "watch_hooks",
                  "conn_test", "verifiers", "book", "store", "marketdata", "dashboard.api")
    )
    for seam in ("plugin_store", "record_metric", "metric_history", "knowledge_add", "create_watch"):
        if seam not in claimed:
            continue
        assert re.search(rf"sdk\.{seam}\(", sources), \
            f"docs/sdk-parity.md marks {seam} ✅ but nothing calls sdk.{seam}()"


def test_no_seam_is_used_without_being_documented(registered):
    """The inverse: a `registry.register_*` call with no row in the doc means the
    matrix has quietly stopped being the full picture."""
    import re

    documented = set(_parity_rows())
    src = "\n".join(
        open(load(m).__file__).read() for m in ("metrics", "knowledge", "chat", "a2a", "lifecycle", "watch_hooks", "conn_test", "events", "verifiers")
    ) + open(plugin.__file__).read()
    used = set(re.findall(r"registry\.(register_\w+)", src))
    undocumented = used - documented
    assert not undocumented, f"seams used but absent from docs/sdk-parity.md: {sorted(undocumented)}"


def test_skipped_seams_carry_a_rationale():
    """A ⛔ with no reason is just an omission with a symbol next to it."""
    for line in (ROOT / "docs" / "sdk-parity.md").read_text().splitlines():
        if line.startswith("| `") and "| ⛔ |" in line:
            reason = line.strip("|").split("|")[2].strip()
            assert len(reason) > 40, f"thin rationale: {line[:80]}"


def test_kill_switch_readout_agrees_with_the_gate(isolated_home, monkeypatch, tmp_path):
    """The status tool must check every location the GATE checks.

    v0.3.0 moved gate() to both locations and left the readout on one, so a halt
    file in the host config dir stopped trading while the tool printed "clear".
    Under-reporting protection on a safety surface is the wrong way to be wrong.
    """
    import re

    # Strip comments first: the fix's own comment NAMES the old call to explain why
    # it's gone, and a whole-file grep would forbid that. (Same trap as the
    # `_live_config_dir` assertion in test_store.py.)
    src = open(load("broker.tools").__file__).read()
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "_killswitch_path()" not in code, "readout checks fewer places than the gate"
    assert re.search(r"store\.killswitch_engaged\(\)", code)

    cfg = tmp_path / "hostcfg"
    cfg.mkdir()
    monkeypatch.setenv("PROTOAGENT_CONFIG_DIR", str(cfg))
    (cfg / "TRADING_HALT").touch()
    st = load("store")
    engine = load("broker.engine")
    assert st.killswitch_engaged() is not None
    ok, why = engine.Mandate(enabled=True, mode="paper").gate()
    assert ok is False and "KILL-SWITCH" in why


def test_mandate_armed_fires_on_the_transition_only(registry, isolated_home):
    """Declared in `emits:` and subscribed to, but v0.3.0 never emitted it — a
    mandate is a file, so there is no code path that 'arms' one to hook."""
    ev, engine = load("events"), load("broker.engine")
    ev.bind(registry)
    engine._last_armed = None

    armed = engine.Mandate(enabled=True, mode="paper")
    armed.gate()                                   # first observation = state, not a transition
    assert registry.emitted == []
    engine.Mandate(enabled=False).gate()           # armed -> disarmed
    assert [t for t, _ in registry.emitted] == [ev.MANDATE_ARMED]
    engine.Mandate(enabled=False).gate()           # no change, no repeat
    assert len(registry.emitted) == 1


def test_parity_doc_counts_match_its_own_tables():
    """The v0.3.0 notes claimed '12 deliberately not used' against a table holding
    19. Pin both numbers to the prose so the claim can't drift again."""
    import re

    text = (ROOT / "docs" / "sdk-parity.md").read_text()
    claim = re.search(r"\*\*(\d+) seams adopted, (\d+) deliberately skipped\.\*\*", text)
    assert claim, "the doc must state its own counts"
    assert int(claim.group(1)) == text.count("| ✅ |")
    assert int(claim.group(2)) == text.count("| ⛔ |")


def test_a_failing_tool_factory_does_not_take_the_others(monkeypatch, registry):
    """One loop meant a mid-loop raise aborted the group AFTER earlier factories
    had registered — a partial toolset live, with the log reporting zero."""
    import conftest

    broken = load("factors.tools")
    monkeypatch.setattr(broken, "get_factor_tools", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    conftest.plugin.register(registry)
    names = {t.name for t in registry.tools}
    assert "stock_quote" in names and "broker_place_order" in names, "unrelated tools were lost"
