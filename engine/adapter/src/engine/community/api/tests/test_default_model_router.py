"""Capability and strict default-model response contract."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from engine.community.api.engine.router import router
from engine.community.core.models.models import DefaultModel
from engine.community.core.engine.capability import Capability


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_default_model_calls_only_default_resolver(client):
    models = MagicMock()
    models.get_default_model = AsyncMock(return_value=DefaultModel(id="p/m"))
    with patch(
        "engine.community.api.engine.default_model.check_capability", return_value=None
    ) as gate:
        with patch(
            "engine.community.api.engine.default_model.EngineManager.get_instance"
        ) as manager:
            manager.return_value.models = models
            response = client.get("/api/engine/default-model")
    assert response.status_code == 200
    assert response.json()["data"] == {"id": "p/m"}
    gate.assert_called_once_with(Capability.MODEL_DEFAULT_GET)
    models.get_default_model.assert_awaited_once_with()
    models.list_models.assert_not_called()


def test_unsupported_short_circuits_before_plugin(client):
    with patch(
        "engine.community.api.engine.default_model.check_capability",
        side_effect=HTTPException(501),
    ):
        with patch(
            "engine.community.api.engine.default_model.EngineManager.get_instance"
        ) as manager:
            assert client.get("/api/engine/default-model").status_code == 501
            manager.assert_not_called()


@pytest.mark.parametrize(
    "answer", [{}, {"id": ""}, {"id": " "}, {"id": None}, {"id": 3}]
)
def test_bad_answer_does_not_become_success(client, answer):
    with patch(
        "engine.community.api.engine.default_model.check_capability", return_value=None
    ):
        with patch(
            "engine.community.api.engine.default_model.EngineManager.get_instance"
        ) as manager:
            manager.return_value.models.get_default_model = AsyncMock(
                return_value=answer
            )
            assert client.get("/api/engine/default-model").status_code == 502


def test_rpc_timeout_remains_gateway_timeout(client):
    with patch("engine.community.api.engine.default_model.check_capability", return_value=None):
        with patch("engine.community.api.engine.default_model.EngineManager.get_instance") as manager:
            manager.return_value.models.get_default_model = AsyncMock(side_effect=TimeoutError())
            assert client.get("/api/engine/default-model").status_code == 504
