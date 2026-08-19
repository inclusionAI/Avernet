"""Unified ORM repository for market favorites."""

from __future__ import annotations

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.market_favorites.repository.models import (
    MarketFavoriteModel,
)
from agentclaw.community.core.repository.protocols.market_favorites import (
    MarketFavoriteRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class MarketFavoriteRepository(MarketFavoriteRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Favorite = MarketFavoriteModel

    def add(
        self,
        *,
        space_id,
        target_type,
        target_code,
        user_id,
        env,
    ):
        try:
            with self._db.orm_session() as db:
                existing = (
                    db.query(self._Favorite)
                    .filter(
                        self._Favorite.user_id == user_id,
                        self._Favorite.target_type == target_type.value,
                        self._Favorite.target_code == target_code,
                        self._Favorite.env == env,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    return existing.to_record()
                row = self._Favorite(
                    space_id=space_id,
                    user_id=user_id,
                    target_type=target_type.value,
                    target_code=target_code,
                    created_by=user_id,
                    env=env,
                )
                db.add(row)
                db.flush()
                db.refresh(row)
                return row.to_record()
        except IntegrityError:
            with self._db.orm_session() as db:
                existing = (
                    db.query(self._Favorite)
                    .filter(
                        self._Favorite.user_id == user_id,
                        self._Favorite.target_type == target_type.value,
                        self._Favorite.target_code == target_code,
                        self._Favorite.env == env,
                    )
                    .one_or_none()
                )
                if existing is None:
                    raise
                return existing.to_record()

    def cancel(self, *, space_id, target_type, target_code, user_id, env) -> bool:
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Favorite)
                .filter(
                    self._Favorite.user_id == user_id,
                    self._Favorite.target_type == target_type.value,
                    self._Favorite.target_code == target_code,
                    self._Favorite.env == env,
                )
                .delete(synchronize_session=False)
            )
            return deleted > 0

    def search(
        self,
        *,
        space_id,
        target_type,
        keyword,
        user_id,
        env,
        offset,
        limit,
    ):
        with self._db.orm_session() as db:
            query = db.query(self._Favorite).filter(
                self._Favorite.user_id == user_id,
                self._Favorite.env == env,
            )
            if target_type is not None:
                query = query.filter(self._Favorite.target_type == target_type.value)
            if keyword:
                query = query.filter(self._Favorite.target_code.ilike(f"%{keyword}%"))
            total = query.count()
            rows = (
                query.order_by(
                    self._Favorite.gmt_modified.desc(), self._Favorite.id.desc()
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [row.to_record() for row in rows]
