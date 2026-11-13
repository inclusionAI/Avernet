"""Recover committed audit events and deliver them through the selected Plugin."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from injector import inject

from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.task_queue.types import Complete
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditEventModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()
_TASK_TYPE = "service_bot.publish.audit_delivery"


class ReleaseAuditDelivery:
    """Internal outbox consumer; tenant comes only from trusted persisted events."""

    @inject
    def __init__(self, db: DatabasePlugin, plugin: ServiceBotReleaseAuditPlugin) -> None:
        self._db = db
        self._plugin = plugin

    def pending_event_ids(self) -> list[str]:
        with self._db.orm_session() as session:
            rows = session.query(ReleaseAuditEventModel.event_id).filter(
                ReleaseAuditEventModel.env == get_current_env(),
                ReleaseAuditEventModel.delivered_at.is_(None),
            ).order_by(ReleaseAuditEventModel.attempts, ReleaseAuditEventModel.id).limit(100).all()
            return [row.event_id for row in rows]

    def deliver(self, event_id: str) -> bool:
        with self._db.orm_session() as session:
            row = session.query(ReleaseAuditEventModel).filter(
                ReleaseAuditEventModel.event_id == event_id,
                ReleaseAuditEventModel.env == get_current_env(),
            ).first()
            if row is None:
                return False
            if row.delivered_at is not None:
                return True
            payload = row.payload_json
            tenant = row.avernet_tenant
            session.query(ReleaseAuditEventModel).filter(
                ReleaseAuditEventModel.id == row.id,
            ).update({ReleaseAuditEventModel.attempts: ReleaseAuditEventModel.attempts + 1},
                     synchronize_session=False)
        event = ReleaseAuditEvent.from_json(payload)
        if event.snapshot.tenant_id != tenant or event.snapshot.env != get_current_env():
            raise ValueError("audit event delivery identity mismatch")
        # COSEC: only the internal durable event determines delivery tenant.
        with avernet_tenant_scope(tenant):
            self._plugin.record(event)
        with self._db.orm_session() as session:
            session.query(ReleaseAuditEventModel).filter(
                ReleaseAuditEventModel.event_id == event_id,
                ReleaseAuditEventModel.env == event.snapshot.env,
                ReleaseAuditEventModel.avernet_tenant == tenant,
                ReleaseAuditEventModel.delivered_at.is_(None),
            ).update({ReleaseAuditEventModel.delivered_at: datetime.now(timezone.utc).replace(tzinfo=None)},
                     synchronize_session=False)
        logger.info("release_audit.event_delivered event_id=%s change_id=%s env=%s tenant_id=%s",
                    event_id, event.change_id, event.snapshot.env, tenant)
        return True


class ReleaseAuditDeliveryHandler:
    """At-least-once task; plugin failures use the existing queue retry policy."""

    task_type = _TASK_TYPE

    def __init__(self, delivery: ReleaseAuditDelivery) -> None:
        self._delivery = delivery

    def handle(self, payload: dict[str, Any] | None) -> Complete:
        if not isinstance(payload, dict) or not isinstance(payload.get("event_id"), str):
            raise ValueError("audit event_id required")
        self._delivery.deliver(payload["event_id"])
        return Complete()


class ReleaseAuditDeliveryLifecycle(LifecycleBase):
    """Recover enqueue gaps; event durability does not depend on this process."""

    def __init__(self, *, registry: HandlerRegistry, queue: TaskQueueService,
                 delivery: ReleaseAuditDelivery) -> None:
        self._registry = registry
        self._queue = queue
        self._delivery = delivery
        self._task: asyncio.Task | None = None

    async def bootstrap(self) -> None:
        self._registry.register(ReleaseAuditDeliveryHandler(self._delivery))

    def enqueue_pending(self) -> None:
        for event_id in self._delivery.pending_event_ids():
            self._queue.enqueue(_TASK_TYPE, {"event_id": event_id}, deadline_seconds=86400,
                                idempotency_key=f"release-audit:{event_id}")

    async def startup(self) -> None:
        self._task = asyncio.create_task(self._scan())

    async def _scan(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.enqueue_pending)
            except Exception as exc:
                logger.error("release_audit.scan_failed exception_type=%s message=outbox_scan_failed",
                             type(exc).__name__)
            await asyncio.sleep(8)

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
