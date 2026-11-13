"""Database-backed publication audit projection, shared by local and production."""
from __future__ import annotations

from datetime import datetime
from time import monotonic
from typing import Any

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = get_logger()


class _ProjectionRace(RuntimeError):
    """Another delivery won an insert or revision transition; retry the event."""


@plugin_impl(mode=Mode.PROD, rationale="durable database audit projection using DatabasePlugin")
class DatabaseServiceBotReleaseAudit(ServiceBotReleaseAuditPlugin):
    """Materialize complete snapshots using optimistic revision updates."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def record(self, event: ReleaseAuditEvent) -> None:
        started = monotonic()
        snapshot = event.snapshot
        logger.info(
            "release_audit.delivery.request system=database direction=outbound operation=record "
            "event_id=%s change_id=%s publish_id=%s bot_id=%s env=%s tenant_id=%s "
            "creator_id=%s operator_id=%s desired_stage=%s finish_status=%s revision=%s "
            "version=%s before_stage=%s before_status=%s final_publish_status=%s finished_at=%s",
            event.event_id, event.change_id, snapshot.publish_id, snapshot.bot_id,
            snapshot.env, snapshot.tenant_id, snapshot.creator_id, snapshot.operator_id,
            snapshot.desired_stage, snapshot.finish_status, event.revision,
            snapshot.version, snapshot.before_stage, snapshot.before_status,
            snapshot.final_publish_status,
            snapshot.finished_at.isoformat() if snapshot.finished_at is not None else None,
        )
        try:
            # COSEC: the payload cannot switch a guarded sink into another tenant.
            if snapshot.tenant_id != get_current_avernet_tenant():
                raise ValueError("audit tenant does not match delivery scope")
            try:
                outcome = self._write(event)
            except (IntegrityError, _ProjectionRace):
                # An insert/update collision may have committed the same event.
                # A fresh transaction re-evaluates revision and content.
                outcome = self._write(event)
        except Exception as exc:
            # Driver exception strings can include SQL bind values; log only the
            # exception category and stable operation context, never raw payloads.
            logger.error(
                "release_audit.delivery.failed system=database operation=record event_id=%s "
                "exception_type=%s message=audit_persistence_failed elapsed_ms=%.2f",
                event.event_id, type(exc).__name__, (monotonic() - started) * 1000,
            )
            raise
        logger.info(
            "release_audit.delivery.success system=database operation=record event_id=%s "
            "result=%s elapsed_ms=%.2f", event.event_id, outcome,
            (monotonic() - started) * 1000,
        )

    def _write(self, event: ReleaseAuditEvent) -> str:
        value = event.snapshot
        values: dict[str, Any] = value.to_dict()
        values.pop("tenant_id")
        values["avernet_tenant"] = value.tenant_id
        values["ext"] = value.ext_json
        values["snapshot_json"] = event.to_json()
        for name in ("bot_created_at", "change_created_at", "submitted_at", "finished_at"):
            timestamp: datetime | None = getattr(value, name)
            values[name] = timestamp.replace(tzinfo=None) if timestamp is not None else None
        with self._db.orm_session() as db:
            query = db.query(ReleaseAuditModel).filter(
                ReleaseAuditModel.avernet_tenant == value.tenant_id,
                ReleaseAuditModel.env == value.env,
                ReleaseAuditModel.change_id == value.change_id,
            )
            row = query.first()
            if row is None:
                db.add(ReleaseAuditModel(**values))
                db.flush()
                return "inserted"
            if row.revision >= value.revision:
                if row.revision == value.revision and row.snapshot_json != event.to_json():
                    logger.error("release_audit.delivery.conflict event_id=%s", event.event_id)
                    raise ValueError("audit revision content conflict")
                logger.info("release_audit.delivery.duplicate event_id=%s stored_revision=%s",
                            event.event_id, row.revision)
                return "duplicate"
            affected = query.filter(ReleaseAuditModel.revision == row.revision).update(
                values, synchronize_session=False,
            )
            if affected == 0:
                raise _ProjectionRace("audit revision changed concurrently")
            return "updated"
