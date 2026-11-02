"""Single-container routing, delegated Caller identity and boundary diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
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
async def test_list_all_crons_missing_bot_returns_empty_list():
    svc = service()
    svc._bot_provider.get_bot.side_effect = BotNotFoundError("Bot not found: bot")
    result = await svc.list_all_crons(
        user_id="wrong-owner",
        nick_name="Owner",
        bot_id="bot",
        runtime_stage=None,
    )
    assert result == {"success": True, "data": [], "total": 0, "failed_targets": []}


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
        device_uuid="instance-a",
        authenticated_user_id="owner",
    )
    assert svc._transport.invoke.await_count == 1
    assert svc._resolver.resolve_for_bot.call_count == 0
    svc._resolver.resolve_for_binding.assert_called_once_with(
        99, "owner", bot_id="bot", device_uuid="instance-a"
    )
    assert result["data"]["runtime_stage"] == stage
    assert result["data"]["device_uuid"] == "instance-a"
    assert result["data"]["operator_id"] == "owner"
    assert "create_by_id" not in result["data"]
    assert "device_id" not in result["data"]


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
        device_uuid="instance-b",
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
    kwargs = dict(runtime_stage="online", device_uuid="instance-a", authenticated_user_id="owner")
    if failure == "wrong_device":
        kwargs["device_uuid"] = "foreign-instance"
    elif failure == "missing_device":
        kwargs["device_uuid"] = None
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
            device_uuid="instance-a",
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
        device_uuid="instance-a",
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
    assert result["data"]["device_uuid"] == "only-instance"
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
        device_uuid="instance-a",
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
    await svc.create_cron(device_uuid="sandbox", **kwargs)
    assert svc._transport.invoke.await_count == 1
    svc._device_provider.list_devices_by_runtime_binding.assert_not_called()
    with pytest.raises(CronRelayError) as exc:
        await svc.create_cron(device_uuid="another-sandbox", **kwargs)
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
        device_uuid="instance-a",
        operator_id="caller",
    )
    assert [row["device_uuid"] for row in result["data"]] == ["instance-a", "instance-a"]
    assert [row["operator_id"] for row in result["data"]] == ["caller", "caller"]
    assert svc._transport.invoke.await_count == 1
    with pytest.raises(CronRelayError):
        await svc.list_all_crons(
            bot_id="all",
            user_id="owner",
            nick_name="Owner",
            runtime_stage="online",
            device_uuid="instance-a",
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
        device_uuid="instance-a",
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
            device_uuid="wrong-instance",
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
        device_uuid="instance-a",
        operator_id="caller",
    )
    assert result["data"][0]["device_uuid"] == "instance-a"
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage,message",
    [
        ("verify", "预发运行环境当前不可用，请先发布到预发，待实例就绪后重试。"),
        ("online", "生产运行环境当前不可用，请先发布到生产，待实例就绪后重试。"),
    ],
)
async def test_unavailable_stage_returns_actionable_business_error(stage, message, caplog):
    svc = service()
    cause = EngineStageNotLiveError(f"no live {stage} runtime for bot=bot env=pre")
    svc._runtime_bindings.resolve.side_effect = cause
    with pytest.raises(CronRelayError) as caught:
        await svc.create_cron(
            bot_id="bot",
            user_id="owner",
            nick_name="Owner",
            body={"name": "Daily"},
            runtime_stage=stage,
            authenticated_user_id="owner",
        )
    assert caught.value.error_code == 409
    assert str(caught.value) == message
    assert caught.value.__cause__ is cause
    svc._transport.invoke.assert_not_awaited()
    assert "cron.target.runtime_unavailable" in caplog.text
    assert str(cause) in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_filter", ["bot", "all"])
async def test_default_listing_uses_authenticated_users_caller_not_parent(bot_filter):
    """Owner-scoped Bot lookup must not choose the owner's Caller container."""
    svc = service(caller=True, provider="local")
    caller = dict(svc._bot_provider.get_bot.return_value)
    svc._bot_provider.list_bots_by_owner_or_collaborator.return_value = {"items": [caller]}
    svc._resolver.resolve_for_bot.return_value = SimpleNamespace(conn_info={"binding": 1})
    svc._publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = None
    svc._resolver.resolve_for_binding.side_effect = lambda binding, actor, **kw: SimpleNamespace(
        conn_info={"binding": binding, "actor": actor}
    )
    svc._transport.invoke.side_effect = lambda conn, *args, **kw: {
        "success": True,
        "data": [{"id": "caller-job" if conn.get("binding") == 99 else "parent-job"}],
    }

    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id=bot_filter,
        authenticated_user_id="viewer",
    )

    assert [row["id"] for row in result["data"]] == ["caller-job"]
    assert result["data"][0]["operator_id"] == "viewer"
    assert result["data"][0]["runtime_stage"] == "online"
    assert result["failed_targets"] == []
    request = svc._runtime_bindings.resolve.call_args.args[0]
    assert (request.owner_id, request.actor_user_id, request.stage) == ("owner", "viewer", "online")
    assert svc._resolver.resolve_for_binding.call_args.args == (99, "viewer")
    svc._publish_repo.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()


@pytest.mark.asyncio
async def test_default_listing_merges_shared_and_callers_existing_instances():
    svc = service(caller=True)
    caller = dict(svc._bot_provider.get_bot.return_value)
    personal = dict(caller, bot_id="personal", bot_type="personal", call_type="owner", binding_id=2)
    svc._bot_provider.list_bots_by_owner_or_collaborator.return_value = {"items": [personal, caller]}
    svc._device_provider.get_device.side_effect = lambda *, binding_id: {
        "status": "ACTIVE", "device_provider": "baas" if binding_id == 99 else "local"
    }
    svc._resolver.resolve_for_bot.return_value = SimpleNamespace(conn_info={"binding": 2})
    svc._resolver.resolve_for_binding.side_effect = lambda binding, actor, **kw: SimpleNamespace(
        conn_info={"binding": binding, "actor": actor, "device": kw.get("device_uuid")}
    )
    svc._transport.invoke.side_effect = lambda conn, *args, **kw: {
        "success": True, "data": [{"id": "same-id", "source": conn["binding"]}],
    }

    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id="all", authenticated_user_id="viewer",
    )

    assert sorted(row["source"] for row in result["data"]) == [2, 99, 99]
    caller_rows = [row for row in result["data"] if row["source"] == 99]
    assert {row["device_uuid"] for row in caller_rows} == {"instance-a", "instance-b"}
    assert {row["operator_id"] for row in caller_rows} == {"viewer"}
    assert {call.args[1] for call in svc._resolver.resolve_for_binding.call_args_list} == {"viewer"}
    assert result["total"] == 3
    svc._runtime_bindings.resolve.assert_called_once()
    svc._device_provider.list_devices_by_runtime_binding.assert_called_once_with(
        binding_id=99, timeout=10.0,
    )


@pytest.mark.asyncio
async def test_missing_current_caller_does_not_fall_back_or_block_other_bots():
    svc = service(caller=True, provider="local")
    caller = dict(svc._bot_provider.get_bot.return_value)
    personal = dict(caller, bot_id="personal", bot_type="personal", call_type="owner", binding_id=2)
    svc._bot_provider.list_bots_by_owner_or_collaborator.return_value = {"items": [caller, personal]}
    svc._runtime_bindings.resolve.side_effect = CallerInstanceNotReadyError("Caller instance is not ready")
    svc._resolver.resolve_for_bot.return_value = SimpleNamespace(conn_info={"binding": 2})
    svc._transport.invoke.return_value = {"success": True, "data": [{"id": "personal-job"}]}

    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id="all", authenticated_user_id="viewer",
    )

    assert [row["id"] for row in result["data"]] == ["personal-job"]
    assert len(result["failed_targets"]) == 1
    assert result["failed_targets"][0]["bot_id"] == "bot"
    assert result["failed_targets"][0]["reason"] == "caller_unavailable"
    assert svc._transport.invoke.await_count == 1
    assert svc._resolver.resolve_for_bot.call_args.args[:2] == ("personal", "owner")
    svc._resolver.resolve_for_binding.assert_not_called()


@pytest.mark.asyncio
async def test_default_caller_online_filter_and_internal_user_fallback():
    svc = service(caller=True, provider="local")
    svc._transport.invoke.return_value = {"success": True, "data": [{"id": "job"}]}
    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id="bot", runtime_stage="online",
    )
    assert result["data"][0]["operator_id"] == "owner"
    assert svc._runtime_bindings.resolve.call_args.args[0].actor_user_id == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["draft", "verify"])
async def test_explicit_non_online_listing_does_not_select_caller(stage):
    svc = service(caller=True, provider="local")
    svc._publish_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = None
    svc._publish_repo.get_retained_verify_by_source_bot_id_and_owner.return_value = None
    svc._transport.invoke.return_value = {"success": True, "data": []}
    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id="bot", runtime_stage=stage,
        authenticated_user_id="viewer",
    )
    assert result["success"] is True
    svc._runtime_bindings.resolve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [CallerInstanceNotReadyError("not ready"), RuntimeError("lookup failed")])
async def test_caller_resolution_failures_return_failed_targets_without_parent_read(error):
    svc = service(caller=True, provider="local")
    svc._runtime_bindings.resolve.side_effect = error
    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id="bot", authenticated_user_id="viewer",
    )
    assert result["data"] == []
    assert result["failed_targets"][0]["reason"] == "caller_unavailable"
    svc._transport.invoke.assert_not_called()
    svc._publish_repo.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_implicit_caller_read_keeps_boundary_logs_and_credentials_private(monkeypatch, fails):
    from agentclaw.community.core.cron.services import cron_targeted_operations, cron_relay

    log = MagicMock()
    monkeypatch.setattr(cron_targeted_operations, "logger", log)
    monkeypatch.setattr(cron_relay, "logger", log)
    svc = service(caller=True, provider="local")
    if fails:
        svc._transport.invoke.side_effect = ValueError("adapter offline")
    else:
        svc._transport.invoke.return_value = {
            "success": True,
            "data": [{"id": "caller-job", "sessionKey": "business-session", "Authorization": "response-secret"}],
        }
    result = await svc.list_all_crons(
        user_id="owner", nick_name="Owner", bot_id="bot", authenticated_user_id="viewer",
    )
    output = "\n".join(call.args[0] % call.args[1:] for call in log.mock_calls if call.args)
    assert "cron.list.caller_selected" in output
    assert "cron.request" in output
    assert "cron.transport.request" in output
    assert "viewer" in output and "owner" in output and "99" in output
    assert "test-secret" not in output and "response-secret" not in output
    if fails:
        assert result["data"] == []
        assert len(result["failed_targets"]) == 1
        assert "cron.transport.failure" in output
        assert "exception_type=ValueError" in output
    else:
        assert result["data"][0]["id"] == "caller-job"
        assert "cron.transport.success" in output and "cron.response" in output
        assert "business-session" in output


@pytest.mark.asyncio
async def test_unsupported_caller_engine_is_not_resolved_or_queried():
    svc = service(caller=True)
    svc._bot_provider.get_bot.return_value["active_engine"] = "hermes"
    result = await svc.list_all_crons(user_id="owner", nick_name="Owner", bot_id="bot")
    assert result["data"] == [] and result["failed_targets"] == []
    svc._runtime_bindings.resolve.assert_not_called()
    svc._transport.invoke.assert_not_called()
