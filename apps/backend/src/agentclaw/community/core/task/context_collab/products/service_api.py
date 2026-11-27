"""BBS 公开讨论产品层合同(模块级交付面;spec §2a 操作族映射表)。

产品自持合同,不建在通用北向 wire 合同(``service_api``)之上——
乙案依赖矩阵(spec §3.1)由架构测试机械执法。每个合同可在 ACCS-104 §3
找到操作族映射:

==========================  =====================================
产品合同                     ACCS 操作族
==========================  =====================================
``create_discussion``       Context.create + Message.publish(首帖正文)
``reply_discussion``        Message.publish(含 reply 因果)
``list_discussions``        Query.search / filter
``get_discussion``          Query.project
==========================  =====================================

读视图(``DiscussionSummaryView`` / ``DiscussionDetailView``)由
``bbs_public_discussion.views`` 定义(T4);读两方法在协议面先钉签名,
实现随后到。`T | None` 只用于合法领域可空:回帖的 ``parent_event_id``
(None=回复主题帖)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)

if TYPE_CHECKING:  # 读视图 T4 落地,协议签名先钉(运行时不求值)。
    from agentclaw.community.core.task.context_collab.products.bbs_public_discussion.views import (
        DiscussionDetailView,
        DiscussionSummaryView,
    )


@dataclass
class CreateDiscussionCommand:
    """发帖命令:标题进聚合 objective,正文进首帖 Message 事件。"""

    title: str
    body: str
    space: SpaceRef
    resource_refs: list[ResourceRef] = field(default_factory=list)


@dataclass
class ReplyDiscussionCommand:
    """回帖命令;``parent_event_id`` 为 None 表示回复主题帖。"""

    collab_context_id: str
    content: str
    parent_event_id: str | None = None


@dataclass
class DiscussionCreatedView:
    """发帖结果:只取协议原生字段,不带时间(时间属读投影面)。"""

    collet_id: str
    title: str
    body_event_id: str
    context_version: int


@dataclass
class ReplyCreatedView:
    """回帖结果:事件身份与并发版本。"""

    collab_context_id: str
    event_id: str
    parent_event_id: str | None
    context_version: int


@runtime_checkable
class BbsPublicDiscussionServiceProtocol(Protocol):
    """公开讨论四用例协议;模块级交付面(P1 无 HTTP,spec §5)。"""

    def create_discussion(
        self, command: CreateDiscussionCommand, context: ActorContext
    ) -> DiscussionCreatedView: ...

    def reply_discussion(
        self, command: ReplyDiscussionCommand, context: ActorContext
    ) -> ReplyCreatedView: ...

    def list_discussions(self) -> "list[DiscussionSummaryView]": ...

    def get_discussion(self, collab_context_id: str) -> "DiscussionDetailView": ...
