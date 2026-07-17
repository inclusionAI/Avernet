"""领域模型 — WhitelistEntry 白名单条目。

与 GovernanceNotification / GovernanceTicket 同级,按实体拆文件。
对应 ORM: WhitelistEntryOrm (ac_bot_whitelist)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WhitelistEntry:
    """白名单领域模型 — service 层唯一接触的对象。

    对应 ORM: WhitelistEntryOrm (ac_bot_whitelist)。
    frozen: 白名单条目创建后不可变,修改走删除+重建。
    sealed 列 id/env 不在本模型上;gmt_create/gmt_modified 作为只读元信息
    (创建/修改时间)开放,供列表序列化展示。
    """

    bot_id: str
    owner_id: str
    whitelist_type: str
    source: str
    reason: str
    created_by: str
    expires_at: datetime | None
    gmt_create: datetime | None = None
    gmt_modified: datetime | None = None

    # ── 业务 property ──────────────────────────────

    @property
    def is_expired(self) -> bool:
        """是否已过期 — expires_at 为 None 表示永不过期。"""
        return self.expires_at is not None and self.expires_at < datetime.now()

    # ── 翻译边界 ─────────────────────────────────────

    @classmethod
    def from_orm(cls, obj: object) -> WhitelistEntry:
        """读翻译: ORM → 领域模型。id/env sealed 列不映射;时间元信息映射。"""
        return cls(
            bot_id=obj.bot_id,
            owner_id=obj.owner_id,
            whitelist_type=obj.whitelist_type,
            source=obj.source or "manual",
            reason=obj.reason or "",
            created_by=obj.created_by or "",
            expires_at=obj.expires_at,
            gmt_create=getattr(obj, "gmt_create", None),
            gmt_modified=getattr(obj, "gmt_modified", None),
        )

    def to_orm(self, row: object | None = None) -> object:
        """写翻译: 领域模型 → ORM。sealed 列由 repo 方法内部填充。"""
        from agentclaw.community.core.economy.governance.repositories.orm import (
            WhitelistEntryOrm,
        )
        row = row or WhitelistEntryOrm()
        row.bot_id = self.bot_id
        row.owner_id = self.owner_id
        row.whitelist_type = self.whitelist_type
        row.source = self.source
        row.reason = self.reason
        row.created_by = self.created_by
        row.expires_at = self.expires_at
        return row

    def to_dict(self) -> dict:
        """API 序列化 — router 直接 ``data=[e.to_dict() for e in items]``。"""
        return {
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "whitelist_type": self.whitelist_type,
            "source": self.source,
            "reason": self.reason,
            "created_by": self.created_by,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }