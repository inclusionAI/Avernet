"""Single-container routing, delegated Caller identity and boundary diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeBinding,
    RuntimeBindingSource,
)
from agentclaw.community.core.runtime_binding.errors import CallerInstanceNotReadyError


def service(*, caller=False, provider="baas", devices=("instance-a", "instance-b")):
    bot = MagicMock()
    bot.get_bot.return_value = dict(
        id=7,
        bot_id="bot",
        bot_type="service",
        owner_id="owner",
        bot_name="Scheduled",
        call_type="caller" if caller else "shared",
        binding_id=1,
    )
    device = MagicMock()
    device.get_device.return_value = dict(
        status="ACTIVE", device_provider=provider, device_id="sandbox"
    )
    device.list_devices_by_runtime_binding.return_value = list(devices)
    resolver = MagicMock()
    resolver.resolve_for_binding.return_value = SimpleNamespace(
        conn_info={"headers": {"Authorization": "Bearer test-secret"}},
    )
    bindings = MagicMock()
    bindings.resolve.return_value = ResolvedRuntimeBinding(
        99,
        RuntimeBindingSource.CALLER_INSTANCE
        if caller
        else RuntimeBindingSource.SERVICE_ONLINE,
    )
    return CronRelayService(
        bot,
        device,
        AsyncMock(),
        resolver,
        MagicMock(),
        MagicMock(),
        bindings,
        MagicMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
async def test_creation_locks_one_device_and_returns_target(stage):
    svc = service()
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    result = await svc.create_cron(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        body={"name": "Daily", "command": "Summarize"},
        runtime_stage=stage,
        device_id="instance-a",
        authenticated_user_id="owner",
    )
    assert svc._transport.invoke.await_count == 1
    assert svc._resolver.resolve_for_bot.call_count == 0
    svc._resolver.resolve_for_binding.assert_called_once_with(
        99, "owner", bot_id="bot", device_uuid="instance-a"
    )
    assert result["data"]["runtime_stage"] == stage
    assert result["data"]["device_id"] == "instance-a"
    assert result["data"]["operator_id"] == "owner"
    assert "create_by_id" not in result["data"]


@pytest.mark.asyncio
async def test_caller_identity_is_not_authenticated_user_and_checks_delegation():
    svc = service(caller=True)
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    await svc.create_cron(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        body={},
        runtime_stage="online",
        device_id="instance-b",
        operator_id="caller",
        authenticated_user_id="admin",
    )
    request = svc._runtime_bindings.resolve.call_args.args[0]
    assert request.actor_user_id == "caller"
    assert request.owner_id == "owner"
    assert svc._collaborators.check_permission.call_args.args[:3] == (
        7,
        "admin",
        "owner",
    )
    assert svc._resolver.resolve_for_binding.call_args.args == (99, "admin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        ("wrong_device", 404),
        ("missing_device", 400),
        ("delegation", 403),
        ("caller_missing", 409),
        ("inactive", 409),
    ],
)
async def test_failed_target_never_forwards_or_falls_back(failure, expected):
    svc = service(caller=failure == "caller_missing")
    kwargs = dict(runtime_stage="online", device_id="instance-a", authenticated_user_id="owner")
    if failure == "wrong_device":
        kwargs["device_id"] = "foreign-instance"
    elif failure == "missing_device":
        kwargs["device_id"] = None
    elif failure == "delegation":
        kwargs["operator_id"] = "caller"
        svc._collaborators.check_permission.side_effect = PermissionError("Denied")
    elif failure == "caller_missing":
        svc._runtime_bindings.resolve.side_effect = CallerInstanceNotReadyError(
            "Not ready"
        )
    elif failure == "inactive":
        svc._device_provider.get_device.return_value["status"] = "PENDING"
    with pytest.raises(CronRelayError) as exc:
        await svc.create_cron(
            bot_id="bot", user_id="owner", nick_name="Owner", body={}, **kwargs
        )
    assert exc.value.error_code == expected
    svc._transport.invoke.assert_not_called()
    svc._resolver.resolve_for_bot.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "verify"])
async def test_explicit_caller_stage_is_rejected(stage):
    svc = service(caller=True)
    with pytest.raises(CronRelayError, match="only support"):
        await svc.create_cron(
            bot_id="bot",
            user_id="owner",
            nick_name="Owner",
            body={},
            runtime_stage=stage,
            device_id="instance-a",
        )
    svc._runtime_bindings.resolve.assert_not_called()
    svc._transport.invoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "get_cron_detail",
        "update_cron",
        "delete_cron",
        "run_cron",
        "get_cron_runs",
        "list_running_crons",
    ],
)
async def test_management_targets_original_container(operation):
    svc = service(caller=True)
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    kwargs = dict(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        runtime_stage="online",
        device_id="instance-a",
        operator_id="caller",
        authenticated_user_id="owner",
    )
    if operation != "list_running_crons":
        kwargs["task_id"] = "job"
    if operation == "update_cron":
        kwargs["body"] = {"command": "Edited task"}
    await getattr(svc, operation)(**kwargs)
    assert svc._transport.invoke.await_count == 1
    assert (
        svc._resolver.resolve_for_binding.call_args.kwargs["device_uuid"]
        == "instance-a"
    )
    svc._resolver.resolve_for_bot.assert_not_called()


@pytest.mark.asyncio
async def test_single_instance_without_device_is_selected_once():
    svc = service(devices=("only-instance",))
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    result = await svc.create_cron(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        body={},
        runtime_stage="online",
    )
    assert result["data"]["device_id"] == "only-instance"
    assert svc._transport.invoke.await_count == 1


@pytest.mark.asyncio
async def test_legacy_caller_draft_uses_parent_binding():
    svc = service(caller=True)
    svc._resolver.resolve_for_bot.return_value = SimpleNamespace(conn_info={})
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    await svc.create_cron(bot_id="bot", user_id="owner", nick_name="Owner", body={})
    svc._runtime_bindings.resolve.assert_not_called()
    svc._resolver.resolve_for_bot.assert_called_once_with("bot", "owner")
    assert svc._transport.invoke.await_count == 1


@pytest.mark.asyncio
async def test_transport_boundary_events_keep_session_key_and_exclude_credentials(
    monkeypatch,
):
    from agentclaw.community.core.cron.services import cron_targeted_operations

    log = MagicMock()
    monkeypatch.setattr(cron_targeted_operations, "logger", log)
    svc = service()
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    kwargs = dict(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        runtime_stage="online",
        device_id="instance-a",
    )
    await svc.create_cron(
        body={
            "command": "Task",
            "sessionKey": "business-session",
            "notify": {"token": "notify-secret"},
        },
        **kwargs,
    )
    output = "\n".join(call.args[0] % call.args[1:] for call in log.mock_calls)
    for event in [
        "cron.request",
        "cron.target.resolved",
        "cron.transport.request",
        "cron.transport.success",
        "cron.response",
    ]:
        assert event in output
    assert "business-session" in output
    assert "'operator_id': 'owner'" in output
    assert "'authenticated_user_id': 'owner'" in output
    assert "test-secret" not in output
    assert "notify-secret" not in output
    svc._transport.invoke.side_effect = RuntimeError("Adapter unavailable")
    with pytest.raises(RuntimeError):
        await svc.create_cron(body={}, **kwargs)
    assert "cron.transport.failure" in str(log.mock_calls)
    assert "cron.failure" in str(log.mock_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["arca", "local"])
async def test_single_provider_checks_device_identity(provider):
    svc = service(provider=provider)
    svc._transport.invoke.return_value = {"success": True, "data": {"task_id": "job"}}
    kwargs = dict(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        body={},
        runtime_stage="online",
    )
    await svc.create_cron(device_id="sandbox", **kwargs)
    assert svc._transport.invoke.await_count == 1
    svc._device_provider.list_devices_by_runtime_binding.assert_not_called()
    with pytest.raises(CronRelayError) as exc:
        await svc.create_cron(device_id="another-sandbox", **kwargs)
    assert exc.value.error_code == 404
    assert svc._transport.invoke.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code", [(TimeoutError(), 504), (RuntimeError("Unavailable"), 409)]
)
async def test_device_query_failure_is_explicit(error, code):
    svc = service()
    svc._device_provider.list_devices_by_runtime_binding.side_effect = error
    with pytest.raises(CronRelayError) as exc:
        await svc.create_cron(
            bot_id="bot",
            user_id="owner",
            nick_name="Owner",
            body={},
            runtime_stage="online",
        )
    assert exc.value.error_code == code
    svc._transport.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_list_is_routed_to_caller_and_decorates_every_row():
    svc = service(caller=True)
    svc._transport.invoke.return_value = {
        "success": True,
        "data": [{"id": "task-a"}, {"id": "task-b"}],
    }
    result = await svc.list_all_crons(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        runtime_stage="online",
        device_id="instance-a",
        operator_id="caller",
    )
    assert [row["device_id"] for row in result["data"]] == ["instance-a", "instance-a"]
    assert [row["operator_id"] for row in result["data"]] == ["caller", "caller"]
    assert svc._transport.invoke.await_count == 1
    with pytest.raises(CronRelayError):
        await svc.list_all_crons(
            bot_id="all",
            user_id="owner",
            nick_name="Owner",
            runtime_stage="online",
            device_id="instance-a",
        )


@pytest.mark.asyncio
async def test_adapter_failure_preserves_result_and_records_failure(monkeypatch):
    from agentclaw.community.core.cron.services import cron_targeted_operations

    log = MagicMock()
    monkeypatch.setattr(cron_targeted_operations, "logger", log)
    svc = service()
    svc._transport.invoke.return_value = {
        "success": False,
        "message": "rejected",
        "data": None,
    }
    result = await svc.create_cron(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        body={},
        runtime_stage="online",
        device_id="instance-a",
    )
    assert result == {"success": False, "message": "rejected", "data": None}
    output = "\n".join(call.args[0] % call.args[1:] for call in log.mock_calls)
    assert "cron.transport.failure" in output
    assert "success=False" in output


def test_same_namespace_injector_resolves_cron_dependencies(test_injector):
    from agentclaw.community.core.runtime_binding.service import (
        RuntimeBindingResolutionService,
    )
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
        CollaboratorService,
    )
    from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol

    svc = test_injector.get(CronRelayServiceProtocol)
    assert type(svc) is CronRelayService
    assert svc._runtime_bindings is test_injector.get(RuntimeBindingResolutionService)
    assert svc._collaborators is test_injector.get(CollaboratorService)


@pytest.mark.asyncio
async def test_target_failure_does_not_claim_transport_was_called(monkeypatch):
    from agentclaw.community.core.cron.services import cron_targeted_operations

    log = MagicMock()
    monkeypatch.setattr(cron_targeted_operations, "logger", log)
    svc = service()
    with pytest.raises(CronRelayError):
        await svc.create_cron(
            bot_id="bot",
            user_id="owner",
            nick_name="Owner",
            body={},
            runtime_stage="online",
            device_id="wrong-instance",
        )
    output = "\n".join(call.args[0] % call.args[1:] for call in log.mock_calls)
    assert "cron.failure" in output
    assert "does not belong" in output
    assert "cron.transport.failure" not in output
    assert "cron.transport.request" not in output
    svc._transport.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_running_rows_keep_caller_target_metadata():
    svc = service(caller=True)
    svc._transport.invoke.return_value = {
        "success": True,
        "data": {"running": [{"task_id": "job"}]},
    }
    result = await svc.list_running_crons(
        bot_id="bot",
        user_id="owner",
        nick_name="Owner",
        runtime_stage="online",
        device_id="instance-a",
        operator_id="caller",
    )
    assert result["data"][0]["device_id"] == "instance-a"
    assert result["data"][0]["operator_id"] == "caller"


def test_log_projection_includes_notify_contract_fields_only():
    from agentclaw.community.core.cron.services.cron_targeted_operations import (
        _cron_business_fields,
    )

    fields = _cron_business_fields(
        {
            "name": "Daily",
            "notify": {"enabled": True, "user_ids": ["staff"], "token": "hidden"},
            "Authorization": "hidden",
            "sessionKey": "visible",
        }
    )
    assert fields == {
        "name": "Daily",
        "notify": {"enabled": True, "user_ids": ["staff"]},
        "sessionKey": "visible",
    }

@pytest.mark.asyncio
async def test_create_adapter_rejects_non_object_body():
    import inspect
    from unittest.mock import AsyncMock, MagicMock
    from agentclaw.community.adapters.http.cron.router import create_cron
    from agentclaw.community.adapters.http.auth.models import AuthenticatedUser

    request = MagicMock()
    request.json = AsyncMock(return_value=["not an object"])
    svc = MagicMock()
    svc.create_cron = AsyncMock()
    result = await inspect.unwrap(create_cron)(
        request=request,
        user=AuthenticatedUser(
            id="owner", staffId="owner", nickName="Owner", operatorName="Owner"
        ),
        service=svc,
        bot_service=MagicMock(),
    )
    assert result.success is False
    assert result.error_code == 400
    svc.create_cron.assert_not_called()
