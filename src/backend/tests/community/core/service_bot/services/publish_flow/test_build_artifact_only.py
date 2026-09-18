"""BuildArtifactOnlyResult 和 build_artifact_only 测试。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import (
    ArtifactBuildRequest,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)
from agentclaw.community.core.service_bot.services.publish_flow.build_stage import (
    BuildArtifactOnlyResult,
    BuildStageRunner,
)


class _SimpleProducer(DeployArtifactProducer):
    def __init__(self, *, artifact_ext: dict | None = None) -> None:
        self.requires_runtime_layout_observation = False
        self.artifact_ext = artifact_ext or {"migration_path": "/snapshot/1"}

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        return DeployArtifact(success=True, ext=self.artifact_ext)


class _FailingProducer(DeployArtifactProducer):
    def __init__(self) -> None:
        self.requires_runtime_layout_observation = False

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        return DeployArtifact(success=False, message="build failed")


def _record(*, status: str | None = None) -> BotPublishRecord:
    now = datetime.now()
    return BotPublishRecord(
        id=1,
        source_bot_pk=11,
        source_bot_id="b1",
        publish_bot_id="published-b1",
        name="demo",
        owner_id="u1",
        permission_owner="u1",
        version=1,
        env="dev",
        status=status or PublishStatus.DRAFT.value,
        ext={},
        gmt_create=now,
        gmt_modified=now,
    )


def _runner(producer, *, ext=None):
    ext_state = Mock()
    ext_state.owner_id.return_value = "u1"
    ext_state.get_latest_ext_snapshot.return_value = (
        dict(ext or {}),
        dict(ext or {}),
    )
    bot_service = Mock()
    bot_service.get_bot.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
        "entity_id": "u1",
        "active_engine": "openclaw",
        "env": "dev",
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
async def test_build_artifact_only_returns_artifact() -> None:
    """构建步骤独立执行，不推进状态。"""
    producer = _SimpleProducer()
    runner, ext_state = _runner(producer)

    result = await runner.build_artifact_only(_record())

    assert isinstance(result, BuildArtifactOnlyResult)
    assert result.migration_path == "/snapshot/1"
    assert result.config_artifact is None
    assert result.docker_image is None
    assert result.center_skill_uuids == ()
    assert result.artifact_ext == {"migration_path": "/snapshot/1"}
    # 不调用 commit_built_artifact——DRAFT 状态不变
    ext_state.commit_built_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_build_artifact_only_extracts_config_artifact() -> None:
    """config_artifact 类型的 producer 正确提取字段。

    exact_center_refs_from_artifact_ext 在 validate_full_artifact=False
    时对不含 "skills" 键的 config_artifact dict 快速退出，不进入
    BotConfigArtifact.from_dict 解析。此处用此结构避免解析异常。
    """
    producer = _SimpleProducer(
        artifact_ext={"config_artifact": {"schema_version": 1, "engine_type": "openclaw"}}
    )
    runner, _ = _runner(producer)

    result = await runner.build_artifact_only(_record())

    assert result.migration_path == ""
    assert isinstance(result.config_artifact, dict)
    assert result.config_artifact["schema_version"] == 1


@pytest.mark.asyncio
async def test_build_artifact_only_raises_on_failure() -> None:
    """构建失败时直接抛异常，不做状态推进。"""
    producer = _FailingProducer()
    runner, ext_state = _runner(producer)

    with pytest.raises(Exception):
        await runner.build_artifact_only(_record())

    ext_state.commit_built_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_build_still_commits_built_artifact() -> None:
    """原有 build() 行为不变——调用 build_artifact_only 后做状态提交。"""
    producer = _SimpleProducer()
    runner, ext_state = _runner(producer)

    result = await runner.build(_record(), "operator")

    assert result.status == PublishStatus.BUILT
    ext_state.commit_built_artifact.assert_called_once()


@pytest.mark.asyncio
async def test_build_failure_still_sets_failed_status() -> None:
    """build() 失败路径仍然设置 FAILED 状态。"""
    producer = _FailingProducer()
    runner, ext_state = _runner(producer)

    result = await runner.build(_record(), "operator")

    assert result.status == PublishStatus.FAILED
    ext_state.update_status.assert_called_once()