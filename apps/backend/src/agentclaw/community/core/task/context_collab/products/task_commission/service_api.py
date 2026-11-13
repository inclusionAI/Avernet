"""任务代办产品层合同(模块级交付面;spec §2a 操作族映射表)。

乙案产品自持合同:``publish_task`` = Context.create + Work.propose/assign;
``start_task``/``report_progress``/``submit_task``/``accept_task``/
``reject_task``/``cancel_task`` = Work 族六动作;``detail_task``/
``list_tasks`` = Query.project/search(用户 HTTP 后置)。
``report_execution`` 是 Bot 入站执行报告合同,复用 Work 动作及 G1/G4/CAS。

`T | None` 只用于合法领域可空:``note``(可选备注)与孤儿 ``body``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
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
class TaskExecutionReportCommand:
    """Authenticated executor input with mandatory concurrency/context fences.

    ``action`` is start_task/report_progress/submit_task, not owner controls.
    Profile and binding references correlate the dispatch; they grant no access.
    A stale replay is a conflict, not a second progress event or a success receipt.
    """

    action: str
    task: TaskAdvanceCommand
    expected_version: int
    product_profile_ref: VersionedProfileRef
    method_binding_ref: str
    # None means no caller-specified deadline, not an unlimited lease.
    deadline: datetime | None = None


@dataclass(frozen=True)
class DispatchedTaskInfo:
    """发布后送达指定执行 bot 的任务信息(L1 渡口 P1 占位承载形)。

    ``title``/``body`` 即任务正文,``collet_id`` 供 bot 回查协作上下文,
    ``assignee_agent_id`` 即被指派的执行者本体。两项 ref 用于关联已选
    L6 Profile 和实际 L5 绑定,不携带授权或 session 凭据。
    """

    collet_id: str
    title: str
    body: str
    assignee_agent_id: str
    # 发起人身份链(裁决 G,ACCS-001 §5 规则 1):经 L1 桥出域时作为
    # OperationContext.accountable_principal_ref 源。
    publisher_principal_id: str
    # Pinned product selection and actual runtime binding, not access credentials.
    product_profile_ref: VersionedProfileRef
    method_binding_ref: str
    # Collet revision at dispatch, the initial expected_revision for Bot reports.
    context_version: int


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
    ``product_profile_ref`` 固定本次采用的 L6 定义;适配器必须消费其中
    的 publication 合同,不能另选 latest。``method_binding_ref`` 是已
    实例化的 L5 绑定,不是发布人或执行者身份。
    """

    collab_context_id: str
    assignee_agent_id: str
    accountable_principal_id: str
    product_profile_ref: VersionedProfileRef
    method_binding_ref: str


@dataclass(frozen=True)
class AssigneeAdmissionReceipt:
    """方法面四步编排的受理回执;P1 供断言/审计,不进产品视图
    (方法 store 行记录运行时结局,不承诺跨重启持久化)。"""

    opportunity_id: str
    application_id: str
    decision_id: str
    state: str


@runtime_checkable
class TaskMethodOpsPort(Protocol):
    """方法编排缝:发布即驱动真 Open 方法生命周期——
    publish_opportunity → submit_application → screen → admit
    (SINGLE_CONTRIBUTION、capacity=1、scope=collet),指定执行者至
    ADMITTED。策略、容量、类型及顺序由所选 L6 publication 合同声明;
    未支持的策略拒绝,失败原样传播,不回滚已创建的下层对象。
    P1 系统编排,恒以发布人 ActorContext 调用;实现住 DI
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
    """任务代办服务协议;用户用例与 Bot 入站报告共用产品权威。"""

    def publish_task(
        self, command: PublishTaskCommand, context: ActorContext
    ) -> TaskCommissionCreatedView: ...

    def report_execution(
        self, command: TaskExecutionReportCommand, context: ActorContext
    ) -> TaskAdvancedView: ...

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
