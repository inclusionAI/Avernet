"""Native failed-restart recovery, not an observed knowledge completion."""

import json

from sqlalchemy import func

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publish,
    PublishOperationKind,
    PublishOperationModel as Operation,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.utils.env_utils import get_current_env


def _latest(session, publish, kind):
    return (session.query(Operation).filter(
        Operation.publish_id == publish.id,
        Operation.operation_kind == kind,
        Operation.stage == "online",
        Operation.env == publish.env,
    ).order_by(Operation.attempt.desc(), Operation.id.desc())
        .populate_existing().with_for_update().first())


def _require_failed_restart(session, publish, ext):
    operation = _latest(session, publish, PublishOperationKind.RESTART.value)
    if operation is not None and operation.baas_publish_id is not None:
        # Exactly the ledger-preferred workflow used by sync_restart_progress.
        workflow = operation.baas_publish_id
    else:
        # Recreate abandons RESTART without a workflow, then FIRST_RELEASE
        # records the replacement. A marker alone is never sufficient proof.
        restart = ext.get("restart")
        workflow = restart.get("online") if isinstance(restart, dict) else None
        if operation is None or operation.state != PublishOperationState.ABANDONED:
            raise KnowledgeError("PUBLISH_RESTART_RETRY_INVALID")
        operation = _latest(session, publish, PublishOperationKind.FIRST_RELEASE.value)
    if (type(workflow) is not int or workflow <= 0 or operation is None
            or operation.baas_publish_id != workflow
            or operation.state != PublishOperationState.FAILED):
        raise KnowledgeError("PUBLISH_RESTART_RETRY_INVALID")


def restore(session, publish_id, expected_json, replacement_json):
    publish = (session.query(Publish).filter(
        Publish.id == publish_id, Publish.env == get_current_env(),
    ).populate_existing().with_for_update().one_or_none())
    if (publish is None or publish.status != PublishStatus.FAILED
            or publish.ext != expected_json):
        return False
    ext = json.loads(publish.ext) if publish.ext else {}
    if ext.get("source_status") != PublishStatus.SUCCESS:
        raise KnowledgeError("PUBLISH_RESTART_RETRY_INVALID")
    _require_failed_restart(session, publish, ext)
    # Keep native status/ext CAS even after the locking reads. No knowledge
    # selection, summary or operation is modified by this admission.
    return bool(session.query(Publish).filter(
        Publish.id == publish_id, Publish.env == publish.env,
        Publish.status == PublishStatus.FAILED, Publish.ext == expected_json,
    ).update({Publish.status: PublishStatus.SUCCESS, Publish.ext: replacement_json,
              Publish.gmt_modified: func.now()}, synchronize_session=False))
