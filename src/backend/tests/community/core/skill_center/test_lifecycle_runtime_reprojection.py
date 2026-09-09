from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import (
    DeviceAliveEvent,
    RuntimeProjectionRequestedEvent,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.lifecycle_runtime_reprojection import (
    LifecycleRuntimeProjectionTaskHandler,
    LifecycleRuntimeProjectionWakeup,
)
from agentclaw.community.core.skill_center.services.mcp_runtime_probe import (
    CurrentMcpRuntimeProbeService,
    McpRuntimeReadinessResult,
    McpRuntimeReadinessStatus,
)
from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
    EngineRuntimeProjectionRegistry,
    RuntimeProjectionDeliveryShape,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Retry
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
)


def _binding(**overrides: object) -> DeviceBindingRecord:
    values: dict[str, object] = {
        "id": 42,
        "entity_id": "owner-1",
        "entity_type": "staff",
        "device_id": "device-1",
        "device_provider": "arca",
        "env": "pre",
        "device_props": {"sandbox_id": "sandbox-1"},
        "status": "PENDING",
        "apply_reason": None,
        "applied_by": "owner-1",
        "release_reason": None,
        "released_by": None,
        "released_at": None,
        "last_alive_at": None,
        "gmt_create": None,
        "gmt_modified": None,
    }
    values.update(overrides)
    return DeviceBindingRecord(**values)  # type: ignore[arg-type]


def _bot(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "entity_id": "owner-1",
        "binding_id": 42,
        "env": "pre",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }
    values.update(overrides)
    return values


def _payload(component: str = "skills", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "env": "pre",
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "binding_id": 42,
        "device_id": "device-1",
        "sandbox_id": "sandbox-1",
        "component": component,
        "source": "device_alive",
        "signal_identity": {"binding_id": 42},
    }
    values.update(overrides)
    return values


def _registry() -> EngineRuntimeProjectionRegistry:
    return EngineRuntimeProjectionRegistry(
        default=MagicMock(),
        by_engine={"teclaw": MagicMock()},
        delivery_shape_by_engine={
            "teclaw": RuntimeProjectionDeliveryShape.WHOLE_ARTIFACT,
        },
    )


def test_registry_exposes_lifecycle_delivery_shape() -> None:
    registry = _registry()

    assert (
        registry.delivery_shape_for_engine("openclaw")
        is RuntimeProjectionDeliveryShape.PER_DOMAIN
    )
    assert (
        registry.delivery_shape_for_engine("teclaw")
        is RuntimeProjectionDeliveryShape.WHOLE_ARTIFACT
    )


def test_registry_rejects_projection_without_delivery_shape() -> None:
    with pytest.raises(ValueError, match="missing_shapes=.*teclaw"):
        EngineRuntimeProjectionRegistry(
            default=MagicMock(),
            by_engine={"teclaw": MagicMock()},
        )


def test_device_alive_enqueues_independent_skill_and_mcp_tasks() -> None:
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _binding()
    bot_repo = MagicMock()
    bot_repo.get_by_binding_id.return_value = _bot()
    queue = MagicMock()
    wakeup = LifecycleRuntimeProjectionWakeup(
        binding_repository=binding_repo,
        bot_repository=bot_repo,
        task_queue_service=queue,
        projection_registry=_registry(),
    )

    wakeup.handle(
        DeviceAliveEvent(
            device_id="device-1",
            binding_id=42,
            entity_id="owner-1",
            entity_type="staff",
            device_provider="arca",
            sandbox_id="sandbox-1",
        )
    )

    assert queue.enqueue.call_count == 2
    components = {call.args[1]["component"] for call in queue.enqueue.call_args_list}
    assert components == {"skills", "mcp"}
    keys = {call.kwargs["idempotency_key"] for call in queue.enqueue.call_args_list}
    assert len(keys) == 2
    assert any(key.startswith("runtime-projection:pre:42:skills:") for key in keys)
    assert any(key.startswith("runtime-projection:pre:42:mcp:") for key in keys)


def test_new_runtime_generation_is_not_swallowed_by_old_live_key() -> None:
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _binding(status="ACTIVE")
    bot_repo = MagicMock()
    bot_repo.get_by_binding_id.return_value = _bot()
    queue = MagicMock()
    wakeup = LifecycleRuntimeProjectionWakeup(
        binding_repository=binding_repo,
        bot_repository=bot_repo,
        task_queue_service=queue,
        projection_registry=_registry(),
    )

    for generation in ("publish-1", "publish-2"):
        wakeup.handle(
            RuntimeProjectionRequestedEvent(
                device_id="device-1",
                binding_id=42,
                entity_id="owner-1",
                entity_type="staff",
                device_provider="arca",
                sandbox_id="sandbox-1",
                source="baas_restart",
                runtime_generation=generation,
            )
        )

    skill_keys = [
        call.kwargs["idempotency_key"]
        for call in queue.enqueue.call_args_list
        if call.args[1]["component"] == "skills"
    ]
    assert len(skill_keys) == 2
    assert skill_keys[0] != skill_keys[1]


def test_whole_artifact_runtime_is_not_enqueued() -> None:
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _binding()
    bot_repo = MagicMock()
    bot_repo.get_by_binding_id.return_value = _bot(active_engine="teclaw")
    queue = MagicMock()
    wakeup = LifecycleRuntimeProjectionWakeup(
        binding_repository=binding_repo,
        bot_repository=bot_repo,
        task_queue_service=queue,
        projection_registry=_registry(),
    )

    wakeup.handle(
        DeviceAliveEvent(
            device_id="device-1",
            binding_id=42,
            entity_id="owner-1",
            entity_type="staff",
            device_provider="teclaw",
            sandbox_id="sandbox-1",
        )
    )

    queue.enqueue.assert_not_called()


def test_wakeup_bootstrap_registers_required_events_and_handler() -> None:
    reset_event_bus()
    registry = HandlerRegistry()
    handler = MagicMock()
    handler.task_type = "runtime_projection.reconcile"
    wakeup = LifecycleRuntimeProjectionWakeup(
        binding_repository=MagicMock(),
        bot_repository=MagicMock(),
        task_queue_service=MagicMock(),
        projection_registry=_registry(),
        registry=registry,
        task_handler=handler,
    )

    asyncio.run(wakeup.bootstrap())

    bus = get_event_bus()
    assert registry.get("runtime_projection.reconcile") is handler
    assert bus.is_subscribed(DeviceAliveEvent, wakeup.handle)
    assert (DeviceAliveEvent, wakeup.handle) in bus._required_handlers
    reset_event_bus()


def _handler(
    *,
    binding: DeviceBindingRecord | None = None,
    bot: dict[str, object] | None = None,
    projection_result: RuntimeProjectionResult | None = None,
    readiness: McpRuntimeReadinessResult | None = None,
):
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = (
        binding if binding is not None else _binding(status="ACTIVE")
    )
    bot_repo = MagicMock()
    bot_repo.get_by_binding_id.return_value = bot if bot is not None else _bot()
    projector = MagicMock()
    projector.project = AsyncMock(
        return_value=projection_result or RuntimeProjectionResult.converged()
    )
    projector.project_mcp_and_cli = AsyncMock(
        return_value=projection_result or RuntimeProjectionResult.converged()
    )
    probe = MagicMock()
    probe.probe_binding = AsyncMock(
        return_value=readiness or McpRuntimeReadinessResult.ready(engine="openclaw")
    )
    handler = LifecycleRuntimeProjectionTaskHandler(
        binding_repository=binding_repo,
        bot_repository=bot_repo,
        projection_registry=_registry(),
        projector=projector,
        mcp_probe=probe,
    )
    return handler, projector, probe


def test_stale_sandbox_completes_without_touching_runtime() -> None:
    handler, projector, probe = _handler(
        binding=_binding(status="ACTIVE", device_props={"sandbox_id": "new-sandbox"})
    )

    assert isinstance(handler.handle(_payload()), Complete)
    projector.project.assert_not_awaited()
    projector.project_mcp_and_cli.assert_not_awaited()
    probe.probe_binding.assert_not_awaited()


def test_skills_task_projects_only_skills() -> None:
    handler, projector, probe = _handler()

    assert isinstance(handler.handle(_payload("skills")), Complete)
    projector.project.assert_awaited_once_with(
        bot_id="bot-1",
        owner_id="owner-1",
        scope=ProjectionScope(skills=True),
    )
    projector.project_mcp_and_cli.assert_not_awaited()
    probe.probe_binding.assert_not_awaited()


def test_pool_owned_skill_component_wakes_pool_task_instead_of_legacy_write() -> None:
    handler, projector, _ = _handler()
    authority = MagicMock(return_value="transition")
    pool_wakeup = MagicMock()
    handler._skill_projection_authority = authority
    handler._skills_pool_reconcile_wakeup = pool_wakeup

    assert isinstance(handler.handle(_payload("skills")), Complete)

    projector.project.assert_not_awaited()
    pool_wakeup.assert_called_once()


def test_skill_task_rewakes_pool_if_cutover_starts_during_legacy_write() -> None:
    handler, projector, _ = _handler()
    authority = MagicMock(side_effect=["legacy", "transition"])
    pool_wakeup = MagicMock()
    handler._skill_projection_authority = authority
    handler._skills_pool_reconcile_wakeup = pool_wakeup

    assert isinstance(handler.handle(_payload("skills")), Complete)

    projector.project.assert_awaited_once()
    pool_wakeup.assert_called_once()


def test_mcp_task_retries_without_projection_when_readiness_is_transient() -> None:
    handler, projector, probe = _handler(
        readiness=McpRuntimeReadinessResult(
            status=McpRuntimeReadinessStatus.TRANSIENT_ERROR,
            engine="openclaw",
            reason="runtime_starting",
            retryable=True,
        )
    )

    outcome = handler.handle(_payload("mcp"))

    assert isinstance(outcome, Retry)
    probe.probe_binding.assert_awaited_once()
    projector.project_mcp_and_cli.assert_not_awaited()


def test_mcp_task_fails_when_runtime_is_not_capable() -> None:
    handler, projector, _ = _handler(
        readiness=McpRuntimeReadinessResult(
            status=McpRuntimeReadinessStatus.NOT_CAPABLE,
            engine="moltis",
            reason="mcp_projection_not_supported",
            retryable=False,
        )
    )

    assert isinstance(handler.handle(_payload("mcp")), Fail)
    projector.project_mcp_and_cli.assert_not_awaited()


def test_mcp_task_re_fences_after_readiness_before_write() -> None:
    handler, projector, _ = _handler()
    handler._bindings.get_by_id.side_effect = [
        _binding(status="ACTIVE"),
        _binding(status="ACTIVE", device_props={"sandbox_id": "sandbox-2"}),
    ]

    assert isinstance(handler.handle(_payload("mcp")), Complete)
    projector.project_mcp_and_cli.assert_not_awaited()


def test_mcp_readiness_uses_physical_aicoding_engine_for_coding_template() -> None:
    handler, _, probe = _handler(
        bot=_bot(
            active_engine="claude_code",
            template_type="applicationCoding",
        ),
        readiness=McpRuntimeReadinessResult.ready(engine="aicoding"),
    )

    assert isinstance(handler.handle(_payload("mcp")), Complete)
    probe.probe_binding.assert_awaited_once_with(
        binding_id=42,
        bot_id="bot-1",
        owner_id="owner-1",
        engine="aicoding",
    )


def test_mcp_readiness_normalizes_logical_engine_alias() -> None:
    handler, _, probe = _handler(
        bot=_bot(active_engine="claude-code"),
        readiness=McpRuntimeReadinessResult.ready(engine="claude_code"),
    )

    assert isinstance(handler.handle(_payload("mcp")), Complete)
    assert probe.probe_binding.await_args.kwargs["engine"] == "claude_code"


def test_projection_issue_retryability_controls_task_outcome() -> None:
    retryable = RuntimeProjectionResult.pending(code="TEMP", reason="later")
    handler, _, _ = _handler(projection_result=retryable)
    assert isinstance(handler.handle(_payload()), Retry)

    permanent = RuntimeProjectionResult(
        status=RuntimeProjectionStatus.DEGRADED,
        issues=(
            RuntimeProjectionIssue(
                resource_type="RUNTIME",
                code="INVALID",
                reason="broken",
                status=RuntimeProjectionStatus.DEGRADED,
                retryable=False,
            ),
        ),
    )
    handler, _, _ = _handler(projection_result=permanent)
    assert isinstance(handler.handle(_payload()), Fail)


def test_invalid_component_fails_without_runtime_access() -> None:
    handler, projector, probe = _handler()

    assert isinstance(handler.handle(_payload("cli")), Fail)
    projector.project.assert_not_awaited()
    probe.probe_binding.assert_not_awaited()


def test_backend_probe_parses_ready_response_for_exact_binding() -> None:
    resolver = MagicMock()
    resolver.resolve_for_binding_invoke.return_value.conn_info = {"binding_id": 42}
    transport = MagicMock()
    transport.invoke = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "status": "READY",
                "engine": "openclaw",
                "reason": None,
                "retryable": False,
            },
        }
    )
    service = CurrentMcpRuntimeProbeService(
        resolver=resolver,
        adapter_transport=transport,
    )

    result = asyncio.run(
        service.probe_binding(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            engine="openclaw",
        )
    )

    assert result.status is McpRuntimeReadinessStatus.READY
    resolver.resolve_for_binding_invoke.assert_called_once_with(
        42,
        "owner-1",
        bot_id="bot-1",
    )
    transport.invoke.assert_awaited_once_with(
        {"binding_id": 42},
        "GET",
        "/api/mcp/readiness",
        timeout=8.0,
    )


def test_backend_probe_fails_closed_for_old_image_and_invalid_response() -> None:
    resolver = MagicMock()
    resolver.resolve_for_binding_invoke.return_value.conn_info = {}
    transport = MagicMock()
    transport.invoke = AsyncMock(
        side_effect=DeviceAdapterEndpointNotFoundError("missing")
    )
    service = CurrentMcpRuntimeProbeService(
        resolver=resolver,
        adapter_transport=transport,
    )
    result = asyncio.run(
        service.probe_binding(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            engine="openclaw",
        )
    )
    assert result.status is McpRuntimeReadinessStatus.NOT_CAPABLE

    transport.invoke = AsyncMock(return_value={"success": True, "data": {}})
    result = asyncio.run(
        service.probe_binding(
            binding_id=42,
            bot_id="bot-1",
            owner_id="owner-1",
            engine="openclaw",
        )
    )
    assert result.status is McpRuntimeReadinessStatus.INVALID
