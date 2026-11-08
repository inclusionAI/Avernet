"""Bot writes serialize against owner-handoff's aggregate row lock."""

from __future__ import annotations
import json
from typing import Any, Dict, Optional
from sqlalchemy import func
from agentclaw.community.core.bot_management.owner_handoff.guard import (
    assert_bot_mutable,
)
from agentclaw.community.log import get_logger

logger = get_logger()

_UPDATE_ALLOWED_FIELDS = (
    "bot_name",
    "bot_desc",
    "owner_name",
    "modifier_id",
    "share_policy",
    "status",
    "binding_id",
    "device_id",
    "active_engine",
    "public",
    "ext",
    "bot_type",
)

_JSON_FIELDS = ("share_policy", "ext")


class BotMutationRepositoryMixin:
    def _assert_mutable_rows(self, db, *, bot_id, owner_id, env=None):
        rows = (
            db.query(self.Model)
            .filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(env=env),
            )
            .with_for_update()
            .all()
        )
        for bot in rows:
            assert_bot_mutable({"ext": bot.ext})

    def update_by_owner(
        self, bot_id: str, owner_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        values: dict = {}
        for field in _UPDATE_ALLOWED_FIELDS:
            if field in update_data:
                v = update_data[field]
                if field in _JSON_FIELDS and v is not None:
                    v = json.dumps(v)
                values[getattr(self.Model, field)] = v

        if not values:
            return self.get_by_id_and_owner(bot_id, owner_id)

        values[self.Model.gmt_modified] = func.now()
        with self._db.orm_session() as db:
            self._assert_mutable_rows(db, bot_id=bot_id, owner_id=owner_id)
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .update(values, synchronize_session=False)
            )
        if rowcount == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id)

    def update_space_by_owner(
        self,
        *,
        bot_id: str,
        owner_id: str,
        space_id: int,
        env: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update only ``space_id`` without widening the generic allowlist.

        The generic ``update_by_owner`` intentionally mirrors the historical
        production allowlist. Space assignment is a newer structured mutation,
        so it gets an explicit repository operation instead of silently changing
        the semantics of every existing caller.
        """
        with self._db.orm_session() as db:
            self._assert_mutable_rows(db, bot_id=bot_id, owner_id=owner_id, env=env)
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(env=env),
                )
                .update(
                    {
                        self.Model.space_id: space_id,
                        self.Model.modifier_id: owner_id,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
        if rowcount == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id, env=env)

    def compare_and_set_ext(
        self,
        *,
        bot_id: str,
        owner_id: str,
        expected_ext: Optional[Dict[str, Any]],
        ext: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Whole-column ``ext`` CAS scoped by owner, env, and live row."""
        expected_json = (
            json.dumps(expected_ext, ensure_ascii=False)
            if expected_ext is not None
            else None
        )
        ext_json = json.dumps(ext, ensure_ascii=False)
        with self._db.orm_session() as db:
            self._assert_mutable_rows(db, bot_id=bot_id, owner_id=owner_id)
            query = db.query(self.Model).filter(
                self.Model.bot_id == bot_id,
                self.Model.owner_id == owner_id,
                self.Model.is_delete == 0,
                self._env(),
            )
            if expected_json is None:
                query = query.filter(self.Model.ext.is_(None))
            else:
                query = query.filter(
                    self.Model.ext.in_((expected_json, json.dumps(expected_ext)))
                )
            affected = query.update(
                {
                    self.Model.ext: ext_json,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )
        logger.info(
            "[bot.ext_cas] operation=compare_and_set_ext bot_id=%s affected_rows=%s "
            "expected_ext_is_null=%s serialization=unicode",
            bot_id,
            affected,
            expected_ext is None,
        )
        if affected == 0:
            return None
        return self.get_by_id_and_owner(bot_id, owner_id)

    def soft_delete_by_owner(self, bot_id: str, owner_id: str) -> bool:
        with self._db.orm_session() as db:
            self._assert_mutable_rows(db, bot_id=bot_id, owner_id=owner_id)
            rowcount = (
                db.query(self.Model)
                .filter(
                    self.Model.bot_id == bot_id,
                    self.Model.owner_id == owner_id,
                    self.Model.is_delete == 0,
                    self._env(),
                )
                .update(
                    {
                        self.Model.is_delete: 1,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return rowcount > 0
