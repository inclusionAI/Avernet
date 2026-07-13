"""Unit tests for Bot Health Checker Router.

Tests the router layer with mocked service and API key validation.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.health_checker.health_checker_router import (
    ErrorCode,
    _parse_statuses_from_list,
    raise_bad_request,
    raise_not_found,
    router,
    validate_bot_health_api_key,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.health_check.bot import (
    AliveDeviceInfo,
    BotAliveCheckResult,
    BotDeviceInfo,
    BotHealthCheckerError,
    BotHealthCheckResult,
    DeviceAliveStatus,
    PaasDeviceInfo,
    PaasDeviceListResponse,
    SandboxNotFoundError,
    TTLExtendResult,
    TTLInfo,
    UnsupportedDeviceProviderError,
)
from secbaas.community.api.health_check.paas import PaasHealthCheckerResult
from secbaas.community.bootstrap import Provide
from tests.unit.adapters.web.conftest import iter_api_routes


def _override_dep(app, mock_svc):
    """Override all Provide[services.bot_health_checker_service] dependencies."""
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: mock_svc


@pytest.fixture(autouse=True)
def mock_db_manager():
    """Mock db_manager to prevent database initialization errors."""
    from secbaas.community.core.database import db_manager

    original = db_manager._connection_factory
    db_manager._connection_factory = lambda ds: MagicMock()
    yield
    db_manager._connection_factory = original


@pytest.fixture
def mock_service():
    """Create a mock service instance with async methods."""
    mock = MagicMock()
    mock.list_all_active_bot_device = AsyncMock()
    mock.list_paas_device_by_bot = AsyncMock()
    mock.extend_ttl_by_bot = AsyncMock()
    mock.check_health_by_bot = AsyncMock()
    mock.check_alive_by_bot = AsyncMock()
    mock.get_sandbox_info = AsyncMock()
    return mock


@pytest.fixture
def mock_api_key():
    """Create a mock API key record with health-checker app_type."""
    return APIKeyRecord(
        id=1,
        gmt_create=datetime(2026, 1, 1),
        gmt_modified=datetime(2026, 1, 1),
        api_key_hash="test_hash",
        api_key_prefix="sk_test_",
        key_name="Test Key",
        app_id="test_app",
        app_type="health-checker",
        description="Test API Key",
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test_user",
        tenant="default",
        env="prod",
        creator="test_user",
        modifier=None,
        policy=None,
    )


@pytest.fixture
def client(mock_service, mock_api_key):
    """Create a test client with dependency override."""
    app = FastAPI()
    app.include_router(router)

    _override_dep(app, mock_service)

    async def override_validate_bot_health_api_key():
        return mock_api_key

    app.dependency_overrides[validate_bot_health_api_key] = (
        override_validate_bot_health_api_key
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ============ Test Data Helpers ============


def make_bot_device_info(**kwargs) -> BotDeviceInfo:
    defaults = dict(
        bot_id="bot-1",
        entity_id="entity-1",
        bot_type="personal",
        status="active",
        active_engine="openclaw",
    )
    defaults.update(kwargs)
    return BotDeviceInfo(**defaults)


def make_paas_device_info(**kwargs) -> PaasDeviceInfo:
    defaults = dict(
        paas_device_id="ARCA-SANDBOX-xxx@0",
        provider_type="ARCA",
        status="online",
    )
    defaults.update(kwargs)
    return PaasDeviceInfo(**defaults)


def make_paas_device_list_response(**kwargs) -> PaasDeviceListResponse:
    defaults = dict(
        bot_id="bot-1",
        entity_id="entity-1",
        bot_type="personal",
        paas_devices=[make_paas_device_info()],
    )
    defaults.update(kwargs)
    return PaasDeviceListResponse(**defaults)


def make_ttl_extend_result(**kwargs) -> TTLExtendResult:
    defaults = dict(
        bot_id="bot-1",
        bot_type="personal",
        total_devices=1,
        extended_count=1,
        skipped_count=0,
        failed_count=0,
        details=[
            TTLInfo(
                paas_device_id="ARCA-SANDBOX-xxx@0",
                old_expiration_time=datetime(2026, 1, 1),
                new_expiration_time=datetime(2026, 1, 2),
                success=True,
            )
        ],
    )
    defaults.update(kwargs)
    return TTLExtendResult(**defaults)


def make_paas_health_checker_result(**kwargs) -> PaasHealthCheckerResult:
    defaults = dict(
        paas_device_id="ARCA-SANDBOX-xxx@0",
        overall_healthy=True,
        checkers={},
    )
    defaults.update(kwargs)
    return PaasHealthCheckerResult(**defaults)


def make_bot_health_check_result(**kwargs) -> BotHealthCheckResult:
    defaults = dict(
        bot_id="bot-1",
        entity_id="entity-1",
        bot_type="personal",
        overall_healthy=True,
        healthy_count=1,
        unhealthy_count=0,
        devices=[make_paas_health_checker_result()],
    )
    defaults.update(kwargs)
    return BotHealthCheckResult(**defaults)


def make_bot_alive_check_result(**kwargs) -> BotAliveCheckResult:
    defaults = dict(
        bot_id="bot-1",
        entity_id="entity-1",
        bot_type="personal",
        minutes=1440,
        overall_alive=True,
        live_count=1,
        idle_count=0,
        unknown_count=0,
        error_count=0,
        devices=[
            AliveDeviceInfo(
                paas_device_id="ARCA-SANDBOX-xxx@0",
                status=DeviceAliveStatus.LIVE,
            )
        ],
    )
    defaults.update(kwargs)
    return BotAliveCheckResult(**defaults)


# ============ Error Helper Tests ============


class TestErrorHelpers:
    """测试错误辅助函数"""

    def test_error_codes(self):
        assert ErrorCode.INVALID_REQUEST == "INVALID_REQUEST"
        assert ErrorCode.UNSUPPORTED_PROVIDER == "UNSUPPORTED_PROVIDER"
        assert ErrorCode.SANDBOX_NOT_FOUND == "SANDBOX_NOT_FOUND"
        assert ErrorCode.INTERNAL_ERROR == "INTERNAL_ERROR"

    def test_raise_not_found(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            raise_not_found(ErrorCode.SANDBOX_NOT_FOUND, "Sandbox not found")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error_code"] == ErrorCode.SANDBOX_NOT_FOUND

    def test_raise_bad_request(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            raise_bad_request(ErrorCode.INVALID_REQUEST, "Invalid request")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error_code"] == ErrorCode.INVALID_REQUEST

    @pytest.mark.asyncio
    async def test_validate_bot_health_api_key_allowed(self, mock_api_key):
        """Validates that a health-checker API key passes."""
        result = await validate_bot_health_api_key(mock_api_key)
        assert result == mock_api_key

    @pytest.mark.asyncio
    async def test_validate_bot_health_api_key_forbidden(self):
        """Validates that a non-health-checker API key is rejected with 403."""
        from fastapi import HTTPException

        wrong_key = APIKeyRecord(
            id=2,
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            api_key_hash="hash",
            api_key_prefix="sk_other_",
            key_name="Other Key",
            app_id="other_app",
            app_type="some-other-type",
            description="",
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="ACTIVE",
            owner="test_user",
            tenant="default",
            env="prod",
            creator="test_user",
            modifier=None,
            policy=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            await validate_bot_health_api_key(wrong_key)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "FORBIDDEN"


class TestParseStatusesFromList:
    """Tests for _parse_statuses_from_list helper."""

    def test_none_returns_online(self):
        assert _parse_statuses_from_list(None) == ["online"]

    def test_empty_list_returns_all(self):
        assert _parse_statuses_from_list([]) == ["draft", "validating", "online"]

    def test_non_empty_returns_as_is(self):
        assert _parse_statuses_from_list(["draft", "online"]) == ["draft", "online"]


# ============ Endpoint Tests ============


class TestActiveBotsEndpoint:
    """GET /active_bots"""

    def test_success(self, client, mock_service):
        mock_service.list_all_active_bot_device.return_value = (
            2,
            [
                make_bot_device_info(bot_id="bot-1"),
                make_bot_device_info(bot_id="bot-2"),
            ],
        )

        response = client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"page": 1, "page_size": 20},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 2
        assert len(data["data"]["items"]) == 2
        assert data["data"]["items"][0]["bot_id"] == "bot-1"

    def test_invalid_bot_type(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"bot_type": "invalid_type"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST

    def test_empty_env(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"env": ""},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST

    def test_internal_error(self, client, mock_service):
        mock_service.list_all_active_bot_device.side_effect = BotHealthCheckerError(
            "DB error"
        )

        response = client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"page": 1, "page_size": 20},
        )

        assert response.status_code == 500
        assert response.json()["detail"]["error_code"] == ErrorCode.INTERNAL_ERROR

    def test_unsupported_provider_error(self, client, mock_service):
        mock_service.list_all_active_bot_device.side_effect = (
            UnsupportedDeviceProviderError("unknown-platform")
        )

        response = client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"page": 1, "page_size": 20},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.UNSUPPORTED_PROVIDER


class TestDevicesEndpoint:
    """GET /devices"""

    def test_success(self, client, mock_service):
        mock_service.list_paas_device_by_bot.return_value = (
            make_paas_device_list_response()
        )

        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["bot_id"] == "bot-1"
        assert len(data["data"]["paas_devices"]) == 1

    def test_sandbox_not_found(self, client, mock_service):
        mock_service.list_paas_device_by_bot.side_effect = SandboxNotFoundError("bot-1")

        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == ErrorCode.SANDBOX_NOT_FOUND

    def test_unsupported_provider(self, client, mock_service):
        mock_service.list_paas_device_by_bot.side_effect = (
            UnsupportedDeviceProviderError("unknown")
        )

        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.UNSUPPORTED_PROVIDER

    def test_internal_error(self, client, mock_service):
        mock_service.list_paas_device_by_bot.side_effect = BotHealthCheckerError(
            "Query failed"
        )

        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 500
        assert response.json()["detail"]["error_code"] == ErrorCode.INTERNAL_ERROR

    def test_invalid_status(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={
                "bot_id": "bot-1",
                "entity_id": "entity-1",
                "statuses": "invalid_status",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST

    def test_empty_env(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={
                "bot_id": "bot-1",
                "entity_id": "entity-1",
                "env": "",
            },
        )

        assert response.status_code == 400

    def test_statuses_all_returns_all(self, client, mock_service):
        """Empty statuses string returns all statuses (draft, validating, online)."""
        mock_service.list_paas_device_by_bot.return_value = (
            make_paas_device_list_response()
        )

        response = client.get(
            "/api/v1/bot-health-checker/devices",
            params={
                "bot_id": "bot-1",
                "entity_id": "entity-1",
                "statuses": "",
            },
        )

        assert response.status_code == 200
        # Should call with all three statuses
        mock_service.list_paas_device_by_bot.assert_called_once()
        _, kwargs = mock_service.list_paas_device_by_bot.call_args
        assert kwargs["statuses"] == ["draft", "validating", "online"]


class TestExtendTTLEndpoint:
    """POST /ttl/extend"""

    def test_success(self, client, mock_service):
        mock_service.extend_ttl_by_bot.return_value = make_ttl_extend_result()

        response = client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["bot_id"] == "bot-1"
        assert data["data"]["extended_count"] == 1

    def test_sandbox_not_found(self, client, mock_service):
        mock_service.extend_ttl_by_bot.side_effect = SandboxNotFoundError("bot-1")

        response = client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 404

    def test_unsupported_provider(self, client, mock_service):
        mock_service.extend_ttl_by_bot.side_effect = UnsupportedDeviceProviderError(
            "unknown"
        )

        response = client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 400

    def test_internal_error(self, client, mock_service):
        mock_service.extend_ttl_by_bot.side_effect = BotHealthCheckerError(
            "Extend failed"
        )

        response = client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 500

    def test_empty_env_rejected(self, client):
        response = client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"bot_id": "bot-1", "entity_id": "entity-1", "env": ""},
        )

        assert response.status_code == 400


class TestHealthEndpoint:
    """GET /health"""

    def test_success(self, client, mock_service):
        mock_service.check_health_by_bot.return_value = make_bot_health_check_result()

        response = client.get(
            "/api/v1/bot-health-checker/health",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["overall_healthy"] is True

    def test_sandbox_not_found(self, client, mock_service):
        mock_service.check_health_by_bot.side_effect = SandboxNotFoundError("bot-1")

        response = client.get(
            "/api/v1/bot-health-checker/health",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 404

    def test_unsupported_provider(self, client, mock_service):
        mock_service.check_health_by_bot.side_effect = UnsupportedDeviceProviderError(
            "unknown"
        )

        response = client.get(
            "/api/v1/bot-health-checker/health",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 400

    def test_internal_error(self, client, mock_service):
        mock_service.check_health_by_bot.side_effect = BotHealthCheckerError(
            "Check failed"
        )

        response = client.get(
            "/api/v1/bot-health-checker/health",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 500

    def test_empty_env(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/health",
            params={"bot_id": "bot-1", "entity_id": "entity-1", "env": ""},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST

    def test_invalid_status(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/health",
            params={
                "bot_id": "bot-1",
                "entity_id": "entity-1",
                "statuses": "online,invalid",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST


class TestAliveEndpoint:
    """GET /alive"""

    def test_success(self, client, mock_service):
        mock_service.check_alive_by_bot.return_value = make_bot_alive_check_result()

        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["overall_alive"] is True
        assert data["data"]["live_count"] == 1

    def test_sandbox_not_found(self, client, mock_service):
        mock_service.check_alive_by_bot.side_effect = SandboxNotFoundError("bot-1")

        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 404

    def test_unsupported_provider(self, client, mock_service):
        mock_service.check_alive_by_bot.side_effect = UnsupportedDeviceProviderError(
            "unknown"
        )

        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 400

    def test_internal_error(self, client, mock_service):
        mock_service.check_alive_by_bot.side_effect = BotHealthCheckerError(
            "Alive check failed"
        )

        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={"bot_id": "bot-1", "entity_id": "entity-1"},
        )

        assert response.status_code == 500

    def test_empty_env(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={"bot_id": "bot-1", "entity_id": "entity-1", "env": ""},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST

    def test_invalid_status(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={
                "bot_id": "bot-1",
                "entity_id": "entity-1",
                "statuses": "draft,bogus",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST

    def test_with_custom_minutes(self, client, mock_service):
        mock_service.check_alive_by_bot.return_value = make_bot_alive_check_result()

        response = client.get(
            "/api/v1/bot-health-checker/alive",
            params={
                "bot_id": "bot-1",
                "entity_id": "entity-1",
                "minutes": 60,
            },
        )

        assert response.status_code == 200
        mock_service.check_alive_by_bot.assert_called_once()
        _, kwargs = mock_service.check_alive_by_bot.call_args
        assert kwargs["minutes"] == 60


class TestSandboxEndpoint:
    """GET /sandbox"""

    def test_success(self, client, mock_service):
        mock_service.get_sandbox_info.return_value = {
            "sandbox_id": "ARCA-SANDBOX-xxx",
            "status": "ACTIVE",
            "source_table": "ac_binding",
        }

        response = client.get(
            "/api/v1/bot-health-checker/sandbox",
            params={"sandbox_id": "ARCA-SANDBOX-xxx@0"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["sandbox_id"] == "ARCA-SANDBOX-xxx"

    def test_not_found(self, client, mock_service):
        mock_service.get_sandbox_info.return_value = None

        response = client.get(
            "/api/v1/bot-health-checker/sandbox",
            params={"sandbox_id": "nonexistent"},
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == ErrorCode.SANDBOX_NOT_FOUND

    def test_internal_error(self, client, mock_service):
        mock_service.get_sandbox_info.side_effect = BotHealthCheckerError(
            "Query failed"
        )

        response = client.get(
            "/api/v1/bot-health-checker/sandbox",
            params={"sandbox_id": "ARCA-SANDBOX-xxx@0"},
        )

        assert response.status_code == 500

    def test_empty_sandbox_id(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/sandbox",
            params={"sandbox_id": ""},
        )

        assert response.status_code == 422

    def test_whitespace_only_sandbox_id(self, client):
        response = client.get(
            "/api/v1/bot-health-checker/sandbox",
            params={"sandbox_id": "   "},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == ErrorCode.INVALID_REQUEST


# ============ Validation Tests ============


class TestBotHealthCheckerRouterValidation:
    """FastAPI parameter validation tests."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_db_manager, mock_api_key, mock_service):
        self.app = FastAPI()
        self.app.include_router(router)

        _override_dep(self.app, mock_service)

        async def override_validate_bot_health_api_key():
            return mock_api_key

        self.app.dependency_overrides[validate_bot_health_api_key] = (
            override_validate_bot_health_api_key
        )
        self.client = TestClient(self.app)

    def test_active_bots_invalid_page(self):
        response = self.client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"page": 0, "page_size": 20},
        )
        assert response.status_code == 422

    def test_active_bots_invalid_page_size(self):
        response = self.client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"page": 1, "page_size": 0},
        )
        assert response.status_code == 422

    def test_active_bots_page_size_too_large(self):
        response = self.client.get(
            "/api/v1/bot-health-checker/active_bots",
            params={"page": 1, "page_size": 200},
        )
        assert response.status_code == 422

    def test_devices_missing_bot_id(self):
        response = self.client.get(
            "/api/v1/bot-health-checker/devices",
            params={"entity_id": "entity-1"},
        )
        assert response.status_code == 422

    def test_devices_missing_entity_id(self):
        response = self.client.get(
            "/api/v1/bot-health-checker/devices",
            params={"bot_id": "bot-1"},
        )
        assert response.status_code == 422

    def test_health_missing_params(self):
        response = self.client.get("/api/v1/bot-health-checker/health")
        assert response.status_code == 422

    def test_alive_missing_params(self):
        response = self.client.get("/api/v1/bot-health-checker/alive")
        assert response.status_code == 422

    def test_sandbox_missing_params(self):
        response = self.client.get("/api/v1/bot-health-checker/sandbox")
        assert response.status_code == 422

    def test_ttl_extend_missing_bot_id(self):
        response = self.client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"entity_id": "entity-1"},
        )
        assert response.status_code == 422

    def test_ttl_extend_missing_entity_id(self):
        response = self.client.post(
            "/api/v1/bot-health-checker/ttl/extend",
            json={"bot_id": "bot-1"},
        )
        assert response.status_code == 422
