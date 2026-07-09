"""Phase 1 — HTTP contract tests for `engine/api/models/router.py`.

Verifies `/api/models` and `/api/models/{id}` flow through `manager.models`
(no legacy `EngineManager.get_model_api()` ABC) and that the capability
guard short-circuits 501 when the active engine doesn't declare
``MODEL_LIST``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.models.router import router as models_router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.models.models import Model, ModelCapabilities, ModelPricing
from engine.community.manager import EngineManager


class _EngineWithModels(BaseEngine):
    name = "rich"
    version = "1.0.0"

    _CAPABILITIES = EngineCapabilities(
        supported={Capability.MODEL_LIST, Capability.MODEL_SWITCH},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


class _EngineWithoutModels(BaseEngine):
    name = "lean"
    version = "0.1.0"

    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


def _install_manager(engine_cls: type[BaseEngine]) -> EngineManager:
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    m = EngineManager(engine_cls.name, registry=registry)
    m._active_engine = engine_cls()
    EngineManager._instance = m
    return m


@pytest.fixture
def rich_manager():
    m = _install_manager(_EngineWithModels)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install_manager(_EngineWithoutModels)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(models_router)
    return TestClient(app)


def _sample_models() -> list[Model]:
    return [
        Model(
            id="openai/gpt-5.3",
            provider="openai",
            provider_id="gpt-5.3",
            name="GPT-5.3",
            display_name="OpenAI GPT-5.3",
            description="flagship",
            capabilities=ModelCapabilities(
                context_window=128000,
                max_output_tokens=4096,
                vision=True,
                function_calling=True,
                reasoning=False,
                streaming=True,
                json_mode=True,
            ),
            pricing=ModelPricing(input_price=0.005, output_price=0.015),
        ),
        Model(
            id="anthropic/claude-3.5-sonnet",
            provider="anthropic",
            provider_id="claude-3.5-sonnet",
            name="Claude 3.5 Sonnet",
            display_name="Claude 3.5 Sonnet",
        ),
        Model(
            id="glink/claude-opus-4-6",
            provider="codefuse-antcc",
            provider_id="glink/claude-opus-4-6",
            name="Claude Opus 4.6",
            display_name="Claude Opus 4.6",
            provider_category="glink_account_hosting",
        ),
    ]


class TestListModels:
    def test_dispatches_through_manager_models(self, rich_manager, client):
        plugin = MagicMock()
        plugin.list_models = AsyncMock(return_value=_sample_models())
        rich_manager._active_engine._models = plugin

        resp = client.get("/api/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 3
        ids = [m["id"] for m in body["data"]["models"]]
        assert ids == [
            "openai/gpt-5.3",
            "anthropic/claude-3.5-sonnet",
            "glink/claude-opus-4-6",
        ]
        # legacy frontend keys preserved
        first = body["data"]["models"][0]
        assert first["provider_id"] == "gpt-5.3"
        assert first["enterprise_enabled"] is True
        assert first["capabilities"]["vision"] is True
        assert first["pricing"]["input_price"] == 0.005
        # provider_category only emitted when set (None → key absent)
        assert "provider_category" not in first
        glink = body["data"]["models"][2]
        assert glink["provider_category"] == "glink_account_hosting"

        plugin.list_models.assert_awaited_once()

    def test_unsupported_engine_returns_501(self, lean_manager, client):
        resp = client.get("/api/models")
        assert resp.status_code == 501


class TestGetModel:
    def test_returns_matched_entry(self, rich_manager, client):
        plugin = MagicMock()
        plugin.list_models = AsyncMock(return_value=_sample_models())
        rich_manager._active_engine._models = plugin

        resp = client.get("/api/models/openai/gpt-5.3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == "openai/gpt-5.3"

    def test_404_when_not_found(self, rich_manager, client):
        plugin = MagicMock()
        plugin.list_models = AsyncMock(return_value=_sample_models())
        rich_manager._active_engine._models = plugin

        resp = client.get("/api/models/missing/model")
        assert resp.status_code == 404

    def test_unsupported_engine_returns_501(self, lean_manager, client):
        resp = client.get("/api/models/openai/gpt-5.3")
        assert resp.status_code == 501
