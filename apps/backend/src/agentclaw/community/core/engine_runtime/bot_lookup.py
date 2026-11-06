"""Runtime-only Bot reads, without management-page enrichment.

Address resolution and permission checks retain their existing responsibility:
this lookup never authorizes an actor, caches a row or selects a runtime stage.
"""

from typing import Any

from injector import inject

from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import (
    BotAddressNotFoundError,
)
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.utils.runtime_list_timing import mark


class RuntimeBotLookup:
    """Read the scoped row needed by the relay and socket composer.

    Unlike BotService.get_bot_by_address, do not re-read the resolved row or
    attach device_binding/template_config. Those display fields are not used
    by either consumer; device routing remains stage-specific downstream.
    """

    @inject
    def __init__(self, repository: BotRepository) -> None:
        self._repository = repository

    def get_bot_by_address(self, bot_id: str, user_id: str) -> dict[str, Any]:
        try:
            address = BotAddressResolver(self._repository).resolve(
                bot_id=bot_id, legacy_owner_id=user_id,
            )
        except BotAddressNotFoundError as exc:
            raise BotNotFoundError(f"Bot not found: {bot_id}") from exc
        mark("bot.locator_resolved")
        return dict(address.bot)

    def get_bot_pk(self, bot_id: str, user_id: str) -> int:
        # Keep the existing current-Owner lookup and missing/keyless semantics.
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return int(bot.get("id") or 0)
