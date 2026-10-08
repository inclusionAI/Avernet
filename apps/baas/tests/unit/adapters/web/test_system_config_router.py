"""
System Config Router 单元测试

测试 system_config_router.py 中的所有 5 个路由处理器。
使用 TestClient 搭配依赖 mock 进行验证。
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.dependencies import get_op_ctx
from secbaas.community.adapters.web.routers.config_management.system_config_router import (
    router,
)
from secbaas.community.api import OperationContext
from secbaas.community.api.config_manage import (
    SystemConfigListResponse,
    SystemConfigResponse,
)
from secbaas.community.bootstrap import Provide
from tests.unit.adapters.web.conftest import iter_api_routes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config_response(
    id_: int = 1,
    conf_key: str = "test.key",
    conf_value: str | None = "test_value",
    env: str = "dev",
    name: str = "Test Config",
    description: str | None = "Test description",
    creator: str = "user1",
    modifier: str = "user1",
) -> SystemConfigResponse:
    now = datetime(2026, 5, 23, 12, 0, 0)
    return SystemConfigResponse(
        id=id_,
        conf_key=conf_key,
        conf_value=conf_value,
        env=env,
        name=name,
        description=description,
        creator=creator,
        modifier=modifier,
        gmt_create=now,
        gmt_modified=now,
    )


def make_list_response(
    items: list[SystemConfigResponse] | None = None,
    total: int = 0,
    page: int = 1,
    page_size: int = 20,
) -> SystemConfigListResponse:
    return SystemConfigListResponse(
        items=items or [],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_mock_svc = MagicMock()


def _install_mock_overrides(app, mock_svc):
    """Replace every Provide[...] dependency with *mock_svc*."""
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, Provide):
                app.dependency_overrides[dep.call] = lambda: mock_svc

    # Override get_op_ctx so create/update/delete routes get a fake operator
    app.dependency_overrides[get_op_ctx] = lambda: OperationContext(
        operator="admin", env="dev"
    )


@pytest.fixture
def client():
    """Create TestClient with router mounted and default mock overrides.

    Every ``Provide[...]`` dependency is replaced with a default
    ``MagicMock`` so that the monkey-patched ``_Marker.__call__``
    (applied by ``app.py`` at import time) never triggers eager DI
    resolution through an unrelated container.  Individual tests that
    need a configured mock call ``_set_mock`` to replace the default.
    """
    app = FastAPI()
    app.include_router(router)
    _install_mock_overrides(app, _mock_svc)
    with TestClient(app) as tc:
        yield tc


def _set_mock(client, mock_svc):
    """Replace the default mock with a configured one for all routes."""
    _install_mock_overrides(client.app, mock_svc)


# ---------------------------------------------------------------------------
# list_configs
# ---------------------------------------------------------------------------


class TestListConfigs:
    """GET /api/v1/system-configs"""

    def test_list_configs_default_pagination(self, client):
        """Default pagination (page=1, page_size=20)."""
        resp = make_list_response(
            items=[make_config_response()], total=1, page=1, page_size=20
        )
        mock_svc = MagicMock()
        mock_svc.list_configs.return_value = resp

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.get("/api/v1/system-configs")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 1
        assert len(body["data"]["items"]) == 1
        assert body["data"]["items"][0]["conf_key"] == "test.key"

    def test_list_configs_custom_pagination(self, client):
        """Custom pagination parameters."""
        resp = make_list_response(items=[], total=0, page=3, page_size=50)
        mock_svc = MagicMock()
        mock_svc.list_configs.return_value = resp

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="pre",
        ):
            response = client.get(
                "/api/v1/system-configs", params={"page": 3, "page_size": 50}
            )

        assert response.status_code == 200
        mock_svc.list_configs.assert_called_once_with(page=3, page_size=50)

    def test_list_configs_empty(self, client):
        """No configs found returns empty list."""
        resp = make_list_response(items=[], total=0)
        mock_svc = MagicMock()
        mock_svc.list_configs.return_value = resp

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.get("/api/v1/system-configs")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0

    def test_list_configs_validation_page_lt_1(self, client):
        """page < 1 returns 422."""
        response = client.get("/api/v1/system-configs", params={"page": 0})
        assert response.status_code == 422

    def test_list_configs_validation_page_size_exceeds_limit(self, client):
        """page_size > 100 returns 422."""
        response = client.get(
            "/api/v1/system-configs", params={"page": 1, "page_size": 200}
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------


class TestGetConfig:
    """GET /api/v1/system-configs/{conf_key}"""

    def test_get_config_success(self, client):
        """Found config returns 200."""
        cfg = make_config_response()
        mock_svc = MagicMock()
        mock_svc.get_config.return_value = cfg

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.get("/api/v1/system-configs/my.config.key")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["conf_key"] == "test.key"

    def test_get_config_not_found(self, client):
        """Missing config returns 404."""
        mock_svc = MagicMock()
        mock_svc.get_config.return_value = None

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.get("/api/v1/system-configs/missing.key")

        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error_code"] == "CONFIG_NOT_FOUND"
        assert "missing.key" in body["detail"]["message"]


# ---------------------------------------------------------------------------
# create_config
# ---------------------------------------------------------------------------


class TestCreateConfig:
    """POST /api/v1/system-configs"""

    def test_create_config_success(self, client):
        """Create with minimal fields returns 201."""
        cfg = make_config_response()
        payload = {
            "conf_key": "new.config",
            "name": "New Config",
            "operator": "admin",
        }
        mock_svc = MagicMock()
        mock_svc.create_config.return_value = cfg

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.post("/api/v1/system-configs", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["data"]["conf_key"] == "test.key"

        # Verify router no longer resolves env (service handles it)
        call_arg = mock_svc.create_config.call_args.kwargs["data"]
        assert call_arg.conf_key == "new.config"

    def test_create_config_all_fields(self, client):
        """Create with all optional fields."""
        cfg = make_config_response(
            conf_key="full.config",
            conf_value="some_value",
            description="Full description",
        )
        payload = {
            "conf_key": "full.config",
            "conf_value": "some_value",
            "name": "Full Config",
            "description": "Full description",
            "operator": "admin",
        }
        mock_svc = MagicMock()
        mock_svc.create_config.return_value = cfg

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="prod",
        ):
            response = client.post("/api/v1/system-configs", json=payload)

        assert response.status_code == 201
        call_arg = mock_svc.create_config.call_args.kwargs["data"]
        # Router passes env=None; service auto-detects
        assert call_arg.name == "Full Config"

    def test_create_config_accepts_special_chars(self, client):
        """conf_key without format restriction accepts special characters."""
        cfg = make_config_response(conf_key="invalid@key")
        payload = {
            "conf_key": "invalid@key",
            "name": "Bad Key",
            "operator": "admin",
        }
        mock_svc = MagicMock()
        mock_svc.create_config.return_value = cfg

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.post("/api/v1/system-configs", json=payload)

        assert response.status_code == 201
        body = response.json()
        assert body["data"]["conf_key"] == "invalid@key"

    def test_create_config_missing_required_fields(self, client):
        """Missing required fields returns 422."""
        response = client.post("/api/v1/system-configs", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    """PUT /api/v1/system-configs/{conf_key}"""

    def test_update_config_success(self, client):
        """Update returns 200 with updated config."""
        updated = make_config_response(conf_value="new_value")
        payload = {"conf_value": "new_value"}
        mock_svc = MagicMock()
        mock_svc.update_config.return_value = updated

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.put("/api/v1/system-configs/existing.key", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["conf_value"] == "new_value"
        mock_svc.update_config.assert_called_once_with(
            conf_key="existing.key",
            data=mock_svc.update_config.call_args.kwargs["data"],
        )

    def test_update_config_not_found(self, client):
        """Update non-existent config returns 404."""
        payload = {"conf_value": "whatever"}
        mock_svc = MagicMock()
        mock_svc.update_config.return_value = None

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.put("/api/v1/system-configs/missing.key", json=payload)

        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error_code"] == "CONFIG_NOT_FOUND"

    def test_update_config_partial(self, client):
        """Update only name — other fields left intact."""
        updated = make_config_response(name="Renamed")
        payload = {"name": "Renamed"}
        mock_svc = MagicMock()
        mock_svc.update_config.return_value = updated

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.put("/api/v1/system-configs/existing.key", json=payload)

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# delete_config
# ---------------------------------------------------------------------------


class TestDeleteConfig:
    """DELETE /api/v1/system-configs/{conf_key}"""

    def test_delete_config_success(self, client):
        """Delete returns 200 with success message."""
        mock_svc = MagicMock()
        mock_svc.delete_config.return_value = True

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.delete("/api/v1/system-configs/removable.key")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["success"] is True
        mock_svc.delete_config.assert_called_once_with(conf_key="removable.key")

    def test_delete_config_not_found(self, client):
        """Delete non-existent config returns 404."""
        mock_svc = MagicMock()
        mock_svc.delete_config.return_value = False

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="dev",
        ):
            response = client.delete("/api/v1/system-configs/missing.key")

        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error_code"] == "CONFIG_NOT_FOUND"

    def test_delete_config_with_op_ctx(self, client):
        """Delete uses operator from op_ctx (no query param)."""
        mock_svc = MagicMock()
        mock_svc.delete_config.return_value = True

        _set_mock(client, mock_svc)
        with patch(
            "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
            return_value="prod",
        ):
            response = client.delete("/api/v1/system-configs/removable.key")

        assert response.status_code == 200
        mock_svc.delete_config.assert_called_once_with(conf_key="removable.key")
