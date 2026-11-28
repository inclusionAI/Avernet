"""Stateless model wire contract and sanitized unsupported/upstream failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from engine.community.api.engine import model_completion as api
from engine.community.core.models.completion import ModelCompletion
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def setup(monkeypatch):
    plugin = SimpleNamespace(
        complete_model=AsyncMock(
            return_value=ModelCompletion(text="answer", model="p/m", tokens=2)
        )
    )
    monkeypatch.setattr(api, "check_capability", lambda cap: None)
    monkeypatch.setattr(
        api.EngineManager, "get_instance", lambda: SimpleNamespace(models=plugin)
    )
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), plugin


def test_completion_contract(setup):
    client, plugin = setup
    r = client.post(
        "/api/engine/models:complete",
        json={"messages": [{"role": "user", "content": "probe"}]},
    )
    assert r.status_code == 200
    assert r.json()["data"] == {"text": "answer", "model": "p/m", "tokens": 2}
    assert plugin.complete_model.call_args.args[0].model is None


@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": "not-allowed"},
        {"max_tokens": True},
        {"messages": []},
        {"messages": [{"role": "tool", "content": "no"}]},
        {"messages": [{"role": "user", "content": "x" * 70001}]},
    ],
)
def test_strict_bounded_input(setup, changes):
    client, plugin = setup
    r = client.post(
        "/api/engine/models:complete",
        json={"messages": [{"role": "user", "content": "probe"}], **changes},
    )
    assert r.status_code == 422
    plugin.complete_model.assert_not_awaited()


@pytest.mark.parametrize(
    "error,status",
    [(NotImplementedError("secret"), 501), (RuntimeError("secret"), 502)],
)
def test_safe_failures(setup, error, status):
    client, plugin = setup
    plugin.complete_model.side_effect = error
    r = client.post(
        "/api/engine/models:complete",
        json={"messages": [{"role": "user", "content": "probe"}]},
    )
    assert r.status_code == status and "secret" not in r.text


def test_capability_refusal_precedes_model_call(setup, monkeypatch):
    client, plugin = setup

    def refuse(cap):
        raise HTTPException(501, "unsupported")

    monkeypatch.setattr(api, "check_capability", refuse)
    assert (
        client.post(
            "/api/engine/models:complete",
            json={"messages": [{"role": "user", "content": "probe"}]},
        ).status_code
        == 501
    )
    plugin.complete_model.assert_not_awaited()


def test_invalid_response_is_not_exported(setup):
    client, plugin = setup
    plugin.complete_model.return_value = {
        "text": "answer",
        "model": "p/m",
        "tokens": True,
        "api_key": "secret",
    }
    r = client.post(
        "/api/engine/models:complete",
        json={"messages": [{"role": "user", "content": "probe"}]},
    )
    assert r.status_code == 502 and "secret" not in r.text
