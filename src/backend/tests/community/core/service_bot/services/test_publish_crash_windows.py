"""Crash-window tests for the publish operation pipeline (#197).

Each test drives an operation against a REAL operation-ledger repository (SQLite)
plus a scripted fake BaaS, simulates a crash between two steps (by interrupting a
real seam — the ``issue`` callable or ``record_workflow`` — via ``_crash_before_record``
or a one-shot side effect), re-runs, and asserts convergence: the
BaaS mutation is issued the expected number of times, the workflow id lands in the
ledger, and follow-up steps (binding) are not duplicated.

Group B lands the release-leg cases (first release); later groups extend this file
per operation.
"""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.service_bot.repository.models import (  # noqa: F401
    BotPublishRecord,
    PublishOperationModel,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.plugins.publish_operation_repository import (
    OrmPublishOperationRepository as PublishOperationRepository,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    operation_request_id,
    to_baas_request_id,
)
from agentclaw.community.core.service_bot.types import PublishStage


# ── ledger + fakes ────────────────────────────────────────────────────────────
class _DB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _ledger():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return PublishOperationRepository(_DB(engine))


def _crash_before_record(ledger):
    """Simulate a crash in the window AFTER the BaaS workflow is issued but BEFORE
    its id is recorded in the ledger.

    Interrupts the real seam — the ledger's ``record_workflow`` raises on its first
    call, then behaves normally on resume — instead of a production hook: ``issue``
    has already created the BaaS workflow (an in-doubt orphan), and the process
    dies before the ledger write. On resume the op is still PENDING, so an existing
    bot adopts the in-doubt workflow and a creation re-issues (accepted orphan)."""
    real = ledger.record_workflow
    state = {"crashed": False}

    def wrapper(*args, **kwargs):
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("pod died after issue, before record")
        return real(*args, **kwargs)

    ledger.record_workflow = wrapper


class FakeBaas:
    """Per-bot workflow list; ``issue`` appends a new workflow (simulating BaaS)."""

    def __init__(self):
        self.workflows = {}
        self._next = 900
        self.resolve_container_provider = Mock(return_value="arca")

    def list_bot_publishes(self, bot_uuid):
        return [dict(w) for w in self.workflows.get(bot_uuid, [])]

    def issue(self, bot_uuid, publish_type="CREATE"):
        self._next += 1
        wid = self._next
        self.workflows.setdefault(bot_uuid, []).append(
            {"id": wid, "publish_type": publish_type, "status": "ACTIVE", "gmt_create": "t"}
        )
        return wid


def _record(status):
    return BotPublishRecord(
        id=1, source_bot_pk=11, source_bot_id="bot-source", publish_bot_id="pub-1",
        name="demo", owner_id="u1", permission_owner="u1", version=1, env="dev",
        status=status, ext={"migration_path": "/m", "config_artifact": None},
        gmt_create=datetime.now(), gmt_modified=datetime.now(),
    )


def _arca_router(build_service):
    from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
        ArcaSnapshotProducer,
    )
    from agentclaw.community.core.service_bot.services.deploy.producer import (
        DeployArtifactProducerRouter,
    )
    arca = ArcaSnapshotProducer(build_service)
    return DeployArtifactProducerRouter(
        providers={"arca": arca, "baas": arca}, default_provider_key="baas"
    )


def _flow(*, ledger, baas, build_service, publish_service=None):
    reader = Mock()
    reader.overrides_for_stage.return_value = {}
    svc = PublishFlowService(
        publish_service or Mock(),
        build_service,
        baas,
        Mock(),  # bot_service
        _arca_router(build_service),
        Mock(),  # common_config_service
        resolver=Mock(),
        device_fs_dispatcher=Mock(),
        teclaw_file_promotion=Mock(),
        device_binding_repo=Mock(),
        channel_overrides_reader=reader,
        task_queue_service=Mock(),
        publish_operation_repo=ledger,
    )
    # ARCA delivery (no config_artifact) → compose_live no-ops to (delivery, None).
    svc._ext_state.owner_id = Mock(return_value="u1")
    return svc


# ── first release: crash windows ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_first_release_crash_before_issue_issues_once_on_resume():
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    crashed = []

    async def release_async(**kw):
        # Crash in the pre-record window: the first attempt dies before the BaaS
        # create lands (no workflow created), so a resume must issue exactly once.
        if not crashed:
            crashed.append(1)
            raise RuntimeError("pod died before issue")
        wid = baas.issue("BOT-new", "CREATE")
        return {"bot_uuid": "BOT-new", "publish_id": wid}

    build_service.release_async = AsyncMock(side_effect=release_async)
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service)
    svc.create_release_binding = Mock(return_value=55)
    svc.record_release_ext = Mock()

    record = _record(PublishStatus.BUILT.value)
    with pytest.raises(RuntimeError):
        await svc._execute_verify_first_release(
            publish_record=record, operator="op", migration_path="/m",
            bot={"bot_id": "b2", "owner_id": "u1"},
        )
    # The create never landed → no BaaS workflow exists for the new bot.
    assert baas.list_bot_publishes("BOT-new") == []

    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )
    # Resume issued exactly one durable workflow (no duplicate); binding created once.
    assert len(baas.list_bot_publishes("BOT-new")) == 1
    svc.create_release_binding.assert_called_once()


@pytest.mark.asyncio
async def test_first_release_crash_after_binding_reuses_binding_on_resume():
    # Crash after the workflow is recorded AND the binding is created (recorded in
    # op.result) but before the ext write. Resume must not re-issue or re-bind.
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def release_async(**kw):
        wid = baas.issue("BOT-new", "CREATE")
        return {"bot_uuid": "BOT-new", "publish_id": wid}

    build_service.release_async = AsyncMock(side_effect=release_async)
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service)
    svc.create_release_binding = Mock(return_value=55)

    ext_calls = []

    def record_ext(**kw):
        ext_calls.append(kw)
        if len(ext_calls) == 1:
            raise RuntimeError("crash after binding, before ext")

    svc.record_release_ext = Mock(side_effect=record_ext)

    record = _record(PublishStatus.BUILT.value)
    with pytest.raises(RuntimeError):
        await svc._execute_verify_first_release(
            publish_record=record, operator="op", migration_path="/m",
            bot={"bot_id": "b2", "owner_id": "u1"},
        )

    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    assert build_service.release_async.await_count == 1  # workflow reused (id in ledger)
    assert svc.create_release_binding.call_count == 1    # binding reused via op.result
    assert len(ext_calls) == 2                           # ext retried and succeeded

    # The op converged to COMPLETED with the single workflow id recorded.
    op = ledger.get_latest_by_kind(1, "first_release", "verify")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901
    assert op.result["binding_id"] == 55


@pytest.mark.asyncio
async def test_first_release_creation_orphan_window_reissues_and_is_visible():
    # A creation has no bot to adopt against, so a crash AFTER the BaaS create but
    # BEFORE the workflow id is recorded is the accepted bounded orphan: the re-run
    # re-issues (a second bot), and the ledger op records only the second id. The
    # in-flight op (PENDING) is what makes the orphan window observable.
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def release_async(**kw):
        wid = baas.issue("BOT-new", "CREATE")
        return {"bot_uuid": "BOT-new", "publish_id": wid}

    build_service.release_async = AsyncMock(side_effect=release_async)
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service)
    svc.create_release_binding = Mock(return_value=55)
    svc.record_release_ext = Mock()

    _crash_before_record(ledger)

    record = _record(PublishStatus.BUILT.value)
    with pytest.raises(RuntimeError):
        await svc._execute_verify_first_release(
            publish_record=record, operator="op", migration_path="/m",
            bot={"bot_id": "b2", "owner_id": "u1"},
        )
    # One bot already created on BaaS but not recorded → the orphan.
    assert build_service.release_async.await_count == 1
    op = ledger.get_latest_by_kind(1, "first_release", "verify")
    assert op.state == PublishOperationState.PENDING.value  # in-flight → observable

    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )
    # Re-issued (accepted creation orphan); the op now records the second workflow.
    assert build_service.release_async.await_count == 2
    op = ledger.get_latest_by_kind(1, "first_release", "verify")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 902  # the second (recorded) workflow


# ── upgrade release: crash windows ────────────────────────────────────────────
def _upgrade_flow(ledger, baas, build_service):
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service)
    # An upgrade reuses an existing binding + reads/writes ext; stub those so the
    # test focuses on issuance idempotency.
    svc._ext_state.get_latest_ext = Mock(return_value={})
    svc._ext_state.update_status = Mock()
    svc.refresh_publish_handle = Mock()
    return svc


async def _run_online_upgrade(svc, record, bot_uuid="BOT-live"):
    # Resolve _execute_upgrade_release's last-publish/binding lookups to bot_uuid.
    from unittest.mock import Mock as _M
    last = _record(PublishStatus.SUCCESS.value)
    last.ext = {"binding": {"online": 88}}
    svc._publish_service.get_publish_by_id = _M(return_value=last)
    svc._publish_service.get_device_binding_by_id = _M(return_value=_M(device_id=bot_uuid))
    svc._bot_service.get_bot = _M(return_value={"bot_id": "b2", "entity_id": "u1"})
    return await svc._execute_upgrade_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "entity_id": "u1"},
    )


@pytest.mark.asyncio
async def test_upgrade_crash_after_issue_resume_adopts_not_reissues():
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def upgrade_async(**kw):
        wid = baas.issue("BOT-live", "UPDATE")
        return {"publish_id": wid, "success": True}

    build_service.upgrade_async = AsyncMock(side_effect=upgrade_async)
    svc = _upgrade_flow(ledger, baas, build_service)

    _crash_before_record(ledger)

    record = _record(PublishStatus.VALIDATING.value)
    record.last_pub_id = 10
    with pytest.raises(RuntimeError):
        await _run_online_upgrade(svc, record)
    assert build_service.upgrade_async.await_count == 1  # issued once, unrecorded

    await _run_online_upgrade(svc, record)
    # Existing-bot → adopt the in-doubt workflow; NO second upgrade.
    assert build_service.upgrade_async.await_count == 1
    op = ledger.get_latest_by_kind(1, "upgrade", "online")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901


@pytest.mark.asyncio
async def test_upgrade_bot_not_found_abandons_and_falls_back():
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def upgrade_async(**kw):
        return {"success": False, "error_code": "BOT_NOT_FOUND"}

    async def release_async(**kw):
        wid = baas.issue("BOT-new", "CREATE")
        return {"bot_uuid": "BOT-new", "publish_id": wid}

    build_service.upgrade_async = AsyncMock(side_effect=upgrade_async)
    build_service.release_async = AsyncMock(side_effect=release_async)
    svc = _upgrade_flow(ledger, baas, build_service)
    svc.create_release_binding = Mock(return_value=55)
    svc.record_release_ext = Mock()

    record = _record(PublishStatus.VALIDATING.value)
    record.last_pub_id = 10
    await _run_online_upgrade(svc, record)

    # Upgrade op abandoned; the first-release fallback opened its own op + created.
    up = ledger.get_latest_by_kind(1, "upgrade", "online")
    assert up.state == PublishOperationState.ABANDONED.value
    fr = ledger.get_latest_by_kind(1, "first_release", "online")
    assert fr.state == PublishOperationState.COMPLETED.value
    build_service.release_async.assert_awaited_once()


# ── retry / abandonment: ledger-driven decisions (#197 Task 10) ───────────────
def _land_completed_op(svc, ledger, *, publish_id, kind, bot_uuid, baas_id):
    """Open → record workflow → complete a ledger op landing ``baas_id`` on
    ``bot_uuid`` (a completed BaaS deploy/lifecycle workflow)."""
    op = svc._operation_runner.open_operation(
        publish_id=publish_id, kind=kind, stage=PublishStage.ONLINE, bot_uuid=bot_uuid
    )
    ledger.record_workflow(op.id, baas_publish_id=baas_id, bot_uuid=bot_uuid)
    ledger.complete(op.id)
    return op


def test_is_current_online_deployment_reads_ledger():
    ledger = _ledger()
    baas = FakeBaas()
    publish_service = Mock()
    rec = _record(PublishStatus.ONLINE_PUB.value)
    publish_service.get_publish_by_id.return_value = rec
    svc = _flow(ledger=ledger, baas=baas, build_service=Mock(),
                publish_service=publish_service)

    # No online release op for this publish → not recorded.
    assert svc.is_current_online_deployment(1) is False

    # An online op with only the workflow recorded (ID_RECORDED, binding/ext not
    # yet written) is NOT "recorded": the crash-resume guard must let the release
    # re-enter and finish, not skip it (Group B review Finding 1).
    op = svc._operation_runner.open_operation(
        publish_id=1, kind="first_release", stage=PublishStage.ONLINE
    )
    ledger.record_workflow(op.id, baas_publish_id=901, bot_uuid="BOT-x")
    assert svc.is_current_online_deployment(1) is False

    # Once the op COMPLETES it is the latest deploy that landed on the bot → the
    # live online release.
    ledger.complete(op.id)
    assert svc.is_current_online_deployment(1) is True


def test_is_current_online_deployment_false_for_rolled_back_completed_op():
    """A COMPLETED online op is stale once a later deploy lands on the same bot.

    Rollback demotes a SUCCESS record to DRAFT and re-deploys the previous version
    onto the shared online bot (a ROLLBACK_DEPLOY op with a higher baas_publish_id).
    The demoted record's prior COMPLETED online op is no longer the latest deploy on
    the bot, so a re-publish of that record must run the release again — the stale op
    must NOT gate it (otherwise the online leg is skipped and no BaaS workflow is
    ever issued for the new lifecycle)."""
    ledger = _ledger()
    baas = FakeBaas()
    publish_service = Mock()
    rec = _record(PublishStatus.ONLINE_PUB.value)
    publish_service.get_publish_by_id.return_value = rec
    svc = _flow(ledger=ledger, baas=baas, build_service=Mock(),
                publish_service=publish_service)

    # This record's online upgrade completed and is the latest deploy on the bot.
    _land_completed_op(svc, ledger, publish_id=1, kind="upgrade",
                       bot_uuid="BOT-live", baas_id=901)
    assert svc.is_current_online_deployment(1) is True

    # Rollback re-deploys the previous version onto the SAME bot (higher baas id),
    # on the target record (publish_id=2). The demoted record's op 901 is no longer
    # the latest deploy → stale → not recorded → a re-publish runs the release.
    _land_completed_op(svc, ledger, publish_id=2, kind="rollback_deploy",
                       bot_uuid="BOT-live", baas_id=902)
    assert svc.is_current_online_deployment(1) is False


def test_is_current_online_deployment_true_after_restart_or_scale():
    """A restart / scale lands on the same online bot (higher baas id) but does NOT
    set the deployed version, so it must not make a live release look stale — else
    retry() would misroute (re-run the release instead of restart) and the online
    gate could re-issue a redundant deploy."""
    ledger = _ledger()
    publish_service = Mock()
    rec = _record(PublishStatus.ONLINE_PUB.value)
    publish_service.get_publish_by_id.return_value = rec
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock(),
                publish_service=publish_service)

    _land_completed_op(svc, ledger, publish_id=1, kind="upgrade",
                       bot_uuid="BOT-live", baas_id=901)
    # A restart and a scale-up land afterwards on the same bot (version unchanged).
    _land_completed_op(svc, ledger, publish_id=1, kind="restart",
                       bot_uuid="BOT-live", baas_id=902)
    _land_completed_op(svc, ledger, publish_id=1, kind="scale",
                       bot_uuid="BOT-live", baas_id=903)
    assert svc.is_current_online_deployment(1) is True


def test_is_current_online_deployment_ignores_ext_marker():
    """The answer is purely ledger-driven: an ext.publish.online marker with no
    completed online release op in the ledger does NOT count as recorded. (The
    ledger is the source of truth; a record with a live release always carries its
    own COMPLETED first_release/upgrade op.)"""
    ledger = _ledger()
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock(),
                publish_service=Mock())
    rec = _record(PublishStatus.ONLINE_PUB.value)
    rec.ext = {"publish": {"online": 500}}
    svc._publish_service.get_publish_by_id = Mock(return_value=rec)
    assert svc.is_current_online_deployment(2) is False


def test_sync_failure_outcome_corrects_release_op():
    """A BaaS-wait failure must fail the ledger op, not just the record: the
    release op COMPLETED at bookkeeping time, and without the correction the
    failed deploy still reads as the live deployment — the online gate would
    skip the re-issue on retry and the record would loop FAILED forever."""
    ledger = _ledger()
    publish_service = Mock()
    rec = _record(PublishStatus.ONLINE_PUB.value)
    publish_service.get_publish_by_id.return_value = rec
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock(),
                publish_service=publish_service)

    op = _land_completed_op(svc, ledger, publish_id=1, kind="upgrade",
                            bot_uuid="BOT-live", baas_id=901)
    assert svc.is_current_online_deployment(1) is True

    svc._handle_sync_failure(
        publish_id=1,
        current_status=PublishStatus.ONLINE_PUB,
        ext={},
        progress={"status": "FAILED", "failed_devices": [{"id": "d1"}]},
        baas_publish_id=901,
    )

    corrected = ledger.get_by_id(op.id)
    assert corrected.state == PublishOperationState.FAILED.value
    # The failed deploy no longer reads as the live deployment → the gate
    # re-runs the release and open_operation opens a fresh attempt.
    assert svc.is_current_online_deployment(1) is False


def test_failed_deploy_does_not_supersede_live_release():
    """Cross-record liveness: v1's release is live; v2's later upgrade lands
    (higher baas id) then its workflow FAILS. Before #Task2 the COMPLETED v2 op
    superseded v1 forever; after the outcome correction v1 reads current again."""
    ledger = _ledger()
    publish_service = Mock()
    rec = _record(PublishStatus.ONLINE_PUB.value)
    publish_service.get_publish_by_id.return_value = rec
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock(),
                publish_service=publish_service)

    _land_completed_op(svc, ledger, publish_id=1, kind="upgrade",
                       bot_uuid="BOT-live", baas_id=901)
    _land_completed_op(svc, ledger, publish_id=2, kind="upgrade",
                       bot_uuid="BOT-live", baas_id=902)
    # v2's landed deploy supersedes v1 while it is (presumed) live/in-flight.
    assert svc.is_current_online_deployment(1) is False

    # v2's workflow terminally fails → outcome-corrected → it never took:
    # v1's release is the latest *landed* deploy on the bot again.
    assert ledger.fail_by_workflow(2, 902, "BaaS publish failed") is True
    assert svc.is_current_online_deployment(1) is True


def test_sync_restart_failure_outcome_corrects_restart_op():
    """The restart wait failing corrects the RESTART op too — consistent ledger
    semantics (a restart op is not a version-setting deploy, but its recorded
    outcome should still reflect reality)."""
    ledger = _ledger()
    publish_service = Mock()
    rec = _record(PublishStatus.ONLINE_PUB.value)
    publish_service.get_publish_by_id.return_value = rec
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock(),
                publish_service=publish_service)

    op = _land_completed_op(svc, ledger, publish_id=1, kind="restart",
                            bot_uuid="BOT-live", baas_id=905)
    svc._handle_sync_failure(
        publish_id=1,
        current_status=PublishStatus.ONLINE_PUB,
        ext={},
        progress={"status": "FAILED"},
        baas_publish_id=905,
        error_message="Restart publish status: FAILED",
    )
    assert ledger.get_by_id(op.id).state == PublishOperationState.FAILED.value


def test_abandon_inflight_operations_marks_nonterminal():
    ledger = _ledger()
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock())

    pending = svc._operation_runner.open_operation(
        publish_id=1, kind="first_release", stage=PublishStage.VERIFY
    )
    recorded = svc._operation_runner.open_operation(
        publish_id=1, kind="first_release", stage=PublishStage.ONLINE
    )
    ledger.record_workflow(recorded.id, baas_publish_id=901, bot_uuid="B")
    done = svc._operation_runner.open_operation(
        publish_id=1, kind="restart", stage=PublishStage.ONLINE
    )
    ledger.record_workflow(done.id, baas_publish_id=902, bot_uuid="B")
    ledger.complete(done.id)

    svc._abandon_inflight_operations(1, reason="rebuild")

    assert ledger.get_by_id(pending.id).state == PublishOperationState.ABANDONED.value
    assert ledger.get_by_id(recorded.id).state == PublishOperationState.ABANDONED.value
    # Already-terminal COMPLETED op is left untouched.
    assert ledger.get_by_id(done.id).state == PublishOperationState.COMPLETED.value


# ── restart: crash windows (durable task, existing-bot adopt) ──────────────────
def _restart_flow(ledger, baas, build_service, record, bot_uuid="BOT-live"):
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service,
                publish_service=Mock())
    svc._publish_service.get_publish_by_id = Mock(return_value=record)
    svc._publish_service.get_device_binding_by_id = Mock(
        return_value=Mock(device_id=bot_uuid)
    )
    svc._bot_service.get_bot = Mock(return_value={"bot_id": "b", "entity_id": "u"})
    svc._mutate_and_update_ext = Mock()
    svc.refresh_publish_handle = Mock()
    return svc


@pytest.mark.asyncio
async def test_restart_crash_after_issue_resume_adopts():
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def upgrade_async(**kw):
        wid = baas.issue("BOT-live", "UPDATE")
        return {"publish_id": wid, "success": True}

    build_service.upgrade_async = AsyncMock(side_effect=upgrade_async)
    record = _record(PublishStatus.SUCCESS.value)
    record.ext = {"binding": {"online": 42}, "migration_path": "/m"}
    svc = _restart_flow(ledger, baas, build_service, record)

    _crash_before_record(ledger)

    with pytest.raises(RuntimeError):
        await svc.execute_restart(publish_id=1, stage="online", operator="op")
    assert build_service.upgrade_async.await_count == 1

    result = await svc.execute_restart(publish_id=1, stage="online", operator="op")
    # Existing bot → adopt the in-doubt workflow; no second restart issued.
    assert build_service.upgrade_async.await_count == 1
    assert result["success"] is True
    op = ledger.get_latest_by_kind(1, "restart", "online")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901


# ── scale: crash windows (existing-bot adopt) ─────────────────────────────────
def _scale_flow(ledger, baas, record, bot_uuid="BOT-live"):
    svc = _flow(ledger=ledger, baas=baas, build_service=Mock(),
                publish_service=Mock())
    svc._publish_service.get_publish_by_id = Mock(return_value=record)
    svc._publish_service.get_device_binding_by_id = Mock(
        return_value=Mock(device_id=bot_uuid)
    )
    svc._bot_service.get_bot = Mock(return_value={"bot_id": "b", "entity_id": "u"})
    svc._ext_state.get_latest_ext = Mock(return_value={"binding": {"online": 42}})
    svc._resolve_scale_target_count = Mock(return_value=3)
    svc._mutate_and_update_ext = Mock()
    return svc


@pytest.mark.asyncio
async def test_scale_crash_after_call_resume_adopts_not_rescales():
    ledger = _ledger()
    baas = FakeBaas()

    def scale_bot(**kw):
        wid = baas.issue("BOT-live", "SCALE_UP")
        return {"publish_id": wid, "target_count": kw.get("target_count")}

    baas.scale_bot = Mock(side_effect=scale_bot)
    record = _record(PublishStatus.SUCCESS.value)
    svc = _scale_flow(ledger, baas, record)

    _crash_before_record(ledger)

    with pytest.raises(RuntimeError):
        await svc.scale_bot(publish_id=1, operator="op")
    assert baas.scale_bot.call_count == 1  # scaled once, unrecorded

    result = await svc.scale_bot(publish_id=1, operator="op")
    # Existing bot → adopt the in-doubt SCALE workflow; NO second scale call.
    assert baas.scale_bot.call_count == 1
    assert result["success"] is True
    assert result["baas_publish_id"] == 901
    op = ledger.get_latest_by_kind(1, "scale", "online")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901
    # The BaaS call carried the runner's deterministic request id, not a wall clock.
    assert baas.scale_bot.call_args.kwargs["request_id"] == operation_request_id(1, "scale", "online", 1)


# ── eval publish/teardown: crash windows ──────────────────────────────────────
def _eval_flow(ledger, baas, build_service, record):
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service,
                publish_service=Mock())
    svc._publish_service.get_publish_by_id = Mock(return_value=record)
    svc._bot_service.get_bot = Mock(return_value={"bot_id": "b", "entity_id": "u"})
    return svc


@pytest.mark.asyncio
async def test_eval_publish_creation_orphan_visible_as_pending_op():
    # Eval is a CREATION (no bot to adopt). A crash after the BaaS create but
    # before the id is recorded leaves an in-flight PENDING op → the orphan bot is
    # observable; the re-run re-issues (accepted creation orphan) and records the
    # second workflow.
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def release_async(**kw):
        wid = baas.issue("BOT-eval", "CREATE")
        return {"bot_uuid": "BOT-eval", "publish_id": wid, "status": "RUNNING"}

    build_service.release_async = AsyncMock(side_effect=release_async)
    record = _record(PublishStatus.SUCCESS.value)
    svc = _eval_flow(ledger, baas, build_service, record)

    _crash_before_record(ledger)

    with pytest.raises(RuntimeError):
        await svc.eval_publish(1, "op", biz_id="biz-1")
    assert build_service.release_async.await_count == 1
    op = ledger.get_latest_by_kind(1, "eval_publish", "eval")
    assert op.state == PublishOperationState.PENDING.value  # in-flight → observable

    result = await svc.eval_publish(1, "op", biz_id="biz-1")
    assert build_service.release_async.await_count == 2  # re-issued (creation orphan)
    assert result["success"] is True
    op = ledger.get_latest_by_kind(1, "eval_publish", "eval")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 902  # the second (recorded) workflow
    # The TTL teardown safety net was enqueued for the eval bot.
    assert svc._task_queue_service.enqueue.called


@pytest.mark.asyncio
async def test_eval_teardown_crash_after_issue_resume_adopts():
    ledger = _ledger()
    baas = FakeBaas()
    svc = _flow(ledger=ledger, baas=baas, build_service=Mock(), publish_service=Mock())

    def destroy_bot(**kw):
        wid = baas.issue("BOT-eval", "DESTROY")
        return {"publish_id": wid}

    baas.destroy_bot = Mock(side_effect=destroy_bot)

    _crash_before_record(ledger)

    with pytest.raises(RuntimeError):
        await svc.execute_eval_teardown(publish_id=7, bot_uuid="BOT-eval", operator="op")
    assert baas.destroy_bot.call_count == 1  # destroyed once, unrecorded

    result = await svc.execute_eval_teardown(publish_id=7, bot_uuid="BOT-eval", operator="op")
    # Existing bot → adopt the in-doubt DESTROY workflow; NO second destroy.
    assert baas.destroy_bot.call_count == 1
    assert result["success"] is True
    assert result["baas_publish_id"] == 901
    op = ledger.get_latest_by_kind(7, "eval_teardown", "eval")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901
    assert baas.destroy_bot.call_args.kwargs["request_id"] == operation_request_id(7, "eval_teardown", "eval", 1)


# ── rollback deploy: crash windows (existing-bot adopt) ───────────────────────
@pytest.mark.asyncio
async def test_rollback_deploy_crash_after_issue_resume_adopts():
    ledger = _ledger()
    baas = FakeBaas()
    build_service = Mock()

    async def upgrade_async(**kw):
        wid = baas.issue("BOT-live", "UPDATE")
        return {"publish_id": wid, "success": True}

    build_service.upgrade_async = AsyncMock(side_effect=upgrade_async)
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service, publish_service=Mock())

    target = _record(PublishStatus.SUCCESS.value)
    target.ext = {
        "migration_path": "/m",
        "binding": {"online": 88},
    }
    current = _record(PublishStatus.DRAFT.value)
    svc._publish_service.get_publish_by_id = Mock(
        side_effect=lambda pid: target if pid == 2 else current
    )
    svc._ext_state.get_latest_ext = Mock(return_value=target.ext)
    svc._publish_service.get_device_binding_by_id = Mock(
        return_value=Mock(device_id="BOT-live")
    )
    svc._bot_service.get_bot = Mock(return_value={"bot_id": "b", "entity_id": "u"})
    svc._update_publish_status = Mock()

    _crash_before_record(ledger)

    with pytest.raises(RuntimeError):
        await svc.execute_rollback(current_publish_id=1, target_publish_id=2, operator="op")
    assert build_service.upgrade_async.await_count == 1  # issued once, unrecorded

    result = await svc.execute_rollback(current_publish_id=1, target_publish_id=2, operator="op")
    # Existing bot → adopt the in-doubt rollback workflow; NO second deploy.
    assert build_service.upgrade_async.await_count == 1
    assert result.baas_publish_id == "901"
    op = ledger.get_latest_by_kind(2, "rollback_deploy", "online")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901


# ── offline destroy: durable + idempotent (binding-status + stop-idempotency) ──
def _destroy_flow(ledger, baas, record, *, binding_status="ACTIVE"):
    svc = _flow(ledger=ledger, baas=baas, build_service=Mock(), publish_service=Mock())
    svc._publish_service.get_publish_by_id = Mock(return_value=record)
    svc._publish_service.get_device_binding_by_id = Mock(
        return_value=Mock(device_id="BOT-live", status=binding_status)
    )
    svc._release_binding = Mock()
    return svc


@pytest.mark.asyncio
async def test_offline_destroy_stop_then_releases_binding():
    ledger = _ledger()
    baas = FakeBaas()
    baas.stop_bot = Mock(return_value={"publish_id": 777})
    record = _record(PublishStatus.RELEASED.value)
    record.ext = {"binding": {"online": 42}}
    svc = _destroy_flow(ledger, baas, record, binding_status="ACTIVE")

    result = await svc.execute_offline_destroy(publish_id=5, stage="online", operator="op")

    assert result["success"] is True
    assert baas.stop_bot.call_count == 1
    # Deterministic, correlation-only request id (stable across retries).
    assert baas.stop_bot.call_args.kwargs["request_id"] == to_baas_request_id("offline_destroy_pub_5_online")
    svc._release_binding.assert_called_once()


@pytest.mark.asyncio
async def test_offline_destroy_released_binding_short_circuits():
    # A re-enqueue for an already-destroyed record (binding RELEASED) is a true
    # no-op — no second stop_bot (idempotency without a trackable workflow id).
    ledger = _ledger()
    baas = FakeBaas()
    baas.stop_bot = Mock(return_value={"publish_id": 777})
    record = _record(PublishStatus.RELEASED.value)
    record.ext = {"binding": {"online": 42}}
    svc = _destroy_flow(ledger, baas, record, binding_status="RELEASED")

    result = await svc.execute_offline_destroy(publish_id=5, stage="online", operator="op")

    assert result["success"] is True
    baas.stop_bot.assert_not_called()
    svc._release_binding.assert_not_called()


@pytest.mark.asyncio
async def test_offline_destroy_stop_failure_propagates_for_retry():
    # A stop_bot failure must PROPAGATE (not be masked as done) so the durable task
    # retries instead of stranding the online bot.
    ledger = _ledger()
    baas = FakeBaas()
    baas.stop_bot = Mock(side_effect=RuntimeError("baas /stop failed"))
    record = _record(PublishStatus.RELEASED.value)
    record.ext = {"binding": {"online": 42}}
    svc = _destroy_flow(ledger, baas, record, binding_status="ACTIVE")

    with pytest.raises(RuntimeError):
        await svc.execute_offline_destroy(publish_id=5, stage="online", operator="op")
    svc._release_binding.assert_not_called()
