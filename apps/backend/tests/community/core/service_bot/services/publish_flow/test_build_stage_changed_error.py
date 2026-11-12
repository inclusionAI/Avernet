"""BuildStageChangedError 处理测试。

验证构建期间并发状态变更时：
1. 日志输出 "Build stage superseded" 而非 "Build failed"
2. 不写入 FAILED 状态
3. 返回正确的 status（当前实际状态）
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.build_stage import (
    BuildStageRunner,
)
from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)


# Lazy import to avoid circular import during module loading
def _BuildStageChangedError():
    from agentclaw.community.core.service_bot.services.publish_flow.errors import (
        BuildStageChangedError,
    )
    return BuildStageChangedError


class _SimpleProducer(DeployArtifactProducer):
    def __init__(self, *, artifact_ext: dict | None = None) -> None:
        self.requires_runtime_layout_observation = False
        self.artifact_ext = artifact_ext or {"migration_path": "/snapshot/1"}

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        return DeployArtifact(success=True, ext=self.artifact_ext)


def _record(*, status: str | None = None, publish_id: int = 1) -> BotPublishRecord:
    now = datetime.now()
    return BotPublishRecord(
        id=publish_id,
        source_bot_pk=11,
        source_bot_id="b1",
        publish_bot_id="published-b1",
        name="demo",
        owner_id="u1",
        permission_owner="u1",
        version=1,
        env="dev",
        status=status or PublishStatus.BUILDING.value,
        ext={},
        gmt_create=now,
        gmt_modified=now,
    )


def _runner(producer, *, ext=None, get_publish_by_id_returns=None):
    ext_state = Mock()
    ext_state.owner_id.return_value = "u1"
    ext_state.get_latest_ext_snapshot.return_value = (
        dict(ext or {}),
        dict(ext or {}),
    )
    ext_state.commit_built_artifact = Mock()
    ext_state.update_status = Mock()
    ext_state.get_publish_by_id = Mock(return_value=get_publish_by_id_returns)

    bot_service = Mock()
    bot_service.get_bot_by_address.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "entity_id": "u1",
        "active_engine": "openclaw",
        "env": "dev",
        "source_bot_pk": 11,
    }
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "baas"
    producer_router = Mock()
    producer_router.resolve.return_value = producer
    behavior = Mock()
    behavior.stage_build_files = AsyncMock()
    behaviors = Mock()
    behaviors.resolve.return_value = behavior
    projector = Mock()
    projector.project = AsyncMock()
    probe = Mock()
    probe.probe_bot = AsyncMock()
    runner = BuildStageRunner(
        ext_state=ext_state,
        bot_service=bot_service,
        baas_service=baas_service,
        producer_router=producer_router,
        provider_behaviors=behaviors,
        runtime_projector=projector,
        runtime_layout_probe=probe,
    )
    return runner, ext_state


@pytest.mark.asyncio
async def test_fetch_and_assert_building_raises_when_not_building() -> None:
    """_fetch_and_assert_building 在状态不是 BUILDING 时抛出 BuildStageChangedError。"""
    BuildStageChangedError = _BuildStageChangedError()
    producer = _SimpleProducer()
    runner, ext_state = _runner(
        producer,
        get_publish_by_id_returns=_record(status=PublishStatus.DRAFT.value),
    )

    with pytest.raises(BuildStageChangedError) as exc_info:
        await runner._fetch_and_assert_building(1)

    assert "Publish record changed before Artifact commit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_and_assert_building_raises_when_record_not_found() -> None:
    """_fetch_and_assert_building 在找不到记录时抛出 BuildStageChangedError。"""
    BuildStageChangedError = _BuildStageChangedError()
    producer = _SimpleProducer()
    runner, ext_state = _runner(
        producer,
        get_publish_by_id_returns=None,
    )

    with pytest.raises(BuildStageChangedError) as exc_info:
        await runner._fetch_and_assert_building(1)

    assert "Publish record not found during build" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_and_assert_building_passes_when_building() -> None:
    """_fetch_and_assert_building 在状态为 BUILDING 时不抛出。"""
    producer = _SimpleProducer()
    runner, ext_state = _runner(
        producer,
        get_publish_by_id_returns=_record(status=PublishStatus.BUILDING.value),
    )

    # 不应抛出异常
    await runner._fetch_and_assert_building(1)


@pytest.mark.asyncio
async def test_build_superseded_when_status_changed_during_build() -> None:
    """构建期间状态被改为 DRAFT，build() 应返回 superseded 而非 FAILED。"""
    producer = _SimpleProducer()
    runner, ext_state = _runner(
        producer,
        get_publish_by_id_returns=_record(status=PublishStatus.DRAFT.value),
    )

    result = await runner.build(_record(), "operator")

    # 不应写入 FAILED 状态
    ext_state.update_status.assert_not_called()
    # 不应调用 commit_built_artifact
    ext_state.commit_built_artifact.assert_not_called()
    # 应返回当前状态（DRAFT）而非 FAILED
    assert result.status == PublishStatus.DRAFT
    assert "superseded" in result.message.lower()


@pytest.mark.asyncio
async def test_build_superseded_when_record_deleted_during_build() -> None:
    """构建期间记录被删除，build() 应返回 superseded 而非 FAILED。"""
    producer = _SimpleProducer()
    runner, ext_state = _runner(
        producer,
        get_publish_by_id_returns=None,
    )

    result = await runner.build(_record(), "operator")

    # 不应写入 FAILED 状态
    ext_state.update_status.assert_not_called()
    # 应返回 DRAFT（默认值）
    assert result.status == PublishStatus.DRAFT
    assert "superseded" in result.message.lower()


@pytest.mark.asyncio
async def test_build_still_fails_on_real_errors() -> None:
    """真正的构建错误（非并发状态变更）仍应写入 FAILED。"""
    from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
        ServiceArtifactBuildError,
        ServiceArtifactBuildErrorCode,
    )

    # Producer 抛出 ServiceArtifactBuildError
    class _ErrorProducer(DeployArtifactProducer):
        def __init__(self) -> None:
            self.requires_runtime_layout_observation = False

        def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
            raise ServiceArtifactBuildError(
                ServiceArtifactBuildErrorCode.SNAPSHOT_INVALID,
                "snapshot broken",
            )

    producer = _ErrorProducer()
    runner, ext_state = _runner(
        producer,
        get_publish_by_id_returns=_record(status=PublishStatus.BUILDING.value),
    )

    result = await runner.build(_record(), "operator")

    # 应写入 FAILED
    ext_state.update_status.assert_called_once()
    assert result.status == PublishStatus.FAILED
