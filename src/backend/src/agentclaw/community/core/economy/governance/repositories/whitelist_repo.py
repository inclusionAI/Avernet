"""Governance whitelist repository — ``ac_bot_whitelist``.

Single-point operations for the unified whitelist table.
Governance uses ``whitelist_type='governance'``.

All methods return domain models or primitives — never ORM objects or raw dicts.
Follows the DatabasePlugin self-managed session pattern: each method opens
its own ``orm_session()``.  Env is resolved internally via ``get_current_env()``.
"""
from __future__ import annotations

from datetime import datetime

from injector import inject
from sqlalchemy import or_

from agentclaw.community.core.economy.governance.domain.whitelist import WhitelistEntry
from agentclaw.community.core.economy.governance.repositories.orm import WhitelistEntryOrm
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

log = get_logger(__name__)


class GovernanceWhitelistRepository:
    """Manage governance whitelist entries in ``ac_bot_whitelist``."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def is_whitelisted(
        self,
        bot_id: str,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
    ) -> bool:
        """点查: (bot_id, owner_id) 是否在有效白名单中。

        有效 = whitelist_type 匹配 + env 匹配 + 未过期。

        Args:
            bot_id: Bot ID。
            owner_id: 负责人 ID。
            whitelist_type: 白名单类型(默认 ``governance``)。

        Returns:
            True 表示在有效白名单中, False 表示不在。
        """
        _env = get_current_env()
        now = datetime.now()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            exists = (
                s.query(WhitelistEntryOrm.id)
                .filter(
                    WhitelistEntryOrm.bot_id == bot_id,
                    WhitelistEntryOrm.owner_id == owner_id,
                    WhitelistEntryOrm.whitelist_type == whitelist_type,
                    WhitelistEntryOrm.env == _env,
                    or_(
                        WhitelistEntryOrm.expires_at.is_(None),
                        WhitelistEntryOrm.expires_at > now,
                    ),
                )
                .first()
            )
            return exists is not None

    def add(
        self,
        *,
        bot_id: str,
        owner_id: str,
        created_by: str,
        whitelist_type: str = "governance",
        source: str = "manual",
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> WhitelistEntry:
        """添加单条白名单。幂等: UK 冲突时返回已有条目。

        Args:
            bot_id: Bot ID。
            owner_id: 负责人 ID。
            created_by: 操作人 ID。
            whitelist_type: 白名单类型(默认 ``governance``)。
            source: 来源(manual / owner / admin / system / emergency)。
            reason: 加白原因。
            expires_at: 过期时间(None 表示永不过期)。

        Returns:
            WhitelistEntry 领域模型。
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            # 幂等: 查已有
            existing = (
                s.query(WhitelistEntryOrm)
                .filter(
                    WhitelistEntryOrm.bot_id == bot_id,
                    WhitelistEntryOrm.owner_id == owner_id,
                    WhitelistEntryOrm.whitelist_type == whitelist_type,
                    WhitelistEntryOrm.env == _env,
                )
                .first()
            )
            if existing:
                log.debug(
                    "[GovernanceWhitelist] add skipped (exists): bot_id=%s, owner_id=%s",
                    bot_id, owner_id,
                )
                return WhitelistEntry.from_orm(existing)

            row = WhitelistEntryOrm(
                bot_id=bot_id,
                owner_id=owner_id,
                whitelist_type=whitelist_type,
                source=source,
                reason=reason[:500] if reason else "",
                created_by=created_by,
                expires_at=expires_at,
            )
            # Explicitly set env (ORM default won't fire when constructor
            # doesn't mention the column).
            row.env = _env
            s.add(row)
            try:
                s.commit()
            except Exception:
                log.exception("[GovernanceWhitelist] add commit failed")
                s.rollback()
                raise

            log.info(
                "[GovernanceWhitelist] add: bot_id=%s, owner_id=%s, type=%s, env=%s",
                bot_id, owner_id, whitelist_type, _env,
            )
            return WhitelistEntry.from_orm(row)

    def remove(
        self,
        *,
        bot_id: str,
        owner_id: str,
        whitelist_type: str = "governance",
    ) -> bool:
        """删除单条白名单。

        Args:
            bot_id: Bot ID。
            owner_id: 负责人 ID。
            whitelist_type: 白名单类型(默认 ``governance``)。

        Returns:
            True=已删除, False=不存在。
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            row = (
                s.query(WhitelistEntryOrm)
                .filter(
                    WhitelistEntryOrm.bot_id == bot_id,
                    WhitelistEntryOrm.owner_id == owner_id,
                    WhitelistEntryOrm.whitelist_type == whitelist_type,
                    WhitelistEntryOrm.env == _env,
                )
                .first()
            )
            if not row:
                return False

            s.delete(row)
            try:
                s.commit()
            except Exception:
                log.exception("[GovernanceWhitelist] remove commit failed")
                s.rollback()
                raise

            log.info(
                "[GovernanceWhitelist] remove: bot_id=%s, owner_id=%s, type=%s, env=%s",
                bot_id, owner_id, whitelist_type, _env,
            )
            return True

    def list_by_owner(
        self,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[WhitelistEntry]:
        """按 owner_id 分页查询白名单条目。

        Args:
            owner_id: 负责人 ID(必填)。
            whitelist_type: 白名单类型(默认 ``governance``)。
            limit: 分页大小。
            offset: 偏移量。

        Returns:
            WhitelistEntry 领域模型列表。
        """
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            rows = (
                s.query(WhitelistEntryOrm)
                .filter(
                    WhitelistEntryOrm.whitelist_type == whitelist_type,
                    WhitelistEntryOrm.env == _env,
                    WhitelistEntryOrm.owner_id == owner_id,
                )
                .order_by(WhitelistEntryOrm.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [WhitelistEntry.from_orm(r) for r in rows]

    def count_by_type(
        self,
        *,
        whitelist_type: str = "governance",
    ) -> int:
        """Count whitelist entries of a given type (self-managed session)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            s.expire_on_commit = False
            return (
                s.query(WhitelistEntryOrm)
                .filter(
                    WhitelistEntryOrm.whitelist_type == whitelist_type,
                    WhitelistEntryOrm.env == _env,
                )
                .count()
            )

    @staticmethod
    def _parse_expires_at(value: object) -> datetime | None:
        """Parse expires_at from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None