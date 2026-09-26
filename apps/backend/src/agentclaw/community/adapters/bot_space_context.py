"""Bridge Bot inventory's space seam to the Spaces Service API."""

from __future__ import annotations

from typing import Any, Mapping

from injector import inject

from agentclaw.community.api.space_service import (
    SpaceAccessServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.bot_inventory.errors import BotInventoryPermissionError
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.types import BusinessSpaceRef
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceType

_PERSONAL_SPACE_ID_PREFIX = "personal:"


class SpaceServiceBotSpaceContext(BusinessSpaceContextProtocol):
    """Resolve inventory space context through the owning Spaces module.

    ``ac_bots.space_id`` stores the numeric ``ac_space.id``. This adapter maps
    it to the inventory contract's string identifier and preserves the
    synthetic ``personal:<user>`` fallback for accounts whose personal Space
    has not been initialized yet; reads must not create a Space as a side
    effect.
    """

    @inject
    def __init__(
        self,
        spaces: SpaceServiceProtocol,
        access: SpaceAccessServiceProtocol,
    ) -> None:
        self._spaces = spaces
        self._access = access

    def resolve_current(
        self, *, owner_id: str, header_space_id: str | None
    ) -> BusinessSpaceRef:
        value = header_space_id.strip() if header_space_id is not None else ""
        if not value or value == f"{_PERSONAL_SPACE_ID_PREFIX}{owner_id}":
            return self._personal(owner_id)

        space_id = _numeric_space_id(value)
        if space_id is None:
            raise BotInventoryPermissionError("business space is not available")
        return self._require_member_space(space_id=space_id, owner_id=owner_id)

    def bot_space(
        self,
        *,
        bot: Mapping[str, Any],
        owner_id: str,
        current_space: BusinessSpaceRef | None = None,
    ) -> BusinessSpaceRef | None:
        raw_space_id = bot.get("space_id")
        if (
            not raw_space_id
            or str(raw_space_id) == f"{_PERSONAL_SPACE_ID_PREFIX}{owner_id}"
        ):
            if current_space is not None and current_space.kind == "personal":
                return current_space
            return self._personal(owner_id)

        space_id = _numeric_space_id(str(raw_space_id))
        if space_id is None:
            return None
        canonical_id = str(space_id)
        if current_space is not None:
            if current_space.space_id == canonical_id:
                return current_space
            # The inventory endpoint always filters to ``current_space``.  A
            # mismatching Bot will be discarded, so avoid an N+1 Space lookup
            # merely to populate fields that cannot reach the response.
            return BusinessSpaceRef(
                space_id=canonical_id,
                name=canonical_id,
                kind="unknown",
            )
        try:
            return self._require_member_space(space_id=space_id, owner_id=owner_id)
        except BotInventoryPermissionError:
            return None

    def assert_bot_visible_in_current_space(
        self, *, bot: Mapping[str, Any], owner_id: str, current_space: BusinessSpaceRef
    ) -> None:
        bot_space = self.bot_space(
            bot=bot,
            owner_id=owner_id,
            current_space=current_space,
        )
        if bot_space is None or bot_space.space_id != current_space.space_id:
            raise BotInventoryPermissionError(
                "bot is not visible in current business space"
            )

    def _personal(self, owner_id: str) -> BusinessSpaceRef:
        records = self._spaces.batch_query_personal(user_ids=[owner_id])
        record = records[0] if records else None
        if record is None or not record.found or record.space_id is None:
            return BusinessSpaceRef(
                space_id=f"{_PERSONAL_SPACE_ID_PREFIX}{owner_id}",
                name="Personal",
                kind="personal",
            )
        space = self._require_member_space(space_id=record.space_id, owner_id=owner_id)
        if space.kind != "personal":
            raise BotInventoryPermissionError("personal space is not available")
        return space

    def _require_member_space(
        self, *, space_id: int, owner_id: str
    ) -> BusinessSpaceRef:
        try:
            space, _member = self._access.require_space_member(
                space_id=space_id,
                user_id=owner_id,
            )
        except (SpaceNotFoundError, SpaceAccessDeniedError) as exc:
            raise BotInventoryPermissionError(
                "business space is not available"
            ) from exc
        if (
            space.space_type is SpaceType.PERSONAL
            and space.personal_owner_id != owner_id
        ):
            raise BotInventoryPermissionError("business space is not available")
        return _to_ref(space)


def _numeric_space_id(value: str) -> int | None:
    try:
        parsed = int(value, 10)
    except ValueError:
        return None
    return parsed if parsed >= 1 and str(parsed) == value else None


def _to_ref(space: SpaceRecord) -> BusinessSpaceRef:
    return BusinessSpaceRef(
        space_id=str(space.id),
        name=space.name,
        kind=space.space_type.value.lower(),
    )
