"""Unified Channel repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``ChannelRepository`` Protocol. The
only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so
this single body runs unchanged on OceanBase (prod) and SQLite (local),
collapsing the previous raw-SQL/ORM twins so CI exercises the prod path
too.

Behavior matches the prior prod twin exactly: ``get_by_id`` has no
``deleted`` guard (prod didn't filter it); ``update_by_id`` /
``update_status_by_id`` / ``delete_by_id`` are single atomic UPDATEs
(one statement, atomic under prod AUTOCOMMIT) with DB-side
``gmt_modified = NOW()`` via ``func.now()`` — never assigned in business
code. ``gmt_create`` is the column ``server_default``. DDL parity:
``specs/2026-05-18-unified-repository-round-3-session-1``.
"""
from __future__ import annotations

import json
from typing import Any

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.chat import ChannelRepository as ChannelRepositoryProtocol


logger = get_logger()


def _row_to_record(row) -> ChannelRecord | None:
    if row is None:
        return None
    try:
        config = (
            json.loads(row.config)
            if isinstance(row.config, str)
            else (row.config or {})
        )
    except (json.JSONDecodeError, TypeError):
        config = {}
    return ChannelRecord(
        id=row.id,
        type=row.type,
        description=row.description,
        identity_id=row.identity_id,
        bind_bot_id=row.bind_bot_id,
        config=config,
        status=row.status,
        deleted=row.deleted,
        gmt_create=row.gmt_create,
        gmt_modified=row.gmt_modified,
        env=row.env if row.env is not None else get_current_env(),
        stage=row.stage,
    )


class ChannelRepository(
    ChannelRepositoryProtocol,
):
    """Unified ``ChannelRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.plugin_api.models import ChannelConfig

        self._db = db
        self._ChannelConfig = ChannelConfig

    def insert_channel(
        self,
        *,
        type: str,
        description: str | None,
        identity_id: str,
        bind_bot_id: str,
        config: dict[str, Any],
        status: str,
        stage: str | None = None,
    ) -> int:
        # gmt_create/gmt_modified are DB server defaults (func.now()).
        with self._db.orm_session() as db:
            row = self._ChannelConfig(
                type=type,
                description=description,
                identity_id=identity_id,
                bind_bot_id=bind_bot_id,
                config=json.dumps(config, ensure_ascii=False),
                status=status,
                deleted=0,
                env=get_current_env(),
                stage=stage,
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            logger.info("[insert_channel] inserted id=%s", row.id)
            return row.id

    def get_by_type_and_identity_ids(
        self,
        *,
        type: str,
        identity_ids: list[str],
        bind_bot_id: str,
    ) -> list[ChannelRecord]:
        if not identity_ids:
            raise ValueError("identity_ids cannot be empty")
        with self._db.orm_session() as db:
            rows = (
                db.query(self._ChannelConfig)
                .filter(
                    self._ChannelConfig.type == type,
                    self._ChannelConfig.identity_id.in_(identity_ids),
                    self._ChannelConfig.bind_bot_id == bind_bot_id,
                    self._ChannelConfig.deleted == 0,
                    self._ChannelConfig.env == get_current_env(),
                )
                .all()
            )
            return [r for r in (_row_to_record(x) for x in rows) if r is not None]

    def get_by_id(self, channel_id: int) -> ChannelRecord | None:
        # No deleted guard — matches the prior prod twin exactly.
        with self._db.orm_session() as db:
            row = (
                db.query(self._ChannelConfig)
                .filter(
                    self._ChannelConfig.id == channel_id,
                    self._ChannelConfig.env == get_current_env(),
                )
                .first()
            )
            return _row_to_record(row)

    def update_by_id(
        self,
        *,
        channel_id: int,
        type: str,
        description: str | None,
        identity_id: str,
        bind_bot_id: str,
        config: dict[str, Any],
        status: str,
        stage: str | None = None,
    ) -> None:
        # Single atomic blind UPDATE WHERE id AND env (no deleted guard) —
        # matches the prior prod twin's one-statement UPDATE.
        with self._db.orm_session() as db:
            db.query(self._ChannelConfig).filter(
                self._ChannelConfig.id == channel_id,
                self._ChannelConfig.env == get_current_env(),
            ).update(
                {
                    self._ChannelConfig.type: type,
                    self._ChannelConfig.description: description,
                    self._ChannelConfig.identity_id: identity_id,
                    self._ChannelConfig.bind_bot_id: bind_bot_id,
                    self._ChannelConfig.config: json.dumps(
                        config, ensure_ascii=False
                    ),
                    self._ChannelConfig.status: status,
                    self._ChannelConfig.stage: stage,
                    self._ChannelConfig.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def update_status_by_id(self, *, channel_id: int, status: str) -> None:
        with self._db.orm_session() as db:
            db.query(self._ChannelConfig).filter(
                self._ChannelConfig.id == channel_id,
                self._ChannelConfig.env == get_current_env(),
            ).update(
                {
                    self._ChannelConfig.status: status,
                    self._ChannelConfig.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def delete_by_id(self, *, channel_id: int) -> None:
        # Logical delete (deleted=1) — single atomic UPDATE.
        with self._db.orm_session() as db:
            db.query(self._ChannelConfig).filter(
                self._ChannelConfig.id == channel_id,
                self._ChannelConfig.env == get_current_env(),
            ).update(
                {
                    self._ChannelConfig.deleted: 1,
                    self._ChannelConfig.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
