"""Product audit records, isolated from L4 reducers and Task versions.

Feedback IDs are business identities, not a cross-context uniqueness guarantee.
No feedback writer is enabled until arbitration/progress contracts are settled.
"""

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    text,
)
from ..product_profile_service.snapshot_codec import canonical


def feedback_event_id(axes: dict, context, interactive_uuid: str) -> str:
    text(interactive_uuid, maximum=128)
    actor_key = sha256(canonical(context.actor.to_dict()).encode()).hexdigest()
    identity = {
        "schema": "product-feedback-id@1",
        "avernet_tenant": axes["avernet_tenant"],
        "env": axes["env"],
        "actor_key": actor_key,
        "interactive_uuid": interactive_uuid,
    }
    return "product-feedback-v1:" + sha256(canonical(identity).encode()).hexdigest()


def append_creation_event(session, axes: dict, collet, rollout, context) -> None:
    if collet.current_rollout_id != rollout.id or rollout.collet_id != collet.id:
        raise Invalid("creation audit has an inconsistent parent chain")
    now = datetime.now(timezone.utc)
    session.add(
        ContextCollabEventModel(
            **axes,
            collab_context_id=collet.collab_context_id,
            event_id=f"product-v2:{collet.id}:{uuid4().hex}:0",
            event_kind="product_runtime.collet.created",
            collet_rollout_id=rollout.id,
            collet_stage_rollout_id=None,
            actor_ref=canonical(context.actor.to_dict()),
            visibility="private",
            payload=canonical(
                {
                    "schema": "product-runtime-audit@1",
                    "profile_id": collet.product_profile_id,
                }
            ),
            primitive_type=None,
            parent_event=None,
            context_version_after=None,
            occurred_at=now,
            gmt_create=now,
            gmt_modified=now,
        )
    )
