"""Shared fixtures for Group Method contract and lifecycle tests."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    GroupRoster,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    GroupMemberCommand,
    MethodStateChangeCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    MethodLifecycleService,
    current_axes,
)
from agentclaw.community.core.task.context_collab.services.single_group_method_service import (
    SingleGroupMethodService,
)

from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

COLLET_ID = "collet_group_001"


PRINCIPAL_ID = "u_2001"


EXECUTOR_ID = "bot_group_exec_001"


NOW = "2026-11-12T08:00:00+00:00"


OBJECTIVE = "为 BBS 公开讨论落 Group 剖面 roster/成员语义测试"


AXES = current_axes()


GROUP_MACHINE = METHOD_MACHINE_REGISTRY["group_member"]


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _context_spec(*, method_kind: str = "Open + Group") -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef("human", PRINCIPAL_ID),
        method_kind=method_kind,
        executor_ref=AgentRef("bot", EXECUTOR_ID),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _status_record(**overrides) -> ContextStatusRecord:
    """构造受控 ``ContextStatusRecord``(与 lifecycle 单测同款)。"""
    fields = {
        "collab_context_id": COLLET_ID,
        "status": TaskStatus.IN_PROGRESS.value,
        "method_kind": "Open + Group",
    }
    fields.update(overrides)
    spec = _context_spec(method_kind=fields["method_kind"])
    principal_ref = PrincipalRef("human", PRINCIPAL_ID)
    executor_ref = AgentRef("bot", EXECUTOR_ID)
    snapshot = CurrentSnapshot(
        objective=OBJECTIVE,
        status=fields["status"],
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        method_kind=fields["method_kind"],
        progress=ProgressSnapshot(),
    )
    collet = Collet(
        collet_id=fields["collab_context_id"],
        initiator_ref=principal_ref,
        state=ColletState.ACTIVE,
        scope=spec.space,
        current_event_id=None,
        version=1,
    )
    return ContextStatusRecord(
        collet=collet,
        context_spec=spec,
        current_snapshot=snapshot,
        events=[],
    )


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 context 返回受控 ``ContextStatusRecord``。"""

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")

    def __init__(self, records: dict[str, ContextStatusRecord]) -> None:
        self._records = dict(records)

    def get_current_status(self, collab_context_id: str):
        return self._records.get(collab_context_id)

    def create_context(self, request):
        raise NotImplementedError("fake only implements get_current_status")

    def append_event(self, collab_context_id: str, request):
        raise NotImplementedError("fake only implements get_current_status")


def make_group_service():
    """真 store + 真 authorizer + 真 lifecycle + 受控 fake 仓储的服务四件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        product_catalog=PRODUCT_CATALOG,
    )
    service = SingleGroupMethodService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        lifecycle=lifecycle,
    )
    return store, repo, lifecycle, service


def active_group_binding(
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_group_alpha",
    collab_context_id: str = COLLET_ID,
) -> str:
    """bind + activate 一条嵌套 Group binding(group 八操作的前置)。"""
    lifecycle.bind(
        collab_context_id,
        BindMethodCommand(
            method_kind="Group",
            method_binding_id=binding_id,
            parent_scope_ref="task_scope_root",
            bind_reason="compose",
        ),
        make_actor(),
    )
    lifecycle.activate(
        collab_context_id, binding_id, MethodStateChangeCommand(), make_actor()
    )
    return binding_id


def member_command(**overrides) -> GroupMemberCommand:
    """group 成员操作共用命令基型;字段可覆盖。"""
    fields = {"participant_ref": "bot_alpha_001", "reason": ""}
    fields.update(overrides)
    return GroupMemberCommand(**fields)


def roster_dict(**overrides) -> dict:
    """GroupRoster 的 ``from_dict`` 输入基型;字段可覆盖。"""
    fields = {
        "roster_id": "roster_001",
        "method_binding_id": "mb_group_alpha",
        "revision": 1,
    }
    fields.update(overrides)
    return fields


def member_dict(**overrides) -> dict:
    """GroupMemberBinding 的 ``from_dict`` 输入基型;字段可覆盖。"""
    fields = {
        "group_member_binding_id": "gm_001",
        "method_binding_id": "mb_group_alpha",
        "participant_ref": "bot_alpha_001",
        "address": "agent://bot_alpha_001",
        "role_binding_refs": [],
        "state": "ACTIVE",
        "joined_at": NOW,
        "left_at": None,
        "transitioned_from": "INVITED",
        "transition_reason": "group.add_member",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def make_group_store():
    """带一条 revision=1 roster 的裸 store(store 切片测试前置)。"""
    store = InMemoryMethodStore()
    roster = store.insert_roster(AXES, GroupRoster.from_dict(roster_dict()), COLLET_ID)
    assert roster.revision == 1
    return store


OWNER_OPS_ALL = (
    "invite",
    "add_member",
    "suspend_member",
    "resume_member",
    "remove_member",
    "replace_member",
    "change_role",
)
