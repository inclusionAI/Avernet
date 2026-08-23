"""Narrow compatibility boundary for the published Legacy SkillSet batch wire."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LegacySkillSetScope:
    """A persisted ordinary SkillSet's strict Bot address."""

    owner_id: str
    bot_id: str


class LegacySkillSetScopeResolverProtocol(Protocol):
    """Recover the strict Bot address for an omitted legacy Bot parameter."""

    def resolve_legacy_set_scope(
        self,
        *,
        set_id: str,
        actor_id: str,
        owner_id_hint: str | None,
    ) -> LegacySkillSetScope | None: ...


def recover_legacy_skill_set_scope(
    *,
    set_id: str,
    actor_id: str,
    owner_id_hint: str | None,
    bot_id_hint: str | None,
    control_plane: LegacySkillSetScopeResolverProtocol,
) -> tuple[str | None, str | None]:
    """Recover only an omitted Bot address; explicit addresses stay strict."""
    if bot_id_hint is not None:
        return owner_id_hint, bot_id_hint
    scope = control_plane.resolve_legacy_set_scope(
        set_id=set_id,
        actor_id=actor_id,
        owner_id_hint=owner_id_hint,
    )
    if scope is None:
        return owner_id_hint, None
    return scope.owner_id, scope.bot_id


class LegacySkillSetCompatibilityProtocol(Protocol):
    """Read Default projections and resolve historical market references."""

    def resolve_or_create_legacy_market_skill(
        self, *, identifier: str, owner_id: str, bot_id: str
    ) -> str: ...

    def get_set_mcp_servers(
        self,
        skill_set_id: str,
        user_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class LegacySkillSetCompatibilityFactoryProtocol(Protocol):
    """Mint the compatibility adapter without exposing the full legacy service."""

    def create(
        self, *args: Any, **kwargs: Any
    ) -> LegacySkillSetCompatibilityProtocol: ...
