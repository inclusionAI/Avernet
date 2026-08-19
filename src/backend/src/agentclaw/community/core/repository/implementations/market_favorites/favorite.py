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
        market_source,
        target_type,
        target_code,
        created_by,
        env,
    ):
        try:
            with self._db.orm_session() as db:
                existing = (
                    db.query(self._Favorite)
                    .filter(
                        self._Favorite.space_id == space_id,
                        self._Favorite.market_source == market_source.value,
                        self._Favorite.target_type == target_type.value,
                        self._Favorite.target_code == target_code,
                        self._Favorite.env == env,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    return existing.to_record(), False
                row = self._Favorite(
                    space_id=space_id,
                    market_source=market_source.value,
                    target_type=target_type.value,
                    target_code=target_code,
                    created_by=created_by,
                    env=env,
                )
                db.add(row)
                db.flush()
                db.refresh(row)
                return row.to_record(), True
        except IntegrityError:
            with self._db.orm_session() as db:
                existing = (
                    db.query(self._Favorite)
                    .filter(
                        self._Favorite.space_id == space_id,
                        self._Favorite.market_source == market_source.value,
                        self._Favorite.target_type == target_type.value,
                        self._Favorite.target_code == target_code,
                        self._Favorite.env == env,
                    )
                    .one_or_none()
                )
                if existing is None:
                    raise
                return existing.to_record(), False

    def cancel(self, *, space_id, market_source, target_type, target_code, env) -> bool:
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Favorite)
                .filter(
                    self._Favorite.space_id == space_id,
                    self._Favorite.market_source == market_source.value,
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
        market_source,
        target_type,
        keyword,
        env,
        offset,
        limit,
    ):
        with self._db.orm_session() as db:
            query = db.query(self._Favorite).filter(
                self._Favorite.space_id == space_id,
                self._Favorite.env == env,
            )
            if market_source is not None:
                query = query.filter(
                    self._Favorite.market_source == market_source.value
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

    def find_favorited_codes(
        self,
        *,
        space_id,
        market_source,
        target_type,
        target_codes,
        env,
    ) -> set[str]:
        if not target_codes:
            return set()
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Favorite.target_code)
                .filter(
                    self._Favorite.space_id == space_id,
                    self._Favorite.market_source == market_source.value,
                    self._Favorite.target_type == target_type.value,
                    self._Favorite.target_code.in_(target_codes),
                    self._Favorite.env == env,
                )
                .all()
            )
            return {row[0] for row in rows}
