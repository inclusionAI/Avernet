"""Pool-size-one regressions for nontransactional policy and CAS diagnostics."""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Integer, MetaData, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from secbaas.community.api.device_manage import DeviceCreationError
from secbaas.community.core.database import DatabaseManager
from secbaas.community.core.repository.bot_run_queue import OrmBotRunQueueRepository
from secbaas.community.core.repository.bot_run_queue._orm_model import BotRunQueueModel
from secbaas.community.core.repository.device_binding import OrmDeviceBindingRepository
from secbaas.community.core.repository.device_binding._orm_model import (
    DeviceBindingModel,
)
from secbaas.community.core.repository.publish_batch import (
    OrmPublishBatchRepository,
    PublishBatchRecord,
)
from secbaas.community.core.repository.publish_batch._orm_model import PublishBatchModel
from secbaas.community.core.repository.ws_relay_session import (
    OrmWsRelaySessionRepository,
)
from secbaas.community.core.repository.ws_relay_session._orm_model import (
    WsRelaySessionModel,
)
from secbaas.community.core.service.bot_run._executor import RequeuedToPendingError
from secbaas.community.core.service.bot_run._worker import BotRequestWorker
from secbaas.community.core.service.device_binding_query import (
    DeviceBindingQueryService,
)
from secbaas.community.core.service.ws_relay_session import WsRelaySessionService


@pytest.fixture
def database(tmp_path, monkeypatch):
    for name in ("bot_run_queue", "ws_relay_session"):
        monkeypatch.setattr(
            f"secbaas.community.core.repository.{name}._orm_repository.get_current_env",
            lambda: "dev",
        )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'short-sessions.db'}",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
    )
    metadata = MetaData()
    for model in (
        BotRunQueueModel,
        DeviceBindingModel,
        PublishBatchModel,
        WsRelaySessionModel,
    ):
        table = model.__table__.to_metadata(metadata)
        table.c.id.type = Integer()
    metadata.create_all(engine)
    stats = SimpleNamespace(peak=0, checkouts=0)

    @event.listens_for(engine, "checkout")
    def checkout(*_):
        stats.checkouts += 1
        stats.peak = max(stats.peak, engine.pool.checkedout())

    @contextmanager
    def orm_session():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    manager = object.__new__(DatabaseManager)
    manager._plugin = SimpleNamespace(orm_session=orm_session)
    yield manager, engine, stats
    assert engine.pool.checkedout() == 0
    engine.dispose()


def relay(database):
    manager, engine, _ = database
    repo = OrmWsRelaySessionRepository(manager)
    repo.insert_init(session_id="s1", machine_id="m1", operator="u1")
    return repo, WsRelaySessionService(repo), engine


def test_relay_concurrent_close_cannot_be_reopened(database, monkeypatch):
    repo, service, engine = relay(database)
    activate = repo.update_active

    def close_before_activate(**kwargs):
        assert engine.pool.checkedout() == 0
        assert (
            repo.update_closed(session_id="s1", expected_statuses=frozenset({"init"}))
            == 1
        )
        return activate(**kwargs)

    monkeypatch.setattr(repo, "update_active", close_before_activate)
    read = repo.get_by_session_id

    def detached_read(session_id):
        assert engine.pool.checkedout() == 0
        return read(session_id)

    monkeypatch.setattr(repo, "get_by_session_id", detached_read)
    with pytest.raises(DeviceCreationError) as exc:
        service.update_status(
            session_id="s1",
            status="active",
            connected_server_instance="server",
            connected_route_info={"host": "localhost"},
        )
    assert exc.value.error_code == "RELAY_STATE_CONFLICT"
    assert read("s1").status == "closed"
    assert database[2].peak == 1


def test_relay_same_status_does_not_overwrite_route_or_close_time(database):
    repo, service, _ = relay(database)
    assert (
        service.update_status(
            session_id="s1",
            status="active",
            connected_server_instance="server",
            connected_route_info={"port": 1234},
        )
        == "active"
    )
    # Same-state no-op is accepted even without route fields.
    assert service.update_status(session_id="s1", status="active") == "active"
    record = repo.get_by_session_id("s1")
    assert record.connected_server_instance == "server"
    assert record.connected_route_info == {"port": 1234}
    assert service.update_status(session_id="s1", status="closed") == "closed"
    closed_at = repo.get_by_session_id("s1").gmt_close
    assert service.update_status(session_id="s1", status="closed") == "closed"
    assert repo.get_by_session_id("s1").gmt_close == closed_at


def test_relay_cas_zero_same_target_is_idempotent(database, monkeypatch):
    repo, service, engine = relay(database)
    close = repo.update_closed

    def racing_close(**kwargs):
        assert engine.pool.checkedout() == 0
        assert close(**kwargs) == 1
        return close(**kwargs)

    monkeypatch.setattr(repo, "update_closed", racing_close)
    assert service.update_status(session_id="s1", status="closed") == "closed"


def test_relay_cas_does_not_cross_environment(database):
    repo, service, engine = relay(database)
    with Session(engine) as session:
        session.query(WsRelaySessionModel).update({"env": "prod"})
        session.commit()
    assert (
        repo.update_active(
            session_id="s1",
            expected_statuses=frozenset({"init"}),
            connected_server_instance="server",
            connected_route_info={"port": 1},
        )
        == 0
    )
    assert (
        repo.update_closed(session_id="s1", expected_statuses=frozenset({"init"})) == 0
    )
    with pytest.raises(DeviceCreationError) as exc:
        service.update_status(session_id="s1", status="closed")
    assert exc.value.error_code == "RELAY_SESSION_NOT_FOUND"


def test_relay_write_exception_propagates_without_diagnostic_retry(
    database, monkeypatch
):
    repo, service, _ = relay(database)
    read = MagicMock(wraps=repo.get_by_session_id)
    write = MagicMock(side_effect=RuntimeError("write failed"))
    monkeypatch.setattr(repo, "get_by_session_id", read)
    monkeypatch.setattr(repo, "update_closed", write)
    with pytest.raises(RuntimeError, match="write failed"):
        service.update_status(session_id="s1", status="closed")
    read.assert_called_once()
    write.assert_called_once()


def test_relay_failure_cleanup_keeps_unconditional_terminal_write(database):
    repo, _, _ = relay(database)
    # LocalPaasService uses this on failure after creating a session. It is not
    # a reversible state transition and deliberately does not supply a fence.
    assert repo.update_closed(session_id="s1") == 1
    assert repo.get_by_session_id("s1").status == "closed"


@pytest.mark.parametrize("action", ["mark_done", "release"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("read_fails", [False, True])
async def test_queue_noop_releases_write_before_diagnostics(
    database,
    monkeypatch,
    action,
    existing,
    read_fails,
):
    manager, engine, stats = database
    repo = OrmBotRunQueueRepository(manager)
    if existing:
        repo.insert_queue(run_id="r1", bot_id="b1")
        assert repo.claim_pending_by_bot("b1", "other-worker") is not None
    worker = object.__new__(BotRequestWorker)
    worker._queue = repo
    worker._worker_id = "this-worker"
    read = repo.get_by_run_id

    def diagnose(run_id):
        assert engine.pool.checkedout() == 0
        if read_fails:
            raise RuntimeError("diagnostic failed")
        return read(run_id)

    reader = MagicMock(side_effect=diagnose)
    monkeypatch.setattr(repo, "get_by_run_id", reader)
    method = "mark_done" if action == "mark_done" else "release_to_pending"
    write = MagicMock(wraps=getattr(repo, method))
    monkeypatch.setattr(repo, method, write)
    record = SimpleNamespace(run_id="r1")
    if action == "mark_done":
        worker._mark_queue_done(record)
    else:
        await worker._requeue_pending(record, RequeuedToPendingError("r1", "session-1"))
    write.assert_called_once_with("r1", "this-worker")
    reader.assert_called_once_with("r1")
    assert stats.peak == 1
    if existing:
        assert read("r1").assigned_worker == "other-worker"
        assert read("r1").status == "RUNNING"


def test_batch_stage_projection_happens_after_single_session(database, monkeypatch):
    manager, engine, stats = database
    repo = OrmPublishBatchRepository(manager)
    with Session(engine) as session:
        for i, values in enumerate(
            [
                {},
                {"batch_index": 1, "extra_config": '{"stage":"GRAY"}'},
                {"tenant": "other"},
                {"env": "prod"},
                {"is_deleted": 5},
                {"publish_id": 2},
                {"extra_config": "invalid-json"},
            ]
        ):
            row = dict(
                id=i + 1,
                tenant="t1",
                env="dev",
                domain="d",
                is_deleted=0,
                creator="u",
                modifier="u",
                publish_id=1,
                bot_id=1,
                batch_index=2,
                batch_capacity=1,
                status="PENDING",
                extra_config='{"stage":"GRAY"}',
            )
            row.update(values)
            session.add(PublishBatchModel(**row))
        session.commit()
    stage = PublishBatchRecord.stage.fget

    def detached_stage(record):
        assert engine.pool.checkedout() == 0
        return stage(record)

    monkeypatch.setattr(PublishBatchRecord, "stage", property(detached_stage))
    checkouts = stats.checkouts
    result = repo.list_by_publish_and_stage(1, "t1", "dev", "GRAY")
    assert [r.id for r in result] == [2, 1]
    assert stats.checkouts == checkouts + 1
    assert [
        r.id for r in repo.list_by_publish_and_stage(1, "t1", "dev", "UNKNOWN")
    ] == [7]


def test_export_formats_outside_session_and_preserves_filters(database, monkeypatch):
    manager, engine, stats = database
    repo = OrmDeviceBindingRepository(manager)
    service = DeviceBindingQueryService(
        MagicMock(), MagicMock(), repo, MagicMock(), MagicMock()
    )
    now = datetime(2026, 10, 29)
    with Session(engine) as session:
        for i in range(204):
            session.add(
                DeviceBindingModel(
                    id=i + 1,
                    entity_id=str(i),
                    entity_type="staff",
                    device_id=str(i),
                    device_provider="baas" if i == 202 else "arca",
                    env="prod" if i == 203 else "pre",
                    status="INACTIVE" if i == 201 else "ACTIVE",
                    device_props='{"bolt_id":"bolt","sandbox_id":"sandbox"}',
                    applied_by="u",
                    gmt_create=now,
                )
            )
        session.commit()
    from secbaas.community.core.service.device_binding_query import _service

    parse = _service.json.loads

    def detached_json(value):
        assert engine.pool.checkedout() == 0
        return parse(value)

    monkeypatch.setattr(_service.json, "loads", detached_json)
    read = MagicMock(wraps=repo.list_export_rows)
    monkeypatch.setattr(repo, "list_export_rows", read)
    result = service.export_device_list(env="pre")
    assert len(result) == 201
    assert result[0] == ("200", "bolt", "sandbox")
    assert result[-1][0] == "0"
    assert [c.kwargs["offset"] for c in read.call_args_list] == [0, 200]
    assert all(c.kwargs["limit"] == 200 for c in read.call_args_list)
    assert len(service.export_device_all()) == 202
    assert stats.peak == 1
