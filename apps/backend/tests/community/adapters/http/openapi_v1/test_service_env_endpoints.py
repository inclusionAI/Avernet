"""HTTP contracts for service evaluation environments."""
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.service_envs import router as service_envs_router
from agentclaw.community.adapters.http.openapi_v1.service_envs.router import publish_eval_env
from agentclaw.community.core.service_bot.errors import ServicePublicationNotFoundError


@pytest.fixture
def resource_services(monkeypatch):
    from importlib import import_module
    module = import_module("agentclaw.community.adapters.http.openapi_v1.service_envs.router")
    monkeypatch.setattr(module, "get_current_env", lambda: "dev")
    bots = Mock()
    bots.get_bot_by_address.return_value = {"id": 1, "bot_type": "service"}
    publications = Mock()
    publications.get_publish_by_id.return_value = SimpleNamespace(
        source_bot_pk=1, source_bot_id="bot-1", env="dev",
    )
    return bots, publications


@pytest.mark.asyncio
async def test_publish_eval_env_handler(resource_services):
    service = Mock()
    service.eval_publish = AsyncMock(return_value={
        "success": True, "publish_id": 7, "stage": "eval", "bot_uuid": "eval-bot",
        "baas_publish_id": 42, "baas_bot_status": None,
    })
    request = Request({"type": "http", "method": "POST", "path": "/"})
    request.state.trace_id = "trace-1"
    result = await publish_eval_env("bot-1", 7, request, "actor", "owner", "task-1", service, *resource_services)
    assert result.code == 202000
    assert result.request_id == "trace-1"
    assert result.data.bot_uuid == "eval-bot"
    assert result.data.baas_bot_status is None
    service.eval_publish.assert_awaited_once_with(
        publish_id=7, operator="actor", biz_id="task-1", resource_spec=None,
    )


def test_openapi_contract():
    app = FastAPI()
    app.include_router(service_envs_router)
    schema = app.openapi()
    operation = schema["paths"]["/openapi/v1/bots/{bot_id}/service-envs/{publication_id}/env-publish"]["post"]
    assert "202" in operation["responses"]
    assert "requestBody" not in operation
    params = {p["name"]: p for p in operation["parameters"]}
    assert params["owner_id"]["in"] == "query"
    assert params["owner_id"]["required"] is True
    assert params["owner_id"]["schema"]["minLength"] == 1
    assert "entity_id" not in params
    assert params["operator"]["in"] == "query"
    assert params["operator"]["required"] is True
    assert params["operator"]["schema"]["minLength"] == 1
    assert params["biz_id"]["in"] == "query"
    assert params["biz_id"]["required"] is False
    assert params["biz_id"]["schema"]["default"] == ""
    publication = next(p for p in operation["parameters"] if p["name"] == "publication_id")
    assert publication["schema"]["minimum"] == 1


@pytest.mark.asyncio
async def test_not_found_is_enveloped(resource_services):
    service = Mock()
    service.eval_publish = AsyncMock(side_effect=ServicePublicationNotFoundError("hidden"))
    request = Request({"type": "http", "method": "POST", "path": "/"})
    result = await publish_eval_env("bot-1", 7, request, "actor", "owner", "", service, *resource_services)
    assert result.status_code == 404


def test_publish_eval_env_admission_is_open():
    from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION, AdmissionMode
    from agentclaw.community.adapters.http.openapi_v1.authorization import AUTHORIZATION, NoCheck

    operation = ("POST", "/openapi/v1/bots/{bot_id}/service-envs/{publication_id}/env-publish")
    assert ADMISSION[operation] is AdmissionMode.OPEN
    assert isinstance(AUTHORIZATION[operation], NoCheck)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("source_bot_pk", 2), ("source_bot_id", "other-bot"), ("env", "other-env"),
])
async def test_foreign_publication_rejected(resource_services, field, value):
    _, publications = resource_services
    setattr(publications.get_publish_by_id.return_value, field, value)
    service = Mock(eval_publish=AsyncMock())
    request = Request({"type": "http", "method": "POST", "path": "/"})
    result = await publish_eval_env(
        "bot-1", 7, request, "actor", "owner", "",
        service, *resource_services,
    )
    assert result.status_code == 404
    service.eval_publish.assert_not_awaited()


_PROGRESS_PATH = (
    "/openapi/v1/service-envs"
    "/env-publish/{baas_publish_id}/progress"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["RUNNING", "SUCCESS", "FAILED"])
async def test_env_progress(status):
    from agentclaw.community.adapters.http.openapi_v1.service_envs.router import get_env_publish_progress

    progress = {"status": status, "overall_progress": {"progress_percentage": 50}}
    service = Mock()
    service.get_baas_publish_progress.return_value = progress
    request = Request({"type": "http", "method": "GET", "path": "/"})
    request.state.trace_id = "progress-trace"
    result = await get_env_publish_progress(
        42, request, service,
    )
    assert result.data == progress
    assert result.request_id == "progress-trace"
    service.get_baas_publish_progress.assert_called_once_with(
        baas_publish_id=42, include_devices=False,
    )
    service.eval_publish.assert_not_called()


def test_progress_contract_and_permissions():
    from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION, AdmissionMode
    from agentclaw.community.adapters.http.openapi_v1.authorization import AUTHORIZATION, NoCheck

    app = FastAPI()
    app.include_router(service_envs_router)
    operation = app.openapi()["paths"][_PROGRESS_PATH]["get"]
    assert "200" in operation["responses"]
    assert {p["name"] for p in operation["parameters"]} == {"baas_publish_id"}
    for name in ("baas_publish_id",):
        parameter = next(p for p in operation["parameters"] if p["name"] == name)
        assert parameter["schema"]["minimum"] == 1
    assert ADMISSION[("GET", _PROGRESS_PATH)] is AdmissionMode.OPEN
    assert isinstance(AUTHORIZATION[("GET", _PROGRESS_PATH)], NoCheck)


@pytest.mark.asyncio
async def test_progress_provider_error_propagates():
    from agentclaw.community.adapters.http.openapi_v1.service_envs.router import get_env_publish_progress

    service = Mock()
    service.get_baas_publish_progress.side_effect = RuntimeError("provider unavailable")
    request = Request({"type": "http", "method": "GET", "path": "/"})
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await get_env_publish_progress(
            42, request, service,
        )


_TEARDOWN_PATH = "/openapi/v1/service-envs/{bot_uuid}/env-teardown"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_defaults", [True, False])
async def test_env_teardown(use_defaults):
    from agentclaw.community.adapters.http.openapi_v1.service_envs.router import teardown_eval_env

    service = Mock()
    service.eval_teardown.return_value = {
        "success": True, "bot_uuid": "eval-bot", "default_tag": "",
        "message": "Eval environment teardown enqueued",
    }
    request = Request({"type": "http", "method": "POST", "path": "/"})
    request.state.trace_id = "teardown-trace"
    kwargs = {} if use_defaults else {"operator": "user-1", "publish_id": 7}
    result = await teardown_eval_env("eval-bot", request, service=service, **kwargs)
    assert result.code == 202000
    assert result.request_id == "teardown-trace"
    assert result.data.bot_uuid == "eval-bot"
    service.eval_teardown.assert_called_once_with(
        "eval-bot", operator="system" if use_defaults else "user-1",
        publish_id=0 if use_defaults else 7,
    )


@pytest.mark.asyncio
async def test_env_teardown_enqueue_failure_propagates():
    from agentclaw.community.adapters.http.openapi_v1.service_envs.router import teardown_eval_env

    service = Mock()
    service.eval_teardown.side_effect = RuntimeError("queue unavailable")
    request = Request({"type": "http", "method": "POST", "path": "/"})
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await teardown_eval_env("eval-bot", request, service=service)


def test_teardown_contract_and_permissions():
    from agentclaw.community.adapters.http.openapi_v1.admission import ADMISSION, AdmissionMode
    from agentclaw.community.adapters.http.openapi_v1.authorization import AUTHORIZATION, NoCheck

    app = FastAPI()
    app.include_router(service_envs_router)
    operation = app.openapi()["paths"][_TEARDOWN_PATH]["post"]
    assert "202" in operation["responses"]
    assert "requestBody" not in operation
    params = {p["name"]: p for p in operation["parameters"]}
    assert set(params) == {"bot_uuid", "operator", "publish_id"}
    assert params["bot_uuid"]["required"] is True
    assert params["bot_uuid"]["schema"]["minLength"] == 1
    assert params["operator"]["in"] == "query"
    assert params["operator"]["schema"]["default"] == "system"
    assert params["operator"]["schema"]["minLength"] == 1
    assert params["publish_id"]["in"] == "query"
    assert params["publish_id"]["schema"]["default"] == 0
    assert params["publish_id"]["schema"]["minimum"] == 0
    assert ADMISSION[("POST", _TEARDOWN_PATH)] is AdmissionMode.OPEN
    assert isinstance(AUTHORIZATION[("POST", _TEARDOWN_PATH)], NoCheck)


@pytest.mark.parametrize("method,path", [
    ("post", "/openapi/v1/bots/{bot_id}/service-envs/{publication_id}/env-publish"),
    ("get", _PROGRESS_PATH),
    ("post", _TEARDOWN_PATH),
])
def test_call_examples_are_published_in_openapi(method, path):
    app = FastAPI()
    app.include_router(service_envs_router)
    description = app.openapi()["paths"][path][method]["description"]
    assert "调用示例" in description
    assert "curl" in description
    assert "${BASE_URL}/openapi/v1/" in description


@pytest.mark.asyncio
async def test_non_service_bot_cannot_publish_eval_environment(resource_services):
    bots, _ = resource_services
    bots.get_bot_by_address.return_value["bot_type"] = "personal"
    service = Mock(eval_publish=AsyncMock())
    request = Request({"type": "http", "method": "POST", "path": "/"})
    result = await publish_eval_env(
        "bot-1", 7, request, "actor", "owner", "", service, *resource_services,
    )
    assert result.status_code == 409
    service.eval_publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("disk", [None, 200])
async def test_publish_resource_spec_forwarded(resource_services, disk):
    from agentclaw.community.kernel.device_dto import ResourceSpecification

    service = Mock(eval_publish=AsyncMock(return_value={
        "success": True, "publish_id": 7, "stage": "eval", "bot_uuid": "eval-bot",
        "baas_publish_id": 42,
    }))
    request = Request({"type": "http", "method": "POST", "path": "/"})
    result = await publish_eval_env(
        "bot-1", 7, request, "actor", "owner", "task-1", service, *resource_services,
        cpu=4, memory=8, disk=disk,
    )
    assert result.code == 202000
    service.eval_publish.assert_awaited_once_with(
        publish_id=7, operator="actor", biz_id="task-1",
        resource_spec=ResourceSpecification(cpu=4, memory=8, disk=disk),
    )
