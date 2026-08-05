"""Cross-tenant isolation for ``ac_user_mcp_config`` (Track A, Stage 5).

This table holds the caller's per-MCP-server API keys and authorization
headers — the most sensitive data in the ``mcp`` category — and is keyed by a
user identifier alone, which is only meaningful *within* a tenant.

Every test here fails without the tenant column + guard registration in
``core/models/mcp.py``, which is the spec's "fails before, passes after"
requirement.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models.mcp import UserMCPConfig
from agentclaw.community.plugins.user_mcp_config_repository import (
    UserMCPConfigRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.avernet_tenant_guard import CrossTenantInsertError
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
        f"sqlite:///{tmp_path / 'mcp.db'}",
        connect_args={"check_same_thread": False},
    )
    UserMCPConfig.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(db):
    return UserMCPConfigRepository(db)


def _config(**ov):
    """A config row as ``MCPConfigService.update_user_unified_config`` writes it."""
    base = dict(
        user_id="12345",
        server_code="mcp.third.weather",
        api_key="sk-tenant-a-secret",
        extra_config={
            "api_key": "sk-tenant-a-secret",
            "headers": {"x-ling-auth": "tenant-a-token"},
            "endpoint_env": "PROD",
            "transport_protocol": "SSE",
        },
        env=get_current_env(),
    )
    base.update(ov)
    return base


@pytest.fixture
def two_tenant_configs(repo):
    """The same user identifier and server, configured by two different tenants.

    This is the shape the spec cares about: a user id like "12345" collides
    across tenants, so nothing but the tenant distinguishes these two rows.
    """
    with avernet_tenant_scope("tenant-a"):
        repo.create(_config())
    with avernet_tenant_scope("tenant-b"):
        repo.create(
            _config(
                api_key="sk-tenant-b-secret",
                extra_config={
                    "api_key": "sk-tenant-b-secret",
                    "headers": {"x-ling-auth": "tenant-b-token"},
                    "endpoint_env": "PRE",
                    "transport_protocol": "STREAMABLE_HTTP",
                },
            )
        )
    return repo


# ── the headline criterion ──────────────────────────────────────────


def test_two_tenants_hold_the_same_user_and_server(two_tenant_configs):
    """Neither tenant can see or displace the other's credentials.

    Fails without the unique-key change: the second ``create`` raises
    IntegrityError against a row that tenant B is not allowed to see.
    """
    repo = two_tenant_configs

    with avernet_tenant_scope("tenant-a"):
        mine = repo.get_by_user_and_server_code("12345", "mcp.third.weather")
        assert mine["api_key"] == "sk-tenant-a-secret"
        assert mine["extra_config"]["headers"] == {"x-ling-auth": "tenant-a-token"}

    with avernet_tenant_scope("tenant-b"):
        mine = repo.get_by_user_and_server_code("12345", "mcp.third.weather")
        assert mine["api_key"] == "sk-tenant-b-secret"
        assert mine["extra_config"]["headers"] == {"x-ling-auth": "tenant-b-token"}


# ── reads ───────────────────────────────────────────────────────────


def test_get_by_user_and_server_code_is_tenant_scoped(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.create(_config())
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_by_user_and_server_code("12345", "mcp.third.weather") is None


def test_get_by_id_is_tenant_scoped(repo):
    with avernet_tenant_scope("tenant-a"):
        created = repo.create(_config())
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_by_id(created["id"]) is None
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id(created["id"]) is not None


def test_list_by_user_is_tenant_scoped(two_tenant_configs):
    repo = two_tenant_configs
    with avernet_tenant_scope("tenant-a"):
        rows = repo.list_by_user("12345")
        assert [r["api_key"] for r in rows] == ["sk-tenant-a-secret"]
    with avernet_tenant_scope("tenant-b"):
        rows = repo.list_by_user("12345")
        assert [r["api_key"] for r in rows] == ["sk-tenant-b-secret"]


def test_own_tenant_still_sees_its_config(repo):
    """The guard must not over-filter — the owning tenant reads normally."""
    with avernet_tenant_scope("tenant-a"):
        repo.create(_config())
        found = repo.get_by_user_and_server_code("12345", "mcp.third.weather")
        assert found is not None
        assert found["extra_config"]["endpoint_env"] == "PROD"


# ── writes ──────────────────────────────────────────────────────────


def test_update_cross_tenant_is_a_noop(repo):
    with avernet_tenant_scope("tenant-a"):
        created = repo.create(_config())

    with avernet_tenant_scope("tenant-b"):
        result = repo.update(created["id"], {"api_key": "HACKED"})
        # Indistinguishable from a missing row.
        assert result is None

    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id(created["id"])["api_key"] == "sk-tenant-a-secret"


def test_delete_cross_tenant_is_a_noop(repo):
    with avernet_tenant_scope("tenant-a"):
        created = repo.create(_config())

    with avernet_tenant_scope("tenant-b"):
        assert repo.delete(created["id"]) is False

    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id(created["id"]) is not None


# ── inserts ─────────────────────────────────────────────────────────


def test_create_stamps_the_current_tenant(repo, db):
    """No call site sets the tenant; the insert guard does it."""
    with avernet_tenant_scope("tenant-b"):
        repo.create(_config())

    with db.orm_session() as s:
        row = (
            s.query(UserMCPConfig)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "tenant-b"


def test_create_outside_any_request_stamps_the_default_tenant(repo, db):
    """Background work and the whole internal API resolve to ``teamclaw``."""
    repo.create(_config())

    with db.orm_session() as s:
        row = (
            s.query(UserMCPConfig)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "teamclaw"


def test_explicit_conflicting_tenant_insert_raises(db):
    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(CrossTenantInsertError, match="UserMCPConfig"):
            with db.orm_session() as s:
                s.add(
                    UserMCPConfig(
                        user_id="12345",
                        server_code="mcp.third.weather",
                        env=get_current_env(),
                        avernet_tenant="tenant-a",  # != current context
                    )
                )
                s.flush()


# ── the tenant must never reach an API response ─────────────────────


def test_to_dict_key_set_is_unchanged(repo):
    """``avernet_tenant`` is deliberately absent from ``to_dict()``."""
    with avernet_tenant_scope("tenant-a"):
        created = repo.create(_config())

    assert set(created) == {
        "id",
        "user_id",
        "server_code",
        "api_key",
        "custom_headers",
        "extra_config",
        "env",
        "gmt_created",
        "gmt_modified",
    }
    assert "avernet_tenant" not in created
