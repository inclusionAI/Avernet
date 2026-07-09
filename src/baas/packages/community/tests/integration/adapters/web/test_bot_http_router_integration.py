"""Integration tests for bot_http_router with real DB and mocked PaaS facade.

Tests the full stack through ASGI:
  Router -> BotHttpDispatcher -> Repositories (real DB) -> PaasServiceFacade (mocked)

Requires ZDAS MySQL database. Marked with @pytest.mark.integration
(excluded from default test runs via pytest.ini).
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

# Init container with minimal config so Selector providers can resolve
# during collection when application modules import Provide[...] markers.
from secbaas.bootstrap import get_container

get_container().config.from_dict(
    {
        "plugins": {
            "database": {
                "plugin_database": os.environ.get("PLUGIN_DATABASE", "ZDAS_ORM"),
            },
        },
    }
)

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.adapters.web.routers.bot_service.http_router import router
from secbaas.core.service.paas import PaasServiceFacade
from tests.unit.adapters.web.conftest import iter_api_routes

pytestmark = [pytest.mark.integration]

app = FastAPI()
app.include_router(router)


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_env():
    """Mock get_current_env to return 'dev'."""
    with patch("secbaas.core.utils.env_utils.get_current_env", return_value="dev"):
        yield


@pytest.fixture(autouse=True)
def mock_dispatcher():
    """Override the Provide dependency to inject a real BotHttpDispatcher.

    The test FastAPI app bypasses the production app.py lifecycle (which
    creates the container, wires routers, and monkey-patches Provide).
    Without this override, Depends(Provide[...]) returns an unresolved
    Provide marker and the route raises an AttributeError.
    """
    from secbaas.core.service.bot_runtime.dispatcher import (
        DefaultBotHttpDispatcher,
    )

    _real_bot_repo = get_container().repository.bot_repository()
    _real_device_repo = get_container().repository.device_repository()
    _mock_paas = get_container().services.paas_facade()

    real_dispatcher = DefaultBotHttpDispatcher(
        bot_repo=_real_bot_repo,
        device_repo=_real_device_repo,
        paas_facade=_mock_paas,
    )
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: real_dispatcher
    yield real_dispatcher
    app.dependency_overrides = old_overrides


@pytest.fixture
def mock_paas_facade():
    """Patch PaasServiceFacade.invoke_http_in_device to return controlled results."""
    with patch.object(
        PaasServiceFacade,
        "invoke_http_in_device",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": "eyJzdGF0dXMiOiAib2sifQ==",
        }
        yield mock


def _ensure_tenant(device_repository, tenant_name="test_tenant"):
    """Insert test_tenant if it doesn't exist."""
    from sqlalchemy import text

    from secbaas.core.utils.env_utils import get_current_env

    env = get_current_env()
    with device_repository._database.orm_session() as session:
        result = session.execute(
            text(
                "SELECT id FROM baas_tenant WHERE name=:name AND env=:env AND is_deleted=0"
            ),
            {"name": tenant_name, "env": env},
        )
        if not result.fetchone():
            session.execute(
                text(
                    "INSERT INTO baas_tenant (name, env, creator, modifier, is_deleted) VALUES (:name, :env, :creator, :modifier, 0)"
                ),
                {
                    "name": tenant_name,
                    "env": env,
                    "creator": "test_user",
                    "modifier": "test_user",
                },
            )
    return tenant_name


def _insert_bot(bot_repository, tenant, env, status="ACTIVE"):
    """Insert a bot record and return (bot_id, bot_uuid)."""
    from uuid import uuid4

    bot_uuid = uuid4().hex
    bot_id = bot_repository.insert_bot(
        bot_uuid=bot_uuid,
        tenant=tenant,
        env=env,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=status,
        name=f"Test Bot {bot_uuid[:8]}",
        description=None,
        template_uuid=None,
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        extra_config={},
    )
    return bot_id, bot_uuid


def _insert_device(
    device_repository, tenant, env, status="ACTIVE", provider_device_id=None
):
    """Insert a device record and return device_uuid."""
    from uuid import uuid4

    device_uuid = uuid4().hex
    if provider_device_id is None:
        provider_device_id = f"container--{device_uuid[:8]}--test"
    device_repository.insert_device(
        device_uuid=device_uuid,
        tenant=tenant,
        env=env,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=status,
        provider_type="LOCAL",
        provider_device_id=provider_device_id,
        provider_device_props={},
        extra_config={},
    )
    return device_uuid


def _create_rel(device_repository, bot_id, device_uuid, tenant, env):
    """Create a bot-device relationship."""
    from sqlalchemy import text

    with device_repository._database.orm_session() as session:
        session.execute(
            text("""INSERT INTO baas_bot_device_rel
               (bot_id, device_uuid, tenant, env, domain, creator, modifier, is_deleted)
               VALUES (:bot_id, :device_uuid, :tenant, :env, :domain, :creator, :modifier, 0)"""),
            {
                "bot_id": bot_id,
                "device_uuid": device_uuid,
                "tenant": tenant,
                "env": env,
                "domain": "test_domain",
                "creator": "test_user",
                "modifier": "test_user",
            },
        )


@pytest.fixture
def test_data(device_repository, bot_repository):
    """Create real DB records: tenant, bot, device, and their relationship."""
    from tests.integration.fixtures.bootstrap import TEST_ENV

    tenant = _ensure_tenant(device_repository)
    env = TEST_ENV

    bot_id, bot_uuid = _insert_bot(bot_repository, tenant, env)
    device_uuid = _insert_device(
        device_repository,
        tenant,
        env,
        provider_device_id="container--test--integration",
    )
    _create_rel(device_repository, bot_id, device_uuid, tenant, env)

    return {
        "tenant": tenant,
        "bot_uuid": bot_uuid,
        "device_uuid": device_uuid,
        "provider_device_id": "container--test--integration",
        "bot_id": bot_id,
        "env": env,
    }


@pytest.mark.asyncio
async def test_invoke_http_get_success(mock_paas_facade, test_data):
    """Full stack: Router -> Service -> DB -> mocked facade returns 200."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/health",
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers.get("content-type") == "application/json"

    mock_paas_facade.assert_awaited_once()
    call_kwargs = mock_paas_facade.call_args.kwargs
    assert call_kwargs["paas_device_id"] == test_data["provider_device_id"]
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["port"] == 8080
    assert call_kwargs["path"] == "/api/health"


@pytest.mark.asyncio
async def test_invoke_http_post_with_body(mock_paas_facade, test_data):
    """Full stack: POST with request body reaches facade with correct args."""
    mock_paas_facade.return_value = {
        "status_code": 201,
        "headers": {"content-type": "application/json"},
        "body": "eyJpZCI6IDQyfQ==",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/443/api/data",
            content=b'{"name": "test"}',
            headers={"content-type": "application/json", "x-custom": "val"},
        )

    assert resp.status_code == 201
    assert resp.json() == {"id": 42}

    call_kwargs = mock_paas_facade.call_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["port"] == 443
    assert call_kwargs["path"] == "/api/data"
    assert call_kwargs["body"] == b'{"name": "test"}'
    assert call_kwargs["headers"].get("x-custom") == "val"
    assert "host" not in call_kwargs["headers"]


@pytest.mark.asyncio
async def test_invoke_http_with_affinity(mock_paas_facade, test_data):
    """device_affinity is passed through and consistent hashing works."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/test"
            "?device_affinity=sticky-session-42",
        )

    assert resp.status_code == 200
    assert (
        mock_paas_facade.call_args.kwargs["paas_device_id"]
        == test_data["provider_device_id"]
    )


@pytest.mark.asyncio
async def test_invoke_http_bot_not_found_404(mock_paas_facade):
    """Non-existent bot UUID returns 404 without calling facade."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/nonexistent-bot-uuid/invoke-http/8080/api/test",
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"
    mock_paas_facade.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_http_no_active_devices_503(
    mock_paas_facade,
    device_repository,
    bot_repository,
):
    """Bot with only non-ACTIVE devices returns 503."""
    from tests.integration.fixtures.bootstrap import TEST_ENV

    tenant = _ensure_tenant(device_repository)
    env = TEST_ENV

    bot_id, bot_uuid = _insert_bot(bot_repository, tenant, env)
    device_uuid = _insert_device(device_repository, tenant, env, status="PENDING")
    _create_rel(device_repository, bot_id, device_uuid, tenant, env)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/bots/{tenant}/{bot_uuid}/invoke-http/8080/api/test",
        )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"
    mock_paas_facade.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_http_multiple_devices_affinity_sticky(
    mock_paas_facade,
    test_data,
    device_repository,
):
    """With multiple devices, same affinity key picks same device across calls."""
    from tests.integration.fixtures.bootstrap import TEST_ENV

    for i in range(2):
        dev_uuid = _insert_device(
            device_repository,
            test_data["tenant"],
            TEST_ENV,
            provider_device_id=f"container--extra{i}--test",
        )
        _create_rel(
            device_repository,
            test_data["bot_id"],
            dev_uuid,
            test_data["tenant"],
            TEST_ENV,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.get(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/test"
            "?device_affinity=my-session",
        )
        assert resp1.status_code == 200
        first_device = mock_paas_facade.call_args.kwargs["paas_device_id"]
        mock_paas_facade.reset_mock()

        resp2 = await client.get(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/test"
            "?device_affinity=my-session",
        )
        assert resp2.status_code == 200
        second_device = mock_paas_facade.call_args.kwargs["paas_device_id"]

    assert first_device == second_device, (
        "Same affinity key should select same device across calls"
    )


@pytest.mark.asyncio
async def test_invoke_http_put_method(mock_paas_facade, test_data):
    """PUT method is supported through the router."""
    mock_paas_facade.return_value = {"status_code": 200, "headers": {}, "body": ""}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/config",
            content=b'{"key": "updated"}',
        )
    assert resp.status_code == 200
    assert mock_paas_facade.call_args.kwargs["method"] == "PUT"
    assert mock_paas_facade.call_args.kwargs["body"] == b'{"key": "updated"}'


@pytest.mark.asyncio
async def test_invoke_http_delete_method(mock_paas_facade, test_data):
    """DELETE method is supported through the router."""
    mock_paas_facade.return_value = {"status_code": 204, "headers": {}, "body": ""}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/resource/99",
        )
    assert resp.status_code == 204
    assert mock_paas_facade.call_args.kwargs["method"] == "DELETE"


@pytest.mark.asyncio
async def test_invoke_http_facade_error_500(mock_paas_facade, test_data):
    """DeviceFacadeException propagates as 500 with error details."""
    from secbaas.core.service.paas import DeviceFacadeException, ErrorCode, PaasError

    mock_paas_facade.side_effect = DeviceFacadeException(
        operation="invoke_http_in_device",
        platform_type="LOCAL",
        template_id=42,
        paas_device_id=test_data["provider_device_id"],
        original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/bots/{test_data['tenant']}/{test_data['bot_uuid']}/invoke-http/8080/api/test",
        )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "DEVICE_NOT_FOUND"
    assert "operation" in detail["context"]


@pytest.mark.asyncio
async def test_invoke_http_missing_tenant_in_path_returns_404(
    mock_paas_facade, test_data
):
    """Missing tenant in path results in 404 (no matching route without tenant)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/bots/{test_data['bot_uuid']}/invoke-http/8080/api/test",
        )
    assert resp.status_code == 404
