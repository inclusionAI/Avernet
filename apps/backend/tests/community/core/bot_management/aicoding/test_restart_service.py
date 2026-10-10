"""Actual BotService lifecycle + coding worker, with only platform I/O faked."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
    journal,
    task_key,
    TASK_TYPE,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_task import (
    AicodingRestartHandler,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import (
    AicodingRestartBackupMixin,
    RestartBackupError,
)
from agentclaw.community.core.task_queue.types import (
    Reschedule,
    Complete,
    Fail,
    EnqueueResult,
    TaskStatus,
)
from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
    _make_service,
)
from tests.community.core.bot_management.aicoding.test_restart_task import (
    Repository,
    Queue,
)


class PlatformQueue(Queue):
    def __init__(self):
        super().__init__()
        self.provider_tasks = []

    def enqueue(
        self, kind, payload, deadline_seconds, *, idempotency_key=None, delay_seconds=0
    ):
        if idempotency_key is None:
            self.provider_tasks.append((kind, payload))
            return EnqueueResult(
                SimpleNamespace(id=100, payload=payload, status=TaskStatus.PENDING),
                True,
            )
        return super().enqueue(
            kind, payload, deadline_seconds, idempotency_key=idempotency_key
        )


@pytest.fixture
def lifecycle():
    repo, queue = Repository(), PlatformQueue()
    repo.bot.update(
        entity_type="staff",
        template_type="personalCoding",
        bot_name="Coding",
        engine_types=["aicoding"],
    )

    def update(_bot_id, _owner, values):
        repo.bot.update(deepcopy(values))
        return deepcopy(repo.bot)

    repo.update_by_owner = update
    binding = dict(
        id=7, status="ACTIVE", device_provider="baas", device_id="old", device_props={}
    )
    device = Mock()
    device.get_device.side_effect = lambda **_: SimpleNamespace(
        **binding, to_dict=lambda: deepcopy(binding)
    )
    binding_repo = Mock()

    def update_props(*, binding_id, props):
        binding["device_props"].update(props)

    def update_status(*, binding_id, status):
        binding["status"] = str(status.value if hasattr(status, "value") else status)

    binding_repo.update_device_props.side_effect = update_props
    binding_repo.update_status.side_effect = update_status
    platform = Mock()
    platform.list_bot_publishes.return_value = [{"id": 10}]
    platform.upgrade_bot.return_value = {"publish_id": 12}  # unchanged other engines
    platform.post_bots_api.return_value = {"publish_id": 12}
    platform._build_create_bot_payload.return_value = {"config": {}}
    service = _make_service(
        bot_repository=repo,
        device_binding_repo=binding_repo,
        device_provider=device,
        baas_service_provider=lambda: platform,
        task_queue_service=queue,
    )
    service._template_service.get_template.return_value = None
    service._template_service.get_template_config.return_value = None
    service._should_register_bcn_provider = Mock(return_value=False)
    service._resolve_bcn_provider_connection_mode = Mock(return_value="ws")
    service._build_engine_extra_envs = Mock(return_value=None)
    service._attach_template_uid_context = Mock(return_value=None)
    service._resolve_baas_restart_template_uuid = Mock(return_value=None)
    progress = Mock(return_value={"status": "PENDING"})
    handler = AicodingRestartHandler(
        repository=repo,
        task_queue=queue,
        bot_service_provider=lambda: service,
        publish_progress=progress,
    )
    return SimpleNamespace(
        repo=repo,
        queue=queue,
        binding=binding,
        platform=platform,
        service=service,
        handler=handler,
        progress=progress,
    )


@pytest.mark.asyncio
async def test_real_baas_restart_observes_existing_intent_after_platform_call(lifecycle):
    s = lifecycle
    accepted = await s.service.restart_bot_async(bot_id="b", user_id="o")
    assert accepted["status"] == "PENDING"
    s.platform.post_bots_api.assert_not_called()

    def upgrade(**kwargs):
        record = journal(s.repo.bot)
        assert record["phase"] == "RESTARTING"
        assert "handoff" not in record
        assert s.binding["device_props"]["restart_workflow_baseline"] == 10
        assert s.repo.bot["status"] == "PENDING"
        assert s.binding["status"] == "PENDING"
        assert len(s.queue.provider_tasks) == 1
        return {"publish_id": 12}

    s.platform.post_bots_api.side_effect = upgrade
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(
        AicodingRestartBackupMixin, "_prepare_restart", return_value=lambda: None
    ):
        assert isinstance(s.handler.handle(task.payload), Reschedule)
    record = journal(s.repo.bot)
    assert record["handoff"]["publish_id"] == "12"
    assert s.service.get_bot("b", "o")["status"] == "PENDING"
    s.repo.bot["status"] = "ACTIVE"
    s.binding["status"] = "ACTIVE"
    # Old runtime ACTIVE is not enough; the exact workflow is still pending.
    assert isinstance(s.handler.handle(task.payload), Reschedule)
    s.repo.bot["ext"]["restart_publish_id"] = "12"
    s.binding["device_props"]["restart_request_id"] = None
    s.progress.return_value = {"status": "SUCCESS"}
    assert isinstance(s.handler.handle(task.payload), Complete)
    s.platform.post_bots_api.assert_called_once()
    assert s.service.get_bot("b", "o")["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_real_backup_failure_never_reaches_platform(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(
        AicodingRestartBackupMixin,
        "_prepare_restart",
        side_effect=RestartBackupError("backup_failed", "secret"),
    ):
        assert isinstance(s.handler.handle(task.payload), Fail)
    s.platform.post_bots_api.assert_not_called()
    assert not s.queue.provider_tasks
    assert s.repo.bot["binding_id"] == 7
    assert s.service.get_bot("b", "o")["status"] == "FAILED"


@pytest.mark.asyncio
async def test_real_lost_baas_response_uses_existing_recovery_intent(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    def lose_response(**kwargs):
        raise ConnectionError("accepted but response lost")

    s.platform.post_bots_api.side_effect = lose_response
    with patch.object(
        AicodingRestartBackupMixin, "_prepare_restart", return_value=lambda: None
    ):
        assert isinstance(s.handler.handle(task.payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "WAITING_READY"
    # Simulate the existing durable BaaS poller adopting the workflow.
    s.binding["device_props"]["restart_publish_id"] = "12"
    assert isinstance(s.handler.handle(task.payload), Reschedule)
    s.repo.bot["status"] = "ACTIVE"
    s.binding["status"] = "ACTIVE"
    s.repo.bot["ext"]["restart_publish_id"] = "12"
    s.binding["device_props"]["restart_request_id"] = None
    s.progress.return_value = {"status": "SUCCESS"}
    assert isinstance(s.handler.handle(task.payload), Complete)
    s.platform.post_bots_api.assert_called_once()


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
@pytest.mark.asyncio
async def test_real_arca_stop_start_skips_backup_and_records_handoff(lifecycle, engine):
    s = lifecycle
    s.repo.bot["active_engine"] = engine
    s.binding["device_provider"] = "arca"
    events = []
    s.service._resolve_restart_target_provider = Mock(return_value="arca")
    device = s.service._device_service_provider()
    device.release_device.side_effect = lambda **_: events.append("release")

    def start(**kwargs):
        assert journal(s.repo.bot)["phase"] == "RESTARTING"
        assert s.repo.bot["binding_id"] is None
        events.append("allocate")
        s.repo.bot.update(binding_id=8, status="PENDING")
        s.binding.update(id=8, device_id="new")
        return deepcopy(s.repo.bot)

    s.service.start_bot = Mock(side_effect=start)

    device.exec_shell_new.side_effect = RuntimeError("sandbox destroyed")

    await s.service.restart_bot_async(bot_id="b", user_id="o")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch(
        "agentclaw.community.core.bot_management.engines.aicoding.restart_backup.prepare_backup",
        side_effect=AssertionError("ARCA must not probe or back up"),
    ) as backup:
        assert isinstance(s.handler.handle(task.payload), Reschedule)
    backup.assert_not_called()
    device.exec_shell_new.assert_not_called()
    assert events == ["release", "allocate"]
    assert isinstance(s.handler.handle(task.payload), Reschedule)
    s.repo.bot["status"] = "ACTIVE"
    assert isinstance(s.handler.handle(task.payload), Complete)
    s.platform.post_bots_api.assert_not_called()
    s.service.start_bot.assert_called_once()


@pytest.mark.asyncio
async def test_sync_preflight_failure_does_not_accept_or_stop_runtime(lifecycle):
    s = lifecycle
    s.platform.list_bot_publishes.side_effect = RuntimeError("provider unavailable")
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart") as backup:
        with pytest.raises(Exception, match="snapshot BaaS restart"):
            await s.service.restart_bot_async(bot_id="b", user_id="o")
    backup.assert_not_called()
    s.platform.post_bots_api.assert_not_called()
    assert s.repo.bot["status"] == "ACTIVE"
    assert not s.queue.tasks
    assert not s.queue.provider_tasks


@pytest.mark.asyncio
async def test_pending_is_written_once_for_successful_baas_restart(lifecycle):
    s = lifecycle
    update = s.repo.update_by_owner
    s.repo.update_by_owner = Mock(side_effect=update)
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    receipt = Mock()
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=receipt):
        assert isinstance(s.handler.handle(task.payload), Reschedule)
    pending_writes = [c for c in s.repo.update_by_owner.call_args_list
                      if c.args[2].get("status") == "PENDING"]
    assert len(pending_writes) == 1
    assert receipt.call_count == 2
    s.platform.post_bots_api.assert_called_once()


@pytest.mark.asyncio
async def test_backup_success_then_preparation_failure_is_terminal(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    s.platform.list_bot_publishes.side_effect = RuntimeError("private provider output")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=Mock()):
        assert isinstance(s.handler.handle(task.payload), Fail)
    assert s.repo.bot["status"] == "FAILED"
    assert journal(s.repo.bot)["phase"] == "FAILED"
    assert "提交前准备失败" in s.repo.bot["ext"]["start_message"]
    assert "private provider output" not in s.repo.bot["ext"]["start_message"]
    s.platform.post_bots_api.assert_not_called()
    assert isinstance(s.handler.handle(task.payload), Fail)
    s.platform.post_bots_api.assert_not_called()


@pytest.mark.asyncio
async def test_definitive_404_marks_failed_and_clears_poll_intent(lifecycle):
    import httpx

    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    request = httpx.Request("POST", "https://provider.invalid/update")
    response = httpx.Response(404, request=request)
    def reject(**kwargs):
        raise httpx.HTTPStatusError(
            "private upstream response", request=request, response=response,
        )

    s.platform.post_bots_api.side_effect = reject
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=Mock()):
        assert isinstance(s.handler.handle(task.payload), Fail)
    assert s.repo.bot["status"] == "FAILED"
    assert "HTTP 404" in s.repo.bot["ext"]["start_message"]
    assert "private upstream response" not in s.repo.bot["ext"]["start_message"]
    assert s.binding["device_props"].get("restart_request_id") is None
    assert s.binding["device_props"]["restart_workflow_baseline"] is None
    s.platform.post_bots_api.assert_called_once()
    assert isinstance(s.handler.handle(task.payload), Fail)
    s.platform.post_bots_api.assert_called_once()


@pytest.mark.asyncio
async def test_final_receipt_failure_clears_intent_without_remote_submit(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    receipt = Mock(side_effect=[None, RestartBackupError("receipt_stale", "changed")])
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=receipt):
        assert isinstance(s.handler.handle(task.payload), Fail)
    assert s.repo.bot["status"] == "FAILED"
    assert s.binding["device_props"].get("restart_request_id") is None
    s.platform.post_bots_api.assert_not_called()


@pytest.mark.asyncio
async def test_persisting_provider_intent_failure_ends_without_waiting(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    update_props = s.service._device_binding_repo.update_device_props.side_effect
    calls = 0

    def fail_first(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database temporarily unavailable")
        return update_props(**kwargs)

    s.service._device_binding_repo.update_device_props.side_effect = fail_first
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=Mock()):
        assert isinstance(s.handler.handle(task.payload), Fail)
    assert s.repo.bot["status"] == "FAILED"
    s.platform.post_bots_api.assert_not_called()


@pytest.mark.asyncio
async def test_request_body_build_failure_is_not_ambiguous_submission(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    s.platform._build_create_bot_payload.side_effect = ValueError("invalid request configuration")
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=Mock()):
        assert isinstance(s.handler.handle(task.payload), Fail)
    assert journal(s.repo.bot)["phase"] == "FAILED"
    assert s.binding["device_props"].get("restart_request_id") is None
    assert "提交前准备失败" in s.repo.bot["ext"]["start_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "moltis", "hermes", "unknown"])
async def test_other_engines_keep_original_pending_write(lifecycle, engine):
    s = lifecycle
    s.repo.bot.update(active_engine=engine, status="PENDING")
    update = s.repo.update_by_owner
    s.repo.update_by_owner = Mock(side_effect=update)
    with patch.object(s.service, "_restart_bot_baas", wraps=s.service._restart_bot_baas) as provider:
        with patch.object(AicodingRestartBackupMixin, "_prepare_restart") as backup:
            await s.service.restart_bot_async(bot_id="b", user_id="o")
    assert not s.queue.tasks
    assert provider.call_count == 1
    assert "prepare_only" not in provider.call_args.kwargs
    backup.assert_not_called()
    pending_writes = [c for c in s.repo.update_by_owner.call_args_list
                      if c.args[2].get("status") == "PENDING"]
    assert len(pending_writes) == 1  # Old behavior, even when already PENDING.
    assert "before_submit" not in s.platform.upgrade_bot.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "moltis"])
async def test_other_engines_keep_original_preparation_rollback(lifecycle, engine):
    s = lifecycle
    s.repo.bot.update(active_engine=engine, status="PENDING")
    s.repo.update_by_owner = Mock(side_effect=[None, deepcopy(s.repo.bot)])
    with pytest.raises(Exception, match="Bot not found while preparing restart"):
        await s.service.restart_bot_async(bot_id="b", user_id="o")
    s.platform.upgrade_bot.assert_not_called()
    writes = [c.args[2] for c in s.repo.update_by_owner.call_args_list]
    assert writes == [{"status": "PENDING"}, {"status": "PENDING"}]
    assert not s.queue.tasks


def test_coding_preflight_does_not_call_shared_restart_or_mutate_status(lifecycle):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_baas import AicodingBaasRestart

    s = lifecycle
    update = s.repo.update_by_owner
    s.repo.update_by_owner = Mock(side_effect=update)
    with patch.object(s.service, "_restart_bot_baas", side_effect=AssertionError("shared restart called")):
        result = AicodingBaasRestart(s.service).preflight(s.service.get_bot("b", "o"), "o")
    assert result is None
    s.platform.list_bot_publishes.assert_called_once()
    s.platform.post_bots_api.assert_not_called()
    s.repo.update_by_owner.assert_not_called()
    s.service._device_binding_repo.update_status.assert_not_called()
    s.service._device_binding_repo.update_device_props.assert_not_called()
    assert not s.queue.provider_tasks


@pytest.mark.asyncio
async def test_coding_worker_never_calls_shared_restart_or_upgrade(lifecycle):
    s = lifecycle
    with patch.object(s.service, "restart_bot", side_effect=AssertionError("legacy restart")), \
         patch.object(s.service, "_restart_bot_baas", side_effect=AssertionError("legacy baas")), \
         patch.object(s.platform, "upgrade_bot", side_effect=AssertionError("legacy client")):
        await s.service.restart_bot_async(bot_id="b", user_id="o")
        task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
        with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=Mock()):
            assert isinstance(s.handler.handle(task.payload), Reschedule)
    s.platform.post_bots_api.assert_called_once()


@pytest.mark.asyncio
async def test_coding_policy_builds_request_then_verifies_then_posts(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    events = []
    s.platform._build_create_bot_payload.side_effect = lambda **_: events.append("build") or {"config": {}}

    def post(**kwargs):
        assert journal(s.repo.bot)["phase"] == "RESTARTING"
        assert kwargs["payload"] == {"config": {}}
        events.append("post")
        return {"publish_id": 12}

    s.platform.post_bots_api.side_effect = post
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart",
                      return_value=lambda: events.append("verify")):
        assert isinstance(s.handler.handle(task.payload), Reschedule)
    assert events == ["verify", "build", "verify", "post"]


@pytest.mark.asyncio
async def test_sync_payload_failure_never_enqueues_or_backs_up(lifecycle):
    s = lifecycle
    s.platform._build_create_bot_payload.side_effect = ValueError("invalid configuration")
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart") as backup:
        with pytest.raises(ValueError, match="invalid configuration"):
            await s.service.restart_bot_async(bot_id="b", user_id="o")
    backup.assert_not_called()
    s.platform.post_bots_api.assert_not_called()
    assert not s.queue.tasks
    assert s.repo.bot["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_binding_target_changed_during_request_build_never_posts(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")

    def build(**kwargs):
        s.binding["device_id"] = "replacement"
        return {"config": {}}

    s.platform._build_create_bot_payload.side_effect = build
    task = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))
    with patch.object(AicodingRestartBackupMixin, "_prepare_restart", return_value=Mock()):
        assert isinstance(s.handler.handle(task.payload), Fail)
    s.platform.post_bots_api.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "moltis", "hermes"])
async def test_other_engines_never_enter_coding_pipeline(lifecycle, engine):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_baas import AicodingBaasRestart

    s = lifecycle
    s.repo.bot["active_engine"] = engine
    with patch.object(AicodingBaasRestart, "preflight", side_effect=AssertionError("coding preflight")), \
         patch.object(AicodingBaasRestart, "execute", side_effect=AssertionError("coding executor")):
        await s.service.restart_bot_async(bot_id="b", user_id="o")
    s.platform.upgrade_bot.assert_called_once()
    s.platform.post_bots_api.assert_not_called()
    assert not s.queue.tasks


@pytest.mark.asyncio
async def test_strategy_request_matches_existing_upgrade_payload_defaults(lifecycle):
    s = lifecycle
    await s.service.restart_bot_async(bot_id="b", user_id="o")
    kwargs = s.platform._build_create_bot_payload.call_args.kwargs
    assert kwargs["device_count"] == 1
    assert kwargs["migration_path"] == ""
    assert kwargs["version"] == "1"
    assert kwargs["auto_approve_publish"] is True
    assert kwargs["mount_home_dir_storage"] is True
    assert kwargs["in_place"] is False
    assert kwargs["ext_info"] is None
    assert kwargs["owner_id"] == "o"
    assert "bot_uuid" not in kwargs
    # Credentials/configurations are deliberately NOT serialized into queue data.
    payload = s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o")).payload
    assert "request_payload" not in payload and "template_config" not in payload


@pytest.mark.parametrize("bot_type", ["personal", "service"])
def test_engine_preparation_matches_legacy_payload_builder_arguments(lifecycle, bot_type):
    import inspect
    from agentclaw.community.core.bot_management.engines.aicoding.restart_baas import AicodingBaasRestart
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    s = lifecycle
    s.repo.bot["bot_type"] = bot_type
    bot = s.service.get_bot("b", "o")
    AicodingBaasRestart(s.service).preflight(deepcopy(bot), "o")
    coding_arguments = dict(s.platform._build_create_bot_payload.call_args.kwargs)
    s.platform._post_bots_api.return_value = {"publish_id": 12}
    s.platform.upgrade_bot.side_effect = lambda **kwargs: BaasService.upgrade_bot(s.platform, **kwargs)
    s.service._restart_bot_baas(bot_id="b", user_id="o", binding_id=7, bot=deepcopy(bot))
    legacy_arguments = dict(s.platform._build_create_bot_payload.call_args.kwargs)
    signature = inspect.signature(BaasService._build_create_bot_payload)

    def normalized(arguments):
        bound = signature.bind(None, **arguments)
        bound.apply_defaults()
        values = dict(bound.arguments)
        values.pop("request_id")  # Independent operations must not share an id.
        return values

    assert normalized(coding_arguments) == normalized(legacy_arguments)
