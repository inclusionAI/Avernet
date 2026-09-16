from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.skills.router import router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.skills.exceptions import (
    LocalSkillPackageInvalidError,
    LocalSkillPackagePublishFailedError,
    LocalSkillPackagePublishInProgressError,
    LocalSkillPackagePublishLockUnavailableError,
    LocalSkillPackageRollbackFailedError,
    LocalSkillPackageTooLargeError,
)
from engine.community.core.skills.local_package import MAX_COMPRESSED_BYTES
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


def _post(skills, *, layout: str = "LEGACY", package: bytes = b"complete-zip"):
    registry = EngineRegistry()
    manager = EngineManager(_Engine.name, registry=registry)
    manager._active_engine = _Engine(skills)
    EngineManager._instance = manager
    app = FastAPI()
    app.include_router(router)
    try:
        return TestClient(app).post(
            "/api/skills/local/apply",
            data={"skill_name": "weather", "layout": layout},
            files={"file": ("weather.zip", package, "application/zip")},
        )
    finally:
        EngineManager.reset_instance()


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
    response = _post(skills)

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


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (LocalSkillPackageTooLargeError(), 413, "package_too_large"),
        (LocalSkillPackageInvalidError(), 400, "invalid_package"),
        (LocalSkillPackagePublishInProgressError(), 409, "publish_in_progress"),
        (
            LocalSkillPackagePublishLockUnavailableError(),
            503,
            "publish_lock_unavailable",
        ),
        (LocalSkillPackagePublishFailedError(), 500, "publish_failed"),
        (LocalSkillPackageRollbackFailedError(), 500, "rollback_failed"),
        (RuntimeError("unknown"), 500, "rollback_failed"),
    ],
)
def test_apply_local_package_maps_publisher_failures(error, status, code) -> None:
    skills = SimpleNamespace(apply_local_package=AsyncMock(side_effect=error))

    response = _post(skills)

    assert response.status_code == status
    assert response.json()["error"] == code


def test_apply_local_package_rejects_invalid_layout_before_dispatch() -> None:
    skills = SimpleNamespace(apply_local_package=AsyncMock())

    response = _post(skills, layout="OTHER")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_package"
    skills.apply_local_package.assert_not_awaited()


def test_apply_local_package_rejects_compressed_limit_before_dispatch() -> None:
    skills = SimpleNamespace(apply_local_package=AsyncMock())

    response = _post(skills, package=b"x" * (MAX_COMPRESSED_BYTES + 1))

    assert response.status_code == 413
    assert response.json()["error"] == "package_too_large"
    skills.apply_local_package.assert_not_awaited()
