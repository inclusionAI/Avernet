"""SQLAlchemy models for work orders and recipient notifications."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderNotificationRecord,
    WorkOrderRecord,
    WorkOrderStatus,
)
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env


class WorkOrderModel(Base):
    __tablename__ = "ac_work_order"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    work_order_no = Column(String(64), nullable=False)
    biz_type = Column(String(64), nullable=False)
    biz_id = Column(String(128), nullable=False)
    biz_data = Column(Text, nullable=True)
    applicant_user_id = Column(String(256), nullable=False)
    apply_reason = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False)
    reviewer_user_id = Column(String(256), nullable=True)
    review_remark = Column(String(512), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "work_order_no", "env", name="uk_work_order_no_env"
        ),
        Index(
            "idx_work_order_applicant_status_env",
            "avernet_tenant",
            "applicant_user_id",
            "status",
            "env",
        ),
        Index(
            "idx_work_order_reviewer_status_env",
            "avernet_tenant",
            "reviewer_user_id",
            "status",
            "env",
        ),
        Index(
            "idx_work_order_biz_status_env",
            "avernet_tenant",
            "biz_type",
            "biz_id",
            "status",
            "env",
        ),
    )

    def to_record(self) -> WorkOrderRecord:
        return WorkOrderRecord(
            id=self.id,
            work_order_no=self.work_order_no,
            biz_type=self.biz_type,
            biz_id=self.biz_id,
            biz_data=self.biz_data,
            applicant_user_id=self.applicant_user_id,
            apply_reason=self.apply_reason,
            status=WorkOrderStatus(self.status),
            reviewer_user_id=self.reviewer_user_id,
            review_remark=self.review_remark,
            reviewed_at=self.reviewed_at,
            env=self.env,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )


class WorkOrderNotificationModel(Base):
    __tablename__ = "ac_work_order_notification"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    work_order_id = Column(AutoIncrementBigInteger, nullable=True)
    recipient_user_id = Column(String(256), nullable=False)
    notification_category = Column(String(32), nullable=False)
    event_type = Column(String(64), nullable=False)
    biz_type = Column(String(64), nullable=False)
    biz_id = Column(String(128), nullable=False)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, server_default="0")
    read_at = Column(DateTime, nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "idx_work_order_notification_recipient_read",
            "avernet_tenant",
            "recipient_user_id",
            "is_read",
            "env",
            "gmt_modified",
        ),
        Index(
            "idx_work_order_notification_work_order",
            "avernet_tenant",
            "work_order_id",
            "recipient_user_id",
            "env",
        ),
        Index(
            "idx_work_order_notification_biz",
            "avernet_tenant",
            "biz_type",
            "biz_id",
            "recipient_user_id",
            "env",
        ),
    )

    def to_record(self) -> WorkOrderNotificationRecord:
        return WorkOrderNotificationRecord(
            id=self.id,
            work_order_id=self.work_order_id,
            recipient_user_id=self.recipient_user_id,
            notification_category=NotificationCategory(self.notification_category),
            event_type=self.event_type,
            biz_type=self.biz_type,
            biz_id=self.biz_id,
            title=self.title,
            content=self.content,
            is_read=bool(self.is_read),
            read_at=self.read_at,
            env=self.env,
            gmt_created=self.gmt_created,
            gmt_modified=self.gmt_modified,
        )


class WorkOrderApproverModel(Base):
    __tablename__ = "ac_work_order_approver"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    work_order_id = Column(AutoIncrementBigInteger, nullable=False)
    approver_user_id = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, server_default="PENDING")
    review_remark = Column(String(512), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    env = Column(String(20), nullable=False, default=get_current_env)
    gmt_created = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "work_order_id", "approver_user_id", name="uk_work_order_approver"
        ),
    )


register_avernet_tenant_guard(WorkOrderModel)
register_avernet_tenant_guard(WorkOrderNotificationModel)
