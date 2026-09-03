"""Bot ownership-Space mutation policy and orchestration."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.bot_management.bot_space import (
    BotSpaceAccessProtocol,
    BotSpaceAssignmentResult,
)
from agentclaw.community.core.bot_management.bot_quota_service_protocol import (
    BotQuotaServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotOperationNotAllowedError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import SpaceType
from agentclaw.community.core.bot_management.bot_space_service_protocol import (
    BotSpaceServiceProtocol,
)


class BotSpaceService(BotSpaceServiceProtocol):
    """Change a Bot's structured ``ac_bots.space_id`` assignment.

    Bot ownership and target-Space membership are both checked in core.  The
    HTTP adapter only translates the typed result to the public contract.
    """

    @inject
    def __init__(
        self,
        repository: BotRepository,
        space_access: BotSpaceAccessProtocol,
        bot_quota: BotQuotaServiceProtocol,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._bot_quota = bot_quota

    def change_space(
        self, *, bot_id: str, owner_id: str, space_id: int
    ) -> BotSpaceAssignmentResult:
        bot = self._repository.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        space, _member = self._space_access.require_space_member(
            space_id=space_id,
            user_id=owner_id,
        )
        if (
            space.space_type is SpaceType.PERSONAL
            and space.personal_owner_id != owner_id
        ):
            # Personal Spaces are not transferable destinations even if a bad
            # membership row exists. Preserve the owner invariant here rather
            # than relying on persistence data being perfect.
            raise SpaceAccessDeniedError("personal space belongs to another user")

        if (
            bot.get("bot_type") == "desktop"
            and space.space_type is not SpaceType.PERSONAL
        ):
            # Local Bots have no shared runtime that a team Space can own. This
            # is the same P0 product constraint enforced by the inventory combo
            # policy; moving one would create a record the runtime cannot honor.
            raise BotOperationNotAllowedError(
                "local bots can only belong to their owner's personal space"
            )

        persisted_space_id = space.id
        changed = bot.get("space_id") != persisted_space_id
        if not changed:
            return BotSpaceAssignmentResult(bot=bot, space=space, changed=False)

        normalizes_legacy_personal = (
            bot.get("space_id") is None
            and space.space_type is SpaceType.PERSONAL
            and space.personal_owner_id == owner_id
        )
        if bot.get("bot_type") == "desktop" or normalizes_legacy_personal:
            updated = self._repository.update_space_by_owner(
                bot_id=bot_id,
                owner_id=owner_id,
                space_id=persisted_space_id,
            )
        else:
            with self._bot_quota.guard_add(owner_id=owner_id, space_id=space.id):
                updated = self._repository.update_space_by_owner(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    space_id=persisted_space_id,
                )
        if updated is None:
            # The Bot may have been deleted between the read and write. Mask it
            # exactly like the initial owner-scoped lookup.
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        return BotSpaceAssignmentResult(bot=updated, space=space, changed=True)
