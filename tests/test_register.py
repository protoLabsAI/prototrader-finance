"""`register()` end to end, with no host — the seam the loader actually calls.

A suite that only asserts modules import will not catch a dead plugin: the host
calls exactly one function, and everything it contributes is a side effect on the
registry. So drive that function and assert the side effects.

This is the test that catches a host import creeping back to module scope: if one
does, `register()` raises here rather than at 3am on someone's box.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import ROOT, plugin


@pytest.fixture
def manifest():
    return yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


@pytest.fixture
def registered(registry):
    plugin.register(registry)
    return registry


# ── the contract ─────────────────────────────────────────────────────────────

def test_register_runs_with_no_host(registered):
    assert registered.tools, "no tools registered"
    assert registered.subagents, "no desk subagents registered"
    assert registered.routers, "no routers registered"


def test_every_tool_has_a_real_description(registered):
    """`@tool` takes its description from the docstring, and an f-string
    "docstring" leaves `__doc__` None — the tool then ships nameless to the model."""
    for t in registered.tools:
        desc = (getattr(t, "description", "") or "").strip()
        assert len(desc) > 20, f"{getattr(t, 'name', t)!r} has no usable description"


def test_tool_names_are_unique(registered):
    names = [t.name for t in registered.tools]
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"


def test_desk_subagent_specs_are_well_formed():
    from conftest import load

    for spec in load("desk.subagents").DESK_SPECS:
        assert spec["name"] and spec["description"]
        assert spec["system_prompt"].strip()
        assert isinstance(spec["tools"], list) and spec["tools"]


# ── the view rules ───────────────────────────────────────────────────────────

def test_page_and_data_routers_use_distinct_prefixes(registered):
    """Rule 1/2: the PAGE is public (an iframe load carries no bearer), the DATA
    routes sit under /api so they inherit the operator bearer gate."""
    prefixes = [p for p, _ in registered.routers]
    assert None in prefixes or any(p is None for p in prefixes), "page router must take the default public prefix"
    assert any(p == "/api/plugins/prototrader-finance" for p in prefixes), "data router must be gated under /api"


def test_declared_view_paths_are_actually_served(registered, manifest):
    """Rule 1, tested against the ACTUAL registered routes rather than the rule.

    A manifest path no router answers is a blank rail icon with no error anywhere
    — the exact failure mode that is invisible until someone clicks it.
    """
    served = set()
    for prefix, router in registered.routers:
        for route in router.routes:
            served.add(f"{prefix or '/plugins/prototrader-finance'}{route.path}")

    for view in manifest.get("views", []):
        assert view["path"] in served, (
            f"view {view['id']!r} declares {view['path']!r}, but the registered routers "
            f"serve only: {sorted(served)}"
        )
        assert not view["path"].startswith("/api/"), "a view PAGE behind the bearer gate blanks in the iframe"


# ── manifest coherence ───────────────────────────────────────────────────────

def test_manifest_and_pyproject_versions_match(manifest):
    import re

    pyproject = (ROOT / "pyproject.toml").read_text()
    version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    assert manifest["version"] == version, "bump the manifest and pyproject together"


def test_ships_disabled(manifest):
    """Enabling runs plugin code in-process with the agent's authority. That is the
    operator's decision, and this plugin adds a file-writing broker."""
    assert manifest["enabled"] is False


def test_config_section_is_a_string(manifest):
    assert isinstance(manifest["config_section"], str)


def test_declared_secrets_are_reachable_from_settings(manifest):
    """A secret with no Settings row can only be set by hand-editing secrets.yaml."""
    rows = {s["key"] for s in manifest.get("settings", [])}
    assert set(manifest.get("secrets", [])) <= rows


def test_seed_manifest_matches_declared_universe():
    from conftest import load

    md = load("marketdata")
    on_disk = json.loads((md.SEED_DIR / "MANIFEST.json").read_text())
    assert on_disk["symbols"] == sorted(on_disk["symbols"])
    assert on_disk["source"] and on_disk["fetched_at_iso"]


def test_dep_tiers_and_scopes_are_declared_the_way_the_host_parses_them(manifest):
    """`optional_pip` and `pip_scopes` are DERIVED from `requires_pip` mapping
    entries — they are not top-level keys. Written as top-level lists (the obvious
    guess) they parse to empty and the declaration silently does nothing.

    Both tiers matter here. yfinance/ccxt are `scope: host` because this plugin
    imports them IN-PROCESS — the default scope is the managed runtime, whose
    site-packages is separate, so `runtime` would pass the frozen install gate and
    then die at the first live fetch. And they are `optional` because every view
    still renders off the bundled snapshot without them, which is what keeps that
    honest scope from making the plugin uninstallable: the frozen refusal only ever
    inspects the HARD deps, so an optional host-scoped entry warns and proceeds.

    There are no hard deps left. pandas/numpy were exactly the combination that
    breaks — hard AND host-scoped — which is why the desktop app refused the plugin
    outright. `tests/test_no_runtime_deps.py` keeps them out.
    """
    assert "optional_pip" not in manifest and "pip_scopes" not in manifest, \
        "these are derived from requires_pip entries, not top-level keys"

    entries = manifest["requires_pip"]
    assert all(isinstance(e, dict) and e.get("pkg") for e in entries)
    by_pkg = {e["pkg"].split(">")[0].split("=")[0]: e for e in entries}
    assert set(by_pkg) == {"yfinance", "ccxt"}, f"unexpected declared deps: {sorted(by_pkg)}"
    for pkg, e in by_pkg.items():
        assert e.get("optional") is True, f"{pkg} must be optional or the frozen app refuses the plugin"
        assert e.get("scope") == "host", f"{pkg} is imported in-process, so scope must be host"
