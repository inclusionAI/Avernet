"""Canonical identity resolution for task-executor BCS group formation."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsBotIdentityResolver,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotGroupIdentity:
    """Resolved identities and the original spelling needed by group formation."""

    participant_ids: list[str]
    member_roles: dict[str, str]
    raw_bindings: dict[str, dict[str, Any]]
    manager_bot_id: str | None
    originator_bot_id: str | None
    resolved: dict[str, str]
    identity_aliases: dict[str, str]

    def canonical_reference(self, bot_id: str) -> str:
        """Return the full identity selected for a possibly bare reference."""
        return self.identity_aliases.get(bot_id, bot_id)

    def bcs_uuid(self, product_bot_id: str) -> str:
        """Return the BCS identity for a participant or group reference."""
        try:
            return self.resolved[product_bot_id]
        except KeyError as exc:
            raise BotIdentityResolutionError(
                f"BCS identity resolver omitted bot_id: {product_bot_id}"
            ) from exc


def resolve_group_identity(
    gf: GroupFormation,
    identity_resolver: BcsBotIdentityResolver,
    *,
    raw_bindings: dict[str, dict[str, Any]],
    member_roles: dict[str, str],
) -> BotGroupIdentity:
    """Resolve all group participants and references to full BCS identities.

    Bare product IDs remain supported at the internal GroupFormation boundary,
    but an explicit full identity in a binding or manager field disambiguates
    them locally. Without such an alias, the BotService resolver fails closed
    when a product bot ID belongs to multiple owners.
    """
    raw_participant_ids = [
        str(bot_id).strip() for bot_id in gf.bot_ids if str(bot_id).strip()
    ]
    if not raw_participant_ids:
        raise BotIdentityResolutionError("cannot form a group without bots")

    manager_bot_id = (
        gf.extend_props.get("manager_bot_id")
        if gf.collab_mode == "manager_worker"
        else None
    )
    originator_bot_id = gf.extend_props.get("originator_bot_id")
    referenced_ids = list(raw_participant_ids)
    if manager_bot_id:
        referenced_ids.append(str(manager_bot_id))
    if originator_bot_id:
        referenced_ids.append(str(originator_bot_id))
    for spec in raw_bindings.values():
        referenced_ids.extend(spec["bot_ids"])
    referenced_ids = list(dict.fromkeys(referenced_ids))

    full_identities_by_product: dict[str, set[str]] = {}
    for referenced_id in referenced_ids:
        product_id, separator, embedded_owner_id = referenced_id.partition(":")
        if separator and product_id and embedded_owner_id:
            full_identities_by_product.setdefault(product_id, set()).add(referenced_id)
    identity_aliases: dict[str, str] = {}
    for referenced_id in referenced_ids:
        if ":" in referenced_id:
            continue
        candidates = full_identities_by_product.get(referenced_id, set())
        if len(candidates) == 1:
            identity_aliases[referenced_id] = next(iter(candidates))

    def canonical_reference(bot_id: str) -> str:
        return identity_aliases.get(bot_id, bot_id)

    # members_info can use either legacy bare or explicit full spelling. Index
    # roles by both forms so alias normalization does not discard a role.
    for member_id, role in list(member_roles.items()):
        member_roles.setdefault(canonical_reference(member_id), role)

    participant_ids: list[str] = []
    canonical_participants: set[str] = set()
    binding_targets = [
        binding_bot_id
        for spec in raw_bindings.values()
        for binding_bot_id in spec["bot_ids"]
    ]
    for referenced_id in [*raw_participant_ids, *binding_targets]:
        canonical_id = canonical_reference(referenced_id)
        if canonical_id in canonical_participants:
            continue
        canonical_participants.add(canonical_id)
        participant_ids.append(referenced_id)

    resolver_ids = [
        canonical_reference(referenced_id) for referenced_id in referenced_ids
    ]
    logger.info(
        "[task][task_executor] form_coop_group start collab=%s bot_ids=%s "
        "referenced_ids=%s manager_bot_id=%s originator_bot_id=%s aliases=%s",
        gf.collab_mode,
        participant_ids,
        referenced_ids,
        manager_bot_id,
        originator_bot_id,
        identity_aliases,
    )
    try:
        resolved_canonical = identity_resolver.resolve_many(
            list(dict.fromkeys(resolver_ids))
        )
    except Exception:
        logger.exception(
            "[task][task_executor] form_coop_group identity resolution failed collab=%s bot_ids=%s "
            "referenced_ids=%s resolver_ids=%s",
            gf.collab_mode,
            participant_ids,
            referenced_ids,
            list(dict.fromkeys(resolver_ids)),
        )
        raise
    resolved = {
        referenced_id: resolved_canonical[
            identity_aliases.get(referenced_id, referenced_id)
        ]
        for referenced_id in referenced_ids
    }

    participant_identities = {
        resolved[participant_id] for participant_id in participant_ids
    }
    reference_extras: list[str] = []
    if manager_bot_id:
        reference_extras.append(str(manager_bot_id))
    if originator_bot_id:
        reference_extras.append(str(originator_bot_id))
    for spec in raw_bindings.values():
        reference_extras.extend(spec["bot_ids"])
    unknown = [
        referenced_id
        for referenced_id in dict.fromkeys(reference_extras)
        if resolved[referenced_id] not in participant_identities
    ]
    if unknown:
        raise BotIdentityResolutionError(
            f"group bindings reference bots outside GroupFormation.bot_ids: {unknown}"
        )
    logger.info(
        "[task][task_executor] form_coop_group identities resolved collab=%s resolved=%s",
        gf.collab_mode,
        resolved,
    )
    return BotGroupIdentity(
        participant_ids=participant_ids,
        member_roles=member_roles,
        raw_bindings=raw_bindings,
        manager_bot_id=str(manager_bot_id) if manager_bot_id else None,
        originator_bot_id=str(originator_bot_id) if originator_bot_id else None,
        resolved=resolved,
        identity_aliases=identity_aliases,
    )
