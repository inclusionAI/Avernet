"""Create or complete the active audit row within the publication transaction."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from agentclaw.community.kernel.service_bot_release_audit import ReleaseAuditSnapshot
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugin_api.service_bot_release_audit import ServiceBotReleaseAuditPlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant

_CHANGE_KEY = "_release_audit_change_id"
_TENANT_KEY = "_release_audit_tenant"


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
        ext_json=json.dumps({"schema_version": 1, "operation_type": "create_draft" if terminal else "publish"}),
    )


def record_created(session: Session, row, actor: str | None, audit: ServiceBotReleaseAuditPlugin | None) -> None:
    """Record an explicitly attributed service publication draft creation."""
    if actor is None or row.status != "draft":
        return
    if audit is None:
        raise RuntimeError("publication audit plugin required")
    audit.create(session, _snapshot(session, row, actor, before=None, desired="draft", terminal=True))


def record_transition(session: Session, row, *, source_status: str | None,
                      actor: str | None, audit: ServiceBotReleaseAuditPlugin | None) -> None:
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
        ext[_TENANT_KEY] = snapshot.tenant_id
        ext.pop("_release_audit_submit_failed", None)
        ext.pop("_release_audit_restart_completed", None)
        ext.pop("_release_audit_wait_restart", None)
        if source_status == "failed" and status == "success":
            ext["_release_audit_wait_restart"] = snapshot.change_id
        # Match ConfigArtifactOffloader.prepare: its artifact representation
        # always serializes last. Preserve the raw offload marker and ordering,
        # otherwise the next exact serialized-ext CAS rejects an unchanged row.
        for artifact_key in ("config_artifact", "config_artifact_oss"):
            if artifact_key in ext:
                ext[artifact_key] = ext.pop(artifact_key)
        row.ext = json.dumps(ext, ensure_ascii=False)
        if audit is None:
            raise RuntimeError("publication audit plugin required")
        audit.create(session, snapshot)
        return
    change_id = ext.get(_CHANGE_KEY)
    submission_failed = change_id is not None and ext.get("_release_audit_submit_failed") == change_id
    if not change_id or (not submission_failed and status not in {"validating", "success", "failed", "released"}):
        return
    if (ext.get("_release_audit_wait_restart") == change_id
            and status == "success" and not submission_failed
            and ext.get("_release_audit_restart_completed") != change_id):
        return
    expected_stage = None if submission_failed else {"validating": "verify", "success": "online"}.get(status)
    result = "failed" if status == "failed" or submission_failed else "cancelled" if status == "released" else "succeeded"
    if audit is None:
        raise RuntimeError("publication audit plugin required")
    # COSEC: restore only the tenant frozen in this trusted publication's change
    # metadata, never a tenant supplied by a completion request.
    with avernet_tenant_scope(ext.get(_TENANT_KEY, get_current_avernet_tenant())):
        audit.complete(session, change_id=change_id, publish_id=row.id, env=row.env,
                       expected_stage=expected_stage, finish_status=result,
                       final_publish_status=status, finished_at=datetime.now(timezone.utc))
