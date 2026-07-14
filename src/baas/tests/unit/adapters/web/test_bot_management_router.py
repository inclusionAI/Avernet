# mypy: disable-error-code="arg-type"
"""Unit tests for bot_management_router endpoints.

Tests all 13 REST API endpoints exposed by the BotManagementService router:
- GET /api/v1/bots - List bots (with/without status filter)
- GET /api/v1/bots/{bot_uuid}/detail-by-uuid - Get bot records with devices by UUID
- GET /api/v1/bots/{bot_id}/detail-by-id - Get bot with devices by ID
- POST /api/v1/bots - Create bot
- GET /api/v1/bots/{bot_uuid} - Get bot details
- POST /api/v1/bots/{bot_uuid}/destroy - Destroy bot
- POST /api/v1/bots/{bot_uuid}/update - Update bot
- POST /api/v1/bots/{bot_uuid}/scale - Scale bot devices
- POST /api/v1/bots/{bot_uuid}/restart - Restart bot
- GET /api/v1/bots/{bot_uuid}/device-status - Get device aggregate status
- GET /api/v1/bots/{bot_uuid}/sessions - List bot sessions
- GET /api/v1/bots/{bot_uuid}/devices - List devices by bot UUID
- GET /api/v1/bots/{bot_id}/devices-by-id - List devices by bot ID

Coverage targets: 62% -> 90% (386 lines).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.management_router import (
    UpdateDevicesRequest,
    router,
)
from secbaas.community.api.bot_manage import (
    BotConfig,
    BotDeviceStatus,
    BotDeviceStatusResponse,
    BotListResponse,
    BotResponse,
    CreateBotResponse,
    DestroyBotResponse,
    RestartBotResponse,
    ScaleBotResponse,
    UpdateBotResponse,
    UpdateDevicesResponse,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import (
    DeviceInfo,
    DeviceListResponse,
    DeviceResponse,
)
from secbaas.community.api.publish_manage import RestartScope
from secbaas.community.bootstrap import Provide
from tests.unit.adapters.web.conftest import iter_api_routes

app = FastAPI()
app.include_router(router)


# ==================== Helpers ====================


def _make_bot_response(
    id: int = 1,
    bot_uuid: str = "BOT-001",
    tenant: str = "test_tenant",
    status: str = "ACTIVE",
    name: str = "test-bot",
    description: str | None = "Test bot",
    template_uuid: str = "TMPL-001",
) -> BotResponse:
    """Build a BotResponse for test assertions."""
    now = datetime.now(tz=UTC)
    return BotResponse(
        id=id,
        bot_uuid=bot_uuid,
        tenant=tenant,
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="user1",
        status=status,
        name=name,
        description=description,
        template_uuid=template_uuid,
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
    )


def _make_device_info(
    device_uuid: str = "DEV-001", status: str = "ACTIVE"
) -> DeviceInfo:
    return DeviceInfo(
        device_uuid=device_uuid,
        status=status,
        provider_type="ARCA",
        provider_device_id=f"sandbox-{device_uuid}",
        gmt_create=datetime.now(tz=UTC),
    )


def _make_device_response(
    device_uuid: str = "DEV-001", status: str = "ACTIVE"
) -> DeviceResponse:
    now = datetime.now(tz=UTC)
    return DeviceResponse(
        id=1,
        device_uuid=device_uuid,
        tenant="test_tenant",
        env="dev",
        domain="default",
        status=status,
        provider_type="ARCA",
        provider_device_id=f"sandbox-{device_uuid}",
        provider_device_props={},
        extra_config=None,
        err_msg=None,
        creator="user1",
        modifier="user1",
        gmt_create=now,
        gmt_modified=now,
    )


# ==================== Fixtures ====================


class MockPaginatedResult:
    """Minimal mock for PaginatedResult used by sessions endpoint."""

    def __init__(self, items: list, total: int, page: int, page_size: int):
        self.items = items
        self.total = total
        self.page = page
        self.page_size = page_size


class _MockSessionRecord:
    """Minimal mock for BotSessionRecord.

    Must provide all attributes accessed by _record_to_response in session_router.py.
    """

    def __init__(self, **kwargs):
        defaults = {
            "id": 1,
            "session_id": "SESS-001",
            "bot_uuid": "BOT-001",
            "status": "RUNNING",
            "device_uuid": "DEV-001",
            "invoker": "test-invoker",
            "context": None,
            "req": {},
            "res": {},
            "result": {},
            "err_msg": None,
            "trace_id": None,
            "gmt_create": datetime.now(tz=UTC),
            "gmt_modified": datetime.now(tz=UTC),
        }
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


@pytest.fixture
def mock_service():
    """Override DI-injected Depends(Provide[...]) with a mock via dependency_overrides.

    Iterates the app's route tree (handling _IncludedRouter wrappers) to find
    and replace every Provide[services.bot_management_service] dependency.
    """
    mock_cls = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: mock_cls
    yield mock_cls
    app.dependency_overrides = old_overrides


# ==================== GET /api/v1/bots — list_bots ====================


@pytest.mark.asyncio
async def test_list_bots_success(mock_service):
    """GET /api/v1/bots returns paginated bot list."""
    bot = _make_bot_response()
    mock_service.list_bots.return_value = BotListResponse(
        items=[bot], total=1, page=1, page_size=20
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["total"] == 1
    assert len(data["data"]["items"]) == 1
    mock_service.list_bots.assert_awaited_once_with(
        tenant="test_tenant", page=1, page_size=20, status=None
    )


@pytest.mark.asyncio
async def test_list_bots_with_status_filter(mock_service):
    """GET /api/v1/bots with status filter passes status parameter."""
    mock_service.list_bots.return_value = BotListResponse(
        items=[], total=0, page=1, page_size=20
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots?tenant=test_tenant&status=FAILED")

    assert resp.status_code == 200
    mock_service.list_bots.assert_awaited_once_with(
        tenant="test_tenant", page=1, page_size=20, status="FAILED"
    )


@pytest.mark.asyncio
async def test_list_bots_with_pagination(mock_service):
    """GET /api/v1/bots with custom page and page_size."""
    mock_service.list_bots.return_value = BotListResponse(
        items=[], total=0, page=5, page_size=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots?tenant=test_tenant&page=5&page_size=10")

    assert resp.status_code == 200
    mock_service.list_bots.assert_awaited_once_with(
        tenant="test_tenant", page=5, page_size=10, status=None
    )


@pytest.mark.asyncio
async def test_list_bots_empty(mock_service):
    """GET /api/v1/bots returns empty list when no bots exist."""
    mock_service.list_bots.return_value = BotListResponse(
        items=[], total=0, page=1, page_size=20
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["total"] == 0
    assert data["data"]["items"] == []


# ==================== GET /{bot_uuid}/detail-by-uuid ====================


@pytest.mark.asyncio
async def test_get_bot_detail_by_uuid_success(mock_service):
    """GET /{bot_uuid}/detail-by-uuid returns records with devices."""
    bot = _make_bot_response()
    mock_service.list_bots_with_devices_by_uuid.return_value = [bot]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/BOT-001/detail-by-uuid?tenant=test_tenant"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["total"] == 1
    mock_service.list_bots_with_devices_by_uuid.assert_awaited_once_with(
        tenant="test_tenant", bot_uuid="BOT-001"
    )


@pytest.mark.asyncio
async def test_get_bot_detail_by_uuid_multiple_records(mock_service):
    """GET /{bot_uuid}/detail-by-uuid handles multiple records for same UUID."""
    bot1 = _make_bot_response(id=1, bot_uuid="BOT-001", status="ACTIVE")
    bot2 = _make_bot_response(id=2, bot_uuid="BOT-001", status="FAILED")
    mock_service.list_bots_with_devices_by_uuid.return_value = [bot1, bot2]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/BOT-001/detail-by-uuid?tenant=test_tenant"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["total"] == 2
    assert len(data["data"]["items"]) == 2


# ==================== GET /{bot_id}/detail-by-id ====================


@pytest.mark.asyncio
async def test_get_bot_detail_by_id_success(mock_service):
    """GET /{bot_id}/detail-by-id returns bot with devices."""
    bot = _make_bot_response(id=42)
    bot.devices = [_make_device_info("DEV-001"), _make_device_info("DEV-002")]
    mock_service.get_bot_with_devices.return_value = bot

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/42/detail-by-id?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["id"] == 42
    mock_service.get_bot_with_devices.assert_awaited_once_with(
        tenant="test_tenant", bot_id=42
    )


@pytest.mark.asyncio
async def test_get_bot_detail_by_id_not_found(mock_service):
    """GET /{bot_id}/detail-by-id returns 404 when bot not found."""
    mock_service.get_bot_with_devices.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/99999/detail-by-id?tenant=test_tenant")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error_code"] == "BOT_NOT_FOUND"


# ==================== POST /api/v1/bots — create_bot ====================


@pytest.mark.asyncio
async def test_create_bot_success(mock_service):
    """POST /api/v1/bots creates a new bot."""
    now = datetime.now(tz=UTC)
    create_resp = CreateBotResponse(
        id=1,
        bot_uuid="BOT-NEW",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="operator1",
        modifier="operator1",
        status="PENDING",
        name="new-bot",
        description="A new bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=101,
        request_id="a" * 32,
    )
    mock_service.create_bot.return_value = create_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots?tenant=test_tenant",
            json={
                "name": "new-bot",
                "template_uuid": "TMPL-001",
                "device_count": 3,
                "operator": "operator1",
                "description": "A new bot",
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["bot_uuid"] == "BOT-NEW"
    assert data["data"]["publish_id"] == 101
    mock_service.create_bot.assert_awaited_once()
    call_kwargs = mock_service.create_bot.call_args.kwargs
    assert call_kwargs["tenant"] == "test_tenant"
    assert call_kwargs["name"] == "new-bot"
    assert call_kwargs["template_uuid"] == "TMPL-001"
    assert call_kwargs["device_count"] == 3
    assert call_kwargs["operator"] == "operator1"


@pytest.mark.asyncio
async def test_create_bot_with_config(mock_service):
    """POST /api/v1/bots with BotConfig."""
    now = datetime.now(tz=UTC)
    mock_service.create_bot.return_value = CreateBotResponse(
        id=1,
        bot_uuid="BOT-CFG",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="op",
        modifier="op",
        status="PENDING",
        name="cfg-bot",
        description="Bot with config",
        template_uuid="TMPL-001",
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=102,
        request_id="a" * 32,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots?tenant=test_tenant",
            json={
                "name": "cfg-bot",
                "template_uuid": "TMPL-001",
                "device_count": 1,
                "operator": "op",
                "request_id": "a" * 32,
                "config": {"entity_type": "staff", "entity_id": "entity123"},
            },
        )

    assert resp.status_code == 200
    call_kwargs = mock_service.create_bot.call_args.kwargs
    assert call_kwargs["config"] is not None


@pytest.mark.asyncio
async def test_create_bot_default_device_count(mock_service):
    """POST /api/v1/bots with default device_count=1."""
    now = datetime.now(tz=UTC)
    mock_service.create_bot.return_value = CreateBotResponse(
        id=1,
        bot_uuid="BOT-DEF",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="op",
        modifier="op",
        status="PENDING",
        name="default-bot",
        description=None,
        template_uuid="TMPL-001",
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=103,
        request_id="a" * 32,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots?tenant=test_tenant",
            json={
                "name": "default-bot",
                "template_uuid": "TMPL-001",
                "operator": "op",
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 200
    call_kwargs = mock_service.create_bot.call_args.kwargs
    assert call_kwargs["device_count"] == 1


# ==================== GET /{bot_uuid} — get_bot ====================


@pytest.mark.asyncio
async def test_get_bot_success(mock_service):
    """GET /{bot_uuid} returns bot details."""
    bot = _make_bot_response()
    mock_service.get_bot.return_value = bot

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/BOT-001?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["bot_uuid"] == "BOT-001"
    mock_service.get_bot.assert_awaited_once_with(
        tenant="test_tenant", bot_uuid="BOT-001", health_check=False, engine_type=None
    )


@pytest.mark.asyncio
async def test_get_bot_not_found(mock_service):
    """GET /{bot_uuid} returns 404 when bot not found."""
    mock_service.get_bot.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/BOT-MISSING?tenant=test_tenant")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error_code"] == "BOT_NOT_FOUND"
    assert "BOT-MISSING" in detail["message"]


@pytest.mark.asyncio
async def test_get_bot_with_health_check_param(mock_service):
    """GET /{bot_uuid}?health_check=true forwards health_check param."""
    bot = _make_bot_response()
    mock_service.get_bot.return_value = bot

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/BOT-001?tenant=test_tenant&health_check=true"
        )

    assert resp.status_code == 200
    mock_service.get_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        health_check=True,
        engine_type=None,
    )


@pytest.mark.asyncio
async def test_get_bot_with_engine_type_param(mock_service):
    """GET /{bot_uuid}?engine_type=openclaw forwards engine_type param."""
    bot = _make_bot_response()
    mock_service.get_bot.return_value = bot

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/BOT-001?tenant=test_tenant&health_check=true"
            "&engine_type=openclaw"
        )

    assert resp.status_code == 200
    mock_service.get_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        health_check=True,
        engine_type="openclaw",
    )


# ==================== POST /{bot_uuid}/destroy — destroy_bot ====================


@pytest.mark.asyncio
async def test_destroy_bot_success(mock_service):
    """POST /{bot_uuid}/destroy destroys a bot."""
    now = datetime.now(tz=UTC)
    destroy_resp = DestroyBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="DESTROYING",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=201,
        request_id="a" * 32,
    )
    mock_service.destroy_bot.return_value = destroy_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/destroy?tenant=test_tenant",
            json={"operator": "operator1", "request_id": "a" * 32},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["publish_id"] == 201
    mock_service.destroy_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        operator="operator1",
        request_id="a" * 32,
        auto_approve_publish=False,
    )


@pytest.mark.asyncio
async def test_destroy_bot_not_found(mock_service):
    """POST /{bot_uuid}/destroy returns 404 when bot not found."""
    mock_service.destroy_bot.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-MISSING/destroy?tenant=test_tenant",
            json={"operator": "op", "request_id": "a" * 32},
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error_code"] == "BOT_NOT_FOUND"


# ==================== POST /{bot_uuid}/update — update_bot ====================


@pytest.mark.asyncio
async def test_update_bot_success(mock_service):
    """POST /{bot_uuid}/update updates bot metadata."""
    now = datetime.now(tz=UTC)
    update_resp = UpdateBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="updated-bot",
        description="Updated description",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=None,
    )
    mock_service.update_bot.return_value = update_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update?tenant=test_tenant",
            json={
                "name": "updated-bot",
                "description": "Updated description",
                "operator": "operator1",
            },
        )

    assert resp.status_code == 200
    mock_service.update_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        operator="operator1",
        bot_name="updated-bot",
        bot_desc="Updated description",
        bot_config=None,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_update_bot_with_config(mock_service):
    """POST /{bot_uuid}/update with BotConfig triggers UPDATE publish."""
    now = datetime.now(tz=UTC)
    update_resp = UpdateBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="cfg-bot",
        description=None,
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=301,
    )
    mock_service.update_bot.return_value = update_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update?tenant=test_tenant",
            json={
                "operator": "operator1",
                "request_id": "a" * 32,
                "config": {"entity_type": "staff"},
            },
        )

    assert resp.status_code == 200
    call_kwargs = mock_service.update_bot.call_args.kwargs
    assert call_kwargs["bot_config"] is not None
    assert call_kwargs["request_id"] == "a" * 32


@pytest.mark.asyncio
async def test_update_bot_name_only(mock_service):
    """POST /{bot_uuid}/update with name only (no config change)."""
    now = datetime.now(tz=UTC)
    update_resp = UpdateBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="renamed-bot",
        description=None,
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=None,
    )
    mock_service.update_bot.return_value = update_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update?tenant=test_tenant",
            json={"name": "renamed-bot", "operator": "operator1"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_service.update_bot.call_args.kwargs
    assert call_kwargs["bot_name"] == "renamed-bot"
    assert call_kwargs["bot_desc"] is None
    assert call_kwargs["bot_config"] is None


# ==================== POST /{bot_uuid}/scale — scale_bot ====================


@pytest.mark.asyncio
async def test_scale_bot_success(mock_service):
    """POST /{bot_uuid}/scale scales a bot to target count."""
    now = datetime.now(tz=UTC)
    scale_resp = ScaleBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        target_count=10,
        publish_id=401,
        request_id="a" * 32,
    )
    mock_service.scale_bot.return_value = scale_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/scale?tenant=test_tenant",
            json={"target_count": 10, "operator": "operator1", "request_id": "a" * 32},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["target_count"] == 10
    assert data["data"]["publish_id"] == 401
    mock_service.scale_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        target_count=10,
        operator="operator1",
        request_id="a" * 32,
        auto_approve_publish=False,
    )


@pytest.mark.asyncio
async def test_scale_bot_with_auto_approve(mock_service):
    """POST /{bot_uuid}/scale with auto_approve_publish=True."""
    now = datetime.now(tz=UTC)
    scale_resp = ScaleBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        target_count=10,
        publish_id=403,
        request_id="a" * 32,
    )
    mock_service.scale_bot.return_value = scale_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/scale?tenant=test_tenant",
            json={
                "target_count": 10,
                "operator": "operator1",
                "request_id": "a" * 32,
                "auto_approve_publish": True,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["target_count"] == 10
    assert data["data"]["publish_id"] == 403
    mock_service.scale_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        target_count=10,
        operator="operator1",
        request_id="a" * 32,
        auto_approve_publish=True,
    )


@pytest.mark.asyncio
async def test_scale_bot_scale_down(mock_service):
    """POST /{bot_uuid}/scale with lower target_count (scale down)."""
    now = datetime.now(tz=UTC)
    scale_resp = ScaleBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="op",
        status="ACTIVE",
        name="test-bot",
        description=None,
        template_uuid="TMPL-001",
        replica_desired=10,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        target_count=3,
        publish_id=402,
        request_id="a" * 32,
    )
    mock_service.scale_bot.return_value = scale_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/scale?tenant=test_tenant",
            json={"target_count": 3, "operator": "op", "request_id": "a" * 32},
        )

    assert resp.status_code == 200
    mock_service.scale_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        target_count=3,
        operator="op",
        request_id="a" * 32,
        auto_approve_publish=False,
    )


# ==================== POST /{bot_uuid}/restart — restart_bot ====================


@pytest.mark.asyncio
async def test_restart_bot_success(mock_service):
    """POST /{bot_uuid}/restart restarts bot devices."""
    now = datetime.now(tz=UTC)
    restart_resp = RestartBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=501,
        request_id="a" * 32,
    )
    mock_service.restart_bot.return_value = restart_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/restart?tenant=test_tenant",
            json={"operator": "operator1", "request_id": "a" * 32},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["publish_id"] == 501
    mock_service.restart_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        operator="operator1",
        request_id="a" * 32,
        scope=RestartScope.ALL,
        auto_approve_publish=False,
    )


@pytest.mark.asyncio
async def test_restart_bot_unhealthy_scope(mock_service):
    """POST /{bot_uuid}/restart with scope='unhealthy'."""
    now = datetime.now(tz=UTC)
    restart_resp = RestartBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="op",
        status="ACTIVE",
        name="test-bot",
        description=None,
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=502,
        request_id="a" * 32,
    )
    mock_service.restart_bot.return_value = restart_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/restart?tenant=test_tenant",
            json={
                "operator": "op",
                "scope": "unhealthy",
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 200
    mock_service.restart_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        operator="op",
        request_id="a" * 32,
        scope=RestartScope.UNHEALTHY,
        auto_approve_publish=False,
    )


@pytest.mark.asyncio
async def test_restart_bot_with_auto_approve(mock_service):
    """POST /{bot_uuid}/restart with auto_approve_publish=True."""
    now = datetime.now(tz=UTC)
    restart_resp = RestartBotResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=503,
        request_id="a" * 32,
    )
    mock_service.restart_bot.return_value = restart_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/restart?tenant=test_tenant",
            json={
                "operator": "operator1",
                "request_id": "a" * 32,
                "auto_approve_publish": True,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["publish_id"] == 503
    mock_service.restart_bot.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        operator="operator1",
        request_id="a" * 32,
        scope=RestartScope.ALL,
        auto_approve_publish=True,
    )


# ==================== GET /{bot_uuid}/device-status ====================


@pytest.mark.asyncio
async def test_get_bot_device_status_success(mock_service):
    """GET /{bot_uuid}/device-status returns aggregate device status."""
    status_resp = BotDeviceStatusResponse(
        bot_uuid="BOT-001",
        bot_id=1,
        bot_status="ACTIVE",
        device_status=BotDeviceStatus.ALL_ONLINE.value,
        device_count=3,
        active_count=3,
        failed_count=0,
        pending_count=0,
        offline_count=0,
        other_count=0,
    )
    mock_service.get_bot_device_status.return_value = status_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/BOT-001/device-status?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["device_status"] == BotDeviceStatus.ALL_ONLINE.value
    assert data["data"]["device_count"] == 3
    assert data["data"]["active_count"] == 3
    assert data["data"]["offline_count"] == 0
    mock_service.get_bot_device_status.assert_awaited_once_with(
        tenant="test_tenant", bot_uuid="BOT-001"
    )


@pytest.mark.asyncio
async def test_get_bot_device_status_partial(mock_service):
    """GET /{bot_uuid}/device-status with partial online mix."""
    status_resp = BotDeviceStatusResponse(
        bot_uuid="BOT-001",
        bot_id=1,
        bot_status="ACTIVE",
        device_status=BotDeviceStatus.PARTIAL_ONLINE.value,
        device_count=5,
        active_count=3,
        failed_count=2,
        pending_count=0,
        offline_count=0,
        other_count=0,
    )
    mock_service.get_bot_device_status.return_value = status_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/BOT-001/device-status?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["device_status"] == BotDeviceStatus.PARTIAL_ONLINE.value
    assert data["data"]["active_count"] == 3
    assert data["data"]["failed_count"] == 2


@pytest.mark.asyncio
async def test_get_bot_device_status_bot_not_found(mock_service):
    """GET /{bot_uuid}/device-status returns 404 when bot not found."""
    mock_service.get_bot_device_status.side_effect = BotNotFoundError("BOT-MISSING")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/BOT-MISSING/device-status?tenant=test_tenant"
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error_code"] == "BOT_NOT_FOUND"


# ==================== GET /{bot_uuid}/devices — list_devices_by_bot_uuid ====================


@pytest.mark.asyncio
async def test_get_bot_devices_by_uuid_success(mock_service):
    """GET /{bot_uuid}/devices returns device lists per bot record."""
    dev_list = DeviceListResponse(
        items=[_make_device_response("DEV-001"), _make_device_response("DEV-002")],
        total=2,
        page=1,
        page_size=2,
    )
    mock_service.list_devices_by_bot_uuid.return_value = [dev_list]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/BOT-001/devices?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["total"] == 2
    mock_service.list_devices_by_bot_uuid.assert_awaited_once_with(
        tenant="test_tenant", bot_uuid="BOT-001"
    )


@pytest.mark.asyncio
async def test_get_bot_devices_by_uuid_empty(mock_service):
    """GET /{bot_uuid}/devices returns empty list."""
    mock_service.list_devices_by_bot_uuid.return_value = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/BOT-001/devices?tenant=test_tenant")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ==================== GET /{bot_id}/devices-by-id — list_devices_by_bot_id ====================


@pytest.mark.asyncio
async def test_get_bot_devices_by_id_success(mock_service):
    """GET /{bot_id}/devices-by-id returns paginated devices by bot ID."""
    dev_list = DeviceListResponse(
        items=[_make_device_response("DEV-003"), _make_device_response("DEV-004")],
        total=2,
        page=1,
        page_size=20,
    )
    mock_service.list_devices_by_bot_id.return_value = dev_list

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/42/devices-by-id?tenant=test_tenant")

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["total"] == 2
    assert len(data["data"]["items"]) == 2
    mock_service.list_devices_by_bot_id.assert_awaited_once_with(
        tenant="test_tenant", bot_id=42, page=1, page_size=20
    )


@pytest.mark.asyncio
async def test_get_bot_devices_by_id_with_pagination(mock_service):
    """GET /{bot_id}/devices-by-id with custom pagination."""
    mock_service.list_devices_by_bot_id.return_value = DeviceListResponse(
        items=[], total=0, page=5, page_size=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/42/devices-by-id?tenant=test_tenant&page=5&page_size=10"
        )

    assert resp.status_code == 200
    mock_service.list_devices_by_bot_id.assert_awaited_once_with(
        tenant="test_tenant", bot_id=42, page=5, page_size=10
    )


# ==================== Unhappy path / edge cases via HTTP ====================


@pytest.mark.asyncio
async def test_destroy_bot_request_missing_operator(mock_service):
    """POST /{bot_uuid}/destroy fails validation with missing operator."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/destroy?tenant=test_tenant",
            json={"request_id": "a" * 32},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_scale_bot_invalid_target_count(mock_service):
    """POST /{bot_uuid}/scale fails validation with target_count=0."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/scale?tenant=test_tenant",
            json={"target_count": 0, "operator": "op", "request_id": "a" * 32},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_bot_missing_name(mock_service):
    """POST /api/v1/bots fails validation with missing name."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots?tenant=test_tenant",
            json={
                "template_uuid": "TMPL-001",
                "operator": "op",
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_restart_bot_invalid_scope(mock_service):
    """POST /{bot_uuid}/restart fails validation with invalid scope value."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/restart?tenant=test_tenant",
            json={
                "operator": "op",
                "scope": "INVALID_SCOPE",
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 422


# ==================== POST /{bot_uuid}/update-devices — update_devices ====================


@pytest.mark.asyncio
async def test_update_devices_success(mock_service):
    """POST /{bot_uuid}/update-devices creates UPDATE_DEVICE publish."""
    now = datetime.now(tz=UTC)
    update_resp = UpdateDevicesResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=601,
        request_id="a" * 32,
    )
    mock_service.update_devices.return_value = update_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update-devices?tenant=test_tenant",
            json={
                "operator": "operator1",
                "device_uuids": ["DEV-001", "DEV-002"],
                "auto_approve_publish": True,
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["publish_id"] == 601
    mock_service.update_devices.assert_awaited_once_with(
        tenant="test_tenant",
        bot_uuid="BOT-001",
        operator="operator1",
        request_id="a" * 32,
        device_uuids=["DEV-001", "DEV-002"],
        auto_approve_publish=True,
        config=None,
    )


@pytest.mark.asyncio
async def test_update_devices_with_config(mock_service):
    """POST /{bot_uuid}/update-devices passes config to service."""
    now = datetime.now(tz=UTC)
    bot_config = BotConfig(entity_type="enterprise", entity_id="ENT-001")
    update_resp = UpdateDevicesResponse(
        id=1,
        bot_uuid="BOT-001",
        tenant="test_tenant",
        env="dev",
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="operator1",
        status="ACTIVE",
        name="test-bot",
        description="Test bot",
        template_uuid="TMPL-001",
        replica_desired=3,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        gmt_create=now,
        gmt_modified=now,
        config=None,
        devices=[],
        publish_id=602,
        request_id="a" * 32,
    )
    mock_service.update_devices.return_value = update_resp

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update-devices?tenant=test_tenant",
            json={
                "operator": "operator1",
                "device_uuids": ["DEV-001"],
                "auto_approve_publish": False,
                "request_id": "a" * 32,
                "config": {"entity_type": "enterprise", "entity_id": "ENT-001"},
            },
        )

    assert resp.status_code == 200
    mock_service.update_devices.assert_awaited_once()
    call_kwargs = mock_service.update_devices.call_args.kwargs
    assert call_kwargs["config"] is not None
    assert call_kwargs["config"].entity_type == "enterprise"
    assert call_kwargs["config"].entity_id == "ENT-001"


@pytest.mark.asyncio
async def test_update_devices_bot_not_found(mock_service):
    """POST /{bot_uuid}/update-devices returns 404 when bot not found."""
    mock_service.update_devices.side_effect = BotNotFoundError("BOT-MISSING")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-MISSING/update-devices?tenant=test_tenant",
            json={
                "operator": "op",
                "device_uuids": ["DEV-001"],
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_devices_invalid_uuids(mock_service):
    """POST /{bot_uuid}/update-devices errors when devices don't belong to bot."""
    mock_service.update_devices.side_effect = ValueError(
        "Device(s) not found or not belonging to bot BOT-001: ['DEV-MISSING']"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update-devices?tenant=test_tenant",
            json={
                "operator": "op",
                "device_uuids": ["DEV-MISSING"],
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_devices_empty_uuids(mock_service):
    """POST /{bot_uuid}/update-devices fails validation with empty device_uuids."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/BOT-001/update-devices?tenant=test_tenant",
            json={
                "operator": "op",
                "device_uuids": [],
                "request_id": "a" * 32,
            },
        )

    assert resp.status_code == 422


# ==================== Model-level tests ====================


class TestCreateBotRequest:
    """Tests for CreateBotRequest model validation."""

    def test_default_device_count(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            CreateBotRequest,
        )

        req = CreateBotRequest(
            name="test",
            template_uuid="TMPL-001",
            operator="op",
            request_id="a" * 32,
        )
        assert req.device_count == 1

    def test_min_device_count(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            CreateBotRequest,
        )

        req = CreateBotRequest(
            name="test",
            template_uuid="TMPL-001",
            device_count=1,
            operator="op",
            request_id="a" * 32,
        )
        assert req.device_count == 1

    def test_description_optional(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            CreateBotRequest,
        )

        req = CreateBotRequest(
            name="test",
            template_uuid="TMPL-001",
            operator="op",
            request_id="a" * 32,
        )
        assert req.description is None

    def test_config_optional(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            CreateBotRequest,
        )

        req = CreateBotRequest(
            name="test",
            template_uuid="TMPL-001",
            operator="op",
            request_id="a" * 32,
            config=BotConfig(entity_type="staff"),
        )
        assert req.config is not None
        assert req.config.entity_type == "staff"


class TestUpdateBotRequest:
    """Tests for UpdateBotRequest model validation."""

    def test_all_fields_optional_except_operator(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            UpdateBotRequest,
        )

        req = UpdateBotRequest(operator="op")
        assert req.name is None
        assert req.description is None
        assert req.config is None
        assert req.request_id is None

    def test_default_operator_empty_string(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            UpdateBotRequest,
        )

        req = UpdateBotRequest()
        assert req.operator == ""

    def test_with_all_fields(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            UpdateBotRequest,
        )

        req = UpdateBotRequest(
            name="new-name",
            description="new-desc",
            operator="op",
            request_id="a" * 32,
            config=BotConfig(entity_type="enterprise"),
        )
        assert req.name == "new-name"
        assert req.description == "new-desc"
        assert req.operator == "op"
        assert req.request_id == "a" * 32
        assert req.config is not None


class TestScaleBotRequest:
    """Tests for ScaleBotRequest model validation."""

    def test_valid_request(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            ScaleBotRequest,
        )

        req = ScaleBotRequest(target_count=5, operator="op", request_id="a" * 32)
        assert req.target_count == 5
        assert req.operator == "op"


class TestRestartBotRequest:
    """Tests for RestartBotRequest model validation."""

    def test_default_scope(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            RestartBotRequest,
        )

        req = RestartBotRequest(operator="op", request_id="a" * 32)
        assert req.scope == RestartScope.ALL

    def test_explicit_scope(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            RestartBotRequest,
        )

        req = RestartBotRequest(
            operator="op", scope=RestartScope.UNHEALTHY, request_id="a" * 32
        )
        assert req.scope == RestartScope.UNHEALTHY

    def test_default_auto_approve_publish(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            RestartBotRequest,
        )

        req = RestartBotRequest(operator="op", request_id="a" * 32)
        assert req.auto_approve_publish is False

    def test_explicit_auto_approve_publish_true(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            RestartBotRequest,
        )

        req = RestartBotRequest(
            operator="op", request_id="a" * 32, auto_approve_publish=True
        )
        assert req.auto_approve_publish is True


class TestDestroyBotRequest:
    """Tests for DestroyBotRequest model validation."""

    def test_valid_request(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            DestroyBotRequest,
        )

        req = DestroyBotRequest(operator="op", request_id="a" * 32)
        assert req.operator == "op"


class TestUpdateDevicesRequest:
    """Tests for UpdateDevicesRequest model validation."""

    def _make(self, **kwargs) -> UpdateDevicesRequest:
        defaults: dict = {
            "device_uuids": ["DEV-001"],
            "operator": "op",
            "request_id": "a" * 32,
        }
        defaults.update(kwargs)
        return UpdateDevicesRequest(**defaults)

    def test_requires_device_uuids(self):
        req = self._make(device_uuids=["DEV-001"])
        assert req.device_uuids == ["DEV-001"]

    def test_default_operator_value(self):
        req = self._make(operator="op")
        assert req.operator == "op"

    def test_default_auto_approve_publish_true(self):
        req = self._make()
        assert req.auto_approve_publish is True

    def test_requires_request_id(self):
        req = self._make(request_id="a" * 32)
        assert req.request_id == "a" * 32

    def test_default_config_none(self):
        req = self._make()
        assert req.config is None

    def test_with_config(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            UpdateDevicesRequest,
        )

        req = UpdateDevicesRequest(
            device_uuids=["DEV-001"],
            operator="op",
            request_id="a" * 32,
            config=BotConfig(entity_type="enterprise", entity_id="ENT-001"),
        )
        assert req.config is not None
        assert req.config.entity_type == "enterprise"
        assert req.config.entity_id == "ENT-001"

    def test_requires_min_one_device_uuid(self):
        from secbaas.community.adapters.web.routers.bot_service.management_router import (
            UpdateDevicesRequest,
        )

        with pytest.raises(Exception):
            UpdateDevicesRequest(device_uuids=[], operator="op")


# ==================== Router definition tests ====================


class TestRouterDefinition:
    """Tests for the APIRouter definition metadata."""

    def test_router_prefix(self):
        assert router.prefix == "/api/v1/bots"

    def test_router_tags(self):
        assert "Bot管理(测试)" in router.tags

    def test_router_has_all_endpoints(self):
        route_paths = [r.path for r in router.routes]
        assert "/api/v1/bots" in route_paths
        assert "/api/v1/bots/{bot_uuid}" in route_paths
        assert "/api/v1/bots/{bot_uuid}/destroy" in route_paths
        assert "/api/v1/bots/{bot_uuid}/update" in route_paths
        assert "/api/v1/bots/{bot_uuid}/scale" in route_paths
        assert "/api/v1/bots/{bot_uuid}/restart" in route_paths
        assert "/api/v1/bots/{bot_uuid}/update-devices" in route_paths
        assert "/api/v1/bots/{bot_uuid}/device-status" in route_paths

        assert "/api/v1/bots/{bot_uuid}/devices" in route_paths
        assert "/api/v1/bots/{bot_uuid}/detail-by-uuid" in route_paths
        assert "/api/v1/bots/{bot_id}/detail-by-id" in route_paths
        assert "/api/v1/bots/{bot_id}/devices-by-id" in route_paths
