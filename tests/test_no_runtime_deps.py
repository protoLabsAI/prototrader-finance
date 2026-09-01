"""The dependency-free claim, measured rather than asserted.

Every module in this plugin is imported here and ``sys.modules`` is then read: if
anything reached for pandas or numpy on the way, the import shows up and this fails.

That specific shape is on purpose. The claim "the plugin does not need pandas" is
exactly the kind that stays true in the README long after it has stopped being true
in the code — one convenient ``import pandas as pd`` inside a helper, added because
it was easier than adding an operation to :mod:`numeric`, and nothing else in the
suite would notice. Every other test in this repo would keep passing, because the
developer's machine has pandas installed.

It also guards the specific regression that motivated the rewrite: a ``scope: host``
dependency makes the plugin **uninstallable** on the frozen desktop app, refused by
the installer before it ever loads. The manifest assertion at the bottom is the other
half — a runtime-clean plugin that still declares the dep would be refused anyway.
"""

from __future__ import annotations

import sys

import pytest
import yaml

from conftest import PKG, ROOT

# Everything with an import side effect worth checking — the data path, the two
# engines, the routers, and the registration entry point the host actually calls.
MODULES = [
    "numeric",
    "marketdata",
    "store",
    "book",
    "metrics",
    "events",
    "knowledge",
    "chat",
    "lifecycle",
    "a2a",
    "conn_test",
    "backtest.engine",
    "backtest.tools",
    "factors.engine",
    "factors.tools",
    "broker.engine",
    "broker.tools",
    "behavioral.engine",
    "behavioral.tools",
    "data.tools",
    "desk.subagents",
    "dashboard.api",
    "dashboard.page",
]

BANNED = ("pandas", "numpy")


def test_no_plugin_module_imports_pandas_or_numpy():
    """Import the whole plugin in a fresh interpreter and see what came with it.

    A subprocess, not this one: the parity test legitimately imports pandas, and
    pytest shares ``sys.modules`` across the session, so checking in-process would
    pass or fail on test ordering rather than on the code.
    """
    import subprocess
    import textwrap

    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(ROOT / "tests")!r})
        from _plugin_testkit import install_host_stubs, load_plugin
        install_host_stubs({{
            "graph.subagents.config": {{"SubagentConfig": lambda **kw: type("S", (), kw)()}},
            "graph.goals": {{"VerifyResult": lambda met, reason="", evidence="": type("V", (), {{}})()}},
        }})
        load_plugin({str(ROOT)!r}, "prototrader-finance")
        import importlib
        for name in {MODULES!r}:
            importlib.import_module("{PKG}." + name)
        # Top-level names only: one pandas import drags in ~300 submodules, and a
        # failure message that lists them all buries the one fact worth reading.
        leaked = sorted({{m.split(".")[0] for m in sys.modules}} & set({BANNED!r}))
        print("LEAKED:" + ",".join(leaked))
        """
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, f"importing the plugin failed:\n{out.stderr[-3000:]}"
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("LEAKED:")]
    assert line, f"probe produced no verdict:\n{out.stdout}\n{out.stderr[-2000:]}"
    leaked = [m for m in line[0][len("LEAKED:") :].split(",") if m]
    assert not leaked, (
        "these modules pulled in a banned dependency: "
        + ", ".join(leaked)
        + " — the plugin must import on a frozen host that ships neither. "
        "Add the operation to numeric.py (with a parity test) instead."
    )


def test_manifest_declares_no_hard_host_scoped_dependency():
    """A HARD ``scope: host`` dep is refused outright by the frozen-app installer.

    Not a style preference: one such entry and the plugin cannot be installed on the
    desktop build at all, whatever the code does — the installer raises before the
    plugin ever loads. That is precisely the state this rewrite was undoing.

    Optional host-scoped deps are fine and yfinance/ccxt stay that way on purpose:
    the host routes those through ``optional_pip``, which warns and installs anyway
    (``graph/plugins/installer.py`` — the refusal only ever sees ``requires_pip``).
    They are imported in-process, so ``host`` is the honest scope; declaring them
    ``runtime`` would pass the gate and then fail at the first live fetch.
    """
    manifest = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    hard_host = [
        entry["pkg"]
        for entry in (manifest.get("requires_pip") or [])
        if isinstance(entry, dict) and entry.get("scope") == "host" and not entry.get("optional")
    ]
    assert not hard_host, (
        f"{hard_host} are hard deps at scope: host, which a frozen desktop app cannot satisfy — "
        "the installer refuses the whole plugin"
    )


@pytest.mark.parametrize("pkg", BANNED)
def test_runtime_requirements_do_not_list_the_dropped_stack(pkg):
    """``requirements.txt`` mirrors the manifest; drift between them is how an
    operator ends up installing a dependency the plugin no longer uses.

    Reads the requirement LINES, not the file text — the header explains why these
    two are gone, and a substring search over the whole file would trip on its own
    explanation.
    """
    lines = [
        ln.strip()
        for ln in (ROOT / "requirements.txt").read_text().splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    named = [ln for ln in lines if ln.lower().split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip() == pkg]
    assert not named, f"{pkg} is still a declared runtime dependency: {named}"
