"""Contract tests for malformed run routing metadata at the HTTP boundary."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.open_api.run_router import router
from tests.unit.adapters.web.conftest import iter_api_routes
from tests.unit.adapters.web.open_api.test_run_router import _make_api_key_record


def _dependencies(node):
    for dep in node.dependencies:
        yield dep
        yield from _dependencies(dep)


@pytest.fixture
def run_app():
    app = FastAPI()
    app.include_router(router)
    validator = AsyncMock()
    validator.verify = AsyncMock(
        side_effect=lambda key: _make_api_key_record() if key == "valid" else None
    )
    runner = AsyncMock()
    runner.chat = AsyncMock(return_value="run-1")
    route = next(
        r
        for r in iter_api_routes(app)
        if getattr(r, "path", None) == "/openapi/v1/runs" and "POST" in r.methods
    )
    for dep in _dependencies(route.dependant):
        if dep.name == "validator":
            app.dependency_overrides[dep.call] = lambda: validator
        elif dep.name == "bot_runner":
            app.dependency_overrides[dep.call] = lambda: runner
    return app, validator, runner


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "secret-marker", 0, 1, False, [], [1]])
async def test_invalid_options_are_rejected_without_delivery(run_app, value, caplog):
    app, _, runner = run_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/openapi/v1/runs",
            headers={"Authorization": "Bearer valid"},
            json={"message": "hello", "metadata": {"bot_options": value}},
        )
    assert response.status_code == 400
    assert response.json() == {
        "detail": {"code": 40001, "message": "Invalid parameter"}
    }
    assert "secret-marker" not in response.text and "secret-marker" not in caplog.text
    runner.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_authentication_precedes_options_validation(run_app):
    app, validator, runner = run_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/openapi/v1/runs",
            headers={"Authorization": "Bearer invalid"},
            json={"message": "hello", "metadata": {"bot_options": "bad"}},
        )
    assert response.status_code == 401
    validator.verify.assert_awaited_once_with("invalid")
    runner.chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"other": "value"},
        {"bot_options": {}},
        {"bot_options": {"lifecycle_stage": "draft"}},
        {"bot_options": {"lifecycle_stage": "eval"}},
    ],
)
async def test_valid_metadata_is_preserved(run_app, metadata):
    app, _, runner = run_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/openapi/v1/runs",
            headers={"Authorization": "Bearer valid"},
            json={"message": "hello", "metadata": metadata},
        )
    assert response.status_code == 200
    assert runner.chat.await_args.kwargs["metadata"] == (metadata or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "eval"])
async def test_valid_stage_reaches_binding_resolver(run_app, stage):
    from secbaas.community.core.service.bot_run import BotBindingResolver
    from secbaas.community.spi.bot_service import BotBindingData

    app, _, runner = run_app
    plugin = AsyncMock()
    plugin.get_binding.return_value = BotBindingData(
        bot_id="bot-1",
        owner_id="entity-1",
        bot_type="service",
        engine_type="openclaw",
        publish_id=None,
        publish_status=None,
        binding_id=0,
        device_provider="baas",
        device_id="fake-device",
    )
    resolver = BotBindingResolver(plugin)

    async def resolve_then_return(**kwargs):
        await resolver.resolve_binding(
            bot_id=kwargs["bot_id"], metadata=kwargs["metadata"]
        )
        return "run-1"

    runner.chat.side_effect = resolve_then_return
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/openapi/v1/runs",
            headers={"Authorization": "Bearer valid"},
            json={
                "message": "hello",
                "metadata": {"bot_options": {"lifecycle_stage": stage}},
            },
        )
    assert response.status_code == 200
    assert plugin.get_binding.await_args.kwargs["stage"] == stage


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "bad", 0, False, []])
async def test_invalid_options_have_no_service_side_effects(value):
    """Exercise the real runner wiring with isolated side-effect boundaries."""
    from unittest.mock import MagicMock

    from secbaas.community.core.service.bot_run import BotServiceSelector
    from tests.unit.adapters.web.test_routing_chain import (
        _make_baas_binding_data,
        _make_runner,
        _make_session_info,
    )

    app = FastAPI()
    app.include_router(router)
    validator = AsyncMock()
    validator.verify.return_value = _make_api_key_record()
    service = MagicMock()
    service.create_session = AsyncMock(return_value=_make_session_info())
    repository = MagicMock()
    selector = BotServiceSelector(claw_service=service, baas_service=service)
    runner = _make_runner(selector, repository, binding_data=_make_baas_binding_data())
    route = next(
        r
        for r in iter_api_routes(app)
        if getattr(r, "path", None) == "/openapi/v1/runs" and "POST" in r.methods
    )
    for dep in _dependencies(route.dependant):
        if dep.name == "validator":
            app.dependency_overrides[dep.call] = lambda: validator
        elif dep.name == "bot_runner":
            app.dependency_overrides[dep.call] = lambda: runner

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/openapi/v1/runs",
            headers={"Authorization": "Bearer valid"},
            json={"message": "hello", "metadata": {"bot_options": value}},
        )
        assert response.status_code == 400
        repository.insert_run.assert_not_called()
        service.create_session.assert_not_awaited()
        runner._dispatchers[0].dispatch_send.assert_not_awaited()

        # Positive control: this wiring reaches insertion and dispatch for a valid request.
        response = await client.post(
            "/openapi/v1/runs",
            headers={"Authorization": "Bearer valid"},
            json={"message": "hello", "metadata": {"bot_options": {}}},
        )
        assert response.status_code == 200
        repository.insert_run.assert_called_once()
        runner._dispatchers[0].dispatch_send.assert_awaited_once()
        # Session creation is deferred to the worker in this path, not executed here.
        service.create_session.assert_not_awaited()
