"""Narrow compatibility boundary for the published Legacy SkillSet batch wire."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class LegacySkillSetCompatibilityProtocol(Protocol):
    """Resolve or materialize a historical market reference as an ``ac_skill.id``."""

    def resolve_or_create_legacy_market_skill(
        self, *, identifier: str, owner_id: str, bot_id: str
    ) -> str: ...


@runtime_checkable
class LegacySkillSetCompatibilityFactoryProtocol(Protocol):
    """Mint the compatibility adapter without exposing the full legacy service."""

    def create(
        self, *args: Any, **kwargs: Any
    ) -> LegacySkillSetCompatibilityProtocol: ...
