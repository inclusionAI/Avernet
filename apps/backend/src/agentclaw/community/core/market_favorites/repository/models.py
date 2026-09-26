"""SQLAlchemy model for ``ac_market_favorite``."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
    MarketSource,
)
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.env_utils import get_current_env


class MarketFavoriteModel(Base):
    __tablename__ = "ac_market_favorite"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    market_source = Column(String(32), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_code = Column(String(128), nullable=False)
    created_by = Column(String(64), nullable=False)
    env = Column(String(20), nullable=True, default=get_current_env)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "space_id",
            "market_source",
            "target_type",
            "target_code",
            "env",
            name="uk_space_target_env",
        ),
        Index(
            "idx_space_env_modified",
            "space_id",
            "env",
            "market_source",
            "target_type",
            "gmt_modified",
        ),
    )

    def to_record(self) -> MarketFavoriteRecord:
        return MarketFavoriteRecord(
            id=self.id,
            space_id=self.space_id,
            market_source=MarketSource(self.market_source),
            target_type=FavoriteTargetType(self.target_type),
            target_code=self.target_code,
            created_by=self.created_by,
            env=self.env,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )
