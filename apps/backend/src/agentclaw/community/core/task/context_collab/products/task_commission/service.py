"""任务代办产品编排(乙案:组合协议层,自持编不开通用北向合同)。

依赖面(spec §3.1,架构测试执法)与公开讨论同矩阵;差异三处编排:
- 发布:``method_kind=Open``(顶层单值,裁决 B)、
  ``executor_ref=AgentRef("bot", assignee)``、
  ``workflow_kind=task_commission_goal``;
- Work 六动作:**时序门**(裁决 J,``TASK_COMMISSION_PRECONDITIONS`` 唯一
  权威表,越序/终态后在事件落库之前被拒)→ **提交权门**(裁决 K,
  submit 仅 executor 值匹配)→ G1 双路径(窄规则对 task.* 天然不适用,
  P1 闭协作,裁决 C)→ 快照推进 = ``resolve_workflow_status`` 结果
  (非恒等,区别于公开讨论裁决 J')——机器是唯一 status 裁决者;
- 外 profile / collet 不存在一律 404(语义不漂别人的 Collet)。
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
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.workflow_resolution import (
    TASK_COMMISSION_PRECONDITIONS,
    resolve_workflow_status,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ContextSpec,
    CurrentSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.creation_facts import (
    creation_payload,
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
)
from agentclaw.community.core.task.context_collab.products.task_commission.views import (
    TaskCommissionDetailView,
    TaskCommissionSummaryView,
)
from agentclaw.community.core.task.context_collab.products.task_commission.dispatch import (
    LoggingTaskDispatch,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    DispatchedTaskInfo,
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskCommissionCreatedView,
    TaskAdvancedView,
    TaskDispatchPort,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    _now,
    current_axes,
    project_created_context,
)

# 与通用创建路径逐字的初始事件形态。
_CREATION_EVENT_KIND = "task.created"
_CREATION_PRIMITIVE_TYPE = "Task"
# 任务正文与 Work 控制事件的承载形态(裁决 F:一律 public;闭协作由 G1
# 写路径把守而非可见性遮蔽)。
_POSTED_EVENT_KIND = "user_interaction.posted"
_MESSAGE_PRIMITIVE = "Message"
_TASK_PRIMITIVE = "Task"
# Work 六动作 → 事件词(机器迁移 event_name 逐字)。
_ADVANCE_EVENT_KINDS = {
    "start_task": "task.started",
    "report_progress": "task.progress.updated",
    "submit_task": "task.submitted",
    "accept_task": "task.accepted",
    "reject_task": "task.rejected",
    "cancel_task": "task.cancelled",
}


def _required_content(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"task {field_name} must be a non-empty string, got: {value!r}"
        )
    return value


@dataclass(frozen=True)
class _AdvanceIntent:
    """G1 ``check_append`` 的 duck-typed 载体(不引 wire 合同类型)。"""

    event_kind: str
    primitive_type: str
    actor_ref: object
    parent_event: str | None
    visibility: str
    payload: dict


class TaskCommissionService:
    """任务代办九用例编排:发布 + Work 六动作(T2),读两例(T3)。"""

    @inject
    def __init__(
        self,
        collab_repo: ContextCollabRepositoryProtocol,
        authorizer: ContextCollabAuthorizerProtocol,
        method_store: MethodStoreProtocol,
        # 交付缝默认日志送达(P2 经 L1/A2A 换真实现);python-injector 对
        # 带默认值参数同样注入,DI 显式绑定见 task_module(bbs authorizer
        # 先例同款注释)。
        dispatch_port: "TaskDispatchPort" = None,
    ) -> None:
        self._repo = collab_repo
        self._authorizer = authorizer
        self._method_store = method_store
        self._dispatch_port = (
            dispatch_port if dispatch_port is not None else LoggingTaskDispatch()
        )

    # ------------------------------------------------------------- 发布

    def publish_task(
        self, command: PublishTaskCommand, context: ActorContext
    ) -> TaskCommissionCreatedView:
        title = _required_content(command.title, "title")
        body = _required_content(command.body, "body")
        assignee = _required_content(command.assignee_agent_id, "assignee")
        spec = ContextSpec(
            space=command.space,
            principal_ref=context.actor,
            method_kind=MethodKind.OPEN.value,
            # 指定执行 bot:辅助执行者,不承担验收权(L6 先例)。
            executor_ref=AgentRef(agent_type="bot", agent_id=assignee),
            objective=title,
            resource_refs=tuple(command.resource_refs),
            product_kind=ProductProfile.TASK_COMMISSION.value,
            workflow_kind=WorkflowKind.TASK_COMMISSION_GOAL.value,
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
        # L5 顶层单值 MethodBinding 投影(Open 无 roster,store 失败如实上抛)。
        project_created_context(
            self._method_store,
            current_axes(),
            record.collet.collet_id,
            spec,
            context,
            now=_now(),
        )
        appended = self._append_message(
            command=None,
            collet=record.collet,
            spec=spec,
            context=context,
            expected_version=1,
            content=body,
        )
        view = TaskCommissionCreatedView(
            collet_id=record.collet.collet_id,
            title=title,
            body_event_id=appended.event.event_id,
            status=record.current_snapshot.status
            if record.current_snapshot is not None
            else "created",
            context_version=appended.collet.version,
        )
        # 发布成功后自动送达指定执行 bot(交付缝;失败如实上抛,任务已
        # 持久不回滚——ACCS:送达不等于也不改写上层权威)。
        self._dispatch_port.dispatch_task_info(
            DispatchedTaskInfo(
                collet_id=view.collet_id,
                title=title,
                body=body,
                assignee_agent_id=assignee,
                publisher_principal_id=context.actor.principal_id,
            )
        )
        return view

    # ------------------------------------------------------- Work 六动作

    def start_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        return self._advance("start_task", command, context)

    def report_progress(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        return self._advance("report_progress", command, context)

    def submit_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        return self._advance("submit_task", command, context)

    def accept_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        return self._advance("accept_task", command, context)

    def reject_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        return self._advance("reject_task", command, context)

    def cancel_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        return self._advance("cancel_task", command, context)

    # ------------------------------------------------------------- 私有

    def _advance(
        self, action: str, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView:
        event_kind = _ADVANCE_EVENT_KINDS[action]
        record = self._repo.get_current_status(command.collab_context_id)
        if record is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {command.collab_context_id!r} not found"
            )
        if (
            record.context_spec.product_kind
            != ProductProfile.TASK_COMMISSION.value
        ):
            raise ContextCollabNotFoundError(
                f"collaboration context {command.collab_context_id!r} is not "
                "a task commission"
            )
        snapshot = record.current_snapshot
        if snapshot is None:
            # 外部部分行无可裁决状态;本产品创建路径恒有快照。
            raise ContextCollabValidationError(
                f"cannot advance task {command.collab_context_id!r}: "
                "no snapshot to adjudicate"
            )
        # 裁决 J——时序门:越序/终态后在事件落库前拒绝。
        allowed = TASK_COMMISSION_PRECONDITIONS[event_kind]
        if snapshot.status not in allowed:
            raise ContextCollabValidationError(
                f"cannot advance task {command.collab_context_id!r}: "
                f"{event_kind!r} is not allowed from status "
                f"{snapshot.status!r}"
            )
        # 裁决 K——提交权:submit 仅 executor 本体(bot 型且 agent_id 匹配)。
        if event_kind == "task.submitted":
            executor = record.context_spec.executor_ref
            if (
                executor is None
                or context.actor.principal_type != "bot"
                or context.actor.principal_id != executor.agent_id
            ):
                raise ContextCollabValidationError(
                    f"cannot submit task {command.collab_context_id!r}: "
                    f"actor {context.actor.principal_id!r} is not the "
                    "designated executor"
                )
        payload: dict = {}
        if command.note:
            payload["note"] = command.note
        if command.percent is not None:
            # percent 是既存协议词汇(快照投影 `_last_payload_percent` 读
            # 同名键);仅 progress 动作携带,0-100 整数。
            if (
                isinstance(command.percent, bool)
                or not isinstance(command.percent, int)
                or not 0 <= command.percent <= 100
            ):
                raise ContextCollabValidationError(
                    "task percent must be an integer in [0, 100], got: "
                    f"{command.percent!r}"
                )
            if event_kind != "task.progress.updated":
                raise ContextCollabValidationError(
                    "task percent is only reportable by report_progress"
                )
            payload["percent"] = command.percent
        if event_kind == "task.submitted":
            payload["artifacts"] = list(command.artifacts)

        intent = _AdvanceIntent(
            event_kind=event_kind,
            primitive_type=_TASK_PRIMITIVE,
            actor_ref=context.actor,
            parent_event=None,
            visibility=VISIBILITY_PUBLIC,
            payload=payload,
        )
        validate_event_body(
            event_kind=intent.event_kind,
            primitive_type=intent.primitive_type,
            visibility=intent.visibility,
            payload=intent.payload,
        )
        # G1 先于机器守卫(spec §5.2 权威管线,与通用服务同序):授权话术
        # 不把 rule_id/守卫内幕泄露给本应最先拒掉的陌生 actor。
        self._authorizer.check_append(
            record.collet, record.context_spec, intent, context
        )
        # 机器是唯一 status 裁决者:守卫 FAIL 在此响亮冒出(如无 artifacts
        # 提交);快照推进非恒等(区别于公开讨论的 J' 恒等)。
        resolved = resolve_workflow_status(
            workflow_kind=record.context_spec.workflow_kind,
            executor_ref=record.context_spec.executor_ref,
            snapshot=snapshot,
            event_kind=event_kind,
            payload=payload,
            actor=context.actor,
        )

        def _advance_snapshot(
            current: CurrentSnapshot | None,
        ) -> CurrentSnapshot | None:
            if current is None or resolved is None:
                return current
            if resolved == current.status:
                return current
            from dataclasses import replace

            return replace(current, status=resolved)

        appended = self._repo.append_event(
            command.collab_context_id,
            AppendEventRecordRequest(
                expected_version=record.collet.version,
                event_kind=event_kind,
                primitive_type=_TASK_PRIMITIVE,
                actor_ref=context.actor,
                parent_event=None,
                visibility=VISIBILITY_PUBLIC,
                payload=payload,
                snapshot_updater=_advance_snapshot,
            ),
        )
        final_status = (
            appended.current_snapshot.status
            if appended.current_snapshot is not None
            else (resolved or snapshot.status)
        )
        return TaskAdvancedView(
            collab_context_id=command.collab_context_id,
            event_id=appended.event.event_id,
            context_version=appended.collet.version,
            status=final_status,
        )

    def _append_message(
        self,
        *,
        command,
        collet,
        spec: ContextSpec,
        context: ActorContext,
        expected_version: int,
        content: str,
    ) -> EventAppendedRecord:
        """任务正文(发布附件的描述 Message):principal 路径,G1 放行。"""
        intent = _AdvanceIntent(
            event_kind=_POSTED_EVENT_KIND,
            primitive_type=_MESSAGE_PRIMITIVE,
            actor_ref=context.actor,
            parent_event=None,
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
            collet.collet_id,
            AppendEventRecordRequest(
                expected_version=expected_version,
                event_kind=_POSTED_EVENT_KIND,
                primitive_type=_MESSAGE_PRIMITIVE,
                actor_ref=context.actor,
                parent_event=None,
                visibility=VISIBILITY_PUBLIC,
                payload={"content": content},
                snapshot_updater=lambda snapshot: snapshot,
            ),
        )

    # ------------------------------------------------------------- 读(T3)

    def list_tasks(self) -> list[TaskCommissionSummaryView]:
        """Query.search/filter:按 product profile 列任务,gmt_create 倒序。"""
        rows = self._repo.list_collets_by_product(
            ProductProfile.TASK_COMMISSION.value
        )
        counts = self._repo.count_message_events(
            [collet.collet_id for collet, _, _ in rows]
        )
        return [
            TaskCommissionSummaryView(
                collet_id=collet.collet_id,
                title=spec.objective,
                initiator_id=collet.initiator_ref.principal_id,
                message_count=counts.get(collet.collet_id, 0),
                created_at=gmt_create,
            )
            for collet, spec, gmt_create in rows
        ]

    def detail_task(self, collab_context_id: str) -> TaskCommissionDetailView:
        """Query.project:标题/正文/机器状态/公开推进历史一次给齐。

        title 读源合同沿公开讨论先例(初始事件 payload objective,
        NULL-payload 外部行回退伴随 spec.objective,P3 撤列时移除);
        events 只投影 public(裁决 F),Task 控制事件与发言事件同列。
        """
        record = self._repo.get_current_status(collab_context_id)
        if record is None:
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} not found"
            )
        if (
            record.context_spec.product_kind
            != ProductProfile.TASK_COMMISSION.value
        ):
            raise ContextCollabNotFoundError(
                f"collaboration context {collab_context_id!r} is not "
                "a task commission"
            )
        title = self._title_of(record)
        messages = [
            event
            for event in record.events
            if event.primitive_type == _MESSAGE_PRIMITIVE
            and event.visibility == VISIBILITY_PUBLIC
        ]
        body_event = messages[0] if messages else None
        body_view = (
            DiscussionDetailBody(
                event_id=body_event.event_id,
                content=self._content_of(body_event),
                author_id=self._principal_id(body_event.actor_ref),
                occurred_at=body_event.occurred_at,
            )
            if body_event is not None
            else None
        )
        events = [
            {
                "event_id": event.event_id,
                "event_kind": event.event_kind,
                "primitive_type": event.primitive_type
                if event.primitive_type is not None
                else "",
                "actor_id": self._principal_id(event.actor_ref),
                "occurred_at": event.occurred_at,
                # 公开事件的 payload 随投影可查(percent/note/artifacts)。
                "payload": event.payload if isinstance(event.payload, dict) else {},
            }
            for event in record.events
            if event.visibility == VISIBILITY_PUBLIC
        ]
        return TaskCommissionDetailView(
            collet_id=collab_context_id,
            title=title,
            status=(
                record.current_snapshot.status
                if record.current_snapshot is not None
                else ""
            ),
            body=body_view,
            events=events,
        )

    # ------------------------------------------------------- 读投影私有件

    @staticmethod
    def _title_of(record) -> str:
        """title 新主=初始事件 payload 的 objective;NULL-payload 回退
        伴随 ``context_spec.objective``(P3 撤列时移除)。"""
        title = record.context_spec.objective
        for event in record.events:
            if event.event_kind == _CREATION_EVENT_KIND:
                payload = event.payload
                if isinstance(payload, dict):
                    objective = payload.get("objective")
                    if isinstance(objective, str) and objective.strip():
                        title = objective
                break
        return title

    @staticmethod
    def _principal_id(ref) -> str:
        """读投影作者标识;外部部分行(NULL actor 列)读为空串。"""
        return ref.principal_id if ref is not None else ""

    @staticmethod
    def _content_of(event) -> str:
        """读投影正文;payload NULL 的部分行读空串(读面容错,不 422)。"""
        payload = event.payload
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, str):
                return content
        return ""
