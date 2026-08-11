"""The internal MCP config API must be byte-identical after tenant isolation.

Track A Stage 5 adds ``avernet_tenant`` to ``ac_user_mcp_config`` and confines
every read/write to the request's tenant. Every internal request resolves to the
default tenant (``teamclaw``), so nothing about these responses may move.

Unlike ``test_mcp.py``, which mocks the services, this drives the **real**
``MCPConfigService`` over the **real** ``UserMCPConfigRepository`` against
SQLite — the layer the guard actually acts on. Only the external MCP Center and
the device-sync fan-out are stubbed.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.mcp.router import router as mcp_router
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.models.mcp import UserMCPConfig
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import UserMCPConfigRepository
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
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mcp_api.db'}",
        connect_args={"check_same_thread": False},
    )
    UserMCPConfig.__table__.create(eng)
    return eng


@pytest.fixture
def repo(engine):
    return UserMCPConfigRepository(_FileSqliteDB(engine))


@pytest.fixture
def config_service(repo):
    mcp_center = MagicMock()
    mcp_center.get_mcp_detail.return_value = {"serverCode": "mcp.third.weather"}
    return MCPConfigService(
        user_mcp_config_repo=repo,
        mcp_center=mcp_center,
        bot_repo=MagicMock(),
    )


@pytest.fixture
def sync_service():
    """Device fan-out stubbed: it is HTTP to devices, not part of this contract."""
    svc = MagicMock()

    async def _sync(**_kwargs):
        return {"success": True, "sync_results": [], "error": None}

    svc.sync_mcp_detail_to_all_bots = _sync
    return svc


@pytest.fixture
def client(config_service, sync_service):
    app = FastAPI()
    app.include_router(mcp_router)

    market = MagicMock()
    market.get_mcp_detail.return_value = {
        "serverCode": "mcp.third.weather",
        "name": "Weather",
    }

    class _TestModule(Module):
        def configure(self, binder):
            binder.bind(MCPConfigServiceProtocol, to=config_service)
            binder.bind(MCPMarketServiceProtocol, to=market)
            binder.bind(MCPSyncServiceProtocol, to=sync_service)

    attach_injector(app, Injector([_TestModule()]))

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="12345", staffId="12345", operatorName="alice"
    )
    # raise_server_exceptions=True: an unhandled error in the handler should
    # surface here as itself, not as an opaque 500.
    return TestClient(app, raise_server_exceptions=True)


# ── GET /api/mcp/user/config ────────────────────────────────────────


def test_get_config_when_absent_is_unchanged(client):
    resp = client.get("/api/mcp/user/config", params={"server_code": "mcp.x"})
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "message": "No config found",
        "data": {
            "server_code": "mcp.x",
            "api_key": None,
            "headers": {},
            "endpoint_env": "PROD",
            "transport_protocol": None,
            "has_config": False,
            "sync_results": None,
        },
    }


def test_get_config_masks_api_key_and_omits_tenant(client, repo):
    repo.create(
        {
            "user_id": "12345",
            "server_code": "mcp.third.weather",
            "api_key": "sk-abcdefghijklmnop",
            "extra_config": {
                "api_key": "sk-abcdefghijklmnop",
                "headers": {"x-ling-auth": "tok"},
                "endpoint_env": "PRE",
                "transport_protocol": "SSE",
            },
            "env": get_current_env(),
        }
    )

    resp = client.get(
        "/api/mcp/user/config", params={"server_code": "mcp.third.weather"}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "message": "Config retrieved",
        "data": {
            "server_code": "mcp.third.weather",
            "api_key": "sk-a****mnop",  # masking unchanged
            "headers": {"x-ling-auth": "tok"},
            "endpoint_env": "PRE",
            "transport_protocol": "SSE",
            "has_config": True,
            "sync_results": None,
        },
    }
    assert "avernet_tenant" not in resp.text


# ── POST /api/mcp/user/config ───────────────────────────────────────


def test_post_config_creates_and_response_is_unchanged(client, repo):
    resp = client.post(
        "/api/mcp/user/config",
        json={
            "server_code": "mcp.third.weather",
            "api_key": "sk-abcdefghijklmnop",
            "headers": {"x-ling-auth": "tok"},
            "endpoint_env": "PROD",
            "transport_protocol": "sse",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "message": "MCP config updated and synced to all devices",
        "data": {
            "server_code": "mcp.third.weather",
            "api_key": "sk-a****mnop",
            # The write path does not echo headers back — only the read path
            # does. Pre-existing behavior, pinned here so isolation cannot
            # quietly change it.
            "headers": None,
            "endpoint_env": "PROD",
            "transport_protocol": "SSE",  # upper-cased, as before
            "has_config": True,
            "sync_results": [],
        },
    }
    assert "avernet_tenant" not in resp.text

    stored = repo.get_by_user_and_server_code("12345", "mcp.third.weather")
    assert stored["extra_config"]["headers"] == {"x-ling-auth": "tok"}


def test_post_config_stamps_the_default_tenant(client, engine):
    """An internal request carries no tenant, so rows land on ``teamclaw``."""
    client.post(
        "/api/mcp/user/config",
        json={"server_code": "mcp.third.weather", "api_key": "sk-1"},
    )

    factory = sessionmaker(bind=engine)
    with factory() as s:
        row = (
            s.query(UserMCPConfig)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "teamclaw"


def test_short_api_key_masking_is_unchanged(client):
    resp = client.post(
        "/api/mcp/user/config",
        json={"server_code": "mcp.third.weather", "api_key": "sk-1"},
    )
    assert resp.json()["data"]["api_key"] == "****"


def test_rejects_bad_endpoint_env_as_before(client):
    resp = client.post(
        "/api/mcp/user/config",
        json={"server_code": "mcp.third.weather", "endpoint_env": "NOPE"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "endpoint_env must be PROD or PRE"


# ── pre-existing rows resolve to the default tenant ─────────────────


def test_rows_written_without_the_column_default_to_teamclaw(engine):
    """A row inserted by a non-ORM writer gets ``teamclaw`` from the DB default.

    This is the ``server_default`` (not a Python ``default=``) doing the work —
    the same mechanism that backfills existing production rows on the
    ``ALTER TABLE``. It is why every current internal response is unchanged.
    """
    from sqlalchemy import text

    # gmt_created/gmt_modified carry a Python-side ``default=``, not a server
    # default, so a raw writer must supply them. Only ``avernet_tenant`` is
    # left out — that is the column under test.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ac_user_mcp_config "
                "(user_id, server_code, env, gmt_created, gmt_modified) "
                "VALUES ('12345', 'mcp.legacy', :env, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"env": get_current_env()},
        )
        stored = conn.execute(
            text(
                "SELECT avernet_tenant FROM ac_user_mcp_config "
                "WHERE server_code = 'mcp.legacy'"
            )
        ).scalar_one()

    assert stored == "teamclaw"
