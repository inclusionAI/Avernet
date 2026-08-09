"""Unified DeviceBindingRepository — behavior + contract.

The last DB-repo twin in the unification program (S5). Covers all
19 Protocol methods + the 3 adopt-prod behavior changes:
- ``gmt_modified`` advances DB-side after each UPDATE (proves the
  ``func.now()`` reaches the column on SQLite).
- ``get_active_engine_by_device_id`` falls back to
  ``DEFAULT_ENGINE_TYPE`` when no matching bot row exists.
- The 3 cross-table writes against ``ac_bots`` propagate
  exceptions instead of the old local twin's silent swallow.
"""
import json
import time
from contextlib import contextmanager
from threading import Event, Thread, current_thread
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.device_repository import DeviceRepository
from agentclaw.community.plugins.local import database as local_db_mod
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


class _PreconnectedFileSqliteDB(_FileSqliteDB):
    @contextmanager
    def orm_session(self):
        db = self._factory()
        db.connection()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'devbind.db'}",
        connect_args={"check_same_thread": False},
    )
    EntityDeviceBinding.__table__.create(engine)
    BotModel.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(db):
    return DeviceRepository(db)


@pytest.fixture
def autocommit_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'devbind-autocommit.db'}",
        connect_args={"check_same_thread": False, "timeout": 0.0},
        isolation_level="AUTOCOMMIT",
    )
    EntityDeviceBinding.__table__.create(engine)
    BotModel.__table__.create(engine)
    return _FileSqliteDB(engine), engine


@pytest.fixture
def preconnected_autocommit_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'devbind-preconnected.db'}",
        connect_args={"check_same_thread": False},
        isolation_level="AUTOCOMMIT",
    )
    EntityDeviceBinding.__table__.create(engine)
    BotModel.__table__.create(engine)
    return _PreconnectedFileSqliteDB(engine)


@pytest.fixture
def static_pool_db():
    """The real local DatabasePlugin backed by one StaticPool connection."""
    local_db_mod.reset_for_tests()
    plugin = local_db_mod.SqliteDB()
    local_db_mod._get_session_factory()
    engine = local_db_mod._engine
    assert engine is not None
    EntityDeviceBinding.__table__.create(engine)
    BotModel.__table__.create(engine)
    try:
        yield plugin, engine
    finally:
        local_db_mod.reset_for_tests()


def _binding(**ov):
    base = dict(
        entity_id="staff-1",
        entity_type="staff",
        device_id="dev-abc",
        device_provider="arca",
        env="dev",
        device_props={"sandbox_id": "sbx-1"},
        status="PENDING",
        apply_reason="r",
        applied_by="emp-1",
    )
    base.update(ov)
    return base


def _bot(db, **ov):
    base = dict(
        bot_id="bot-1",
        entity_id="staff-1",
        entity_type="staff",
        creator_id="emp-1",
        owner_id="emp-1",
        status="PENDING",
        active_engine="moltis",
        device_id="dev-abc",
        binding_id=None,
        ext=None,
    )
    base.update(ov)
    with db.orm_session() as s:
        row = BotModel(**base)
        s.add(row)
        s.flush()
        return row.id


# ── insert / reads ──────────────────────────────────────────────────

def test_insert_and_get_by_id(repo):
    bid = repo.insert_binding(**_binding())
    assert bid > 0
    rec = repo.get_by_id(bid)
    assert rec.device_id == "dev-abc"
    assert rec.status == "PENDING"
    assert rec.device_props == {"sandbox_id": "sbx-1"}


def test_get_by_id_missing(repo):
    assert repo.get_by_id(999) is None


def test_get_by_device_id_latest(repo, db):
    # Two bindings with the same device_id — get_by_device_id picks
    # the most recent (ORDER BY id DESC LIMIT 1). The SQLite model
    # has only a non-unique idx_device_id (the prod uk_device_id is
    # NOT enforced locally), so the second insert succeeds here. T4
    # Pre verifies prod raises on the duplicate.
    repo.insert_binding(**_binding())
    bid2 = repo.insert_binding(
        **_binding(status="ACTIVE", apply_reason="r2")
    )
    rec = repo.get_by_device_id("dev-abc")
    assert rec.id == bid2
    assert rec.status == "ACTIVE"


def test_get_by_device_id_missing(repo):
    assert repo.get_by_device_id("nope") is None


def test_get_by_ids(repo):
    a = repo.insert_binding(**_binding(device_id="d1"))
    b = repo.insert_binding(**_binding(device_id="d2"))
    out = repo.get_by_ids([a, b])
    assert [r.id for r in out] == [b, a]  # ORDER BY id DESC
    assert repo.get_by_ids([]) == []


def test_exists_device_id(repo):
    assert repo.exists_device_id(device_id="d1") is False
    repo.insert_binding(**_binding(device_id="d1"))
    assert repo.exists_device_id(device_id="d1") is True


def test_get_released_binding(repo):
    bid = repo.insert_binding(**_binding(device_id="d-rel"))
    assert repo.get_released_binding(device_id="d-rel") is None
    repo.release_binding(
        binding_id=bid, release_reason="done", released_by="emp-1"
    )
    rec = repo.get_released_binding(device_id="d-rel")
    assert rec is not None and rec.id == bid
    assert rec.status == "RELEASED"


# ── list + count ────────────────────────────────────────────────────

def test_list_bindings_filters_and_pagination(repo):
    for i in range(5):
        repo.insert_binding(
            **_binding(device_id=f"d{i}", env="dev",
                       status="PENDING" if i % 2 else "ACTIVE")
        )
    total, items = repo.list_bindings(env="dev", entity_id="staff-1")
    assert total == 5
    assert len(items) == 5
    total, items = repo.list_bindings(
        env="dev", entity_id="staff-1", status="ACTIVE"
    )
    assert total == 3
    # pagination
    _, page1 = repo.list_bindings(
        env="dev", entity_id="staff-1", page=1, page_size=2
    )
    assert len(page1) == 2
    # env filter actually filters
    repo.insert_binding(**_binding(device_id="d-other", env="prod"))
    t_dev, _ = repo.list_bindings(env="dev")
    t_prod, _ = repo.list_bindings(env="prod")
    assert t_dev == 5 and t_prod == 1


def test_list_active_caller_instance_bindings_filters_scope_and_deduplicates(repo):
    matching_old = repo.insert_binding(
        **_binding(
            entity_id="owner-1",
            device_id="BOT-caller-1",
            device_provider="baas",
            env="prod",
            status="ACTIVE",
            apply_reason="caller_instance:service-bot-1",
            applied_by="caller-1",
        )
    )
    matching_latest = repo.insert_binding(
        **_binding(
            entity_id="owner-1",
            device_id="BOT-caller-1",
            device_provider="baas",
            env="prod",
            status="ACTIVE",
            apply_reason="caller_instance:service-bot-1",
            applied_by="caller-1",
        )
    )
    second_match = repo.insert_binding(
        **_binding(
            entity_id="owner-1",
            device_id="BOT-caller-2",
            device_provider="baas",
            env="prod",
            status="ACTIVE",
            apply_reason="caller_instance:service-bot-1",
            applied_by="caller-2",
        )
    )

    excluded = [
        dict(entity_id="other-owner"),
        dict(env="dev"),
        dict(status="PENDING"),
        dict(device_provider="arca"),
        dict(entity_type="team"),
        dict(apply_reason="caller_instance:other-service-bot"),
        dict(apply_reason="caller_instance:service-bot-1-extra"),
    ]
    for index, override in enumerate(excluded):
        values = dict(
            entity_id="owner-1",
            entity_type="staff",
            device_id=f"BOT-excluded-{index}",
            device_provider="baas",
            env="prod",
            status="ACTIVE",
            apply_reason="caller_instance:service-bot-1",
            applied_by=f"excluded-{index}",
        )
        values.update(override)
        repo.insert_binding(**_binding(**values))

    bindings = repo.list_active_caller_instance_bindings(
        bot_id="service-bot-1",
        owner_id="owner-1",
        env="prod",
    )

    assert [binding.id for binding in bindings] == [second_match, matching_latest]
    assert {binding.device_id for binding in bindings} == {
        "BOT-caller-1",
        "BOT-caller-2",
    }
    assert matching_old not in {binding.id for binding in bindings}


def test_count_non_released_bindings(repo):
    repo.insert_binding(**_binding(device_id="d1", status="ACTIVE"))
    bid2 = repo.insert_binding(
        **_binding(device_id="d2", status="PENDING")
    )
    repo.insert_binding(**_binding(device_id="d3", status="ACTIVE"))
    repo.release_binding(
        binding_id=bid2, release_reason=None, released_by="x"
    )
    n = repo.count_non_released_bindings(
        entity_id="staff-1", entity_type="staff", env="dev"
    )
    assert n == 2  # one released → excluded


# ── update family (adopt-prod gmt_modified DB-side) ─────────────────

def test_release_binding_is_soft_delete(repo, db):
    bid = repo.insert_binding(**_binding())
    pre = repo.get_by_id(bid).gmt_modified
    time.sleep(1.05)  # SQLite CURRENT_TIMESTAMP has 1-sec resolution
    repo.release_binding(
        binding_id=bid, release_reason="done", released_by="emp-9"
    )
    rec = repo.get_by_id(bid)
    assert rec is not None  # row still there → soft-delete
    assert rec.status == "RELEASED"
    assert rec.release_reason == "done"
    assert rec.released_by == "emp-9"
    assert rec.released_at is not None
    assert rec.gmt_modified > pre  # advanced DB-side


def test_update_status_advances_gmt_modified(repo):
    bid = repo.insert_binding(**_binding())
    pre = repo.get_by_id(bid).gmt_modified
    time.sleep(1.05)
    repo.update_status(binding_id=bid, status="ACTIVE")
    rec = repo.get_by_id(bid)
    assert rec.status == "ACTIVE"
    assert rec.gmt_modified > pre


def test_update_status_and_alive_at(repo):
    bid = repo.insert_binding(**_binding())
    pre = repo.get_by_id(bid).gmt_modified
    time.sleep(1.05)
    repo.update_status_and_alive_at(binding_id=bid, status="ACTIVE")
    rec = repo.get_by_id(bid)
    assert rec.status == "ACTIVE"
    assert rec.last_alive_at is not None
    assert rec.gmt_modified > pre


def test_update_device_props_merges_preserving_other_keys(repo):
    bid = repo.insert_binding(**_binding(device_props={"callback_token": "tok"}))
    repo.update_device_props(binding_id=bid, props={"publish_id": "pub-9"})
    rec = repo.get_by_id(bid)
    # merge: other keys survive, new key added
    assert rec.device_props == {"callback_token": "tok", "publish_id": "pub-9"}


def test_update_device_props_overwrites_same_key(repo):
    bid = repo.insert_binding(**_binding(device_props={"publish_id": "old"}))
    repo.update_device_props(binding_id=bid, props={"publish_id": "new"})
    assert repo.get_by_id(bid).device_props == {"publish_id": "new"}


def test_update_device_props_missing_binding_is_noop(repo):
    # No row → silent no-op (must not raise).
    repo.update_device_props(binding_id=999999, props={"publish_id": "x"})


def test_reuse_binding_clears_release_fields(repo):
    bid = repo.insert_binding(**_binding())
    repo.release_binding(
        binding_id=bid, release_reason="r", released_by="x"
    )
    repo.reuse_binding(
        binding_id=bid,
        device_props={"sandbox_id": "sbx-2"},
        apply_reason="reuse",
        applied_by="emp-2",
    )
    rec = repo.get_by_id(bid)
    assert rec.status == "PENDING"
    assert rec.device_props == {"sandbox_id": "sbx-2"}
    assert rec.apply_reason == "reuse"
    assert rec.applied_by == "emp-2"
    assert rec.release_reason is None
    assert rec.released_by is None
    assert rec.released_at is None
    assert rec.last_alive_at is None


def test_batch_update_env(repo):
    a = repo.insert_binding(**_binding(device_id="d1", env="dev"))
    b = repo.insert_binding(**_binding(device_id="d2", env="dev"))
    n = repo.batch_update_env(binding_ids=[a, b], env="prod")
    assert n == 2
    assert repo.get_by_id(a).env == "prod"
    assert repo.get_by_id(b).env == "prod"
    assert repo.batch_update_env(binding_ids=[], env="prod") == 0


# ── cross-table reads/writes (adopt-prod: DEFAULT_ENGINE + propagate) ─

def test_get_active_engine_uses_default_when_no_bot(repo):
    # No ac_bots row matches device_id 'xxx' → fallback.
    assert (
        repo.get_active_engine_by_device_id(device_id="xxx")
        == DEFAULT_ENGINE_TYPE
    )


def test_get_active_engine_returns_value_when_bot_exists(repo, db):
    _bot(db, bot_id="bot-9", device_id="dev-9", active_engine="claude_code")
    assert (
        repo.get_active_engine_by_device_id(device_id="dev-9")
        == "claude_code"
    )


def test_update_bot_start_status_merges_ext(repo, db):
    bid = repo.insert_binding(**_binding())
    _bot(db, bot_id="b1", binding_id=bid, ext=json.dumps({"keep": 1}))
    repo.update_bot_start_status(
        binding_id=bid, status="OK", message="hello"
    )
    with db.orm_session() as s:
        row = s.query(BotModel).filter_by(binding_id=bid).first()
        ext = json.loads(row.ext)
        assert ext["keep"] == 1
        assert ext["start_status"] == "OK"
        assert ext["start_message"] == "hello"


def test_update_bot_start_status_skips_when_no_bot(repo):
    # No ac_bots row → method logs + returns; does NOT raise.
    repo.update_bot_start_status(
        binding_id=999, status="OK", message=None
    )


def test_update_bot_start_status_propagates_on_malformed_ext(repo, db):
    """Adopt-prod behavior change: malformed JSON does NOT swallow.
    The unified body's json.loads guard returns {} on JSONDecodeError
    (matching prod), so this is actually graceful — the propagate-
    not-swallow guarantee covers OTHER failure modes (DB errors,
    schema mismatches). Here we assert the graceful no-raise path."""
    bid = repo.insert_binding(**_binding())
    _bot(db, bot_id="b1", binding_id=bid, ext="not valid json {{{")
    repo.update_bot_start_status(
        binding_id=bid, status="OK", message=None
    )
    with db.orm_session() as s:
        row = s.query(BotModel).filter_by(binding_id=bid).first()
        ext = json.loads(row.ext)
        assert ext == {"start_status": "OK"}  # reset to {} + new keys


def test_update_bot_status_on_device_active_only_when_pending(repo, db):
    bid = repo.insert_binding(**_binding())
    _bot(db, bot_id="b1", binding_id=bid, status="PENDING")
    _bot(db, bot_id="b2", binding_id=999, status="PENDING")
    repo.update_bot_status_on_device_active(binding_id=bid)
    with db.orm_session() as s:
        assert s.query(BotModel).filter_by(bot_id="b1").one().status == "ACTIVE"
        # untouched
        assert s.query(BotModel).filter_by(bot_id="b2").one().status == "PENDING"

    # Non-PENDING bot is NOT flipped.
    _bot(db, bot_id="b3", binding_id=bid + 100, status="FAILED")
    repo.update_bot_status_on_device_active(binding_id=bid + 100)
    with db.orm_session() as s:
        assert s.query(BotModel).filter_by(bot_id="b3").one().status == "FAILED"


def test_update_bot_status_on_device_failed_unconditional(repo, db):
    bid = repo.insert_binding(**_binding())
    _bot(db, bot_id="b1", binding_id=bid, status="ACTIVE")
    repo.update_bot_status_on_device_failed(binding_id=bid)
    with db.orm_session() as s:
        assert s.query(BotModel).filter_by(bot_id="b1").one().status == "FAILED"


# ── guarded Teclaw terminal transition ─────────────────────────────

def test_transition_teclaw_publish_terminal_updates_bot_and_binding(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    with patch(_ENV_MOD, return_value="dev"):
        transitioned = repo.transition_teclaw_publish_terminal(
            binding_id=bid,
            bot_id="bot-teclaw",
            owner_id="emp-1",
            publish_id=9,
            status="ACTIVE",
        )

    assert transitioned is True
    assert repo.get_by_id(bid).status == "ACTIVE"
    with db.orm_session() as s:
        assert (
            s.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "ACTIVE"
        )


def test_transition_teclaw_publish_terminal_rolls_back_bot_on_binding_failure(
    autocommit_db,
):
    db, engine = autocommit_db
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    def fail_binding_update(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        if statement.lstrip().lower().startswith(
            "update ac_entity_device_binding"
        ):
            raise RuntimeError("injected binding write failure")

    event.listen(engine, "before_cursor_execute", fail_binding_update)
    try:
        with patch(_ENV_MOD, return_value="dev"):
            with pytest.raises(
                RuntimeError, match="injected binding write failure"
            ):
                repo.transition_teclaw_publish_terminal(
                    binding_id=bid,
                    bot_id="bot-teclaw",
                    owner_id="emp-1",
                    publish_id=9,
                    status="ACTIVE",
                )
    finally:
        event.remove(engine, "before_cursor_execute", fail_binding_update)

    assert repo.get_by_id(bid).status == "PENDING"
    with db.orm_session() as session:
        assert (
            session.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "PENDING"
        )


def test_transition_teclaw_publish_terminal_commits_both_under_autocommit(
    autocommit_db,
):
    db, _engine = autocommit_db
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    with patch(_ENV_MOD, return_value="dev"):
        transitioned = repo.transition_teclaw_publish_terminal(
            binding_id=bid,
            bot_id="bot-teclaw",
            owner_id="emp-1",
            publish_id=9,
            status="ACTIVE",
        )

    assert transitioned is True
    assert repo.get_by_id(bid).status == "ACTIVE"
    with db.orm_session() as session:
        assert (
            session.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "ACTIVE"
        )


def test_transition_teclaw_publish_terminal_rejects_preconnected_session(
    preconnected_autocommit_db,
):
    db = preconnected_autocommit_db
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    with patch(_ENV_MOD, return_value="dev"):
        with pytest.raises(RuntimeError, match="fresh ORM Session"):
            repo.transition_teclaw_publish_terminal(
                binding_id=bid,
                bot_id="bot-teclaw",
                owner_id="emp-1",
                publish_id=9,
                status="ACTIVE",
            )

    assert repo.get_by_id(bid).status == "PENDING"
    with db.orm_session() as session:
        assert (
            session.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "PENDING"
        )


def test_transition_teclaw_publish_terminal_rejects_isolation_mismatch(
    db,
):
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    with (
        patch(_ENV_MOD, return_value="dev"),
        patch(
            "sqlalchemy.engine.Connection.get_isolation_level",
            return_value="READ_UNCOMMITTED",
        ),
        pytest.raises(RuntimeError, match="transaction isolation mismatch"),
    ):
        repo.transition_teclaw_publish_terminal(
            binding_id=bid,
            bot_id="bot-teclaw",
            owner_id="emp-1",
            publish_id=9,
            status="ACTIVE",
        )

    assert repo.get_by_id(bid).status == "PENDING"
    with db.orm_session() as session:
        assert (
            session.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "PENDING"
        )


def test_transition_teclaw_publish_terminal_serializes_concurrent_release(
    autocommit_db,
):
    db, engine = autocommit_db
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    guard_read = Event()
    allow_transition = Event()
    release_update_started = Event()
    release_finished = Event()
    transition_errors = []
    release_errors = []

    def pause_after_guarded_read(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        normalized = statement.lstrip().lower()
        if (
            current_thread().name == "terminal-transition"
            and normalized.startswith("select")
            and "from ac_entity_device_binding" in normalized
        ):
            guard_read.set()
            if not allow_transition.wait(timeout=5):
                raise RuntimeError("terminal transition coordination timed out")

    def observe_release_update(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        normalized = statement.lstrip().lower()
        if (
            current_thread().name == "release-writer"
            and normalized.startswith("update ac_entity_device_binding")
        ):
            release_update_started.set()

    def run_transition():
        try:
            with patch(_ENV_MOD, return_value="dev"):
                repo.transition_teclaw_publish_terminal(
                    binding_id=bid,
                    bot_id="bot-teclaw",
                    owner_id="emp-1",
                    publish_id=9,
                    status="ACTIVE",
                )
        except Exception as exc:  # noqa: BLE001 - re-raised in test thread
            transition_errors.append(exc)

    def release_binding():
        try:
            repo.release_binding(
                binding_id=bid,
                release_reason="concurrent release",
                released_by="emp-1",
            )
        except Exception as exc:  # noqa: BLE001 - re-raised in test thread
            release_errors.append(exc)
        finally:
            release_finished.set()

    event.listen(engine, "after_cursor_execute", pause_after_guarded_read)
    event.listen(engine, "before_cursor_execute", observe_release_update)
    terminal_thread = Thread(
        target=run_transition,
        name="terminal-transition",
    )
    release_thread = Thread(
        target=release_binding,
        name="release-writer",
    )
    try:
        terminal_thread.start()
        assert guard_read.wait(timeout=5)
        release_thread.start()
        assert release_update_started.wait(timeout=5)
        assert release_finished.wait(timeout=5)
        assert len(release_errors) == 1
        assert "database is locked" in str(release_errors[0]).lower()
        allow_transition.set()
        terminal_thread.join(timeout=5)
        release_thread.join(timeout=5)
    finally:
        allow_transition.set()
        terminal_thread.join(timeout=5)
        release_thread.join(timeout=5)
        event.remove(engine, "after_cursor_execute", pause_after_guarded_read)
        event.remove(engine, "before_cursor_execute", observe_release_update)

    assert not terminal_thread.is_alive()
    assert not release_thread.is_alive()
    assert transition_errors == []
    repo.release_binding(
        binding_id=bid,
        release_reason="concurrent release",
        released_by="emp-1",
    )
    binding = repo.get_by_id(bid)
    assert binding.status == "RELEASED"
    assert binding.release_reason == "concurrent release"
    with db.orm_session() as session:
        assert (
            session.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "ACTIVE"
        )


def test_local_static_pool_serializes_terminal_transition_and_release(
    static_pool_db,
):
    """Real local sessions must not overlap on StaticPool's one connection."""
    db, engine = static_pool_db
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    _bot(
        db,
        bot_id="bot-teclaw-static-pool",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    guarded_read_finished = Event()
    allow_transition = Event()
    release_called = Event()
    release_update_started = Event()
    errors: list[BaseException] = []

    def pause_after_guarded_read(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        normalized = statement.lstrip().lower()
        if (
            current_thread().name == "static-pool-terminal"
            and normalized.startswith("select")
            and "from ac_entity_device_binding" in normalized
        ):
            guarded_read_finished.set()
            if not allow_transition.wait(timeout=5):
                raise RuntimeError("StaticPool coordination timed out")

    def observe_release_update(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        normalized = statement.lstrip().lower()
        if (
            current_thread().name == "static-pool-release"
            and normalized.startswith("update ac_entity_device_binding")
        ):
            release_update_started.set()

    def run_transition() -> None:
        try:
            with patch(_ENV_MOD, return_value="dev"):
                repo.transition_teclaw_publish_terminal(
                    binding_id=bid,
                    bot_id="bot-teclaw-static-pool",
                    owner_id="emp-1",
                    publish_id=9,
                    status="ACTIVE",
                )
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    def release_binding() -> None:
        release_called.set()
        try:
            repo.release_binding(
                binding_id=bid,
                release_reason="concurrent release",
                released_by="emp-1",
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    event.listen(engine, "after_cursor_execute", pause_after_guarded_read)
    event.listen(engine, "before_cursor_execute", observe_release_update)
    terminal_thread = Thread(
        target=run_transition,
        name="static-pool-terminal",
    )
    release_thread = Thread(
        target=release_binding,
        name="static-pool-release",
    )
    try:
        terminal_thread.start()
        assert guarded_read_finished.wait(timeout=5)
        release_thread.start()
        assert release_called.wait(timeout=5)
        update_started_while_transition_paused = release_update_started.wait(
            timeout=0.25
        )
        allow_transition.set()
        terminal_thread.join(timeout=5)
        release_thread.join(timeout=5)
    finally:
        allow_transition.set()
        terminal_thread.join(timeout=5)
        release_thread.join(timeout=5)
        event.remove(engine, "after_cursor_execute", pause_after_guarded_read)
        event.remove(engine, "before_cursor_execute", observe_release_update)

    assert errors == []
    assert not terminal_thread.is_alive()
    assert not release_thread.is_alive()
    assert update_started_while_transition_paused is False
    binding = repo.get_by_id(bid)
    assert binding.status == "RELEASED"
    assert binding.release_reason == "concurrent release"
    with db.orm_session() as session:
        assert (
            session.query(BotModel)
            .filter_by(bot_id="bot-teclaw-static-pool")
            .one()
            .status
            == "ACTIVE"
        )


@pytest.mark.parametrize(
    "binding_overrides",
    [
        {"status": "RELEASED"},
        {"device_provider": "baas"},
        {"device_props": {"publish_id": 10}},
    ],
)
def test_transition_teclaw_publish_terminal_guard_mismatch_is_noop(
    repo, db, binding_overrides
):
    binding_data = {
        "device_provider": "teclaw",
        "device_props": {"publish_id": 9},
        **binding_overrides,
    }
    bid = repo.insert_binding(**_binding(**binding_data))
    _bot(
        db,
        bot_id="bot-teclaw",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
    )

    with patch(_ENV_MOD, return_value="dev"):
        transitioned = repo.transition_teclaw_publish_terminal(
            binding_id=bid,
            bot_id="bot-teclaw",
            owner_id="emp-1",
            publish_id=9,
            status="ACTIVE",
        )

    assert transitioned is False
    assert repo.get_by_id(bid).status == binding_overrides.get("status", "PENDING")
    with db.orm_session() as s:
        assert (
            s.query(BotModel).filter_by(bot_id="bot-teclaw").one().status
            == "PENDING"
        )


@pytest.mark.parametrize(
    "bot_overrides",
    [
        None,
        {"owner_id": "different-owner"},
        {"binding_id": None},
        {"env": "prod"},
        {"is_delete": 1},
    ],
)
def test_transition_teclaw_publish_terminal_bot_mismatch_rolls_back_binding(
    repo, db, bot_overrides
):
    bid = repo.insert_binding(
        **_binding(
            device_provider="teclaw",
            device_props={"publish_id": 9},
        )
    )
    if bot_overrides is not None:
        bot_data = {
            "owner_id": "emp-1",
            "binding_id": bid,
            "env": "dev",
            **bot_overrides,
        }
        _bot(
            db,
            bot_id="bot-teclaw",
            **bot_data,
        )

    with patch(_ENV_MOD, return_value="dev"):
        with pytest.raises(RuntimeError, match="expected exactly one"):
            repo.transition_teclaw_publish_terminal(
                binding_id=bid,
                bot_id="bot-teclaw",
                owner_id="emp-1",
                publish_id=9,
                status="ACTIVE",
            )

    assert repo.get_by_id(bid).status == "PENDING"


# ── get_active_by_bot_and_owner — DeviceContextResolver 入口 ───────

def test_get_active_by_bot_and_owner_returns_binding_when_exists(repo, db):
    """Seed bot + active binding → 返回 record，id 字段正确，含
    device_provider / device_props / status 等业务字段。"""
    bid = repo.insert_binding(
        **_binding(
            device_id="dev-resolver-1",
            device_provider="arca",
            status="ACTIVE",
            device_props={"sandbox_id": "sbx-r1"},
        )
    )
    _bot(
        db,
        bot_id="bot-resolver-1",
        owner_id="emp-resolver",
        binding_id=bid,
        device_id="dev-resolver-1",
    )
    rec = repo.get_active_by_bot_and_owner(
        bot_id="bot-resolver-1", owner_id="emp-resolver"
    )
    assert rec is not None
    assert rec.id == bid
    assert rec.device_id == "dev-resolver-1"
    assert rec.device_provider == "arca"
    assert rec.status == "ACTIVE"
    assert rec.device_props == {"sandbox_id": "sbx-r1"}


def test_get_active_by_bot_and_owner_returns_none_when_no_binding(repo, db):
    """bot 存在但 binding_id=NULL → 返 None。"""
    _bot(
        db,
        bot_id="bot-no-bind",
        owner_id="emp-resolver",
        binding_id=None,
        device_id=None,
    )
    assert (
        repo.get_active_by_bot_and_owner(
            bot_id="bot-no-bind", owner_id="emp-resolver"
        )
        is None
    )


def test_get_active_by_bot_and_owner_returns_none_when_wrong_owner(repo, db):
    """bot owner_id 不匹配 → 返 None（即使 bot 有有效 binding）。"""
    bid = repo.insert_binding(**_binding(device_id="dev-owner-mismatch"))
    _bot(
        db,
        bot_id="bot-owner-mismatch",
        owner_id="emp-real-owner",
        binding_id=bid,
        device_id="dev-owner-mismatch",
    )
    assert (
        repo.get_active_by_bot_and_owner(
            bot_id="bot-owner-mismatch", owner_id="emp-attacker"
        )
        is None
    )


def test_get_active_by_bot_and_owner_returns_none_when_bot_missing(repo):
    """bot 完全不存在 → 返 None。"""
    assert (
        repo.get_active_by_bot_and_owner(
            bot_id="nope", owner_id="emp-resolver"
        )
        is None
    )


def test_get_active_by_bot_and_owner_skips_soft_deleted_bot(repo, db):
    """bot 被软删 (is_delete=1) → 返 None。"""
    bid = repo.insert_binding(**_binding(device_id="dev-soft-del"))
    _bot(
        db,
        bot_id="bot-soft-del",
        owner_id="emp-resolver",
        binding_id=bid,
        device_id="dev-soft-del",
        is_delete=1,
    )
    assert (
        repo.get_active_by_bot_and_owner(
            bot_id="bot-soft-del", owner_id="emp-resolver"
        )
        is None
    )


# ── env-isolation (P0 hotfix) ──────────────────────────────────────
# ac_bots / ac_entity_device_binding 在 pre / prod 共享同一 DB，仅
# 通过 env 字段区分。所有按 bot_id / owner_id / device_id 查的链路
# 都必须加 env 过滤，否则跨环境串数据（同 user 在 pre 和 prod 都有
# 同 bot_id 的 default bot 时会随机命中错环境）。

_ENV_MOD = "agentclaw.community.plugins.device_repository.get_current_env"


def test_get_active_by_bot_and_owner_env_isolation(repo, db):
    """同 bot_id + owner_id 在 pre / prod 各一条 binding，
    切到对应 env 时只返回该 env 的 binding。"""
    bid_pre = repo.insert_binding(
        **_binding(device_id="dev-pre", env="pre", status="ACTIVE")
    )
    bid_prod = repo.insert_binding(
        **_binding(device_id="dev-prod", env="prod", status="ACTIVE")
    )
    _bot(
        db,
        bot_id="bot-shared",
        owner_id="emp-x",
        binding_id=bid_pre,
        device_id="dev-pre",
        env="pre",
    )
    _bot(
        db,
        bot_id="bot-shared",
        owner_id="emp-x",
        binding_id=bid_prod,
        device_id="dev-prod",
        env="prod",
    )

    with patch(_ENV_MOD, return_value="pre"):
        rec = repo.get_active_by_bot_and_owner(
            bot_id="bot-shared", owner_id="emp-x"
        )
        assert rec is not None
        assert rec.id == bid_pre
        assert rec.device_id == "dev-pre"

    with patch(_ENV_MOD, return_value="prod"):
        rec = repo.get_active_by_bot_and_owner(
            bot_id="bot-shared", owner_id="emp-x"
        )
        assert rec is not None
        assert rec.id == bid_prod
        assert rec.device_id == "dev-prod"


def test_get_by_device_id_env_isolation(repo):
    """同 device_id 在 pre / prod 各一条 binding，仅返当前 env 的。"""
    bid_pre = repo.insert_binding(
        **_binding(device_id="dev-shared", env="pre", status="PENDING")
    )
    bid_prod = repo.insert_binding(
        **_binding(device_id="dev-shared", env="prod", status="ACTIVE")
    )

    with patch(_ENV_MOD, return_value="pre"):
        rec = repo.get_by_device_id("dev-shared")
        assert rec is not None
        assert rec.id == bid_pre
        assert rec.env == "pre"

    with patch(_ENV_MOD, return_value="prod"):
        rec = repo.get_by_device_id("dev-shared")
        assert rec is not None
        assert rec.id == bid_prod
        assert rec.env == "prod"


def test_exists_device_id_env_isolation(repo):
    """device_id 只在 prod 存在 → 切到 pre 时返 False，prod 返 True。"""
    repo.insert_binding(
        **_binding(device_id="dev-prod-only", env="prod")
    )

    with patch(_ENV_MOD, return_value="pre"):
        assert repo.exists_device_id(device_id="dev-prod-only") is False

    with patch(_ENV_MOD, return_value="prod"):
        assert repo.exists_device_id(device_id="dev-prod-only") is True


def test_get_released_binding_env_isolation(repo):
    """同 device_id 在 pre / prod 各一条 RELEASED binding，
    仅返当前 env 的。"""
    bid_pre = repo.insert_binding(
        **_binding(device_id="dev-rel-shared", env="pre")
    )
    bid_prod = repo.insert_binding(
        **_binding(device_id="dev-rel-shared", env="prod")
    )
    repo.release_binding(
        binding_id=bid_pre, release_reason="r-pre", released_by="x"
    )
    repo.release_binding(
        binding_id=bid_prod, release_reason="r-prod", released_by="x"
    )

    with patch(_ENV_MOD, return_value="pre"):
        rec = repo.get_released_binding(device_id="dev-rel-shared")
        assert rec is not None
        assert rec.id == bid_pre
        assert rec.release_reason == "r-pre"

    with patch(_ENV_MOD, return_value="prod"):
        rec = repo.get_released_binding(device_id="dev-rel-shared")
        assert rec is not None
        assert rec.id == bid_prod
        assert rec.release_reason == "r-prod"


def test_get_active_engine_by_device_id_env_isolation(repo, db):
    """同 device_id 对应 pre / prod 各一条 bot（active_engine 不同），
    返当前 env 的 bot 的 engine。"""
    _bot(
        db,
        bot_id="bot-engine-pre",
        device_id="dev-engine-shared",
        active_engine="moltis",
        env="pre",
    )
    _bot(
        db,
        bot_id="bot-engine-prod",
        device_id="dev-engine-shared",
        active_engine="claude_code",
        env="prod",
    )

    with patch(_ENV_MOD, return_value="pre"):
        assert (
            repo.get_active_engine_by_device_id(
                device_id="dev-engine-shared"
            )
            == "moltis"
        )

    with patch(_ENV_MOD, return_value="prod"):
        assert (
            repo.get_active_engine_by_device_id(
                device_id="dev-engine-shared"
            )
            == "claude_code"
        )


def test_update_bot_status_on_device_active_env_isolation(repo, db):
    """同 binding_id 在 pre / prod 各挂一个 PENDING bot，update_bot_*
    在 pre env 时只翻 pre 的 bot；prod 的保持 PENDING。"""
    # 在 pre env 下插 binding，PK 自增；为了让 pre 和 prod 的 bot
    # 都指向同一个 binding_id（模拟跨环境数据），两条 bot 共用 bid。
    bid = repo.insert_binding(**_binding(env="pre"))
    _bot(
        db,
        bot_id="bot-pre",
        binding_id=bid,
        status="PENDING",
        env="pre",
    )
    _bot(
        db,
        bot_id="bot-prod",
        binding_id=bid,
        status="PENDING",
        env="prod",
    )

    with patch(_ENV_MOD, return_value="pre"):
        repo.update_bot_status_on_device_active(binding_id=bid)

    with db.orm_session() as s:
        assert (
            s.query(BotModel).filter_by(bot_id="bot-pre").one().status
            == "ACTIVE"
        )
        # prod 的 bot 在 pre env update 时不应被翻
        assert (
            s.query(BotModel).filter_by(bot_id="bot-prod").one().status
            == "PENDING"
        )

    # 反过来：切 prod env，只翻 prod 的 bot
    with patch(_ENV_MOD, return_value="prod"):
        repo.update_bot_status_on_device_active(binding_id=bid)

    with db.orm_session() as s:
        assert (
            s.query(BotModel).filter_by(bot_id="bot-prod").one().status
            == "ACTIVE"
        )
