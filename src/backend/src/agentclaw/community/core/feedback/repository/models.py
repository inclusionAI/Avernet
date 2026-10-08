"""ORM model for the general feedback table.

Feedback records the reporter, the product module the feedback is about, and
the content. Unlike forum Topics, rows carry no opaque string business id: the
auto-increment primary key ``id`` is the stable identifier returned to callers,
since feedback is write-once and is never addressed per-item on the surface.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.feedback.models import FeedbackRecord
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class FeedbackModel(Base):
    __tablename__ = "ac_feedback"

    id = Column(
        AutoIncrementBigInteger,
        primary_key=True,
        autoincrement=True,
        nullable=False,
    )
    reporter_id = Column(String(256), nullable=False)
    module = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "idx_feedback_module",
            "avernet_tenant",
            "env",
            "module",
            "gmt_create",
            "id",
        ),
        Index(
            "idx_feedback_reporter",
            "avernet_tenant",
            "env",
            "reporter_id",
            "gmt_create",
        ),
        Index(
            "idx_feedback_list",
            "avernet_tenant",
            "env",
            "gmt_create",
            "id",
        ),
    )

    def to_record(self) -> FeedbackRecord:
        return FeedbackRecord(
            id=self.id,
            reporter_id=self.reporter_id,
            module=self.module,
            content=self.content,
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )


register_avernet_tenant_guard(FeedbackModel)
