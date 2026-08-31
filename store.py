"""Where this plugin keeps its own files — the one place that answers "which dir?".

Every durable byte the plugin owns (paper-broker state, the audit ledger, the
market-data cache) resolves through here, so there is exactly one policy to
review and exactly one thing to change when the host moves.

The resolution order is deliberate:

1. ``PROTOTRADER_FINANCE_HOME`` — an explicit operator/test override. First so a
   test never touches a real instance and an operator can always win.
2. :func:`graph.sdk.plugin_store` — the supported seam. Instance-scoped (ADR 0004
   / ADR 0065), so the dev sandbox and every fleet member get their own copy for
   free. This is the path that runs in production.
3. ``infra.paths.instance_paths().store(...)`` — the same directory on a host
   released before ``sdk.plugin_store`` existed.
4. ``$PROTOAGENT_CONFIG_DIR/prototrader-finance`` — no host at all (the host-free
   test suite, `pytest` in a bare venv).

Each step is guarded and falls through, so an older host degrades to the same
directory instead of raising at import.

**Why this module exists.** v0.1.0 called ``graph.config_io._live_config_dir`` —
a *private* host symbol — and wrote plugin state into the host's own config
directory. Private symbols get renamed without notice, and the failure mode is
the bad one: the import sat inside a ``try/except Exception`` that fell back to a
relative ``config`` path, so a rename would not crash, it would silently start
writing the ledger somewhere else. Nothing would look broken until someone went
looking for their fills.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("protoagent.plugins.prototrader-finance")

PLUGIN_ID = "prototrader-finance"

# Runtime state that v0.1.0 wrote into the host config dir. Migrated on first use
# (see `_migrate_legacy`) so upgrading doesn't strand an operator's paper history.
_LEGACY_FILES = ("broker_paper.json", "broker_audit.jsonl")

_home: Path | None = None


def _from_sdk() -> Path | None:
    """The supported seam. Returns None on any host that can't serve it."""
    try:
        from graph import sdk

        return sdk.plugin_store(plugin_id=PLUGIN_ID)
    except Exception:
        return None


def _from_instance_paths() -> Path | None:
    """Same directory, on a host predating ``sdk.plugin_store``."""
    try:
        from infra.paths import instance_paths

        p = instance_paths().store(PLUGIN_ID)
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _host_config_dir() -> Path:
    """The host's *instance* config dir — public API only.

    ``instance_paths().config_dir`` is the supported accessor (ADR 0065). Used to
    find v0.1.0's files for migration and to honour a kill-switch dropped there;
    never to write new state.

    v0.1.0 reached for ``graph.config_io._live_config_dir`` instead. That private
    symbol no longer exists on the host, so the ``except`` branch has quietly been
    taking every call — resolving to a *cwd-relative* ``config`` directory. Public
    names get deprecation cycles; private ones just vanish.
    """
    try:
        from infra.paths import instance_paths

        return instance_paths().config_dir
    except Exception:
        return Path(os.environ.get("PROTOAGENT_CONFIG_DIR", "config")).expanduser()


def _migrate_legacy(home: Path) -> None:
    """Move v0.1.0's broker files out of the host config dir, once.

    Only moves a file whose destination does not exist, so a re-run is a no-op and
    live state always wins over a stale leftover. Failures are logged, never
    raised — a migration hiccup must not stop the plugin loading.
    """
    src_dir = _host_config_dir()
    for name in _LEGACY_FILES:
        src, dst = src_dir / name, home / name
        if not src.is_file() or dst.exists():
            continue
        try:
            shutil.move(str(src), str(dst))
            log.info("[%s] migrated %s → %s", PLUGIN_ID, src, dst)
        except Exception:
            log.exception("[%s] could not migrate %s", PLUGIN_ID, src)


def home() -> Path:
    """This plugin's own writable directory, created and instance-scoped."""
    global _home
    if _home is not None:
        return _home

    override = os.environ.get("PROTOTRADER_FINANCE_HOME")
    if override:
        p = Path(override).expanduser()
        p.mkdir(parents=True, exist_ok=True)
    else:
        p = _from_sdk() or _from_instance_paths()
        if p is None:
            p = _host_config_dir() / PLUGIN_ID
            p.mkdir(parents=True, exist_ok=True)

    # Migration runs for EVERY branch, the override included: an operator who
    # relocates the store still expects their paper history to follow them.
    _migrate_legacy(p)
    _home = p
    return _home


def reset_cache() -> None:
    """Forget the memoized home — for tests that repoint the env var."""
    global _home
    _home = None


def path(*parts: str) -> Path:
    """A path inside the plugin's home, with parent dirs created."""
    p = home().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── the named files ──────────────────────────────────────────────────────────

def state_path() -> Path:
    """Paper-broker positions + cash."""
    return path("broker_paper.json")


def audit_path() -> Path:
    """Append-only fill ledger (JSONL)."""
    return path("broker_audit.jsonl")


def cache_dir() -> Path:
    """Market-data cache — rebuildable, safe to delete."""
    return path("cache")


def mandate_path(config: dict | None = None) -> Path:
    """The broker mandate.

    Operator-authored input, not runtime state, so the manifest's
    ``broker_mandate_path`` setting wins when set — an operator may keep it beside
    their other config, or in a read-only mount. Unset, it lives in the plugin
    home next to the state it governs.
    """
    configured = ((config or {}).get("broker_mandate_path") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return home() / "broker_mandate.yaml"


def killswitch_path() -> Path:
    """The halt file. See :func:`killswitch_engaged` — both locations count."""
    return home() / "TRADING_HALT"


def killswitch_locations() -> tuple[Path, ...]:
    """Every path a ``TRADING_HALT`` file is honoured at.

    The host config dir stays honoured alongside the plugin home on purpose: an
    operator reaching for the kill-switch is having a bad day, and a halt file
    that lands in the wrong directory must fail *safe*, not silently.
    """
    return (killswitch_path(), _host_config_dir() / "TRADING_HALT")


def killswitch_engaged() -> Path | None:
    """The halt file that is engaged, or None."""
    return next((p for p in killswitch_locations() if p.exists()), None)
