"""Crash-window tests for the publish operation pipeline (#197).

Each test drives an operation against a REAL operation-ledger repository (SQLite)
plus a scripted fake BaaS, simulates a crash between two steps (via the runner's
checkpoint hook or a one-shot side effect), re-runs, and asserts convergence: the
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
    PublishOperationRepository,
)
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
)


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

    async def release_async(**kw):
        wid = baas.issue("BOT-new", "CREATE")
        return {"bot_uuid": "BOT-new", "publish_id": wid}

    build_service.release_async = AsyncMock(side_effect=release_async)
    svc = _flow(ledger=ledger, baas=baas, build_service=build_service)
    svc.create_release_binding = Mock(return_value=55)
    svc.record_release_ext = Mock()

    crashed = []

    def checkpoint(name):
        if name == "before_issue" and not crashed:
            crashed.append(1)
            raise RuntimeError("pod died before issue")

    svc._operation_runner._checkpoint = checkpoint

    record = _record(PublishStatus.BUILT.value)
    with pytest.raises(RuntimeError):
        await svc._execute_verify_first_release(
            publish_record=record, operator="op", migration_path="/m",
            bot={"bot_id": "b2", "owner_id": "u1"},
        )
    # nothing issued yet
    assert build_service.release_async.await_count == 0

    svc._operation_runner._checkpoint = lambda _n: None
    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )
    assert build_service.release_async.await_count == 1  # issued exactly once
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
    op = ledger.get_latest_by_kind(1, "verify_first_release", "verify")
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

    crashed = []

    def checkpoint(name):
        if name == "after_issue" and not crashed:
            crashed.append(1)
            raise RuntimeError("pod died after create, before record")

    svc._operation_runner._checkpoint = checkpoint

    record = _record(PublishStatus.BUILT.value)
    with pytest.raises(RuntimeError):
        await svc._execute_verify_first_release(
            publish_record=record, operator="op", migration_path="/m",
            bot={"bot_id": "b2", "owner_id": "u1"},
        )
    # One bot already created on BaaS but not recorded → the orphan.
    assert build_service.release_async.await_count == 1
    op = ledger.get_latest_by_kind(1, "verify_first_release", "verify")
    assert op.state == PublishOperationState.PENDING.value  # in-flight → observable

    svc._operation_runner._checkpoint = lambda _n: None
    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )
    # Re-issued (accepted creation orphan); the op now records the second workflow.
    assert build_service.release_async.await_count == 2
    op = ledger.get_latest_by_kind(1, "verify_first_release", "verify")
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

    crashed = []

    def checkpoint(name):
        if name == "after_issue" and not crashed:
            crashed.append(1)
            raise RuntimeError("crash after upgrade issued, before record")

    svc._operation_runner._checkpoint = checkpoint

    record = _record(PublishStatus.VALIDATING.value)
    record.last_pub_id = 10
    with pytest.raises(RuntimeError):
        await _run_online_upgrade(svc, record)
    assert build_service.upgrade_async.await_count == 1  # issued once, unrecorded

    svc._operation_runner._checkpoint = lambda _n: None
    await _run_online_upgrade(svc, record)
    # Existing-bot → adopt the in-doubt workflow; NO second upgrade.
    assert build_service.upgrade_async.await_count == 1
    op = ledger.get_latest_by_kind(1, "online_upgrade", "online")
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
    up = ledger.get_latest_by_kind(1, "online_upgrade", "online")
    assert up.state == PublishOperationState.ABANDONED.value
    fr = ledger.get_latest_by_kind(1, "online_first_release", "online")
    assert fr.state == PublishOperationState.COMPLETED.value
    build_service.release_async.assert_awaited_once()


# ── retry / abandonment: ledger-driven decisions (#197 Task 10) ───────────────
def test_is_online_release_recorded_reads_ledger():
    ledger = _ledger()
    baas = FakeBaas()
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = _record(PublishStatus.ONLINE_PUB.value)
    svc = _flow(ledger=ledger, baas=baas, build_service=Mock(),
                publish_service=publish_service)

    # No op, no ext marker (record ext has no 'publish') → not recorded.
    assert svc.is_online_release_recorded(1) is False

    # An online op with only the workflow recorded (ID_RECORDED, binding/ext not
    # yet written) is NOT "recorded": the crash-resume guard must let the release
    # re-enter and finish, not skip it (Group B review Finding 1).
    op = svc._operation_runner.open_operation(
        publish_id=1, kind="online_first_release", stage="online"
    )
    ledger.record_workflow(op.id, baas_publish_id=901, bot_uuid="BOT-x")
    assert svc.is_online_release_recorded(1) is False

    # Only once the op COMPLETES (binding + ext done) is it "recorded".
    ledger.complete(op.id)
    assert svc.is_online_release_recorded(1) is True


def test_is_online_release_recorded_ext_fallback_for_pre_ledger():
    ledger = _ledger()
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock(),
                publish_service=Mock())
    # No ledger op, but a legacy ext.publish.online marker (pre-ledger record).
    rec = _record(PublishStatus.ONLINE_PUB.value)
    rec.ext = {"publish": {"online": 500}}
    svc._publish_service.get_publish_by_id = Mock(return_value=rec)
    assert svc.is_online_release_recorded(2) is True


def test_abandon_inflight_operations_marks_nonterminal():
    ledger = _ledger()
    svc = _flow(ledger=ledger, baas=FakeBaas(), build_service=Mock())

    pending = svc._operation_runner.open_operation(
        publish_id=1, kind="verify_first_release", stage="verify"
    )
    recorded = svc._operation_runner.open_operation(
        publish_id=1, kind="online_first_release", stage="online"
    )
    ledger.record_workflow(recorded.id, baas_publish_id=901, bot_uuid="B")
    done = svc._operation_runner.open_operation(
        publish_id=1, kind="restart", stage="online"
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

    crashed = []

    def checkpoint(name):
        if name == "after_issue" and not crashed:
            crashed.append(1)
            raise RuntimeError("crash after restart issued, before record")

    svc._operation_runner._checkpoint = checkpoint

    with pytest.raises(RuntimeError):
        await svc.execute_restart(publish_id=1, stage="online", operator="op")
    assert build_service.upgrade_async.await_count == 1

    svc._operation_runner._checkpoint = lambda _n: None
    result = await svc.execute_restart(publish_id=1, stage="online", operator="op")
    # Existing bot → adopt the in-doubt workflow; no second restart issued.
    assert build_service.upgrade_async.await_count == 1
    assert result["success"] is True
    op = ledger.get_latest_by_kind(1, "restart", "online")
    assert op.state == PublishOperationState.COMPLETED.value
    assert op.baas_publish_id == 901
