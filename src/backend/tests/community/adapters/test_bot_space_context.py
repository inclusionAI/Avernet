"""Tests for the Spaces Service API bridge used by Bot inventory."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.bot_space_context import (
    SpaceServiceBotSpaceContext,
)
from agentclaw.community.core.bot_inventory.errors import BotInventoryPermissionError
from agentclaw.community.core.bot_inventory.types import BusinessSpaceRef
from agentclaw.community.core.spaces.errors import SpaceAccessDeniedError
from agentclaw.community.core.spaces.models import (
    PersonalSpaceLookupRecord,
    SpaceMemberRecord,
    SpaceRecord,
    SpaceRole,
    SpaceType,
)


def _space(
    space_id: int,
    *,
    kind: SpaceType,
    owner_id: str | None = None,
) -> SpaceRecord:
    now = datetime.now(UTC)
    return SpaceRecord(
        id=space_id,
        space_code=f"spc-{space_id}",
        space_type=kind,
        name="Personal" if kind is SpaceType.PERSONAL else "Team",
        personal_owner_id=owner_id,
        env="test",
        created_by=owner_id or "u1",
        updated_by=owner_id or "u1",
        gmt_created=now,
        gmt_modified=now,
    )


def _member(space_id: int, user_id: str) -> SpaceMemberRecord:
    now = datetime.now(UTC)
    return SpaceMemberRecord(
        id=1,
        space_id=space_id,
        user_id=user_id,
        role=SpaceRole.OWNER,
        env="test",
        created_by=user_id,
        gmt_created=now,
        gmt_modified=now,
    )


def _context(*, personal_id: int | None = 8):
    spaces = MagicMock()
    spaces.batch_query_personal.return_value = [
        PersonalSpaceLookupRecord(
            user_id="u1",
            space_id=personal_id,
            found=personal_id is not None,
        )
    ]
    access = MagicMock()

    def require_member(*, space_id: int, user_id: str):
        kind = SpaceType.PERSONAL if space_id == 8 else SpaceType.TEAM
        return _space(
            space_id,
            kind=kind,
            owner_id=user_id if kind is SpaceType.PERSONAL else None,
        ), _member(space_id, user_id)

    access.require_space_member.side_effect = require_member
    return SpaceServiceBotSpaceContext(spaces, access), spaces, access


@pytest.mark.unit
def test_default_context_resolves_numeric_personal_space() -> None:
    context, _spaces, access = _context()

    result = context.resolve_current(owner_id="u1", header_space_id=None)

    assert result == BusinessSpaceRef(space_id="8", name="Personal", kind="personal")
    access.require_space_member.assert_called_once_with(space_id=8, user_id="u1")


@pytest.mark.unit
def test_uninitialized_personal_space_keeps_legacy_read_fallback() -> None:
    context, _spaces, access = _context(personal_id=None)

    result = context.resolve_current(owner_id="u1", header_space_id=None)

    assert result.space_id == "personal:u1"
    assert result.kind == "personal"
    access.require_space_member.assert_not_called()


@pytest.mark.unit
def test_numeric_team_context_requires_membership() -> None:
    context, _spaces, access = _context()

    result = context.resolve_current(owner_id="u1", header_space_id="42")

    assert result == BusinessSpaceRef(space_id="42", name="Team", kind="team")
    access.require_space_member.assert_called_once_with(space_id=42, user_id="u1")


@pytest.mark.unit
@pytest.mark.parametrize("value", ["team", "0", "01", "-1", "personal:u2"])
def test_invalid_or_foreign_context_fails_closed(value: str) -> None:
    context, _spaces, _access = _context()

    with pytest.raises(BotInventoryPermissionError):
        context.resolve_current(owner_id="u1", header_space_id=value)


@pytest.mark.unit
def test_non_member_context_is_masked() -> None:
    context, _spaces, access = _context()
    access.require_space_member.side_effect = SpaceAccessDeniedError("no")

    with pytest.raises(BotInventoryPermissionError):
        context.resolve_current(owner_id="u1", header_space_id="42")


@pytest.mark.unit
def test_reassigned_bot_uses_current_team_without_extra_lookup() -> None:
    context, _spaces, access = _context()
    current = BusinessSpaceRef(space_id="42", name="Team", kind="team")

    result = context.bot_space(
        bot={"bot_id": "b1", "space_id": 42},
        owner_id="u1",
        current_space=current,
    )

    assert result is current
    access.require_space_member.assert_not_called()


@pytest.mark.unit
def test_numeric_personal_assignment_is_visible_in_default_inventory() -> None:
    context, _spaces, access = _context()
    current = context.resolve_current(owner_id="u1", header_space_id=None)
    access.reset_mock()

    result = context.bot_space(
        bot={"bot_id": "b1", "space_id": 8},
        owner_id="u1",
        current_space=current,
    )

    assert result is current
    access.require_space_member.assert_not_called()


@pytest.mark.unit
def test_legacy_null_bot_uses_numeric_personal_context() -> None:
    context, _spaces, access = _context()
    current = context.resolve_current(owner_id="u1", header_space_id=None)
    access.reset_mock()

    result = context.bot_space(
        bot={"bot_id": "b1", "space_id": None},
        owner_id="u1",
        current_space=current,
    )

    assert result is current
    access.require_space_member.assert_not_called()
