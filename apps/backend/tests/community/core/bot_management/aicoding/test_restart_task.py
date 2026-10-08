"""Ordinary coding restart: durable admission, backup fencing and status contract."""

from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
    BUSINESS_TIMEOUT,
    KEY,
    TASK_TYPE,
    RestartState,
    RestartSuperseded,
    current_restart,
    journal,
    task_key,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_task import (
    AicodingRestartHandler,
    AicodingRestartLifecycle,
)
from agentclaw.community.core.bot_management.engines.aicoding.restart_backup import (
    RestartBackupError,
)
from agentclaw.community.core.bot_management.engines.registry import (
    resolve_restart_strategy,
)
from agentclaw.community.core.bot_management.engines.restart_contract import (
    RestartServices,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import (
    Complete,
    EnqueueResult,
    Fail,
    Reschedule,
    TaskStatus,
)
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)


class Repository:
    def __init__(self, engine="aicoding"):
        self.bot = dict(
            bot_id="b",
            owner_id="o",
            entity_id="o",
            bot_type="personal",
            active_engine=engine,
            status="ACTIVE",
            binding_id=7,
            ext={"unrelated": {"keep": True}},
        )
        self.lock = Lock()
        self.conflict = False
        self.failed_write = False
        self.tenants = []

    def get_by_id_and_owner(self, bot_id, owner_id):
        self.tenants.append(get_current_avernet_tenant())
        return deepcopy(self.bot)

    def compare_and_set_ext(
        self, *, bot_id, owner_id, expected_ext, ext
    ):
        with self.lock:
            if self.failed_write:
                raise RuntimeError("database unavailable")
            if self.conflict:
                self.bot["ext"]["concurrent"] = 1
                self.conflict = False
            if self.bot["ext"] != expected_ext:
                return None
            self.bot["ext"] = deepcopy(ext)
            return deepcopy(self.bot)

    def update_by_owner(self, bot_id, owner_id, update_data):
        with self.lock:
            if self.failed_write:
                raise RuntimeError("database unavailable")
            self.bot.update(deepcopy(update_data))
            return deepcopy(self.bot)


class Queue:
    def __init__(self):
        self.tasks = {}
        self.lock = Lock()
        self.fail = False

    def find_by_idempotency_key(self, kind, key):
        return self.tasks.get(key)

    def enqueue(self, kind, payload, deadline_seconds, *, idempotency_key):
        if self.fail:
            raise RuntimeError("queue unavailable")
        with self.lock:
            old = self.tasks.get(idempotency_key)
            if old and old.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return EnqueueResult(old, False)
            task = SimpleNamespace(
                id=(old.id + 1 if old else 1),
                payload=deepcopy(payload),
                status=TaskStatus.PENDING,
            )
            self.tasks[idempotency_key] = task
            return EnqueueResult(task, True)


@pytest.fixture
def setup(monkeypatch):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_baas import AicodingBaasRestart

    # These are task-state-machine tests. Actual strategy-owned orchestration
    # and shared-service isolation are exercised by test_restart_service.py.
    monkeypatch.setattr(AicodingBaasRestart, "preflight", lambda *args: None)
    monkeypatch.setattr(AicodingBaasRestart, "execute", lambda self, bot, execution:
        self.service.restart_bot(bot_id=execution.state.bot_id,
                                 user_id=execution.state.owner_id,
                                 nick_name=execution.payload.get("nick_name")))
    repo, queue = Repository(), Queue()
    binding = {
        "status": "ACTIVE",
        "device_id": "target",
        "device_provider": "baas",
        "device_props": {},
    }
    service = Mock()
    service.get_bot.side_effect = lambda *_: {
        **repo.get_by_id_and_owner("b", "o"),
        "device_binding": deepcopy(binding),
    }
    service._repository, service._task_queue_service = repo, queue
    service._template_service = Mock()
    service._restart_bot_baas = Mock(return_value=None)
    ctx, strategy = resolve_restart_strategy(repo.bot)
    services = RestartServices(repo, queue, service.get_bot, service._template_service, service)
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
        service=service,
        ctx=ctx,
        strategy=strategy,
        services=services,
        handler=handler,
        progress=progress,
    )


async def submit(s):
    return await BotService.restart_bot_async(s.service, bot_id="b", user_id="o")


def task(s):
    return s.queue.find_by_idempotency_key(TASK_TYPE, task_key("b", "o"))


def mock_lifecycle(s, *, backup_error=None, publish_id="12", crash_after_handoff=False):
    def restart(**kwargs):
        assert kwargs == {"bot_id": "b", "user_id": "o", "nick_name": None}
        execution = current_restart.get()
        if backup_error:
            raise backup_error
        execution.check_target(s.ctx, s.repo.bot["binding_id"])
        execution.fence_mutation()
        s.binding["device_props"] = {
            "restart_request_id": "request",
            "restart_publish_id": publish_id,
            "restart_workflow_baseline": 10,
        }
        if crash_after_handoff:
            raise ConnectionError("secret upstream response")
        return deepcopy(s.repo.bot)

    s.service.restart_bot.side_effect = restart


@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
@pytest.mark.asyncio
async def test_admission_returns_pending_without_executing_restart(setup, engine):
    s = setup
    s.repo.bot["active_engine"] = engine
    s.repo.conflict = True
    result = await submit(s)
    assert result["status"] == "PENDING"
    assert result["binding_id"] == 7
    assert result["restart_in_progress"]
    assert s.repo.bot["status"] == "PENDING"
    assert s.repo.bot["ext"]["unrelated"] == {"keep": True}
    assert s.repo.bot["ext"]["concurrent"] == 1
    s.service.restart_bot.assert_not_called()
    assert task(s).payload["operation_id"] == journal(s.repo.bot)["operation_id"]
    assert "extra_configs" not in task(s).payload


@pytest.mark.asyncio
async def test_duplicate_submission_joins_same_operation(setup):
    s = setup
    first = await submit(s)
    second = await submit(s)
    assert first["restart_operation_id"] == second["restart_operation_id"]
    assert len(s.queue.tasks) == 1


@pytest.mark.asyncio
async def test_concurrent_submissions_dedup_before_backup(setup):
    import asyncio

    s = setup
    results = await asyncio.gather(*[submit(s) for _ in range(12)])
    assert len({r["restart_operation_id"] for r in results}) == 1
    assert len(s.queue.tasks) == 1
    s.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_failure_does_not_write_pending(setup):
    setup.queue.fail = True
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await submit(setup)
    assert setup.repo.bot["status"] == "ACTIVE"
    assert KEY not in setup.repo.bot["ext"]
    setup.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
async def test_worker_repairs_enqueue_before_journal_crash(setup):
    s = setup
    s.repo.failed_write = True
    with pytest.raises(RuntimeError, match="database unavailable"):
        await submit(s)
    assert task(s) is not None and KEY not in s.repo.bot["ext"]
    s.repo.failed_write = False
    mock_lifecycle(s)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "WAITING_READY"
    assert s.repo.bot["status"] == "PENDING"


@pytest.mark.asyncio
async def test_backup_failure_persists_existing_status_and_startup_error_fields(setup):
    s = setup
    s.repo.bot["ext"]["start_status"] = "SUCCEEDED"
    await submit(s)
    mock_lifecycle(
        s, backup_error=RestartBackupError("backup_failed", "secret raw text")
    )
    result = s.handler.handle(task(s).payload)
    assert isinstance(result, Fail)
    view = s.service.get_bot("b", "o")
    assert view["status"] == "FAILED"
    assert "旧容器未销毁" in view["ext"]["start_message"]
    assert "secret" not in view["ext"]["start_message"]
    assert s.binding["status"] == "ACTIVE"
    assert s.repo.bot["binding_id"] == 7
    assert s.repo.bot["status"] == "FAILED"
    assert s.repo.bot["ext"]["start_status"] == "FAILED"
    assert s.repo.bot["ext"]["start_message"] == view["ext"]["start_message"]
    assert current_restart.get() is None


@pytest.mark.asyncio
async def test_backup_timeout_has_actionable_reason(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s, backup_error=TimeoutError("secret"))
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    assert "备份超时" in s.service.get_bot("b", "o")["ext"]["start_message"]


@pytest.mark.asyncio
async def test_target_changes_before_worker_does_not_restart(setup):
    s = setup
    await submit(s)
    s.repo.bot["binding_id"] = 99
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    s.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
async def test_read_returns_persisted_runtime_state_without_restart_projection(setup):
    s = setup
    await submit(s)
    s.repo.bot["status"] = "ACTIVE"  # old runtime callback
    s.repo.bot["ext"].update(start_status="FAILED", start_message="old failure")
    s.binding["error_message"] = "old device error"
    view = s.service.get_bot("b", "o")
    assert view["status"] == "ACTIVE"
    assert view["ext"]["start_message"] == "old failure"
    assert view["ext"]["start_status"] == "FAILED"
    assert view["device_binding"]["error_message"] == "old device error"
    assert s.repo.bot["ext"]["start_status"] == "FAILED"


@pytest.mark.asyncio
async def test_real_workflow_success_required_then_raw_readiness(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    s.repo.bot["status"] = "ACTIVE"
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert s.service.get_bot("b", "o")["status"] == "ACTIVE"
    s.repo.bot["ext"]["restart_publish_id"] = "12"
    s.binding["device_props"]["restart_request_id"] = None
    s.progress.return_value = {"status": "SUCCESS"}
    assert isinstance(s.handler.handle(task(s).payload), Complete)
    assert s.service.get_bot("b", "o")["status"] == "ACTIVE"
    s.service.restart_bot.assert_called_once()


@pytest.mark.asyncio
async def test_lost_response_after_handoff_observes_without_reissuing(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s, crash_after_handoff=True)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "WAITING_READY"
    s.repo.bot["status"] = "ACTIVE"
    s.repo.bot["ext"]["restart_publish_id"] = "12"
    s.binding["device_props"]["restart_request_id"] = None
    s.progress.return_value = {"status": "SUCCESS"}
    assert isinstance(s.handler.handle(task(s).payload), Complete)
    s.service.restart_bot.assert_called_once()


@pytest.mark.asyncio
async def test_provider_adoption_can_finish_before_own_observer(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s, publish_id=None, crash_after_handoff=True)
    s.handler.handle(task(s).payload)
    s.binding["device_props"] = {"restart_publish_id": "12", "restart_request_id": None}
    s.repo.bot["status"] = "ACTIVE"
    s.repo.bot["ext"]["restart_publish_id"] = "12"
    s.binding["device_props"]["restart_request_id"] = None
    s.progress.return_value = {"status": "SUCCESS"}
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert isinstance(s.handler.handle(task(s).payload), Complete)
    s.service.restart_bot.assert_called_once()


@pytest.mark.asyncio
async def test_ambiguous_mutation_waits_for_handoff_never_replays(setup):
    s = setup
    await submit(s)
    record = journal(s.repo.bot)
    record["phase"] = "RESTARTING"
    s.repo.bot["status"] = "ACTIVE"
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    s.service.restart_bot.assert_not_called()
    assert journal(s.repo.bot)["phase"] == "RESTARTING"
    s.handler.clock = lambda: task(s).payload["started_at"] + BUSINESS_TIMEOUT
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    assert "超时" in journal(s.repo.bot)["error_message"]


@pytest.mark.asyncio
async def test_stable_backup_id_and_single_under_lock_mutation_fence(setup):
    s = setup
    await submit(s)
    state = RestartState(s.repo, "b", "o", task(s).payload["operation_id"])
    state.update({"QUEUED"}, phase="BACKING_UP")
    from agentclaw.community.core.bot_management.engines.aicoding.restart_state import (
        RestartExecution,
    )

    execution = RestartExecution(state, task(s).payload)
    receipt = Mock()
    context_reset_handle = current_restart.set(execution)
    try:
        with patch.object(
            s.strategy, "_prepare_restart", return_value=receipt
        ) as backup:
            verifier = s.strategy.prepare_restart(s.ctx, binding_id=7)
            assert backup.call_args.kwargs["operation_id"] == execution.operation_id
            assert journal(s.repo.bot)["phase"] == "BACKING_UP"
            verifier()
            receipt.assert_called_once()
            assert journal(s.repo.bot)["phase"] == "BACKING_UP"
            assert not execution.fenced
            s.strategy.before_restart_submission(s.ctx)
            assert receipt.call_count == 2
            assert journal(s.repo.bot)["phase"] == "RESTARTING"
            with pytest.raises(RestartSuperseded):
                s.strategy.before_restart_submission(s.ctx)
    finally:
        current_restart.reset(context_reset_handle)


@pytest.mark.asyncio
async def test_superseded_task_cannot_overwrite_new_operation(setup):
    s = setup
    await submit(s)
    old = task(s)
    old.status = TaskStatus.FAILED
    await submit(s)
    operation = journal(s.repo.bot)["operation_id"]
    assert operation != old.payload["operation_id"]
    assert isinstance(s.handler.handle(old.payload), Complete)
    assert journal(s.repo.bot)["operation_id"] == operation
    s.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
async def test_queue_terminal_status_is_not_inferred_by_bot_read(setup):
    s = setup
    await submit(s)
    task(s).status = TaskStatus.TIMED_OUT
    view = s.service.get_bot("b", "o")
    assert view["status"] == "PENDING"
    assert "start_message" not in view["ext"]


@pytest.mark.asyncio
async def test_browser_five_minute_limit_does_not_cancel_task(setup):
    s = setup
    await submit(s)
    task(s).payload["started_at"] -= 301
    mock_lifecycle(s)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "WAITING_READY"


@pytest.mark.asyncio
async def test_worker_restores_tenant_and_dedup_key_is_tenant_scoped(setup):
    s = setup
    with avernet_tenant_scope("external"):
        await submit(s)
        external_task = task(s)
        external_key = task_key("b", "o")
    assert task_key("b", "o") != external_key
    mock_lifecycle(s)
    s.repo.tenants.clear()
    assert isinstance(s.handler.handle(external_task.payload), Reschedule)
    assert set(s.repo.tenants) == {"external"}
    assert get_current_avernet_tenant() != "external"


@pytest.mark.parametrize("engine", ["openclaw", "teclaw", "hermes", "qoder", "unknown"])
@pytest.mark.asyncio
async def test_other_engines_never_use_queue_or_status_projection(setup, engine):
    s = setup
    s.repo.bot["active_engine"] = engine
    s.repo.bot["ext"][KEY] = {"phase": "FAILED", "error_message": "not theirs"}
    s.service.restart_bot.return_value = {"original": True}
    assert await submit(s) == {"original": True}
    assert not s.queue.tasks
    assert s.service.get_bot("b", "o")["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_handler_registered_at_bootstrap(setup):
    registry = HandlerRegistry()
    await AicodingRestartLifecycle(registry, setup.handler).bootstrap()
    assert registry.get(TASK_TYPE) is setup.handler
    assert registry.wakes_on_enqueue(TASK_TYPE)


def test_lifecycle_is_resolvable_in_real_di(test_injector):
    lifecycle = test_injector.get(AicodingRestartLifecycle)
    assert isinstance(lifecycle.handler, AicodingRestartHandler)


@pytest.mark.asyncio
async def test_status_http_response_keeps_existing_failure_fields(setup):
    import inspect
    from agentclaw.community.adapters.http.bot_management.router import get_bot_status

    s = setup
    await submit(s)
    mock_lifecycle(s, backup_error=TimeoutError())
    s.handler.handle(task(s).payload)
    response = await inspect.unwrap(get_bot_status)(
        bot_id="b",
        owner_id="o",
        ctx=SimpleNamespace(user_id="o"),
        bot_service=s.service,
    )
    assert response.success
    assert response.data["bot_status"] == "FAILED"
    assert response.data["is_ready"] is False
    assert "备份超时" in response.data["error_message"]


@pytest.mark.asyncio
async def test_same_binding_with_changed_device_is_rejected(setup):
    s = setup
    await submit(s)
    s.binding["device_id"] = "replacement"
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    s.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
async def test_old_failed_record_does_not_hide_new_binding(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s, backup_error=TimeoutError())
    s.handler.handle(task(s).payload)
    s.repo.bot.update(binding_id=99, status="ACTIVE")
    assert s.service.get_bot("b", "o")["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_lease_lost_delivery_keeps_live_operation_key(setup):
    s = setup
    await submit(s)
    record = journal(s.repo.bot)
    record["phase"] = "RESTARTING"
    state = RestartState(s.repo, "b", "o", record["operation_id"])
    assert isinstance(s.handler._superseded(state), Reschedule)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "RESTARTING"
    s.service.restart_bot.assert_not_called()


@pytest.mark.asyncio
async def test_publish_success_does_not_bypass_provider_finalization(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s)
    s.handler.handle(task(s).payload)
    s.repo.bot["status"] = "ACTIVE"
    s.progress.return_value = {"status": "SUCCESS"}
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "WAITING_READY"
    s.repo.bot["ext"]["restart_publish_id"] = "12"
    # Still waiting for the provider handler's intent cleanup/token refresh.
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    s.binding["device_props"]["restart_request_id"] = None
    assert isinstance(s.handler.handle(task(s).payload), Complete)


@pytest.mark.asyncio
async def test_retry_clears_existing_failure_fields_atomically(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s, backup_error=TimeoutError())
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    task(s).status = TaskStatus.FAILED
    await submit(s)
    assert s.repo.bot["status"] == "PENDING"
    assert "start_status" not in s.repo.bot["ext"]
    assert "start_message" not in s.repo.bot["ext"]
    assert s.repo.bot["ext"]["unrelated"] == {"keep": True}


@pytest.mark.asyncio
async def test_runtime_report_behavior_is_unchanged_without_read_projection(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s, backup_error=TimeoutError())
    s.handler.handle(task(s).payload)
    failure = s.repo.bot["ext"]["start_message"]
    s.repo.bot["ext"].update(start_status="SUCCEEDED", start_message="late report")
    view = s.service.get_bot("b", "o")
    assert view["status"] == "FAILED"
    assert view["ext"]["start_status"] == "SUCCEEDED"
    assert view["ext"]["start_message"] == "late report"
    assert journal(s.repo.bot)["error_message"] == failure
    assert "error_message" not in view


@pytest.mark.asyncio
async def test_target_changed_failure_does_not_mark_replacement_failed(setup):
    s = setup
    await submit(s)
    s.repo.bot.update(binding_id=99, status="ACTIVE")
    assert isinstance(s.handler.handle(task(s).payload), Fail)
    assert s.repo.bot["status"] == "ACTIVE"
    assert "start_message" not in s.repo.bot["ext"]
    assert s.service.get_bot("b", "o")["status"] == "ACTIVE"


@pytest.mark.parametrize("status", ["FAILED", "ACTIVE", "PENDING"])
@pytest.mark.asyncio
async def test_unbound_restart_preserves_original_lifecycle_input(setup, status):
    s = setup
    s.repo.bot.update(binding_id=None, status=status)
    s.service.restart_bot.side_effect = lambda **kwargs: {"original_status": s.repo.bot["status"]}
    assert await submit(s) == {"original_status": status}
    assert not s.queue.tasks
    assert KEY not in s.repo.bot["ext"]
    s.service.restart_bot.assert_called_once_with(bot_id="b", user_id="o")


@pytest.mark.asyncio
async def test_pending_binding_preserves_existing_activation_guard(setup):
    s = setup
    s.binding["status"] = "PENDING"
    s.service.restart_bot.return_value = {"activation_in_progress": True}
    assert await submit(s) == {"activation_in_progress": True}
    assert s.repo.bot["status"] == "ACTIVE"
    assert not s.queue.tasks


@pytest.mark.asyncio
async def test_old_provider_intent_is_not_a_new_restart_completion(setup):
    s = setup
    s.binding["device_props"] = {
        "restart_request_id": "old", "restart_publish_id": "10",
    }
    await submit(s)

    def ambiguous(**kwargs):
        current_restart.get().fence_mutation()
        raise ConnectionError("unknown submission result")

    s.service.restart_bot.side_effect = ambiguous
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "RESTARTING"
    assert "handoff" not in journal(s.repo.bot)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    s.service.restart_bot.assert_called_once()


@pytest.mark.asyncio
async def test_process_loss_after_mutation_is_not_replayed(setup):
    s = setup
    await submit(s)

    class ProcessLost(BaseException):
        pass

    def crash(**kwargs):
        current_restart.get().fence_mutation()
        raise ProcessLost()

    s.service.restart_bot.side_effect = crash
    with pytest.raises(ProcessLost):
        s.handler.handle(task(s).payload)
    assert current_restart.get() is None
    assert journal(s.repo.bot)["phase"] == "RESTARTING"
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    s.service.restart_bot.assert_called_once()


@pytest.mark.asyncio
async def test_reclaimed_submission_adopts_handoff_without_reissuing(setup):
    s = setup
    await submit(s)
    mock_lifecycle(s)
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    # Simulate process loss after POST/intent persistence, before handoff capture.
    record = s.repo.bot["ext"][KEY]
    record["phase"] = "RESTARTING"
    record.pop("handoff")
    assert isinstance(s.handler.handle(task(s).payload), Reschedule)
    assert journal(s.repo.bot)["phase"] == "WAITING_READY"
    assert journal(s.repo.bot)["handoff"]["publish_id"] == "12"
    s.service.restart_bot.assert_called_once()


@pytest.mark.parametrize("fenced", [False, True])
def test_submission_error_cleanup_never_clears_a_newer_operation(fenced):
    from agentclaw.community.core.bot_management.engines.aicoding.restart_baas import (
        AicodingSubmissionMixin,
    )
    import httpx

    request = httpx.Request("POST", "https://provider.invalid/update")
    error = httpx.HTTPStatusError("not found", request=request,
                                  response=httpx.Response(404, request=request))
    execution = SimpleNamespace(
        payload={"provider": "baas", "binding_id": 7}, fenced=fenced,
        operation_id="old", state=Mock(),
    )
    execution.state.read.return_value = {
        "binding_id": 7, "ext": {KEY: {"operation_id": "new", "phase": "BACKING_UP"}},
    }
    clear = Mock()
    context_reset_handle = current_restart.set(execution)
    try:
        with pytest.raises(RestartSuperseded):
            AicodingSubmissionMixin().on_restart_submission_error(None, error, clear_intent=clear)
        clear.assert_not_called()
    finally:
        current_restart.reset(context_reset_handle)
