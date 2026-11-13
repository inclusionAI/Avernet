"""任务代办产品层合同(模块级交付面;spec §2a 操作族映射表)。

乙案产品自持合同:``publish_task`` = Context.create + Work.propose/assign;
``start_task``/``report_progress``/``submit_task``/``accept_task``/
``reject_task``/``cancel_task`` = Work 族六动作;``detail_task``/
``list_tasks`` = Query.project/search(P1 含,HTTP 后置)。

`T | None` 只用于合法领域可空:``note``(可选备注)与孤儿 ``body``。
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

if TYPE_CHECKING:  # 读视图(T3)落地面,协议签名先钉,运行时不求值。
    from agentclaw.community.core.task.context_collab.products.task_commission.views import (
        TaskCommissionDetailView,
        TaskCommissionSummaryView,
    )


@dataclass
class PublishTaskCommand:
    """发布命令:标题进聚合 objective,正文进首帖 Message 事件。

    ``assignee_agent_id`` 是被委托执行 bot 的 agent_id(P1 仅 bot 型);
    它落 ``ContextSpec.executor_ref``——辅助执行者,不承担验收权
    (沿 L6 先例"不把代办伪装成 Single")。
    """

    title: str
    body: str
    space: SpaceRef
    assignee_agent_id: str
    resource_refs: list[ResourceRef] = field(default_factory=list)


@dataclass
class TaskAdvanceCommand:
    """Work 六动作共用命令形;``note`` 可选备注,``percent`` 仅 progress 用
    (0-100 整数,启用既存 percent 协议词汇),``artifacts`` 仅 submit 用。"""

    collab_context_id: str
    note: str | None = None
    percent: int | None = None
    artifacts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DispatchedTaskInfo:
    """发布后送达指定执行 bot 的任务信息(L1 渡口 P1 占位承载形)。

    ``title``/``body`` 即任务正文,``collet_id`` 供 bot 回查协作上下文,
    ``assignee_agent_id`` 即被指派的执行者本体。
    """

    collet_id: str
    title: str
    body: str
    assignee_agent_id: str
    # 发起人身份链(裁决 G,ACCS-001 §5 规则 1):经 L1 桥出域时作为
    # OperationContext.accountable_principal_ref 源。
    publisher_principal_id: str


@runtime_checkable
class TaskDispatchPort(Protocol):
    """交办交付缝:真实通道 P2 经 L1/A2A 接管,同端口整体替换。"""

    def dispatch_task_info(self, info: DispatchedTaskInfo) -> None: ...


@dataclass(frozen=True)
class AssigneeAdmissionCommand:
    """发布编排驱动的指定执行者录取命令(方法编排缝的语义面)。

    ``collab_context_id`` 即机会 scope_ref 与录取 admitted_scope_ref;
    ``assignee_agent_id`` 是被指派执行 bot(申请的 applicant_agent_ref);
    ``accountable_principal_id`` 是发布发起人 principal_id——申请的
    责任主体:系统代投的申请由发布人担责(withdraw 自助面同语义)。
    """

    collab_context_id: str
    assignee_agent_id: str
    accountable_principal_id: str


@dataclass(frozen=True)
class AssigneeAdmissionReceipt:
    """方法面四步编排的受理回执;P1 供断言/审计,不进产品视图
    (方法 store 行即持久结局)。"""

    opportunity_id: str
    application_id: str
    decision_id: str
    state: str


@runtime_checkable
class TaskMethodOpsPort(Protocol):
    """方法编排缝:发布即驱动真 Open 方法生命周期——
    publish_opportunity → submit_application → screen → admit
    (SINGLE_CONTRIBUTION、capacity=1、scope=collet),指定执行者至
    ADMITTED。P1 系统编排,恒以发布人 ActorContext 调用;实现住 DI
    适配层(产品白名单外的服务面经此缝进入,白名单零改动)。"""

    def admit_designated_assignee(
        self, command: AssigneeAdmissionCommand, context: ActorContext
    ) -> AssigneeAdmissionReceipt: ...


@dataclass
class TaskCommissionCreatedView:
    """发布结果:status 恒为创建态("created");事件身份与并发版本。"""

    collet_id: str
    title: str
    body_event_id: str
    status: str
    context_version: int


@dataclass
class TaskAdvancedView:
    """六 Work 动作统一返回:推进后的新 status(机器裁决派写)。"""

    collab_context_id: str
    event_id: str
    status: str
    context_version: int


@runtime_checkable
class TaskCommissionServiceProtocol(Protocol):
    """任务代办九用例协议;模块级交付面(P1 无 HTTP,沿公开讨论 spec §5)。"""

    def publish_task(
        self, command: PublishTaskCommand, context: ActorContext
    ) -> TaskCommissionCreatedView: ...

    def start_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

    def report_progress(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

    def submit_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

    def accept_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

    def reject_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

    def cancel_task(
        self, command: TaskAdvanceCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

    def list_tasks(self) -> "list[TaskCommissionSummaryView]": ...

    def detail_task(self, collab_context_id: str) -> "TaskCommissionDetailView": ...
