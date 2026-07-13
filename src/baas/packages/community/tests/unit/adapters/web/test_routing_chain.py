"""Integration tests for OpenAPI routing chain with mocked DB and WebSocket.

Tests the full chain: validate_api_key → BotRunner → BotService.
Mocks the DI dependencies at the FastAPI Depends level.

Three scenarios:
- app_type="baas" → BaasBotService (backward compat)
- app_type="bot" + device_provider="arca" → BotRunner → ClawBotService
- app_type="bot" + device_provider="baas" → BotRunner → BaasBotService (delegated)
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from secbaas.adapters.web.routers.open_api.dependencies import (
    validate_api_key,
)
from secbaas.api.api_gateway import APIKeyRecord
from secbaas.api.bot_runtime import (
    BotNotAvailableError,
    BotNotFoundError,
    BotResponse,
    BotServiceError,
    SessionInfo,
)
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.core.service.bot_run import BotRunner, BotServiceSelector
from secbaas.core.service.bot_run._internal_protocols import MessageDispatcher
from secbaas.spi.bot_service import BotBindingData
from tests.unit.adapters.web.conftest import iter_api_routes

API_KEY_PREFIX = "key-abc"
TENANT = "test-tenant"
BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"
SANDBOX_ID = "arca-sandbox-test-000@0"
DEVICE_ID_ARCA = "staff_test_bot_uuid"
DEVICE_ID_BAAS = "test-device-uuid-00000000000000000000000000001"


def _override_provide(app, **provide_overrides):
    """Override Provide dependencies by parameter name.

    Args:
        app: FastAPI app with routes included.
        **provide_overrides: keyword args where key is the dep name and
            value is the override factory (lambda).
    """
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide) and dep.name in provide_overrides:
                app.dependency_overrides[dep.call] = provide_overrides[dep.name]


def _make_session_info(session_id="agent:main:sess-int-001"):
    return SessionInfo(
        session_id=session_id,
        bot_id=BOT_ID,
        status="active",
        created_at=datetime.now(),
        metadata={"tenant": TENANT, "invoker": API_KEY_PREFIX},
    )


def _make_runner(selector, run_repo, binding_data=None):
    """Create a BotRunner with a mocked BotServicePlugin and dispatcher.

    Args:
        selector: BotServiceSelector instance
        run_repo: Mock BotRunRepository
        binding_data: BotBindingData or None — what plugin.get_binding() returns.
            If None, get_binding() raises PaasError(PLATFORM_UNAVAILABLE).
    """
    from secbaas.api.device_manage import ErrorCode, PaasError

    mock_plugin = MagicMock()
    if binding_data is not None:
        mock_plugin.get_binding = AsyncMock(return_value=binding_data)
    else:
        mock_plugin.get_binding = AsyncMock(
            side_effect=PaasError(ErrorCode.NOT_FOUND, "not found")
        )
    mock_plugin.report = AsyncMock()
    # 默认 dispatcher: dispatch_send 为 AsyncMock（不实际发送）
    mock_dispatcher = MagicMock(spec=MessageDispatcher)
    mock_dispatcher.accepts = MagicMock(return_value=True)
    mock_dispatcher.dispatch_send = AsyncMock()
    mock_dispatcher.dispatch_inject = AsyncMock()
    # 默认 run_repo: get_by_run_id 返回 None（首次请求，非幂等命中）
    run_repo.get_by_run_id = MagicMock(return_value=None)
    return BotRunner(
        bot_service_selector=selector,
        run_repository=run_repo,
        bot_service_plugin=mock_plugin,
        dispatchers=[mock_dispatcher],
    )


@pytest.fixture
def api_key_record_baas():
    return APIKeyRecord(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="hash123",
        api_key_prefix=API_KEY_PREFIX,
        key_name="test-key",
        app_id="bot-uuid-123",
        app_type="baas",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test",
        tenant=TENANT,
        env="test",
        creator="test",
        modifier=None,
        policy=None,
    )


@pytest.fixture
def api_key_record_bot():
    return APIKeyRecord(
        id=2,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="hash456",
        api_key_prefix=API_KEY_PREFIX,
        key_name="test-key",
        app_id=f"{BOT_ID}:{ENTITY_ID}",
        app_type="bot",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test",
        tenant=TENANT,
        env="test",
        creator="test",
        modifier=None,
        policy=None,
    )


def _make_arca_binding_data():
    return BotBindingData(
        bot_id=BOT_ID,
        owner_id=ENTITY_ID,
        bot_type="personal",
        engine_type="openclaw",
        binding_id=100101,
        device_provider="arca",
        device_id=DEVICE_ID_ARCA,
    )


def _make_baas_binding_data():
    return BotBindingData(
        bot_id=BOT_ID,
        owner_id=ENTITY_ID,
        bot_type="service",
        engine_type="openclaw",
        binding_id=100002,
        device_provider="baas",
        device_id=DEVICE_ID_BAAS,
    )


# ==================== Scenario 1: app_type=baas ====================


class TestBaasAppTypeFlow:
    @pytest.fixture(autouse=True)
    def setup(self, api_key_record_baas):
        from secbaas.adapters.web.routers.open_api import run_router as router

        mock_baas = MagicMock()
        mock_baas.create_session = AsyncMock(return_value=_make_session_info())
        mock_baas.send_message = AsyncMock(
            return_value=BotResponse(content="baas reply"),
        )

        mock_run = MagicMock()
        mock_run.insert_run = MagicMock()

        app = FastAPI()

        async def override_val(ak=Depends(lambda: api_key_record_baas)):
            return ak

        selector = BotServiceSelector(claw_service=mock_baas, baas_service=mock_baas)
        runner = _make_runner(
            selector, mock_run, binding_data=_make_baas_binding_data()
        )

        app.dependency_overrides[validate_api_key] = override_val
        app.include_router(router.router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        self._app = app
        self._baas = mock_baas
        self._runner = runner

    @pytest.mark.asyncio
    async def test_create_session_and_dispatch(self):
        with TestClient(self._app) as client:
            resp = client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        assert resp.status_code == 200
        self._baas.create_session.assert_called_once()
        self._runner._dispatchers[0].dispatch_send.assert_called_once()


class TestBaasAppTypeErrorFlow:
    @pytest.fixture(autouse=True)
    def setup(self, api_key_record_baas):
        from secbaas.adapters.web.routers.open_api import run_router as router

        mock_baas = MagicMock()
        mock_baas.create_session = AsyncMock(
            side_effect=BotServiceError("baas unavailable"),
        )
        mock_run = MagicMock()

        app = FastAPI()

        async def override_val(ak=Depends(lambda: api_key_record_baas)):
            return ak

        selector = BotServiceSelector(claw_service=mock_baas, baas_service=mock_baas)
        runner = _make_runner(
            selector, mock_run, binding_data=_make_baas_binding_data()
        )

        app.dependency_overrides[validate_api_key] = override_val
        app.include_router(router.router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        self._app = app

    def test_baas_service_error_returns_400(self):
        with TestClient(self._app) as client:
            resp = client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == 60001


# ==================== Scenario 2: app_type=bot + arca binding ====================


class TestArcaBindingFlow:
    @pytest.fixture(autouse=True)
    def setup(self, api_key_record_bot):
        from secbaas.adapters.web.routers.open_api import run_router as router

        mock_claw = MagicMock()
        mock_claw.create_session = AsyncMock(return_value=_make_session_info())
        mock_claw.send_message = AsyncMock(
            return_value=BotResponse(content="claw reply")
        )

        mock_run = MagicMock()
        mock_run.insert_run = MagicMock()

        binding_data = _make_arca_binding_data()
        app = FastAPI()

        async def override_val(ak=Depends(lambda: api_key_record_bot)):
            return ak

        selector = BotServiceSelector(claw_service=mock_claw, baas_service=MagicMock())
        runner = _make_runner(selector, mock_run, binding_data=binding_data)

        app.dependency_overrides[validate_api_key] = override_val
        app.include_router(router.router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        self._app = app
        self._claw = mock_claw
        self._runner = runner

    def test_binding_info_passed_to_create_session(self):
        with TestClient(self._app) as client:
            client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        kw = self._claw.create_session.call_args.kwargs
        # arca provider: sandbox_id == device_id
        assert kw["binding_info"].sandbox_id == DEVICE_ID_ARCA
        assert kw["binding_info"].device_provider == "arca"

    @pytest.mark.asyncio
    async def test_full_chat_cycle(self):
        with TestClient(self._app) as client:
            client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        self._claw.create_session.assert_called_once()
        self._runner._dispatchers[0].dispatch_send.assert_called_once()


class TestArcaBindingErrorFlow:
    @pytest.fixture(autouse=True)
    def setup(self, api_key_record_bot):
        from secbaas.adapters.web.routers.open_api.run_router import (
            router as run_router,
        )

        mock_claw = MagicMock()
        mock_claw.create_session = AsyncMock(
            side_effect=BotServiceError("ClawBotService requires binding_info"),
        )
        mock_run = MagicMock()

        app = FastAPI()

        async def override_val(ak=Depends(lambda: api_key_record_bot)):
            return ak

        selector = BotServiceSelector(claw_service=mock_claw, baas_service=MagicMock())
        runner = _make_runner(selector, mock_run, binding_data=None)

        app.dependency_overrides[validate_api_key] = override_val
        app.include_router(run_router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )
        self._app = app

    def test_no_binding_info_returns_400(self, api_key_record_bot):
        with TestClient(self._app) as client:
            resp = client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == 60001

    def test_bot_not_found_returns_404(self, api_key_record_bot):
        from secbaas.adapters.web.routers.open_api.run_router import router

        mock_claw = MagicMock()
        mock_claw.create_session = AsyncMock(
            side_effect=BotNotFoundError("bot not found")
        )
        mock_run = MagicMock()
        app = FastAPI()

        async def ov(ak=Depends(lambda: api_key_record_bot)):
            return ak

        selector = BotServiceSelector(claw_service=mock_claw, baas_service=MagicMock())
        runner = _make_runner(
            selector, mock_run, binding_data=_make_arca_binding_data()
        )

        app.dependency_overrides[validate_api_key] = ov
        app.include_router(router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        with TestClient(app) as client:
            resp = client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        assert resp.status_code == 404

    def test_bot_not_available_returns_503(self, api_key_record_bot):
        from secbaas.adapters.web.routers.open_api.run_router import router

        mock_claw = MagicMock()
        mock_claw.create_session = AsyncMock(
            side_effect=BotNotAvailableError("bot offline", "unavailable"),
        )
        mock_run = MagicMock()
        app = FastAPI()

        async def ov(ak=Depends(lambda: api_key_record_bot)):
            return ak

        selector = BotServiceSelector(claw_service=mock_claw, baas_service=MagicMock())
        runner = _make_runner(
            selector, mock_run, binding_data=_make_arca_binding_data()
        )

        app.dependency_overrides[validate_api_key] = ov
        app.include_router(router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        with TestClient(app) as client:
            resp = client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        assert resp.status_code == 503


class TestBaasBindingFlow:
    @pytest.fixture(autouse=True)
    def setup(self, api_key_record_bot):
        from secbaas.adapters.web.routers.open_api import run_router as router

        mock_baas = MagicMock()
        mock_baas.create_session = AsyncMock(return_value=_make_session_info())
        mock_baas.send_message = AsyncMock(
            return_value=BotResponse(content="baas delegated")
        )

        mock_run = MagicMock()
        mock_run.insert_run = MagicMock()

        binding_data = _make_baas_binding_data()
        app = FastAPI()

        async def override_val(ak=Depends(lambda: api_key_record_bot)):
            return ak

        selector = BotServiceSelector(claw_service=MagicMock(), baas_service=mock_baas)
        runner = _make_runner(selector, mock_run, binding_data=binding_data)

        app.dependency_overrides[validate_api_key] = override_val
        app.include_router(router.router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        self._app = app
        self._baas = mock_baas
        self._runner = runner

    def test_bot_id_overridden_with_device_id(self):
        with TestClient(self._app) as client:
            client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        kw = self._baas.create_session.call_args.kwargs
        assert kw["bot_id"] == DEVICE_ID_BAAS
        assert kw["bot_id"] != f"{BOT_ID}:{ENTITY_ID}"

    @pytest.mark.asyncio
    async def test_delegated_chat_cycle(self):
        with TestClient(self._app) as client:
            client.post(
                "/openapi/v1/runs",
                json={"message": "hello"},
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        self._baas.create_session.assert_called_once()
        self._runner._dispatchers[0].dispatch_send.assert_called_once()


# ==================== Scenario 4: get_run_result ====================


class TestRunResultFlow:
    @pytest.fixture(autouse=True)
    def setup(self, api_key_record_bot):
        from secbaas.adapters.web.routers.open_api import run_router as router

        run_record = MagicMock()
        run_record.run_id = "test-run-id"
        run_record.bot_id = f"{BOT_ID}:{ENTITY_ID}"
        run_record.api_key_prefix = API_KEY_PREFIX
        run_record.status = "SUCCESS"
        run_record.result_content = "hello from claw"
        run_record.result_extra = None
        run_record.metadata = None
        run_record.error = None
        run_record.gmt_create = datetime.now()
        run_record.completed_at = datetime.now()
        run_record.gmt_modified = datetime.now()

        mock_run = MagicMock()
        mock_run.get_by_run_id = MagicMock(return_value=run_record)

        mock_service = MagicMock()
        app = FastAPI()

        async def override_val(ak=Depends(lambda: api_key_record_bot)):
            return ak

        selector = BotServiceSelector(
            claw_service=mock_service, baas_service=mock_service
        )
        runner = _make_runner(selector, mock_run, binding_data=None)
        # _make_runner overwrites get_by_run_id to return None;
        # for get_result we need it to return the actual record
        mock_run.get_by_run_id = MagicMock(return_value=run_record)

        app.dependency_overrides[validate_api_key] = override_val
        app.include_router(router.router)

        _override_provide(
            app,
            bot_runner=lambda: runner,
            bot_run_repo=lambda: mock_run,
        )

        self._app = app
        self._run = mock_run

    def test_get_run_result(self):
        with TestClient(self._app) as client:
            resp = client.get(
                "/openapi/v1/runs/test-run-id",
                headers={"Authorization": "Bearer test_key_12345678"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "SUCCESS"
        assert data["data"]["result"]["content"] == "hello from claw"
