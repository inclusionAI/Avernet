"""Default-model HTTP boundary: capability, strict shape, safe failures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from engine.community.api.engine import default_model


@pytest.fixture
def setup(monkeypatch):
    plugin = SimpleNamespace(get_default_model=AsyncMock(return_value="p/m"))
    monkeypatch.setattr(default_model, "check_capability", lambda cap: None)
    monkeypatch.setattr(default_model.EngineManager, "get_instance", lambda: SimpleNamespace(models=plugin))
    app = FastAPI()
    app.include_router(default_model.router)
    return TestClient(app), plugin


@pytest.mark.parametrize("model", ["p/m", None])
def test_only_publishes_model_id(setup, model):
    client, plugin = setup
    plugin.get_default_model.return_value = model
    response = client.get("/api/engine/default-model")
    assert response.status_code == 200
    assert response.json()["data"] == {"model_id": model}


@pytest.mark.parametrize("model", [5, {}, " "])
def test_rejects_bad_plugin_result(setup, model):
    client, plugin = setup
    plugin.get_default_model.return_value = model
    assert client.get("/api/engine/default-model").status_code == 502


def test_does_not_publish_upstream_exception(setup):
    client, plugin = setup
    plugin.get_default_model.side_effect = ValueError("secret configuration")
    response = client.get("/api/engine/default-model")
    assert response.status_code == 502
    assert "secret" not in response.text


def test_unsupported_does_not_call_plugin(setup, monkeypatch):
    client, plugin = setup
    def refuse(cap):
        raise HTTPException(501, "unsupported")
    monkeypatch.setattr(default_model, "check_capability", refuse)
    assert client.get("/api/engine/default-model").status_code == 501
    plugin.get_default_model.assert_not_awaited()
