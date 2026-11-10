"""Identity inheritance for manifest creations.

Split out of ``create_flow`` (R9: no module over 1000 lines). One cohesive
concern: which Bot supplies a creation's passport identity, whether it may,
and what happens instead of a passport application when one does.

Everything that validates a source runs **before** anything is written, so an
invalid source leaves no stored manifest and never reaches Passport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from agentclaw.community.core.bot_management.create_context import BotCreateSpec
from agentclaw.community.core.bot_management.errors import IdentitySourceInvalidError
from agentclaw.community.core.mcp.services.passport_scope import (
    passport_mcp_codes_from_entries,
)
from agentclaw.community.plugin_api.passport import extract_cli_items

if TYPE_CHECKING:  # bot_service imports this module's consumers; avoid a cycle.
    from agentclaw.community.core.bot_management.services.bot_service import BotService


def resolve_identity_source(
    *,
    spec: BotCreateSpec,
    bot_service: "BotService",
) -> dict[str, Any] | None:
    """Validate and return the Bot this creation inherits identity from.

    ``None`` for an ordinary creation — every check below is skipped, so the
    ordinary path is byte-for-byte what it was. Raises
    :class:`IdentitySourceInvalidError` naming the reason otherwise.
    """
    source_id = (spec.identity_bot_id or "").strip()
    if not source_id:
        if spec.identity_bot_id is not None:
            # Blank is not "no source": the caller asked for inheritance and
            # named nothing. Storing NULL would silently give them an ordinary
            # Bot that later fails at its first credential lookup.
            raise IdentitySourceInvalidError("identity_bot_id must not be blank")
        return None

    source = bot_service.get_identity_source_row(source_id, entity_id=spec.entity_id)
    if source is None:
        # Covers absent, soft-deleted and another entity's Bot alike: the
        # lookup is owner-scoped, so a foreign Bot is simply not found. Naming
        # the entity makes the same-entity rule visible instead of looking like
        # a typo in the id.
        raise IdentitySourceInvalidError(
            f"identity source {source_id!r} is not a Bot owned by "
            f"{spec.entity_id!r}"
        )

    passport = (source.get("ext") or {}).get("passport") or {}
    # An issued, usable passport is one that carries an agent code. The
    # ``status`` field is plugin-specific -- the self-issued plugin writes
    # ``ISSUED``, the prod plugin writes no status at all -- so requiring it
    # would reject every source a real deployment issues. Only a pending
    # status is refused: the code exists but the passport is not usable yet.
    if (
        passport.get("status") == "PENDING"
        or not str(passport.get("agent_code") or "").strip()
    ):
        raise IdentitySourceInvalidError(
            f"identity source {source_id!r} holds no issued passport; "
            "it cannot supply an agent code"
        )

    return source


def identity_scope_violations(
    *, parsed_manifest: Mapping[str, Any], source_passport: Mapping[str, Any] | None
) -> set[str]:
    """Declared resources the identity source's passport does not cover.

    Compared on passport-declared scope: LOCAL/stdio MCPs are outside passport's
    ownership, so they are neither required nor refused here. CLIs compare on
    ``cli_code``, the only identifying field.
    """
    allowed_mcps = set(passport_mcp_codes_from_entries(_passport_mcps(source_passport)))
    allowed_clis = {
        item["cli_code"]
        for item in extract_cli_items(source_passport)
        if item.get("cli_code")
    }

    violations: set[str] = set()
    for entry in parsed_manifest.get("mcp") or ():
        code = entry.get("server_code") if isinstance(entry, Mapping) else None
        if code and code not in allowed_mcps:
            violations.add(f"mcp:{code}")
    for entry in parsed_manifest.get("cli_tools") or ():
        code = entry.get("name") if isinstance(entry, Mapping) else None
        if code and code not in allowed_clis:
            violations.add(f"cli:{code}")
    return violations


def inherited_agent_code(
    *,
    spec: BotCreateSpec,
    bot_service: "BotService",
) -> str:
    """The identity source's agent code, read at creation time.

    Never the inheriting Bot's own id: nothing was ever issued under it. Raises
    rather than returning empty, so a missing identity is loud at creation
    instead of surfacing later as opaque failures at the gateway.
    """
    source_id = (spec.identity_bot_id or "").strip()
    source = bot_service.get_identity_source_row(source_id, entity_id=spec.entity_id)
    if source is None:
        raise IdentitySourceInvalidError(
            f"identity source {source_id!r} is no longer available"
        )
    passport = (source.get("ext") or {}).get("passport") or {}
    agent_code = str(passport.get("agent_code") or "").strip()
    if not agent_code:
        raise IdentitySourceInvalidError(
            f"identity source {source_id!r} has no agent code"
        )
    return agent_code


def _passport_mcps(passport: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The MCP entries a passport declares (its resource manifest)."""
    if not isinstance(passport, Mapping):
        return []
    raw = passport.get("mcps") or passport.get("mcp_items") or []
    return [item for item in raw if isinstance(item, Mapping)]
