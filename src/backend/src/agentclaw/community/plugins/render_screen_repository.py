"""Unified RenderScreen repository (prod ZDAS + local SQLite).

One ORM implementation behind ``RenderScreenRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local),
collapsing the previous raw-SQL/ORM twins so CI exercises the prod path
too.

Duplicate-name rejection lives in the service layer
(``RenderScreenService.create_render_screen``), not here: production
``ac_bot_render_screen`` has no unique index on (bot_id, name, env) — see
specs/2026-05-17-unified-repository-round-2/
ddl-parity-ac_bot_render_screen.md — so a DB constraint violation can
never occur on prod and the repo does not attempt to handle one. The old
prod twin's ``pymysql.IntegrityError`` catch was likewise dead on prod.
"""
from injector import inject
from sqlalchemy import func

from agentclaw.community.core.bot_management.render_screen.models import (
    RenderScreenRecord,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


def _model_to_record(row) -> RenderScreenRecord:
    return RenderScreenRecord(
        id=row.id,
        bot_id=row.bot_id,
        owner_id=row.owner_id,
        name=row.name,
        cdn_url=row.cdn_url,
        env=row.env,
        creator_id=row.creator_id,
        is_delete=row.is_delete,
        gmt_create=row.gmt_create,
        gmt_modified=row.gmt_modified,
    )


class RenderScreenRepository:
    """Unified ``RenderScreenRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.bot_management.render_screen.sqlite_models import (
            RenderScreenModel,
        )

        self._db = db
        self._Model = RenderScreenModel

    def insert(
        self,
        *,
        bot_id: str,
        owner_id: str,
        name: str,
        cdn_url: str,
        creator_id: str,
    ) -> int:
        with self._db.orm_session() as db:
            row = self._Model(
                bot_id=bot_id,
                owner_id=owner_id,
                name=name,
                cdn_url=cdn_url,
                env=get_current_env(),
                creator_id=creator_id,
                is_delete=0,
                gmt_create=func.now(),
                gmt_modified=func.now(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            logger.info("[RenderScreen] insert id=%s", row.id)
            return row.id

    def list_by_bot_id(self, *, bot_id: str, owner_id: str) -> list[RenderScreenRecord]:
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Model)
                .filter(
                    self._Model.bot_id == bot_id,
                    self._Model.owner_id == owner_id,
                    self._Model.is_delete == 0,
                    self._Model.env == get_current_env(),
                )
                .order_by(self._Model.gmt_create.desc())
                .all()
            )
            return [_model_to_record(r) for r in rows]

    def get_by_id(self, record_id: int) -> RenderScreenRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(self._Model)
                .filter(
                    self._Model.id == record_id,
                    self._Model.is_delete == 0,
                    self._Model.env == get_current_env(),
                )
                .first()
            )
            return _model_to_record(row) if row else None

    def update_by_id(
        self, *, record_id: int, name: str, cdn_url: str
    ) -> None:
        # Single conditional UPDATE (atomic on both backends, incl. prod
        # AUTOCOMMIT) that keeps the is_delete=0 / env guard in the WHERE
        # — mirrors the prior prod twin. The fetch-then-mutate ORM pattern
        # dropped that guard (UPDATE was by pk), so a row soft-deleted by
        # a concurrent delete_by_id between the SELECT and the write could
        # be silently resurrected. Same shape as delete_by_id below.
        with self._db.orm_session() as db:
            db.query(self._Model).filter(
                self._Model.id == record_id,
                self._Model.is_delete == 0,
                self._Model.env == get_current_env(),
            ).update(
                {
                    self._Model.name: name,
                    self._Model.cdn_url: cdn_url,
                    self._Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def delete_by_id(self, *, record_id: int) -> None:
        with self._db.orm_session() as db:
            db.query(self._Model).filter(
                self._Model.id == record_id,
                self._Model.env == get_current_env(),
            ).update(
                {
                    self._Model.is_delete: 1,
                    self._Model.gmt_modified: func.now(),
                },
                synchronize_session="fetch",
            )
