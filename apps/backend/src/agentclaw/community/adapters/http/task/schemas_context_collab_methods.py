"""L5 method 操作 wire DTO(spec §7 / 2026-11-12 spec)。

Pydantic 薄组装:wire 命名 snake_case 与 ``method_service_api`` 字段
一致,``to_domain()`` 组装命令;响应 DTO 只投影结果对象字段,不透
``__dict__``。命令幂等键/可选字段与协议一致(P1 全部可缺省);
``reason`` 落审计四字段,不参与幂等比对面。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    ComposeMethodCommand,
    GroupChangeRoleCommand,
    GroupMemberCommand,
    GroupReplaceMemberCommand,
    MethodStateChangeCommand,
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenOpportunityCommand,
    OpenOpportunityOpCommand,
    SingleDelegateCommand,
    SingleReplaceCommand,
    SingleSelectCommand,
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
    UpdateBindingConfigCommand,
)


# ---------------------------------------------------------------------------
# 命令(lifecycle / compose)
# ---------------------------------------------------------------------------


class BindMethodRequest(BaseModel):
    """嵌套 bind 请求(``method_binding_id`` 为幂等键,缺省服务端生成)。"""

    method_kind: str = Field(..., description="Single | Group | Open | Swarm")
    method_binding_id: str | None = Field(
        None, description="幂等键(spec §8.3);缺省由服务端生成"
    )
    parent_scope_ref: str | None = Field(
        None, description="委托/嵌套作用域;无约束的委托可空"
    )
    participant_ref: str | None = Field(
        None, description="执行侧参与者引用;缺省沿用所属 Collet 的 executor"
    )
    config: dict[str, Any] = Field(default_factory=dict, description="剖面配置")
    bind_reason: str = Field("", description="绑定来源审计")

    def to_domain(self) -> BindMethodCommand:
        return BindMethodCommand(
            method_kind=self.method_kind,
            method_binding_id=self.method_binding_id,
            parent_scope_ref=self.parent_scope_ref,
            participant_ref=self.participant_ref,
            config=self.config,
            bind_reason=self.bind_reason,
        )


class MethodStateChangeRequest(BaseModel):
    """生命周期状态操作共用请求;``reason`` 落审计四字段。"""

    reason: str = Field("", description="迁移原因;落审计四字段")

    def to_domain(self) -> MethodStateChangeCommand:
        return MethodStateChangeCommand(reason=self.reason)


class UpdateBindingConfigRequest(BaseModel):
    """binding config 变更请求;乐观并发版本必填。"""

    config: dict[str, Any] = Field(..., description="新剖面配置")
    expected_version: int = Field(..., description="乐观并发比对版本")

    def to_domain(self) -> UpdateBindingConfigCommand:
        return UpdateBindingConfigCommand(
            config=self.config, expected_version=self.expected_version
        )


class ComposeMethodRequest(BaseModel):
    """组合边创建请求;``relation`` 是 wire token,矩阵校验在服务层。"""

    relation: str = Field(
        ..., description="nest | delegate-to | support | parallelize | escalate"
    )
    child_binding_id: str = Field(..., description="子 binding 引用")
    child_method_kind: str = Field(..., description="子 binding 的方法 kind")
    edge_id: str | None = Field(
        None, description="组合边幂等键(spec §8.3);缺省服务端生成"
    )
    reason: str = Field("", description="组合原因;落审计")

    def to_domain(self) -> ComposeMethodCommand:
        return ComposeMethodCommand(
            relation=self.relation,
            child_binding_id=self.child_binding_id,
            child_method_kind=self.child_method_kind,
            edge_id=self.edge_id,
            reason=self.reason,
        )


# ---------------------------------------------------------------------------
# 命令(Single / Group)
# ---------------------------------------------------------------------------


class SingleSelectRequest(BaseModel):
    """Single Select 请求;agent 引用显式必填(Query 结果不自动成为选择)。"""

    agent_participant_ref: str = Field(..., description="被选 agent 参与者引用")
    scope_ref: str = Field(..., description="作用域引用")
    requirement_refs: list[str] = Field(default_factory=list)
    selection_evidence_refs: list[str] = Field(default_factory=list)
    valid_until: str | None = Field(None, description="生效窗口终点(ISO)")
    selection_id: str | None = Field(
        None, description="幂等键(spec §8.3);缺省服务端生成"
    )
    reason: str = Field("", description="选择原因;落审计")

    def to_domain(self) -> SingleSelectCommand:
        return SingleSelectCommand(
            agent_participant_ref=self.agent_participant_ref,
            scope_ref=self.scope_ref,
            requirement_refs=self.requirement_refs,
            selection_evidence_refs=self.selection_evidence_refs,
            valid_until=self.valid_until,
            selection_id=self.selection_id,
            reason=self.reason,
        )


class SingleReplaceRequest(BaseModel):
    """Single Replace 请求;原子替换现役 selection。"""

    agent_participant_ref: str = Field(..., description="新被选 agent 引用")
    scope_ref: str = Field(..., description="作用域引用")
    reason: str = Field("", description="替换原因;落审计")

    def to_domain(self) -> SingleReplaceCommand:
        return SingleReplaceCommand(
            agent_participant_ref=self.agent_participant_ref,
            scope_ref=self.scope_ref,
            reason=self.reason,
        )


class SingleDelegateRequest(BaseModel):
    """Single Delegate 便捷请求:嵌套 binding + DELEGATE_TO 边一次落定。"""

    method_kind: str = Field(..., description="子方法 kind(Single/Group/Swarm)")
    parent_scope_ref: str | None = Field(
        None, description="委托作用域;无约束委托可空"
    )
    child_binding_id: str | None = Field(
        None, description="子 binding 幂等键;缺省服务端生成"
    )
    edge_id: str | None = Field(
        None, description="组合边幂等键;缺省服务端生成"
    )
    config: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field("", description="委托原因;落审计")

    def to_domain(self) -> SingleDelegateCommand:
        return SingleDelegateCommand(
            method_kind=self.method_kind,
            parent_scope_ref=self.parent_scope_ref,
            child_binding_id=self.child_binding_id,
            edge_id=self.edge_id,
            config=self.config,
            reason=self.reason,
        )


class GroupMemberRequest(BaseModel):
    """group 成员操作共用请求(invite/add/suspend/resume/leave/remove)。"""

    participant_ref: str = Field(..., description="成员参与者引用")
    address: str = Field("", description="成员地址(自由格式)")
    role_binding_refs: list[str] = Field(default_factory=list)
    handover_to: str | None = Field(
        None, description="remove_member 未完成工作转移目标(P1 仅格式校验)"
    )
    member_binding_id: str | None = Field(
        None, description="成员幂等键(spec §8.3);invite 用"
    )
    reason: str = Field("", description="操作原因;落审计")

    def to_domain(self) -> GroupMemberCommand:
        return GroupMemberCommand(
            participant_ref=self.participant_ref,
            address=self.address,
            role_binding_refs=self.role_binding_refs,
            handover_to=self.handover_to,
            member_binding_id=self.member_binding_id,
            reason=self.reason,
        )


class GroupReplaceMemberRequest(BaseModel):
    """group ReplaceMember 请求;存储锁内原子:旧→REPLACED、新→ACTIVE。"""

    old_participant_ref: str = Field(..., description="被替换成员引用")
    new_participant_ref: str = Field(..., description="新成员引用")
    address: str = Field("", description="新成员地址")
    role_binding_refs: list[str] = Field(default_factory=list)
    reason: str = Field("", description="换人原因;落审计")

    def to_domain(self) -> GroupReplaceMemberCommand:
        return GroupReplaceMemberCommand(
            old_participant_ref=self.old_participant_ref,
            new_participant_ref=self.new_participant_ref,
            address=self.address,
            role_binding_refs=self.role_binding_refs,
            reason=self.reason,
        )


class GroupChangeRoleRequest(BaseModel):
    """group ChangeRole 请求;roster revision 比对,不匹配 409 语义。"""

    participant_ref: str = Field(..., description="目标成员引用")
    role_binding_refs: list[str] = Field(..., description="新角色绑定引用")
    expected_revision: int = Field(..., description="roster 乐观并发基准")
    reason: str = Field("", description="调角原因;落审计")

    def to_domain(self) -> GroupChangeRoleCommand:
        return GroupChangeRoleCommand(
            participant_ref=self.participant_ref,
            role_binding_refs=self.role_binding_refs,
            expected_revision=self.expected_revision,
            reason=self.reason,
        )


# ---------------------------------------------------------------------------
# 命令(Swarm)
# ---------------------------------------------------------------------------


class SwarmExecutionProfileRequest(BaseModel):
    """执行剖面声明请求;声明即活(P1,无状态列 → 不落审计)。"""

    mode: str = Field(..., description="A2A_AGENT | NATIVE_COMPUTE")
    target_ref: str = Field(..., description="派发目标引用")
    supported_work_kinds: list[str] = Field(
        ..., description="PLAN/MATCH/SCHEDULE/EVALUATE/EXECUTE 非空子集"
    )
    input_contract_ref: str = Field(..., description="输入契约引用")
    execution_profile_id: str | None = Field(
        None, description="幂等键(spec §8.3);缺省服务端生成"
    )
    output_contract_ref: str = Field("", description="输出契约引用")
    lifecycle_mapping: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SwarmExecutionProfileCommand:
        return SwarmExecutionProfileCommand(
            mode=self.mode,
            target_ref=self.target_ref,
            supported_work_kinds=self.supported_work_kinds,
            input_contract_ref=self.input_contract_ref,
            execution_profile_id=self.execution_profile_id,
            output_contract_ref=self.output_contract_ref,
            lifecycle_mapping=self.lifecycle_mapping,
        )


class SwarmPlanStageRequest(BaseModel):
    """计划候选的 Stage 七要素(wire 与 spec §4.3 Stage 对齐,状态/审计
    由服务端落)。"""

    stage_id: str = Field(..., description="Stage 幂等 id")
    name: str = Field(..., description="Stage 名称")
    dependency_stage_refs: list[str] = Field(default_factory=list)
    requirement_refs: list[str] = Field(default_factory=list)
    input_artifact_refs: list[str] = Field(default_factory=list)
    output_artifact_roles: list[str] = Field(default_factory=list)
    completion_condition: dict[str, Any] = Field(
        ..., description="完成断言结构(非空)"
    )


class SwarmPlanCandidateRequest(BaseModel):
    """计划候选提交请求;provenance 执行记录由服务端自建。"""

    task_ref: str = Field(..., description="计划承载的 Task 引用")
    swarm_plan_id: str | None = Field(
        None, description="计划幂等键(spec §8.3);缺省服务端生成"
    )
    max_parallelism: int = Field(1, description="结构并行宽度上限")
    max_depth: int = Field(1, description="依赖链深度上限")
    budget_resource_refs: list[str] = Field(default_factory=list)
    termination_condition: dict[str, Any] = Field(default_factory=dict)
    stages: list[SwarmPlanStageRequest] = Field(default_factory=list)
    reason: str = Field("", description="提交原因;落审计")

    def to_domain(self) -> SwarmPlanCandidateCommand:
        return SwarmPlanCandidateCommand(
            task_ref=self.task_ref,
            swarm_plan_id=self.swarm_plan_id,
            max_parallelism=self.max_parallelism,
            max_depth=self.max_depth,
            budget_resource_refs=self.budget_resource_refs,
            termination_condition=self.termination_condition,
            stages=[
                {
                    "stage_id": stage.stage_id,
                    "name": stage.name,
                    "dependency_stage_refs": stage.dependency_stage_refs,
                    "requirement_refs": stage.requirement_refs,
                    "input_artifact_refs": stage.input_artifact_refs,
                    "output_artifact_roles": stage.output_artifact_roles,
                    "completion_condition": stage.completion_condition,
                }
                for stage in self.stages
            ],
            reason=self.reason,
        )


class SwarmPlanOpRequest(BaseModel):
    """计划状态机操作共用请求(adopt/start/pause/resume/terminate)。"""

    reason: str = Field("", description="操作原因;落审计")

    def to_domain(self) -> SwarmPlanOpCommand:
        return SwarmPlanOpCommand(reason=self.reason)


class SwarmAllocateRequest(BaseModel):
    """Stage 分配请求;lease/剖面/并行上限的结构断言在服务层。"""

    stage_ref: str = Field(..., description="目标 Stage 引用")
    target_kind: str = Field(..., description="AGENT | COMPUTE")
    target_ref: str = Field(..., description="分配目标引用")
    execution_profile_ref: str = Field(
        ..., description="binding 作用域内已声明的执行剖面引用"
    )
    lease_expires_at: str = Field(..., description="租约到期(ISO,须晚于 now)")
    qualification_evidence_refs: list[str] = Field(default_factory=list)
    allocation_id: str | None = Field(
        None, description=" 分配幂等键(spec §8.3);缺省服务端生成"
    )
    reason: str = Field("", description="分配原因;落审计")

    def to_domain(self) -> SwarmAllocateCommand:
        return SwarmAllocateCommand(
            stage_ref=self.stage_ref,
            target_kind=self.target_kind,
            target_ref=self.target_ref,
            execution_profile_ref=self.execution_profile_ref,
            lease_expires_at=self.lease_expires_at,
            qualification_evidence_refs=self.qualification_evidence_refs,
            allocation_id=self.allocation_id,
            reason=self.reason,
        )


class SwarmStageOutcomeRequest(BaseModel):
    """Stage 终局请求(complete/fail);complete 的分配查验在服务层。"""

    allocation_refs: list[str] = Field(
        default_factory=list,
        description="complete 要释放的分配;缺省取该 Stage 全部 ACTIVE",
    )
    reason: str = Field("", description="终局原因;落审计")

    def to_domain(self) -> SwarmStageOutcomeCommand:
        return SwarmStageOutcomeCommand(
            allocation_refs=self.allocation_refs, reason=self.reason
        )


# ---------------------------------------------------------------------------
# 命令(Open)
# ---------------------------------------------------------------------------


class OpenOpportunityRequest(BaseModel):
    """机会发布请求;窗口形态校验与 COLLET_PARTICIPANT 的 P1 拒绝在
    服务层。"""

    scope_ref: str = Field(..., description="机会作用域引用")
    participation_type: str = Field(
        ...,
        description=(
            "COLLET_PARTICIPANT | GROUP_MEMBER | SWARM_ALLOCATION | "
            "SINGLE_CONTRIBUTION"
        ),
    )
    opens_at: str = Field(..., description="开放起点(ISO)")
    opportunity_id: str | None = Field(
        None, description="机会幂等键(spec §8.3);缺省服务端生成"
    )
    requirement_refs: list[str] = Field(default_factory=list)
    admission_policy_refs: list[str] = Field(default_factory=list)
    capacity: int | None = Field(
        None, description="容量上限;None 不设限"
    )
    closes_at: str | None = Field(None, description="窗口终点(ISO)")
    reason: str = Field("", description="发布原因;落审计")

    def to_domain(self) -> OpenOpportunityCommand:
        return OpenOpportunityCommand(
            scope_ref=self.scope_ref,
            participation_type=self.participation_type,
            opens_at=self.opens_at,
            opportunity_id=self.opportunity_id,
            requirement_refs=self.requirement_refs,
            admission_policy_refs=self.admission_policy_refs,
            capacity=self.capacity,
            closes_at=self.closes_at,
            reason=self.reason,
        )


class OpenOpportunityOpRequest(BaseModel):
    """机会状态机操作共用请求(pause/reopen/close)。"""

    reason: str = Field("", description="操作原因;落审计")

    def to_domain(self) -> OpenOpportunityOpCommand:
        return OpenOpportunityOpCommand(reason=self.reason)


class OpenApplicationRequest(BaseModel):
    """公开提交申请请求;``accountable_principal_ref`` 是 withdraw 的
    self-help 比对目标。"""

    applicant_agent_ref: str = Field(..., description="申请人 agent 引用")
    accountable_principal_ref: str = Field(..., description="申请责任主体")
    capability_evidence_refs: list[str] = Field(default_factory=list)
    proposed_artifact_refs: list[str] = Field(default_factory=list)
    requested_role_refs: list[str] = Field(default_factory=list)
    application_id: str | None = Field(
        None, description="申请幂等键(spec §8.3);缺省服务端生成"
    )
    reason: str = Field("", description="申请原因;落审计")

    def to_domain(self) -> OpenApplicationCommand:
        return OpenApplicationCommand(
            applicant_agent_ref=self.applicant_agent_ref,
            accountable_principal_ref=self.accountable_principal_ref,
            capability_evidence_refs=self.capability_evidence_refs,
            proposed_artifact_refs=self.proposed_artifact_refs,
            requested_role_refs=self.requested_role_refs,
            application_id=self.application_id,
            reason=self.reason,
        )


class OpenAdmitRequest(BaseModel):
    """录取请求;SWARM_ALLOCATION 必须携带 target_stage_ref。"""

    admitted_scope_ref: str | None = Field(
        None, description="录取生效的作用域(仅对它生效)"
    )
    target_stage_ref: str | None = Field(
        None, description="SWARM_ALLOCATION 的目标 Stage 引用"
    )
    decision_id: str | None = Field(
        None, description="AdmissionDecision 幂等键(spec §8.3)"
    )
    reason: str = Field("", description="录取原因;落审计")

    def to_domain(self) -> OpenAdmitCommand:
        return OpenAdmitCommand(
            admitted_scope_ref=self.admitted_scope_ref,
            target_stage_ref=self.target_stage_ref,
            decision_id=self.decision_id,
            reason=self.reason,
        )


class OpenDecisionOpRequest(BaseModel):
    """申请裁决操作共用请求(screen/reject/withdraw/revoke)。"""

    reason: str = Field("", description="裁决原因;落审计")

    def to_domain(self) -> OpenDecisionOpCommand:
        return OpenDecisionOpCommand(reason=self.reason)


# ---------------------------------------------------------------------------
# 响应 DTO(spec §7 投影)
# ---------------------------------------------------------------------------


class MethodBindingDto(BaseModel):
    """``MethodBinding`` ``to_dict`` 投影。"""

    binding: dict[str, Any]


class MethodInspectionDto(BaseModel):
    """inspection 投影:binding 状态摘要 + capabilities + conformance
    evidence(spec §5)。"""

    binding: dict[str, Any]
    state_summary: dict[str, Any]
    capabilities: dict[str, Any]
    conformance_evidence: dict[str, Any]


class CompositionEdgeDto(BaseModel):
    """``MethodCompositionEdge`` ``to_dict`` 投影。"""

    edge: dict[str, Any]


class MethodOpAcceptedDto(BaseModel):
    """binding 生命周期/config 变更受理结果。"""

    accepted: bool
    collab_context_id: str
    method_binding_id: str
    state: str
    version: int


class ProfileOpAcceptedDto(BaseModel):
    """Profile 对象面操作受理结果(selection/member/plan/allocation/
    opportunity/application 等)。"""

    accepted: bool
    collab_context_id: str
    method_binding_id: str
    object_kind: str
    object_id: str
    state: str


class SwarmPlanValidationReportDto(BaseModel):
    """ValidatePlan 报告投影(transport-agnostic dict 的 HTTP 面)。"""

    swarm_plan_id: str
    valid: bool
    violations: list[str]
    stage_count: int
