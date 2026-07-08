"""Ratchet guard: the local-mode sofapy monkeypatch only shrinks (B2).

``agentclaw.community.local.patch_sofapy_for_local`` fakes ``sofapy_base`` submodules so
local/community boots work without the internal package. B2 removed the
**config** fake; B5 removed the **tracer** fakes (tracing moved behind the
``TracerPlugin`` capability); B7 removed the **mcp** fakes (the sofapy
MCP-server entrypoint was dropped); B6 removed the **layotto_manager** fake
(core no longer reads layotto — DRM goes through the injected ``DRMReaderPlugin``).
The remaining fakes belong to the prod-boot bootstrap (runner/application +
logger). This guard pins the exact set of faked ``sofapy_base.*`` modules so:

- a regression that re-adds the ``sofapy_base.app.config`` fake fails here, and
- adding any new fake fails until it is listed with its owning SDD — proving the
  monkeypatch provably shrinks as each SDD lands (each deletes its entry).

Containers (``sofapy_base`` / ``sofapy_base.app``) are namespace parents, not
subsystem fakes, so they are excluded.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import agentclaw.community.local as local_pkg

_LOCAL_INIT = pathlib.Path(local_pkg.__file__).resolve()

# Namespace parents — not subsystem fakes.
_CONTAINERS = frozenset({"sofapy_base", "sofapy_base.app"})

# The remaining faked sofapy_base modules, each tagged with the SDD that will
# delete it. Update this dict (and only shrink it) as those SDDs land.
_EXPECTED_FAKES: dict[str, str] = {
    "sofapy_base.app.application": "prod boot — SOFAPyApplication wrapper",
    "sofapy_base.runner": "prod boot — sofapy runner entrypoint",
    "sofapy_base.logger": "logger bootstrap (out of scope)",
    "sofapy_base.logger.logger": "logger bootstrap (out of scope)",
}


def _faked_sofapy_modules() -> set[str]:
    """Collect the ``sofapy_base.*`` module names created as stubs.

    Scans ``local/__init__.py`` for ``_create_stub_module`` /
    ``_create_catch_all_module`` calls whose first positional arg is a string
    literal starting with ``sofapy_base`` (the not-installed stub tree — the
    authoritative list of what gets faked).
    """
    tree = _local_ast()
    creators = {"_create_stub_module", "_create_catch_all_module"}
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id in creators):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            name = first.value
            if name.startswith("sofapy_base"):
                found.add(name)
    return found - _CONTAINERS


def _local_ast() -> ast.AST:
    return ast.parse(_LOCAL_INIT.read_text(), filename=str(_LOCAL_INIT))


def _imports_sofapy_config_anywhere() -> bool:
    """True if ``local/__init__.py`` imports ``sofapy_base.app.config`` at all.

    The installed branch fakes by attribute-patching a *real* import
    (``import sofapy_base.app.config as config_module``), which the stub-creation
    scan above can't see. Catching the import itself closes that path: the config
    fake can only be re-added by first importing the module.
    """
    for node in ast.walk(_local_ast()):
        if isinstance(node, ast.Import):
            if any(
                a.name == "sofapy_base.app.config"
                or a.name.startswith("sofapy_base.app.config.")
                for a in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "sofapy_base.app.config" or mod.startswith(
                "sofapy_base.app.config."
            ):
                return True
            if mod == "sofapy_base.app" and any(
                a.name == "config" for a in node.names
            ):
                return True
    return False


@pytest.mark.unit
def test_config_fake_is_gone() -> None:
    faked = _faked_sofapy_modules()
    assert "sofapy_base.app.config" not in faked, (
        "The sofapy config fake was reintroduced in patch_sofapy_for_local — "
        "config must come from the ConfigProvider registry (B2)."
    )
    # Also covers the installed branch (attribute-patching a real import).
    assert not _imports_sofapy_config_anywhere(), (
        "local/__init__.py imports sofapy_base.app.config — the config fake must "
        "stay removed (config comes from the ConfigProvider registry, B2)."
    )


@pytest.mark.unit
def test_remaining_fakes_match_annotated_set() -> None:
    faked = _faked_sofapy_modules()
    expected = set(_EXPECTED_FAKES)

    added = faked - expected
    removed = expected - faked
    assert not added, (
        "New sofapy_base fake(s) not listed in _EXPECTED_FAKES — add each with "
        "its owning SDD so the monkeypatch shrink stays tracked:\n  "
        + "\n  ".join(sorted(added))
    )
    assert not removed, (
        "A fake listed in _EXPECTED_FAKES is gone (good — the monkeypatch "
        "shrank). Remove its stale entry from _EXPECTED_FAKES:\n  "
        + "\n  ".join(sorted(removed))
    )
