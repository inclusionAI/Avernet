"""Unit tests for Open API router error handling.

Covers:
- BotServiceError (e.g., missing tenant) → 400 BUSINESS_ERROR
- BotNotFoundError → 404
- BotNotAvailableError → 503
- Generic Exception → 500

These tests verify the exception→HTTP mapping in run_router and message_router.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    validate_api_key,
)
from secbaas.community.api.bot_runtime import (
    BotNotAvailableError,
    BotNotFoundError,
    BotServiceError,
)
from tests.unit.adapters.web.conftest import iter_api_routes

# ==================== Fixtures ====================

BOT_UUID = "bot-uuid-123"
API_KEY_PREFIX = "key-abc"
TENANT = "test-tenant"
RUN_ID = "run-001"
MESSAGE_ID = "msg-001"


def _make_api_key_record(app_type="baas", app_id=BOT_UUID, tenant=TENANT):
    from datetime import datetime

    from secbaas.community.api.api_gateway import APIKeyRecord

    return APIKeyRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="hash123",
        api_key_prefix=API_KEY_PREFIX,
        key_name="test-key",
        app_id=app_id,
        app_type=app_type,
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test",
        tenant=tenant,
        env="test",
        creator="test",
        modifier=None,
        policy=None,
    )


# ==================== Helper: build test app with overrides ====================


def _override_provide(app, **provide_overrides):
    """Override Provide dependencies by parameter name.

    Args:
        app: FastAPI app with routes included.
        **provide_overrides: keyword args where key is the dep name and
            value is the override factory (lambda).
    """
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            from secbaas.community.bootstrap import Provide

            if isinstance(dep.call, Provide) and dep.name in provide_overrides:
                app.dependency_overrides[dep.call] = provide_overrides[dep.name]


def _build_run_app(bot_runner) -> FastAPI:
    """Build a FastAPI app with run_router and overridden dependencies."""
    from secbaas.community.adapters.web.routers.open_api.run_router import (
        router as run_router,
    )
    from secbaas.community.core.repository.bot_run import BotRunRepository

    app = FastAPI()
    app.include_router(run_router)

    api_key_record = _make_api_key_record()

    app.dependency_overrides[validate_api_key] = lambda: api_key_record

    run_repo = MagicMock(spec=BotRunRepository)
    run_repo.insert_run.return_value = RUN_ID

    _override_provide(
        app,
        bot_runner=lambda: bot_runner,
        bot_run_repo=lambda: run_repo,
    )

    return app


def _build_message_app(bot_runner) -> FastAPI:
    """Build a FastAPI app with message_router and overridden dependencies."""
    from secbaas.community.adapters.web.routers.open_api.message_router import (
        router as message_router,
    )
    from secbaas.community.core.repository.bot_run import BotRunRepository

    app = FastAPI()
    app.include_router(message_router)

    # /messages requires app_type="system"
    api_key_record = _make_api_key_record(app_type="system")

    app.dependency_overrides[validate_api_key] = lambda: api_key_record

    run_repo = MagicMock(spec=BotRunRepository)
    run_repo.insert_run.return_value = MESSAGE_ID

    _override_provide(
        app,
        bot_runner=lambda: bot_runner,
        bot_run_repo=lambda: run_repo,
    )

    return app


# ==================== TestRunRouterErrorHandling ====================


class TestRunRouterErrorHandling:
    """Test /openapi/v1/runs exception → HTTP status mapping."""

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        """BotServiceError (e.g., missing tenant) → 400 with BUSINESS_ERROR code."""
        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(
            side_effect=BotServiceError(
                "tenant is required in metadata for BaaS bot session, bot_id=bot-uuid-123"
            )
        )

        app = _build_run_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/runs",
            json={"message": "hello"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == 60001
        assert "tenant is required" in detail["message"]

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        """BotNotFoundError → 404."""
        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(side_effect=BotNotFoundError(BOT_UUID))

        app = _build_run_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/runs",
            json={"message": "hello"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_bot_not_available_returns_503(self):
        """BotNotAvailableError → 503."""
        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(
            side_effect=BotNotAvailableError(BOT_UUID, "INACTIVE")
        )

        app = _build_run_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/runs",
            json={"message": "hello"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        """Unexpected Exception → 500."""
        mock_runner = AsyncMock()
        mock_runner.chat = AsyncMock(side_effect=RuntimeError("unexpected"))

        app = _build_run_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/runs",
            json={"message": "hello"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 500


# ==================== TestMessageRouterErrorHandling ====================


class TestMessageRouterErrorHandling:
    """Test /openapi/v1/messages exception → HTTP status mapping."""

    @pytest.mark.asyncio
    async def test_bot_service_error_returns_400(self):
        """BotServiceError (e.g., missing tenant) → 400 with BUSINESS_ERROR code."""
        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(
            side_effect=BotServiceError(
                "tenant is required in metadata for BaaS bot session, bot_id=bot-uuid-123"
            )
        )

        app = _build_message_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/messages",
            json={"message": "hello", "bot_id": BOT_UUID},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == 60001
        assert "tenant is required" in detail["message"]

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_404(self):
        """BotNotFoundError → 404."""
        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(side_effect=BotNotFoundError(BOT_UUID))

        app = _build_message_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/messages",
            json={"message": "hello", "bot_id": BOT_UUID},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_bot_not_available_returns_503(self):
        """BotNotAvailableError → 503."""
        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(
            side_effect=BotNotAvailableError(BOT_UUID, "INACTIVE")
        )

        app = _build_message_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/messages",
            json={"message": "hello", "bot_id": BOT_UUID},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        """Unexpected Exception → 500."""
        mock_runner = AsyncMock()
        mock_runner.deliver_message = AsyncMock(side_effect=RuntimeError("unexpected"))

        app = _build_message_app(mock_runner)

        client = TestClient(app)
        response = client.post(
            "/openapi/v1/messages",
            json={"message": "hello", "bot_id": BOT_UUID},
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 500
