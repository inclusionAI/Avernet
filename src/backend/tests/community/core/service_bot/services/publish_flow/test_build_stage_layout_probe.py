"""BuildStage ownership of fresh filesystem layout observations."""

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
    ServiceArtifactBuildError,
    ServiceArtifactBuildErrorCode,
)
from agentclaw.community.core.service_bot.services.deploy.producer import (
    DeployArtifact,
    DeployArtifactProducer,
)
from agentclaw.community.core.service_bot.services.publish_flow.build_stage import (
    BuildStageRunner,
)
from agentclaw.community.core.skill_center.runtime_layout_probe_service_protocol import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)


class _Producer(DeployArtifactProducer):
    def __init__(self, *, filesystem: bool) -> None:
        self.requires_runtime_layout_observation = filesystem
        self.requests: list[ArtifactBuildRequest] = []

    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        self.requests.append(request)
        return DeployArtifact(success=True, ext={"migration_path": "/snapshot/1"})


class _FailingProducer(DeployArtifactProducer):
    def produce_artifact(self, request: ArtifactBuildRequest) -> DeployArtifact:
        raise ServiceArtifactBuildError(
            ServiceArtifactBuildErrorCode.LAYOUT_EVIDENCE_UNAVAILABLE,
            "service build runtime layout evidence is unavailable",
        )


def _record() -> BotPublishRecord:
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
        status=PublishStatus.BUILDING.value,
        ext={},
        gmt_create=now,
        gmt_modified=now,
    )


def _probe_result() -> RuntimeLayoutProbeResult:
    return RuntimeLayoutProbeResult(
        status=RuntimeLayoutProbeStatus.READY,
        engine="openclaw",
        layout_contract_version="skills-pool-p3-v1",
        preparation_id="preparation-1",
        evidence={
            "resolved_layout": {
                "engine": "openclaw",
                "layout_contract_version": "skills-pool-p3-v1",
                "active_root": "/home/admin/.openclaw/workspace/skills",
                "local_root": (
                    "/home/admin/.openclaw/workspace/skills-pool/skills-local"
                ),
                "repo_root": (
                    "/home/admin/.openclaw/workspace/skills-pool/skills-repo"
                ),
                "pool_center": (
                    "/home/admin/.openclaw/workspace/skills-pool/skill-center"
                ),
            },
            "supported_mapping_contract_versions": [
                "skills-pool-mapping-v2",
                "skills-pool-mapping-v3",
            ],
            "center_mount": {
                "status": "READY",
                "reason": None,
                "restart_required": False,
            },
        },
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
    probe.probe_bot = AsyncMock(return_value=_probe_result())
    runner = BuildStageRunner(
        ext_state=ext_state,
        bot_service=bot_service,
        baas_service=baas_service,
        producer_router=producer_router,
        provider_behaviors=behaviors,
        runtime_projector=projector,
        runtime_layout_probe=probe,
    )
    return runner, ext_state, projector, probe


@pytest.mark.asyncio
async def test_filesystem_producer_receives_one_fresh_observation() -> None:
    producer = _Producer(filesystem=True)
    runner, ext_state, projector, probe = _runner(producer)

    result = await runner.build(_record(), "operator")

    assert result.status == PublishStatus.BUILT
    projector.project.assert_awaited_once_with(
        bot_id="b1", owner_id="u1", scope=ProjectionScope.everything()
    )
    probe.probe_bot.assert_awaited_once_with(
        bot_id="b1", user_id="u1", engine="openclaw"
    )
    observation = producer.requests[0].layout_observation
    assert observation is not None
    assert observation.status is RuntimeLayoutProbeStatus.READY
    ext_state.commit_built_artifact.assert_called_once()


@pytest.mark.asyncio
async def test_config_artifact_producer_does_not_probe_filesystem() -> None:
    producer = _Producer(filesystem=False)
    runner, _ext_state, _projector, probe = _runner(producer)

    result = await runner.build(_record(), "operator")

    assert result.status == PublishStatus.BUILT
    probe.probe_bot.assert_not_awaited()
    assert producer.requests[0].layout_observation is None


@pytest.mark.asyncio
async def test_classified_failure_is_persisted_for_internal_diagnosis() -> None:
    runner, ext_state, _projector, _probe = _runner(_FailingProducer())

    result = await runner.build(_record(), "operator")

    assert result.status == PublishStatus.FAILED
    failed_ext = ext_state.update_status.call_args.kwargs["ext"]
    assert failed_ext["error_code"] == (
        "SERVICE_ARTIFACT_LAYOUT_EVIDENCE_UNAVAILABLE"
    )
    assert failed_ext["source_status"] == PublishStatus.BUILDING.value


@pytest.mark.asyncio
async def test_successful_retry_clears_stale_build_error_fields() -> None:
    producer = _Producer(filesystem=False)
    runner, ext_state, _projector, _probe = _runner(
        producer,
        ext={
            "error_code": "SERVICE_ARTIFACT_SNAPSHOT_INVALID",
            "error_message": "old failure",
            "source_status": PublishStatus.BUILDING.value,
            "keep": "value",
        },
    )

    result = await runner.build(_record(), "operator")

    assert result.status == PublishStatus.BUILT
    built_ext = ext_state.commit_built_artifact.call_args.kwargs["ext"]
    assert built_ext == {"keep": "value", "migration_path": "/snapshot/1"}
