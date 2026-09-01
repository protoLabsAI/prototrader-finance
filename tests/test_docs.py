"""The docs must describe THIS plugin, not a previous one.

This repo has shipped, in three consecutive releases: a parity doc claiming a
capability whose writer had zero callers, a release note claiming "12 deliberately
skipped" against a table of 19, and a README describing a test harness that had
been replaced. Prose drifts silently because nothing executes it.

So these tests execute it: every list in README.md and docs/reference.md is parsed
and checked against what `register()` actually contributes. A tool renamed, a
verifier dropped, an event added — any of those fails the build until the docs
catch up.
"""

from __future__ import annotations

import re

import pytest
import yaml

from conftest import ROOT, load, plugin

README = (ROOT / "README.md").read_text()
REFERENCE = (ROOT / "docs" / "reference.md").read_text()
OPERATING = (ROOT / "docs" / "operating.md").read_text()
PARITY = (ROOT / "docs" / "sdk-parity.md").read_text()
DOCS = {"README.md": README, "docs/reference.md": REFERENCE,
        "docs/operating.md": OPERATING, "docs/sdk-parity.md": PARITY}


@pytest.fixture(scope="module")
def registered():
    from conftest import FakeRegistry

    r = FakeRegistry(plugin_id="prototrader-finance")
    plugin.register(r)
    return r


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


def _ticked(text: str) -> set[str]:
    """Every `backticked` token in a chunk of markdown."""
    return set(re.findall(r"`([a-z0-9_./-]+)`", text))


def _section(doc: str, heading: str) -> str:
    """The body under a `## heading` / `### heading`, up to the next same-or-higher one."""
    m = re.search(rf"^(#{{2,3}}) {re.escape(heading)}\s*$", doc, re.M)
    assert m, f"missing heading: {heading}"
    depth = len(m.group(1))
    rest = doc[m.end():]
    nxt = re.search(rf"^#{{1,{depth}}} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


# ── the contributed surface ──────────────────────────────────────────────────

def test_readme_tool_list_matches_the_registered_tools(registered):
    row = next(ln for ln in README.splitlines() if ln.startswith("| **Tools**"))
    assert _ticked(row) == {t.name for t in registered.tools}


def test_reference_documents_every_tool_exactly_once(registered):
    documented = _ticked(_section(REFERENCE, "Tools (13)"))
    names = {t.name for t in registered.tools}
    missing = names - documented
    assert not missing, f"undocumented tools: {sorted(missing)}"
    # Guard the count in the heading too.
    assert f"Tools ({len(names)})" in REFERENCE, f"heading says the wrong count; there are {len(names)}"


def test_documented_verifiers_match_the_registered_ones(registered):
    for doc_name, body in (("README.md", README), ("docs/reference.md", _section(REFERENCE, "Goal verifiers (5)"))):
        chunk = next(ln for ln in body.splitlines() if "portfolio_return" in ln) if doc_name == "README.md" else body
        assert set(registered.verifiers) <= _ticked(chunk), f"{doc_name} omits a verifier"
    assert f"Goal verifiers ({len(registered.verifiers)})" in REFERENCE


def test_documented_events_match_the_code_and_the_manifest(manifest):
    ev = load("events")
    documented = _ticked(_section(REFERENCE, "Events (7)"))
    assert set(ev.TOPICS) <= documented, f"undocumented topics: {sorted(set(ev.TOPICS) - documented)}"
    assert set(ev.TOPICS) == set(manifest["emits"])
    assert f"Events ({len(ev.TOPICS)})" in REFERENCE


def test_every_documented_event_states_a_payload():
    """A topic with no payload shape is a topic nobody can subscribe to usefully."""
    for line in _section(REFERENCE, "Events (7)").splitlines():
        if line.startswith("| `") and "Payload" not in line:
            cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
            assert cells[1].startswith("{") or cells[1] == "—", f"no payload shape: {cells[0]}"


def test_documented_a2a_skills_match(registered):
    documented = _ticked(_section(REFERENCE, "A2A card skills"))
    assert {s["id"] for s in registered.a2a_skills} <= documented


def test_documented_settings_match_the_manifest(manifest):
    documented = _ticked(_section(REFERENCE, "Settings (ADR 0019)"))
    assert {s["key"] for s in manifest["settings"]} <= documented
    assert manifest["config_section"] in REFERENCE


def test_documented_skills_and_workflows_exist_on_disk():
    body = _section(REFERENCE, "Subagents, skills, workflows")
    for d in (ROOT / "skills").iterdir():
        if d.is_dir():
            assert f"`{d.name}`" in body, f"skill {d.name} is undocumented"
            assert (d / "SKILL.md").is_file(), f"{d.name} has no SKILL.md — it won't load"
    for w in (ROOT / "workflows").glob("*.yaml"):
        assert f"`{w.stem}`" in body, f"workflow {w.stem} is undocumented"


def test_documented_subagents_match(registered):
    body = _section(REFERENCE, "Subagents, skills, workflows")
    assert {s.name for s in registered.subagents} <= _ticked(body)


def test_documented_routes_are_actually_served(registered):
    """Every route the reference advertises must exist, or a reader integrates
    against a 404."""
    served = set()
    for prefix, router in registered.routers:
        for route in router.routes:
            served.add(f"{prefix if prefix is not None else '/plugins/prototrader-finance'}{route.path}")
    body = _section(REFERENCE, "HTTP routes")
    documented = {
        m.group(1).strip().replace("…", "/api/plugins/prototrader-finance")
        for m in re.finditer(r"`(?:GET|POST) ([^`?]+)", body)
    }
    missing = documented - served
    assert not missing, f"documented but not served: {sorted(missing)}\nserved: {sorted(served)}"

    # ...and the inverse. Checking one direction only lets a NEW endpoint ship
    # undocumented, which is the direction that actually happens — nobody adds a
    # doc row for a route they forgot to write about.
    undocumented = served - documented
    assert not undocumented, f"served but undocumented in docs/reference.md: {sorted(undocumented)}"


# ── claims with numbers in them ──────────────────────────────────────────────

def test_seed_symbol_count_claim_is_true():
    import json

    n = len(json.load(open(ROOT / "seed" / "MANIFEST.json"))["symbols"])
    assert f"{n} symbols" in README, f"README's seed count is stale; there are {n}"


def test_min_host_version_claim_matches_the_manifest(manifest):
    assert f"v{manifest['min_protoagent_version']}" in README


def test_install_snippet_pins_the_current_release(manifest):
    """A snippet pinning a version that no longer exists sends a new user to a
    release they can't install."""
    m = re.search(r"--ref (v[\d.]+|main)", README)
    assert m, "the install snippet should show an explicit --ref"
    ref = m.group(1)
    assert ref != "main", "pin a release tag, not a moving branch"
    assert ref == f"v{manifest['version']}", f"README pins {ref}, manifest is v{manifest['version']}"


def test_test_count_claim_is_a_floor_that_holds():
    m = re.search(r"(\d+)\+ tests", README)
    if m:
        collected = len(list((ROOT / "tests").glob("test_*.py")))
        assert collected >= 5, "suite shrank unexpectedly"


# ── links ────────────────────────────────────────────────────────────────────

def test_no_dead_relative_links():
    """A broken link in a README is the cheapest possible way to look unmaintained."""
    for name, text in DOCS.items():
        base = (ROOT / name).parent
        for target in re.findall(r"\]\((\.[^)#]+)", text):
            assert (base / target).exists(), f"{name}: dead link → {target}"


def test_docs_do_not_point_at_the_deprecated_host_as_a_starting_point():
    """protoTrader (the fork) is deprecated and its origin no longer resolves.
    Naming it as history is fine; recommending it is not."""
    assert "deprecated" in README.lower()
    for name, text in DOCS.items():
        assert "protoTrader-in-space" not in text, f"{name} references a repo that no longer exists"
