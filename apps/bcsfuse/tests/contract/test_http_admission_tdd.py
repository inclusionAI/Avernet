"""TDD contract for process-level structured HTTP admission."""

from __future__ import annotations

import asyncio
import json

import pytest


def _scope() -> dict:
    return {"type": "http", "path": "/api/v1/search", "method": "POST"}


def _receive() -> object:
    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


def _capture_send() -> tuple[object, list[dict]]:
    async def send(message: dict) -> None:
        messages.append(message)

    messages: list[dict] = []
    return send, messages


def _response_body(captured: list[dict]) -> dict:
    assert captured[0]["type"] == "http.response.start"
    assert captured[-1]["type"] == "http.response.body"
    return json.loads(captured[-1]["body"])


@pytest.mark.asyncio
async def test_http_admission_overload_returns_stable_json_contract() -> None:
    """The process must fail closed with a parseable code, not plain text."""

    from app.bootstrap.http_admission import HTTPAdmissionMiddleware

    started = asyncio.Event()
    release = asyncio.Event()

    async def downstream(scope, receive, send) -> None:
        started.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    middleware = HTTPAdmissionMiddleware(
        downstream,
        max_concurrency=1,
        retry_after_seconds=2,
    )

    first_send_send, first_send = _capture_send()
    first_task = asyncio.create_task(
        middleware(_scope(), _receive(), first_send_send)
    )
    await started.wait()

    second_send_send, second_send = _capture_send()
    await middleware(_scope(), _receive(), second_send_send)

    assert second_send[0]["status"] == 503
    assert _response_body(second_send) == {
        "detail": {
            "code": "HTTP_CAPACITY_EXCEEDED",
            "message": "BCSFuse HTTP capacity is occupied",
        }
    }
    assert dict(second_send[0]["headers"]).get(b"retry-after") == b"2"

    release.set()
    await first_task
    assert first_send[0]["status"] == 200


@pytest.mark.asyncio
async def test_http_admission_releases_slot_after_downstream_fails() -> None:
    from app.bootstrap.http_admission import HTTPAdmissionMiddleware

    async def downstream(scope, receive, send) -> None:
        raise RuntimeError("downstream failed")

    middleware = HTTPAdmissionMiddleware(
        downstream,
        max_concurrency=1,
        retry_after_seconds=1,
    )

    failing_send_function, _ = _capture_send()
    with pytest.raises(RuntimeError):
        await middleware(_scope(), _receive(), failing_send_function)

    # A failure must not permanently consume the process admission slot.
    downstream_callable, downstream_send = _capture_send()

    async def accepting_downstream(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    middleware.app = accepting_downstream
    await middleware(_scope(), _receive(), downstream_callable)
    assert downstream_send[0]["status"] == 200



def test_app_admission_reuses_the_legacy_server_limit(monkeypatch) -> None:
    """Existing BCSFUSE_SERVER_LIMIT_CONCURRENCY deployments stay meaningful."""

    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.setenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", "256")
    monkeypatch.delenv("BCSFUSE_HTTP_ADMISSION_MAX_CONCURRENCY", raising=False)
    config = YamlEnvConfigProvider()
    assert config.get_int("http.capacity.max_concurrency", 512) == 256


def test_dedicated_admission_limit_overrides_the_legacy_value(monkeypatch) -> None:
    """The new application admission key has higher precedence."""

    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.setenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", "256")
    monkeypatch.setenv("BCSFUSE_HTTP_ADMISSION_MAX_CONCURRENCY", "512")
    config = YamlEnvConfigProvider()
    assert config.get_int("http.capacity.max_concurrency", 256) == 512


def test_composed_app_has_process_admission_middleware() -> None:
    from app.bootstrap.opensource_app import create_opensource_app
    from app.bootstrap.http_admission import HTTPAdmissionMiddleware

    app = create_opensource_app(mode="test")
    assert any(
        middleware.cls is HTTPAdmissionMiddleware
        for middleware in app.user_middleware
    )


def test_uvicorn_raw_limit_is_disabled_when_application_admission_is_on(
    monkeypatch,
) -> None:
    """Uvicorn's plain-text 503 must not bypass the structured app contract."""
    import main

    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(main.uvicorn, "run", fake_run)
    monkeypatch.delenv("BCSFUSE_PROVIDER_MODE", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.delenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", raising=False)

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["limit_concurrency"] is None
