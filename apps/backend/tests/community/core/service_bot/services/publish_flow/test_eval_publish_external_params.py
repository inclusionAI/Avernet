"""eval_publish 外部参数适配测试。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.eval_publish_mixin import (
    EvalPublishMixin,
)
from agentclaw.community.core.service_bot.types import PublishStage


_DEFAULT_EXT = object()


def _record(*, ext=_DEFAULT_EXT, status=None) -> BotPublishRecord:
    """Build a publish record; ``ext=None`` must remain explicit, not defaulted."""
    now = datetime.now()
    ext_value = (
        {"migration_path": "/arca/snapshot"} if ext is _DEFAULT_EXT else ext
    )
    return BotPublishRecord(
        id=42,
        source_bot_pk=1,
        source_bot_id="src-bot",
        publish_bot_id="src-bot.pub",
        name="test",
        owner_id="u1",
        permission_owner="u1",
        version=1,
        env="dev",
        status=status or PublishStatus.BUILT.value,
        ext=ext_value,
        gmt_create=now,
        gmt_modified=now,
    )


class _FakeEvalPublishService(EvalPublishMixin):
    """最小化 mixin 载体，模拟 PublishFlowService 的依赖。"""

    def __init__(self, *, publish_record=None, image_docker="registry/image:tag"):
        self._publish_service = Mock()
        self._publish_service.get_publish_by_id.return_value = (
            publish_record or _record()
        )
        self._bot_service = Mock()
        self._bot_service.get_bot_by_address.return_value = {
            "bot_id": "src-bot",
            "owner_id": "u1",
            "active_engine": "openclaw",
        }
        self._ext_state = Mock()
        self._ext_state.owner_id.return_value = "u1"
        self._ext_state.compose_live.return_value = ({}, None)
        self._operation_runner = Mock()
        self._operation_runner.open_operation.return_value = Mock(
            bot_uuid="eval-uuid",
            baas_publish_id=99,
            request_id="req-1",
        )
        self._operation_runner.acquire_workflow = AsyncMock(
            return_value=Mock(bot_uuid="eval-uuid", baas_publish_id=99)
        )
        self._operation_runner.complete_operation = Mock()
        self._build_service = Mock()
        self._baas_service = Mock()
        self._task_queue_service = Mock()
        # resolve_publish_image_pin / device_provider
        self._image_docker = image_docker
        self._image_pin = Mock(docker_image=image_docker)

    def _get_owner_id(self, record):
        return "u1"

    def resolve_publish_image_pin(self, record, *, device_provider):
        return self._image_pin

    def device_provider(self, bot):
        return "baas"


def _capture_release(svc):
    """Make the fake operation runner execute the BaaS release callback."""
    svc._build_service.release_async = AsyncMock()

    async def issue(op, callback):
        await callback()
        return op

    svc._operation_runner.acquire_workflow.side_effect = issue
    return svc._build_service.release_async


_TEARDOWN_PATCH = (
    "agentclaw.community.core.service_bot.services.publish_flow.tasks"
    ".enqueue_eval_teardown"
)
_EXTRA_ENVS_PATCH = (
    "agentclaw.community.core.service_bot.services.publish_flow."
    "eval_publish_mixin.service_publish_extra_envs"
)


@pytest.mark.asyncio
async def test_eval_publish_with_external_migration_path() -> None:
    """外部传入 migration_path 时优先使用。"""
    record = _record(ext={"migration_path": ""})  # ext 中无值
    svc = _FakeEvalPublishService(publish_record=record)

    with patch(_TEARDOWN_PATCH):
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
            migration_path="/external/snapshot",
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_eval_publish_without_external_migration_path_fallback() -> None:
    """不传外部参数时降级到 publish_record.ext。"""
    record = _record(ext={"migration_path": "/arca/snapshot"})
    svc = _FakeEvalPublishService(publish_record=record)

    with patch(_TEARDOWN_PATCH):
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_eval_publish_fails_without_any_migration_path() -> None:
    """既无外部传入又无 ext 中的 migration_path 时报错。"""
    record = _record(ext={"config_artifact": None})
    svc = _FakeEvalPublishService(publish_record=record)

    with patch(_TEARDOWN_PATCH):
        with pytest.raises(PublishFlowServiceError, match="Build artifact path not found"):
            await svc.eval_publish(
                publish_id=42,
                operator="tester",
            )


@pytest.mark.asyncio
async def test_eval_publish_with_external_docker_image() -> None:
    """外部传入 docker_image 时跳过 resolve_publish_image_pin。"""
    record = _record(ext={"migration_path": "/arca/snapshot"})
    svc = _FakeEvalPublishService(publish_record=record)

    with patch(_TEARDOWN_PATCH):
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
            docker_image="custom/registry/image:latest",
        )

    assert result["success"] is True

@pytest.mark.asyncio
@pytest.mark.parametrize("with_template", [False, True])
@pytest.mark.parametrize("spec_values", [None, {"cpu": 4, "memory": 8}, {"cpu": 4, "memory": 8, "disk": 200}])
async def test_eval_publish_resource_override_is_deployment_local(with_template, spec_values):
    from copy import deepcopy
    from agentclaw.community.kernel.device_dto import ResourceSpecification

    svc = _FakeEvalPublishService()
    bot = svc._bot_service.get_bot_by_address.return_value
    if with_template:
        bot["template_config"] = {
            "resource_spec": {"cpu": 1, "memory": 2, "disk": 10},
            "envs": {"KEEP": "value"}, "docker_image": "original/image:tag",
        }
    original = deepcopy(bot)
    svc._build_service.release_async = AsyncMock()

    async def issue(op, callback):
        await callback()
        return op

    svc._operation_runner.acquire_workflow.side_effect = issue
    spec = ResourceSpecification(**spec_values) if spec_values else None
    with patch(_TEARDOWN_PATCH):
        result = await svc.eval_publish(42, "tester", resource_spec=spec)

    assert result["success"] is True
    assert result["stage"] == PublishStage.EVAL.value
    kwargs = svc._build_service.release_async.await_args.kwargs
    expected = deepcopy(original.get("template_config"))
    if spec_values:
        expected = {**(expected or {}), "resource_spec": spec_values}
    assert kwargs["template_config"] == expected
    assert kwargs["docker_image"] == "registry/image:tag"
    assert bot == original


@pytest.mark.asyncio
async def test_eval_publish_device_count_forwarded() -> None:
    """外部传入 device_count 时应原样转发给 release_async。"""
    record = _record(ext={"migration_path": "/arca/snapshot"})
    svc = _FakeEvalPublishService(publish_record=record)
    svc._build_service.release_async = AsyncMock()

    async def issue(op, callback):
        await callback()
        return op

    svc._operation_runner.acquire_workflow.side_effect = issue

    with patch(_TEARDOWN_PATCH):
        result = await svc.eval_publish(42, "tester", device_count=3)

    assert result["success"] is True
    assert svc._build_service.release_async.await_args.kwargs["device_count"] == 3


@pytest.mark.asyncio
async def test_eval_publish_rejects_non_positive_device_count() -> None:
    """device_count 是部署意向输入，0 或负数没有可执行语义。"""
    svc = _FakeEvalPublishService()

    with pytest.raises(
        PublishFlowServiceError,
        match="device_count must be at least 1",
    ):
        await svc.eval_publish(42, "tester", device_count=0)


@pytest.mark.asyncio
async def test_eval_publish_survives_none_publish_ext_with_external_artifact() -> None:
    """生产形态：publish_record.ext=None 不再触发 NoneType.get 异常。"""
    record = _record(ext=None)
    artifact_ext = {
        "migration_path": "/arca/current-build",
        "skills_manifest": {"active_layout": "pool_active"},
    }
    svc = _FakeEvalPublishService(publish_record=record)
    release_async = _capture_release(svc)

    with patch(_TEARDOWN_PATCH), patch(
        _EXTRA_ENVS_PATCH, return_value={"AGENTCLAW_SKILLS_LAYOUT": "pool_active"}
    ) as extra_envs:
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
            migration_path="/external/current-build",
            artifact_ext=artifact_ext,
        )

    assert result["success"] is True
    assert record.ext is None
    assert svc._ext_state.commit_built_artifact.call_count == 0
    release_kwargs = release_async.await_args.kwargs
    assert release_kwargs["migration_path"] == "/external/current-build"
    assert (
        release_kwargs["ext_info"]["skills_manifest"]
        == artifact_ext["skills_manifest"]
    )
    assert (
        release_kwargs["extra_envs"]["AGENTCLAW_SKILLS_LAYOUT"] == "pool_active"
    )
    assert (
        extra_envs.call_args.args[0]["skills_manifest"]
        == artifact_ext["skills_manifest"]
    )


@pytest.mark.asyncio
async def test_eval_publish_current_artifact_manifest_overrides_stored_manifest() -> None:
    """DRAFT eval 必须使用本次构建 manifest，而不是 DB ext 里的旧 manifest。"""
    stale_manifest = {"active_layout": "legacy_active"}
    current_manifest = {"active_layout": "pool_active"}
    record = _record(
        ext={"migration_path": "/arca/old", "skills_manifest": stale_manifest}
    )
    artifact_ext = {
        "migration_path": "/arca/current-build",
        "skills_manifest": current_manifest,
    }
    svc = _FakeEvalPublishService(publish_record=record)
    release_async = _capture_release(svc)

    with patch(_TEARDOWN_PATCH), patch(_EXTRA_ENVS_PATCH, return_value={}):
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
            artifact_ext=artifact_ext,
        )

    assert result["success"] is True
    assert record.ext["skills_manifest"] == stale_manifest
    release_kwargs = release_async.await_args.kwargs
    assert release_kwargs["migration_path"] == "/arca/current-build"
    assert release_kwargs["ext_info"]["skills_manifest"] is current_manifest


@pytest.mark.asyncio
async def test_eval_publish_current_artifact_without_manifest_does_not_reuse_stored() -> None:
    """本次构建没有 manifest 时，不能回退 ac_bot_publish.ext 里的旧 manifest。"""
    stale_manifest = {"active_layout": "legacy_active"}
    record = _record(
        ext={"migration_path": "/arca/old", "skills_manifest": stale_manifest}
    )
    artifact_ext = {"migration_path": "/arca/current-build"}
    svc = _FakeEvalPublishService(publish_record=record)
    release_async = _capture_release(svc)

    with patch(_TEARDOWN_PATCH), patch(_EXTRA_ENVS_PATCH, return_value={}) as extra_envs:
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
            artifact_ext=artifact_ext,
        )

    assert result["success"] is True
    release_kwargs = release_async.await_args.kwargs
    assert "skills_manifest" not in release_kwargs["ext_info"]
    assert extra_envs.call_args.args[0]["skills_manifest"] is None


@pytest.mark.asyncio
async def test_eval_publish_without_artifact_ext_falls_back_to_stored_manifest() -> None:
    """非 DRAFT-build 调用方不传 artifact_ext 时保留 DB ext 兼容语义。"""
    stored_manifest = {"active_layout": "pool_active"}
    record = _record(
        ext={
            "migration_path": "/arca/snapshot",
            "skills_manifest": stored_manifest,
        }
    )
    svc = _FakeEvalPublishService(publish_record=record)
    release_async = _capture_release(svc)

    with patch(_TEARDOWN_PATCH), patch(_EXTRA_ENVS_PATCH, return_value={}):
        result = await svc.eval_publish(
            publish_id=42,
            operator="tester",
        )

    assert result["success"] is True
    assert (
        release_async.await_args.kwargs["ext_info"]["skills_manifest"]
        is stored_manifest
    )
