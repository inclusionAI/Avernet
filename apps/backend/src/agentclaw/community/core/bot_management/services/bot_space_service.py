"""Bot ownership-Space mutation policy and orchestration."""

from __future__ import annotations

from injector import inject

from agentclaw.community.api.bot_manager_sync import BotManagerSyncProtocol
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
from agentclaw.community.kernel.bot_manager_sync import SyncReason
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


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
        manager_sync: BotManagerSyncProtocol,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._bot_quota = bot_quota
        self._manager_sync = manager_sync

    def change_space(
        self,
        *,
        bot_id: str,
        owner_id: str,
        space_id: int,
        env: str | None = None,
    ) -> BotSpaceAssignmentResult:
        # Normalize once here; every collaborator below receives the
        # resolved data env so admin cross-env corrections stay on a
        # single business code path. Order of checks is load-bearing for
        # unknown env values: the bot lookup fails fast before quota or
        # write is ever reached.
        # An explicit env is matched as-is against the ``env`` column —
        # no family folding (gray->prod, prepub->pre); folding only
        # applies to the runtime fallback. Invalid values fail closed
        # at the bot lookup.
        env = env or get_current_env()
        bot = self._repository.get_by_id_and_owner(bot_id, owner_id, env=env)
        if bot is None:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        space, _member = self._space_access.require_space_member(
            space_id=space_id,
            user_id=owner_id,
            env=env,
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
        if (
            bot.get("bot_type") == "desktop"
            or (bot.get("active_engine") or "").strip().lower() == "teclaw"
            or normalizes_legacy_personal
        ):
            updated = self._repository.update_space_by_owner(
                bot_id=bot_id,
                owner_id=owner_id,
                space_id=persisted_space_id,
                env=env,
            )
        else:
            with self._bot_quota.guard_add(
                owner_id=owner_id, space_id=space.id, env=env
            ):
                updated = self._repository.update_space_by_owner(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    space_id=persisted_space_id,
                    env=env,
                )
        if updated is None:
            # The Bot may have been deleted between the read and write. Mask it
            # exactly like the initial owner-scoped lookup.
            raise BotNotFoundError(f"Bot not found: {bot_id}")
        # The assignment is durable — the Bot's Space membership now vouches
        # for a different collaborator set, so BCS's manager projection may
        # have moved. Best-effort submit with the EXPLICIT env threaded through
        # this business call (never re-collapsed to the process env): a
        # rejected submit leaves the Space change successful.
        try:
            self._manager_sync.request_sync(
                bot_pk=updated["id"],
                env=env,
                avernet_tenant=get_current_avernet_tenant(),
                reason=SyncReason.BOT_SPACE_CHANGED,
            )
        except Exception:
            logger.error(
                "manager sync notification rejected",
                extra={"bot_pk": updated.get("id"), "env": env},
            )
        return BotSpaceAssignmentResult(bot=updated, space=space, changed=True)
