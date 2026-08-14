"""Durable Backend-to-Engine materialization dispatch."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from injector import Injector, inject

from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.protocols.platform import SessionResourceRepositoryProtocol
from agentclaw.community.core.session_resources.types import (
    SessionResourceStatus,
    TransferApiVersion,
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
        repository: SessionResourceRepositoryProtocol,
        token_vault: TokenVault,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._repository = repository
        self._vault = token_vault

    @property
    def task_type(self) -> str:
        return MATERIALIZE_TASK_TYPE

    def handle(self, payload: dict | None) -> TaskOutcome:
        if not isinstance(payload, dict):
            return Retry(error="invalid materialization payload")
        try:
            resource_id = payload.get("resource_id")
            task_id = payload.get("task_id")
            task_version = payload.get("task_version")
            if (
                not isinstance(resource_id, str)
                or not isinstance(task_id, str)
                or not isinstance(task_version, int)
            ):
                return Retry(error="invalid materialization payload")
            record = self._repository.get_by_resource_id(resource_id)
            if (
                record is None
                or record.task_id != task_id
                or record.task_version != task_version
                or record.status is not SessionResourceStatus.DEVICE_SYNCING
            ):
                log.info(
                    "session_resource.materialize.dispatch.stale resource_id=%s task_version=%s",
                    resource_id,
                    task_version,
                )
                return Complete()
            if record.binding_id is None:
                context = self._resolver.resolve_for_bot(
                    record.bot_id,
                    record.owner_id,
                )
            else:
                context = self._resolver.resolve_for_binding(
                    record.binding_id,
                    record.owner_id,
                    bot_id=record.bot_id,
                )
            session_id = None
            if record.transfer_api_version is TransferApiVersion.SESSION_V2:
                if not record.session_key_ciphertext:
                    raise ValueError("session_key_missing")
                session_id = self._vault.decrypt_or_passthrough(
                    record.session_key_ciphertext
                )
                if not session_id:
                    raise ValueError("session_key_missing")
            body = {
                "resource_id": record.resource_id,
                "transfer_id": record.transfer_id,
                "task_id": record.task_id,
                "task_version": record.task_version,
                "scope_key_hash": record.scope_key_hash,
                "session_key_hash": record.session_key_hash,
                "workspace_relative_path": record.workspace_relative_path,
                "filename": record.filename,
                "size_bytes": record.size_bytes,
                "content_hash": record.client_content_hash,
                "uploaded_at": self._format_uploaded_at(record.gmt_create),
                "transfer_api_version": record.transfer_api_version.value,
                "tenant": record.tenant,
                "session_id": session_id,
                "device_path": (
                    record.device_path
                    if record.transfer_api_version
                    is TransferApiVersion.BOT_DEVICE_V1
                    else None
                ),
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
                "session_resource.materialize.dispatch.accepted resource_id=%s task_version=%s provider=%s upload_time_present=%s",
                record.resource_id,
                record.task_version,
                context.provider,
                body["uploaded_at"] is not None,
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

    @staticmethod
    def _format_uploaded_at(uploaded_at: datetime | None) -> str | None:
        if uploaded_at is None:
            return None
        if uploaded_at.tzinfo is None:
            uploaded_at = uploaded_at.replace(tzinfo=UTC)
        return uploaded_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
