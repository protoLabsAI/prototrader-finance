"""Test bootstrap — the host's own harness, vendored.

`tests/_plugin_testkit.py` is a verbatim copy of protoAgent's
`graph/plugins/testkit.py`, which is what the scaffolder vendors into a standalone
plugin. Using it rather than a local imitation matters for one concrete reason:
its `FakeRegistry` is held to a parity test against the real `PluginRegistry`, and
its `install_host_stubs` is **non-clobbering** — it leaves a genuinely importable
host module alone.

v0.3.0 hand-rolled all three. The local fake skipped the `<plugin-id>:` namespacing
that `register_goal_verifier` applies, so a test happily asserted a key production
never produces; and the local stubber's `sys.modules.setdefault("graph", ...)` would
shadow a real host if one were ever on the path.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_testkit import FakeRegistry, install_host_stubs, load_plugin, plugin_module_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ID = "prototrader-finance"
PKG = plugin_module_name(PLUGIN_ID)

# Stubs BEFORE the plugin loads, so its host imports resolve. `SubagentConfig` and
# `VerifyResult` are the two host types this plugin actually constructs, so they
# need real behaviour rather than the generic attribute stub.
install_host_stubs(
    {
        "graph.subagents.config": {"SubagentConfig": lambda **kw: type("SubagentConfig", (), kw)()},
        "graph.goals": {
            "VerifyResult": lambda met, reason="", evidence="": type(
                "VerifyResult", (), {"met": met, "reason": reason, "evidence": evidence}
            )()
        },
    }
)

plugin = load_plugin(ROOT, PLUGIN_ID)


def load(dotted: str):
    """Import a plugin submodule by its in-package path: ``load("broker.engine")``."""
    return importlib.import_module(f"{PKG}.{dotted}")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the plugin's store at a tmp dir for EVERY test.

    Autouse on purpose: a test that writes a paper fill into the developer's real
    instance store is a bug that only shows up as mysterious state later.
    """
    st = load("store")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PROTOTRADER_FINANCE_HOME", str(home))
    st.reset_cache()
    yield home
    st.reset_cache()


@pytest.fixture
def offline(monkeypatch):
    """Make every live provider call fail, so tests exercise the fallback tiers."""
    md = load("marketdata")
    monkeypatch.setattr(md, "_fetch_live", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network disabled")))
    return md


@pytest.fixture
def registry():
    return FakeRegistry(plugin_id=PLUGIN_ID)
