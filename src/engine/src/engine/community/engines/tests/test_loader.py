from __future__ import annotations

import importlib
import runpy
from pathlib import Path

import pytest


def test_corp_profile_loads_every_internal_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []
    loader_path = Path(__file__).parents[1] / "__init__.py"
    monkeypatch.setenv("ENGINE_PROFILE", "corp")
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda module_name: imported.append(module_name),
    )

    runpy.run_path(str(loader_path))

    assert imported == [
        "engine.community.engines.openclaw",
        "engine.corp.engines.claude_code",
        "engine.corp.engines.aicoding",
        "engine.corp.engines.hermes",
        "engine.corp.engines.deepseek_harness",
    ]
