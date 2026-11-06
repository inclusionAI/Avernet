"""Formal HTTP routines calls through real Cron and recording local transport."""

import time

import jwt
import pytest

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.endpoints.test_openapi_routines import _Resolver, _KEY
from tests.community.endpoints.test_cron_router import _seed_bot_with_owner, _seed_bot_with_collaborator

BASE = "/openapi/v1/bots/bot_test/routines"
TARGET = {"runtime_stage": "draft", "device_uuid": "local_dev_test", "operator_id": "u_owner"}
BODY = {"name": "Daily", "trigger": {"type": "schedule", "cron": "0 9 * * *"}, "command": "Summarize"}


def headers(user="u_owner", app_only=False):
    now = int(time.time())
    principals = [{"type": "app", "tenant": "teamclaw", "app": {
        "app_id": 701, "app_name": "routine-test", "owners": "team", "tenant": "teamclaw"}}] if app_only else [
        {"type": "user", "subject": {"id": user, "username": "routine@example.test"}}]
    return {PRINCIPAL_HEADER: jwt.encode(
        {"iss": "gateway", "aud": "backend", "iat": now,
         "exp": now + 3600, "principals": principals}, _KEY, algorithm="HS256")}


def seed(world, collaborator=False):
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    (_seed_bot_with_collaborator if collaborator else _seed_bot_with_owner)(world)
    return world.get(DeviceAdapterTransport)


@pytest.mark.asyncio
async def test_real_http_create_manage_same_container_and_legacy_equivalence(world, async_client):
    transport = seed(world)
    query = {"user_id": "u_owner"}
    created = await async_client.post(BASE, params=query, headers=headers(), json={**BODY, **TARGET})
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert {key: data[key] for key in TARGET} == TARGET
    routine_id = data["routine_id"]
    path = f"{BASE}/{routine_id}"
    query.update(TARGET)
    listed = await async_client.get(BASE, params=query, headers=headers())
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["routine_id"] == routine_id
    detailed = await async_client.get(path, params=query, headers=headers())
    assert detailed.json()["data"]["owner_id"] == "u_owner"
    assert detailed.json()["data"]["device_uuid"] == "local_dev_test"
    updated = await async_client.request("PATCH", path, params=query, headers=headers(), json={"enabled": False})
    assert updated.status_code == 200
    run = await async_client.post(f"{path}/run", params=query, headers=headers())
    assert run.status_code == 200
    assert run.json()["data"]["device_uuid"] == "local_dev_test"
    history = await async_client.get(f"{path}/runs", params=query, headers=headers())
    assert history.status_code == 200
    before = len(transport.calls_to("invoke"))
    legacy = await async_client.get(f"/api/cron/{routine_id}", params={"bot_id": "bot_test", **TARGET}, headers={"x-user-id": "u_owner"})
    assert legacy.json()["success"] is True
    assert len(transport.calls_to("invoke")) == before + 1
    calls = transport.calls_to("invoke")
    assert calls
    assert all(not {"runtime_stage", "device_uuid", "operator_id"} & (c.kwargs.get("body") or {}).keys() for c in calls)
    removed = await async_client.delete(path, params=query, headers=headers())
    assert removed.json()["data"]["deleted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", [("GET", "", None), ("GET", "/job", None),
    ("PATCH", "/job", {"enabled": False}), ("DELETE", "/job", None),
    ("POST", "/job/run", None), ("GET", "/job/runs", None)])
@pytest.mark.parametrize("target,status", [({"runtime_stage": "prod"}, 400), ({"runtime_stage": "draft", "device_uuid": "foreign", "operator_id": "u_owner"}, 404)])
async def test_http_invalid_target_never_reaches_transport(world, async_client, method, suffix, body, target, status):
    transport = seed(world)
    result = await async_client.request(method, BASE + suffix, params={"user_id": "u_owner", **target}, headers=headers(), json=body)
    assert result.status_code == status, result.text
    assert result.json()["data"] is None
    assert transport.calls_to("invoke") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target,status", [({"device_uuid": "instance"}, 400),
    ({"runtime_stage": "prod"}, 422), ({"runtime_stage": "draft", "device_uuid": 123}, 422),
    ({"runtime_stage": "draft", "operator_id": ""}, 422)])
async def test_http_create_target_schema_and_combination_errors(world, async_client, target, status):
    transport = seed(world)
    result = await async_client.post(BASE, params={"user_id": "u_owner"}, headers=headers(), json={**BODY, **target})
    assert result.status_code == status, result.text
    assert result.json()["data"] is None
    assert transport.calls_to("invoke") == []


@pytest.mark.asyncio
async def test_http_deprecated_contract_does_not_gain_selectors(world, async_client):
    seed(world)
    response = await async_client.post("/openapi/v1/bots/routines", params={"user_id": "u_owner"}, headers=headers(),
                                       json={**BODY, "bot_id": "bot_test", "runtime_stage": "online", "device_uuid": "foreign"})
    assert response.status_code == 201, response.text
    schema = (await async_client.get("/openapi.json")).json()
    for operation in schema["paths"]["/openapi/v1/bots/routines/{routine_id}"].values():
        if not isinstance(operation, dict):
            continue
        assert not {"runtime_stage", "device_uuid", "operator_id"} & {p["name"] for p in operation.get("parameters", [])}
    assert not {"runtime_stage", "device_uuid", "operator_id"} & schema["components"]["schemas"]["RoutineCreate"]["properties"].keys()


@pytest.mark.asyncio
async def test_http_uuid_only_draft_runs_preserves_legacy_rejection(world, async_client):
    transport = seed(world)
    result = await async_client.get(BASE + "/job/runs", params={"user_id": "u_owner", "runtime_stage": "draft", "device_uuid": "local_dev_test"}, headers=headers())
    assert result.status_code == 400
    assert transport.calls_to("invoke") == []


class _RuntimeProviders:
    """Recording runtime capability boundaries around the real Cron service."""

    def __init__(self, bot_provider, *, caller=False):
        self.bot_provider = bot_provider
        self.caller = caller
        self.binding_requests = []
        self.connections = []
        self.devices = ["instance-a", "instance-b"]
        self.status = "ACTIVE"
        self.query_error = None
        self.binding_error = None

    def get_bot(self, bot_id, owner_id):
        bot = self.bot_provider.get_bot(bot_id, owner_id)
        return {**bot, "call_type": "caller" if self.caller else "shared"}

    def get_device(self, *, binding_id):
        return {"status": self.status, "device_provider": "baas", "device_id": "provider-device"}

    def list_devices_by_runtime_binding(self, **kwargs):
        if self.query_error:
            raise self.query_error
        return self.devices

    def resolve(self, request):
        from agentclaw.community.core.runtime_binding.models import ResolvedRuntimeBinding, RuntimeBindingSource
        self.binding_requests.append(request)
        if self.binding_error:
            raise self.binding_error
        return ResolvedRuntimeBinding(99, RuntimeBindingSource.CALLER_INSTANCE if self.caller else RuntimeBindingSource.SERVICE_ONLINE)

    def get_latest_by_source_bot_id_and_owner_and_status(self, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(id=7, ext={"binding": {"online": 99, "verify": 98}}, status=kwargs["status"])

    def resolve_for_binding(self, binding_id, actor, **kwargs):
        from types import SimpleNamespace
        self.connections.append((binding_id, actor, kwargs))
        return SimpleNamespace(conn_info={"url": kwargs.get("device_uuid", "single")})

    def resolve_for_binding_invoke(self, binding_id, actor, **kwargs):
        return self.resolve_for_binding(binding_id, actor, **kwargs)

    def resolve_for_bot(self, bot_id, owner_id, **kwargs):
        return self.resolve_for_binding(1, owner_id, **kwargs)


class _RecordingTransport:
    def __init__(self):
        self.calls = []
        self.fail_devices = set()
        self.error = None
        self.extensions = {}
        self.run_responses = {}

    def set_runs_response(self, device, data):
        """Configure per-device history at the transport boundary, without patching services."""
        self.run_responses[device] = data

    async def invoke(self, conn_info, method, path, body=None, params=None, **kwargs):
        self.calls.append((conn_info, method, path, body, params))
        if self.error:
            raise self.error
        if conn_info["url"] in self.fail_devices:
            return {"success": False, "data": None, "message": "instance unavailable"}
        if path.endswith("/runs"):
            if conn_info["url"] in self.run_responses:
                return {"success": True, "data": dict(self.run_responses[conn_info["url"]])}
            return {"success": True, "data": {"runs": [{"job_id": "same", "started_at_ms": 1000}], **self.extensions}}
        if path.endswith("/run"):
            return {"success": True, "data": {"status": "dispatched", **self.extensions}}
        job = {"id": "same", "schedule": {"expr": "0 9 * * *"}, "payload": {"message": "complete business message"}, **self.extensions}
        return {"success": True, "data": [job] if method == "GET" and path == "/api/cron" else job}


def seed_recording_runtime(world, *, caller=False, collaborator=False):
    from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
    from agentclaw.community.core.cron.services.cron_relay import CronRelayService
    seed(world, collaborator)
    wired = world.get(CronRelayServiceProtocol)
    providers = _RuntimeProviders(wired._bot_provider, caller=caller)
    transport = _RecordingTransport()
    real = CronRelayService(providers, providers, transport, providers, wired._template_repo,
                            providers, providers, wired._collaborators)
    world.injector.binder.bind(CronRelayServiceProtocol, to=real)
    return providers, transport


@pytest.mark.asyncio
async def test_http_real_multireplica_list_and_stage_broadcast(world, async_client):
    providers, transport = seed_recording_runtime(world)
    query = {"user_id": "u_owner", "runtime_stage": "online"}
    listed = await async_client.get(BASE, params=query, headers=headers())
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 2
    assert [row["routine_id"] for row in listed.json()["data"]["items"]] == ["same", "same"]
    assert {row["device_uuid"] for row in listed.json()["data"]["items"]} == set(providers.devices)
    before = len(transport.calls)
    update = await async_client.request("PATCH", BASE + "/same", params=query, headers=headers(), json={"enabled": False})
    assert update.status_code == 200, update.text
    assert update.json()["data"]["succeeded"] == 2
    assert len(transport.calls) == before + 2
    run = await async_client.post(BASE + "/same/run", params=query, headers=headers())
    assert run.status_code == 200, run.text
    assert {row["run"]["device_uuid"] for row in run.json()["data"]["results"]} == set(providers.devices)
    runs = await async_client.get(BASE + "/same/runs", params=query, headers=headers())
    assert runs.json()["data"]["total"] == 2
    assert {row["device_uuid"] for row in runs.json()["data"]["items"]} == set(providers.devices)
    before = len(transport.calls)
    deleted = await async_client.delete(BASE + "/same", params=query, headers=headers())
    assert deleted.status_code == 403
    assert len(transport.calls) == before
    assert providers.binding_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_devices,success", [({"instance-b"}, False), ({"instance-a", "instance-b"}, False)])
async def test_http_real_broadcast_partial_or_total_failure_is_standard_error(world, async_client, fail_devices, success):
    _, transport = seed_recording_runtime(world)
    transport.fail_devices = fail_devices
    result = await async_client.post(BASE + "/same/run", params={"user_id": "u_owner", "runtime_stage": "online"}, headers=headers())
    assert result.status_code == 502, result.text
    assert result.json()["data"] is None
    assert len(transport.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [False, True])
async def test_http_explicit_target_is_single_call_and_uses_authenticated_actor(world, async_client, caller):
    providers, transport = seed_recording_runtime(world, caller=caller, collaborator=True)
    query = {"user_id": "u_collab", "entity_id": "u_owner"}
    result = await async_client.post(BASE, params=query, headers=headers("u_collab"), json={**BODY, "runtime_stage": "online", "device_uuid": "instance-a"})
    assert result.status_code == 201, result.text
    assert result.json()["data"]["operator_id"] == "u_collab"
    assert len(transport.calls) == 1
    assert providers.binding_requests[0].actor_user_id == "u_collab"
    assert providers.binding_requests[0].owner_id == "u_owner"
    assert providers.connections[0][1] == ("u_collab" if caller else "u_owner")


@pytest.mark.asyncio
async def test_http_default_caller_list_selects_current_actor_online_only(world, async_client):
    providers, transport = seed_recording_runtime(world, caller=True, collaborator=True)
    result = await async_client.get(BASE, params={"user_id": "u_collab", "entity_id": "u_owner"}, headers=headers("u_collab"))
    assert result.status_code == 200, result.text
    assert len(transport.calls) == 2
    assert providers.binding_requests[0].actor_user_id == "u_collab"
    assert {row["operator_id"] for row in result.json()["data"]["items"]} == {"u_collab"}
    assert {row["runtime_stage"] for row in result.json()["data"]["items"]} == {"online"}


@pytest.mark.asyncio
async def test_http_upstream_exception_never_logs_credential_cause(world, async_client, caplog):
    _, transport = seed_recording_runtime(world)
    sentinel = "credential-" + "do-not-log"
    transport.error = ValueError(sentinel)
    result = await async_client.post(BASE, params={"user_id": "u_owner"}, headers=headers(), json={**BODY, "runtime_stage": "online", "device_uuid": "instance-a"})
    assert result.status_code == 502, result.text
    assert sentinel not in result.text
    assert sentinel not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("app_grant", [False, True])
async def test_http_app_only_keeps_grant_and_authorized_acting_user(world, async_client, app_grant):
    from agentclaw.community.core.repository.protocols.bot import BotAppGrantRepositoryProtocol
    providers, transport = seed_recording_runtime(world)
    if app_grant:
        world.get(BotAppGrantRepositoryProtocol).grant({"app_id": 701, "app_name": "routine-test", "bot_id": "bot_test",
            "user_id": "u_owner", "owner_id": "u_owner", "granted_by": "u_owner"})
    result = await async_client.get(BASE, params={"user_id": "u_owner", "runtime_stage": "online", "device_uuid": "instance-a"}, headers=headers(app_only=True))
    assert result.status_code == (200 if app_grant else 404), result.text
    if app_grant:
        assert len(transport.calls) == 1
        assert providers.binding_requests[0].actor_user_id == "u_owner"
    else:
        assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [("multiple", 400), ("wrong", 404), ("inactive", 409), ("timeout", 504), ("caller_stage", 400), ("caller_missing", 409)])
async def test_http_target_resolution_failures_never_forward(world, async_client, failure, status):
    from agentclaw.community.core.runtime_binding.errors import CallerInstanceNotReadyError
    providers, transport = seed_recording_runtime(world, caller=failure.startswith("caller"))
    target = {"runtime_stage": "online", "device_uuid": "instance-a"}
    if failure == "multiple":
        target.pop("device_uuid")
    elif failure == "wrong":
        target["device_uuid"] = "foreign"
    elif failure == "inactive":
        providers.status = "PENDING"
    elif failure == "timeout":
        providers.query_error = TimeoutError("runtime query timeout")
    elif failure == "caller_stage":
        target["runtime_stage"] = "draft"
    else:
        providers.binding_error = CallerInstanceNotReadyError("Caller unavailable")
    result = await async_client.post(BASE, params={"user_id": "u_owner"}, headers=headers(), json={**BODY, **target})
    assert result.status_code == status, result.text
    assert result.json()["data"] is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_http_member_cannot_delegate_caller_even_with_edit_lock(world, async_client):
    from tests.community.factories.access import make_staff_user
    from tests.community.factories.bot_collaborator import make_collaborator
    from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService
    _, transport = seed_recording_runtime(world, caller=True)
    make_staff_user(world, user_id="u_member")
    make_collaborator(world, user_id="u_member", role="member")
    world.get(CollaboratorLockService).acquire_lock("bot_test", "u_owner", "u_member")
    result = await async_client.post(BASE + "/job/run", params={"user_id": "u_member", "entity_id": "u_owner",
        "runtime_stage": "online", "device_uuid": "instance-a", "operator_id": "u_owner"}, headers=headers("u_member"))
    assert result.status_code == 403, result.text
    assert transport.calls == []


@pytest.mark.asyncio
async def test_http_uuid_only_published_runs_keeps_device_filter_branch(world, async_client):
    providers, transport = seed_recording_runtime(world)
    result = await async_client.get(BASE + "/job/runs", params={"user_id": "u_owner", "runtime_stage": "online", "device_uuid": "instance-b"}, headers=headers())
    assert result.status_code == 200, result.text
    assert len(transport.calls) == 1
    assert transport.calls[0][0]["url"] == "instance-b"
    assert providers.binding_requests == []


@pytest.mark.asyncio
async def test_http_logs_business_fields_trace_and_session_keys_without_credentials(world, async_client, caplog):
    import logging
    _, transport = seed_recording_runtime(world)
    caplog.set_level(logging.INFO)
    sentinel = "private-auth-" + "value"
    transport.extensions = {
        "token": sentinel,
        "Authorization": sentinel,
        "Cookie": sentinel,
        "secret": sentinel,
        "credentials": {
            "password": sentinel,
        },
        "sessionKey": "business-session-a",
        "session_key": "business-session-b",
    }
    result = await async_client.post(BASE, params={"user_id": "u_owner"}, headers={**headers(), "Cookie": sentinel}, json={**BODY, "runtime_stage": "online", "device_uuid": "instance-a"})
    assert result.status_code == 201, result.text
    assert sentinel not in caplog.text
    assert "business-session-a" in caplog.text and "business-session-b" in caplog.text
    assert "Summarize" in caplog.text and "complete business message" in caplog.text
    assert result.json()["request_id"] in caplog.text
    assert "openapi.routines.request" in caplog.text and "openapi.routines.success" in caplog.text
