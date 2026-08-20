"""Behavior tests for Space-scoped market favorites."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.market_favorites.errors import FavoriteTargetInvalidError
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
    MarketSource,
)
from agentclaw.community.core.market_favorites.services.favorite_service import (
    MarketFavoriteService,
)


def _record(*, target_code: str = "skill-1") -> MarketFavoriteRecord:
    now = datetime(2026, 8, 18, 10, 0, 0)
    return MarketFavoriteRecord(
        id=1,
        space_id=7,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code=target_code,
        created_by="member-1",
        env="dev",
        gmt_created=now,
        gmt_modified=now,
    )


def _service():
    repository = MagicMock()
    access = MagicMock()
    return MarketFavoriteService(repository, access), repository, access


def test_add_requires_membership_and_normalizes_target_code() -> None:
    service, repository, access = _service()
    repository.add.return_value = (_record(), True)

    assert service.add(
        space_id=7,
        actor_id="member-1",
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code=" skill-1 ",
    ) == (_record(), True)

    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    repository.add.assert_called_once_with(
        space_id=7,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="member-1",
        env="dev",
    )


@pytest.mark.parametrize("target_code", ["", "   ", "x" * 129])
def test_add_rejects_invalid_target_code(target_code: str) -> None:
    service, repository, _ = _service()

    with pytest.raises(FavoriteTargetInvalidError, match="1-128"):
        service.add(
            space_id=7,
            actor_id="member-1",
            market_source=MarketSource.TEAMCLAW,
            target_type=FavoriteTargetType.MCP,
            target_code=target_code,
        )

    repository.add.assert_not_called()


def test_cancel_missing_favorite_is_idempotent() -> None:
    service, repository, access = _service()
    repository.cancel.return_value = False

    assert (
        service.cancel(
            space_id=7,
            actor_id="member-1",
            market_source=MarketSource.SKILLCENTER,
            target_type=FavoriteTargetType.SKILL,
            target_code=" skill-1 ",
        )
        is False
    )

    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    assert repository.cancel.call_args.kwargs["target_code"] == "skill-1"


def test_cancel_existing_favorite_returns_true() -> None:
    service, repository, _ = _service()
    repository.cancel.return_value = True

    assert (
        service.cancel(
            space_id=7,
            actor_id="member-1",
            market_source=MarketSource.SKILLCENTER,
            target_type=FavoriteTargetType.SKILL,
            target_code="skill-1",
        )
        is True
    )


def test_search_requires_membership_and_normalizes_filters() -> None:
    service, repository, access = _service()
    repository.search.return_value = (1, [_record()])

    result = service.search(
        space_id=7,
        actor_id="member-1",
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        keyword="  skill  ",
        page_no=2,
        page_size=10,
    )

    assert result == (1, [_record()])
    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    repository.search.assert_called_once_with(
        space_id=7,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        keyword="skill",
        env="dev",
        offset=10,
        limit=10,
    )


def test_search_turns_blank_keyword_into_none() -> None:
    service, repository, _ = _service()
    repository.search.return_value = (0, [])

    service.search(
        space_id=7,
        actor_id="member-1",
        market_source=None,
        target_type=None,
        keyword="  ",
        page_no=1,
        page_size=20,
    )

    assert repository.search.call_args.kwargs["keyword"] is None


def test_find_favorited_codes_is_batched_normalized_and_ordered() -> None:
    service, repository, access = _service()
    repository.find_favorited_codes.return_value = {"skill-1", "skill-2"}

    result = service.find_favorited_codes(
        space_id=7,
        actor_id="member-1",
        market_source=MarketSource.TEAMCLAW,
        target_type=FavoriteTargetType.SKILL,
        target_codes=[" skill-2 ", "skill-1", "skill-2", "skill-3"],
    )

    assert result == ["skill-2", "skill-1"]
    access.require_space_member.assert_called_once_with(space_id=7, user_id="member-1")
    repository.find_favorited_codes.assert_called_once_with(
        space_id=7,
        market_source=MarketSource.TEAMCLAW,
        target_type=FavoriteTargetType.SKILL,
        target_codes=["skill-2", "skill-1", "skill-3"],
        env="dev",
    )
