"""Unified system-config repository — behavior + contract.

Round-3/session-3 criteria: single ORM body, 11-method
``ConfigRepositoryProtocol`` parity over two tables. Covers the
prod-parity guarantees: genuine atomic upsert (idempotent id),
``description`` COALESCE (incoming None keeps the existing value),
single blind DELETE, and the FK ON DELETE CASCADE from
``delete_category`` to ``ac_config_item`` (validates the SQLite
``PRAGMA foreign_keys=ON`` connect listener).
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugins.config_repository import ConfigRepository
from agentclaw.community.core.system_config.orm import AcConfigCategory, AcConfigItem

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


def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cfg.db'}",
        connect_args={"check_same_thread": False},
    )

    # Mirror the production SqliteDB connect listener so the FK
    # ON DELETE CASCADE actually fires under SQLite (the behavior
    # delete_category relies on for prod parity).
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # Create both tables incl. FK + unique constraints.
    AcConfigCategory.__table__.create(engine)
    AcConfigItem.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return ConfigRepository(_make_db(tmp_path))


# ── category CRUD ───────────────────────────────────────────────────

def test_upsert_category_insert_then_get(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备", env="prod",
        description="desc", operator="alice",
    )
    assert isinstance(cid, int) and cid > 0
    rec = repo.get_category(category="device", env="prod")
    assert rec.id == cid
    assert rec.category_name == "设备"
    assert rec.description == "desc"
    assert rec.creator == "alice"
    assert repo.get_category_by_id(category_id=cid).category == "device"


def test_upsert_category_is_idempotent_same_id(repo):
    a = repo.upsert_category(
        category="device", category_name="设备", env="prod",
        operator="alice",
    )
    b = repo.upsert_category(
        category="device", category_name="设备v2", env="prod",
        operator="bob",
    )
    assert a == b  # same row, atomic upsert on uk_category_env
    rec = repo.get_category_by_id(category_id=a)
    assert rec.category_name == "设备v2"
    assert rec.modifier == "bob"
    assert rec.creator == "alice"  # creator untouched on update (prod)


def test_upsert_category_description_coalesce(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备", env="prod",
        description="original", operator="alice",
    )
    # Re-upsert with description=None → must KEEP the original.
    repo.upsert_category(
        category="device", category_name="设备", env="prod",
        description=None, operator="bob",
    )
    assert repo.get_category_by_id(category_id=cid).description == "original"
    # Re-upsert with a real description → overwrites.
    repo.upsert_category(
        category="device", category_name="设备", env="prod",
        description="updated", operator="bob",
    )
    assert repo.get_category_by_id(category_id=cid).description == "updated"


def test_upsert_category_env_isolation(repo):
    a = repo.upsert_category(
        category="device", category_name="x", env="prod"
    )
    b = repo.upsert_category(
        category="device", category_name="x", env="pre"
    )
    assert a != b
    assert len(repo.list_categories(env="prod")) == 1
    assert len(repo.list_categories(env="pre")) == 1


def test_delete_category_cascades_to_items(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备", env="prod"
    )
    repo.upsert_config(
        parent_id=cid, config_key="k1", config_value="v1"
    )
    repo.upsert_config(
        parent_id=cid, config_key="k2", config_value="v2"
    )
    assert len(repo.list_configs(parent_id=cid)) == 2

    assert repo.delete_category(category_id=cid) is True
    # FK ON DELETE CASCADE removed the child config items.
    assert repo.list_configs(parent_id=cid) == []
    assert repo.get_category_by_id(category_id=cid) is None


def test_delete_category_missing_returns_false(repo):
    assert repo.delete_category(category_id=999999) is False


# ── config-item CRUD ────────────────────────────────────────────────

def test_upsert_config_insert_then_get(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备", env="prod"
    )
    iid = repo.upsert_config(
        parent_id=cid, config_key="timeout", config_value="30",
        description="d", operator="alice",
    )
    assert iid > 0
    rec = repo.get_config_by_key(parent_id=cid, config_key="timeout")
    assert rec.id == iid
    assert rec.config_value == "30"
    assert repo.get_config(config_id=iid).config_key == "timeout"


def test_upsert_config_idempotent_and_coalesce(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备", env="prod"
    )
    a = repo.upsert_config(
        parent_id=cid, config_key="k", config_value="1",
        description="orig", operator="alice",
    )
    b = repo.upsert_config(
        parent_id=cid, config_key="k", config_value="2",
        description=None, operator="bob",
    )
    assert a == b
    rec = repo.get_config(config_id=a)
    assert rec.config_value == "2"
    assert rec.description == "orig"  # None kept the prior value
    assert rec.modifier == "bob"


def test_delete_config(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备", env="prod"
    )
    iid = repo.upsert_config(
        parent_id=cid, config_key="k", config_value="v"
    )
    assert repo.delete_config(config_id=iid) is True
    assert repo.get_config(config_id=iid) is None
    assert repo.delete_config(config_id=iid) is False


def test_list_configs_and_list_all_configs(repo):
    cid = repo.upsert_category(
        category="device", category_name="设备名", env="prod"
    )
    repo.upsert_config(
        parent_id=cid, config_key="b", config_value='{"n": 1}'
    )
    repo.upsert_config(
        parent_id=cid, config_key="a", config_value="plain"
    )
    items = repo.list_configs(parent_id=cid)
    assert [i.config_key for i in items] == ["a", "b"]  # ordered

    all_cfg = repo.list_all_configs(env="prod")
    assert len(all_cfg) == 2
    by_key = {c["config_key"]: c for c in all_cfg}
    assert by_key["b"]["config_value"] == {"n": 1}  # JSON parsed
    assert by_key["a"]["config_value"] == "plain"  # non-JSON passthrough
    assert by_key["a"]["category"] == "device"
    assert by_key["a"]["category_name"] == "设备名"
