"""SQLAlchemy ORM model for ``ac_acli_operation``.

One row per acli add, remove or cancel. It records what a task-queue task
cannot: which task an operation is (the API answers by ``operation_id``,
while a task handler is never told its task id) and which engine operation the
task started. Status is not here; it is the task's.

Registered on ``agentclaw.community.core.base.Base`` so the local-mode
``create_all`` bootstrap emits the table; the side-effect import lives in
``core/schema.py``. The prod DDL is ``../sql/2026_10_21_acli_operation.sql``.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.acli_clis.operations import AcliAction, OperationKind
from agentclaw.community.core.base import Base
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY";
# BigInteger renders as "BIGINT" there.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


@dataclass(frozen=True, slots=True)
class AcliOperationRecord:
    """One ``ac_acli_operation`` row."""

    operation_id: str
    task_id: int
    kind: OperationKind
    action: AcliAction
    cli_code: str
    bot_pk: int
    stage: PublishStage
    #: The operation a cancel targets; ``None`` for an add or remove.
    target_operation_id: str | None = None
    #: The engine operation the task started; ``None`` until the engine accepted it.
    engine_operation_id: str | None = None


class AcliOperationModel(Base):
    __tablename__ = "ac_acli_operation"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识")
    operation_id = Column(String(32), nullable=False, comment="操作 ID（API 返回）")
    task_id = Column(BigInteger, nullable=False, comment="ac_task_queue.id")
    kind = Column(String(16), nullable=False, comment="operation / cancel")
    action = Column(String(16), nullable=False, comment="install / uninstall")
    cli_code = Column(String(256), nullable=False, comment="acli 市场名")
    bot_pk = Column(BigInteger, nullable=False, comment="ac_bots.id")
    stage = Column(String(16), nullable=False, comment="draft / verify / online")
    target_operation_id = Column(String(32), nullable=True, comment="取消的目标操作 ID")
    engine_operation_id = Column(String(32), nullable=True, comment="引擎操作 ID")
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间"
    )

    __table_args__ = (
        UniqueConstraint("operation_id", name="uk_operation_id"),
        # The latest operation of a runtime, per kind: the live-operations read.
        Index("idx_env_bot_stage_kind", "env", "bot_pk", "stage", "kind", "id"),
    )

    def to_record(self) -> AcliOperationRecord:
        return AcliOperationRecord(
            operation_id=self.operation_id,
            task_id=int(self.task_id),
            kind=OperationKind(self.kind),
            action=AcliAction(self.action),
            cli_code=self.cli_code,
            bot_pk=int(self.bot_pk),
            stage=PublishStage(self.stage),
            target_operation_id=self.target_operation_id,
            engine_operation_id=self.engine_operation_id,
        )


__all__ = ["AcliOperationModel", "AcliOperationRecord"]
