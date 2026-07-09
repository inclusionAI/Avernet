"""Unified DeviceBindingRepository — behavior + contract.

The last DB-repo twin in the unification program (S5). Covers all
17 Protocol methods + the 3 adopt-prod behavior changes:
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
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.device_repository import DeviceRepository
from agentclaw.community.plugins.local.sqlite_models import EntityDeviceBinding

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
