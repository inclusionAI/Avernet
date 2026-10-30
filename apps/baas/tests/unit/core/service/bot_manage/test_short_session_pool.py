"""Real QueuePool regressions (no StaticPool or RLock hiding nested checkout)."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Integer, MetaData, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from secbaas.community.core.database import DatabaseManager, TransactionRollbackError
from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.repository.bot import (
    BotModel,
    BotRecordConflictError,
    OrmBotRepository,
)
from secbaas.community.core.repository.bot_run_queue import OrmBotRunQueueRepository
from secbaas.community.core.repository.bot_run_queue._orm_model import BotRunQueueModel
from secbaas.community.core.repository.file_transfer_ticket import OrmTicketRepository
from secbaas.community.core.repository.file_transfer_ticket._orm_model import (
    FileTransferTicketModel,
)
from secbaas.community.core.repository.session_file_ticket import (
    OrmSessionTicketRepository,
)
from secbaas.community.core.repository.session_file_ticket._orm_model import (
    SessionFileTicketModel,
)
from secbaas.community.core.service.bot_manage._cloning import try_insert_pending_bot
from secbaas.community.core.service.bot_run._worker import BotRequestWorker
from secbaas.community.core.service.file_transfer._ticket_status import (
    update_ticket_status as update_bot_status,
)
from secbaas.community.core.service.session_file_sharing._ticket_status import (
    update_ticket_status as update_session_status,
)


@pytest.fixture
def database(tmp_path):
    engines = []

    def build(pool_size=1):
        engine = create_engine(
            f"sqlite:///{tmp_path / str(len(engines))}.db",
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=0,
            pool_timeout=0.2,
            connect_args={"check_same_thread": False},
        )
        engines.append(engine)
        # BigInteger PK is INTEGER on the SQLite backend in production too;
        # adapt copied test DDL without mutating global mapped models/types.
        metadata = MetaData()
        for model in [
            BotModel,
            FileTransferTicketModel,
            SessionFileTicketModel,
            BotRunQueueModel,
        ]:
            table = model.__table__.to_metadata(metadata)
            table.c.id.type = Integer()
        metadata.create_all(engine)
        stats = SimpleNamespace(peak=0, checkouts=0, exits=0)

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
                finally:
                    stats.exits += 1

        manager = object.__new__(DatabaseManager)
        manager._plugin = SimpleNamespace(orm_session=orm_session)
        return manager, engine, stats

    yield build
    for engine in engines:
        engine.dispose()


def seed_source(repository):
    return repository.insert_bot(
        bot_uuid="bot-1",
        tenant="t1",
        env="dev",
        domain="test",
        creator="user",
        modifier="user",
        status="ACTIVE",
        name="original",
    )


def test_pending_clone_needs_only_one_connection(database):
    manager, engine, stats = database()
    repository = OrmBotRepository(manager, MagicMock())
    source_id = seed_source(repository)
    clone_id = try_insert_pending_bot(
        repository, source_bot_id=source_id, tenant="t1", env="dev", name="clone"
    )
    assert clone_id != source_id
    assert repository.get_by_id(clone_id, "t1", "dev").name == "clone"
    with pytest.raises(BotRecordConflictError):
        try_insert_pending_bot(
            repository, source_bot_id=source_id, tenant="t1", env="dev"
        )
    assert stats.peak == 1
    assert engine.pool.checkedout() == 0
    # Historical clones stay deleted and a new live slot can be reserved.
    repository.soft_delete(bot_id=clone_id, tenant="t1", env="dev", modifier="user")
    next_id = try_insert_pending_bot(
        repository, source_bot_id=source_id, tenant="t1", env="dev"
    )
    assert next_id != clone_id
    assert repository.get_by_id(clone_id, "t1", "dev") is None


def test_concurrent_clone_inserts_have_exactly_one_winner(database):
    manager, engine, _ = database(pool_size=2)
    repository = OrmBotRepository(manager, MagicMock())
    source_id = seed_source(repository)
    barrier = Barrier(2)

    class RacingRepository:
        def get_by_id(self, *args):
            return repository.get_by_id(*args)

        def get_by_bot_uuid(self, *args):
            snapshot = repository.get_by_bot_uuid(*args)
            # Both callers see an empty slot, after releasing their read Session.
            barrier.wait(timeout=5)
            return snapshot

        def insert_bot(self, **kwargs):
            return repository.insert_bot(**kwargs)

    def clone():
        try:
            return try_insert_pending_bot(
                RacingRepository(), source_bot_id=source_id, tenant="t1", env="dev"
            )
        except BotRecordConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: clone(), range(2)))
    assert results.count("conflict") == 1
    assert len([r for r in results if isinstance(r, int)]) == 1
    assert len(repository.list_by_bot_uuid("bot-1", "t1", "dev")) == 2
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("kind", ["bot", "session"])
def test_zero_cas_releases_update_connection_before_diagnostic_read(
    database, monkeypatch, kind
):
    manager, engine, stats = database()
    is_bot = kind == "bot"
    model = FileTransferTicketModel if is_bot else SessionFileTicketModel
    cls = OrmTicketRepository if is_bot else OrmSessionTicketRepository
    update = update_bot_status if is_bot else update_session_status
    monkeypatch.setattr(
        f"secbaas.community.core.repository.{'file_transfer_ticket' if is_bot else 'session_file_ticket'}._orm_repository.get_current_env",
        lambda: "dev",
    )
    # No row exists: UPDATE affects zero. Verify that Service diagnostics
    # happen only after its plugin context has exited and checked in.
    repository = cls(manager)
    read = repository.get_by_transfer_id

    def diagnose(transfer_id):
        assert engine.pool.checkedout() == 0
        assert stats.exits == 1
        return read(transfer_id)

    monkeypatch.setattr(repository, "get_by_transfer_id", diagnose)
    from secbaas.community.api.bot_runtime import TransferNotFoundError

    with pytest.raises(TransferNotFoundError):
        update(repository, "absent", "DONE")
    assert stats.checkouts == 2
    assert stats.peak == 1
    assert engine.pool.checkedout() == 0
    # CAS also preserves environment filtering and the expected-state fence.
    with Session(engine) as session:
        values = dict(
            transfer_id="f1",
            tenant="t1",
            env="dev",
            status="DONE",
            filename="f",
            fileservice_staging_path="f",
            operator="u",
        )
        if is_bot:
            values.update(paas_device_id="", direction="UPLOAD")
        else:
            values.update(session_id="s1")
        session.add(model(**values))
        session.commit()
    affected = repository.compare_and_set_status(
        "f1", "UPLOADING", expected_statuses=frozenset({"CREATED"})
    )
    assert affected == 0
    assert read("f1").status == "DONE"
    values.update(transfer_id="other-env", env="prod", status="CREATED")
    with Session(engine) as session:
        session.add(model(**values))
        session.commit()
    assert (
        repository.compare_and_set_status(
            "other-env", "UPLOADING", expected_statuses=frozenset({"CREATED"})
        )
        == 0
    )
    with Session(engine) as session:
        row = session.query(model).filter(model.transfer_id == "other-env").one()
        assert row.status == "CREATED"


def test_debug_logging_does_not_acquire_connection(database, caplog):
    manager, engine, stats = database()

    class Repository(OrmConnectionMixin):
        _database = manager

        @with_orm_session
        def no_sql(self):
            return 42

    with caplog.at_level("DEBUG", logger="orm"):
        assert Repository().no_sql() == 42
    assert stats.checkouts == 0
    assert engine.pool.checkedout() == 0
    assert "method=" in caplog.text
    assert "query=" not in caplog.text


def test_explicit_transaction_rolls_back_all_participants(database):
    manager, engine, _ = database()
    with pytest.raises(TransactionRollbackError):
        with manager.scoped_orm_session():
            with manager.orm_session() as session:
                session.execute(
                    text("CREATE TABLE IF NOT EXISTS tx_probe (value INTEGER)")
                )
                session.execute(text("INSERT INTO tx_probe VALUES (1)"))
            try:
                with manager.orm_session() as session:
                    session.execute(text("INSERT INTO tx_probe VALUES (2)"))
                    raise ValueError("fail second operation")
            except ValueError:
                pass
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM tx_probe")).scalar_one() == 0
        )
    assert engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_timeout_policy_runs_after_snapshot_connection_released(
    database, monkeypatch
):
    manager, engine, _ = database()
    monkeypatch.setattr(
        "secbaas.community.core.repository.bot_run_queue._orm_repository.get_current_env",
        lambda: "dev",
    )
    repository = OrmBotRunQueueRepository(manager)
    repository.insert_queue(run_id="expired", bot_id="bot-1", meta={"timeout": -1})
    repository.insert_queue(run_id="fresh", bot_id="bot-1", meta={"timeout": 3600})
    repository.insert_queue(run_id="no-deadline", bot_id="bot-1", meta={})
    # SQLite CURRENT_TIMESTAMP is UTC; the worker's existing contract uses
    # local naive datetimes. Use a consistent test clock rather than making
    # the fresh sample depend on the host timezone.
    with manager.orm_session() as session:
        session.query(BotRunQueueModel).update({"gmt_create": datetime.now()})
    assert len(repository.list_unfinished(limit=2)) == 2
    worker = object.__new__(BotRequestWorker)
    worker._queue = repository
    worker._executor = SimpleNamespace(execute=AsyncMock())
    worker._fire_timeout_callback = AsyncMock()
    calculate = BotRequestWorker._record_deadline

    def deadline(record):
        assert engine.pool.checkedout() == 0
        return calculate(record)

    worker._record_deadline = deadline
    await worker._timeout_scan_global()
    worker._executor.execute.assert_awaited_once()
    assert worker._executor.execute.call_args.args[0].run_id == "expired"
    assert repository.get_by_run_id("expired").status == "DONE"
    assert repository.get_by_run_id("fresh").status == "PENDING"
    assert repository.get_by_run_id("no-deadline").status == "PENDING"
    assert engine.pool.checkedout() == 0


def test_destroy_does_not_need_a_live_released_slot(database):
    manager, engine, _ = database()
    repository = OrmBotRepository(manager, MagicMock())
    active = seed_source(repository)
    released = repository.insert_bot(
        bot_uuid="bot-1",
        tenant="t1",
        env="dev",
        domain="test",
        creator="user",
        modifier="user",
        status="RELEASED",
        name="released",
    )
    repository.complete_destroy(bot_id=active, tenant="t1", env="dev", modifier="user")
    assert repository.get_by_id(active, "t1", "dev") is None
    assert repository.get_by_id(released, "t1", "dev").status == "RELEASED"
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("kind", ["bot", "session"])
def test_ticket_commit_failure_propagates_without_diagnostic_read(
    database, monkeypatch, kind
):
    manager, engine, _ = database()
    cls = OrmTicketRepository if kind == "bot" else OrmSessionTicketRepository
    update = update_bot_status if kind == "bot" else update_session_status
    repository = cls(manager)
    diagnostic = MagicMock()
    monkeypatch.setattr(repository, "get_by_transfer_id", diagnostic)

    @event.listens_for(engine, "commit")
    def fail_commit(_connection):
        raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        update(repository, "absent", "DONE")
    diagnostic.assert_not_called()
    assert engine.pool.checkedout() == 0
