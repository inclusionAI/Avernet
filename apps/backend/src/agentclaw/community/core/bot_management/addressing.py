"""Resolve request locators once, then carry one immutable Bot address.

External contracts still use several historical shapes.  This module keeps
those shapes at the boundary: downstream code receives a stable ``bot_pk`` and
``entity_id`` and must not continue using the request's ``owner_id`` as a
resource key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from injector import inject

from agentclaw.community.core.bot_management.errors import (
    BotAddressConflictError,
    BotAddressNotFoundError,
    BotLookupAmbiguousError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository


@dataclass(frozen=True, slots=True)
class ResolvedBotContext:
    """Stable identity plus mutable responsibility and the resolved Bot row."""

    bot_pk: int
    bot_id: str
    entity_id: str
    current_owner_id: str
    bot: Mapping[str, Any]

    @property
    def resource_owner_id(self) -> str:
        """Compatibility value for resource columns still named ``owner_id``."""
        return self.entity_id


class BotAddressResolverProtocol(Protocol):
    def resolve(
        self,
        *,
        bot_id: str,
        entity_id: str | None = None,
        legacy_owner_id: str | None = None,
        allow_unique_bot_id: bool = False,
    ) -> ResolvedBotContext: ...

    def resolve_pk(self, bot_pk: int) -> ResolvedBotContext: ...

    def resolve_publish_source(
        self, *, source_bot_pk: int, source_bot_id: str, legacy_owner_id: str,
    ) -> ResolvedBotContext: ...


class BotAddressResolver(BotAddressResolverProtocol):
    """Resolve stable, legacy and PK addresses without silent ambiguity."""

    @inject
    def __init__(self, repository: BotRepository) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        bot_id: str,
        entity_id: str | None = None,
        legacy_owner_id: str | None = None,
        allow_unique_bot_id: bool = False,
    ) -> ResolvedBotContext:
        if entity_id:
            bot = self._repository.get_by_id_and_entity(bot_id, entity_id)
            if bot is None:
                raise BotAddressNotFoundError(
                    f"Bot not found: bot_id={bot_id}, entity_id={entity_id}"
                )
            if legacy_owner_id and legacy_owner_id not in {
                str(bot["entity_id"]),
                str(bot["owner_id"]),
            }:
                raise BotAddressConflictError(
                    "entity_id and owner_id do not address the same Bot"
                )
            return self._context(bot)

        if legacy_owner_id:
            matches = self._repository.list_matches_by_legacy_locator(
                bot_id, legacy_owner_id
            )
            if not matches:
                raise BotAddressNotFoundError(
                    f"Bot not found: bot_id={bot_id}, owner_id={legacy_owner_id}"
                )
            if len(matches) != 1:
                raise BotLookupAmbiguousError(
                    f"Bot address is ambiguous: bot_id={bot_id}, "
                    f"owner_id={legacy_owner_id}"
                )
            return self._context(matches[0])

        if allow_unique_bot_id:
            bot = self._repository.get_unique_by_id(bot_id)
            if bot is None:
                raise BotAddressNotFoundError(f"Bot not found: bot_id={bot_id}")
            return self._context(bot)

        raise BotAddressNotFoundError(
            f"Bot locator is required: bot_id={bot_id}"
        )

    def resolve_pk(self, bot_pk: int) -> ResolvedBotContext:
        bot = self._repository.get_by_pk(bot_pk)
        if bot is None:
            raise BotAddressNotFoundError(f"Bot not found: bot_pk={bot_pk}")
        return self._context(bot)

    def resolve_publish_source(
        self, *, source_bot_pk: int, source_bot_id: str, legacy_owner_id: str,
    ) -> ResolvedBotContext:
        """Resolve publication metadata by its immutable source key first.

        Old and new publish rows disagree on the meaning of owner_id; a live
        source PK needs neither interpretation. Historical default lineages can
        outlive their source row, so retain their owner/entity metadata lookup.
        This never rewrites a publish source PK or substitutes a stage binding.
        Other missing sources and mismatched PKs fail closed.
        """
        bot = self._repository.get_by_pk(source_bot_pk)
        if bot is None:
            if source_bot_id == "default":
                return self.resolve(bot_id=source_bot_id, legacy_owner_id=legacy_owner_id)
            raise BotAddressNotFoundError(f"Publish source Bot not found: bot_pk={source_bot_pk}")
        if str(bot["bot_id"]) != source_bot_id:
            raise BotAddressConflictError("Publish source PK and bot_id identify different Bots")
        return self._context(bot)

    @staticmethod
    def _context(bot: Mapping[str, Any]) -> ResolvedBotContext:
        return ResolvedBotContext(
            bot_pk=int(bot["id"]),
            bot_id=str(bot["bot_id"]),
            entity_id=str(bot["entity_id"]),
            current_owner_id=str(bot["owner_id"]),
            bot=bot,
        )


def stable_resource_owner_id(bot: Mapping[str, Any]) -> str:
    """Return the stable locator stored in legacy resource ``owner_id`` columns."""
    entity_id = bot.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ValueError("Bot entity_id is required for resource addressing")
    return entity_id
