"""Conformance contract for every community ``SkillsService`` package applier."""

from __future__ import annotations

import hashlib

import pytest

from engine.community.core.adapters.claude_code.skills import (
    ClaudeCodeSkillsAdapter,
)
from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.skills.models import (
    LocalSkillPackageAction,
    LocalSkillPackageApplyRequest,
    LocalSkillPackageApplyResult,
    LocalSkillPackageLayout,
)


class _Application:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.requests: list[LocalSkillPackageApplyRequest] = []

    async def apply(
        self, request: LocalSkillPackageApplyRequest
    ) -> LocalSkillPackageApplyResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return LocalSkillPackageApplyResult(
            skill_name=request.skill_name,
            action=LocalSkillPackageAction.CREATED,
            content_digest="sha256:" + hashlib.sha256(request.package).hexdigest(),
            target_path=f"/runtime/skills-local/{request.skill_name}",
        )


@pytest.fixture(params=[OpenClawSkillsAdapter, ClaudeCodeSkillsAdapter])
def adapter(request):
    return request.param(object())


@pytest.mark.asyncio
async def test_package_apply_forwards_one_complete_package(adapter) -> None:
    application = _Application()
    adapter._local_packages = application
    package = b"canonical-zip"
    request = LocalSkillPackageApplyRequest(
        skill_name="weather",
        layout=LocalSkillPackageLayout.POOL,
        package=package,
    )

    result = await adapter.apply_local_package(request)

    assert application.requests == [request]
    assert result.skill_name == "weather"
    assert result.content_digest == "sha256:" + hashlib.sha256(package).hexdigest()


@pytest.mark.asyncio
async def test_package_apply_propagates_application_failure(adapter) -> None:
    failure = RuntimeError("publish failed")
    application = _Application(failure=failure)
    adapter._local_packages = application
    request = LocalSkillPackageApplyRequest(
        skill_name="weather",
        layout=LocalSkillPackageLayout.LEGACY,
        package=b"canonical-zip",
    )

    with pytest.raises(RuntimeError, match="publish failed"):
        await adapter.apply_local_package(request)

    assert application.requests == [request]
