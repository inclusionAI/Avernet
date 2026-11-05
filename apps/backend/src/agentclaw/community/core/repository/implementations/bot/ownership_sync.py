"""Read current Owner facts from Bots only, independent of manager eligibility.

Each call binds its explicit tenant and opens a short session. No collaborator
or Space state is read, no facts are cached, and no sync state is written.
"""

from __future__ import annotations

import json

from injector import inject
from sqlalchemy.exc import SQLAlchemyError

from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    BOT_NOT_FOUND_CODE,
    SnapshotUnavailable,
    SyncKey,
)
from agentclaw.community.kernel.bot_ownership_sync import OwnershipFacts
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class SqlOwnershipSyncReadRepository(OwnershipSyncReadRepository):
    """Scoped, read-only SQL implementation of the ownership facts contract."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def read_facts(self, key: SyncKey) -> OwnershipFacts:
        with avernet_tenant_scope(key.avernet_tenant):
            try:
                with self._db.orm_session() as session:
                    bot = (
                        session.query(BotModel)
                        .filter(
                            BotModel.id == key.bot_pk,
                            BotModel.avernet_tenant == key.avernet_tenant,
                            BotModel.env == key.env,
                            BotModel.is_delete == 0,
                        )
                        .one_or_none()
                    )
                    if bot is None:
                        raise SnapshotUnavailable(BOT_NOT_FOUND_CODE)
                    try:
                        ext = json.loads(bot.ext or "{}")
                    except ValueError as exc:
                        raise SnapshotUnavailable("invalid_bot_ext") from exc
                    if not isinstance(ext, dict):
                        raise SnapshotUnavailable("invalid_bot_ext")
                    return OwnershipFacts(
                        key,
                        bot.bot_id,
                        bot.entity_id,
                        bot.owner_id,
                        bot_type=bot.bot_type,
                        bcn_provider_bot_ref=ext.get("bcn_provider_bot_ref"),
                    )

            except SQLAlchemyError as exc:
                raise SnapshotUnavailable("read_error") from exc

    def page_keys(
        self, *, avernet_tenant: str, env: str, after_pk: int, limit: int
    ) -> tuple[SyncKey, ...]:
        bounded = max(0, min(limit, 100))
        if bounded == 0:
            return ()
        with avernet_tenant_scope(avernet_tenant):
            try:
                with self._db.orm_session() as session:
                    rows = (
                        session.query(BotModel.id)
                        .filter(
                            BotModel.avernet_tenant == avernet_tenant,
                            BotModel.env == env,
                            BotModel.is_delete == 0,
                            BotModel.id > after_pk,
                        )
                        .order_by(BotModel.id.asc())
                        .limit(bounded)
                        .all()
                    )
                    return tuple(
                        SyncKey(avernet_tenant, env, bot_pk) for (bot_pk,) in rows
                    )
            except SQLAlchemyError as exc:
                raise SnapshotUnavailable("read_error") from exc
