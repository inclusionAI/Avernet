"""Bot Publish 模型定义。

包含：
- Pydantic 业务模型（用于 API 层）
- SQLAlchemy ORM 模型（用于数据库持久化）
"""
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, Integer, String, DateTime, Text, Index
from sqlalchemy.sql import func

from agentclaw.community.plugin_api.models import Base
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments a column declared exactly ``INTEGER PRIMARY KEY``;
# BigInteger renders as BIGINT and breaks autoincrement there. with_variant keeps
# BIGINT on MySQL/OceanBase and uses INTEGER on SQLite (mirrors ac_task_queue).
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


# ============================================================================
# 枚举定义
# ============================================================================
class PublishStatus(StrEnum):
    """发布状态枚举"""
    DRAFT = "draft"              # 草稿
    BUILDING = "building"        # 构建中
    BUILT = "built"              # 已构建
    VALIDATE_PUB = "validate_pub"  # 验证发布
    VALIDATING = "validating"     # 验证中
    ONLINE_PUB = "online_pub"     # 上线发布
    SUCCESS = "success"           # 发布成功
    UPGRADED = "upgraded"         # 已升级
    RELEASED = "released"         # 已下线
    FAILED = "failed"             # 发布失败

    @classmethod
    def all(cls) -> list:
        """返回所有状态值列表"""
        return [s.value for s in cls]


def select_stage_bind_id(binding_info: dict, status: str):
    """根据发布状态从 ``ext.binding`` 选择 stage ``bind_id``。

    必须以 status 字段为准来选择，不能假设“有 online 就一定是 online 态”：

    - ``validating``：取 ``verify``
    - ``success``：取 ``online``
    - 其他状态：优先 ``online``，其次 ``verify``

    ``bind_id`` 可能为 0，调用方应以 ``if not bind_id`` 判定“无 stage binding”
    （binding 主键恒 ≥1，故 0 永远不是真实 binding）。
    """
    if status == PublishStatus.VALIDATING.value:
        return binding_info.get("verify")
    if status == PublishStatus.SUCCESS.value:
        return binding_info.get("online")
    # 其他状态优先使用 online，其次 verify
    return binding_info.get("online") or binding_info.get("verify")


# ============================================================================
# Pydantic 业务模型
# ============================================================================
class BotPublishRecord(BaseModel):
    """Bot发布记录业务模型。

    对应 ac_bot_publish 表。
    """
    id: Optional[int] = Field(default=None, description="主键ID")
    source_bot_pk: int = Field(..., description="ac_bots 表主键")
    source_bot_id: str = Field(..., description="源 bot_id，区分数字分身/个人")
    publish_bot_id: str = Field(..., description="发布后的 bot_id = source_bot_id + pub + version")
    name: str = Field(..., description="bot名称")
    description: Optional[str] = Field(default=None, description="描述")
    owner_id: str = Field(..., description="工号")
    owner_name: Optional[str] = Field(default=None, description="花名")
    status: str = Field(default=PublishStatus.DRAFT, description="发布状态")
    version: Optional[int] = Field(default=None, description="版本号")
    last_pub_id: int = Field(default=0, description="上次发布成功id")
    env: str = Field(default="dev", description="环境: prod/pre/dev")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展信息")
    permission_owner: str = Field(..., description="权限归属")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "source_bot_pk": self.source_bot_pk,
            "source_bot_id": self.source_bot_id,
            "publish_bot_id": self.publish_bot_id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "status": self.status,
            "version": self.version,
            "last_pub_id": self.last_pub_id,
            "env": self.env,
            "ext": self.ext,
            "permission_owner": self.permission_owner,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }


class BotPublishCreate(BaseModel):
    """创建发布记录请求模型"""
    source_bot_pk: int = Field(..., description="ac_bots 表主键")
    source_bot_id: str = Field(..., description="源 bot_id")
    publish_bot_id: str = Field(..., description="发布后的 bot_id")
    name: str = Field(..., description="bot名称")
    description: Optional[str] = Field(default=None, description="描述")
    owner_id: str = Field(..., description="工号")
    owner_name: Optional[str] = Field(default=None, description="花名")
    status: str = Field(default=PublishStatus.DRAFT, description="发布状态")
    version: Optional[int] = Field(default=None, description="版本号")
    last_pub_id: int = Field(default=0, description="上次发布成功id")
    env: str = Field(default="dev", description="环境")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展信息")
    permission_owner: str = Field(..., description="权限归属")


class BotPublishUpdate(BaseModel):
    """更新发布记录请求模型"""
    status: Optional[str] = Field(default=None, description="发布状态")
    version: Optional[int] = Field(default=None, description="版本号")
    last_pub_id: Optional[int] = Field(default=None, description="上次发布成功id")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="扩展信息")


# ============================================================================
# SQLAlchemy ORM Model
# ============================================================================
class BotPublishModel(Base):
    """SQLAlchemy ORM model for ac_bot_publish table."""
    __tablename__ = "ac_bot_publish"

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)

    # 业务字段
    source_bot_pk = Column(Integer, nullable=False, comment="ac_bots 表主键")
    source_bot_id = Column(String(512), nullable=False, comment="源 bot_id")
    publish_bot_id = Column(String(1024), nullable=False, comment="发布后的 bot_id")
    name = Column(String(2048), nullable=False, comment="bot名称")
    description = Column(String(4096), nullable=True, comment="描述")
    owner_id = Column(String(128), nullable=False, comment="工号")
    owner_name = Column(String(128), nullable=True, comment="花名")
    status = Column(String(128), nullable=False, default=PublishStatus.DRAFT, comment="发布状态")
    version = Column(Integer, nullable=True, comment="版本号")
    last_pub_id = Column(Integer, nullable=False, default=0, comment="上次发布成功id")
    env = Column(String(64), nullable=False, default=get_current_env, comment="环境")
    ext = Column(Text, nullable=True, comment="扩展信息 JSON")
    permission_owner = Column(String(64), nullable=False, comment="权限归属")

    # 时间戳
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间")

    __table_args__ = (
        Index(
            "idx_pb_owner_status",
            "publish_bot_id",
            "owner_id",
            "status",
        ),
        Index("idx_owner_name", "owner_name"),
        Index("idx_name", "name"),
        Index("idx_last_pub_id", "last_pub_id"),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "source_bot_pk": self.source_bot_pk,
            "source_bot_id": self.source_bot_id,
            "publish_bot_id": self.publish_bot_id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "owner_name": self.owner_name,
            "status": self.status,
            "version": self.version,
            "last_pub_id": self.last_pub_id,
            "env": self.env,
            "ext": json.loads(self.ext) if self.ext else None,
            "permission_owner": self.permission_owner,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modified": self.gmt_modified.isoformat() if self.gmt_modified else None,
        }

    def to_record(self) -> BotPublishRecord:
        """Convert to Pydantic record."""
        return BotPublishRecord(
            id=self.id,
            source_bot_pk=self.source_bot_pk,
            source_bot_id=self.source_bot_id,
            publish_bot_id=self.publish_bot_id,
            name=self.name,
            description=self.description,
            owner_id=self.owner_id,
            owner_name=self.owner_name,
            status=self.status,
            version=self.version,
            last_pub_id=self.last_pub_id,
            env=self.env,
            ext=json.loads(self.ext) if self.ext else None,
            permission_owner=self.permission_owner,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )


# ============================================================================
# Publish operation ledger (ac_publish_operation)
# ============================================================================
# Each row is one *logical* publish operation against the BaaS layer (create,
# upgrade, restart, scale, offline-destroy, rollback deploy, eval, approval).
# Intent is persisted BEFORE the BaaS call; the returned workflow id and step
# results are persisted after. A crash-resume of the operation reads its ledger
# row and picks up at the first incomplete step instead of re-issuing. See
# specs/2026-07-15-publish-service-idempotency/plan.md.


class PublishOperationKind(StrEnum):
    """The distinct BaaS-mutating operations tracked in the ledger."""

    VERIFY_FIRST_RELEASE = "verify_first_release"
    VERIFY_UPGRADE = "verify_upgrade"
    ONLINE_FIRST_RELEASE = "online_first_release"
    ONLINE_UPGRADE = "online_upgrade"
    RESTART = "restart"
    SCALE = "scale"
    OFFLINE_DESTROY = "offline_destroy"
    ROLLBACK_DEPLOY = "rollback_deploy"
    DESTROY_STAGE = "destroy_stage"
    EVAL_PUBLISH = "eval_publish"
    EVAL_TEARDOWN = "eval_teardown"
    APPROVAL_CREATE = "approval_create"

    @classmethod
    def creation_kinds(cls) -> set["PublishOperationKind"]:
        """Kinds that create a *new* BaaS bot (no existing bot_uuid to query
        workflows under) — the bounded-orphan window applies only to these."""
        return {
            cls.VERIFY_FIRST_RELEASE,
            cls.ONLINE_FIRST_RELEASE,
            cls.EVAL_PUBLISH,
        }


class PublishOperationState(StrEnum):
    """Ledger step state. Forward: PENDING -> ID_RECORDED -> COMPLETED.

    ``PENDING`` — intent persisted, BaaS call not yet confirmed recorded.
    ``ID_RECORDED`` — the BaaS workflow id is persisted against the row.
    ``COMPLETED`` — the operation's follow-up steps finished.
    ``FAILED`` — a step failed terminally (mirrors the domain failure).
    ``ABANDONED`` — superseded by a fresh attempt / new-version publish.
    """

    PENDING = "pending"
    ID_RECORDED = "id_recorded"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"

    @classmethod
    def terminal(cls) -> set["PublishOperationState"]:
        return {cls.COMPLETED, cls.FAILED, cls.ABANDONED}


class PublishOperationRecord(BaseModel):
    """Business model for one ``ac_publish_operation`` row."""

    id: Optional[int] = Field(default=None, description="主键ID")
    publish_id: int = Field(..., description="agentclaw 发布单 id（eval_teardown 按 bot_uuid 时为 0）")
    operation_kind: str = Field(..., description="PublishOperationKind")
    stage: str = Field(default="", description="verify/online/eval/''")
    attempt: int = Field(default=1, description="重试代数；abandon 后 +1 开新行")
    state: str = Field(default=PublishOperationState.PENDING, description="PublishOperationState")
    request_id: str = Field(..., description="确定性请求 id（关联/审计用，非幂等键）")
    bot_uuid: str = Field(default="", description="目标 BaaS bot（创建类在拿到前为空）")
    baas_publish_id: Optional[int] = Field(default=None, description="BaaS 工作流 id，ID_RECORDED 时写入")
    params: Optional[Dict[str, Any]] = Field(default=None, description="重发所需入参")
    result: Optional[Dict[str, Any]] = Field(default=None, description="步骤结果（binding id / draft id / puid 等）")
    last_error: Optional[str] = Field(default=None, description="最后一次步骤失败信息")
    operator: str = Field(default="", description="操作人")
    env: str = Field(default="dev", description="环境: prod/pre/dev")
    gmt_create: datetime = Field(default_factory=datetime.now, description="创建时间")
    gmt_modified: datetime = Field(default_factory=datetime.now, description="修改时间")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class PublishOperationModel(Base):
    """SQLAlchemy ORM model for the ``ac_publish_operation`` table."""

    __tablename__ = "ac_publish_operation"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )

    publish_id = Column(AutoIncrementBigInteger, nullable=False, comment="agentclaw 发布单 id")
    operation_kind = Column(String(64), nullable=False, comment="PublishOperationKind")
    stage = Column(String(16), nullable=False, default="", comment="verify/online/eval/''")
    attempt = Column(Integer, nullable=False, default=1, comment="重试代数")
    state = Column(
        String(32),
        nullable=False,
        default=PublishOperationState.PENDING.value,
        comment="pending/id_recorded/completed/failed/abandoned",
    )
    request_id = Column(String(128), nullable=False, comment="确定性请求 id（关联/审计）")
    bot_uuid = Column(String(128), nullable=False, default="", comment="目标 BaaS bot")
    baas_publish_id = Column(AutoIncrementBigInteger, nullable=True, comment="BaaS 工作流 id")
    params = Column(Text, nullable=True, comment="重发入参 JSON")
    result = Column(Text, nullable=True, comment="步骤结果 JSON")
    last_error = Column(Text, nullable=True, comment="最后失败信息")
    operator = Column(String(128), nullable=False, default="", comment="操作人")

    env = Column(String(32), nullable=False, default=get_current_env, comment="prod/pre/dev")
    gmt_create = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="修改时间"
    )

    __table_args__ = (
        # The operation identity a re-run uses to find-or-create its intent row.
        Index(
            "uk_op",
            "publish_id",
            "operation_kind",
            "stage",
            "attempt",
            unique=True,
        ),
        # "any in-flight op for this record?" scans.
        Index("idx_pub_state", "publish_id", "state"),
        # Orphan sweeps / adopt-by-query differencing by target bot.
        Index("idx_bot", "bot_uuid"),
    )

    def to_record(self) -> PublishOperationRecord:
        """Convert to the Pydantic business model."""
        return PublishOperationRecord(
            id=self.id,
            publish_id=self.publish_id,
            operation_kind=self.operation_kind,
            stage=self.stage,
            attempt=self.attempt,
            state=self.state,
            request_id=self.request_id,
            bot_uuid=self.bot_uuid,
            baas_publish_id=self.baas_publish_id,
            params=json.loads(self.params) if self.params else None,
            result=json.loads(self.result) if self.result else None,
            last_error=self.last_error,
            operator=self.operator,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
