"""Personal-space-only fallback for consuming business-space context."""
from __future__ import annotations

from typing import Any, Mapping

from agentclaw.community.core.bot_inventory.errors import BotInventoryPermissionError
from agentclaw.community.core.bot_inventory.protocols import BusinessSpaceContextProtocol
from agentclaw.community.core.bot_inventory.types import BusinessSpaceRef

_PERSONAL_SPACE_ID_PREFIX = "personal:"


class NoopBusinessSpaceContext(BusinessSpaceContextProtocol):
    """Fallback that only supports a user's personal business space.

    This is intentionally not a permissive noop: when a non-personal header is
    supplied and no business-space owner API is wired, fail closed.
    """

    def resolve_current(
        self, *, owner_id: str, header_space_id: str | None
    ) -> BusinessSpaceRef:
        personal = self._personal(owner_id)
        if header_space_id in (None, "", personal.space_id):
            return personal
        raise BotInventoryPermissionError("business space is not available")

    def bot_space(
        self, *, bot: Mapping[str, Any], owner_id: str
    ) -> BusinessSpaceRef | None:
        # Read the structured ``ac_bots.space_id`` column — NOT ``bot.ext`` (the
        # contract owner rejected the transient ext path). A NULL space falls
        # back to the personal space so the personal view always contains the
        # bot; name/kind are not duplicated on ac_bots, so name resolves to the
        # space id and kind to ``personal``, the same way the bots router's
        # ``BotSpaceRef`` derivation does. (Returning None for NULL would make a
        # legacy personal bot invisible in its own personal view.)
        space_id = bot.get("space_id")
        if not space_id:
            return self._personal(owner_id)
        return BusinessSpaceRef(
            space_id=str(space_id),
            name=str(space_id),
            kind="personal",
        )

    def assert_bot_visible_in_current_space(
        self, *, bot: Mapping[str, Any], owner_id: str, current_space: BusinessSpaceRef
    ) -> None:
        bot_space = self.bot_space(bot=bot, owner_id=owner_id)
        if bot_space is None or bot_space.space_id != current_space.space_id:
            raise BotInventoryPermissionError("bot is not visible in current business space")

    @staticmethod
    def _personal(owner_id: str) -> BusinessSpaceRef:
        return BusinessSpaceRef(
            space_id=f"{_PERSONAL_SPACE_ID_PREFIX}{owner_id}",
            name="Personal",
            kind="personal",
        )
