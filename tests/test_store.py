"""The plugin's own directory: resolution, migration, and the fail-safe kill-switch.

These test the function that RESOLVES a path, not just that the module imports —
a lazy host import inside a function raises only when called, and v0.1.0's caller
caught broadly, which is exactly how its state quietly went to the wrong place.
"""

from __future__ import annotations

import json

from conftest import load


def test_env_override_wins(tmp_path, monkeypatch):
    st = load("store")
    target = tmp_path / "explicit"
    monkeypatch.setenv("PROTOTRADER_FINANCE_HOME", str(target))
    st.reset_cache()
    assert st.home() == target
    assert target.is_dir()  # created, not just named


def test_home_is_memoized(isolated_home):
    st = load("store")
    assert st.home() is st.home() or st.home() == st.home()
    assert st.home() == isolated_home


def test_path_creates_parents(isolated_home):
    st = load("store")
    p = st.path("cache", "deep", "file.csv.gz")
    assert p.parent.is_dir()
    assert p.parent == isolated_home / "cache" / "deep"


def test_named_files_live_in_home(isolated_home):
    st = load("store")
    assert st.state_path() == isolated_home / "broker_paper.json"
    assert st.audit_path() == isolated_home / "broker_audit.jsonl"
    assert st.cache_dir() == isolated_home / "cache"


def test_no_private_host_symbols():
    """v0.1.0 imported `graph.config_io._live_config_dir`, which the host has since
    deleted. Private names get no deprecation cycle — pin that we use none."""
    for mod in ("store", "broker.engine", "marketdata"):
        text = open(load(mod).__file__).read()
        # Import STATEMENTS only — store.__doc__ names the old symbol on purpose,
        # to explain why it's gone. A grep-the-whole-file assert would forbid that.
        imports = [ln.strip() for ln in text.splitlines()
                   if ln.strip().startswith(("import ", "from ")) or " import " in ln]
        offenders = [ln for ln in imports if "config_io" in ln and "import _" in ln]
        assert not offenders, f"{mod} imports a private host symbol: {offenders}"


# ── migration off v0.1.0's layout ────────────────────────────────────────────

def test_migrates_legacy_broker_files(tmp_path, monkeypatch):
    st = load("store")
    cfg = tmp_path / "hostcfg"
    cfg.mkdir()
    (cfg / "broker_paper.json").write_text(json.dumps({"cash": 1234.0}))
    (cfg / "broker_audit.jsonl").write_text('{"event":"fill"}\n')
    monkeypatch.setenv("PROTOAGENT_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("PROTOTRADER_FINANCE_HOME", str(tmp_path / "home"))
    st.reset_cache()

    home = st.home()
    assert json.loads((home / "broker_paper.json").read_text())["cash"] == 1234.0
    assert (home / "broker_audit.jsonl").exists()
    assert not (cfg / "broker_paper.json").exists()  # moved, not copied


def test_migration_never_clobbers_live_state(tmp_path, monkeypatch):
    """Live state wins over a stale leftover — the migration must be a no-op once run."""
    st = load("store")
    cfg, home = tmp_path / "hostcfg", tmp_path / "home"
    cfg.mkdir()
    home.mkdir(exist_ok=True)
    (cfg / "broker_paper.json").write_text(json.dumps({"cash": 1.0}))     # stale
    (home / "broker_paper.json").write_text(json.dumps({"cash": 999.0}))  # live
    monkeypatch.setenv("PROTOAGENT_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("PROTOTRADER_FINANCE_HOME", str(home))
    st.reset_cache()

    assert json.loads((st.home() / "broker_paper.json").read_text())["cash"] == 999.0


# ── mandate + kill-switch ────────────────────────────────────────────────────

def test_mandate_honours_configured_path(tmp_path, isolated_home):
    st = load("store")
    custom = tmp_path / "elsewhere" / "mandate.yaml"
    assert st.mandate_path({"broker_mandate_path": str(custom)}) == custom
    assert st.mandate_path({"broker_mandate_path": "  "}) == isolated_home / "broker_mandate.yaml"
    assert st.mandate_path(None) == isolated_home / "broker_mandate.yaml"


def test_killswitch_reads_both_locations(tmp_path, monkeypatch, isolated_home):
    st = load("store")
    cfg = tmp_path / "hostcfg"
    cfg.mkdir()
    monkeypatch.setenv("PROTOAGENT_CONFIG_DIR", str(cfg))

    assert st.killswitch_engaged() is None
    (cfg / "TRADING_HALT").touch()
    assert st.killswitch_engaged() == cfg / "TRADING_HALT"
    (cfg / "TRADING_HALT").unlink()
    (isolated_home / "TRADING_HALT").touch()
    assert st.killswitch_engaged() == isolated_home / "TRADING_HALT"
