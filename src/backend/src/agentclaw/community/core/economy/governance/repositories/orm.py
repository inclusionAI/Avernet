"""ORM models for the economy/governance module.

4 tables (class names carry ``Orm`` suffix to distinguish from domain models):
  - GovernanceNotificationOrm  (ac_governance_notify_log)     — 通知 + 反馈 + 工单生命周期
  - AuditLogOrm                (ac_governance_audit)          — 审计日志 (append-only)
  - WhitelistEntryOrm          (ac_bot_whitelist)             — 统一白名单
  - GovernanceTicketOrm        (ac_governance_task_record_daily) — 离线任务记录

领域模型见 domain.py; repo 用 from_orm/to_orm/apply_to 做翻译边界。

Note: ``ac_governance_analysis_daily`` is NOT stored online.  ODPS pipeline
analysis_daily is a process-level intermediate; only task_record_daily (the
scan + notification core fields) is upserted into the online DB.

Pipeline bookkeeping fields (task_create_key, analysis_ref,
estimated_improvement_range, baseline_tokens) are NOT stored — they
are ODPS internals that the online side never needs.

Env isolation: all 4 tables carry an ``env`` column (default ``get_current_env``)
so that dev / pre / prod sharing the same MySQL database do not read or
clobber each other's data. Unique constraints include ``env``.

Naming: ``dt_version`` (not ``dt``) — this is a data version marker,
semantically equivalent to the offline partition date but decoupled
from the offline naming convention.

Type conventions (研发规范 compliant):
  - DATETIME is prohibited; all temporal columns use TIMESTAMP.
  - FLOAT/DOUBLE is prohibited; saving_ratio uses NUMERIC(10,4).
  - AutoIncrementBigInteger PK (BigInteger on MySQL, Integer on SQLite).
  - UK constraints via __table_args__ with named name= parameter.
"""
from __future__ import annotations

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Column,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)

from agentclaw.community.plugin_api.models import AutoIncrementBigInteger, Base
from agentclaw.community.utils.env_utils import get_current_env

from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
    NotifyStatus,
)


class GovernanceNotificationOrm(Base):
    """通知事件流水 — 一次通知事件一行。

    角色: 创建时冻结当时的工单快照和实际发送正文; 创建后快照不可变;
    发送器仅允许更新投递态字段。

    字段分组:
      - 身份列: 创建时写入,不可变 (notification_id, ticket_id, bot_id, ...)
      - 冻结快照: 创建时写入,不可变 (dt_version, governance_decision, hit_dimensions, ...)
      - 投递态: 发送器可更新 (notify_status, sent_at, send_attempt_count, ...)
      - 类型列: 创建时写入,不可变 (notify_type, notify_source)
      - 旧工单字段: 物理保留,逻辑封锁(不参与任何业务读写)
      - 元信息: env, gmt_create, gmt_modified

    ⚠️ 逻辑封锁字段 (22列): 以下列物理保留但 ORM/business logic 不得读写:
      governance_status / governance_cycle_id / response / response_at /
      response_remark / response_source / close_reason / closed_at /
      cooldown_until / repair_deadline / mute_until / last_seen_at /
      latest_dt_version / data_refresh_count / latest_decision /
      consecutive_normal_days / feedback_payload / remind_count /
      remind_at / expire_at / actor_id / dry_run

    工单状态/生命周期由 task_record 承担, 不再从 notify_log 读取。
    """

    __tablename__ = "ac_governance_notify_log"

    # ── 身份列 (创建时写入,不可变) ──────────────────
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    notification_id = Column(String(64), nullable=False, unique=True)
    ticket_id = Column(String(64), nullable=True, comment="工单ID — 挂到 task_record")
    bot_id = Column(String(64), nullable=False)
    bot_name = Column(String(128))
    owner_id = Column(String(64), nullable=False)
    worker_id = Column(String(160), nullable=False)  # "{owner_id}:{bot_id}" — up to 64+1+64

    # ── 冻结快照 (创建时写入,不可变) ────────────────
    dt_version = Column(String(8), nullable=False)
    governance_decision = Column(String(32), nullable=False, comment="freeze 创建时 task_record.latest_decision, 非其 governance_decision")
    hit_dimensions = Column(String(512))
    hit_dimensions_count = Column(Integer)
    expected_token_saving = Column(BigInteger, nullable=True)
    saving_ratio = Column(Numeric(10, 4), nullable=True)
    notification_md = Column(Text, comment="渲染正文 — 创建时由 render_governance_notify 生成, 不可变")
    notification_structured = Column(Text, comment="原始 JSON 结构 (来自 task_record), 不可变")
    governance_max_priority = Column(String(8))

    # ── 投递态 (发送器可更新) ──────────────────────
    notify_status = Column(String(16), default="pending")  # pending/sending/sent/failed/cancelled
    sent_at = Column(TIMESTAMP, nullable=True)
    send_attempt_count = Column(Integer, default=0)
    last_send_at = Column(TIMESTAMP, nullable=True)
    last_send_error = Column(Text, nullable=True)
    external_message_id = Column(String(128), nullable=True, comment="每条独立, 不覆盖")
    notify_channel = Column(String(16), default="markdown")  # markdown/tc_card (actual channel, including degradation)

    # ── 类型列 (创建时写入,不可变) ──────────────────
    notify_type = Column(String(32), default="first_send")  # first_send / reminder
    notify_source = Column(String(32), default="offline_batch")  # offline_batch/online_cron/manual

    # ── 旧工单字段 (物理保留, 逻辑封锁) ─────────────
    # ⚠️ 以下 22 列禁止在 service/business logic 中读写。
    # 工单生命周期全部由 ac_governance_task_record_daily 承担。
    governance_status = Column(String(16), default="open")  # [SEALED]
    governance_cycle_id = Column(String(64), nullable=False)  # [SEALED]
    response = Column(String(32), nullable=True)  # [SEALED]
    response_at = Column(TIMESTAMP, nullable=True)  # [SEALED]
    response_remark = Column(Text, nullable=True)  # [SEALED]
    response_source = Column(String(32), nullable=True)  # [SEALED]
    close_reason = Column(String(32), nullable=True)  # [SEALED]
    closed_at = Column(TIMESTAMP, nullable=True)  # [SEALED]
    cooldown_until = Column(TIMESTAMP, nullable=True)  # [SEALED]
    repair_deadline = Column(TIMESTAMP, nullable=True)  # [SEALED]
    mute_until = Column(TIMESTAMP, nullable=True)  # [SEALED]
    last_seen_at = Column(TIMESTAMP, nullable=True)  # [SEALED]
    latest_dt_version = Column(String(8))  # [SEALED]
    data_refresh_count = Column(Integer, default=0)  # [SEALED]
    latest_decision = Column(String(32), nullable=True)  # [SEALED]
    consecutive_normal_days = Column(Integer, default=0)  # [SEALED]
    feedback_payload = Column(Text, nullable=True)  # [SEALED]
    remind_count = Column(Integer, default=0)  # [SEALED]
    remind_at = Column(TIMESTAMP, nullable=True)  # [SEALED]
    expire_at = Column(TIMESTAMP, nullable=True)  # [SEALED]
    actor_id = Column(String(64), nullable=True, comment="[SEALED] 原操作人ID")  # [SEALED]
    dry_run = Column(SmallInteger, default=0)  # [SEALED]

    # ── 元信息 ─────────────────────────────────────
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识: dev/pre/prod")
    gmt_create = Column(TIMESTAMP, nullable=False, default=func.now())
    gmt_modified = Column(TIMESTAMP, nullable=False, default=func.now(), onupdate=func.now())

    # ── 业务 property (设计文档 §2.3 定义的重命名) ──

    @property
    def decision_at_create(self) -> str:
        """governance_decision 的业务名 — 创建时冻结的决策。"""
        return self.governance_decision or "actionable"

    @property
    def triggered_dimensions(self) -> str | None:
        """hit_dimensions 的业务名。"""
        return self.hit_dimensions

    @property
    def severity(self) -> str | None:
        """governance_max_priority 的业务名。"""
        return self.governance_max_priority

    @property
    def estimated_saving_tokens(self) -> int | None:
        """expected_token_saving 的业务名。"""
        return self.expected_token_saving

    @property
    def delivery_status(self) -> str:
        """notify_status 的业务名。"""
        return self.notify_status or "pending"

    @property
    def channel(self) -> str:
        """notify_channel 的业务名。"""
        return self.notify_channel or "markdown"

    @property
    def is_pending(self) -> bool:
        """通知是否待发送。"""
        return self.notify_status == NotifyStatus.PENDING

    def to_dict(self) -> dict:
        """Convert to plain dict — safe to use after session closes.

        Time fields keep ``datetime`` type; see
        :meth:`GovernanceTicket.to_dict` for rationale.
        """
        return {
            "id": self.id,
            "notification_id": self.notification_id,
            "ticket_id": self.ticket_id,
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "owner_id": self.owner_id,
            "worker_id": self.worker_id,
            "dt_version": self.dt_version,
            "governance_decision": self.governance_decision,
            "hit_dimensions": self.hit_dimensions,
            "hit_dimensions_count": self.hit_dimensions_count,
            "expected_token_saving": self.expected_token_saving,
            "saving_ratio": float(self.saving_ratio) if self.saving_ratio else None,
            "notification_md": self.notification_md,
            "notification_structured": self.notification_structured,
            "governance_max_priority": self.governance_max_priority,
            "notify_status": self.notify_status,
            "sent_at": self.sent_at,
            "send_attempt_count": self.send_attempt_count,
            "last_send_at": self.last_send_at,
            "last_send_error": self.last_send_error,
            "external_message_id": self.external_message_id,
            "notify_channel": self.notify_channel,
            "notify_type": self.notify_type,
            "notify_source": self.notify_source,
            "governance_status": self.governance_status,
            "response": self.response,
            "close_reason": self.close_reason,
            "env": self.env,
            "gmt_create": self.gmt_create,
            "gmt_modified": self.gmt_modified,
        }

    __table_args__ = (
        # 旧 UK 降级为普通索引 (一个工单跨天多次发送)
        Index(
            "idx_econ_gov_notify_worker_dt_version",
            "worker_id", "dt_version", "env",
        ),
        # 新索引
        Index("idx_econ_gov_notify_ticket_id", "env", "ticket_id"),
        Index("idx_econ_gov_notify_notify_status", "env", "notify_status"),
        # 保留旧索引 (兼容旧查询 + 管理端)
        Index("idx_econ_gov_notify_status", "env", "governance_status"),
        Index("idx_econ_gov_notify_owner_status", "env", "owner_id", "governance_status"),
        Index("idx_econ_gov_notify_bot_owner", "env", "bot_id", "owner_id"),
        Index("idx_econ_gov_notify_delivery", "env", "governance_status", "notify_status"),
    )


class AuditLogOrm(Base):
    """Append-only audit trail for governance operations.

    Every scan run, user feedback, and admin action writes
    a row here. The ``run_id`` ties all audit rows from a single
    scan together (UUID4).

    ``notification_id`` links audit rows back to the specific
    GovernanceNotification row — essential for card callback traceability
    (which notification was clicked / responded to).
    """

    __tablename__ = "ac_governance_audit"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(64))
    notification_id = Column(String(64), nullable=True, comment="通知唯一ID — 回调审计追溯用")
    bot_id = Column(String(64))
    owner_id = Column(String(64))
    check_result = Column(String(32))  # actionable/observe/justified/skipped_whitelist/skipped_cooldown/auto_resolved/out_of_scope/errored
    governance_decision = Column(String(32))
    hit_dimensions = Column(String(512))
    expected_token_saving = Column(BigInteger, nullable=True)
    saving_ratio = Column(Numeric(10, 4), nullable=True)
    action_taken = Column(String(64))  # enqueued/whitelist_filtered/muted/cooldown_filtered/auto_resolved/mute_expired/out_of_scope/reminded/expired_unresolved/data_not_ready/error/user_resolved/admin_*
    source = Column(String(32), default="daily_scan")
    error_msg = Column(Text, nullable=True)
    actor_id = Column(String(64), nullable=True, comment="实际操作人ID — owner自操作=owner_id，admin代操作=admin_id，系统行为=NULL")
    server_host = Column(String(128), nullable=True, comment="处理服务器主机名")
    dry_run = Column(SmallInteger, default=0)
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识: dev/pre/prod")
    gmt_create = Column(TIMESTAMP, nullable=False, default=func.now())
    gmt_modified = Column(TIMESTAMP, nullable=False, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        """Convert to plain dict — safe to use after session closes."""
        return {
            "id": self.id,
            "run_id": self.run_id,
            "notification_id": self.notification_id,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "check_result": self.check_result,
            "governance_decision": self.governance_decision,
            "hit_dimensions": self.hit_dimensions,
            "expected_token_saving": self.expected_token_saving,
            "saving_ratio": float(self.saving_ratio) if self.saving_ratio else None,
            "action_taken": self.action_taken,
            "source": self.source,
            "error_msg": self.error_msg,
            "actor_id": self.actor_id,
            "server_host": self.server_host,
            "dry_run": self.dry_run,
            "env": self.env,
            "gmt_create": self.gmt_create,
            "gmt_modified": self.gmt_modified,
        }

    # ── 业务 property ──

    @property
    def action(self) -> str:
        """action_taken 的业务名 — 审计动作。"""
        return self.action_taken or "unknown"

    @property
    def created_at(self):
        """gmt_create 的业务名。"""
        return self.gmt_create

    __table_args__ = (
        # data-readiness check runs MAX(gmt_create) WHERE action_taken IN(...)
        # every scan; run_id groups a scan's audit rows for ops queries.
        # notification_id index for card callback audit traceability.
        Index("idx_econ_gov_audit_action_time", "env", "action_taken", "gmt_create"),
        Index("idx_econ_gov_audit_run", "env", "run_id"),
        Index("idx_econ_gov_audit_notification", "env", "notification_id"),
        Index("idx_econ_gov_audit_actor", "env", "actor_id"),
        Index("idx_econ_gov_audit_server", "env", "server_host"),
    )


class WhitelistEntryOrm(Base):
    """Unified whitelist table — ``whitelist_type`` discriminates usage.

    Governance uses ``whitelist_type='governance'``; dormant can use
    ``whitelist_type='dormant'`` in the future. The existing
    ``ac_bot_dormant_whitelist`` table is left untouched.
    """

    __tablename__ = "ac_bot_whitelist"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    bot_id = Column(String(64), nullable=False)
    owner_id = Column(String(64), nullable=False)
    whitelist_type = Column(String(32), nullable=False)  # governance / dormant (reserved)
    source = Column(String(64), default="manual")  # system / owner / admin / manual / card_callback / http_api / owner_feedback
    reason = Column(String(512))
    created_by = Column(String(64))
    expires_at = Column(TIMESTAMP, nullable=True)  # NULL = permanent
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识: dev/pre/prod")
    gmt_create = Column(TIMESTAMP, nullable=False, default=func.now())
    gmt_modified = Column(TIMESTAMP, nullable=False, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        """Convert to plain dict — safe to use after session closes."""
        return {
            "id": self.id,
            "bot_id": self.bot_id,
            "owner_id": self.owner_id,
            "whitelist_type": self.whitelist_type,
            "source": self.source,
            "reason": self.reason,
            "created_by": self.created_by,
            "expires_at": self.expires_at,
            "env": self.env,
            "gmt_create": self.gmt_create,
            "gmt_modified": self.gmt_modified,
        }

    # ── 业务 property ──

    @property
    def is_expired(self) -> bool:
        """白名单是否已过期 (None = 永久)。"""
        from datetime import datetime
        return self.expires_at is not None and self.expires_at < datetime.now()

    __table_args__ = (
        UniqueConstraint(
            "bot_id", "owner_id", "whitelist_type", "env",
            name="uk_econ_gov_wl_bot_owner_type",
        ),
        Index("idx_econ_gov_wl_type", "env", "whitelist_type"),
    )


class GovernanceTicketOrm(Base):
    """治理工单 — 一个 owner-bot 对最多一条 active 工单, 生命周期跨天稳定。

    角色: 工单身份 + 最新快照 + 生命周期状态 + 用户反馈 + 管理员审核 + 提醒调度。
    原地更新到最新态 (history 由 notify_log 的冻结快照保留)。
    closed 后释放 active_worker; 后续仍 actionable 则创建新工单 (new ticket_id)。

    旧列重定位 (不删, 含义调整):
      - governance_decision → initial_decision (INSERT后永不更新, §5.6)
      - dt_version → 工单当前所基于的最新数据版本
      - hit_dimensions / expected_token_saving / ... → 工单最新快照 (可随离线更新)
      - last_sync_at → 最近一次离线驱动时间

    UK 变更:
      - 旧 UK (worker_id, dt_version, env) → 降级为普通索引
      - 新 UK (env, ticket_id) — 工单稳定 UUID
      - 新 UK (env, active_worker) — 一 owner-bot 对一 active 兜底
    """

    __tablename__ = "ac_governance_task_record_daily"

    # ── 旧列 (重定位) ──────────────────────────────
    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    worker_id = Column(String(160), nullable=False)  # 工单身份: owner_id:bot_id (禁止 device worker_id)
    bot_id = Column(String(64))
    owner_id = Column(String(64), nullable=True, comment="负责人ID")
    dt_version = Column(String(8), nullable=False, comment="工单当前所基于的最新数据版本")
    governance_decision = Column(
        String(32),
        comment="[CAUTION] This is initial_decision, NOT the CSV governance_decision. "
        "CSV governance_decision maps to latest_decision. "
        "This column is frozen at INSERT time (always='actionable'), never updated (§5.6).",
    )
    bot_name = Column(String(128))
    hit_dimensions = Column(String(512), comment="最新快照")
    hit_dimensions_count = Column(Integer)
    governance_max_priority = Column(String(8))  # P0/P1
    expected_token_saving = Column(BigInteger, nullable=True)
    saving_ratio = Column(Numeric(10, 4), nullable=True)
    task_summary = Column(Text, nullable=True)
    notification_structured = Column(Text, nullable=True, comment="最新快照: 原始 JSON 结构")
    analysis_status = Column(String(32))
    last_sync_at = Column(TIMESTAMP, nullable=False, comment="最近一次离线驱动时间")
    env = Column(String(20), nullable=False, default=get_current_env, comment="环境标识: dev/pre/prod")
    gmt_create = Column(TIMESTAMP, nullable=False, default=func.now())
    gmt_modified = Column(TIMESTAMP, nullable=False, default=func.now(), onupdate=func.now())

    # ── 新增列 ──────────────────────────────────────
    ticket_id = Column(String(64), nullable=True, comment="工单稳定 UUID")
    active_worker = Column(String(160), nullable=True, comment="active=owner_id:bot_id; closed=NULL")
    governance_status = Column(String(16), default="open", comment="open/scheduled/waiting_review/closed")

    # --- 用户反馈 ---
    response = Column(String(32), nullable=True, comment="optimized/dispute/whitelist/need_time")
    response_at = Column(TIMESTAMP, nullable=True)
    response_remark = Column(Text, nullable=True)
    response_source = Column(String(32), nullable=True, comment="http_api/card_callback/admin_api")

    # --- 关闭 ---
    close_reason = Column(String(32), nullable=True)
    closed_at = Column(TIMESTAMP, nullable=True)
    cooldown_until = Column(TIMESTAMP, nullable=True)

    # --- 审核 ---
    review_reason = Column(String(32), nullable=True,
                           comment="user_optimized/user_disputed/user_whitelisted/admin_paused/schedule_due")
    review_decision = Column(String(32), nullable=True,
                             comment="approve_close/approve_whitelist/reject_for_reopen")
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(TIMESTAMP, nullable=True)
    review_remark = Column(Text, nullable=True)

    # --- 排期修复 ---
    repair_deadline = Column(TIMESTAMP, nullable=True, comment="修复截止日期 (need_time必填)")
    mute_until = Column(TIMESTAMP, nullable=True, comment="repair_deadline + cooldown_days")
    last_seen_at = Column(TIMESTAMP, nullable=True, comment="最近一次离线仍命中actionable的时间")

    # --- 恢复追踪 ---
    latest_decision = Column(
        String(32), nullable=True,
        comment="current_decision: 上传时刷新; 对应 CSV governance_decision; actionable/normal/unknown",
    )
    consecutive_normal_days = Column(
        Integer, default=0,
        comment="连续完整batch的 latest_decision=normal 天数",
    )
    last_decision_dt_version = Column(
        String(8), nullable=True,
        comment="最近一次 latest_decision 被完整 batch 更新的 dt_version (防重)",
    )

    # --- 提醒调度 ---
    remind_at = Column(TIMESTAMP, nullable=True, comment="下一次允许创建 reminder notify 的时间")
    remind_count = Column(Integer, default=0, comment="已成功发送的 reminder 次数")

    # --- 其它 ---
    feedback_payload = Column(Text, nullable=True, comment="结构化反馈 JSON")
    actor_id = Column(String(64), nullable=True, comment="实际操作人ID")
    delivery_status = Column(String(32), default="none", comment="最近通知投递状态: none/first_send:sent/reminder:failed/...")

    def to_dict(self) -> dict:
        """Convert to plain dict — safe to use after session closes.

        Follows the project-wide pattern (see BotModel.to_dict()).
        Time fields keep ``datetime`` type (not isoformat) because
        governance service layer does datetime comparisons
        (``cooldown_until > now`` etc.); API-layer serialization
        is the router's responsibility.

        NOTE: This method is retained for diagnostics/debug only.
        The hot path (Repo → Service → Router) passes ORM objects
        directly; ``to_dict()`` is NOT called on the hot path.
        """
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "worker_id": self.worker_id,
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "owner_id": self.owner_id,
            "dt_version": self.dt_version,
            "governance_decision": self.governance_decision,
            "hit_dimensions": self.hit_dimensions,
            "hit_dimensions_count": self.hit_dimensions_count,
            "governance_max_priority": self.governance_max_priority,
            "expected_token_saving": self.expected_token_saving,
            "saving_ratio": float(self.saving_ratio) if self.saving_ratio else None,
            "task_summary": self.task_summary,
            "notification_structured": self.notification_structured,
            "analysis_status": self.analysis_status,
            "active_worker": self.active_worker,
            "governance_status": self.governance_status,
            "response": self.response,
            "response_at": self.response_at,
            "response_remark": self.response_remark,
            "response_source": self.response_source,
            "close_reason": self.close_reason,
            "closed_at": self.closed_at,
            "cooldown_until": self.cooldown_until,
            "review_reason": self.review_reason,
            "review_decision": self.review_decision,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "review_remark": self.review_remark,
            "repair_deadline": self.repair_deadline,
            "mute_until": self.mute_until,
            "last_seen_at": self.last_seen_at,
            "latest_decision": self.latest_decision,
            "consecutive_normal_days": self.consecutive_normal_days,
            "last_decision_dt_version": self.last_decision_dt_version,
            "remind_at": self.remind_at,
            "remind_count": self.remind_count,
            "feedback_payload": self.feedback_payload,
            "actor_id": self.actor_id,
            "last_sync_at": self.last_sync_at,
            "env": self.env,
            "gmt_create": self.gmt_create,
            "gmt_modified": self.gmt_modified,
        }

    # ── 业务 property (设计文档 §2.3 定义的重命名) ──

    @property
    def initial_decision(self) -> str:
        """governance_decision 的业务名 — 永远 = 'actionable' (§5.6)。"""
        return self.governance_decision or "actionable"

    @property
    def current_decision(self) -> str | None:
        """latest_decision 的业务名 — 最新决策。"""
        return self.latest_decision

    @property
    def assignee(self) -> str | None:
        """active_worker 的业务名 — 谁持有这个工单。"""
        return self.active_worker

    @property
    def severity(self) -> str | None:
        """governance_max_priority 的业务名 — 严重等级。"""
        return self.governance_max_priority

    @property
    def triggered_dimensions(self) -> str | None:
        """hit_dimensions 的业务名 — 触发了哪些治理维度。"""
        return self.hit_dimensions

    @property
    def estimated_saving_tokens(self) -> int | None:
        """expected_token_saving 的业务名。"""
        return self.expected_token_saving

    @property
    def user_feedback(self) -> str | None:
        """response 的业务名 — 用户反馈类型。"""
        return self.response

    @property
    def feedback_at(self):
        """response_at 的业务名 — 反馈时间。"""
        return self.response_at

    @property
    def feedback_remark(self) -> str | None:
        """response_remark 的业务名 — 反馈备注。"""
        return self.response_remark

    @property
    def feedback_source(self) -> str | None:
        """response_source 的业务名 — 反馈来源。"""
        return self.response_source

    @property
    def resume_at(self):
        """mute_until 的业务名 — 何时恢复。"""
        return self.mute_until

    # ── 业务方法 ──

    @property
    def is_open(self) -> bool:
        """工单是否处于 open 状态。"""
        return self.governance_status == GovernanceStatus.OPEN

    @property
    def is_active(self) -> bool:
        """工单是否活跃（尚未关闭）。"""
        return self.governance_status in (
            GovernanceStatus.OPEN, GovernanceStatus.SCHEDULED, GovernanceStatus.WAITING_REVIEW,
        )

    @property
    def is_actionable(self) -> bool:
        """当前决策是否仍需处理 — 决定是否发送/提醒。"""
        return self.latest_decision == "actionable"

    @property
    def has_feedback(self) -> bool:
        """用户是否已反馈。"""
        return self.response is not None

    def can_accept_feedback(self) -> bool:
        """§7.4.1: 仅 open + 未反馈 才接受。"""
        return self.governance_status == GovernanceStatus.OPEN and self.response is None

    def compute_cooldown_until(self, cooldown_days: int):
        """计算冷却截止时间。"""
        from datetime import datetime, timedelta
        return datetime.now() + timedelta(days=cooldown_days)

    def compute_resume_at(self, repair_deadline, cooldown_days: int):
        """need_time 反馈: 修复截止 + 冷却天数 = 恢复时间。"""
        from datetime import timedelta
        return repair_deadline + timedelta(days=cooldown_days)

    __table_args__ = (
        # 旧 UK 降级为普通索引 (一个 worker 可有多条历史工单)
        Index(
            "idx_econ_gov_taskrec_worker_dt_version",
            "worker_id", "dt_version", "env",
        ),
        # 新 UK
        UniqueConstraint("env", "ticket_id", name="uk_econ_gov_taskrec_ticket_id"),
        UniqueConstraint("env", "active_worker", name="uk_econ_gov_taskrec_active_worker"),
        # 旧索引 (保留)
        Index("idx_econ_gov_taskrec_dt_decision", "env", "dt_version", "governance_decision", "analysis_status"),
        Index("idx_econ_gov_taskrec_owner_dt", "env", "owner_id", "dt_version"),
        # 新索引
        Index("idx_econ_gov_taskrec_status", "env", "governance_status"),
        Index("idx_econ_gov_taskrec_owner_status", "env", "owner_id", "governance_status"),
        Index("idx_econ_gov_taskrec_remind_at", "env", "remind_at"),
        Index("idx_econ_gov_taskrec_status_mute", "env", "governance_status", "mute_until"),
    )
