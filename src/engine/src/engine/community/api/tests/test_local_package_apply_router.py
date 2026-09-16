from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.skills.router import router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.skills.models import (
    LocalSkillPackageAction,
    LocalSkillPackageApplyResult,
)
from engine.community.manager import EngineManager


class _Engine(BaseEngine):
    name = "package-engine"
    version = "1"
    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SKILLS_LOCAL_PACKAGE_APPLY}
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, skills) -> None:
        super().__init__({})
        self._session = MagicMock()
        self._chat = MagicMock()
        self._skills = skills


def test_apply_local_package_dispatches_one_complete_zip() -> None:
    skills = SimpleNamespace(
        apply_local_package=AsyncMock(
            return_value=LocalSkillPackageApplyResult(
                skill_name="weather",
                action=LocalSkillPackageAction.CREATED,
                content_digest="sha256:" + "a" * 64,
                target_path="/runtime/skills-local/weather",
            )
        )
    )
    registry = EngineRegistry()
    manager = EngineManager(_Engine.name, registry=registry)
    manager._active_engine = _Engine(skills)
    EngineManager._instance = manager
    app = FastAPI()
    app.include_router(router)

    try:
        response = TestClient(app).post(
            "/api/skills/local/apply",
            data={"skill_name": "weather", "layout": "LEGACY"},
            files={"file": ("weather.zip", b"complete-zip", "application/zip")},
        )
    finally:
        EngineManager.reset_instance()

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "skill_name": "weather",
            "action": "created",
            "content_digest": "sha256:" + "a" * 64,
            "target_path": "/runtime/skills-local/weather",
        },
        "message": "Local Skill package applied",
        "warning": None,
        "total": None,
        "error": None,
    }
    request = skills.apply_local_package.await_args.args[0]
    assert request.skill_name == "weather"
    assert request.layout.value == "LEGACY"
    assert request.package == b"complete-zip"
