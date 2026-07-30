"""Cross-tenant isolation for the public MCP config path (Track B, Task 7).

Stage 5 (`tests/community/plugins/test_user_mcp_config_tenant_isolation.py`)
proves the guard at the repository. This proves it through the exact layer the
public handlers use: `core/mcp/config_flow.read_unified_config` /
`write_unified_config`, driven against a **real** `MCPConfigService` + real
`UserMCPConfigRepository` + the real Stage 5 guard over SQLite. Only the
marketplace and device-sync collaborators (external systems) are mocked; the
config read/write and the tenant guard are the real thing.

That is the mechanism behind the endpoints' guarantee: a config owned by another
tenant is invisible (read reports "no config") and un-overwritable (a write
creates the caller's own row rather than displacing the other tenant's).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.mcp.config_flow import (
    read_unified_config,
    write_unified_config,
)
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.models.mcp import UserMCPConfig
from agentclaw.community.plugins.user_mcp_config_repository import (
    UserMCPConfigRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
SERVER = "mcp.third.weather"
USER = "12345"  # a user id that collides across tenants


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
def config_service(tmp_path):
    """A real MCPConfigService over the real repo + guard on file SQLite.

    ``mcp_center`` and ``bot_repo`` are not exercised by the read/write config
    path (headers validation is skipped by passing ``headers=None``), so they
    are mocks.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp.db'}",
        connect_args={"check_same_thread": False},
    )
    UserMCPConfig.__table__.create(engine)
    repo = UserMCPConfigRepository(_FileSqliteDB(engine))
    return MCPConfigService(
        user_mcp_config_repo=repo, mcp_center=MagicMock(), bot_repo=MagicMock()
    )


@pytest.fixture
def market():
    m = MagicMock()
    m.get_mcp_detail.return_value = {"serverCode": SERVER, "name": "Weather"}
    return m


@pytest.fixture
def sync():
    m = MagicMock()
    m.sync_mcp_detail_to_all_bots = AsyncMock(
        return_value={"success": True, "sync_results": [], "error": None}
    )
    return m


def _write(config_service, market, sync, tenant, *, api_key):
    with avernet_tenant_scope(tenant):
        return asyncio.run(
            write_unified_config(
                user_id=USER,
                server_code=SERVER,
                entity_id=USER,
                entity_type="staff",
                api_key=api_key,
                headers=None,
                endpoint_env="PROD",
                transport_protocol="SSE",
                config_service=config_service,
                market_service=market,
                sync_service=sync,
            )
        )


def _read(config_service, tenant):
    with avernet_tenant_scope(tenant):
        return read_unified_config(
            user_id=USER, server_code=SERVER, config_service=config_service
        )


def test_config_written_under_a_is_invisible_from_b(config_service, market, sync):
    _write(config_service, market, sync, TENANT_A, api_key="sk-tenant-a-secret")

    # Tenant B, reading through the same flow, sees nothing.
    b_view = _read(config_service, TENANT_B)
    assert b_view.has_config is False
    assert b_view.api_key is None

    # Tenant A still sees its own (masked).
    a_view = _read(config_service, TENANT_A)
    assert a_view.has_config is True
    assert a_view.api_key == "sk-t****cret"


def test_b_write_creates_own_row_not_overwrite_a(config_service, market, sync):
    _write(config_service, market, sync, TENANT_A, api_key="sk-tenant-a-secret")
    _write(config_service, market, sync, TENANT_B, api_key="sk-tenant-b-secret")

    # Each tenant reads back its own credential; neither displaced the other.
    assert _read(config_service, TENANT_A).api_key == "sk-t****cret"  # a's masked
    a_full = config_service.get_user_unified_config  # sanity: distinct stored rows
    with avernet_tenant_scope(TENANT_A):
        assert a_full(USER, SERVER)["api_key"] == "sk-tenant-a-secret"
    with avernet_tenant_scope(TENANT_B):
        assert a_full(USER, SERVER)["api_key"] == "sk-tenant-b-secret"


def test_two_tenants_same_user_and_server_coexist(config_service, market, sync):
    """The case the Stage 5 unique-key swap enables: same user id + server,
    two tenants, neither write rejected and neither read crosses over."""
    _write(config_service, market, sync, TENANT_A, api_key="sk-a-aaaaaaaa")
    # Would raise IntegrityError against the old (user_id, server_code, env) key.
    _write(config_service, market, sync, TENANT_B, api_key="sk-b-bbbbbbbb")

    assert _read(config_service, TENANT_A).api_key == "sk-a****aaaa"
    assert _read(config_service, TENANT_B).api_key == "sk-b****bbbb"


def test_cross_tenant_write_does_not_touch_the_other_row(config_service, market, sync):
    """A tenant-B write after a tenant-A write leaves A's stored bytes intact."""
    _write(config_service, market, sync, TENANT_A, api_key="sk-tenant-a-secret")
    _write(config_service, market, sync, TENANT_B, api_key="sk-tenant-b-secret")

    with avernet_tenant_scope(TENANT_A):
        stored = config_service.get_user_unified_config(USER, SERVER)
        assert stored["api_key"] == "sk-tenant-a-secret"  # untouched
