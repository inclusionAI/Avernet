"""Observe committed publication changes without affecting native transactions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from sqlalchemy.orm import Session

from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.log import get_logger
from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditSnapshot
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant

_CHANGE_KEY = "_release_audit_change_id"
_TENANT_KEY = "_release_audit_tenant"
logger = get_logger()


def _observe(database, publish_id: int, *, status: str, ext: str | None,
             actor: str | None, source_status: str | None, created: bool = False,
             new_change_id: str | None = None, audit: ServiceBotReleaseAuditPlugin | None) -> str:
    """Observe only after native commit, in a separately owned transaction."""
    started = monotonic()
    outcome = "skipped"
    try:
        with database.transactional_orm_session() as session:
            row = session.query(BotPublishModel).filter(
                BotPublishModel.id == publish_id, BotPublishModel.status == status,
                BotPublishModel.ext.is_(None) if ext is None else BotPublishModel.ext == ext,
            ).first()
            if row is None:
                logger.info("release_audit.stale publish_id=%s status=%s", publish_id, status)
                return "skipped"
            if created:
                if audit is None:
                    raise RuntimeError("publication audit plugin required")
                audit.create(session, _snapshot(session, row, actor, before=None, desired="draft", terminal=True))
                outcome = "success"
            else:
                applied = _record_transition(session, row, source_status=source_status, actor=actor,
                                             new_change_id=new_change_id, audit=audit)
                outcome = "success" if applied else "skipped"
        return outcome
    except Exception as exc:
        logger.warning(
            "release_audit.skipped publish_id=%s status=%s exception_type=%s elapsed_ms=%.2f",
            publish_id, status, type(exc).__name__, (monotonic() - started) * 1000,
        )
        return "failed"


def _start_stage(status: str, source_status: str | None, actor: str | None) -> str | None:
    if actor is not None and source_status != status:
        if status in {"building", "built", "validate_pub"} and source_status in {"draft", "failed"}:
            return "verify"
        if status in {"online_pub", "validating", "success"} and source_status in {"validating", "failed", "success"}:
            return "online"
    return None


def prepare_transition(row, *, source_status: str | None, actor: str | None) -> str | None:
    """Fence a new native attempt even when its later audit INSERT fails."""
    if _start_stage(row.status, source_status, actor) is None:
        return None
    try:
        ext = json.loads(row.ext) if row.ext else {}
        change_id = uuid4().hex
        metadata = {_CHANGE_KEY: change_id, _TENANT_KEY: get_current_avernet_tenant()}
        for key in (*metadata, "_release_audit_submit_failed", "_release_audit_restart_completed",
                    "_release_audit_wait_restart"):
            ext.pop(key, None)
        if source_status == "failed" and row.status == "success":
            metadata["_release_audit_wait_restart"] = change_id
        # Add only audit metadata; preserve all native values and their ordering.
        row.ext = json.dumps({**metadata, **ext}, ensure_ascii=False)
        return change_id
    except Exception as exc:
        logger.warning("release_audit.prepare_failed publish_id=%s exception_type=%s", row.id, type(exc).__name__)
        return None


def _before_stage(status: str | None) -> str | None:
    if status is None:
        return None
    if status in {"draft", "building", "built"}:
        return "draft"
    if status in {"validate_pub", "validating"}:
        return "verify"
    if status in {"online_pub", "success", "upgraded"}:
        return "online"
    return None


def _snapshot(session: Session, row, actor: str, *, before: str | None,
              desired: str, terminal: bool, change_id: str | None = None) -> ReleaseAuditSnapshot:
    bot = session.query(BotModel).filter(BotModel.id == row.source_bot_pk,
                                         BotModel.env == row.env).first()
    if bot is None:
        raise ValueError("audit source Bot not found in current tenant/environment")
    now = datetime.now(timezone.utc)
    return ReleaseAuditSnapshot(
        change_id=change_id or uuid4().hex, source_bot_pk=row.source_bot_pk, bot_id=row.source_bot_id,
        entity_id=bot.entity_id, env=row.env, tenant_id=get_current_avernet_tenant(),
        creator_id=bot.creator_id or None, operator_id=actor, bot_created_at=bot.gmt_create,
        change_created_at=now, submitted_at=None if terminal else now,
        before_stage=_before_stage(before), desired_stage=desired, before_status=before,
        publish_id=row.id, version=row.version, finished_at=now if terminal else None,
        finish_status="succeeded" if terminal else "pending",
        final_publish_status=row.status if terminal else None,
        ext_json=json.dumps({"schema_version": 1, "operation_type": "create_draft" if terminal else "publish"}),
    )


def _log_call(operation: str, publish_id: int, outcome: str, started: float) -> None:
    try:
        logger.info("release_audit.%s.finished publish_id=%s outcome=%s elapsed_ms=%.2f",
                    operation, publish_id, outcome, (monotonic() - started) * 1000)
    except Exception:
        # Optional timing diagnostics must not change publication behavior.
        pass


def record_created(database, publish_id: int, *, ext: str | None, actor: str | None,
                   audit: ServiceBotReleaseAuditPlugin | None) -> None:
    started, outcome = monotonic(), "failed"
    try:
        outcome = "skipped" if actor is None else _observe(
            database, publish_id, status="draft", ext=ext, actor=actor,
            source_status=None, created=True, audit=audit)
    finally:
        _log_call("record_created", publish_id, outcome, started)


def record_transition(database, publish_id: int, *, status: str, ext: str | None,
                      source_status: str | None, actor: str | None, new_change_id: str | None,
                      audit: ServiceBotReleaseAuditPlugin | None) -> None:
    started, outcome = monotonic(), "failed"
    try:
        outcome = _observe(database, publish_id, status=status, ext=ext, source_status=source_status,
                           actor=actor, new_change_id=new_change_id, audit=audit)
    finally:
        _log_call("record_transition", publish_id, outcome, started)


def _record_transition(session: Session, row, *, source_status: str | None,
                       actor: str | None, new_change_id: str | None,
                       audit: ServiceBotReleaseAuditPlugin | None) -> bool:
    status = row.status
    ext = json.loads(row.ext) if row.ext else {}
    start_stage = _start_stage(status, source_status, actor)
    if start_stage:
        if not new_change_id or ext.get(_CHANGE_KEY) != new_change_id:
            return False
        snapshot = _snapshot(session, row, actor, before=source_status, desired=start_stage,
                             terminal=False, change_id=new_change_id)
        if audit is None:
            raise RuntimeError("publication audit plugin required")
        audit.create(session, snapshot)
        return True
    change_id = ext.get(_CHANGE_KEY)
    submission_failed = change_id is not None and ext.get("_release_audit_submit_failed") == change_id
    if not change_id or (not submission_failed and status not in {"validating", "success", "failed", "released"}):
        return False
    if (ext.get("_release_audit_wait_restart") == change_id
            and status == "success" and not submission_failed
            and ext.get("_release_audit_restart_completed") != change_id):
        return False
    expected_stage = None if submission_failed else {"validating": "verify", "success": "online"}.get(status)
    result = "failed" if status == "failed" or submission_failed else "cancelled" if status == "released" else "succeeded"
    if audit is None:
        raise RuntimeError("publication audit plugin required")
    # COSEC: restore only the tenant frozen in this trusted publication's change
    # metadata, never a tenant supplied by a completion request.
    with avernet_tenant_scope(ext.get(_TENANT_KEY, get_current_avernet_tenant())):
        return audit.complete(session, change_id=change_id, publish_id=row.id, env=row.env,
                              expected_stage=expected_stage, finish_status=result,
                              final_publish_status=status, finished_at=datetime.now(timezone.utc))
