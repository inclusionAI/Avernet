"""Single read endpoint and existing administrator writer contract."""
import asyncio
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task import manifest_routes
from agentclaw.community.adapters.http.system_config import router as config_routes
from agentclaw.community.adapters.http.system_config.schemas import SetConfigRequest
from agentclaw.community.api.task.task_manifest_service import TaskPanelManifestServiceProtocol
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.task_panel.manifest import TaskPanelManifestService


def config(version="0.1.1"):
    return {"schema_version": 1, "bundles": [{"name": "taskPanel", "type": "url",
        "url": f"https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@{version}/dist/index.umd.js"}]}


@pytest.fixture
def setup(monkeypatch):
    store = Mock()
    store.get_config.return_value = config()
    store.set_config.return_value = 1
    app = FastAPI()
    app.include_router(manifest_routes.router, prefix="/api/v1/collaboration/tasks")
    user = AuthenticatedUser(id="viewer", staffId="viewer", operatorName="viewer")
    app.dependency_overrides[get_current_user] = lambda: user
    injector = Injector()
    injector.binder.bind(TaskPanelManifestServiceProtocol, to=InstanceProvider(TaskPanelManifestService(store)))
    attach_injector(app, injector)
    monkeypatch.setattr(manifest_routes, "get_current_env", lambda: "pre")
    monkeypatch.setattr(config_routes.env_utils, "get_current_env", lambda: "pre")
    return TestClient(app), store, user


def test_only_one_manifest_endpoint_and_config_updates_visible_on_next_request(setup):
    client, store, _ = setup
    response = client.get("/api/v1/collaboration/tasks/manifest?env=prod")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"] == {"schema_version": 1, "env": "pre", "bundles": [
        {"name": "taskPanel", "url": config()["bundles"][0]["url"]}]}
    store.get_config.assert_called_with(category="task", config_key="panel", env="pre")
    store.get_config.return_value = config("0.1.2")
    assert "@0.1.2/" in client.get("/api/v1/collaboration/tasks/manifest").json()["data"]["bundles"][0]["url"]
    assert client.get("/openapi/v1/collaboration/tasks/manifest").status_code == 404
    store.set_config.assert_not_called()


def test_manifest_requires_authentication(setup):
    client, store, _ = setup
    def denied():
        raise HTTPException(401, "not authenticated")
    client.app.dependency_overrides[get_current_user] = denied
    assert client.get("/api/v1/collaboration/tasks/manifest").status_code == 401
    store.get_config.assert_not_called()


@pytest.mark.parametrize("value", [None, {}, {"schema_version": 2}])
def test_invalid_configuration_is_503_not_an_empty_success(setup, value):
    client, store, _ = setup
    store.get_config.return_value = value
    response = client.get("/api/v1/collaboration/tasks/manifest")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"] is None


def write(store, user, *, allowed=True, category="task", key="panel", value=None):
    auth = Mock()
    auth.is_operator_allowed.return_value = allowed
    req = SetConfigRequest(category=category, config_key=key, config_value=config() if value is None else value)
    return asyncio.run(config_routes.set_config(req, user, store, auth))


def test_existing_config_writer_requires_operator_and_preserves_unrelated_keys(setup):
    _, store, user = setup
    with pytest.raises(Forbidden):
        write(store, user, allowed=False)
    store.set_config.assert_not_called()
    result = write(store, user)
    assert result.success
    store.set_config.assert_called_once_with(category="task", config_key="panel", config_value=config(),
        env="pre", description=None, operator="viewer")
    assert write(store, user, allowed=False, key="some_other_key", value=True).success


@pytest.mark.parametrize("category,key", [("TASK ", "PANEL "), ("ｔａｓｋ", "ｐａｎｅｌ"), ("tásk", "panél")])
def test_aliases_cannot_bypass_operator_check(setup, category, key):
    _, store, user = setup
    with pytest.raises(Forbidden):
        write(store, user, allowed=False, category=category, key=key)
    with pytest.raises(HTTPException) as error:
        write(store, user, category=category, key=key)
    assert error.value.status_code == 422
    store.set_config.assert_not_called()


def test_writer_rejects_invalid_value_or_missing_category_instead_of_claiming_success(setup):
    _, store, user = setup
    with pytest.raises(HTTPException) as error:
        write(store, user, value={})
    assert error.value.status_code == 422
    store.set_config.assert_not_called()
    store.set_config.return_value = 0
    with pytest.raises(HTTPException) as error:
        write(store, user)
    assert error.value.status_code == 503
