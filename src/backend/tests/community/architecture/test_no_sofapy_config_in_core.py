"""Architecture guard: no sofapy config import in core / api / utils (B2).

After B2, configuration is read through the ``core/config`` ConfigProvider
registry (``sofa.sofa_config`` → ``load_config()``), never by importing the
company-internal ``sofapy_base.app.config`` directly. The single sanctioned
sofapy config import is the corporate provider ``plugins/prod/config.py`` (it is
the corporate implementation and is expected to use the internal package).

This guard fails if any file under ``core/``, ``adapters/http/`` (the public
API layer) or ``utils/`` (re)introduces a ``sofapy_base.app.config`` /
``get_config`` import — so the config fake can stay deleted.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_AGENTCLAW_ROOT = _THIS_FILE.parents[3] / "src" / "agentclaw"

# Directories that must stay free of direct sofapy config imports.
_SCAN_DIRS = ("core", "adapters/http", "utils")

# The only file allowed to import sofapy config — the corporate provider. It
# lives under ``plugins/prod`` (outside the scan dirs), so this is documentation
# of intent rather than a reachable exception.
_ALLOWED = frozenset({"corp/plugins/prod/config.py"})


def _imports_sofapy_config(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # ``from sofapy_base.app.config import get_config`` (and submodules).
            if mod == "sofapy_base.app.config" or mod.startswith(
                "sofapy_base.app.config."
            ):
                return True
            # ``from sofapy_base.app import config`` — config imported by name.
            if mod == "sofapy_base.app" and any(
                alias.name == "config" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            # ``import sofapy_base.app.config`` / ``... as cfg``.
            for alias in node.names:
                if alias.name == "sofapy_base.app.config" or alias.name.startswith(
                    "sofapy_base.app.config."
                ):
                    return True
    return False


@pytest.mark.unit
def test_no_sofapy_config_import_in_core() -> None:
    offenders: list[str] = []
    for sub in _SCAN_DIRS:
        root = _AGENTCLAW_ROOT / sub
        if not root.exists():
            continue
        for file in root.rglob("*.py"):
            rel = file.relative_to(_AGENTCLAW_ROOT).as_posix()
            if rel in _ALLOWED:
                continue
            try:
                tree = ast.parse(file.read_text(), filename=str(file))
            except SyntaxError:
                continue
            if _imports_sofapy_config(tree):
                offenders.append(rel)

    assert not offenders, (
        "Direct sofapy config import found outside the corporate provider "
        "(plugins/prod/config.py). Read configuration via "
        "`agentclaw.community.core.config.sofa.sofa_config` instead:\n  "
        + "\n  ".join(sorted(offenders))
    )
