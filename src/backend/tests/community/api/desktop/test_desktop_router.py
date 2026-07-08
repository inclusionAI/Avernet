"""Tests for desktop API router."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotService
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


@pytest.fixture
def mock_user():
    """Mock authenticated user."""
    user = MagicMock(spec=AuthenticatedIdentity)
    user.staffId = "u001"
    user.operatorName = "test_user"
    user.nickName = "Test User"
    user.tenantId = "default"
    return user


def _attach_mock_injector(app, desktop_service=None, passport_plugin=None, auth_rel_plugin=None, baas_service=None):
    """Attach a DI injector with DesktopBotService mock."""
    svc = desktop_service if desktop_service is not None else MagicMock(spec=DesktopBotService)
    pp = passport_plugin if passport_plugin is not None else MagicMock(spec=PassportPlugin)
    arp = auth_rel_plugin if auth_rel_plugin is not None else MagicMock(spec=AuthRelationshipPlugin)

    # Mock AuthPlugin: ``resolve_user_from_request`` raises Unauthorized
    # by default (matches LocalAuth's behavior on missing identity).
    # Tests that need an authed user override ``get_current_user`` via
    # ``app.dependency_overrides`` and bypass this code path.
    from agentclaw.community.core.errors import Unauthorized as _UnauthorizedErr
    _auth_mock = MagicMock(spec=AuthPlugin)
    _auth_mock.resolve_user_from_request = AsyncMock(
        side_effect=_UnauthorizedErr("Local mode: user identity required."),
    )

    from agentclaw.community.api.desktop_bot_service import DesktopBotServiceProtocol
    from agentclaw.community.api.baas_service import BaasServiceProtocol

    _baas = baas_service if baas_service is not None else MagicMock(spec=BaasServiceProtocol)

    class _M(Module):
        def configure(self, binder):
            binder.bind(DesktopBotService, to=svc)
            binder.bind(DesktopBotServiceProtocol, to=svc)
            binder.bind(AuthPlugin, to=_auth_mock)
            binder.bind(PassportPlugin, to=pp)
            binder.bind(AuthRelationshipPlugin, to=arp)
            binder.bind(BaasServiceProtocol, to=_baas)

    attach_injector(app, Injector([_M()]))

    # Bare FastAPI apps in tests lack the global exception handlers registered
    # in api/app.py, so Unauthorized raised by get_current_user collapses to
    # 500 instead of 401.  Add the handler locally so auth-failure tests work.
    from agentclaw.community.core.errors import Unauthorized
    from fastapi.responses import JSONResponse

    @app.exception_handler(Unauthorized)
    async def _unauthorized_handler(request, exc):
        return JSONResponse(status_code=401, content={"detail": str(exc)})


@pytest.fixture
def desktop_service():
    """Mock DesktopBotService."""
    return MagicMock(spec=DesktopBotService)


@pytest.fixture
def passport_plugin():
    """Mock PassportPlugin."""
    return MagicMock(spec=PassportPlugin)


@pytest.fixture
def auth_rel_plugin():
    """Mock AuthRelationshipPlugin."""
    return MagicMock(spec=AuthRelationshipPlugin)


@pytest.fixture
def client(mock_user, desktop_service, passport_plugin, auth_rel_plugin):
    """Create test client with mocked DesktopBotService."""
    from agentclaw.community.adapters.http.desktop.router import bot_router, device_router
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user

    app = FastAPI()
    app.include_router(device_router)
    app.include_router(bot_router)

    app.dependency_overrides[get_current_user] = lambda: mock_user
    _attach_mock_injector(app, desktop_service, passport_plugin, auth_rel_plugin)

    yield TestClient(app)
    app.dependency_overrides.clear()


class TestDesktopListDevices:
    LIST_PATH = "/api/desktop/devices"

    def test_list_returns_devices(self, client, desktop_service):
        """GET /api/desktop/devices returns paginated machine list."""
        desktop_service.list_devices.return_value = (2, [
            {
                "machine_id": "m-001", "machine_name": "MacBook Pro",
                "status": "ACTIVE", "last_online_at": "2026-05-13T10:00:00",
                "created_at": "2026-05-01T08:00:00",
            },
            {
                "machine_id": "m-002", "machine_name": "Linux Box",
                "status": "ACTIVE", "last_online_at": "2026-05-13T09:30:00",
                "created_at": "2026-05-05T10:00:00",
            },
        ])

        resp = client.get(self.LIST_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["machine_id"] == "m-001"
        assert data["items"][0]["machine_name"] == "MacBook Pro"
        assert data["items"][1]["machine_id"] == "m-002"

    def test_list_empty(self, client, desktop_service):
        desktop_service.list_devices.return_value = (0, [])

        resp = client.get(self.LIST_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_filters_by_status(self, client, desktop_service):
        desktop_service.list_devices.return_value = (0, [])

        resp = client.get(f"{self.LIST_PATH}?status=ACTIVE")

        assert resp.status_code == 200
        desktop_service.list_devices.assert_called_once_with(
            user_id="u001", page=1, page_size=20, status="ACTIVE",
        )

    def test_list_pagination(self, client, desktop_service):
        desktop_service.list_devices.return_value = (0, [])

        resp = client.get(f"{self.LIST_PATH}?page=2&page_size=10")

        assert resp.status_code == 200
        desktop_service.list_devices.assert_called_once_with(
            user_id="u001", page=2, page_size=10, status=None,
        )

    def test_list_no_staff_id_returns_401(self, client, desktop_service):
        """Returns 401 when authenticated user has no staffId."""
        from agentclaw.community.adapters.http.desktop.router import device_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(device_router)
        _attach_mock_injector(app, desktop_service)

        anon_user = MagicMock(spec=AuthenticatedIdentity)
        anon_user.staffId = None
        app.dependency_overrides[get_current_user] = lambda: anon_user

        anon_client = TestClient(app)
        resp = anon_client.get(self.LIST_PATH)

        assert resp.status_code == 401

    def test_list_without_auth_returns_401(self):
        from agentclaw.community.adapters.http.desktop.router import device_router

        app = FastAPI()
        app.include_router(device_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.get(self.LIST_PATH)
        assert resp.status_code == 401

    def test_list_service_error(self, client, desktop_service):
        """Returns error when list_devices raises DesktopBotServiceError."""
        from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotServiceError
        desktop_service.list_devices.side_effect = DesktopBotServiceError("BaaS failed")

        resp = client.get(self.LIST_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

    def test_list_unexpected_error(self, client, desktop_service):
        """Returns error when list_devices raises unexpected Exception."""
        desktop_service.list_devices.side_effect = RuntimeError("unexpected")

        resp = client.get(self.LIST_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


class TestDesktopListFiles:
    FILES_PATH = "/api/desktop/devices/m-001/files"

    def test_list_files_root(self, client, desktop_service):
        desktop_service.list_directory.return_value = {
            "name": "Desktop",
            "children": [
                {"name": "folder1", "children": []},
                {"name": "file1.txt"},
            ],
        }

        resp = client.get(self.FILES_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["name"] == "Desktop"
        assert len(data["data"]["children"]) == 2
        desktop_service.list_directory.assert_called_once_with(
            machine_id="m-001", dir="~/Desktop",
        )

    def test_list_files_with_dir(self, client, desktop_service):
        desktop_service.list_directory.return_value = {
            "name": "Documents",
            "children": [{"name": "report.pdf"}],
        }

        resp = client.get(f"{self.FILES_PATH}?dir=~/Documents")

        assert resp.status_code == 200
        desktop_service.list_directory.assert_called_once_with(
            machine_id="m-001", dir="~/Documents",
        )

    def test_list_files_empty(self, client, desktop_service):
        desktop_service.list_directory.return_value = {}

        resp = client.get(self.FILES_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"] == {}

    def test_list_files_no_auth_returns_401(self):
        from agentclaw.community.adapters.http.desktop.router import device_router

        app = FastAPI()
        app.include_router(device_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.get(self.FILES_PATH)
        assert resp.status_code == 401

    def test_list_files_no_staff_id_returns_401(self, client, desktop_service):
        """Returns 401 when authenticated user has no staffId."""
        from agentclaw.community.adapters.http.desktop.router import device_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(device_router)
        _attach_mock_injector(app, desktop_service)

        anon_user = MagicMock(spec=AuthenticatedIdentity)
        anon_user.staffId = None
        app.dependency_overrides[get_current_user] = lambda: anon_user

        anon_client = TestClient(app)
        resp = anon_client.get(self.FILES_PATH)
        assert resp.status_code == 401

    def test_list_files_service_error(self, client, desktop_service):
        """Returns error when list_directory raises DesktopBotServiceError."""
        from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotServiceError
        desktop_service.list_directory.side_effect = DesktopBotServiceError("BaaS failed")

        resp = client.get(self.FILES_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

    def test_list_files_unexpected_error(self, client, desktop_service):
        """Returns error when list_directory raises unexpected Exception."""
        desktop_service.list_directory.side_effect = RuntimeError("unexpected")

        resp = client.get(self.FILES_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


class TestDesktopCreate:
    CREATE_PATH = "/api/desktop/bots"

    def test_create_bot(self, client, desktop_service):
        desktop_service.apply_passport_before_create.return_value = {
            "need_authorization": True,
            "bot_id": "desktop_bot_001",
        }

        resp = client.post(self.CREATE_PATH, json={
            "bot_name": "My Desktop",
            "bot_desc": "My dev machine",
            "machine_id": "m-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 401
        assert data["data"]["bot_id"] == "desktop_bot_001"
        desktop_service.apply_passport_before_create.assert_called_once_with(
            bot={"bot_name": "My Desktop", "bot_desc": "My dev machine"},
            user_id="u001",
            machine_id="m-001",
            mount_path=None,
            avatar_url=None,
            engine_type=None,
        )
        desktop_service.create_after_authorization.assert_not_called()

    def test_create_bot_minimal(self, client, desktop_service):
        """Create with only required fields."""
        desktop_service.apply_passport_before_create.return_value = {
            "need_authorization": True,
            "bot_id": "bot_002",
        }

        resp = client.post(self.CREATE_PATH, json={
            "bot_name": "Minimal", "machine_id": "m-002",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 401
        desktop_service.apply_passport_before_create.assert_called_once_with(
            bot={"bot_name": "Minimal", "bot_desc": None},
            user_id="u001",
            machine_id="m-002",
            mount_path=None,
            avatar_url=None,
            engine_type=None,
        )
        desktop_service.create_after_authorization.assert_not_called()

    def test_create_returns_authorization_links(self, client, desktop_service):
        """apply_passport_before_create 返回的 iframe_url/redirect_url 应透传给前端。"""
        desktop_service.apply_passport_before_create.return_value = {
            "need_authorization": True,
            "bot_id": "desktop_bot_001",
            "iframe_url": "https://auth.example.com/iframe",
            "redirect_url": "https://auth.example.com/redirect",
        }

        resp = client.post(self.CREATE_PATH, json={
            "bot_name": "My Desktop", "machine_id": "m-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 401
        assert data["data"]["iframe_url"] == "https://auth.example.com/iframe"
        assert data["data"]["redirect_url"] == "https://auth.example.com/redirect"
        desktop_service.create_after_authorization.assert_not_called()

    def test_create_unauthorized_returns_401(self):
        from agentclaw.community.adapters.http.desktop.router import bot_router

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.post(self.CREATE_PATH, json={
            "bot_name": "test", "machine_id": "m-001",
        })
        assert resp.status_code == 401


class TestDesktopAuthStatus:
    AUTH_STATUS_PATH = "/api/desktop/bots/auth-status"

    def test_auth_status_pending(self, client, desktop_service, passport_plugin):
        passport_plugin.query_auth_status.return_value = {"status": "PENDING"}

        resp = client.post(self.AUTH_STATUS_PATH, json={
            "bot_id": "desktop_bot_001",
            "bot_name": "My Desktop",
            "machine_id": "m-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "PENDING"
        assert data["data"]["message"] == "授权处理中"
        desktop_service.create_after_authorization.assert_not_called()

    def test_auth_status_issued(self, client, desktop_service, passport_plugin, auth_rel_plugin):
        passport_plugin.query_auth_status.return_value = {"status": "ISSUED"}
        desktop_service.create_after_authorization.return_value = {
            "bot_uuid": "bot-uuid-001",
            "binding_id": 1,
            "bot_id": "desktop_bot_001",
            "agent_code": "ac-001",
        }

        resp = client.post(self.AUTH_STATUS_PATH, json={
            "bot_id": "desktop_bot_001",
            "bot_name": "My Desktop",
            "bot_desc": "desc",
            "machine_id": "m-001",
            "mount_path": "/custom/mount",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["status"] == "ISSUED"
        assert data["data"]["bot"]["bot_uuid"] == "bot-uuid-001"
        desktop_service.create_after_authorization.assert_called_once_with(
            bot={
                "bot_id": "desktop_bot_001",
                "bot_name": "My Desktop",
                "bot_desc": "desc",
                "avatar_url": None,
            },
            user_id="u001",
            machine_id="m-001",
            mount_path="/custom/mount",
            engine_type=None,
        )
        auth_rel_plugin.create_relationship.assert_called_once_with(
            work_no="u001",
            agent_code="ac-001",
            description="Bot owner default authorization",
            operator_work_no="u001",
            operator_name="Test User",
        )

    def test_auth_status_missing_bot_id(self, client, desktop_service, passport_plugin):
        resp = client.post(self.AUTH_STATUS_PATH, json={
            "bot_name": "My Desktop", "machine_id": "m-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        passport_plugin.query_auth_status.assert_not_called()

    def test_auth_status_query_fails(self, client, desktop_service, passport_plugin):
        passport_plugin.query_auth_status.return_value = None

        resp = client.post(self.AUTH_STATUS_PATH, json={
            "bot_id": "desktop_bot_001", "machine_id": "m-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        desktop_service.create_after_authorization.assert_not_called()

    def test_auth_status_unknown_status(self, client, desktop_service, passport_plugin):
        passport_plugin.query_auth_status.return_value = {"status": "REVOKED"}

        resp = client.post(self.AUTH_STATUS_PATH, json={
            "bot_id": "desktop_bot_001", "machine_id": "m-001",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert "REVOKED" in data["message"]
        desktop_service.create_after_authorization.assert_not_called()


class TestDesktopRestart:
    RESTART_PATH = "/api/desktop/bots/desktop_bot_001/restart"

    def test_restart_bot(self, client, desktop_service):
        desktop_service.restart.return_value = {
            "device_id": "m-001", "bot_id": "desktop_bot_001", "status": "PENDING",
        }

        resp = client.post(self.RESTART_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["device_id"] == "m-001"
        assert data["data"]["status"] == "PENDING"
        desktop_service.restart.assert_called_once_with(
            bot_id="desktop_bot_001", user_id="u001",
        )

    def test_restart_bot_not_found(self, client, desktop_service):
        """Returns error when verify_ownership finds no bot."""
        from agentclaw.community.core.errors import NotFound
        desktop_service.verify_ownership.side_effect = NotFound("Bot not found")

        resp = client.post(self.RESTART_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_restart_service_error(self, client, desktop_service):
        """Returns error when restart raises DesktopBotServiceError."""
        from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotServiceError
        desktop_service.restart.side_effect = DesktopBotServiceError("BaaS restart failed")

        resp = client.post(self.RESTART_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

    def test_restart_unauthorized_returns_401(self):
        from agentclaw.community.adapters.http.desktop.router import bot_router

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.post(self.RESTART_PATH)
        assert resp.status_code == 401

    def test_restart_no_staff_id_returns_401(self, client, desktop_service):
        """Returns 401 when authenticated user has no staffId."""
        from agentclaw.community.adapters.http.desktop.router import bot_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app, desktop_service)

        anon_user = MagicMock(spec=AuthenticatedIdentity)
        anon_user.staffId = None
        app.dependency_overrides[get_current_user] = lambda: anon_user

        anon_client = TestClient(app)
        resp = anon_client.post(self.RESTART_PATH)
        assert resp.status_code == 401

    def test_restart_unexpected_error(self, client, desktop_service):
        """Returns error when restart raises unexpected Exception."""
        desktop_service.restart.side_effect = RuntimeError("unexpected")

        resp = client.post(self.RESTART_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


class TestDesktopDelete:
    DELETE_PATH = "/api/desktop/bots/desktop_bot_001"

    def test_delete_bot(self, client, desktop_service):
        desktop_service.delete.return_value = {
            "device_id": "m-001", "bot_id": "desktop_bot_001", "status": "DELETED",
        }

        resp = client.delete(self.DELETE_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["device_id"] == "m-001"
        assert data["data"]["status"] == "DELETED"
        desktop_service.delete.assert_called_once_with(
            bot_id="desktop_bot_001", user_id="u001",
        )

    def test_delete_bot_not_found(self, client, desktop_service):
        """Returns error when verify_ownership finds no bot."""
        from agentclaw.community.core.errors import NotFound
        desktop_service.verify_ownership.side_effect = NotFound("Bot not found")

        resp = client.delete(self.DELETE_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_delete_service_error(self, client, desktop_service):
        """Returns error when delete raises DesktopBotServiceError."""
        from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotServiceError
        desktop_service.delete.side_effect = DesktopBotServiceError("BaaS destroy failed")

        resp = client.delete(self.DELETE_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

    def test_delete_unauthorized_returns_401(self):
        from agentclaw.community.adapters.http.desktop.router import bot_router

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.delete(self.DELETE_PATH)
        assert resp.status_code == 401

    def test_delete_no_staff_id_returns_401(self, client, desktop_service):
        """Returns 401 when authenticated user has no staffId."""
        from agentclaw.community.adapters.http.desktop.router import bot_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app, desktop_service)

        anon_user = MagicMock(spec=AuthenticatedIdentity)
        anon_user.staffId = None
        app.dependency_overrides[get_current_user] = lambda: anon_user

        anon_client = TestClient(app)
        resp = anon_client.delete(self.DELETE_PATH)
        assert resp.status_code == 401

    def test_delete_unexpected_error(self, client, desktop_service):
        """Returns error when delete raises unexpected Exception."""
        desktop_service.delete.side_effect = RuntimeError("unexpected")

        resp = client.delete(self.DELETE_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


class TestDesktopOpenFolder:
    OPEN_FOLDER_PATH = "/api/desktop/bots/desktop_bot_001/open-folder"

    def test_open_folder_success(self, client, desktop_service):
        desktop_service.open_folder.return_value = {
            "bot_id": "desktop_bot_001",
            "workspace_path": "~/.teamclaw/boxes/desktop_bot_001",
        }

        resp = client.post(self.OPEN_FOLDER_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["bot_id"] == "desktop_bot_001"
        assert data["data"]["workspace_path"] == "~/.teamclaw/boxes/desktop_bot_001"
        desktop_service.open_folder.assert_called_once_with(
            bot_id="desktop_bot_001", user_id="u001", folder_path=None,
        )

    def test_open_folder_with_relative_path(self, client, desktop_service):
        """POST with folder_path body passes it through to service."""
        desktop_service.open_folder.return_value = {
            "bot_id": "desktop_bot_001",
        }

        resp = client.post(self.OPEN_FOLDER_PATH, json={
            "folder_path": "src/components",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        desktop_service.open_folder.assert_called_once_with(
            bot_id="desktop_bot_001", user_id="u001", folder_path="src/components",
        )

    def test_open_folder_with_absolute_path(self, client, desktop_service):
        """POST with absolute folder_path body passes it through to service."""
        desktop_service.open_folder.return_value = {
            "bot_id": "desktop_bot_001",
        }

        resp = client.post(self.OPEN_FOLDER_PATH, json={
            "folder_path": "/Users/user/Desktop/project",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        desktop_service.open_folder.assert_called_once_with(
            bot_id="desktop_bot_001",
            user_id="u001",
            folder_path="/Users/user/Desktop/project",
        )

    def test_open_folder_traversal_rejected(self, client, desktop_service):
        """POST with '..' in folder_path returns validation error."""
        resp = client.post(self.OPEN_FOLDER_PATH, json={
            "folder_path": "../etc/passwd",
        })

        assert resp.status_code == 422

    def test_open_folder_bot_not_found(self, client, desktop_service):
        from agentclaw.community.core.errors import NotFound
        desktop_service.verify_ownership.side_effect = NotFound("Bot not found")

        resp = client.post(self.OPEN_FOLDER_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_open_folder_service_error(self, client, desktop_service):
        from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotServiceError
        desktop_service.open_folder.side_effect = DesktopBotServiceError("BaaS unreachable")

        resp = client.post(self.OPEN_FOLDER_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

    def test_open_folder_unauthorized_returns_401(self):
        from agentclaw.community.adapters.http.desktop.router import bot_router

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.post(self.OPEN_FOLDER_PATH)
        assert resp.status_code == 401

    def test_open_folder_no_staff_id_returns_401(self, client, desktop_service):
        """Returns 401 when authenticated user has no staffId."""
        from agentclaw.community.adapters.http.desktop.router import bot_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app, desktop_service)

        anon_user = MagicMock(spec=AuthenticatedIdentity)
        anon_user.staffId = None
        app.dependency_overrides[get_current_user] = lambda: anon_user

        anon_client = TestClient(app)
        resp = anon_client.post(self.OPEN_FOLDER_PATH)
        assert resp.status_code == 401

    def test_open_folder_unexpected_error(self, client, desktop_service):
        """Returns error when open_folder raises unexpected Exception."""
        desktop_service.open_folder.side_effect = RuntimeError("unexpected")

        resp = client.post(self.OPEN_FOLDER_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


class TestDesktopStartProgress:
    START_PROGRESS_PATH = "/api/desktop/bots/test-bot-uuid/start-progress"

    def test_start_progress_success(self, client, mock_user):
        """GET /{bot_uuid}/start-progress returns progress from BaaS."""
        from agentclaw.community.api.baas_service import BaasServiceProtocol
        baas_mock = MagicMock(spec=BaasServiceProtocol)
        baas_mock.get_bot_start_progress.return_value = {
            "progress": "75%",
            "status": "RUNNING",
        }
        _attach_mock_injector(
            FastAPI(), baas_service=baas_mock,
        )  # just to validate the mock shape

        # Rebuild client with custom baas mock
        from agentclaw.community.adapters.http.desktop.router import bot_router, device_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(device_router)
        app.include_router(bot_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        _attach_mock_injector(app, baas_service=baas_mock)
        test_client = TestClient(app)

        resp = test_client.get(self.START_PROGRESS_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["progress"] == "75%"
        assert data["data"]["status"] == "RUNNING"
        baas_mock.get_bot_start_progress.assert_called_once_with(
            bot_uuid="test-bot-uuid",
            device_affinity=None,
        )
        app.dependency_overrides.clear()

    def test_start_progress_with_device_affinity(self, mock_user):
        """GET with device_affinity query param passes it to BaasService."""
        from agentclaw.community.api.baas_service import BaasServiceProtocol
        from agentclaw.community.adapters.http.desktop.router import bot_router, device_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        baas_mock = MagicMock(spec=BaasServiceProtocol)
        baas_mock.get_bot_start_progress.return_value = {"progress": "100%"}

        app = FastAPI()
        app.include_router(device_router)
        app.include_router(bot_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        _attach_mock_injector(app, baas_service=baas_mock)
        test_client = TestClient(app)

        resp = test_client.get(f"{self.START_PROGRESS_PATH}?device_affinity=machine-01")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["progress"] == "100%"
        baas_mock.get_bot_start_progress.assert_called_once_with(
            bot_uuid="test-bot-uuid",
            device_affinity="machine-01",
        )
        app.dependency_overrides.clear()

    def test_start_progress_baas_error(self, mock_user):
        """Returns error when BaasService raises BaasServiceError."""
        from agentclaw.community.api.baas_service import BaasServiceProtocol
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
        from agentclaw.community.adapters.http.desktop.router import bot_router, device_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        baas_mock = MagicMock(spec=BaasServiceProtocol)
        baas_mock.get_bot_start_progress.side_effect = BaasServiceError("BaaS unavailable")

        app = FastAPI()
        app.include_router(device_router)
        app.include_router(bot_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        _attach_mock_injector(app, baas_service=baas_mock)
        test_client = TestClient(app)

        resp = test_client.get(self.START_PROGRESS_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        app.dependency_overrides.clear()

    def test_start_progress_no_staff_id_returns_401(self):
        """Returns 401 when authenticated user has no staffId."""
        from agentclaw.community.adapters.http.desktop.router import bot_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app)

        anon_user = MagicMock(spec=AuthenticatedIdentity)
        anon_user.staffId = None
        app.dependency_overrides[get_current_user] = lambda: anon_user

        anon_client = TestClient(app)
        resp = anon_client.get(self.START_PROGRESS_PATH)

        assert resp.status_code == 401
        app.dependency_overrides.clear()

    def test_start_progress_unauthorized_returns_401(self):
        """Returns 401 when no auth is provided."""
        from agentclaw.community.adapters.http.desktop.router import bot_router

        app = FastAPI()
        app.include_router(bot_router)
        _attach_mock_injector(app)
        client = TestClient(app)

        resp = client.get(self.START_PROGRESS_PATH)
        assert resp.status_code == 401

    def test_start_progress_unexpected_error(self, mock_user):
        """Returns error when BaasService raises unexpected Exception."""
        from agentclaw.community.api.baas_service import BaasServiceProtocol
        from agentclaw.community.adapters.http.desktop.router import bot_router, device_router
        from agentclaw.community.adapters.http.auth.dependencies import get_current_user

        baas_mock = MagicMock(spec=BaasServiceProtocol)
        baas_mock.get_bot_start_progress.side_effect = RuntimeError("unexpected")

        app = FastAPI()
        app.include_router(device_router)
        app.include_router(bot_router)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        _attach_mock_injector(app, baas_service=baas_mock)
        test_client = TestClient(app)

        resp = test_client.get(self.START_PROGRESS_PATH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        app.dependency_overrides.clear()
