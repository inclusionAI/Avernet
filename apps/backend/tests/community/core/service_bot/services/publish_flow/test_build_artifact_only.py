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
    bot_service.get_bot_by_address.return_value = {
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
    assert result.employee_snapshot is None  # 未启用数字员工
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


@pytest.mark.asyncio
async def test_build_artifact_only_captures_employee_snapshot_before_produce() -> None:
    """数字员工快照在 produce_artifact 之前捕获，确保时序正确。"""
    producer = _SimpleProducer()

    ext_state = Mock()
    ext_state.owner_id.return_value = "u1"
    ext_state.get_latest_ext_snapshot.return_value = ({}, {})

    bot_service = Mock()
    bot_service.get_bot_by_address.return_value = {
        "bot_id": "b1",
        "owner_id": "u1",
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

    # 模拟数字员工 provider，记录调用顺序
    call_order = []
    snapshot_obj = object()
    employee_service = Mock()
    employee_service.capture = Mock(return_value=snapshot_obj)
    employee_provider = Mock(return_value=employee_service)

    # 给 produce_artifact 添加副作用来记录调用顺序
    original_produce = producer.produce_artifact
    def _tracked_produce(request):
        call_order.append("produce")
        return original_produce(request)
    producer.produce_artifact = _tracked_produce

    original_capture = employee_service.capture
    def _tracked_capture(bot):
        call_order.append("capture")
        return original_capture(bot)
    employee_service.capture = _tracked_capture

    runner = BuildStageRunner(
        ext_state=ext_state,
        bot_service=bot_service,
        baas_service=baas_service,
        producer_router=producer_router,
        provider_behaviors=behaviors,
        runtime_projector=projector,
        runtime_layout_probe=probe,
        employee_publication_provider=employee_provider,
    )

    result = await runner.build_artifact_only(_record())

    # 快照在 produce 之前捕获
    assert call_order == ["capture", "produce"]
    assert result.employee_snapshot is snapshot_obj
@pytest.mark.asyncio
async def test_pending_draft_probe_retries_before_build(monkeypatch) -> None:
    from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError

    producer = _SimpleProducer()
    runner, ext = _runner(producer)
    bot = {"id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
           "active_engine": "claude_code", "env": "dev", "device_binding": {
               "device_id": "bot-uuid", "device_provider": "baas", "status": "PENDING",
               "device_props": {},
           }}
    runner._bot_service.get_bot_by_address.return_value = bot
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.side_effect = [
        BaasNoActiveDevicesError(status_code=503), {"exit_code": 0},
    ]
    monkeypatch.setattr("agentclaw.community.core.service_bot.services.publish_flow.build_stage.asyncio.sleep", AsyncMock())
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.BUILT
    assert runner._baas_service.exec_command_on_bot.call_count == 2
    runner._runtime_projector.project.assert_awaited_once()
    ext.commit_built_artifact.assert_called_once()


@pytest.mark.asyncio
async def test_pending_draft_probe_rejects_changed_target(monkeypatch) -> None:
    producer = _SimpleProducer()
    runner, ext = _runner(producer)
    bot = {"id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
           "active_engine": "claude_code", "env": "dev", "device_binding": {
               "device_id": "bot-uuid", "device_provider": "baas", "status": "PENDING",
               "device_props": {},
           }}
    runner._bot_service.get_bot_by_address.side_effect = [bot, {**bot, "binding_id": 13}]
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.return_value = {"exit_code": 0}
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    runner._runtime_projector.project.assert_not_awaited()
    ext.commit_built_artifact.assert_not_called()
    ext.update_status.assert_called_once()

@pytest.mark.asyncio
async def test_pending_draft_probe_exhaustion_fails_without_build(monkeypatch) -> None:
    from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError

    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "PENDING",
            "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.side_effect = BaasNoActiveDevicesError(status_code=503)
    monkeypatch.setattr("agentclaw.community.core.service_bot.services.publish_flow.build_stage.asyncio.sleep", AsyncMock())
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    assert runner._baas_service.exec_command_on_bot.call_count == 22
    runner._runtime_projector.project.assert_not_awaited()
    ext.commit_built_artifact.assert_not_called()
    ext.update_status.assert_called_once()

@pytest.mark.asyncio
async def test_pending_draft_probe_stops_when_publish_changed() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "PENDING", "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.DRAFT.value)
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    runner._baas_service.exec_command_on_bot.assert_not_called()
    runner._runtime_projector.project.assert_not_awaited()
    ext.commit_built_artifact.assert_not_called()
    ext.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_active_binding_still_probes_for_device_availability() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.return_value = {"exit_code": 0}
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.BUILT
    runner._baas_service.exec_command_on_bot.assert_called_once()

@pytest.mark.asyncio
async def test_coding_draft_with_unreadable_binding_fails_closed() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code",
    }
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    runner._baas_service.exec_command_on_bot.assert_not_called()
    runner._runtime_projector.project.assert_not_awaited()
    ext.commit_built_artifact.assert_not_called()


def test_readiness_timeout_does_not_wait_for_blocked_worker() -> None:
    import asyncio
    import threading
    import time

    runner, ext = _runner(_SimpleProducer())
    release = threading.Event()
    bot = {"id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
           "active_engine": "claude_code", "device_binding": {
               "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
           }}
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.side_effect = lambda **kwargs: release.wait(2)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(asyncio.wait_for(runner._wait_for_draft_device(_record(), bot), timeout=0.05))
        assert time.monotonic() - started < 1
    finally:
        release.set()


@pytest.mark.asyncio
async def test_coding_draft_without_binding_fails_before_projection() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": None,
        "active_engine": "claude_code",
    }
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    runner._runtime_projector.project.assert_not_awaited()
    ext.commit_built_artifact.assert_not_called()

@pytest.mark.asyncio
async def test_concurrent_built_status_does_not_report_build_success() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.BUILT.value)
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    ext.update_status.assert_not_called()
    ext.commit_built_artifact.assert_not_called()

@pytest.mark.asyncio
async def test_probe_failure_racing_state_transition_does_not_rewrite_publish() -> None:
    from agentclaw.community.core.service_bot.services.bot_publish_service import PublishNotFoundError

    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
        },
    }
    ext.get_record.side_effect = [
        _record(status=PublishStatus.BUILDING.value),
        _record(status=PublishStatus.BUILT.value),
    ]
    runner._baas_service.exec_command_on_bot.side_effect = RuntimeError("probe failure")
    ext.update_status.side_effect = PublishNotFoundError("CAS lost")
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    assert "stopped" in result.message
    ext.commit_built_artifact.assert_not_called()

@pytest.mark.asyncio
async def test_readiness_capacity_waits_without_blocking_other_providers() -> None:
    import asyncio
    from agentclaw.community.core.service_bot.services.publish_flow.build_stage import _READINESS_SLOTS

    for _ in range(8):
        assert _READINESS_SLOTS.acquire(blocking=False)
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.return_value = {"exit_code": 0}
    task = asyncio.create_task(runner.build(_record(status=PublishStatus.BUILDING.value), "operator"))
    try:
        await asyncio.sleep(0.1)
        assert not task.done()
        other, _ = _runner(_SimpleProducer())
        result = await other.build(_record(status=PublishStatus.BUILDING.value), "operator")
        assert result.status == PublishStatus.BUILT
    finally:
        for _ in range(8):
            _READINESS_SLOTS.release()
    assert (await asyncio.wait_for(task, 1)).status == PublishStatus.BUILT

@pytest.mark.asyncio
async def test_readiness_slots_recover_after_real_blocked_reads() -> None:
    import asyncio
    import threading
    from agentclaw.community.core.service_bot.services.publish_flow.build_stage import _readiness_read

    release = threading.Event()
    entered = threading.Barrier(9)

    def blocked():
        entered.wait(timeout=2)
        release.wait(timeout=3)
        return "done"

    tasks = [asyncio.create_task(_readiness_read(blocked)) for _ in range(8)]
    try:
        await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_readiness_read(lambda: "ninth"), timeout=0.1)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert await asyncio.wait_for(_readiness_read(lambda: "recovered"), 1) == "recovered"

@pytest.mark.asyncio
async def test_readiness_wait_covers_fifteen_intervals(monkeypatch) -> None:
    from agentclaw.community.core.service_bot.baas_service_errors import BaasNoActiveDevicesError

    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "PENDING", "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.side_effect = [
        *[BaasNoActiveDevicesError(status_code=503) for _ in range(15)], {"exit_code": 0},
    ]
    sleep = AsyncMock()
    monkeypatch.setattr("agentclaw.community.core.service_bot.services.publish_flow.build_stage.asyncio.sleep", sleep)
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.BUILT
    assert runner._baas_service.exec_command_on_bot.call_count == 16
    assert sleep.await_count == 15

@pytest.mark.asyncio
async def test_nonzero_readiness_probe_fails_before_projection() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._bot_service.get_bot_by_address.return_value = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
        },
    }
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.return_value = {"exit_code": 1}
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    assert "probe failed" in result.message
    runner._runtime_projector.project.assert_not_awaited()
    ext.commit_built_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_readiness_timeout_maps_to_failed_build() -> None:
    runner, ext = _runner(_SimpleProducer())
    runner._wait_for_draft_device = AsyncMock(side_effect=TimeoutError)
    result = await runner.build(_record(status=PublishStatus.BUILDING.value), "operator")
    assert result.status == PublishStatus.FAILED
    assert "readiness timed out" in result.message
    ext.update_status.assert_called_once()
    ext.commit_built_artifact.assert_not_called()

@pytest.mark.asyncio
async def test_readiness_reads_inherit_external_tenant() -> None:
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant

    runner, ext = _runner(_SimpleProducer())
    bot = {
        "id": 11, "bot_id": "b1", "owner_id": "u1", "binding_id": 12,
        "active_engine": "claude_code", "device_binding": {
            "device_id": "bot-uuid", "device_provider": "baas", "status": "ACTIVE", "device_props": {},
        },
    }
    seen = []

    def lookup(**kwargs):
        seen.append(get_current_avernet_tenant())
        return bot

    runner._bot_service.get_bot_by_address.side_effect = lookup
    ext.get_record.return_value = _record(status=PublishStatus.BUILDING.value)
    runner._baas_service.exec_command_on_bot.return_value = {"exit_code": 0}
    with avernet_tenant_scope("external-tenant"):
        assert await runner._wait_for_draft_device(_record(status=PublishStatus.BUILDING.value), bot) == bot
    assert seen == ["external-tenant"]
