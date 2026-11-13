"""Context collab L5 Method 服务 API 契约(transport-agnostic,spec §5)。

L5 方法族命令/结果对象与四个服务协议;模式沿用既有
``service_api.py``:delivery 层做解析与序列化,只依赖本协议,不依赖
实现类,本模块不感知 HTTP。按操作族拆四个协议:
``MethodLifecycleServiceProtocol``(§3.2/§3.3 生命周期 + 组合 +
inspection)、``GroupMethodOpsServiceProtocol``(spec §5 裁决:Single
四操作与 group 八操作共用一个协议,profile 操作族按 spec §4.1/§4.2
落齐)、``SwarmMethodOpsServiceProtocol``(§4.3)、
``OpenMethodOpsServiceProtocol``(§4.4)。方法签名统一以
``(collab_context_id, method_binding_id, ...)`` 起头;创建类命令的
幂等键(同 ID 同形态返回已存在对象,spec §8.3)一律可空缺省,由
服务端生成。实现落在 services/(T8–T12),本模块只有契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.service_api import ActorContext

__all__ = [
    "ActorContext",
    "BindMethodCommand",
    "MethodStateChangeCommand",
    "UpdateBindingConfigCommand",
    "ComposeMethodCommand",
    "SingleSelectCommand",
    "SingleReplaceCommand",
    "SingleDelegateCommand",
    "GroupMemberCommand",
    "GroupChangeRoleCommand",
    "GroupReplaceMemberCommand",
    "SwarmExecutionProfileCommand",
    "SwarmPlanCandidateCommand",
    "SwarmPlanOpCommand",
    "SwarmAllocateCommand",
    "SwarmStageOutcomeCommand",
    "OpenOpportunityCommand",
    "OpenOpportunityOpCommand",
    "OpenApplicationCommand",
    "OpenAdmitCommand",
    "OpenDecisionOpCommand",
    "MethodBindingResult",
    "MethodInspectionResult",
    "CompositionEdgeResult",
    "MethodOpAcceptedResult",
    "ProfileOpAcceptedResult",
    "MethodLifecycleServiceProtocol",
    "GroupMethodOpsServiceProtocol",
    "SwarmMethodOpsServiceProtocol",
    "OpenMethodOpsServiceProtocol",
]


@dataclass
class BindMethodCommand:
    """嵌套/委托绑定命令(spec §3.2/§8.3);``method_binding_id`` 为幂等键,缺省由服务端生成。"""

    method_kind: str  # wire 取值(spec §3.1):"Single" | "Group" | "Open" | "Swarm"
    method_binding_id: str | None = None
    parent_scope_ref: str | None = None
    participant_ref: str | None = None
    config: dict = field(default_factory=dict)
    bind_reason: str = ""


@dataclass
class MethodStateChangeCommand:
    """生命周期/释放类操作命令;``reason`` 落审计四字段(spec §6.3)。"""

    reason: str = ""


@dataclass
class UpdateBindingConfigCommand:
    """binding config 变更命令(spec §3.2 update 操作);乐观并发版本号必填。"""

    config: dict
    expected_version: int


@dataclass
class ComposeMethodCommand:
    """组合边创建命令(spec §3.3);``relation`` 为 wire token,矩阵校验在服务层。"""

    relation: str  # "nest" | "delegate-to" | "support" | "parallelize" | "escalate"
    child_binding_id: str
    child_method_kind: str
    edge_id: str | None = None  # 幂等键(spec §8.3),缺省由服务端生成
    reason: str = ""


@dataclass
class SingleSelectCommand:
    """Single Select 命令(spec §4.1);Query 结果不自动成为 selection,必须显式携带 agent 引用。"""

    agent_participant_ref: str
    scope_ref: str
    requirement_refs: list[str] = field(default_factory=list)
    selection_evidence_refs: list[str] = field(default_factory=list)
    valid_until: str | None = None
    selection_id: str | None = None  # 幂等键(spec §8.3)
    reason: str = ""


@dataclass
class SingleReplaceCommand:
    """Single Replace 命令(spec §4.1);存储锁内原子替换 ACTIVE selection。"""

    agent_participant_ref: str
    scope_ref: str
    reason: str = ""


@dataclass
class SingleDelegateCommand:
    """Single Delegate 便捷操作命令(spec §4.1):创建嵌套 binding + DELEGATE_TO 边。"""

    method_kind: str
    parent_scope_ref: str | None
    child_binding_id: str | None = None
    edge_id: str | None = None
    config: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class GroupMemberCommand:
    """group 成员操作共用命令(spec §4.2):invite/add_member/suspend_member/resume_member/leave/remove_member。"""

    participant_ref: str
    address: str = ""
    role_binding_refs: list[str] = field(default_factory=list)
    handover_to: str | None = None  # remove_member 未完成工作转移目标(P1 仅校验格式)
    member_binding_id: str | None = None  # 成员幂等键(spec §8.3)
    reason: str = ""


@dataclass
class GroupChangeRoleCommand:
    """group ChangeRole 命令(spec §4.2);roster revision 比对,不匹配即 409 语义。"""

    participant_ref: str
    role_binding_refs: list[str]
    expected_revision: int
    reason: str = ""


@dataclass
class GroupReplaceMemberCommand:
    """group ReplaceMember 命令(spec §4.2);存储锁内原子:旧→REPLACED、新→ACTIVE。"""

    old_participant_ref: str
    new_participant_ref: str
    address: str = ""
    role_binding_refs: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class SwarmExecutionProfileCommand:
    """Swarm DeclareExecutionProfile 命令(spec §4.3);``execution_profile_id`` 为幂等键。"""

    mode: str  # "A2A_AGENT" | "NATIVE_COMPUTE"
    target_ref: str
    supported_work_kinds: list[str]  # "PLAN" | "MATCH" | "SCHEDULE" | "EVALUATE" | "EXECUTE" 子集
    input_contract_ref: str
    execution_profile_id: str | None = None
    output_contract_ref: str = ""
    lifecycle_mapping: dict = field(default_factory=dict)


@dataclass
class SwarmPlanCandidateCommand:
    """Swarm SubmitPlanCandidate 命令(spec §4.3);stages 每项含 spec §4.3 Stage 七要素。"""

    task_ref: str
    swarm_plan_id: str | None = None  # 幂等键(spec §8.3)
    max_parallelism: int = 1
    max_depth: int = 1
    budget_resource_refs: list[str] = field(default_factory=list)
    termination_condition: dict = field(default_factory=dict)
    # 每个 stage dict:stage_id / name / dependency_stage_refs / requirement_refs /
    # input_artifact_refs / output_artifact_roles / completion_condition。
    stages: list[dict] = field(default_factory=list)
    reason: str = ""


@dataclass
class SwarmPlanOpCommand:
    """Swarm 计划状态机操作共用命令(spec §4.3):adopt/start/pause/resume/terminate_plan。"""

    reason: str = ""


@dataclass
class SwarmAllocateCommand:
    """Swarm AllocateStage 命令(spec §4.3);active allocation 必须持有未过期 lease(结构断言)。"""

    stage_ref: str
    target_kind: str  # "AGENT" | "COMPUTE"
    target_ref: str
    execution_profile_ref: str
    lease_expires_at: str
    qualification_evidence_refs: list[str] = field(default_factory=list)
    allocation_id: str | None = None  # 幂等键(spec §8.3)
    reason: str = ""


@dataclass
class SwarmStageOutcomeCommand:
    """Swarm stage 终局命令共用(spec §4.3):complete_stage/fail_stage。"""

    allocation_refs: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class OpenOpportunityCommand:
    """Open PublishOpportunity 命令(spec §4.4);``opportunity_id`` 为幂等键。"""

    scope_ref: str
    participation_type: str  # "COLLET_PARTICIPANT" | "GROUP_MEMBER" | "SWARM_ALLOCATION" | "SINGLE_CONTRIBUTION"
    opens_at: str
    opportunity_id: str | None = None
    requirement_refs: list[str] = field(default_factory=list)
    admission_policy_refs: list[str] = field(default_factory=list)
    capacity: int | None = None
    closes_at: str | None = None
    reason: str = ""


@dataclass
class OpenOpportunityOpCommand:
    """Open opportunity 状态机操作共用命令(spec §4.4):publish 挂 publish 命令,pause/reopen/close 用本命令。"""

    reason: str = ""


@dataclass
class OpenApplicationCommand:
    """Open SubmitApplication 命令(spec §4.4);责任主体必填,``application_id`` 为幂等键。"""

    applicant_agent_ref: str
    accountable_principal_ref: str
    capability_evidence_refs: list[str] = field(default_factory=list)
    proposed_artifact_refs: list[str] = field(default_factory=list)
    requested_role_refs: list[str] = field(default_factory=list)
    application_id: str | None = None
    reason: str = ""


@dataclass
class OpenAdmitCommand:
    """Open Admit 命令(spec §4.4);``SWARM_ALLOCATION`` 必须携带 ``target_stage_ref``(作者裁决)。"""

    admitted_scope_ref: str | None = None
    target_stage_ref: str | None = None
    decision_id: str | None = None  # AdmissionDecision 幂等键(spec §8.3)
    reason: str = ""


@dataclass
class OpenDecisionOpCommand:
    """Open application 裁决操作共用命令(spec §4.4):screen/reject/withdraw/revoke_admission。"""

    reason: str = ""


@dataclass
class MethodBindingResult:
    """bind 用例结果;``binding`` 为 MethodBinding ``to_dict`` 投影。"""

    binding: dict


@dataclass
class MethodInspectionResult:
    """inspection 投影结果(spec §5):state summary + capabilities(profile 注册表)+ conformance evidence。"""

    binding: dict
    state_summary: dict
    capabilities: dict  # 自 METHOD_PROFILE_REGISTRY 投影
    conformance_evidence: dict  # 最近一次 G4 校验报告 + Profile 对象状态计数


@dataclass
class CompositionEdgeResult:
    """compose/detach 用例结果;``edge`` 为 MethodCompositionEdge ``to_dict`` 投影。"""

    edge: dict


@dataclass
class MethodOpAcceptedResult:
    """binding 生命周期与 config 变更用例结果(spec §3.2);``version`` 为乐观并发版本。"""

    accepted: bool
    collab_context_id: str
    method_binding_id: str
    state: str
    version: int


@dataclass
class ProfileOpAcceptedResult:
    """Single/Group/Swarm/Open 各对象面操作的通用用例结果(spec §4)。

    ``object_kind``/``object_id``/``state`` 描述操作落定的 Profile 内部
    对象(selection/member/plan/allocation/opportunity/application);单
    一结果对象避免逐操作族各造一个 dataclass。
    """

    accepted: bool
    collab_context_id: str
    method_binding_id: str
    object_kind: str
    object_id: str
    state: str


@runtime_checkable
class MethodLifecycleServiceProtocol(Protocol):
    """L5 公共合同服务协议(spec §3.2/§3.3);delivery 依赖本协议,不依赖实现类。"""

    def bind(self, collab_context_id: str, command: BindMethodCommand,
             context: ActorContext) -> MethodBindingResult: ...
    def activate(self, collab_context_id: str, method_binding_id: str,
                 command: MethodStateChangeCommand,
                 context: ActorContext) -> MethodOpAcceptedResult: ...
    def suspend(self, collab_context_id: str, method_binding_id: str,
                command: MethodStateChangeCommand,
                context: ActorContext) -> MethodOpAcceptedResult: ...
    def resume(self, collab_context_id: str, method_binding_id: str,
               command: MethodStateChangeCommand,
               context: ActorContext) -> MethodOpAcceptedResult: ...
    def terminate(self, collab_context_id: str, method_binding_id: str,
                  command: MethodStateChangeCommand,
                  context: ActorContext) -> MethodOpAcceptedResult: ...
    def update_config(self, collab_context_id: str, method_binding_id: str,
                      command: UpdateBindingConfigCommand,
                      context: ActorContext) -> MethodOpAcceptedResult: ...
    def inspect(self, collab_context_id: str, method_binding_id: str,
                context: ActorContext) -> MethodInspectionResult: ...
    def compose(self, collab_context_id: str, parent_binding_id: str,
                command: ComposeMethodCommand,
                context: ActorContext) -> CompositionEdgeResult: ...
    def detach(self, collab_context_id: str, edge_id: str,
               command: MethodStateChangeCommand,
               context: ActorContext) -> CompositionEdgeResult: ...


@runtime_checkable
class GroupMethodOpsServiceProtocol(Protocol):
    """Single + Group 操作协议(spec §4.1/§4.2;spec §5 GroupProfileOps 含 Single 委托入口)。

    ``delegate`` 创建嵌套 binding + DELEGATE_TO 边,返回组合边投影;
    其余操作返回 Profile 对象通用受理结果。
    """

    def select(self, collab_context_id: str, method_binding_id: str,
               command: SingleSelectCommand,
               context: ActorContext) -> ProfileOpAcceptedResult: ...
    def replace(self, collab_context_id: str, method_binding_id: str,
                command: SingleReplaceCommand,
                context: ActorContext) -> ProfileOpAcceptedResult: ...
    def release(self, collab_context_id: str, method_binding_id: str,
                command: MethodStateChangeCommand,
                context: ActorContext) -> ProfileOpAcceptedResult: ...
    def delegate(self, collab_context_id: str, method_binding_id: str,
                 command: SingleDelegateCommand,
                 context: ActorContext) -> CompositionEdgeResult: ...
    def invite(self, collab_context_id: str, method_binding_id: str,
               command: GroupMemberCommand,
               context: ActorContext) -> ProfileOpAcceptedResult: ...
    def add_member(self, collab_context_id: str, method_binding_id: str,
                   command: GroupMemberCommand,
                   context: ActorContext) -> ProfileOpAcceptedResult: ...
    def suspend_member(self, collab_context_id: str, method_binding_id: str,
                       command: GroupMemberCommand,
                       context: ActorContext) -> ProfileOpAcceptedResult: ...
    def resume_member(self, collab_context_id: str, method_binding_id: str,
                      command: GroupMemberCommand,
                      context: ActorContext) -> ProfileOpAcceptedResult: ...
    def leave(self, collab_context_id: str, method_binding_id: str,
              command: GroupMemberCommand,
              context: ActorContext) -> ProfileOpAcceptedResult: ...
    def remove_member(self, collab_context_id: str, method_binding_id: str,
                      command: GroupMemberCommand,
                      context: ActorContext) -> ProfileOpAcceptedResult: ...
    def replace_member(self, collab_context_id: str, method_binding_id: str,
                       command: GroupReplaceMemberCommand,
                       context: ActorContext) -> ProfileOpAcceptedResult: ...
    def change_role(self, collab_context_id: str, method_binding_id: str,
                    command: GroupChangeRoleCommand,
                    context: ActorContext) -> ProfileOpAcceptedResult: ...


@runtime_checkable
class SwarmMethodOpsServiceProtocol(Protocol):
    """Swarm 权威面操作协议,11 操作(spec §4.3);Computation Plane 派发不在本协议(P2)。"""

    def declare_execution_profile(self, collab_context_id: str,
                                   method_binding_id: str,
                                   command: SwarmExecutionProfileCommand,
                                   context: ActorContext) -> ProfileOpAcceptedResult: ...
    def submit_plan_candidate(self, collab_context_id: str,
                              method_binding_id: str,
                              command: SwarmPlanCandidateCommand,
                              context: ActorContext) -> ProfileOpAcceptedResult: ...
    def validate_plan(self, collab_context_id: str, method_binding_id: str,
                      swarm_plan_id: str,
                      context: ActorContext) -> dict: ...
    def adopt_plan(self, collab_context_id: str, method_binding_id: str,
                   swarm_plan_id: str, command: SwarmPlanOpCommand,
                   context: ActorContext) -> ProfileOpAcceptedResult: ...
    def start_plan(self, collab_context_id: str, method_binding_id: str,
                   swarm_plan_id: str, command: SwarmPlanOpCommand,
                   context: ActorContext) -> ProfileOpAcceptedResult: ...
    def pause_plan(self, collab_context_id: str, method_binding_id: str,
                   swarm_plan_id: str, command: SwarmPlanOpCommand,
                   context: ActorContext) -> ProfileOpAcceptedResult: ...
    def resume_plan(self, collab_context_id: str, method_binding_id: str,
                    swarm_plan_id: str, command: SwarmPlanOpCommand,
                    context: ActorContext) -> ProfileOpAcceptedResult: ...
    def terminate_plan(self, collab_context_id: str, method_binding_id: str,
                       swarm_plan_id: str, command: SwarmPlanOpCommand,
                       context: ActorContext) -> ProfileOpAcceptedResult: ...
    def allocate_stage(self, collab_context_id: str, method_binding_id: str,
                       command: SwarmAllocateCommand,
                       context: ActorContext) -> ProfileOpAcceptedResult: ...
    def complete_stage(self, collab_context_id: str, method_binding_id: str,
                       stage_ref: str, command: SwarmStageOutcomeCommand,
                       context: ActorContext) -> ProfileOpAcceptedResult: ...
    def fail_stage(self, collab_context_id: str, method_binding_id: str,
                   stage_ref: str, command: SwarmStageOutcomeCommand,
                   context: ActorContext) -> ProfileOpAcceptedResult: ...


@runtime_checkable
class OpenMethodOpsServiceProtocol(Protocol):
    """Open Profile 操作协议(spec §4.4);录取只对 ``admitted_scope_ref`` 生效,不产生 Space Membership。"""

    def publish_opportunity(self, collab_context_id: str,
                            method_binding_id: str,
                            command: OpenOpportunityCommand,
                            context: ActorContext) -> ProfileOpAcceptedResult: ...
    def pause_opportunity(self, collab_context_id: str, method_binding_id: str,
                          opportunity_ref: str, command: OpenOpportunityOpCommand,
                          context: ActorContext) -> ProfileOpAcceptedResult: ...
    def reopen_opportunity(self, collab_context_id: str,
                           method_binding_id: str, opportunity_ref: str,
                           command: OpenOpportunityOpCommand,
                           context: ActorContext) -> ProfileOpAcceptedResult: ...
    def close_opportunity(self, collab_context_id: str,
                          method_binding_id: str, opportunity_ref: str,
                          command: OpenOpportunityOpCommand,
                          context: ActorContext) -> ProfileOpAcceptedResult: ...
    def submit_application(self, collab_context_id: str,
                           method_binding_id: str, opportunity_ref: str,
                           command: OpenApplicationCommand,
                           context: ActorContext) -> ProfileOpAcceptedResult: ...
    def screen(self, collab_context_id: str, method_binding_id: str,
               application_ref: str, command: OpenDecisionOpCommand,
               context: ActorContext) -> ProfileOpAcceptedResult: ...
    def reject(self, collab_context_id: str, method_binding_id: str,
               application_ref: str, command: OpenDecisionOpCommand,
               context: ActorContext) -> ProfileOpAcceptedResult: ...
    def withdraw(self, collab_context_id: str, method_binding_id: str,
                 application_ref: str, command: OpenDecisionOpCommand,
                 context: ActorContext) -> ProfileOpAcceptedResult: ...
    def admit(self, collab_context_id: str, method_binding_id: str,
              application_ref: str, command: OpenAdmitCommand,
              context: ActorContext) -> ProfileOpAcceptedResult: ...
    def revoke_admission(self, collab_context_id: str, method_binding_id: str,
                         application_ref: str,
                         command: OpenDecisionOpCommand,
                         context: ActorContext) -> ProfileOpAcceptedResult: ...
