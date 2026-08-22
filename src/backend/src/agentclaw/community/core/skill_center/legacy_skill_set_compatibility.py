"""Narrow compatibility boundary for the published Legacy SkillSet batch wire."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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
