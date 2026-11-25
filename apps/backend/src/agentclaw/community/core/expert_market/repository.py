"""Dialect-neutral catalogue persistence and global discovery ordering."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, or_

from agentclaw.community.core.repository.json_expression import JsonText

from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.protocols import (
    ExpertCatalogRepositoryProtocol,
)
from agentclaw.community.core.expert_market.orm import ExpertModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.implementations.bot.expert_origin import (
    expert_creation_counts,
)


class ExpertCatalogRepository(ExpertCatalogRepositoryProtocol):
    def __init__(self, db: DatabasePlugin):
        self._db = db

    @staticmethod
    def _scope(scope: ExpertScope):
        return (
            ExpertModel.avernet_tenant == scope.tenant,
            ExpertModel.env == scope.env,
            ExpertModel.source_repo_id == scope.source_repo_id,
        )

    def reconcile(
        self,
        scope: ExpertScope,
        source_commit: str,
        valid: dict[str, dict[str, Any]],
        errors: dict[str, str],
        seen: set[str],
    ) -> None:
        """Apply a completely enumerated scan; parse failures preserve old inputs."""
        with self._db.orm_session() as db:
            rows = {
                r.expert_id: r
                for r in db.query(ExpertModel).filter(*self._scope(scope)).all()
            }
            for expert_id, value in valid.items():
                row = rows.get(expert_id)
                if row is None:
                    row = ExpertModel(
                        avernet_tenant=scope.tenant,
                        env=scope.env,
                        source_repo_id=scope.source_repo_id,
                        expert_id=expert_id,
                    )
                    db.add(row)
                for field in (
                    "name",
                    "description",
                    "category",
                    "enabled",
                    "expert_path",
                    "content_digest",
                ):
                    setattr(row, field, value[field])
                row.tags = json.dumps(value["tags"], ensure_ascii=False)
                row.effective_input = json.dumps(value, ensure_ascii=False)
                row.source_commit = source_commit
                row.last_seen_commit = source_commit
                row.sync_error = None
                row.is_deleted = False
            for expert_id, row in rows.items():
                if expert_id in errors:
                    row.last_seen_commit = source_commit
                    row.sync_error = errors[expert_id]
                elif expert_id not in seen:
                    row.is_deleted = True
                    row.last_seen_commit = source_commit

    def _query(self, db, scope: ExpertScope):
        counts = expert_creation_counts(db, tenant=scope.tenant, env=scope.env)
        count = func.coalesce(counts.c.creation_count, 0)
        query = (
            db.query(ExpertModel, count)
            .outerjoin(
                counts,
                (counts.c.expert_id == ExpertModel.expert_id)
                & (counts.c.repo_id == ExpertModel.source_repo_id),
            )
            .filter(
                *self._scope(scope),
                ExpertModel.enabled.is_(True),
                ExpertModel.is_deleted.is_(False),
            )
        )
        return query, count

    @staticmethod
    def _record(row, count: int) -> dict[str, Any]:
        value = json.loads(row.effective_input)
        value.update(
            creation_count=int(count),
            first_added_at=row.first_added_at.isoformat(),
            source_commit=row.source_commit,
        )
        return value

    def search(
        self,
        scope: ExpertScope,
        *,
        keyword: str,
        category: str | None,
        sort: str,
        page_num: int,
        page_size: int,
    ):
        with self._db.orm_session() as db:
            query, count = self._query(db, scope)
            keyword = keyword.strip()
            if keyword:
                escaped = (
                    keyword.lower()
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = "%" + escaped + "%"
                query = query.filter(
                    or_(
                        *(
                            func.lower(field).like(pattern, escape="\\")
                            for field in (
                                ExpertModel.name,
                                ExpertModel.description,
                                *(
                                    JsonText(ExpertModel.tags, f"$[{i}]")
                                    for i in range(20)
                                ),
                            )
                        )
                    )
                )
            if category is not None:
                query = query.filter(ExpertModel.category == category)
            total = query.count()
            primary = (
                ExpertModel.first_added_at.desc() if sort == "newest" else count.desc()
            )
            rows = (
                query.order_by(
                    primary, ExpertModel.expert_id, ExpertModel.source_repo_id
                )
                .offset((page_num - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [self._record(row, number) for row, number in rows]

    def get(self, scope: ExpertScope, expert_id: str):
        with self._db.orm_session() as db:
            query, _ = self._query(db, scope)
            result = query.filter(ExpertModel.expert_id == expert_id).one_or_none()
            return self._record(*result) if result else None

    def categories(self, scope: ExpertScope) -> list[str]:
        with self._db.orm_session() as db:
            rows = (
                db.query(ExpertModel.category)
                .filter(
                    *self._scope(scope),
                    ExpertModel.enabled.is_(True),
                    ExpertModel.is_deleted.is_(False),
                )
                .distinct()
                .order_by(ExpertModel.category)
                .all()
            )
            return [row[0] for row in rows]
