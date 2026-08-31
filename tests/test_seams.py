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
    assert set(registered.subscriptions) == set(manifest["subscribes"])
    for topic in registered.subscriptions:
        assert topic.startswith("prototrader-finance.")


def test_emit_is_safe_with_no_registry():
    """Telemetry must never be able to fail a fill."""
    ev = load("events")
    ev.bind(None)
    ev.emit(ev.ORDER_FILLED, symbol="AAPL")  # must not raise


# ── verifiers ────────────────────────────────────────────────────────────────

def test_all_verifiers_registered_and_namespaced(registered):
    v = load("verifiers")
    assert set(registered.verifiers) == {f"prototrader-finance:{n}" for n in v.VERIFIERS}
    for _name, (fn, desc) in registered.verifiers.items():
        assert desc.strip(), "a verifier with no description is unpickable in the console"


def test_verifiers_are_async_two_arg(registered):
    for name, (fn, _d) in registered.verifiers.items():
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

    dd = asyncio.run(_run(v.max_drawdown, {"limit": 0.15}))
    assert dd.met is False and "drawdown" in dd.reason

    ret = asyncio.run(_run(v.portfolio_return, {"min_return": 0.02}))
    assert "total return" in ret.reason


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
    hook = registered.lifecycle_hooks[0]
    assert hook.get("on_app_loaded") and hook.get("on_system_wake")


def test_test_connection_route_matches_the_config_section(registered, manifest):
    """ADR 0029: the console looks for /api/config/test-<config_section> exactly."""
    assert manifest["test"] is True
    served = {f"{p or ''}{r.path}" for p, router in registered.routers for r in router.routes}
    assert f"/api/config/test-{manifest['config_section']}" in served


def test_seams_never_hard_fail_without_a_host(registered):
    """Every SDK-backed seam is an enhancement. Host-free, they must no-op, not
    raise — this whole suite runs with no protoAgent present and register() ran."""
    s = load("seams")
    assert s.equity_history() == []
    s.record_equity(1.0)          # no host: swallowed
    assert s.arm_tripwires({}) == 0


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
        "on": registered.subscriptions,
    }
    for seam, got in evidence.items():
        if seam in claimed:
            assert got, f"docs/sdk-parity.md marks {seam} ✅ but register() contributed nothing"


def test_no_seam_is_used_without_being_documented(registered):
    """The inverse: a `registry.register_*` call with no row in the doc means the
    matrix has quietly stopped being the full picture."""
    import re

    documented = set(_parity_rows())
    src = "\n".join(
        open(load(m).__file__).read() for m in ("seams", "events", "verifiers")
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
