"""SQLAlchemy ORM model for ``ac_bot_copy``.

Spec §7.5.3 (verbatim DDL lives in ``sql/2026_10_26_bot_copy.sql``). Side-effect
import on ``core.base.Base.metadata`` so ``create_all`` builds the table in
singlebox/tests; prod OceanBase applies the OOB SQL once. The model mirrors
``ac_bot_config_manifest/repository/models.py``'s conventions: ``AutoIncrementBigInteger``,
``_binary_string`` for byte-precise id columns, ``register_avernet_tenant_guard``,
``server_default`` on tenant so create_all emits the same DEFAULT the OOB DDL does.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus
from agentclaw.community.core.task_queue.repository.models import (
    AutoIncrementBigInteger,
    _binary_string,
)
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env


class AcBotCopyModel(Base):
    """SQLAlchemy ORM model for ``ac_bot_copy`` (bot-copy operation ledger)."""

    __tablename__ = "ac_bot_copy"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )

    # FE polling correlation id + the copy state reference's identifier.
    # utf8mb4_bin so two operations whose ids differ only by case cannot share a
    # row under MySQL/OceanBase's default case-insensitive collation.
    operation_id = Column(
        _binary_string(64),
        nullable=False,
        comment="FE polling correlation id (UNIQUE per env)",
    )
    source_bot_id = Column(_binary_string(64), nullable=False, comment="源 ac_bots.bot_id")
    source_owner_id = Column(String(1024), nullable=False, comment="源 ac_bots.owner_id")
    dst_bot_id = Column(_binary_string(64), nullable=False, comment="新建 dst ac_bots.bot_id")
    dst_owner_id = Column(String(1024), nullable=False, comment="dst ac_bots.owner_id")
    operator_id = Column(
        String(1024),
        nullable=False,
        comment="trigger actor; collaborator 场景可与 dst_owner_id 不等",
    )

    # varchar(24), no DB CHECK — the enum is code-enforced (types.BotCopyStatus).
    # Under derive-on-read this is written INITIAL at insert and not CAS-driven;
    # the column is retained for spec alignment + the retry CAS's terminal set.
    status = Column(
        String(24),
        nullable=False,
        default="INITIAL",
        comment="INITIAL/CREATED/CONFIG_COPIED/FILE_SYNCED/BOT_STARTED/CAPSET_SYNCED/CONVERGED/AUTH_PENDING/FAILED",
    )

    request_detail = Column(Text, comment="JSON: request-side params blob (excludes + future params)")
    progress_ext = Column(Text, comment="JSON: step intermediate state for resync")
    retries = Column(Integer, nullable=False, default=0, comment="operator retry attempt count")
    last_retry_at = Column(
        DateTime, nullable=True, comment="operator retry CAS throttle stamp (last_retry_at < now-60s)"
    )
    last_error = Column(Text, comment="terminal FAILED last exception summary")

    env = Column(
        String(20),
        nullable=False,
        default=get_current_env,
        comment="prod/pre/dev/singlebox; all queries filter",
    )
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")

    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="修改时间",
    )

    __table_args__ = (
        UniqueConstraint("env", "operation_id", name="uk_env_operation_id"),
        Index("idx_env_dst_bot_id", "env", "dst_bot_id"),
        Index("idx_env_status_last_retry_at", "env", "status", "last_retry_at"),
        Index("idx_env_operator_status", "env", "operator_id", "status"),
    )

    def to_record(self) -> BotCopyRow:
        """Convert the ORM row to its business record."""
        return BotCopyRow(
            id=self.id,
            operation_id=self.operation_id,
            source_bot_id=self.source_bot_id,
            source_owner_id=self.source_owner_id,
            dst_bot_id=self.dst_bot_id,
            dst_owner_id=self.dst_owner_id,
            operator_id=self.operator_id,
            status=BotCopyStatus(self.status),
            request_detail=_loads_json(self.request_detail),
            progress_ext=_loads_json(self.progress_ext),
            retries=self.retries,
            last_retry_at=self.last_retry_at,
            last_error=self.last_error,
            env=self.env,
            avernet_tenant=self.avernet_tenant,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


def _loads_json(text: str | None) -> dict | None:
    """Parse a JSON Text column, ``None`` when the column is NULL or empty."""
    import json

    if not text:
        return None
    return json.loads(text)


# Confines every read to the request's tenant and stamps it on insert.
register_avernet_tenant_guard(AcBotCopyModel)
