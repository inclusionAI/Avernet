"""Direct single-table audit writes inside the publication transaction."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from time import monotonic
from typing import Any

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditSnapshot
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = get_logger()


@plugin_impl(mode=Mode.PROD, rationale="single-table audit using the publication DB transaction")
class DatabaseServiceBotReleaseAudit(ServiceBotReleaseAuditPlugin):
    """The caller owns the session; this Plugin never opens or commits one."""

    def create(self, session: Any, snapshot: ReleaseAuditSnapshot) -> None:
        started = monotonic()
        logger.info(
            "release_audit.create.request change_id=%s publish_id=%s bot_id=%s env=%s "
            "creator_id=%s operator_id=%s version=%s desired_stage=%s finish_status=%s",
            snapshot.change_id, snapshot.publish_id, snapshot.bot_id, snapshot.env,
            snapshot.creator_id, snapshot.operator_id, snapshot.version,
            snapshot.desired_stage, snapshot.finish_status,
        )
        try:
            # COSEC: a submission cannot select a tenant different from its caller.
            if snapshot.tenant_id != get_current_avernet_tenant():
                raise ValueError("audit tenant does not match submission scope")
            values = asdict(snapshot)
            values["avernet_tenant"] = values.pop("tenant_id")
            values["ext"] = values.pop("ext_json")
            for name in ("bot_created_at", "change_created_at", "submitted_at", "finished_at"):
                value = values[name]
                values[name] = value.replace(tzinfo=None) if value is not None else None
            session.add(ReleaseAuditModel(**values))
            session.flush()
        except Exception as exc:
            logger.error("release_audit.create.failed change_id=%s exception_type=%s elapsed_ms=%.2f",
                         snapshot.change_id, type(exc).__name__, (monotonic() - started) * 1000)
            raise
        logger.info("release_audit.create.success change_id=%s elapsed_ms=%.2f",
                    snapshot.change_id, (monotonic() - started) * 1000)

    def complete(
        self, session: Any, *, change_id: str, publish_id: int, env: str,
        expected_stage: str | None, finish_status: str,
        final_publish_status: str, finished_at: datetime,
    ) -> bool:
        started = monotonic()
        logger.info(
            "release_audit.complete.request change_id=%s publish_id=%s env=%s tenant_id=%s "
            "expected_stage=%s finish_status=%s final_publish_status=%s finished_at=%s",
            change_id, publish_id, env, get_current_avernet_tenant(), expected_stage,
            finish_status, final_publish_status, finished_at.isoformat(),
        )
        try:
            query = session.query(ReleaseAuditModel).filter(
                ReleaseAuditModel.change_id == change_id,
                ReleaseAuditModel.publish_id == publish_id,
                ReleaseAuditModel.env == env,
            )
            if query.first() is None:
                raise ValueError("audit change missing")
            query = query.filter(ReleaseAuditModel.finish_status == "pending")
            if expected_stage is not None:
                query = query.filter(ReleaseAuditModel.desired_stage == expected_stage)
            changed = query.update({
                ReleaseAuditModel.finish_status: finish_status,
                ReleaseAuditModel.final_publish_status: final_publish_status,
                ReleaseAuditModel.finished_at: finished_at.replace(tzinfo=None),
            }, synchronize_session=False)
        except Exception as exc:
            logger.error("release_audit.complete.failed change_id=%s exception_type=%s elapsed_ms=%.2f",
                         change_id, type(exc).__name__, (monotonic() - started) * 1000)
            raise
        logger.info("release_audit.complete.success change_id=%s updated=%s elapsed_ms=%.2f",
                    change_id, bool(changed), (monotonic() - started) * 1000)
        return bool(changed)
