"""Strict business codecs; no control JSON or synthetic execution records."""

from dataclasses import asdict
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    shape,
    text,
)
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from ..product_profile import ProductProfile, ProductProfileStatus
from ..profile_record import ProductProfileRecord
from ..product_profile_service.profile_config_yaml import decode_config
from ..product_profile_service.snapshot_codec import canonical, load

MAX_MEDIUMTEXT_BYTES = 16777215


def audit_identity(context) -> str:
    principal = PrincipalRef.from_dict(context.actor.to_dict())
    if ":" in principal.principal_type:
        raise Invalid("audit principal type cannot contain a colon")
    result = f"{principal.principal_type}:{principal.principal_id}"
    if len(result) > 1024:
        raise Invalid("audit identity exceeds 1024 characters")
    return result


def status(value: str) -> StatusEnum:
    try:
        return StatusEnum(value)
    except (ValueError, TypeError):
        raise Invalid("invalid stored business status") from None


def profile_from_row(row) -> ProductProfileRecord:
    composition, stages = decode_config(row.config)
    try:
        lifecycle = ProductProfileStatus(row.status)
    except (ValueError, TypeError):
        raise Invalid("invalid stored Profile lifecycle status") from None
    return ProductProfileRecord(
        ProductProfile(row.id, row.name, composition, stages),
        row.description,
        lifecycle,
        row.creator,
        row.modifer,
    )


def encode_evidence(
    acceptances: list[AcceptanceResult], artifacts: list[Artifact], error_msg: str
):
    if type(acceptances) is not list or any(
        type(a) is not AcceptanceResult for a in acceptances
    ):
        raise Invalid("acceptances must be typed results")
    if type(artifacts) is not list or any(type(a) is not Artifact for a in artifacts):
        raise Invalid("artifacts must be typed values")
    encoded = canonical([asdict(a) for a in acceptances])
    if len(encoded) > 8192:
        raise Invalid("acceptances JSON exceeds 8192 characters")
    text(error_msg, empty=True, maximum=65535)
    artifact_json = canonical([a.to_dict() for a in artifacts])
    text(artifact_json, maximum=MAX_MEDIUMTEXT_BYTES)
    return encoded, artifact_json, error_msg


def stage_from_row(row) -> StageRollout:
    raw_acceptances, raw_artifacts = load(row.acceptances), load(row.artifacts)
    if type(raw_acceptances) is not list or type(raw_artifacts) is not list:
        raise Invalid("stored evidence must be JSON arrays")
    acceptances = []
    for item in raw_acceptances:
        shape(item, {"condition", "passed", "reason"})
        acceptances.append(AcceptanceResult(**item))
    artifacts = [Artifact.from_dict(item) for item in raw_artifacts]
    encode_evidence(acceptances, artifacts, row.error_msg)
    # Check the actual stored wire size too, not only its canonical re-encoding.
    if len(row.acceptances) > 8192:
        raise Invalid("stored acceptances JSON exceeds 8192 characters")
    if bool(row.assignee_type) != bool(row.assignee_uuid):
        raise Invalid("incomplete stored assignee")
    text(row.assignee_type, empty=True, maximum=64)
    text(row.assignee_uuid, empty=True, maximum=256)
    text(row.session_id, empty=True, maximum=512)
    return StageRollout(
        row.name,
        row.id,
        status(row.status),
        row.assignee_type,
        row.assignee_uuid,
        row.input,
        row.session_id,
        artifacts,
        acceptances,
        row.error_msg,
    )
