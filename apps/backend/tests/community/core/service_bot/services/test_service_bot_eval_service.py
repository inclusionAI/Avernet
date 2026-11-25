"""Unit guards for service-Bot evaluation context resolution and authorization."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
    ServicePublicationNotFoundError,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services import service_bot_eval_service
from agentclaw.community.core.service_bot.services.service_bot_eval_service import (
    ServiceBotEvalService,
)

NOW = datetime(2026, 11, 9, 12, 0, 0)


class FixedBotRepository:
    """Only the legacy locator resolution used by the service under test."""

    def __init__(self, bot: dict) -> None:
        self._bot = bot

    def list_matches_by_legacy_locator(self, bot_id: str, owner_id: str) -> list[dict]:
        assert bot_id == self._bot["bot_id"]
        assert owner_id == self._bot["owner_id"]
        return [self._bot]


def record(version: int = 1, status: PublishStatus = PublishStatus.DRAFT) -> BotPublishRecord:
    return BotPublishRecord(
        id=version,
        source_bot_pk=1,
        source_bot_id="service-eval",
        publish_bot_id="service-eval",
        name="Service evaluation Bot",
        owner_id="owner",
        permission_owner="owner",
        status=status.value,
        version=version,
        env="dev",
        gmt_create=NOW,
        gmt_modified=NOW,
    )


def service(bot: dict, records: list[BotPublishRecord] | None = None) -> ServiceBotEvalService:
    publish_repo = Mock()
    publish_repo.list_by_source_bot.return_value = records or []
    return ServiceBotEvalService(
        bot_repo=FixedBotRepository(bot),
        publish_repo=publish_repo,
        collaborator_service=Mock(),
        plugin=AsyncMock(),
    )


def bot(**overrides: object) -> dict:
    values: dict = {
        "id": 1,
        "bot_id": "service-eval",
        "owner_id": "owner",
        "entity_id": "owner",
        "bot_type": "service",
        "gmt_modified": NOW,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("modified", "expected"),
    [
        (datetime(2026, 11, 9, 8, 30, 5), "2026-11-09T08:30:05"),
        (date(2026, 11, 9), "2026-11-09"),
    ],
)
def test_eval_context_serializes_datetime_and_date_modified(
    monkeypatch, modified, expected
):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(gmt_modified=modified), [record()])

    _, release, context = svc._resolve(
        "service-eval",
        "1",
        actor_id="owner",
        owner_id="owner",
        required_level=PermissionLevel.MEMBER,
        require_draft=True,
    )

    assert release.version == 1
    assert context.bot_id == "service-eval"
    assert context.latest_bot_modified_at == expected


def test_eval_context_rejects_actor_without_permission(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    collaborator_service = Mock()
    collaborator_service.get_permission_level.return_value = PermissionLevel.NONE
    svc = ServiceBotEvalService(
        bot_repo=FixedBotRepository(bot()),
        publish_repo=Mock(),
        collaborator_service=collaborator_service,
        plugin=AsyncMock(),
    )

    with pytest.raises(ServicePublicationNotFoundError, match="bot not found"):
        svc._resolve(
            "service-eval",
            "1",
            actor_id="member",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
        )


def test_eval_context_rejects_non_service_bot(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(bot_type="personal"), [record()])

    with pytest.raises(ServicePublicationConflictError, match="not a service bot"):
        svc._resolve(
            "service-eval",
            "1",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
        )


def test_eval_context_rejects_missing_requested_version(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(), [record(version=2)])

    with pytest.raises(ServicePublicationNotFoundError, match="publication not found"):
        svc._resolve(
            "service-eval",
            "3",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
        )


def test_eval_context_rejects_service_bot_without_publication(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot())

    with pytest.raises(ServicePublicationConflictError, match="no actionable publication"):
        svc._resolve(
            "service-eval",
            "latest",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
            latest_when_missing=True,
        )


def test_precheck_rejects_non_draft_publication(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc = service(bot(), [record(status=PublishStatus.SUCCESS)])

    with pytest.raises(ServicePublicationConflictError, match="not a draft"):
        svc._resolve(
            "service-eval",
            "1",
            actor_id="owner",
            owner_id="owner",
            required_level=PermissionLevel.MEMBER,
            require_draft=True,
        )


class _StandardTaskRecord:
    id = 123
    uuid = "quality-123"
    task_type = "eval"
    biz_type = "service_bot_single"
    status = "success"
    ext: dict = {}


@pytest.mark.parametrize(
    ("status", "ext", "expected"),
    [
        ("success", {"standard_result_save_status": "SKIPPED"}, False),
        ("success", {"standard_result_saved": True}, False),
        ("success", {}, True),
        ("failed", {"standard_result_save_status": "SAVING"}, True),
        ("task_executed", {"standard_result_save_status": "SAVING"}, False),
    ],
)
def test_pending_standard_save_detects_only_unsaved_terminal_tasks(status, ext, expected):
    record = _StandardTaskRecord()
    record.status = status
    record.ext = ext
    assert ServiceBotEvalService._is_pending_standard_save(record) is expected


def test_public_environment_result_hides_infrastructure_identifiers():
    result = ServiceBotEvalService._public_standard_environment_result(
        {
            "supported": True,
            "default_tag": "eval",
            "destroyed": True,
            "binding_id": "binding-secret",
            "device_id": "device-secret",
            "eval_headers": {"X-Eval-Id": "eval-secret"},
            "reason": "standard_eval_finished",
        },
        reason="standard_eval_finished",
    )
    assert result == {
        "supported": True,
        "default_tag": "eval",
        "destroyed": True,
        "destroy_status": "succeeded",
        "reason": "standard_eval_finished",
        "error": "",
    }


def test_standard_task_snapshot_hides_environment_routing_internals():
    record = _StandardTaskRecord()
    record.ext = {
        "standard_environment": {
            "supported": True,
            "default_tag": "eval",
            "build_status": "active",
            "binding_id": "binding-secret",
            "device_id": "device-secret",
            "eval_headers": {"X-Eval-Id": "eval-secret"},
        },
    }
    snapshot = ServiceBotEvalService._standard_task_snapshot(record)
    assert snapshot["environment"] == {
        "supported": True,
        "default_tag": "eval",
        "build_status": "active",
        "build_info": "",
        "error": "",
    }
    assert snapshot["environment_status"] == "active"


def test_standard_task_snapshot_exposes_section_seven_task_id_and_flow_steps():
    record = _StandardTaskRecord()
    record.ext = {
        "set_uuid": "set-1",
        "sample_set_name": "标准样本集",
        "sample_set_count": 300,
        "eval_progress": {
            "status": "completed",
            "report": {"reportUrl": "https://example.test/report"},
        },
        "standard_result_saved": True,
    }
    snapshot = ServiceBotEvalService._standard_task_snapshot(record)
    assert snapshot["id"] == 123
    assert snapshot["task_id"] == 123
    assert snapshot["uuid"] == "quality-123"
    assert snapshot["report_url"] == "https://example.test/report"
    assert snapshot["set_name"] == "标准样本集"
    assert [step["key"] for step in snapshot["steps"]] == [
        "create_environment",
        "confirm_sample_set",
        "execute_evaluation",
        "evaluation_report",
        "save_evaluation_result",
    ]
    assert snapshot["steps"][-1]["status"] == "DONE"


class _TaskRecord:
    def __init__(
        self,
        *,
        task_id: int = 123,
        status: str = "init",
        ext: dict | None = None,
    ) -> None:
        self.id = task_id
        self.uuid = f"quality-{task_id}"
        self.task_type = "eval"
        self.biz_type = "service_bot_single"
        self.status = status
        self.ext = ext or {}


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _standard_service() -> tuple[ServiceBotEvalService, Mock, Mock, Mock, AsyncMock]:
    publish_repo = Mock()
    publish_repo.list_by_source_bot.return_value = [record()]
    plugin = AsyncMock()
    quality_tasks = Mock()
    task_processor = Mock()
    masa_eval_http = Mock()
    svc = ServiceBotEvalService(
        bot_repo=FixedBotRepository(bot()),
        publish_repo=publish_repo,
        collaborator_service=Mock(),
        plugin=plugin,
        quality_tasks=quality_tasks,
        task_processor=task_processor,
        masa_eval_http=masa_eval_http,
    )
    return svc, plugin, quality_tasks, task_processor, masa_eval_http


def test_standard_backend_reports_each_missing_dependency():
    plugin = AsyncMock()
    quality_tasks = Mock()
    task_processor = Mock()
    repo = FixedBotRepository(bot())
    publish_repo = Mock()

    variants = [
        (None, None, None),
        (quality_tasks, None, None),
        (quality_tasks, task_processor, None),
    ]
    for has_quality, has_processor, has_masa in variants:
        svc = ServiceBotEvalService(
            bot_repo=repo,
            publish_repo=publish_repo,
            collaborator_service=Mock(),
            plugin=plugin,
            quality_tasks=has_quality,
            task_processor=has_processor,
            masa_eval_http=has_masa,
        )
        with pytest.raises(ServicePublicationConflictError, match="unavailable"):
            svc._standard_backend()


def test_standard_sample_set_response_covers_nested_sets_invalid_and_empty(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc, _, _, _, masa = _standard_service()

    nested = _Response({"support": True, "data": {"sets": [{"uuid": "set-1"}]}})
    masa.get.return_value = nested
    assert svc._list_sample_sets(bot_id="bot", owner_id="owner", masa_eval_http=masa) == {
        "support": True,
        "sets": [{"uuid": "set-1"}],
    }

    empty = _Response({"support": False})
    assert svc._list_sample_sets(bot_id="bot", owner_id="owner", masa_eval_http=_FakeHttp(empty))["sets"] == []

    invalid_http = _FakeHttp(_Response({"sets": "invalid"}))
    with pytest.raises(ServicePublicationConflictError, match="invalid sample sets"):
        svc._list_sample_sets(
            bot_id="bot",
            owner_id="owner",
            masa_eval_http=invalid_http,
        )


class _FakeHttp:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return self.response


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("init", ["RUNNING", "WAITING", "WAITING", "WAITING", "WAITING"]),
        ("env_ready", ["DONE", "DONE", "RUNNING", "WAITING", "WAITING"]),
        ("task_executed", ["DONE", "DONE", "DONE", "RUNNING", "WAITING"]),
        ("unknown", ["UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "WAITING"]),
    ],
)
def test_standard_task_snapshot_covers_each_flow_state(status, expected):
    snapshot = ServiceBotEvalService._standard_task_snapshot(
        _TaskRecord(status=status)
    )
    assert [step["status"] for step in snapshot["steps"]] == expected


def test_standard_task_snapshot_separates_environment_and_masafailure_steps():
    masa_failure = _TaskRecord(
        status="failed",
        ext={
            "set_task_uuid": "masa-task-1",
            "standard_environment": {"supported": True, "build_status": "active"},
            "error_msg": "MASA evaluation failed (trace_id: trace-1)",
        },
    )
    snapshot = ServiceBotEvalService._standard_task_snapshot(masa_failure)
    statuses = [step["status"] for step in snapshot["steps"]]
    assert statuses == ["DONE", "DONE", "FAILED", "FAILED", "RUNNING"]
    assert snapshot["steps"][0]["error"] == ""
    assert "MASA evaluation failed" in snapshot["steps"][2]["error"]

    environment_failure = _TaskRecord(
        status="failed",
        ext={
            "standard_environment": {
                "supported": True,
                "build_status": "failed",
                "error": "Build failed before MASA task creation",
            },
        },
    )
    snapshot = ServiceBotEvalService._standard_task_snapshot(environment_failure)
    statuses = [step["status"] for step in snapshot["steps"]]
    assert statuses == ["FAILED", "WAITING", "WAITING", "WAITING", "RUNNING"]
    assert snapshot["steps"][0]["error"] == "Build failed before MASA task creation"


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        ({"standard_result_save_status": "skipped"}, "DONE"),
        ({}, "RUNNING"),
    ],
)
def test_standard_task_snapshot_saves_terminal_result_state(ext, expected):
    snapshot = ServiceBotEvalService._standard_task_snapshot(
        _TaskRecord(status="success", ext=ext)
    )
    assert snapshot["steps"][-1]["status"] == expected


def test_standard_task_filters_and_sample_set_lookup():
    version_one = _TaskRecord(task_id=1, ext={"source": "standard_eval", "version": 1})
    version_two = _TaskRecord(task_id=2, ext={"source": "standard_eval", "version": 2})
    simple = _TaskRecord(task_id=3)
    records = ServiceBotEvalService._standard_task_records(
        [version_one, version_two, simple], bot_version="1"
    )
    assert records == [version_one]
    assert records != [version_two]

    sample = {"uuid": "set-1"}
    lookup_service = _standard_service()[0]
    assert lookup_service._find_sample_set("set-1", [{"uuid": "no"}, sample]) is sample
    assert lookup_service._find_sample_set("missing", [{"uuid": "set-1"}]) is None


@pytest.mark.asyncio
async def test_standard_environment_creation_and_polling_branches():
    svc, plugin, quality_tasks, _, _ = _standard_service()
    current = _TaskRecord(task_id=123, status="init")

    assert svc._standard_environment({"build_status": "active"})["default_tag"] == "eval"
    with pytest.raises(ServicePublicationConflictError, match="environment response"):
        svc._standard_environment(None)

    plugin.create_standard_environment.return_value = {"supported": True, "build_status": "active"}
    quality_tasks.update_task_status.return_value = current
    await svc._prepare_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "env_ready"

    plugin.create_standard_environment.return_value = {"supported": True, "build_status": "failed"}
    await svc._prepare_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "failed"

    plugin.create_standard_environment.return_value = {"supported": True, "build_status": "building"}
    await svc._prepare_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "env_preparing"

    plugin.create_standard_environment.return_value = {"supported": False}
    await svc._prepare_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "init"

    plugin.get_standard_environment_status.return_value = {"supported": True, "build_status": "active"}
    await svc._poll_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "env_ready"

    plugin.get_standard_environment_status.return_value = {"supported": True, "build_status": "failed"}
    await svc._poll_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "failed"

    plugin.get_standard_environment_status.return_value = {"supported": True, "build_status": "building"}
    await svc._poll_standard_environment(current, Mock())
    assert quality_tasks.update_task_status.call_args.args[1] == "init"


@pytest.mark.asyncio
async def test_standard_task_driver_saves_result_and_destroys_environment(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc, plugin, quality_tasks, task_processor, _ = _standard_service()
    context = service_bot_eval_service.ServiceBotEvalContext(
        bot_id="service-eval",
        bot_version="1",
        release_id="1",
        publish_id="1",
        operator_id="owner",
        latest_bot_modified_at="2026-11-09T12:00:00",
    )
    initial = _TaskRecord(task_id=123, status="init", ext={})
    env_ready = _TaskRecord(
        task_id=123,
        status="env_ready",
        ext={
            "source": "standard_eval",
            "version": "1",
            "standard_environment_provider": "service_bot_eval_plugin",
            "eval_env": {"build_status": "active"},
            "eval_progress": {"report": {"reportUrl": "https://reports.example.test"}},
        },
    )
    task_created = _TaskRecord(task_id=123, status="task_created", ext=env_ready.ext)
    task_executed = _TaskRecord(task_id=123, status="task_executed", ext=env_ready.ext)
    success = _TaskRecord(task_id=123, status="success", ext=env_ready.ext)

    plugin.create_standard_environment.return_value = {"supported": True, "build_status": "active"}
    plugin.save_standard_result.return_value = {
        "saved": True,
        "gate_status": "PASSED",
    }
    plugin.destroy_standard_environment.return_value = {
        "supported": True,
        "destroyed": True,
        "destroy_status": "succeeded",
        "reason": "standard_eval_finished",
    }
    quality_tasks.get_task_by_id.return_value = initial
    quality_tasks.update_task_status.side_effect = [
        env_ready,
        success,
        success,
    ]
    task_processor.process = AsyncMock(side_effect=[task_created, task_executed, success])

    await svc._drive_standard_task(
        123, context=context, trigger_source="manual_restart_standard_eval"
    )

    plugin.save_standard_result.assert_awaited_once()
    plugin.destroy_standard_environment.assert_awaited_once_with(
        context,
        default_tag="eval",
        reason="standard_eval_finished",
    )
    updated_statuses = [
        call.args[1] for call in quality_tasks.update_task_status.call_args_list
    ]
    assert updated_statuses == ["env_ready", "success", "success"]


@pytest.mark.asyncio
async def test_create_standard_task_reuses_active_and_persists_text_goal(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc, _, quality_tasks, _, masa = _standard_service()
    active = _TaskRecord(
        task_id=123,
        status="env_ready",
        ext={"source": "standard_eval", "version": "1", "trigger_source": "manual"},
    )
    masa.get.return_value = _Response(
        {"sets": [{"uuid": "set-1", "name": "标准样本集", "sample_count": 300}]}
    )
    quality_tasks.list_tasks.return_value = ([active], 1)
    svc._standard_task_drivers[123] = Mock()

    reused = await svc.create_standard_task(
        "service-eval",
        "1",
        actor_id="owner",
        owner_id="owner",
        task={
            "set_uuid": "set-1",
            "goal_type": "json",
            "goal_json": {"metric": "pass"},
            "trigger_source": "manual",
        },
    )
    assert reused["task_id"] == 123

    created = _TaskRecord(
        task_id=124,
        status="init",
        ext={
            "source": "standard_eval",
            "publish_id": 1,
            "version": 1,
            "set_uuid": "set-1",
            "sample_set_name": "标准样本集",
            "sample_set_count": 300,
            "sample_set_url": "",
            "trigger_source": "manual",
            "goal_type": "text",
            "goal_text": "通过率不低于90%",
        },
    )
    quality_tasks.list_tasks.return_value = ([], 0)
    quality_tasks.create_task.return_value = created
    svc._standard_task_drivers[124] = Mock()

    result = await svc.create_standard_task(
        "service-eval",
        "1",
        actor_id="owner",
        owner_id="owner",
        task={
            "set_uuid": "set-1",
            "goal_type": "text",
            "goal_text": "通过率不低于90%",
            "trigger_source": "manual",
        },
    )
    assert result["task_id"] == 124
    ext = quality_tasks.create_task.call_args.kwargs["ext"]
    assert ext["goal_text"] == "通过率不低于90%"


@pytest.mark.asyncio
async def test_current_standard_task_errors_signal_and_pending_save(monkeypatch):
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc, _, quality_tasks, _, _ = _standard_service()

    quality_tasks.list_tasks.return_value = ([], 0)
    with pytest.raises(ServicePublicationNotFoundError, match="standard evaluation task"):
        await svc.get_current_standard_task(
            "service-eval",
            "1",
            actor_id="owner",
            owner_id="owner",
        )

    pending_save = _TaskRecord(
        task_id=125,
        status="success",
        ext={
            "source": "standard_eval",
            "version": "1",
            "eval_progress": {"report": {"reportUrl": "https://reports.example.test"}},
        },
    )
    quality_tasks.list_tasks.return_value = ([pending_save], 1)
    svc._standard_task_drivers[125] = Mock()

    snapshot = await svc.get_current_standard_task(
        "service-eval",
        "1",
        actor_id="owner",
        owner_id="owner",
    )
    assert snapshot["task_id"] == 125
    assert snapshot["steps"][-1]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_prepare_standard_environment_uses_four_hour_default_ttl():
    """Long-running standard evaluation: env TTL default must cover a full eval run."""
    svc, plugin, quality_tasks, _, _ = _standard_service()
    current = _TaskRecord(task_id=123, status="init")
    plugin.create_standard_environment.return_value = {
        "supported": True,
        "build_status": "building",
    }
    quality_tasks.update_task_status.return_value = current

    await svc._prepare_standard_environment(current, Mock())

    assert plugin.create_standard_environment.call_args.kwargs["ttl_minutes"] == 240


@pytest.mark.asyncio
async def test_standard_task_driver_progress_poll_backoff(monkeypatch):
    """task_created polls MASA every 15s for the first 5 minutes, then 60s."""
    monkeypatch.setattr(service_bot_eval_service, "get_current_env", lambda: "dev")
    svc, plugin, quality_tasks, task_processor, _ = _standard_service()
    context = service_bot_eval_service.ServiceBotEvalContext(
        bot_id="service-eval",
        bot_version="1",
        release_id="1",
        publish_id="1",
        operator_id="owner",
        latest_bot_modified_at="2026-11-09T12:00:00",
    )

    initial = _TaskRecord(task_id=123, status="init", ext={})
    driver_ext = {
        "source": "standard_eval",
        "version": "1",
        "standard_environment_provider": "service_bot_eval_plugin",
        "eval_env": {"build_status": "active"},
    }
    env_ready = _TaskRecord(task_id=123, status="env_ready", ext=driver_ext)
    task_created_first = _TaskRecord(task_id=123, status="task_created", ext=driver_ext)
    task_created_second = _TaskRecord(task_id=123, status="task_created", ext=driver_ext)
    task_executed = _TaskRecord(task_id=123, status="task_executed", ext=driver_ext)
    success = _TaskRecord(task_id=123, status="success", ext=driver_ext)

    plugin.create_standard_environment.return_value = {"supported": True, "build_status": "active"}
    plugin.save_standard_result.return_value = {"saved": True, "gate_status": "PASSED"}
    plugin.destroy_standard_environment.return_value = {"supported": True, "destroyed": True}
    quality_tasks.get_task_by_id.return_value = initial
    quality_tasks.update_task_status.side_effect = [env_ready, success, success]
    task_processor.process = AsyncMock(
        side_effect=[
            task_created_first,
            task_created_second,
            task_created_second,
            task_executed,
            success,
        ]
    )
    fake_time = Mock()
    fake_time.monotonic = Mock(side_effect=[0, 160, 320])
    sleepy = AsyncMock()
    monkeypatch.setattr(service_bot_eval_service, "time", fake_time)
    monkeypatch.setattr(service_bot_eval_service.asyncio, "sleep", sleepy)

    await svc._drive_standard_task(
        123, context=context, trigger_source="manual_restart_standard_eval"
    )

    assert [call.args[0] for call in sleepy.call_args_list] == [15, 60]
