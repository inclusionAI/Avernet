"""Pure ownership projection: established BCN address, current Owner payload."""

from __future__ import annotations

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.kernel.bot_manager_sync import SnapshotUnavailable
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipFacts,
    OwnershipSnapshot,
)


def build_snapshot(facts: OwnershipFacts) -> OwnershipSnapshot:
    """Validate exact identities without trimming, coercion or Owner fallback."""
    values = {
        "tenant": facts.key.avernet_tenant,
        "env": facts.key.env,
        "bot_id": facts.bot_id,
        "entity_id": facts.entity_id,
        "owner_id": facts.owner_id,
    }
    for name, value in values.items():
        if not isinstance(value, str) or not value or value != value.strip():
            raise SnapshotUnavailable(f"invalid_{name}")
    if type(facts.key.bot_pk) is not int or facts.key.bot_pk < 1:
        raise SnapshotUnavailable("invalid_bot_pk")
    if facts.bot_type is not None and not isinstance(facts.bot_type, str):
        raise SnapshotUnavailable("invalid_bot_type")
    try:
        address = bcn_bot_reference(
            {
                "bot_id": facts.bot_id,
                "entity_id": facts.entity_id,
                "owner_id": facts.owner_id,
                "bot_type": facts.bot_type,
                "ext": {"bcn_provider_bot_ref": facts.bcn_provider_bot_ref},
            }
        )
    except ValueError as exc:
        raise SnapshotUnavailable("invalid_bcn_identity") from exc
    return OwnershipSnapshot(
        key=facts.key,
        bcs_bot_id=address,
        owner_user_id=facts.owner_id,
    )
