"""The one authority for who controls a capability's activation state.

R1 — Set-managed, no direct control. A capability that is a member of ANY
     Set of the Bot's — the Default Set included, excluded or not — is
     activated/deactivated only through Set-level operations (activate/
     deactivate the Set; exclude/un-exclude for Default-Set members).
R2 — Deactivate before joining. A capability holding a direct Installation
     row cannot be added to a Set (checked before R3 — today's precedence).
R3 — One Set per capability: held by ANY Set (ordinary or Default, excluded
     or not) ⇒ cannot be added to another.

Identical for skills and MCPs. Callers read the facts; this module decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
)


def is_set_managed(
    *,
    referencing_sets: Sequence[Mapping[str, Any]],
    bot_id: str,
    owner_id: str,
    engine_type: str | None,
    default_engine_types: tuple[str, ...],
) -> bool:
    """R1: is some Set of this Bot's managing the capability?

    True ⇒ direct activate/deactivate must be refused. ``referencing_sets``
    are the Sets holding a membership row for the capability; Sets that are
    not the Bot's — another Bot's, another engine's — are filtered out here.
    There is deliberately no exclusion carve-out: an excluded Default-Set
    member stays Set-managed, and re-activating it means removing the
    exclusion, never the capability-level command.
    """
    return any(
        _set_belongs_to_bot(
            skill_set,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
            default_engine_types=default_engine_types,
        )
        for skill_set in referencing_sets
    )


def require_can_join_set(
    *, is_directly_active: bool, is_in_another_set: bool
) -> None:
    """R2 + R3 for the add-to-Set commands, in today's error precedence.

    Raises ``SkillSetControlPlaneConflictError("RESOURCE_DIRECT_ACTIVE")``
    first, then ``("RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET")``; returns when
    joining is allowed. ``is_in_another_set`` covers ordinary AND Default
    Sets.
    """
    if is_directly_active:
        raise SkillSetControlPlaneConflictError("RESOURCE_DIRECT_ACTIVE")
    if is_in_another_set:
        raise SkillSetControlPlaneConflictError(
            "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
        )


def require_direct_mcp_control_allowed(
    *,
    server_code: str,
    platform_default_codes: frozenset[str],
) -> None:
    """Refuse Direct control of an engine/template Default MCP.

    Platform Default MCPs are code policy rather than Set membership, so R1's
    membership walk cannot see them. They remain policy-managed even after a
    Bot excludes them; exclusion/un-exclusion is their only control surface.
    """
    if server_code in platform_default_codes:
        raise SkillSetControlPlaneConflictError(
            "RESOURCE_MANAGED_BY_PLATFORM_POLICY"
        )


def _set_belongs_to_bot(
    skill_set: Mapping[str, Any],
    *,
    bot_id: str,
    owner_id: str,
    engine_type: str | None,
    default_engine_types: tuple[str, ...],
) -> bool:
    """Is this Set one of the Bot's — its own, or the platform Default it
    inherits?

    Ownerless platform Defaults are settled first, by ``user_id``: the
    repository projects a null ``bolt_id`` to the string ``"default"``, which
    is also a real legacy Bot id, so testing ``bolt_id`` first would confuse
    the two. Such a Default reaches only Bots on its engine.

    One accepted divergence from ``_bot_sets``: it takes the first candidate
    engine with any Default rows, this accepts any candidate. They differ only
    when both the layout and persisted engines have Defaults, where this errs
    toward refusing direct control.
    """
    if bool(skill_set.get("is_default")) and not skill_set.get("user_id"):
        # Decided before the Bot-owned comparison below, which cannot tell
        # these apart: a Bot whose id really is ``default`` shares the string
        # this projection puts on every platform Default.
        return str(skill_set.get("engine_type") or "") in default_engine_types
    if str(skill_set.get("bolt_id") or bot_id) != bot_id:
        return False
    # ``bot_id`` alone does not identify a Bot: the legacy ``default`` Bot
    # exists once per owner, so a Set with this ``bolt_id`` may belong to
    # someone else entirely. And a Set left behind on a previous engine no
    # longer applies. Both are the rest of ``_owned_set_scope``, which is what
    # the listing's bridge selects Sets with; matching less here refuses
    # direct control over Sets the listing never bridges.
    if str(skill_set.get("user_id") or "") != owner_id:
        return False
    return engine_type is None or str(skill_set.get("engine_type") or "") == engine_type


__all__ = [
    "is_set_managed",
    "require_can_join_set",
    "require_direct_mcp_control_allowed",
]
