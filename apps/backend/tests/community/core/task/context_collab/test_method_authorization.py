"""L5 方法操作 G1 结构授权接缝单测(Task 6,spec §8.1)。

矩阵式覆盖 ``StructuralMethodOpsAuthorizer``:
- 治理面操作(lifecycle/group/swarm/open 属主操作):owner principal
  通过、owner executor 通过、陌生 actor 拒绝(``ContextCollabValidationError``,
  错误消息点名 op 与 actor 身份);
- 读面操作(``inspect`` / ``validate_plan`` / ``submit_application``):
  任意已认证 actor 通过,即使 principal_id/executor_id 双 None;
- 身份注入语义:executor-only 上下文(executor_id 有值、principal_id 为
  None)executor 通过、principal 拒绝;双 None(上下文不可归属)治理
  操作对 owner 也一律拒绝;
- self-help(字符串精确相等,spec §12 第 8 条):``leave`` 比对
  ``member.participant_ref``,``withdraw`` 比对
  ``application.accountable_principal_ref``;目标行缺失即拒绝;
- 空 identity actor(``principal_id=""``)对一切操作拒绝,包括读面与
  self-help 中"字符串相等"会成立的退化情形;
- runtime 协议满足性:``StructuralMethodOpsAuthorizer`` 通过
  ``MethodOpsAuthorizerProtocol`` 的 ``isinstance`` 检查。
"""
from dataclasses import dataclass

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    GROUP_OWNER_OPS,
    LIFECYCLE_OWNER_OPS,
    MethodOpsAuthorizerProtocol,
    OPEN_OWNER_OPS,
    SWARM_OWNER_OPS,
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext

PRINCIPAL_ID = "u_1001"
EXECUTOR_ID = "bot_coding_001"
STRANGER_ID = "u_stranger"


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(
        actor=PrincipalRef(principal_type="human", principal_id=principal_id),
        source="api",
    )


def make_binding() -> MethodBinding:
    return MethodBinding(
        method_binding_id="binding-1",
        collab_context_id="ctx-1",
        method_kind="Group",
        method_names=("Group",),
        profile_versions={"Group": "0.2"},
        participant_ref=EXECUTOR_ID,
    )


@dataclass(frozen=True)
class MemberRow:
    """group 成员行投影;授权只读取 ``participant_ref`` 字段。"""

    participant_ref: str


@dataclass(frozen=True)
class ApplicationRow:
    """open 申请行投影;授权只读取 ``accountable_principal_ref`` 字段。"""

    accountable_principal_ref: str


# 治理面操作全集 × 四个接缝方法:owner 通过 / 陌生 actor 拒绝的矩阵输入。
OWNER_OP_CASES = [
    ("check_lifecycle_op", op) for op in LIFECYCLE_OWNER_OPS
] + [
    ("check_group_op", op) for op in GROUP_OWNER_OPS
] + [
    ("check_swarm_op", op) for op in SWARM_OWNER_OPS
] + [
    ("check_open_op", op) for op in OPEN_OWNER_OPS
]


def run_check(
    authorizer: StructuralMethodOpsAuthorizer,
    check_name: str,
    op: str,
    actor: ActorContext,
    *,
    principal_id: str | None = PRINCIPAL_ID,
    executor_id: str | None = EXECUTOR_ID,
    target=None,
):
    """以属主身份参数调用指定接缝方法;group/open 面把 member/application
    作为目标的第 4 个位置参数传入。"""
    method = getattr(authorizer, check_name)
    if check_name == "check_group_op":
        method(op, make_binding(), actor, target, principal_id, executor_id)
    elif check_name == "check_open_op":
        method(op, make_binding(), actor, target, principal_id, executor_id)
    else:
        method(op, make_binding(), actor, principal_id, executor_id)


def test_structural_authorizer_satisfies_protocol():
    assert isinstance(
        StructuralMethodOpsAuthorizer(), MethodOpsAuthorizerProtocol
    )


@pytest.mark.parametrize("check_name,op", OWNER_OP_CASES)
def test_owner_principal_and_executor_pass(check_name, op):
    authorizer = StructuralMethodOpsAuthorizer()
    run_check(authorizer, check_name, op, make_actor(PRINCIPAL_ID))
    run_check(authorizer, check_name, op, make_actor(EXECUTOR_ID))


@pytest.mark.parametrize("check_name,op", OWNER_OP_CASES)
def test_stranger_actor_denied(check_name, op):
    authorizer = StructuralMethodOpsAuthorizer()
    with pytest.raises(ContextCollabValidationError):
        run_check(authorizer, check_name, op, make_actor(STRANGER_ID))


@pytest.mark.parametrize("check_name,op", OWNER_OP_CASES)
def test_unattributable_context_denies_even_owners(check_name, op):
    # principal_id/executor_id 双 None:不可归属的上下文不能授权治理操作。
    authorizer = StructuralMethodOpsAuthorizer()
    with pytest.raises(ContextCollabValidationError):
        run_check(
            authorizer,
            check_name,
            op,
            make_actor(PRINCIPAL_ID),
            principal_id=None,
            executor_id=None,
        )


def test_denial_message_names_op_and_actor_identity():
    authorizer = StructuralMethodOpsAuthorizer()
    with pytest.raises(ContextCollabValidationError, match="activate") as excinfo:
        authorizer.check_lifecycle_op(
            "activate",
            make_binding(),
            make_actor(STRANGER_ID),
            principal_id=PRINCIPAL_ID,
            executor_id=EXECUTOR_ID,
        )
    assert STRANGER_ID in str(excinfo.value)


def test_executor_only_context_executor_passes_principal_denied():
    authorizer = StructuralMethodOpsAuthorizer()
    binding = make_binding()
    authorizer.check_lifecycle_op(
        "activate",
        binding,
        make_actor(EXECUTOR_ID),
        principal_id=None,
        executor_id=EXECUTOR_ID,
    )
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_lifecycle_op(
            "activate",
            binding,
            make_actor(PRINCIPAL_ID),
            principal_id=None,
            executor_id=EXECUTOR_ID,
        )


@pytest.mark.parametrize(
    "check_name,op",
    [
        ("check_lifecycle_op", "inspect"),
        ("check_swarm_op", "validate_plan"),
        ("check_open_op", "submit_application"),
    ],
)
def test_public_read_ops_allow_any_authenticated_actor(check_name, op):
    authorizer = StructuralMethodOpsAuthorizer()
    # 读面/公开提交:即使上下文不可归属(双 None)也放行已认证陌生 actor。
    run_check(
        authorizer,
        check_name,
        op,
        make_actor(STRANGER_ID),
        principal_id=None,
        executor_id=None,
    )
    # 属主身份参数齐全时同样放行。
    run_check(authorizer, check_name, op, make_actor(STRANGER_ID))


def test_leave_member_identity_passes():
    authorizer = StructuralMethodOpsAuthorizer()
    # 本人退出:陌生 actor 与 member.participant_ref 字符串相等即通过。
    authorizer.check_group_op(
        "leave",
        make_binding(),
        make_actor(STRANGER_ID),
        MemberRow(participant_ref=STRANGER_ID),
        PRINCIPAL_ID,
        EXECUTOR_ID,
    )


def test_leave_denies_mismatched_member_even_for_principal():
    authorizer = StructuralMethodOpsAuthorizer()
    member = MemberRow(participant_ref="u_other")
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_group_op(
            "leave",
            make_binding(),
            make_actor(PRINCIPAL_ID),
            member,
            PRINCIPAL_ID,
            EXECUTOR_ID,
        )
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_group_op(
            "leave",
            make_binding(),
            make_actor(STRANGER_ID),
            MemberRow(participant_ref=STRANGER_ID.upper()),
            PRINCIPAL_ID,
            EXECUTOR_ID,
        )


def test_leave_denies_missing_member():
    authorizer = StructuralMethodOpsAuthorizer()
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_group_op(
            "leave",
            make_binding(),
            make_actor(PRINCIPAL_ID),
            None,
            PRINCIPAL_ID,
            EXECUTOR_ID,
        )


def test_withdraw_applicant_identity_passes():
    authorizer = StructuralMethodOpsAuthorizer()
    authorizer.check_open_op(
        "withdraw",
        make_binding(),
        make_actor(STRANGER_ID),
        ApplicationRow(accountable_principal_ref=STRANGER_ID),
        PRINCIPAL_ID,
        EXECUTOR_ID,
    )


def test_withdraw_denies_mismatched_application_even_for_principal():
    authorizer = StructuralMethodOpsAuthorizer()
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_open_op(
            "withdraw",
            make_binding(),
            make_actor(PRINCIPAL_ID),
            ApplicationRow(accountable_principal_ref="u_other"),
            PRINCIPAL_ID,
            EXECUTOR_ID,
        )
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_open_op(
            "withdraw",
            make_binding(),
            make_actor(STRANGER_ID),
            ApplicationRow(accountable_principal_ref=STRANGER_ID.upper()),
            PRINCIPAL_ID,
            EXECUTOR_ID,
        )


def test_withdraw_denies_missing_application():
    authorizer = StructuralMethodOpsAuthorizer()
    with pytest.raises(ContextCollabValidationError):
        authorizer.check_open_op(
            "withdraw",
            make_binding(),
            make_actor(PRINCIPAL_ID),
            None,
            PRINCIPAL_ID,
            EXECUTOR_ID,
        )


def test_falsy_actor_identity_denied_everywhere():
    authorizer = StructuralMethodOpsAuthorizer()
    empty_actor = make_actor("")
    binding = make_binding()
    # 属主标识再齐全,空 identity actor 一律拒绝;self-help 的退化
    # "相等"(空串 == 空串)同样拒绝。
    deny_cases = [
        lambda: authorizer.check_lifecycle_op(
            "activate", binding, empty_actor, PRINCIPAL_ID, EXECUTOR_ID
        ),
        lambda: authorizer.check_lifecycle_op(
            "inspect", binding, empty_actor, PRINCIPAL_ID, EXECUTOR_ID
        ),
        lambda: authorizer.check_group_op(
            "invite", binding, empty_actor, None, PRINCIPAL_ID, EXECUTOR_ID
        ),
        lambda: authorizer.check_group_op(
            "leave",
            binding,
            empty_actor,
            MemberRow(participant_ref=""),
            PRINCIPAL_ID,
            EXECUTOR_ID,
        ),
        lambda: authorizer.check_swarm_op(
            "validate_plan", binding, empty_actor, PRINCIPAL_ID, EXECUTOR_ID
        ),
        lambda: authorizer.check_swarm_op(
            "adopt_plan", binding, empty_actor, PRINCIPAL_ID, EXECUTOR_ID
        ),
        lambda: authorizer.check_open_op(
            "submit_application", binding, empty_actor, None, PRINCIPAL_ID,
            EXECUTOR_ID,
        ),
        lambda: authorizer.check_open_op(
            "withdraw",
            binding,
            empty_actor,
            ApplicationRow(accountable_principal_ref=""),
            PRINCIPAL_ID,
            EXECUTOR_ID,
        ),
    ]
    for case in deny_cases:
        with pytest.raises(ContextCollabValidationError):
            case()
