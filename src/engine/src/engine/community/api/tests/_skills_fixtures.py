"""Shared test scaffolding for skills router tests.

Installs an EngineManager singleton with the OpenClaw skills ACL adapter (over a
real `OpenClawPluginImpl`) pointed at a tmp_path base dir, so the existing FS-level
assertions in ``test_skills_bindpath`` / ``test_skills_clean`` keep working after
the F2 ACL conversion. The base dir is set via the ``SKILLS_LINK_BASE_DIR`` env
the port resolves at call time.

Imports a concrete `OpenClawPluginImpl` (a plugins impl) — a deliberate test-only
exception to the api↛plugins rule, carved out in `.importlinter`. F6 replaces it
with a `plugins/local` mock skills impl and drops the ignore.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


class _SkillsTestEngine(BaseEngine):
    """Stand-in engine that wires only the SkillsService slot (ACL adapter)."""

    name = "openclaw-test"
    version = "1.0.0"
    _CAPABILITIES = EngineCapabilities(
        supported={
            Capability.SKILLS_SYNC_SYMLINKS,
            Capability.SKILLS_SYNC_BINDPATHS,
            Capability.SKILLS_CLEAN_SYMLINKS,
        },
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self) -> None:
        super().__init__(None)
        self._session = MagicMock()
        self._chat = MagicMock()
        self._skills = OpenClawSkillsAdapter(OpenClawPluginImpl())


def install_skills_manager(router: APIRouter, base_dir: Path):
    """Yield a TestClient backed by a manager whose SkillsService (ACL adapter)
    resolves `base_dir` as its symlink base via the SKILLS_LINK_BASE_DIR env."""
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(_SkillsTestEngine)
    m = EngineManager(_SkillsTestEngine.name, registry=registry)
    m._active_engine = _SkillsTestEngine()
    EngineManager._instance = m

    app = FastAPI()
    app.include_router(router)
    prev = os.environ.get("SKILLS_LINK_BASE_DIR")
    os.environ["SKILLS_LINK_BASE_DIR"] = str(base_dir)
    try:
        yield TestClient(app)
    finally:
        if prev is None:
            os.environ.pop("SKILLS_LINK_BASE_DIR", None)
        else:
            os.environ["SKILLS_LINK_BASE_DIR"] = prev
        EngineManager.reset_instance()
