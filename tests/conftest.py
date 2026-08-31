"""Test bootstrap — import the plugin exactly the way the host does, with no host.

protoAgent loads a plugin's ``__init__.py`` as a **synthetic package** whose
``__path__`` is the repo root (``graph/plugins/loader.py::_load_plugin_module``),
registering it in ``sys.modules`` *before* exec so ``from .store import …``
resolves. This does the same, under the same name the host would use, so the
suite exercises the production import graph rather than a test-only one.

That matters more than it sounds. The old suite loaded each engine standalone via
``spec_from_file_location("bt_engine", "backtest/engine.py")`` — no package, no
relative imports. Every test passed against a module graph that production never
builds, so a broken relative import was invisible to CI.

Executing ``__init__.py`` is safe with no host because every host-only import
lives inside ``register()`` or a function body, never at module top. The suite
therefore needs only ``requirements-dev.txt``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _install_host_stubs() -> None:
    """Stand in for the handful of host symbols the plugin genuinely needs.

    The alternative is wrapping `register()` in a try/except and asserting nothing
    — which is how a plugin ends up green in CI and dead in production. Stubbing
    the type lets the suite assert that three subagents really do get registered.

    Kept deliberately tiny: if this file grows, the plugin is leaning on the host
    too hard for a bundle that claims to be host-free testable.
    """
    import types

    if "graph.subagents.config" in sys.modules:
        return
    graph = sys.modules.setdefault("graph", types.ModuleType("graph"))
    graph.__path__ = []  # a package, so `graph.subagents` can hang off it
    subagents = sys.modules.setdefault("graph.subagents", types.ModuleType("graph.subagents"))
    subagents.__path__ = []
    graph.subagents = subagents

    cfg_mod = types.ModuleType("graph.subagents.config")

    class SubagentConfig:  # noqa: D401 - a stand-in for the host dataclass
        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.name = kw.get("name")

    cfg_mod.SubagentConfig = SubagentConfig
    sys.modules["graph.subagents.config"] = cfg_mod
    subagents.config = cfg_mod


_install_host_stubs()

# The host's own sanitizer: "protoagent_plugin_" + non-identifier chars → "_".
PKG = "protoagent_plugin_prototrader_finance"


def _install_package() -> object:
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod  # before exec — relative imports resolve the parent here
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(PKG, None)
        raise
    return mod


plugin = _install_package()


def load(dotted: str):
    """Import a plugin submodule by its in-package path: ``load("broker.engine")``."""
    return importlib.import_module(f"{PKG}.{dotted}")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the plugin's store at a tmp dir for EVERY test.

    Autouse on purpose: a test that writes a paper fill into the developer's real
    instance store is a bug that only shows up as mysterious state later. The
    memoized home is reset on both sides so ordering can't leak a path.
    """
    st = load("store")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)  # created up front so a test can drop a file in it
    monkeypatch.setenv("PROTOTRADER_FINANCE_HOME", str(home))
    st.reset_cache()
    yield home
    st.reset_cache()


@pytest.fixture
def offline(monkeypatch):
    """Make every live provider call fail, so tests exercise the fallback tiers."""
    md = load("marketdata")

    def _boom(*a, **k):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(md, "_fetch_live", _boom)
    return md


class FakeRegistry:
    """Captures what ``register()`` contributes — the host seam, host-free."""

    def __init__(self, config: dict | None = None, plugin_id: str = "prototrader-finance"):
        self.config = config or {}
        self.plugin_id = plugin_id
        self.tools: list = []
        self.subagents: list = []
        self.routers: list = []
        self.skill_dirs: list = []
        self.workflow_dirs: list = []
        self.chat_commands: dict = {}
        self.verifiers: dict = {}
        self.a2a_skills: list = []
        self.watch_hooks: list = []
        self.lifecycle_hooks: list = []
        self.subscriptions: dict = {}
        self.emitted: list = []
        self.host = None

    def register_tool(self, t):
        self.tools.append(t)

    def register_tools(self, ts):
        self.tools.extend(ts)

    def register_subagent(self, cfg):
        self.subagents.append(cfg)

    def register_router(self, router, prefix=None):
        self.routers.append((prefix, router))

    def register_skill_dir(self, path):
        self.skill_dirs.append(str(path))

    def register_workflow_dir(self, path):
        self.workflow_dirs.append(str(path))

    def register_chat_command(self, name, handler):
        self.chat_commands[name] = handler

    def register_goal_verifier(self, name, fn, description=""):
        self.verifiers[name] = (fn, description)

    def register_a2a_skill(self, spec):
        self.a2a_skills.append(spec)

    def register_watch_hook(self, **kw):
        self.watch_hooks.append(kw)

    def register_lifecycle_hook(self, **kw):
        self.lifecycle_hooks.append(kw)

    def on(self, topic, handler):
        self.subscriptions.setdefault(topic, []).append(handler)

    def emit(self, topic, data=None):
        self.emitted.append((topic, data))


@pytest.fixture
def registry():
    return FakeRegistry()
