"""Durable, binding-fenced lifecycle reprojection for per-domain runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from agentclaw.community.core.events.types import (
    DeviceAliveEvent,
    RuntimeProjectionRequestedEvent,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.mcp_runtime_probe import (
    CurrentMcpRuntimeProbeService,
    McpRuntimeReadinessStatus,
)
from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
    EngineRuntimeProjectionRegistry,
    RuntimeProjectionDeliveryShape,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Retry,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase

LIFECYCLE_RUNTIME_PROJECTION_TASK = "runtime_projection.reconcile"
LIFECYCLE_RUNTIME_PROJECTION_DEADLINE_SECONDS = 10 * 60


class LifecycleProjectionComponent(StrEnum):
    SKILLS = "skills"
    MCP = "mcp"


def build_lifecycle_projection_key(
    *, env: str, binding_id: int, component: LifecycleProjectionComponent
) -> str:
    return f"runtime-projection:{env}:{binding_id}:{component.value}"


class LifecycleRuntimeProjectionTaskHandler:
    def __init__(
        self,
        *,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        projection_registry: EngineRuntimeProjectionRegistry,
        projector: BotRuntimeProjectorProtocol,
        mcp_probe: CurrentMcpRuntimeProbeService,
        skill_projection_owned_elsewhere: (
            Callable[[dict[str, Any]], bool] | None
        ) = None,
    ) -> None:
        self._bindings = binding_repository
        self._bots = bot_repository
        self._projection_registry = projection_registry
        self._projector = projector
        self._mcp_probe = mcp_probe
        self._skill_projection_owned_elsewhere = skill_projection_owned_elsewhere

    @property
    def task_type(self) -> str:
        return LIFECYCLE_RUNTIME_PROJECTION_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            work = self._parse_payload(payload)
        except ValueError as error:
            return Fail(f"invalid lifecycle runtime projection payload: {error}")

        binding = self._bindings.get_by_id(work["binding_id"])
        bot = self._bots.get_by_binding_id(work["binding_id"])
        if binding is None or bot is None:
            return Complete()
        if not self._is_current(work=work, binding=binding, bot=bot):
            return Complete()

        engine = str(bot.get("active_engine") or "openclaw")
        if (
            self._projection_registry.delivery_shape_for_engine(engine)
            is not RuntimeProjectionDeliveryShape.PER_DOMAIN
        ):
            return Complete()

        return asyncio.run(self._run(work=work, engine=engine))

    async def _run(self, *, work: dict[str, Any], engine: str) -> TaskOutcome:
        component = work["component"]
        if component is LifecycleProjectionComponent.SKILLS:
            bot = self._bots.get_by_binding_id(work["binding_id"])
            if (
                bot is not None
                and self._skill_projection_owned_elsewhere is not None
                and self._skill_projection_owned_elsewhere(bot)
            ):
                return Complete()
            result = await self._projector.project(
                bot_id=work["bot_id"],
                owner_id=work["owner_id"],
                scope=ProjectionScope(skills=True),
            )
            return self._projection_outcome(result)

        readiness = await self._mcp_probe.probe_binding(
            binding_id=work["binding_id"],
            bot_id=work["bot_id"],
            owner_id=work["owner_id"],
            engine=engine,
        )
        if readiness.status is McpRuntimeReadinessStatus.TRANSIENT_ERROR:
            return Retry(readiness.reason)
        if readiness.status is not McpRuntimeReadinessStatus.READY:
            return Fail(
                f"MCP runtime readiness {readiness.status.value}: "
                f"{readiness.reason or 'no reason'}"
            )
        binding = self._bindings.get_by_id(work["binding_id"])
        bot = self._bots.get_by_binding_id(work["binding_id"])
        if binding is None or bot is None or not self._is_current(
            work=work, binding=binding, bot=bot
        ):
            return Complete()
        result = await self._projector.project_mcp_and_cli(
            bot_id=work["bot_id"],
            owner_id=work["owner_id"],
            scope=ProjectionScope(mcp=True, claim_all_mcp=True),
        )
        return self._projection_outcome(result)

    @staticmethod
    def _projection_outcome(result: RuntimeProjectionResult) -> TaskOutcome:
        if result.status in {
            RuntimeProjectionStatus.CONVERGED,
            RuntimeProjectionStatus.SKIPPED,
        }:
            return Complete()
        if result.issues and all(issue.retryable for issue in result.issues):
            return Retry(
                "; ".join(f"{issue.code}: {issue.reason}" for issue in result.issues)
            )
        return Fail(
            "; ".join(f"{issue.code}: {issue.reason}" for issue in result.issues)
            or f"runtime projection {result.status.value} without actionable issue"
        )

    @staticmethod
    def _is_current(*, work: dict[str, Any], binding: Any, bot: dict) -> bool:
        current_sandbox = (binding.device_props or {}).get("sandbox_id")
        return (
            binding.status in {"PENDING", "ACTIVE"}
            and binding.env == work["env"]
            and binding.device_id == work["device_id"]
            and current_sandbox == work["sandbox_id"]
            and bot.get("bot_id") == work["bot_id"]
            and str(bot.get("owner_id")) == work["owner_id"]
            and str(bot.get("binding_id")) == str(work["binding_id"])
        )

    @staticmethod
    def _parse_payload(payload: dict | None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        parsed: dict[str, Any] = {}
        for key in ("env", "bot_id", "owner_id", "device_id", "source"):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string")
            parsed[key] = value
        binding_id = payload.get("binding_id")
        if not isinstance(binding_id, int) or isinstance(binding_id, bool):
            raise ValueError("binding_id must be an integer")
        parsed["binding_id"] = binding_id
        sandbox_id = payload.get("sandbox_id")
        if sandbox_id is not None and not isinstance(sandbox_id, str):
            raise ValueError("sandbox_id must be a string or null")
        parsed["sandbox_id"] = sandbox_id
        try:
            parsed["component"] = LifecycleProjectionComponent(payload.get("component"))
        except (TypeError, ValueError) as error:
            raise ValueError("component must be skills or mcp") from error
        signal_identity = payload.get("signal_identity")
        if not isinstance(signal_identity, dict):
            raise ValueError("signal_identity must be an object")
        parsed["signal_identity"] = dict(signal_identity)
        return parsed


class LifecycleRuntimeProjectionWakeup(LifecycleBase):
    """Persist lifecycle signals before the publisher advances its state."""

    def __init__(
        self,
        *,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        task_queue_service: TaskQueueService,
        projection_registry: EngineRuntimeProjectionRegistry,
        registry: HandlerRegistry | None = None,
        task_handler: LifecycleRuntimeProjectionTaskHandler | None = None,
    ) -> None:
        self._bindings = binding_repository
        self._bots = bot_repository
        self._queue = task_queue_service
        self._projection_registry = projection_registry
        self._registry = registry
        self._task_handler = task_handler

    async def bootstrap(self) -> None:
        if self._registry is not None and self._task_handler is not None:
            registered = self._registry.get(self._task_handler.task_type)
            if registered is None:
                self._registry.register(self._task_handler, wake_on_enqueue=True)
            elif registered is not self._task_handler:
                raise ValueError(
                    f"task_type {self._task_handler.task_type!r} is already "
                    "registered by another handler"
                )
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        for event_type in (DeviceAliveEvent, RuntimeProjectionRequestedEvent):
            if not bus.is_subscribed(event_type, self.handle):
                bus.subscribe(event_type, self.handle, required=True)

    def handle(self, event: DeviceAliveEvent | RuntimeProjectionRequestedEvent) -> None:
        binding = self._bindings.get_by_id(event.binding_id)
        bot = self._bots.get_by_binding_id(event.binding_id)
        if binding is None or bot is None:
            return
        if not self._matches_event(binding=binding, event=event):
            return
        engine = str(bot.get("active_engine") or "openclaw")
        if (
            self._projection_registry.delivery_shape_for_engine(engine)
            is not RuntimeProjectionDeliveryShape.PER_DOMAIN
        ):
            return
        bot_id = bot.get("bot_id")
        owner_id = bot.get("owner_id")
        if not isinstance(bot_id, str) or not bot_id or owner_id is None:
            return
        source = "device_alive" if isinstance(event, DeviceAliveEvent) else event.source
        for component in LifecycleProjectionComponent:
            payload = {
                "env": binding.env,
                "bot_id": bot_id,
                "owner_id": str(owner_id),
                "binding_id": binding.id,
                "device_id": binding.device_id,
                "sandbox_id": (binding.device_props or {}).get("sandbox_id"),
                "component": component.value,
                "source": source,
                "signal_identity": {
                    "binding_id": event.binding_id,
                    "device_id": event.device_id,
                    "sandbox_id": event.sandbox_id,
                },
            }
            self._queue.enqueue(
                LIFECYCLE_RUNTIME_PROJECTION_TASK,
                payload,
                deadline_seconds=LIFECYCLE_RUNTIME_PROJECTION_DEADLINE_SECONDS,
                idempotency_key=build_lifecycle_projection_key(
                    env=binding.env,
                    binding_id=binding.id,
                    component=component,
                ),
            )

    @staticmethod
    def _matches_event(*, binding: Any, event: Any) -> bool:
        current_sandbox = (binding.device_props or {}).get("sandbox_id")
        return (
            binding.device_id == event.device_id
            and binding.entity_id == event.entity_id
            and binding.entity_type == event.entity_type
            and binding.device_provider == event.device_provider
            and current_sandbox == event.sandbox_id
        )


__all__ = [
    "LIFECYCLE_RUNTIME_PROJECTION_DEADLINE_SECONDS",
    "LIFECYCLE_RUNTIME_PROJECTION_TASK",
    "LifecycleProjectionComponent",
    "LifecycleRuntimeProjectionTaskHandler",
    "LifecycleRuntimeProjectionWakeup",
    "build_lifecycle_projection_key",
]
