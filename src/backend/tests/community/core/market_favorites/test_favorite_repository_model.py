"""Persistence invariants for user-scoped market favorites."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.market_favorites.repository.models import (
    MarketFavoriteModel,
)


def _favorite(*, user_id: str, space_id: int) -> MarketFavoriteModel:
    return MarketFavoriteModel(
        space_id=space_id,
        user_id=user_id,
        target_type="SKILL",
        target_code="skill-1",
        created_by=user_id,
        env="dev",
    )


def test_unique_constraint_is_tenant_env_user_and_object_scoped() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add_all(
        [_favorite(user_id="user-a", space_id=1), _favorite(user_id="user-b", space_id=1)]
    )
    session.commit()

    session.add(_favorite(user_id="user-a", space_id=2))
    with pytest.raises(IntegrityError):
        session.commit()
