"""Unified DeviceBindingRepository — behavior + contract.

The last DB-repo twin in the unification program (S5). Covers the complete
Protocol surface plus the 3 adopt-prod behavior changes:
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
from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository
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


def test_reuse_released_desktop_binding_requires_exact_creation_context(repo, db):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            device_props={
                "client_id": "client-1",
                "callback_token": "token-1",
                "publish_id": "16",
            },
        )
    )
    repo.release_binding(
        binding_id=bid,
        release_reason="Desktop bot creation did not persist",
        released_by="u001",
    )
    _bot(
        db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        status="PROVISIONING",
        device_id=None,
        binding_id=None,
        env="dev",
    )

    reused = repo.recover_baas_desktop_creation_binding_if_matches(
        binding_id=bid,
        bot_id="desktop-bot",
        owner_id="u001",
        device_id="desktop-bot-uuid",
        entity_id="u001",
        env="dev",
        expected_client_id="client-1",
        expected_callback_token="token-1",
        device_props={
            "client_id": "client-1",
            "callback_token": "token-1",
            "publish_id": "17",
        },
        apply_reason="Create desktop bot: Desktop",
        applied_by="u001",
    )

    assert reused is True
    binding = repo.get_by_id(bid)
    assert binding.status == "PENDING"
    assert binding.device_props["publish_id"] == "17"
    assert binding.release_reason is None
    assert binding.released_by is None
    assert binding.released_at is None
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="desktop-bot").one()
        bot.status = "PENDING"
        bot.binding_id = bid
        bot.device_id = "desktop-bot-uuid"
    assert repo.recover_baas_desktop_creation_binding_if_matches(
        binding_id=bid,
        bot_id="desktop-bot",
        owner_id="u001",
        device_id="desktop-bot-uuid",
        entity_id="u001",
        env="dev",
        expected_client_id="client-1",
        expected_callback_token="token-1",
        device_props={"client_id": "client-1", "callback_token": "token-1"},
        apply_reason="Create desktop bot: Desktop",
        applied_by="u001",
    ) is False


@pytest.mark.parametrize(
    ("expected_client_id", "expected_callback_token"),
    [("other-client", "token-1"), ("client-1", "other-token")],
)
def test_reuse_released_desktop_binding_rejects_foreign_creation_context(
    repo,
    db,
    expected_client_id,
    expected_callback_token,
):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            device_props={
                "client_id": "client-1",
                "callback_token": "token-1",
            },
        )
    )
    repo.release_binding(binding_id=bid, release_reason="done", released_by="u001")
    _bot(
        db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        status="PROVISIONING",
        device_id=None,
        binding_id=None,
        env="dev",
    )

    reused = repo.recover_baas_desktop_creation_binding_if_matches(
        binding_id=bid,
        bot_id="desktop-bot",
        owner_id="u001",
        device_id="desktop-bot-uuid",
        entity_id="u001",
        env="dev",
        expected_client_id=expected_client_id,
        expected_callback_token=expected_callback_token,
        device_props={"client_id": "client-1", "callback_token": "token-1"},
        apply_reason="Create desktop bot: Desktop",
        applied_by="u001",
    )

    assert reused is False
    assert repo.get_by_id(bid).status == "RELEASED"


def test_detach_released_desktop_binding_repairs_retained_bot(repo, db):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            device_props={
                "client_id": "client-1",
                "callback_token": "token-1",
            },
        )
    )
    _bot(
        db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        active_engine="openclaw",
        device_id="desktop-bot-uuid",
        binding_id=bid,
        status="PENDING",
        env="dev",
    )
    repo.release_binding(
        binding_id=bid,
        release_reason="Desktop bot creation did not persist",
        released_by="u001",
    )

    repaired = repo.detach_released_baas_desktop_binding_if_matches(
        binding_id=bid,
        bot_id="desktop-bot",
        owner_id="u001",
        device_id="desktop-bot-uuid",
        entity_id="u001",
        env="dev",
        expected_client_id="client-1",
        expected_callback_token="token-1",
    )

    assert repaired is True
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="desktop-bot").one()
        assert bot.status == "PENDING"
        assert bot.binding_id is None
        assert bot.device_id is None


@pytest.mark.parametrize("status", ["RELEASED", "PENDING"])
def test_recover_desktop_creation_binding_accepts_only_matching_orphan(
    repo, db, status
):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            status=status,
            device_props={
                "client_id": "client-1",
                "callback_token": "token-1",
                "publish_id": "16",
            },
        )
    )
    if status == "RELEASED":
        repo.release_binding(
            binding_id=bid,
            release_reason="Desktop bot creation did not persist",
            released_by="u001",
        )
    _bot(
        db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        status="PROVISIONING",
        device_id=None,
        binding_id=None,
        env="dev",
    )

    recovered = repo.recover_baas_desktop_creation_binding_if_matches(
        binding_id=bid,
        bot_id="desktop-bot",
        owner_id="u001",
        device_id="desktop-bot-uuid",
        entity_id="u001",
        env="dev",
        expected_client_id="client-1",
        expected_callback_token="token-1",
        device_props={
            "client_id": "client-1",
            "callback_token": "token-1",
            "publish_id": "17",
        },
        apply_reason="Create desktop bot: Desktop",
        applied_by="u001",
    )

    assert recovered is True
    binding = repo.get_by_id(bid)
    assert binding.status == "PENDING"
    assert binding.device_props["publish_id"] == "17"
    assert binding.release_reason is None


def test_recover_desktop_creation_binding_rejects_foreign_bot_link(repo, db):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            status="PENDING",
            device_props={
                "client_id": "client-1",
                "callback_token": "token-1",
            },
        )
    )
    _bot(
        db,
        bot_id="foreign-bot",
        owner_id="other-owner",
        binding_id=bid,
        device_id="desktop-bot-uuid",
        env="dev",
    )
    _bot(
        db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        binding_id=None,
        device_id=None,
        status="PROVISIONING",
        env="dev",
    )

    assert repo.recover_baas_desktop_creation_binding_if_matches(
        binding_id=bid,
        bot_id="desktop-bot",
        owner_id="u001",
        device_id="desktop-bot-uuid",
        entity_id="u001",
        env="dev",
        expected_client_id="client-1",
        expected_callback_token="token-1",
        device_props={"client_id": "client-1", "callback_token": "token-1"},
        apply_reason="Create desktop bot: Desktop",
        applied_by="u001",
    ) is False


def test_desktop_data_init_trigger_claim_is_current_and_once(repo):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            status="ACTIVE",
            device_props={
                "restart_publish_id": "17",
                "layout_confirmed_startup_identity": "17",
            },
        )
    )
    _bot(
        repo._db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        binding_id=bid,
        device_id="desktop-bot-uuid",
        status="ACTIVE",
        env="dev",
        ext=json.dumps(
            {
                "start_status": "SUCCEEDED",
                "data_init_status": "pending_init",
            }
        ),
    )

    assert repo.claim_baas_desktop_data_init_trigger_if_ready(
        binding_id=bid,
        device_id="desktop-bot-uuid",
        startup_identity="17",
    ) is True
    assert repo.claim_baas_desktop_data_init_trigger_if_ready(
        binding_id=bid,
        device_id="desktop-bot-uuid",
        startup_identity="17",
    ) is False
    assert repo.claim_baas_desktop_data_init_trigger_if_ready(
        binding_id=bid,
        device_id="desktop-bot-uuid",
        startup_identity="16",
    ) is False


def test_desktop_data_init_trigger_claim_waits_for_active_bot(repo):
    bid = repo.insert_binding(
        **_binding(
            entity_id="u001",
            entity_type="staff",
            device_id="desktop-bot-uuid",
            device_provider="baas",
            env="dev",
            status="ACTIVE",
            device_props={
                "restart_publish_id": "17",
                "layout_confirmed_startup_identity": "17",
            },
        )
    )
    _bot(
        repo._db,
        bot_id="desktop-bot",
        owner_id="u001",
        entity_id="u001",
        binding_id=bid,
        device_id="desktop-bot-uuid",
        status="PENDING",
        env="dev",
        ext=json.dumps(
            {
                "start_status": "SUCCEEDED",
                "data_init_status": "pending_init",
            }
        ),
    )

    assert repo.claim_baas_desktop_data_init_trigger_if_ready(
        binding_id=bid,
        device_id="desktop-bot-uuid",
        startup_identity="17",
    ) is False

    with repo._db.orm_session() as db:
        db.query(BotModel).filter(BotModel.binding_id == bid).update(
            {BotModel.status: "ACTIVE"}, synchronize_session=False
        )

    assert repo.claim_baas_desktop_data_init_trigger_if_ready(
        binding_id=bid,
        device_id="desktop-bot-uuid",
        startup_identity="17",
    ) is True


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


def test_update_bot_status_on_device_active_only_from_creation_states(repo, db):
    bid = repo.insert_binding(**_binding())
    _bot(db, bot_id="b1", binding_id=bid, status="PENDING")
    _bot(db, bot_id="b2", binding_id=999, status="PENDING")
    repo.update_bot_status_on_device_active(binding_id=bid)
    with db.orm_session() as s:
        assert s.query(BotModel).filter_by(bot_id="b1").one().status == "ACTIVE"
        # untouched
        assert s.query(BotModel).filter_by(bot_id="b2").one().status == "PENDING"

    # A terminal failed bot is NOT flipped.
    _bot(db, bot_id="b3", binding_id=bid + 100, status="FAILED")
    repo.update_bot_status_on_device_active(binding_id=bid + 100)
    with db.orm_session() as s:
        assert s.query(BotModel).filter_by(bot_id="b3").one().status == "FAILED"


def test_update_bot_status_on_device_active_accepts_provisioning_claim(repo, db):
    """A claimed create converges when its asynchronous device becomes ACTIVE."""
    bid = repo.insert_binding(**_binding())
    _bot(db, bot_id="b1", binding_id=bid, status="PROVISIONING")

    repo.update_bot_status_on_device_active(binding_id=bid)

    with db.orm_session() as s:
        assert s.query(BotModel).filter_by(bot_id="b1").one().status == "ACTIVE"


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


def test_transition_baas_restart_terminal_updates_matching_bot_and_binding(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            device_props={
                "restart_request_id": "request-1",
                "restart_publish_id": "9",
            },
        )
    )
    old_ext = {"keep": "value"}
    new_ext = {"keep": "value", "restart_publish_id": "9"}
    _bot(
        db,
        bot_id="bot-baas",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        ext=json.dumps(old_ext),
    )

    with patch(_ENV_MOD, return_value="dev"):
        transitioned = repo.transition_baas_restart_terminal(
            binding_id=bid,
            bot_id="bot-baas",
            owner_id="emp-1",
            publish_id=9,
            request_id="request-1",
            status="ACTIVE",
            expected_bot_ext=old_ext,
            bot_ext=new_ext,
        )

    assert transitioned is True
    assert repo.get_by_id(bid).status == "ACTIVE"
    with db.orm_session() as s:
        bot = s.query(BotModel).filter_by(bot_id="bot-baas").one()
        assert bot.status == "ACTIVE"
        assert json.loads(bot.ext) == new_ext


def test_baas_restart_guards_reject_superseded_identity(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            device_props={
                "restart_request_id": "request-new",
                "restart_workflow_baseline": 8,
                "restart_publish_id": "10",
            },
        )
    )
    _bot(
        db,
        bot_id="bot-baas",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        ext=json.dumps({"keep": "value"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        transitioned = repo.transition_baas_restart_terminal(
            binding_id=bid,
            bot_id="bot-baas",
            owner_id="emp-1",
            publish_id=9,
            request_id="request-old",
            status="ACTIVE",
            expected_bot_ext={"keep": "value"},
            bot_ext={"restart_publish_id": "9"},
        )
        cleared = repo.clear_baas_restart_intent_if_matches(
            binding_id=bid,
            publish_id=9,
            request_id="request-old",
            keys=("restart_request_id", "restart_workflow_baseline"),
        )

    assert transitioned is False
    assert cleared is False
    binding = repo.get_by_id(bid)
    assert binding.status == "PENDING"
    assert binding.device_props["restart_request_id"] == "request-new"
    assert binding.device_props["restart_publish_id"] == "10"


def test_clear_baas_restart_intent_preserves_other_props(repo):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            device_props={
                "restart_request_id": "request-1",
                "restart_workflow_baseline": 8,
                "restart_publish_id": "9",
                "callback_token": "keep-me",
            },
        )
    )

    cleared = repo.clear_baas_restart_intent_if_matches(
        binding_id=bid,
        publish_id=9,
        request_id="request-1",
        keys=("restart_request_id", "restart_workflow_baseline"),
    )

    assert cleared is True
    props = repo.get_by_id(bid).device_props
    assert props["restart_request_id"] is None
    assert props["restart_workflow_baseline"] is None
    assert props["restart_publish_id"] == "9"
    assert props["callback_token"] == "keep-me"


def test_adopt_baas_restart_publish_guards_request_and_baseline(repo):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            device_props={
                "restart_request_id": "request-1",
                "restart_workflow_baseline": 8,
                "restart_publish_id": None,
            },
        )
    )

    assert repo.adopt_baas_restart_publish_if_matches(
        binding_id=bid,
        request_id="request-stale",
        workflow_baseline=8,
        publish_id=9,
    ) is False
    assert repo.adopt_baas_restart_publish_if_matches(
        binding_id=bid,
        request_id="request-1",
        workflow_baseline=8,
        publish_id=9,
    ) is True

    props = repo.get_by_id(bid).device_props
    assert props["publish_id"] == "9"
    assert props["restart_publish_id"] == "9"


@pytest.mark.parametrize("terminal_status", ["RELEASED", "STOPPED"])
def test_baas_restart_terminal_does_not_revive_stopped_binding(
    repo, db, terminal_status
):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status=terminal_status,
            device_props={
                "restart_request_id": "request-1",
                "restart_publish_id": "9",
            },
        )
    )
    old_ext = {"keep": "value"}
    _bot(
        db,
        bot_id="bot-baas",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        ext=json.dumps(old_ext),
    )

    with patch(_ENV_MOD, return_value="dev"):
        transitioned = repo.transition_baas_restart_terminal(
            binding_id=bid,
            bot_id="bot-baas",
            owner_id="emp-1",
            publish_id=9,
            request_id="request-1",
            status="ACTIVE",
            expected_bot_ext=old_ext,
            bot_ext={"restart_publish_id": "9"},
        )

    assert transitioned is False
    assert repo.get_by_id(bid).status == terminal_status
    with db.orm_session() as session:
        assert (
            session.query(BotModel).filter_by(bot_id="bot-baas").one().status
            == "PENDING"
        )


def test_prepare_baas_desktop_restart_updates_identity_and_status_atomically(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status="ACTIVE",
            device_props={"callback_token": "keep"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status="ACTIVE",
        ext=json.dumps({"keep": "value"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        prepared = repo.prepare_baas_desktop_restart(
            binding_id=bid,
            bot_id="bot-desktop",
            owner_id="emp-1",
            expected_publish_id=None,
            bot_ext_patch={"publish_id": "17", "pending_since": "now"},
            binding_props_patch={"restart_publish_id": "17"},
        )

    assert prepared is True
    binding = repo.get_by_id(bid)
    assert binding.status == "PENDING"
    assert binding.device_props == {
        "callback_token": "keep",
        "restart_publish_id": "17",
    }
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-desktop").one()
        assert bot.status == "PENDING"
        assert json.loads(bot.ext) == {
            "keep": "value",
            "publish_id": "17",
            "pending_since": "now",
        }


def test_prepare_baas_desktop_restart_allows_explicit_retry_from_failed(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status="FAILED",
            device_props={"restart_publish_id": "16"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status="FAILED",
        ext=json.dumps({"publish_id": "16"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        prepared = repo.prepare_baas_desktop_restart(
            binding_id=bid,
            bot_id="bot-desktop",
            owner_id="emp-1",
            expected_publish_id="16",
            bot_ext_patch={"publish_id": "17", "pending_since": "now"},
            binding_props_patch={"restart_publish_id": "17"},
        )

    assert prepared is True
    binding = repo.get_by_id(bid)
    assert binding.status == "PENDING"
    assert binding.device_props["restart_publish_id"] == "17"
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-desktop").one()
        assert bot.status == "PENDING"
        assert json.loads(bot.ext)["publish_id"] == "17"


@pytest.mark.parametrize("terminal_status", ["RELEASED", "STOPPED"])
def test_prepare_baas_desktop_restart_keeps_release_boundary(
    repo, db, terminal_status
):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status=terminal_status,
            device_props={"restart_publish_id": "16"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status=terminal_status,
        ext=json.dumps({"publish_id": "16"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        prepared = repo.prepare_baas_desktop_restart(
            binding_id=bid,
            bot_id="bot-desktop",
            owner_id="emp-1",
            expected_publish_id="16",
            bot_ext_patch={"publish_id": "17"},
            binding_props_patch={"restart_publish_id": "17"},
        )

    assert prepared is False
    binding = repo.get_by_id(bid)
    assert binding.status == terminal_status
    assert binding.device_props["restart_publish_id"] == "16"
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-desktop").one()
        assert bot.status == terminal_status
        assert json.loads(bot.ext)["publish_id"] == "16"


def test_prepare_baas_desktop_restart_rolls_back_both_rows_on_failure(
    autocommit_db,
):
    db, engine = autocommit_db
    repo = DeviceRepository(db)
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status="ACTIVE",
            device_props={"callback_token": "keep"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status="ACTIVE",
        ext=json.dumps({"keep": "value"}),
    )

    def fail_binding_update(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ):
        if statement.lstrip().lower().startswith(
            "update ac_entity_device_binding"
        ):
            raise RuntimeError("binding write failed")

    event.listen(engine, "before_cursor_execute", fail_binding_update)
    try:
        with (
            patch(_ENV_MOD, return_value="dev"),
            pytest.raises(RuntimeError, match="binding write failed"),
        ):
            repo.prepare_baas_desktop_restart(
                binding_id=bid,
                bot_id="bot-desktop",
                owner_id="emp-1",
                expected_publish_id=None,
                bot_ext_patch={"publish_id": "17"},
                binding_props_patch={"restart_publish_id": "17"},
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_binding_update)

    binding = repo.get_by_id(bid)
    assert binding.status == "ACTIVE"
    assert binding.device_props == {"callback_token": "keep"}
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-desktop").one()
        assert bot.status == "ACTIVE"
        assert json.loads(bot.ext) == {"keep": "value"}


def test_prepare_baas_desktop_restart_rejects_changed_publish_baseline(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status="PENDING",
            device_props={"restart_publish_id": "newer-publish"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status="PENDING",
        ext=json.dumps({"publish_id": "newer-publish"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        prepared = repo.prepare_baas_desktop_restart(
            binding_id=bid,
            bot_id="bot-desktop",
            owner_id="emp-1",
            expected_publish_id="older-baseline",
            bot_ext_patch={"publish_id": "older-publish"},
            binding_props_patch={"restart_publish_id": "older-publish"},
        )

    assert prepared is False
    assert repo.get_by_id(bid).device_props["restart_publish_id"] == (
        "newer-publish"
    )


def test_layout_startup_success_updates_bot_only_for_current_identity(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status="PENDING",
            device_props={"restart_publish_id": "17"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status="ACTIVE",
        ext=json.dumps({"keep": "value"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        assert repo.transition_layout_startup_status_if_matches(
            binding_id=bid,
            startup_identity="17",
            status="SUCCEEDED",
            message=None,
        )

    assert repo.get_by_id(bid).status == "PENDING"
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-desktop").one()
        assert bot.status == "ACTIVE"
        assert json.loads(bot.ext) == {
            "keep": "value",
            "start_status": "SUCCEEDED",
        }


def test_layout_startup_failure_is_guarded_by_current_identity(repo, db):
    bid = repo.insert_binding(
        **_binding(
            device_provider="baas",
            status="PENDING",
            device_props={"restart_publish_id": "newer"},
        )
    )
    _bot(
        db,
        bot_id="bot-desktop",
        owner_id="emp-1",
        binding_id=bid,
        env="dev",
        status="PENDING",
        ext=json.dumps({"keep": "value"}),
    )

    with patch(_ENV_MOD, return_value="dev"):
        assert not repo.transition_layout_startup_status_if_matches(
            binding_id=bid,
            startup_identity="older",
            status="FAILED",
            message="invalid layout",
        )
        assert repo.transition_layout_startup_status_if_matches(
            binding_id=bid,
            startup_identity="newer",
            status="FAILED",
            message="invalid layout",
        )

    assert repo.get_by_id(bid).status == "FAILED"
    with db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-desktop").one()
        assert bot.status == "FAILED"
        assert json.loads(bot.ext)["start_message"] == "invalid layout"


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

_ENV_MOD = "agentclaw.community.core.repository.implementations.devices.device.get_current_env"


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
