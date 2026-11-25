"""Real journal/queue transactions, exercising the public recycle interface."""

import json
import time
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_management.repository.models import BotRestartLockModel
from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_dormant.activate_service import ActivateBotService, validate_activation
from agentclaw.community.core.bot_dormant.sqlite_models import DormantCheckAudit, DormantNotifyLog
from agentclaw.community.core.bot_management.engines.restart_contract import (
    RecycleUnavailable,
    journal,
)
from agentclaw.community.core.bot_dormant.recycle_task import (
    RecycleHandler,
    RecycleOperations,
)
from agentclaw.community.core.bot_management.engines.restart_contract import (
    RecycleBackupBlocked,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotNotFoundError,
)
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.implementations.bot.recycle import (
    RecycleRepository,
)
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.services.wakeup import WorkerWakeup
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry
from agentclaw.community.di.config import TaskQueueConfig
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def case(tmp_path, request):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'recycle.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            BotModel.__table__,
            EntityDeviceBinding.__table__,
            DormantCheckAudit.__table__,
            DormantNotifyLog.__table__,
            TaskQueueModel.__table__,
            BotRestartLockModel.__table__,
        ],
    )
    factory = sessionmaker(engine, expire_on_commit=False)

    class DB:
        @contextmanager
        def orm_session(self):
            with factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        @contextmanager
        def transactional_orm_session(self):
            # Same SQLite write-UoW semantics as LocalDatabasePlugin.
            with self.orm_session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                yield session

    db = DB()
    with db.orm_session() as session:
        binding = EntityDeviceBinding(
            entity_id="u1",
            entity_type="staff",
            device_id="d1",
            device_provider="arca",
            env=get_current_env(),
            device_props=json.dumps({"sandbox_id": "s1"}),
            status="ACTIVE",
            applied_by="u1",
        )
        session.add(binding)
        session.flush()
        session.add(
            BotModel(
                bot_id="b1",
                owner_id="u1",
                creator_id="u1",
                entity_id="u1",
                entity_type="staff",
                bot_type="personal",
                active_engine=getattr(request, "param", "claude_code"),
                status="ACTIVE",
                binding_id=binding.id,
                device_id="d1",
                ext=json.dumps({"other": "preserved"}),
                env=get_current_env(),
            )
        )
    config = TaskQueueConfig(app="agentclaw")

    def is_teclaw(engine):
        return str(engine or "").strip().lower() == "teclaw"

    store = RecycleRepository(
        db, config.app, validate_activation=lambda bot: validate_activation(bot, is_teclaw)
    )
    tracer = MagicMock()
    tracer.current_trace_id.return_value = None
    tracer.export_trace_carrier.return_value = None
    queue = TaskQueueService(
        TaskQueueRepository(db), HandlerRegistry(), WorkerWakeup(), config, tracer
    )
    target = {
        "binding_id": binding.id,
        "provider": "arca",
        "device_id": "d1",
        "sandbox_id": "s1",
        "bot_uuid": "d1",
    }
    runtime = MagicMock()
    runtime.target.return_value = target
    runtime.prepare.return_value = lambda: None
    runtime.submit_release.return_value = {"request_id": "release-1"}
    runtime.observe_release.return_value = "RELEASED"
    clock = [time.time()]
    service = RecycleOperations(
        store=store,
        runtime=runtime,
        queue=queue,
        guard=lambda bot: nullcontext(),
        app=config.app,
        worker_enabled=True,
        is_teclaw=is_teclaw,
        clock=lambda: clock[0],
    )
    passport = MagicMock()
    handler = RecycleHandler(service, passport, clock=lambda: clock[0])
    c = SimpleNamespace(
        db=db,
        store=store,
        service=service,
        handler=handler,
        runtime=runtime,
        passport=passport,
        clock=clock,
        queue=queue,
        target=target,
    )
    yield c
    engine.dispose()


def submit(c):
    result = c.service.submit(bot_id="b1", owner_id="u1", request_id="r1")
    return result, journal(c.store.read("b1", "u1"))


def test_sync_admission_skips_queue_after_existing_operation_checks(case):
    case.service.should_queue = lambda _bot: False

    result, record = submit(case)

    assert result["_synchronous"] is True
    assert result["operation_id"] is None
    assert result["phase"] is None
    assert record == {}
    with case.db.orm_session() as session:
        assert session.query(TaskQueueModel).count() == 0


def test_existing_async_operation_wins_over_sync_fallback(case):
    first, _ = submit(case)
    case.service.should_queue = lambda _bot: False

    second, record = submit(case)

    assert second["operation_id"] == first["operation_id"]
    assert second["phase"] == "QUEUED"
    assert second["changed"] is False
    assert record["phase"] == "QUEUED"
    with case.db.orm_session() as session:
        assert session.query(TaskQueueModel).count() == 1


def test_admission_is_durable_fast_and_deduplicated(case):
    first, record = submit(case)
    second, _ = submit(case)
    assert first["changed"] is True and second["changed"] is False
    assert first["operation_id"] == second["operation_id"] == record["operation_id"]
    assert record["phase"] == "QUEUED"
    case.runtime.prepare.assert_not_called()
    case.runtime.submit_release.assert_not_called()
    with case.db.orm_session() as session:
        assert session.query(TaskQueueModel).count() == 1


def test_complete_and_redeliver_commits_once(case):
    result, payload = submit(case)
    assert isinstance(case.handler.handle(payload), Complete)
    assert isinstance(case.handler.handle(payload), Complete)
    bot = case.store.read("b1", "u1")
    assert bot["status"] == "RECYCLED" and bot["binding_id"] is None
    assert bot["ext"]["other"] == "preserved"
    assert journal(bot)["phase"] == "SUCCEEDED"
    with case.db.orm_session() as session:
        audits = session.query(DormantCheckAudit).all()
        assert len(audits) == 1 and audits[0].source == "openapi"
        assert audits[0].run_id == result["operation_id"]
        assert session.query(EntityDeviceBinding).one().status == "RELEASED"
    case.runtime.submit_release.assert_called_once()
    case.passport.freeze_agent_passport.assert_called_once()


@pytest.mark.parametrize(
    "reason,code", [("backup_failed", "BACKUP_FAILED"), ("timeout", "BACKUP_TIMEOUT")]
)
def test_backup_veto_never_marks_bot_failed_or_releases(case, reason, code):
    _, payload = submit(case)
    case.runtime.prepare.side_effect = RecycleBackupBlocked(reason)
    assert isinstance(case.handler.handle(payload), Fail)
    bot = case.store.read("b1", "u1")
    assert bot["status"] == "ACTIVE" and bot["binding_id"] == case.target["binding_id"]
    assert journal(bot)["phase"] == "FAILED" and journal(bot)["error_code"] == code
    assert "start_status" not in bot["ext"]
    case.runtime.submit_release.assert_not_called()
    case.passport.freeze_agent_passport.assert_not_called()


@pytest.mark.parametrize("source", ["openapi", "manual_ops"])
def test_release_failure_keeps_bot_and_binding_without_replaying(case, source):
    result = case.service.submit(bot_id="b1", owner_id="u1", source=source)
    payload = journal(case.store.read("b1", "u1"))
    case.runtime.submit_release.side_effect = TimeoutError("secret must not be exposed")
    assert isinstance(case.handler.handle(payload), Fail)
    assert isinstance(case.handler.handle(payload), Fail)
    public = case.service.get_operation(
        bot_id="b1", owner_id="u1", operation_id=result["operation_id"],
    )
    assert public["phase"] == "FAILED" and public["error_code"] == "RELEASE_FAILED"
    assert public["bot_status"] == "ACTIVE" and "secret" not in str(public)
    assert case.store.read("b1", "u1")["binding_id"] == case.target["binding_id"]
    with case.db.orm_session() as session:
        assert session.query(EntityDeviceBinding).one().status == "ACTIVE"
        assert session.query(DormantCheckAudit).count() == 0
        assert session.query(DormantNotifyLog).count() == 0
    case.runtime.submit_release.assert_called_once()
    case.runtime.observe_release.assert_not_called()
    case.passport.freeze_agent_passport.assert_not_called()


@pytest.mark.parametrize("source", ["openapi", "manual_ops"])
def test_successful_recycle_does_not_wait_for_physical_status(case, source):
    result = case.service.submit(bot_id="b1", owner_id="u1", source=source)
    payload = journal(case.store.read("b1", "u1"))
    case.runtime.observe_release.side_effect = RuntimeError("physical status unavailable")

    assert isinstance(case.handler.handle(payload), Complete)
    public = case.service.get_operation(
        bot_id="b1", owner_id="u1", operation_id=result["operation_id"],
    )
    assert public["phase"] == "SUCCEEDED" and public["bot_status"] == "RECYCLED"
    assert case.store.read("b1", "u1")["binding_id"] is None
    with case.db.orm_session() as session:
        assert session.query(EntityDeviceBinding).one().status == "RELEASED"
        assert session.query(DormantCheckAudit).count() == 1
        assert session.query(DormantNotifyLog).count() == (source == "manual_ops")
    case.runtime.submit_release.assert_called_once()
    case.runtime.observe_release.assert_not_called()


def test_same_binding_retargeted_during_backup_is_never_destroyed(case):
    _, payload = submit(case)

    def changed(*args):
        with case.db.orm_session() as session:
            row = session.query(EntityDeviceBinding).one()
            row.device_id = "replacement"
        return lambda: None

    case.runtime.prepare.side_effect = changed
    case.handler.handle(payload)
    bot = case.store.read("b1", "u1")
    assert journal(bot)["phase"] == "FAILED" and bot["status"] == "ACTIVE"
    case.runtime.submit_release.assert_not_called()


def test_audit_commit_failure_rolls_back_and_only_retries_completion(case):
    _, payload = submit(case)

    def fail(*args):
        raise RuntimeError("database failure")

    event.listen(DormantCheckAudit, "before_insert", fail)
    try:
        assert isinstance(case.handler.handle(payload), Retry)
        bot = case.store.read("b1", "u1")
        assert bot["status"] == "ACTIVE" and journal(bot)["phase"] == "RELEASING"
        assert journal(bot)["freeze_done"] is True
        with case.db.orm_session() as session:
            assert session.query(EntityDeviceBinding).one().status == "ACTIVE"
    finally:
        event.remove(DormantCheckAudit, "before_insert", fail)
    assert isinstance(case.handler.handle(payload), Complete)
    case.runtime.submit_release.assert_called_once()
    case.passport.freeze_agent_passport.assert_called_once()
    with case.db.orm_session() as session:
        assert session.query(DormantCheckAudit).count() == 1


def test_query_checks_operation_and_tenant_and_is_read_only(case):
    result, _ = submit(case)
    before = case.store.read("b1", "u1")
    public = case.service.get_operation(
        bot_id="b1", owner_id="u1", operation_id=result["operation_id"]
    )
    assert public["phase"] == "QUEUED" and "target" not in public
    assert case.store.read("b1", "u1") == before
    with pytest.raises(BotNotFoundError):
        case.service.get_operation(bot_id="b1", owner_id="u1", operation_id="other")
    with avernet_tenant_scope("another-tenant"), pytest.raises(BotNotFoundError):
        case.service.get_operation(
            bot_id="b1", owner_id="u1", operation_id=result["operation_id"]
        )


def test_activate_rejects_queued_recycle_before_active_idempotency(case):
    submit(case)
    bots = MagicMock()
    bots.get_bot.return_value = case.store.read("b1", "u1")
    bots.is_teclaw_bot.return_value = False
    svc = ActivateBotService(bots, case.passport, activation_control=case.store)
    with pytest.raises(BotInvalidLifecycleStateError):
        svc.activate(bot_id="b1", owner_id="u1")
    bots.start_bot.assert_not_called()


def test_activation_claim_supports_legacy_and_deduplicates(case):
    with case.db.orm_session() as session:
        row = session.query(BotModel).one()
        row.status, row.binding_id, row.device_id = "RECYCLED", None, None
    assert case.store.claim_activation("b1", "u1") == ("REACTIVATING", True)
    assert case.store.claim_activation("b1", "u1") == ("REACTIVATING", False)


def test_worker_disabled_refuses_to_accept(case):
    case.service.worker_enabled = False
    with pytest.raises(RecycleUnavailable):
        submit(case)
    with case.db.orm_session() as session:
        assert session.query(TaskQueueModel).count() == 0


def test_enqueue_before_journal_crash_is_repaired_without_duplicate_task(
    case, monkeypatch
):
    original = case.store.ensure
    call = [0]

    def crash_once(*args):
        call[0] += 1
        if call[0] == 1:
            raise RuntimeError("crash after queue commit")
        return original(*args)

    monkeypatch.setattr(case.store, "ensure", crash_once)
    with pytest.raises(RecycleUnavailable):
        submit(case)
    result, record = submit(case)
    assert result["changed"] is False and record["phase"] == "QUEUED"
    with case.db.orm_session() as session:
        assert session.query(TaskQueueModel).count() == 1


def test_lost_finalizer_fails_task_without_changing_bot_or_repeating_freeze(case):
    _, payload = submit(case)
    case.store.update(
        payload,
        {"QUEUED"},
        phase="RELEASING",
        release_started_at=case.clock[0],
        finalizer="previous-process",
        release_confirmed=True,
    )
    assert isinstance(case.handler.handle(payload), Reschedule)
    case.clock[0] += 601
    assert isinstance(case.handler.handle(payload), Fail)
    bot = case.store.read("b1", "u1")
    assert journal(bot)["phase"] == "FAILED" and bot["status"] == "ACTIVE"
    assert journal(bot)["error_code"] == "FINALIZATION_FAILED"
    case.passport.freeze_agent_passport.assert_not_called()
    assert case.store.claim_activation("b1", "u1") == ("ACTIVE", False)


def test_passport_failure_is_warning_not_failed_recycle(case):
    _, payload = submit(case)
    case.passport.freeze_agent_passport.side_effect = RuntimeError("secret")
    assert isinstance(case.handler.handle(payload), Complete)
    result = case.service.get_operation(
        bot_id="b1", owner_id="u1", operation_id=payload["operation_id"]
    )
    assert result["phase"] == "SUCCEEDED" and result["bot_status"] == "RECYCLED"
    assert result["warning"]["code"] == "PASSPORT_FREEZE_FAILED"
    assert "secret" not in str(result)


def test_old_activation_rollback_cannot_freeze_a_new_binding(case):
    with case.db.orm_session() as session:
        row = session.query(BotModel).one()
        row.status = "REACTIVATING"  # but a replacement binding has been assigned
    with case.store.activation_rollback("b1", "u1") as allowed:
        assert allowed is False
    assert case.store.read("b1", "u1")["status"] == "REACTIVATING"


def test_terminal_queue_with_stale_releasing_journal_is_not_reenqueued(case):
    _, payload = submit(case)
    case.store.update(
        payload, {"QUEUED"}, phase="RELEASING", release_started_at=case.clock[0]
    )
    with case.db.orm_session() as session:
        row = session.query(TaskQueueModel).one()
        row.status, row.active_idempotency_key = "TIMED_OUT", None
    result = case.service.submit(bot_id="b1", owner_id="u1")
    assert result["phase"] == "FAILED" and result["changed"] is False
    with case.db.orm_session() as session:
        assert session.query(TaskQueueModel).count() == 1


def test_success_record_is_preserved_by_activation(case):
    _, payload = submit(case)
    case.handler.handle(payload)
    assert case.store.claim_activation("b1", "u1") == ("REACTIVATING", True)
    result = case.service.get_operation(
        bot_id="b1", owner_id="u1", operation_id=payload["operation_id"]
    )
    assert result["phase"] == "SUCCEEDED" and result["bot_status"] == "REACTIVATING"


def test_activation_thread_start_failure_releases_claim(case, monkeypatch):
    with case.db.orm_session() as session:
        row = session.query(BotModel).one()
        row.status, row.binding_id, row.device_id = "RECYCLED", None, None
    bots = MagicMock()
    bots.get_bot.return_value = case.store.read("b1", "u1")
    bots.is_teclaw_bot.return_value = False
    service = ActivateBotService(bots, case.passport, activation_control=case.store)
    monkeypatch.setattr(
        "threading.Thread.start", MagicMock(side_effect=RuntimeError("no thread"))
    )
    with pytest.raises(RuntimeError, match="no thread"):
        service.activate(bot_id="b1", owner_id="u1")
    assert case.store.read("b1", "u1")["status"] == "RECYCLED"
    case.passport.unfreeze_agent_passport.assert_not_called()


def test_engine_changed_after_admission_fails_operation_not_bot(case):
    _, payload = submit(case)
    with case.db.orm_session() as session:
        session.query(BotModel).one().active_engine = "teclaw"
    assert isinstance(case.handler.handle(payload), Complete)
    bot = case.store.read("b1", "u1")
    assert bot["status"] == "ACTIVE" and journal(bot)["error_code"] == "TARGET_CHANGED"
    case.runtime.prepare.assert_not_called()
    case.runtime.submit_release.assert_not_called()


def test_redelivery_cannot_reset_backup_policy_budget_or_target_evidence(case):
    _, payload = submit(case)
    first = case.store.update(
        payload,
        {"QUEUED"},
        phase="BACKING_UP",
        backup_started_at=1,
        fail_block=True,
        fastdisk_targets=["s1"],
        physical_targets=["p1"],
    )
    second = case.store.update(
        payload,
        {"BACKING_UP"},
        backup_started_at=999,
        fail_block=False,
        fastdisk_targets=["s2"],
    )
    assert second["backup_started_at"] == first["backup_started_at"] == 1
    assert second["fail_block"] is True
    assert second["fastdisk_targets"] == ["s1", "s2"]
    from agentclaw.community.core.bot_management.engines.restart_contract import (
        RecycleTargetChanged,
    )

    with pytest.raises(RecycleTargetChanged):
        case.store.update(payload, {"BACKING_UP"}, physical_targets=["p2"])


def test_activation_uses_atomic_claim_not_stale_active_read(case, monkeypatch):
    stale = case.store.read("b1", "u1")
    with case.db.orm_session() as session:
        row = session.query(BotModel).one()
        row.status, row.binding_id, row.device_id = "RECYCLED", None, None
    bots = MagicMock()
    bots.get_bot.return_value = stale
    bots.is_teclaw_bot.return_value = False
    thread = MagicMock()
    monkeypatch.setattr("threading.Thread", thread)
    result = ActivateBotService(
        bots, case.passport, activation_control=case.store
    ).activate(bot_id="b1", owner_id="u1")
    assert result.status == "REACTIVATING" and result.changed is True
    thread.return_value.start.assert_called_once()


def test_concurrent_deliveries_share_one_destroy_freeze_and_audit(case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, payload = submit(case)
    ready = Barrier(6)

    def prepare(*_):
        ready.wait(timeout=5)
        return None

    case.runtime.prepare.side_effect = prepare
    with ThreadPoolExecutor(max_workers=6) as workers:
        outcomes = list(workers.map(lambda _: case.handler.handle(payload), range(6)))
    assert all(isinstance(outcome, (Complete, Reschedule)) for outcome in outcomes)
    assert isinstance(case.handler.handle(payload), Complete)
    assert journal(case.store.read("b1", "u1"))["phase"] == "SUCCEEDED"
    case.runtime.submit_release.assert_called_once()
    case.passport.freeze_agent_passport.assert_called_once()
    with case.db.orm_session() as session:
        assert session.query(DormantCheckAudit).count() == 1


def test_already_recycled_is_200_shape_even_before_queue_completion(case):
    _, payload = submit(case)
    assert isinstance(case.handler.handle(payload), Complete)
    result = case.service.submit(bot_id="b1", owner_id="u1")
    assert result["status"] == "RECYCLED" and result["changed"] is False
    assert result["operation_id"] is None
    case.runtime.submit_release.assert_called_once()
    with case.db.orm_session() as session:
        assert session.query(DormantCheckAudit).count() == 1


def test_canonical_external_engine_policy_guards_admission_and_activation(case):
    from agentclaw.community.core.bot_management.services.bot_service import (
        BotOperationNotAllowedError,
    )

    case.service.is_teclaw = lambda _: True
    with pytest.raises(BotOperationNotAllowedError):
        case.service.submit(bot_id="b1", owner_id="u1")
    case.runtime.submit_release.assert_not_called()
    with case.db.orm_session() as session:
        row = session.query(BotModel).one()
        row.status, row.binding_id, row.device_id = "RECYCLED", None, None
    case.store.validate_activation = lambda bot: validate_activation(bot, lambda _: True)
    with pytest.raises(BotOperationNotAllowedError):
        case.store.claim_activation("b1", "u1")
    assert case.store.read("b1", "u1")["status"] == "RECYCLED"


@pytest.fixture
def deletion(case):
    from agentclaw.community.core.repository.implementations.bot.restart_lock import (
        BotRestartLockRepository,
    )
    from agentclaw.community.di.config import TaskQueueWorkerConfig
    from agentclaw.community.di.modules.bot_dormant_module import BotDormantModule
    from tests.community.core.bot_management.services.test_bot_service_misc import _make_service

    locks = BotRestartLockRepository(case.db)
    service = _make_service()
    service._restart_lock_repo = locks
    service._repository.get_by_id_and_owner.side_effect = case.store.read
    device = MagicMock()
    device.get_device.return_value = SimpleNamespace(status="ACTIVE")
    service._device_service_provider = lambda: device
    grants = MagicMock()
    grants.revoke_all_for_bot.return_value = 0
    service._bot_app_grant_provider = lambda: grants

    def delete_row(*_):
        with case.db.orm_session() as session:
            session.query(BotModel).one().is_delete = 1
        return True

    service._repository.soft_delete_by_owner.side_effect = delete_row
    assembled = BotDormantModule().recycle_operations(
        case.store, MagicMock(), case.queue, locks, MagicMock(),
        TaskQueueConfig(app="agentclaw"), TaskQueueWorkerConfig(enabled=True),
        MagicMock(), lambda: MagicMock(),
    )
    case.service.guard = assembled.guard
    return SimpleNamespace(service=service, device=device, grants=grants, locks=locks)


def test_delete_holds_admission_lock_through_destructive_effects(case, deletion):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant

    entered, resume = Event(), Event()

    def withdraw(**_):
        entered.set()
        assert resume.wait(5), "delete was not resumed"
        return 0

    deletion.grants.revoke_all_for_bot.side_effect = withdraw
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bind_current_avernet_tenant(deletion.service.delete_bot), "b1", "u1")
        try:
            assert entered.wait(5), "delete did not reach grant withdrawal"
            with pytest.raises(BotInvalidLifecycleStateError):
                submit(case)
            assert journal(case.store.read("b1", "u1")) == {}
            case.runtime.submit_release.assert_not_called()
        finally:
            resume.set()
        assert future.result(timeout=5) is True
    deletion.device.release_device.assert_called_once()
    deletion.service._passport_plugin.destroy_passport.assert_called_once()
    with case.db.orm_session() as session:
        assert session.query(BotModel).one().is_delete == 1
        assert session.query(TaskQueueModel).count() == 0
        assert session.query(BotRestartLockModel).count() == 0


def test_admitted_recycle_blocks_delete_before_any_side_effect(case, deletion):
    submit(case)
    with pytest.raises(BotInvalidLifecycleStateError):
        deletion.service.delete_bot("b1", "u1")
    deletion.grants.revoke_all_for_bot.assert_not_called()
    deletion.device.release_device.assert_not_called()
    deletion.service._passport_plugin.destroy_passport.assert_not_called()
    deletion.service._repository.soft_delete_by_owner.assert_not_called()
    assert journal(case.store.read("b1", "u1"))["phase"] == "QUEUED"


@pytest.mark.parametrize("source", ["openapi", "manual_ops"])
def test_nonblocking_backup_warning_continues_original_recycle(case, source):
    result = case.service.submit(bot_id="b1", owner_id="u1", source=source)
    payload = journal(case.store.read("b1", "u1"))

    def prepare(bot, record, save):
        save(warning={"code": "BACKUP_FAILED", "message": "备份失败，按开关继续回收"})
        return lambda: None

    case.runtime.prepare.side_effect = prepare
    assert isinstance(case.handler.handle(payload), Complete)
    public = case.service.get_operation(
        bot_id="b1", owner_id="u1", operation_id=result["operation_id"],
    )
    assert public["phase"] == "SUCCEEDED" and public["bot_status"] == "RECYCLED"
    assert public["warning"]["code"] == "BACKUP_FAILED"
    case.runtime.submit_release.assert_called_once()
    case.runtime.observe_release.assert_not_called()


@pytest.mark.parametrize("change", ["type", "engine", "recycle", "status"])
def test_activation_rechecks_domain_admission_on_locked_row(case, monkeypatch, change):
    stale = case.store.read("b1", "u1")
    with case.db.orm_session() as session:
        row = session.query(BotModel).one()
        row.status, row.binding_id, row.device_id = "RECYCLED", None, None
        if change == "type":
            row.bot_type = "service"
        elif change == "engine":
            row.active_engine = "teclaw"
        elif change == "recycle":
            row.ext = json.dumps({"recycle": {"phase": "NEEDS_CONFIRMATION"}})
        else:
            row.status = "FAILED"
    bots = MagicMock()
    bots.get_bot.return_value = stale
    bots.is_teclaw_bot.return_value = False
    thread = MagicMock()
    monkeypatch.setattr("threading.Thread", thread)
    from agentclaw.community.core.bot_management.services.bot_service import BotOperationNotAllowedError
    with pytest.raises((BotOperationNotAllowedError, BotInvalidLifecycleStateError)):
        ActivateBotService(bots, case.passport, activation_control=case.store).activate(bot_id="b1", owner_id="u1")
    thread.assert_not_called()
    case.passport.unfreeze_agent_passport.assert_not_called()
    assert case.store.read("b1", "u1")["status"] != "REACTIVATING"


@pytest.mark.parametrize("case", ["openclaw", "hermes", "aicoding", "claude_code"], indirect=True)
def test_all_personal_engines_share_recycle_query_and_activation(case):
    result, payload = submit(case)
    assert result["phase"] == "QUEUED" and result["operation_id"]
    assert isinstance(case.handler.handle(payload), Complete)
    public = case.service.get_operation(bot_id="b1", owner_id="u1", operation_id=result["operation_id"])
    assert public["phase"] == "SUCCEEDED" and public["bot_status"] == "RECYCLED"
    assert case.store.claim_activation("b1", "u1") == ("REACTIVATING", True)
    assert case.store.claim_activation("b1", "u1") == ("REACTIVATING", False)
    assert isinstance(case.handler.handle(payload), Complete)
    case.runtime.submit_release.assert_called_once()
    with case.db.orm_session() as session:
        assert session.query(DormantCheckAudit).count() == 1


def test_manual_admission_keeps_first_metadata_and_notifies_only_on_completion(case):
    first = case.service.submit(bot_id="b1", owner_id="u1", source="manual_ops", reason="first reason")
    duplicate = case.service.submit(bot_id="b1", owner_id="u1", reason="other entry")
    assert duplicate["operation_id"] == first["operation_id"]
    assert duplicate["changed"] is False
    payload = journal(case.store.read("b1", "u1"))
    assert payload["source"] == "manual_ops" and payload["reason"] == "first reason"
    with case.db.orm_session() as session:
        assert session.query(DormantCheckAudit).count() == 0
        assert session.query(DormantNotifyLog).count() == 0
    assert isinstance(case.handler.handle(payload), Complete)
    assert isinstance(case.handler.handle(payload), Complete)
    with case.db.orm_session() as session:
        audit = session.query(DormantCheckAudit).one()
        assert audit.source == "manual_ops" and audit.check_result == "inactive"
        assert audit.run_id == first["operation_id"]
        notice = session.query(DormantNotifyLog).one()
        assert notice.send_status == "pending" and notice.dry_run == 0
        assert notice.notify_source == "internal_scan"
        assert notice.entity_id == notice.owner_id == "u1"
        assert "Bot 已回收" in notice.content
        assert session.query(EntityDeviceBinding).one().release_reason == "dormant_recycle"


@pytest.mark.parametrize("status,dry_run", [("pending", 1), ("cancelled", 0), ("sent", 0), ("failed", 0)])
def test_manual_completion_preserves_daily_notification_suppression(case, status, dry_run):
    from datetime import date
    with case.db.orm_session() as session:
        session.add(DormantNotifyLog(bot_id="b1", owner_id="u1", notify_type="recycle",
                    dt=date.today().strftime("%Y%m%d"), send_status=status, dry_run=dry_run, content="old"))
    case.service.submit(bot_id="b1", owner_id="u1", source="manual_ops")
    payload = journal(case.store.read("b1", "u1"))
    assert isinstance(case.handler.handle(payload), Complete)
    bot = case.store.read("b1", "u1")
    assert bot["status"] == "RECYCLED"
    assert journal(bot)["notification_enqueued"] is False
    with case.db.orm_session() as session:
        assert session.query(DormantCheckAudit).count() == 1
        notice = session.query(DormantNotifyLog).one()
        assert notice.send_status == status and notice.content == "old" and notice.dry_run == dry_run


def test_manual_notification_write_failure_rolls_back_entire_completion(case):
    case.service.submit(bot_id="b1", owner_id="u1", source="manual_ops")
    payload = journal(case.store.read("b1", "u1"))
    def fail(*args):
        raise RuntimeError("notification database failure")
    event.listen(DormantNotifyLog, "before_insert", fail)
    try:
        assert isinstance(case.handler.handle(payload), Retry)
        bot = case.store.read("b1", "u1")
        assert bot["status"] == "ACTIVE" and bot["binding_id"] is not None
        assert journal(bot)["phase"] == "RELEASING"
        with case.db.orm_session() as session:
            assert session.query(DormantCheckAudit).count() == 0
            assert session.query(EntityDeviceBinding).one().status == "ACTIVE"
    finally:
        event.remove(DormantNotifyLog, "before_insert", fail)
    assert isinstance(case.handler.handle(payload), Complete)
    case.runtime.submit_release.assert_called_once()


def test_manual_backup_failure_never_enqueues_success(case):
    case.service.submit(bot_id="b1", owner_id="u1", source="manual_ops")
    payload = journal(case.store.read("b1", "u1"))
    case.runtime.prepare.side_effect = RecycleBackupBlocked("backup_failed")
    assert isinstance(case.handler.handle(payload), Fail)
    with case.db.orm_session() as session:
        assert session.query(DormantNotifyLog).count() == 0
        assert session.query(DormantCheckAudit).count() == 0
    case.runtime.submit_release.assert_not_called()


def test_openapi_first_duplicate_does_not_gain_manual_notification(case):
    first, payload = submit(case)
    duplicate = case.service.submit(bot_id="b1", owner_id="u1", source="manual_ops", reason="later")
    assert duplicate["operation_id"] == first["operation_id"]
    assert isinstance(case.handler.handle(payload), Complete)
    with case.db.orm_session() as session:
        assert session.query(DormantNotifyLog).count() == 0
        assert session.query(DormantCheckAudit).one().source == "openapi"
