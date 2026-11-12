"""BBS 公开讨论产品编排(乙案:组合协议层,自持编不开通用北向合同)。

依赖面(spec §3.1,架构测试执法):L4 对象与 ``creation_payload`` 纯函数、
G1 结构授权与窄规则、G4 payload 合同、L6/L5 词汇、三处基建端口
(仓储协议、MethodStore、L5 顶层投影)——**零 ``service_api``**。

编排要点(spec §3.3 权威转写):
- 发帖:创建上下文(payload=``creation_payload`` 六键全量创作事实)+ 紧随
  追加首帖正文 public Message(expected_version=1)+ L5 顶层 ACTIVE
  MethodBinding 投影;两步非原子,正文失败整体报错、调用方重试新建
  (创建不幂等,spec 裁决 D);
- 回帖:跨产品面拦截(非 ``bbs_public_discussion`` 一律 404)、产品域
  断言"parent 必须指向 Message 事件"(``task.created`` 骨架在流内能过
  仓储存在性,必须在此拒)、G1 窄规则(任意已认证 actor 可发 public
  Message)与 ``expected_version`` CAS 由仓储把守,冲突 409 透传;
- 回帖快照推进恒等(裁决 J:公开讨论无 G4 状态机,Message 事件不改
  Task status)。
"""

from __future__ import annotations

from dataclasses import dataclass

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    VISIBILITY_PUBLIC,
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    validate_event_body,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.creation_facts import (
    creation_payload,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l6_products.profiles import (
    ProductProfile,
    WorkflowKind,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCollabRepositoryProtocol,
    CreateContextRecordRequest,
    EventAppendedRecord,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.products.bbs_public_discussion.views import (
    DiscussionDetailBody,
    DiscussionDetailView,
    DiscussionReplyItem,
    DiscussionSummaryView,
)
from agentclaw.community.core.task.context_collab.products.service_api import (
    CreateDiscussionCommand,
    DiscussionCreatedView,
    ReplyCreatedView,
    ReplyDiscussionCommand,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    _now,
    current_axes,
    project_created_context,
)

# 与既有创建路径逐字(通用服务 CREATION_* 常量语义,产品自持同值)。
_CREATION_EVENT_KIND = "task.created"
_CREATION_PRIMITIVE_TYPE = "Task"
# 公开讨论的发言事件形态(G4 validate_event_body 合同)。
_POSTED_EVENT_KIND = "user_interaction.posted"
_MESSAGE_PRIMITIVE = "Message"


def _required_content(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"discussion {field_name} must be a non-empty string, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class _PublicMessageIntent:
    """G1 ``check_append`` 的 duck-typed 载体(不引 wire 合同类型)。"""

    event_kind: str
    primitive_type: str
    actor_ref: object
    parent_event: str | None
    visibility: str
    payload: dict


def _identity_updater(snapshot):
    """恒等快照推进(裁决 J)。"""
    return snapshot


def _principal_id(ref) -> str:
    """读投影作者标识;外部部分行(NULL actor 列)读为空串。"""
    return ref.principal_id if ref is not None else ""


def _content_of(event) -> str:
    """读投影正文;payload 为 NULL 的部分行读为空串(读面容错,不 422)。"""
    payload = event.payload
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str):
            return content
    return ""


class BbsPublicDiscussionService:
    """公开讨论四用例编排:发帖/回帖(T3),列表/详情(T4)。"""

    @inject
    def __init__(
        self,
        collab_repo: ContextCollabRepositoryProtocol,
        authorizer: ContextCollabAuthorizerProtocol,
        method_store: MethodStoreProtocol,
    ) -> None:
        self._repo = collab_repo
        self._authorizer = authorizer
        self._method_store = method_store

    # ------------------------------------------------------------- 发帖

    def create_discussion(
        self, command: CreateDiscussionCommand, context: ActorContext
    ) -> DiscussionCreatedView:
        title = _required_content(command.title, "title")
        body = _required_content(command.body, "body")
        spec = ContextSpec(
            space=command.space,
            principal_ref=context.actor,
            method_kind=MethodKind.OPEN_GROUP.value,
            executor_ref=None,
            objective=title,
            resource_refs=tuple(command.resource_refs),
            product_kind=ProductProfile.BBS_PUBLIC_DISCUSSION.value,
            workflow_kind=WorkflowKind.BBS_PUBLIC_DISCUSSION.value,
        )
        self._authorizer.check_create(spec, context)

        record = self._repo.create_context(
            CreateContextRecordRequest(
                context_spec=spec,
                initial_event=InitialEventDraft(
                    event_kind=_CREATION_EVENT_KIND,
                    primitive_type=_CREATION_PRIMITIVE_TYPE,
                    actor_ref=context.actor,
                    payload=creation_payload(spec, title),
                ),
            )
        )
        # L5 顶层 ACTIVE MethodBinding 投影(store 写失败如实上抛)。
        project_created_context(
            self._method_store,
            current_axes(),
            record.collet.collet_id,
            spec,
            context,
            now=_now(),
        )
        appended = self._append_public_message(
            record.collet.collet_id,
            record.collet,
            spec,
            actor_ref=context.actor,
            content=body,
            parent_event_id=None,
            context=context,
            expected_version=1,
        )
        return DiscussionCreatedView(
            collet_id=record.collet.collet_id,
            title=title,
            body_event_id=appended.event.event_id,
            context_version=appended.collet.version,
        )

    # ------------------------------------------------------------- 回帖

    def reply_discussion(
        self, command: ReplyDiscussionCommand, context: ActorContext
    ) -> ReplyCreatedView:
        content = _required_content(command.content, "content")
        record = self._repo.get_current_status(command.collab_context_id)
        if record is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {command.collab_context_id!r} not found"
            )
        # 跨产品面拦截:非本产品 profile 的 Collet 一律按不存在处理(404),
        # 语义不得漂到别人的上下文上。
        if (
            record.context_spec.product_kind
            != ProductProfile.BBS_PUBLIC_DISCUSSION.value
        ):
            raise ContextCollabNotFoundError(
                f"collaboration context {command.collab_context_id!r} is not "
                "a bbs public discussion"
            )
        self._assert_message_parent(record.events, command.parent_event_id)

        appended = self._append_public_message(
            command.collab_context_id,
            record.collet,
            record.context_spec,
            actor_ref=context.actor,
            content=content,
            parent_event_id=command.parent_event_id,
            context=context,
            expected_version=record.collet.version,
        )
        return ReplyCreatedView(
            collab_context_id=command.collab_context_id,
            event_id=appended.event.event_id,
            parent_event_id=appended.event.parent_event,
            context_version=appended.collet.version,
        )

    # ------------------------------------------------------------- 私有

    @staticmethod
    def _assert_message_parent(
        events: list[EventRecord], parent_event_id: str | None
    ) -> None:
        """产品域断言:parent 必须指向 Message 事件(骨架事件不可当 parent)。

        存在性由仓储把守(不存在/跨流会被拒);此处只拦"在流内但不是
        用户发言"的骨架事件——把回帖挂到 ``task.created`` 上会把骨架吸进
        读投影的回复树。
        """
        if parent_event_id is None:
            return
        target = next(
            (event for event in events if event.event_id == parent_event_id),
            None,
        )
        if target is not None and target.primitive_type != _MESSAGE_PRIMITIVE:
            raise ContextCollabValidationError(
                f"parent_event {parent_event_id!r} is not a Message event; "
                "replies may only reply to user postings"
            )

    def _append_public_message(
        self,
        collab_context_id: str,
        collet,
        spec: ContextSpec,
        *,
        actor_ref,
        content: str,
        parent_event_id: str | None,
        context: ActorContext,
        expected_version: int,
    ) -> EventAppendedRecord:
        intent = _PublicMessageIntent(
            event_kind=_POSTED_EVENT_KIND,
            primitive_type=_MESSAGE_PRIMITIVE,
            actor_ref=actor_ref,
            parent_event=parent_event_id,
            visibility=VISIBILITY_PUBLIC,
            payload={"content": content},
        )
        validate_event_body(
            event_kind=intent.event_kind,
            primitive_type=intent.primitive_type,
            visibility=intent.visibility,
            payload=intent.payload,
        )
        self._authorizer.check_append(collet, spec, intent, context)
        return self._repo.append_event(
            collab_context_id,
            AppendEventRecordRequest(
                expected_version=expected_version,
                event_kind=intent.event_kind,
                primitive_type=intent.primitive_type,
                actor_ref=actor_ref,
                parent_event=intent.parent_event,
                visibility=intent.visibility,
                payload=intent.payload,
                snapshot_updater=_identity_updater,
            ),
        )

    # ------------------------------------------------------------- 读(T4)

    def list_discussions(self) -> list[DiscussionSummaryView]:
        """Query.search/filter:按 product profile 列帖,gmt_create 倒序。

        ``message_count`` 由仓储单 GROUP BY 聚合(无事件 id 取 0);
        title 过渡期伴随读(裁决 2,自 spec JSON;P3 随过滤源升级)。
        """
        rows = self._repo.list_collets_by_product(
            ProductProfile.BBS_PUBLIC_DISCUSSION.value
        )
        counts = self._repo.count_message_events(
            [collet.collet_id for collet, _, _ in rows]
        )
        return [
            DiscussionSummaryView(
                collet_id=collet.collet_id,
                title=spec.objective,
                initiator_id=collet.initiator_ref.principal_id,
                message_count=counts.get(collet.collet_id, 0),
                created_at=gmt_create,
            )
            for collet, spec, gmt_create in rows
        ]

    def get_discussion(self, collab_context_id: str) -> DiscussionDetailView:
        """Query.project:一次给齐标题/正文/全部公开回帖。

        title 读源合同(创建事实 spec §3 裁决 1):初始事件 payload 的
        ``objective``,NULL-payload 外部行回退伴随 ``context_spec.objective``
        (P3 撤列时移除)。body 与 replies **只投影 public**(读可见性
        裁决 F;visibility NULL 按 private 处理,事件 envelope 语义);
        孤儿帖 ``body=None``(发帖两步窗口态,合法领域可空)。
        """
        record = self._repo.get_current_status(collab_context_id)
        if record is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        if (
            record.context_spec.product_kind
            != ProductProfile.BBS_PUBLIC_DISCUSSION.value
        ):
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} is not "
                "a bbs public discussion"
            )

        title = record.context_spec.objective
        for event in record.events:
            if event.event_kind == _CREATION_EVENT_KIND:
                # title 新主:初始事件 payload 的 objective(创建事实 spec
                # §3 裁决 1);NULL-payload/缺键外部行回退伴随 spec.objective。
                payload = event.payload
                if isinstance(payload, dict):
                    objective = payload.get("objective")
                    if isinstance(objective, str) and objective.strip():
                        title = objective
                break
        messages = [
            event
            for event in record.events
            if event.primitive_type == _MESSAGE_PRIMITIVE
            and event.visibility == VISIBILITY_PUBLIC
        ]
        # body 取首条 public Message;孤儿帖(窗口态/外部行)为 None。
        body_event = messages[0] if messages else None
        body_view = (
            DiscussionDetailBody(
                event_id=body_event.event_id,
                content=_content_of(body_event),
                author_id=_principal_id(body_event.actor_ref),
                occurred_at=body_event.occurred_at,
            )
            if body_event is not None
            else None
        )
        replies = [
            DiscussionReplyItem(
                event_id=event.event_id,
                content=_content_of(event),
                author_id=_principal_id(event.actor_ref),
                parent_event_id=event.parent_event,
                occurred_at=event.occurred_at,
            )
            for event in messages[1:]
        ]
        return DiscussionDetailView(
            collet_id=collab_context_id,
            title=title,
            state=record.collet.state.value,
            body=body_view,
            replies=replies,
        )
