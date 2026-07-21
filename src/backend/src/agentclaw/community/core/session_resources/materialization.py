"""Durable Backend-to-Engine materialization dispatch."""
from __future__ import annotations

import asyncio
import logging

from injector import Injector, inject

from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Retry, TaskOutcome
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)

log = logging.getLogger("session_resource.materialization")
MATERIALIZE_TASK_TYPE = "session_resource.materialize"


class SessionResourceMaterializeHandler:
    @inject
    def __init__(
        self,
        resolver: DeviceContextResolver,
        transport: DeviceAdapterTransport,
    ) -> None:
        self._resolver = resolver
        self._transport = transport

    @property
    def task_type(self) -> str:
        return MATERIALIZE_TASK_TYPE

    def handle(self, payload: dict | None) -> TaskOutcome:
        if not isinstance(payload, dict):
            return Retry(error="invalid materialization payload")
        try:
            context = self._resolver.resolve_for_bot(
                str(payload["bot_id"]),
                str(payload["owner_id"]),
            )
            body = {
                key: payload.get(key)
                for key in (
                    "resource_id",
                    "transfer_id",
                    "task_id",
                    "task_version",
                    "scope_key_hash",
                    "session_key_hash",
                    "device_path",
                    "filename",
                    "size_bytes",
                    "content_hash",
                )
            }
            response = asyncio.run(
                self._transport.invoke(
                    context.conn_info,
                    "POST",
                    "/api/resource-materializations",
                    body=body,
                )
            )
            if not isinstance(response, dict) or not response.get("accepted"):
                raise RuntimeError("Engine did not accept materialization")
            log.info(
                "session_resource.materialize.dispatch.accepted resource_id=%s task_version=%s provider=%s",
                payload.get("resource_id"),
                payload.get("task_version"),
                context.provider,
            )
            return Complete()
        except Exception as exc:
            log.warning(
                "session_resource.materialize.dispatch.retry resource_id=%s task_version=%s error_type=%s",
                payload.get("resource_id"),
                payload.get("task_version"),
                type(exc).__name__,
            )
            return Retry(error=type(exc).__name__)


class SessionResourceTaskLifecycle(LifecycleBase):
    """Register the task handler before an enabled worker starts."""

    @inject
    def __init__(
        self,
        registry: HandlerRegistry,
        config: TaskQueueWorkerConfig,
        injector: Injector,
    ) -> None:
        self._registry = registry
        self._config = config
        self._injector = injector

    async def bootstrap(self) -> None:
        if not self._config.enabled:
            log.info("session_resource.materialize.handler disabled_with_worker=true")
            return
        handler = self._injector.get(SessionResourceMaterializeHandler)
        self._registry.register(handler)
        log.info("session_resource.materialize.handler registered=true")
