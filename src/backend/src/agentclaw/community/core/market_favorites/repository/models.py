"""SQLAlchemy model for ``ac_market_favorite``."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
)
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env


class MarketFavoriteModel(Base):
    __tablename__ = "ac_market_favorite"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    space_id = Column(AutoIncrementBigInteger, nullable=False)
    target_type = Column(String(32), nullable=False)
    target_code = Column(String(128), nullable=False)
    created_by = Column(String(256), nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "space_id",
            "target_type",
            "target_code",
            "env",
            name="uk_market_favorite_target_env",
        ),
        Index(
            "idx_market_favorite_space",
            "avernet_tenant",
            "space_id",
            "env",
            "gmt_modified",
        ),
    )

    def to_record(self) -> MarketFavoriteRecord:
        return MarketFavoriteRecord(
            id=self.id,
            space_id=self.space_id,
            target_type=FavoriteTargetType(self.target_type),
            target_code=self.target_code,
            created_by=self.created_by,
            env=self.env,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )


register_avernet_tenant_guard(MarketFavoriteModel)
