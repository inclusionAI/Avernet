"""Task-owned manifest read/write interface backed by existing config storage."""
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.task import manifest_routes
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.di.modules.system_config_module import SystemConfigModule
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.plugin_api.auth import AuthPlugin

PATH = "/api/v1/collaboration/tasks/manifest"


def config(version="0.1.1"):
    return {"schema_version": 1, "bundles": [{"name": "taskPanel", "type": "url",
        "url": f"https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@{version}/dist/index.umd.js"}]}


@pytest.fixture
def setup(monkeypatch):
    store = Mock()
    store.get_config.return_value = config()
    store.list_categories.return_value = []
    store.create_category.return_value = 1
    store.set_config.return_value = 1
    auth = Mock()
    auth.is_operator_allowed.return_value = True
    app = FastAPI()
    app.include_router(manifest_routes.router, prefix="/api/v1/collaboration/tasks")
    user = AuthenticatedUser(id="viewer", staffId="viewer", operatorName="viewer")
    app.dependency_overrides[get_current_user] = lambda: user

    # Exercise the real task manifest service binding.
    injector = Injector([SystemConfigModule, TaskModule])
    injector.binder.bind(AuthPlugin, to=InstanceProvider(auth))
    injector.binder.bind(SystemConfigServiceProtocol, to=InstanceProvider(store))
    attach_injector(app, injector)
    monkeypatch.setattr(manifest_routes, "get_current_env", lambda: "pre")
    return TestClient(app), store, auth


def test_only_one_manifest_path_and_config_updates_visible_on_next_request(setup):
    client, store, _ = setup
    response = client.get(PATH + "?env=prod")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"] == {"schema_version": 1, "env": "pre", "bundles": [
        {"name": "taskPanel", "url": config()["bundles"][0]["url"]}]}
    store.get_config.assert_called_with(category="task", config_key="panel", env="pre")
    store.get_config.return_value = config("0.1.2")
    assert "@0.1.2/" in client.get(PATH).json()["data"]["bundles"][0]["url"]
    assert client.get("/openapi/v1/collaboration/tasks/manifest").status_code == 404
    assert client.post("/openapi/v1/collaboration/tasks/manifest", json=config()).status_code == 404
    assert client.put(PATH, json=config()).status_code == 405
    store.set_config.assert_not_called()


@pytest.mark.parametrize("method", ["get", "post"])
def test_manifest_requires_authentication(setup, method):
    client, store, _ = setup
    def denied():
        raise HTTPException(401, "not authenticated")
    client.app.dependency_overrides[get_current_user] = denied
    response = client.get(PATH) if method == "get" else client.post(PATH, json=config())
    assert response.status_code == 401
    store.get_config.assert_not_called()
    store.set_config.assert_not_called()
    store.create_category.assert_not_called()


def test_manifest_write_requires_login_but_not_operator_permission(setup):
    client, store, auth = setup
    auth.is_operator_allowed.return_value = False
    assert client.post(PATH, json=config()).status_code == 200
    auth.is_operator_allowed.assert_not_called()
    store.set_config.assert_called_once()


def test_writer_creates_category_and_saves_using_server_environment(setup):
    client, store, _ = setup
    response = client.post(PATH + "?env=prod", json=config())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"]["env"] == "pre"
    store.create_category.assert_called_once_with(category="task", category_name="Task", env="pre", operator="viewer")
    store.set_config.assert_called_once_with(category="task", config_key="panel", config_value=config(), env="pre", operator="viewer")


def test_writer_replaces_existing_configuration_without_overwriting_category_metadata(setup):
    client, store, _ = setup
    store.list_categories.return_value = [{"category": "task", "category_name": "Existing"}]
    assert client.post(PATH, json=config("0.1.2")).status_code == 200
    store.create_category.assert_not_called()
    assert store.set_config.call_args.kwargs["config_value"] == config("0.1.2")


@pytest.mark.parametrize("value", [None, {}, {"schema_version": 2}])
def test_invalid_read_configuration_is_503_not_an_empty_success(setup, value):
    client, store, _ = setup
    store.get_config.return_value = value
    response = client.get(PATH)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"] is None


@pytest.mark.parametrize("value", [{}, {"schema_version": 2}, {**config(), "env": "prod"},
    {**config(), "bundles": []}, {**config(), "bundles": [{"name": "taskPanel", "type": "url", "url": "javascript:alert(1)"}]}])
def test_invalid_write_is_rejected_before_storage(setup, value):
    client, store, _ = setup
    assert client.post(PATH, json=value).status_code == 422
    store.set_config.assert_not_called()
    store.create_category.assert_not_called()


@pytest.mark.parametrize("operation", ["list_categories", "create_category", "set_config"])
def test_storage_exceptions_are_503(setup, operation):
    client, store, _ = setup
    getattr(store, operation).side_effect = RuntimeError("storage unavailable")
    response = client.post(PATH, json=config())
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"] is None
    if operation != "set_config":
        store.set_config.assert_not_called()


@pytest.mark.parametrize("operation", ["create_category", "set_config"])
def test_unsaved_configuration_is_not_reported_as_success(setup, operation):
    client, store, _ = setup
    getattr(store, operation).return_value = 0
    assert client.post(PATH, json=config()).status_code == 503
    if operation == "create_category":
        store.set_config.assert_not_called()
