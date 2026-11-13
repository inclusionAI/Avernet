"""Transactional publication audit production; no plugin I/O runs in this seam."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditEvent, ReleaseAuditSnapshot
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.release_audit_models import ReleaseAuditEventModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

logger = get_logger()
_CHANGE_KEY = "_release_audit_change_id"


def _append(session: Session, event: ReleaseAuditEvent) -> None:
    value = event.snapshot
    session.add(ReleaseAuditEventModel(
        event_id=event.event_id, change_id=event.change_id, revision=event.revision,
        publish_id=value.publish_id, env=value.env, avernet_tenant=value.tenant_id,
        payload_json=event.to_json(),
    ))
    session.flush()
    logger.info(
        "release_audit.event_staged event_id=%s change_id=%s publish_id=%s "
        "bot_id=%s env=%s desired_stage=%s finish_status=%s operator_id=%s",
        event.event_id, event.change_id, value.publish_id, value.bot_id, value.env,
        value.desired_stage, value.finish_status, value.operator_id,
    )


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
              desired: str, terminal: bool) -> ReleaseAuditSnapshot:
    bot = session.query(BotModel).filter(BotModel.id == row.source_bot_pk,
                                         BotModel.env == row.env).first()
    if bot is None:
        raise ValueError("audit source Bot not found in current tenant/environment")
    now = datetime.now(timezone.utc)
    return ReleaseAuditSnapshot(
        change_id=uuid4().hex, source_bot_pk=row.source_bot_pk, bot_id=row.source_bot_id,
        entity_id=bot.entity_id, env=row.env, tenant_id=get_current_avernet_tenant(),
        creator_id=bot.creator_id or None, operator_id=actor, bot_created_at=bot.gmt_create,
        change_created_at=now, submitted_at=None if terminal else now,
        before_stage=_before_stage(before), desired_stage=desired, before_status=before,
        publish_id=row.id, version=row.version, finished_at=now if terminal else None,
        finish_status="succeeded" if terminal else "pending",
        final_publish_status=row.status if terminal else None,
        revision=2 if terminal else 1,
        ext_json=json.dumps({"schema_version": 1, "operation_type": "create_draft" if terminal else "publish"}),
    )


def record_created(session: Session, row, actor: str | None) -> None:
    """Record an explicitly attributed service publication draft creation."""
    if actor is None or row.status != "draft":
        return
    _append(session, ReleaseAuditEvent(_snapshot(session, row, actor, before=None,
                                                desired="draft", terminal=True)))


def record_transition(session: Session, row, *, source_status: str | None,
                      actor: str | None) -> None:
    """Called only after the publication UPDATE wins, inside its transaction."""
    status = row.status
    ext = json.loads(row.ext) if row.ext else {}
    # A caller-attributed start is a new business change; a background resume is not.
    start_stage = None
    if actor is not None and source_status != status:
        if status in {"building", "built", "validate_pub"} and source_status in {"draft", "failed"}:
            start_stage = "verify"
        elif status in {"online_pub", "validating", "success"} and source_status in {"validating", "failed", "success"}:
            start_stage = "online"
    if start_stage:
        snapshot = _snapshot(session, row, actor, before=source_status, desired=start_stage,
                             terminal=False)
        ext[_CHANGE_KEY] = snapshot.change_id
        ext.pop("_release_audit_submit_failed", None)
        ext.pop("_release_audit_restart_completed", None)
        ext.pop("_release_audit_wait_restart", None)
        if source_status == "failed" and status == "success":
            ext["_release_audit_wait_restart"] = snapshot.change_id
        row.ext = json.dumps(ext, ensure_ascii=False)
        _append(session, ReleaseAuditEvent(snapshot))
        return
    change_id = ext.get(_CHANGE_KEY)
    submission_failed = change_id is not None and ext.get("_release_audit_submit_failed") == change_id
    if not change_id or (not submission_failed and status not in {"validating", "success", "failed", "released"}):
        return
    started = session.query(ReleaseAuditEventModel).filter(
        ReleaseAuditEventModel.change_id == change_id,
        ReleaseAuditEventModel.avernet_tenant == get_current_avernet_tenant(),
        ReleaseAuditEventModel.env == row.env,
        ReleaseAuditEventModel.publish_id == row.id,
        ReleaseAuditEventModel.revision == 1,
    ).first()
    if started is None:
        raise ValueError("audit submission event missing")
    finished = session.query(ReleaseAuditEventModel).filter(
        ReleaseAuditEventModel.event_id == f"{change_id}:2",
    ).first()
    if finished is not None:
        return
    original = ReleaseAuditEvent.from_json(started.payload_json).snapshot
    if (ext.get("_release_audit_wait_restart") == change_id
            and status == "success" and not submission_failed
            and ext.get("_release_audit_restart_completed") != change_id):
        return
    expected_success = "validating" if original.desired_stage == "verify" else "success"
    if not submission_failed and status not in {expected_success, "failed", "released"}:
        return
    result = "failed" if status == "failed" or submission_failed else "cancelled" if status == "released" else "succeeded"
    snapshot = replace(original, revision=2, finished_at=datetime.now(timezone.utc),
                       finish_status=result, final_publish_status=status)
    _append(session, ReleaseAuditEvent(snapshot))
