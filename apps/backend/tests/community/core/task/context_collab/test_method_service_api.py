"""L5 Method Service API 契约结构测试(Task 7)。

纯结构测试,不落任何服务实现(T8–T12 才实现协议):
- 四个 Protocol 全为 ``runtime_checkable``,方法名与签名(参数名 +
  注解 + 返回类型)逐字对齐 plan 裁决(delivery 只依赖 Protocol,
  重命名即断链);
- 命令/结果 dataclass 字段名、顺序、默认值与裁决一致(幂等键可空
  缺省,``list``/``dict`` 默认走 ``default_factory``,必填字段在前);
- ``ActorContext`` 复用 ``service_api`` 既有定义(同一对象,不重定义)。
"""
import dataclasses
import inspect

import pytest

from agentclaw.community.core.task.context_collab import method_service_api
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    ComposeMethodCommand,
    CompositionEdgeResult,
    GroupChangeRoleCommand,
    GroupMemberCommand,
    GroupMethodOpsServiceProtocol,
    GroupReplaceMemberCommand,
    MethodBindingResult,
    MethodInspectionResult,
    MethodLifecycleServiceProtocol,
    MethodOpAcceptedResult,
    MethodStateChangeCommand,
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
    OpenOpportunityOpCommand,
    ProfileOpAcceptedResult,
    SingleDelegateCommand,
    SingleReplaceCommand,
    SingleSelectCommand,
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmMethodOpsServiceProtocol,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
    UpdateBindingConfigCommand,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext as ServiceApiActorContext,
)

MISSING = dataclasses.MISSING

# 统一尾参:所有方法以 ..., command, context: ActorContext 收尾。
CTX = ("context", "ActorContext")


def _params(*names_and_annotations: tuple[str, str]) -> list:
    return list(names_and_annotations)


def _binding_ops(command: str, returns: str, *locator: tuple[str, str]):
    """profile 操作族签名:collab_context_id + method_binding_id + 定位参数。"""
    return _params(
        ("collab_context_id", "str"),
        ("method_binding_id", "str"),
        *locator,
        ("command", command),
        CTX,
    ), returns


# 命令 -> 有序 (字段名, 期望默认值) 表;MISSING = 必填无默认。
COMMAND_FIELDS: dict[type, list[tuple[str, object]]] = {
    BindMethodCommand: [
        ("method_kind", MISSING),
        ("method_binding_id", None),
        ("parent_scope_ref", None),
        ("participant_ref", None),
        ("config", {}),
        ("bind_reason", ""),
    ],
    MethodStateChangeCommand: [("reason", "")],
    UpdateBindingConfigCommand: [
        ("config", MISSING),
        ("expected_version", MISSING),
    ],
    ComposeMethodCommand: [
        ("relation", MISSING),
        ("child_binding_id", MISSING),
        ("child_method_kind", MISSING),
        ("edge_id", None),
        ("reason", ""),
    ],
    SingleSelectCommand: [
        ("agent_participant_ref", MISSING),
        ("scope_ref", MISSING),
        ("requirement_refs", []),
        ("selection_evidence_refs", []),
        ("valid_until", None),
        ("selection_id", None),
        ("reason", ""),
    ],
    SingleReplaceCommand: [
        ("agent_participant_ref", MISSING),
        ("scope_ref", MISSING),
        ("reason", ""),
    ],
    SingleDelegateCommand: [
        ("method_kind", MISSING),
        ("parent_scope_ref", MISSING),
        ("child_binding_id", None),
        ("edge_id", None),
        ("config", {}),
        ("reason", ""),
    ],
    GroupMemberCommand: [
        ("participant_ref", MISSING),
        ("address", ""),
        ("role_binding_refs", []),
        ("handover_to", None),
        ("member_binding_id", None),
        ("reason", ""),
    ],
    GroupChangeRoleCommand: [
        ("participant_ref", MISSING),
        ("role_binding_refs", MISSING),
        ("expected_revision", MISSING),
        ("reason", ""),
    ],
    GroupReplaceMemberCommand: [
        ("old_participant_ref", MISSING),
        ("new_participant_ref", MISSING),
        ("address", ""),
        ("role_binding_refs", []),
        ("reason", ""),
    ],
    SwarmExecutionProfileCommand: [
        ("mode", MISSING),
        ("target_ref", MISSING),
        ("supported_work_kinds", MISSING),
        ("input_contract_ref", MISSING),
        ("execution_profile_id", None),
        ("output_contract_ref", ""),
        ("lifecycle_mapping", {}),
    ],
    SwarmPlanCandidateCommand: [
        ("task_ref", MISSING),
        ("swarm_plan_id", None),
        ("max_parallelism", 1),
        ("max_depth", 1),
        ("budget_resource_refs", []),
        ("termination_condition", {}),
        ("stages", []),
        ("reason", ""),
    ],
    SwarmPlanOpCommand: [("reason", "")],
    SwarmAllocateCommand: [
        ("stage_ref", MISSING),
        ("target_kind", MISSING),
        ("target_ref", MISSING),
        ("execution_profile_ref", MISSING),
        ("lease_expires_at", MISSING),
        ("qualification_evidence_refs", []),
        ("allocation_id", None),
        ("reason", ""),
    ],
    SwarmStageOutcomeCommand: [
        ("allocation_refs", []),
        ("reason", ""),
    ],
    OpenOpportunityCommand: [
        ("scope_ref", MISSING),
        ("participation_type", MISSING),
        ("opens_at", MISSING),
        ("opportunity_id", None),
        ("requirement_refs", []),
        ("admission_policy_refs", []),
        ("capacity", None),
        ("closes_at", None),
        ("reason", ""),
    ],
    OpenOpportunityOpCommand: [("reason", "")],
    OpenApplicationCommand: [
        ("applicant_agent_ref", MISSING),
        ("accountable_principal_ref", MISSING),
        ("capability_evidence_refs", []),
        ("proposed_artifact_refs", []),
        ("requested_role_refs", []),
        ("application_id", None),
        ("reason", ""),
    ],
    OpenAdmitCommand: [
        ("admitted_scope_ref", None),
        ("target_stage_ref", None),
        ("decision_id", None),
        ("reason", ""),
    ],
    OpenDecisionOpCommand: [("reason", "")],
}

# 结果 -> 有序必填字段(结果对象不设默认,字段名/顺序入裁决)。
RESULT_FIELDS: dict[type, list[str]] = {
    MethodBindingResult: ["binding"],
    MethodInspectionResult: [
        "binding",
        "state_summary",
        "capabilities",
        "conformance_evidence",
    ],
    CompositionEdgeResult: ["edge"],
    MethodOpAcceptedResult: [
        "accepted",
        "collab_context_id",
        "method_binding_id",
        "state",
        "version",
    ],
    ProfileOpAcceptedResult: [
        "accepted",
        "collab_context_id",
        "method_binding_id",
        "object_kind",
        "object_id",
        "state",
    ],
}

# 协议方法 -> (入参 (参数名, 注解字符串) 列表, 返回注解);spec §5 + plan 裁决。
LIFECYCLE_SIGNATURES: dict[str, tuple] = {
    "bind": (
        _params(
            ("collab_context_id", "str"),
            ("command", "BindMethodCommand"),
            CTX,
        ),
        "MethodBindingResult",
    ),
    # activate/suspend/resume/terminate/update_config/inspect 同以 method_binding_id 定位。
    "activate": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            ("command", "MethodStateChangeCommand"),
            CTX,
        ),
        "MethodOpAcceptedResult",
    ),
    "suspend": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            ("command", "MethodStateChangeCommand"),
            CTX,
        ),
        "MethodOpAcceptedResult",
    ),
    "resume": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            ("command", "MethodStateChangeCommand"),
            CTX,
        ),
        "MethodOpAcceptedResult",
    ),
    "terminate": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            ("command", "MethodStateChangeCommand"),
            CTX,
        ),
        "MethodOpAcceptedResult",
    ),
    "update_config": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            ("command", "UpdateBindingConfigCommand"),
            CTX,
        ),
        "MethodOpAcceptedResult",
    ),
    "inspect": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            CTX,
        ),
        "MethodInspectionResult",
    ),
    "compose": (
        _params(
            ("collab_context_id", "str"),
            ("parent_binding_id", "str"),
            ("command", "ComposeMethodCommand"),
            CTX,
        ),
        "CompositionEdgeResult",
    ),
    "detach": (
        _params(
            ("collab_context_id", "str"),
            ("edge_id", "str"),
            ("command", "MethodStateChangeCommand"),
            CTX,
        ),
        "CompositionEdgeResult",
    ),
}

GROUP_SIGNATURES: dict[str, tuple] = {
    "select": _binding_ops(
        "SingleSelectCommand", "ProfileOpAcceptedResult"
    ),
    "replace": _binding_ops(
        "SingleReplaceCommand", "ProfileOpAcceptedResult"
    ),
    "release": _binding_ops(
        "MethodStateChangeCommand", "ProfileOpAcceptedResult"
    ),
    "delegate": _binding_ops("SingleDelegateCommand", "CompositionEdgeResult"),
    "invite": _binding_ops("GroupMemberCommand", "ProfileOpAcceptedResult"),
    "add_member": _binding_ops("GroupMemberCommand", "ProfileOpAcceptedResult"),
    "suspend_member": _binding_ops(
        "GroupMemberCommand", "ProfileOpAcceptedResult"
    ),
    "resume_member": _binding_ops(
        "GroupMemberCommand", "ProfileOpAcceptedResult"
    ),
    "leave": _binding_ops("GroupMemberCommand", "ProfileOpAcceptedResult"),
    "remove_member": _binding_ops("GroupMemberCommand", "ProfileOpAcceptedResult"),
    "replace_member": _binding_ops(
        "GroupReplaceMemberCommand", "ProfileOpAcceptedResult"
    ),
    "change_role": _binding_ops(
        "GroupChangeRoleCommand", "ProfileOpAcceptedResult"
    ),
}

SWARM_SIGNATURES: dict[str, tuple] = {
    "declare_execution_profile": _binding_ops(
        "SwarmExecutionProfileCommand", "ProfileOpAcceptedResult"
    ),
    "submit_plan_candidate": _binding_ops(
        "SwarmPlanCandidateCommand", "ProfileOpAcceptedResult"
    ),
    # validate_plan 只读报告,无命令对象(spec §4.3:结构校验不改状态)。
    "validate_plan": (
        _params(
            ("collab_context_id", "str"),
            ("method_binding_id", "str"),
            ("swarm_plan_id", "str"),
            CTX,
        ),
        "dict",
    ),
    "adopt_plan": _binding_ops(
        "SwarmPlanOpCommand", "ProfileOpAcceptedResult", ("swarm_plan_id", "str")
    ),
    "start_plan": _binding_ops(
        "SwarmPlanOpCommand", "ProfileOpAcceptedResult", ("swarm_plan_id", "str")
    ),
    "pause_plan": _binding_ops(
        "SwarmPlanOpCommand", "ProfileOpAcceptedResult", ("swarm_plan_id", "str")
    ),
    "resume_plan": _binding_ops(
        "SwarmPlanOpCommand", "ProfileOpAcceptedResult", ("swarm_plan_id", "str")
    ),
    "terminate_plan": _binding_ops(
        "SwarmPlanOpCommand", "ProfileOpAcceptedResult", ("swarm_plan_id", "str")
    ),
    "allocate_stage": _binding_ops(
        "SwarmAllocateCommand", "ProfileOpAcceptedResult"
    ),
    "complete_stage": _binding_ops(
        "SwarmStageOutcomeCommand", "ProfileOpAcceptedResult", ("stage_ref", "str")
    ),
    "fail_stage": _binding_ops(
        "SwarmStageOutcomeCommand", "ProfileOpAcceptedResult", ("stage_ref", "str")
    ),
}

OPEN_SIGNATURES: dict[str, tuple] = {
    "publish_opportunity": _binding_ops(
        "OpenOpportunityCommand", "ProfileOpAcceptedResult"
    ),
    "pause_opportunity": _binding_ops(
        "OpenOpportunityOpCommand", "ProfileOpAcceptedResult",
        ("opportunity_ref", "str"),
    ),
    "reopen_opportunity": _binding_ops(
        "OpenOpportunityOpCommand", "ProfileOpAcceptedResult",
        ("opportunity_ref", "str"),
    ),
    "close_opportunity": _binding_ops(
        "OpenOpportunityOpCommand", "ProfileOpAcceptedResult",
        ("opportunity_ref", "str"),
    ),
    "submit_application": _binding_ops(
        "OpenApplicationCommand", "ProfileOpAcceptedResult",
        ("opportunity_ref", "str"),
    ),
    "screen": _binding_ops(
        "OpenDecisionOpCommand", "ProfileOpAcceptedResult",
        ("application_ref", "str"),
    ),
    "reject": _binding_ops(
        "OpenDecisionOpCommand", "ProfileOpAcceptedResult",
        ("application_ref", "str"),
    ),
    "withdraw": _binding_ops(
        "OpenDecisionOpCommand", "ProfileOpAcceptedResult",
        ("application_ref", "str"),
    ),
    "admit": _binding_ops(
        "OpenAdmitCommand", "ProfileOpAcceptedResult",
        ("application_ref", "str"),
    ),
    "revoke_admission": _binding_ops(
        "OpenDecisionOpCommand", "ProfileOpAcceptedResult",
        ("application_ref", "str"),
    ),
}

ALL_PROTOCOL_SIGNATURES = (
    (MethodLifecycleServiceProtocol, LIFECYCLE_SIGNATURES),
    (GroupMethodOpsServiceProtocol, GROUP_SIGNATURES),
    (SwarmMethodOpsServiceProtocol, SWARM_SIGNATURES),
    (OpenMethodOpsServiceProtocol, OPEN_SIGNATURES),
)


@pytest.mark.parametrize(
    ("cls", "expected"),
    sorted(COMMAND_FIELDS.items(), key=lambda item: item[0].__name__),
)
def test_command_fields_and_defaults(cls, expected):
    """命令字段名、顺序与默认值逐一对齐裁决;必填字段无默认。"""
    fields = dataclasses.fields(cls)
    assert [field.name for field in fields] == [name for name, _ in expected]
    for field, (_, default) in zip(fields, expected):
        if default is MISSING:
            assert field.default is MISSING
            assert field.default_factory is MISSING
        elif isinstance(default, (list, dict)):
            # list/dict 默认必须 default_factory,避免实例间共享可变默认。
            assert field.default is MISSING
            assert field.default_factory is not MISSING
        else:
            assert field.default == default
            assert field.default_factory is MISSING


def test_command_required_fields_enforced():
    """必填字段调用方必须显式给;缺参抛 TypeError 而非静默默认。"""
    with pytest.raises(TypeError):
        SingleSelectCommand()
    command = BindMethodCommand(method_kind="Group")
    # 幂等键缺省 None -> 服务端生成(spec §8.3);同型命令两次构造的
    # config 默认互不共享。
    assert command.method_binding_id is None
    assert command.config == {}
    assert command.config is not BindMethodCommand(method_kind="Group").config


def test_result_fields_required():
    """结果对象字段全必填,顺序入裁决。"""
    for cls, names in sorted(
        RESULT_FIELDS.items(), key=lambda item: item[0].__name__
    ):
        assert [field.name for field in dataclasses.fields(cls)] == names
        for field in dataclasses.fields(cls):
            assert field.default is MISSING
            assert field.default_factory is MISSING


@pytest.mark.parametrize(
    ("protocol", "signatures"),
    ALL_PROTOCOL_SIGNATURES,
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_protocol_methods_and_signatures(protocol, signatures):
    """协议方法名齐全,签名(参数名 + 注解 + 返回)逐字对齐裁决。"""
    for method, (expected_params, returns) in signatures.items():
        signature = inspect.signature(getattr(protocol, method))
        params = [
            (parameter.name, parameter.annotation)
            for parameter in signature.parameters.values()
            if parameter.name != "self"
        ]
        assert params == expected_params, f"{protocol.__name__}.{method}"
        assert signature.return_annotation == returns, (
            f"{protocol.__name__}.{method}"
        )


def _fake_service(method_names: list[str]):
    """以 type() 工厂拼一个实现全部方法名的最小假件。"""

    def _make_method(_name):
        def _run(self, *args, **kwargs):  # pragma: no cover - 结构测试不执行
            raise AssertionError("结构测试不应调用协议实现")

        return _run

    namespace = {name: _make_method(name) for name in method_names}
    return type("FakeMethodService", (), namespace)()


@pytest.mark.parametrize(
    ("protocol", "signatures"),
    ALL_PROTOCOL_SIGNATURES,
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_protocols_runtime_checkable(protocol, signatures):
    """四协议均 runtime_checkable:方法齐全的最小假件 isinstance 通过。"""
    assert isinstance(_fake_service(list(signatures)), protocol)
    # 反例:缺方法的对象不满足协议(不存在"空协议"误判)。
    assert not isinstance(_fake_service([]), protocol)
    assert not isinstance(object(), protocol)


def test_actor_context_reused_from_service_api():
    """ActorContext 复用 service_api 既有定义(同一对象,不重定义)。"""
    assert method_service_api.ActorContext is ServiceApiActorContext
