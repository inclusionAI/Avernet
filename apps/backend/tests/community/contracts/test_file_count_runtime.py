"""File-count consumer contracts against real domain resolution and DI."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
async def test_count_current_stage_instances(world, stage):
    from agentclaw.community.api.file_count_service import FileCountServiceProtocol
    from agentclaw.community.kernel.file_count import FileCountQuery
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
    from tests.community.factories.publish_ignore import seed_publish_ignore

    seed_publish_ignore(world, stage=stage)
    world.get(DeviceAdapterTransport).set_response("invoke", {
        "success": True, "data": {"path": "/workspace", "file_count": 7, "elapsed_ms": 1},
    })
    service = world.get(FileCountServiceProtocol)
    result = await service.query(
        FileCountQuery("ignore-bot", "ignore_owner", stage, "/workspace", "count-request"),
        "ignore_owner", is_admin=False,
    )
    assert result["success"] is True
    assert result["stage"] == stage
    assert result["results"][0]["file_count"] == 7
    call = world.get(DeviceAdapterTransport).calls_to("invoke")[0]
    assert call.args[0]["device_uuid"] == "replica-a"
    assert call.args[0]["bot_uuid"] == "ignore-runtime"
    assert call.args[1:3] == ("GET", "/api/file/count")
    assert call.kwargs["params"] == {"path": "/workspace", "request_id": "count-request"}


@pytest.mark.asyncio
async def test_count_denies_anonymous(world):
    from agentclaw.community.api.file_count_service import FileCountServiceProtocol
    from agentclaw.community.kernel.file_count import FileCountError, FileCountQuery

    with pytest.raises(FileCountError, match="permission_denied"):
        await world.get(FileCountServiceProtocol).query(
            FileCountQuery("missing", "entity", "online", "/workspace", "r"),
            "anonymous", is_admin=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case,error", [("missing", "bot_not_found"), ("denied", "permission_denied"),
                                       ("type", "not_service_bot"), ("stage", "stage_not_bound"),
                                       ("binding", "binding_conflict"), ("provider", "unsupported_provider"),
                                       ("empty", "no_current_instances")])
async def test_domain_failures_do_not_scan(case, error):
    from types import SimpleNamespace
    from unittest.mock import Mock, AsyncMock
    from agentclaw.community.core.service_bot.services.file_count_service import FileCountService
    from agentclaw.community.kernel.file_count import FileCountError, FileCountQuery
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel
    from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError

    bots = Mock(get_by_id_and_entity=Mock(return_value=None if case == "missing" else {
        "owner_id": "owner", "bot_type": "personal" if case == "type" else "service",
    }))
    permissions = Mock(get_operable_permission_level=Mock(return_value=(
        PermissionLevel.NONE if case == "denied" else PermissionLevel.ADMIN)))
    resolver = Mock(resolve=Mock(return_value=SimpleNamespace(binding_id=1)))
    if case == "stage":
        resolver.resolve.side_effect = RuntimeBindingResolutionError("stage missing")
    bindings = Mock(get_by_id=Mock(return_value=None if case == "binding" else SimpleNamespace(
        id=1, device_provider="unknown" if case == "provider" else "baas", device_id="d")))
    runtime = SimpleNamespace(targets=AsyncMock(return_value=[]), query=AsyncMock())
    service = FileCountService(bots, resolver, bindings, permissions, runtime, "dev")
    with pytest.raises(FileCountError, match=error):
        await service.query(FileCountQuery("bot", "owner", "online", ".", "r"), "actor", is_admin=False)
    runtime.query.assert_not_called()


@pytest.mark.asyncio
async def test_multi_instance_bounded_and_partial_results(world):
    import asyncio
    from typing import Annotated
    from agentclaw.community.api.file_count_service import FileCountServiceProtocol
    from agentclaw.community.kernel.file_count import FileCountQuery
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
    from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS
    from tests.community.factories.publish_ignore import seed_publish_ignore
    from tests.community.framework import http_envelope_response

    seed_publish_ignore(world)
    world.get(Annotated[HttpClient, QUALIFIER_BAAS]).set_response("get", http_envelope_response(
        data=[{"items": [{"uuid": "a"}, {"uuid": "b"}, {"uuid": "c"}]}]))
    active = 0
    maximum = 0

    async def invoke(conn, method, path, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        if conn["device_uuid"] == "b":
            raise TimeoutError("secret-value")
        return {"success": True, "data": {"path": ".", "file_count": 0, "elapsed_ms": 1}}

    world.get(DeviceAdapterTransport).set_override("invoke", invoke)
    result = await world.get(FileCountServiceProtocol).query(
        FileCountQuery("ignore-bot", "ignore_owner", "online", ".", "r"), "ignore_owner", is_admin=False)
    assert maximum == 2
    assert result["success"] is False
    assert [r["file_count"] for r in result["results"]] == [0, None, 0]
    assert [r["status"] for r in result["results"]] == ["success", "timeout", "success"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,is_admin,allowed", [(None, False, False), ("member", False, False),
                                                  ("admin", False, True), (None, True, True)])
async def test_real_management_permission(world, role, is_admin, allowed):
    from agentclaw.community.api.file_count_service import FileCountServiceProtocol
    from agentclaw.community.core.bot_collaborator.collaborator_service_protocol import CollaboratorServiceProtocol
    from agentclaw.community.kernel.file_count import FileCountQuery, FileCountError
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
    from tests.community.factories.file_count import seed_file_count
    from tests.community.factories.access import make_staff_user

    seed_file_count(world)
    make_staff_user(world, user_id="count_actor")
    if role:
        world.get(CollaboratorServiceProtocol).add_collaborator(
            "ignore-bot", "ignore_owner", "count_actor", "ignore_owner", role=role, env="dev")
    service = world.get(FileCountServiceProtocol)
    query = FileCountQuery("ignore-bot", "ignore_owner", "draft", "/workspace", "r")
    if allowed:
        result = await service.query(query, "count_actor", is_admin=is_admin)
        assert result["success"] is True
    else:
        with pytest.raises(FileCountError, match="permission_denied"):
            await service.query(query, "count_actor", is_admin=is_admin)
        assert not world.get(DeviceAdapterTransport).calls_to("invoke")


def test_public_protocol_is_owning_core_protocol():
    from agentclaw.community.api.file_count_service import FileCountServiceProtocol
    from agentclaw.community.core.service_bot.file_count_service_protocol import FileCountServiceProtocol as CoreProtocol
    from agentclaw.community.plugin_api.file_count_runtime import FileCountRuntime
    from agentclaw.community.plugins.community.file_count_runtime import HttpFileCountRuntime
    assert FileCountServiceProtocol is CoreProtocol
    assert issubclass(HttpFileCountRuntime, FileCountRuntime)
