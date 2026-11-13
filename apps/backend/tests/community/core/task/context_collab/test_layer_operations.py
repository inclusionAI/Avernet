"""Functional layer ports: no ContextSpec/ProductCatalog needed inside L4/L5."""

from dataclasses import dataclass, replace
from importlib import import_module

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
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
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.collet_lifecycle import (
    ColletLifecycleService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.work import (
    prepare_event_append,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenOpportunityCommand,
    SingleSelectCommand,
    SwarmExecutionProfileCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.initialization import (
    ResolvedMethodSpec,
    initialize_top_level_binding,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.lifecycle import (
    MethodLifecycleService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.single_group import (
    SingleGroupMethodService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.service import (
    SwarmMethodService,
)
from agentclaw.community.core.task.context_collab.persistence.collet_lifecycle_store import (
    InMemoryColletLifecycleStore,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = PrincipalRef("bot", "worker")
COLLET_ID = "collet_operations"
NOW = "2026-11-13T08:00:00+00:00"


def make_task():
    return Task(
        task_id="task_operations",
        collet_ref=COLLET_ID,
        version=7,
        objective="Produce a report",
        status="created",
        principal_ref=OWNER.actor,
        executor_ref=AgentRef("bot", "worker"),
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_created",
        last_event_ref="evt_created",
    )


@dataclass
class LowerRecord:
    collet: Collet
    task: Task
    owner_principal_id: str = "owner"
    designated_executor_id: str = "worker"


class Reader:
    def get_current_status(self, collab_context_id):
        assert collab_context_id == COLLET_ID
        return LowerRecord(
            Collet(
                COLLET_ID,
                OWNER.actor,
                ColletState.ACTIVE,
                SpaceRef("space", "test"),
                None,
                11,
            ),
            make_task(),
        )


class Recovery:
    def __init__(self, store, kind):
        self.store = store
        self.kind = kind
        self.calls = 0

    def recover(self, collab_context_id, actor, *, now):
        self.calls += 1
        return initialize_top_level_binding(
            self.store,
            current_axes(),
            collab_context_id,
            ResolvedMethodSpec(self.kind, {self.kind: "1.0"}, (), "owner", "worker"),
            actor,
            now=now,
        )


@pytest.mark.parametrize("kind", ["Single", "Group", "Open", "Swarm"])
def test_method_operations_use_lower_reader_and_resolved_facts(kind):
    store, reader = InMemoryMethodStore(), Reader()
    recovery = Recovery(store, kind)
    authorizer = StructuralMethodOpsAuthorizer()
    lifecycle = MethodLifecycleService(store, authorizer, reader, recovery=recovery)
    binding_id = top_level_binding_id(COLLET_ID)
    lifecycle.inspect(COLLET_ID, binding_id, OWNER)
    lifecycle.inspect(COLLET_ID, binding_id, OWNER)
    assert recovery.calls == 1
    assert not hasattr(reader.get_current_status(COLLET_ID), "context_spec")
    if kind == "Single":
        service = SingleGroupMethodService(store, authorizer, reader, lifecycle)
        result = service.select(
            COLLET_ID, binding_id, SingleSelectCommand("worker", "scope"), OWNER
        )
        assert result.state == "ACTIVE"
    elif kind == "Group":
        members = store.list_members(current_axes(), binding_id, COLLET_ID)
        assert {member.participant_ref for member in members} == {"owner", "worker"}
    elif kind == "Open":
        service = OpenMethodService(store, authorizer, reader)
        result = service.publish_opportunity(
            COLLET_ID,
            binding_id,
            OpenOpportunityCommand("scope", "SINGLE_CONTRIBUTION", NOW),
            OWNER,
        )
        assert result.state == "OPEN"
    else:
        service = SwarmMethodService(store, authorizer, reader)
        result = service.declare_execution_profile(
            COLLET_ID,
            binding_id,
            SwarmExecutionProfileCommand(
                "A2A_AGENT", "agent://worker", ["EXECUTE"], "contract://input"
            ),
            OWNER,
        )
        assert result.accepted


def test_collet_lifecycle_does_not_read_l6_configuration():
    service = ColletLifecycleService(InMemoryColletLifecycleStore(), Reader())
    assert service.suspend(COLLET_ID, OWNER).new_state is ColletState.SUSPENDED
    # Preserve the existing ID-only executor governance rule (not Bot reporting).
    executor = ActorContext(PrincipalRef("human", "worker"), "api")
    assert service.resume(COLLET_ID, executor).new_state is ColletState.ACTIVE


def request(event_kind="task.started", primitive_type="Task"):
    return AppendEventRecordRequest(
        11, event_kind, primitive_type, BOT, None, "public", {}
    )


def test_work_preparation_resolves_and_fences_task_without_writing():
    prepared = prepare_event_append(
        COLLET_ID,
        request(),
        task=make_task(),
        actor=BOT,
        workflow_kind="task_commission_goal",
        workflow_version=1,
    )
    assert prepared.request.task_update.task_ref == "task_operations"
    assert prepared.request.task_update.expected_version == 7
    assert prepared.request.task_update.status == "in_progress"
    assert prepared.request.expected_version == 11
    assert make_task().status == "created"


@pytest.mark.parametrize("error", [ContextCollabVersionConflict, RuntimeError])
def test_work_commit_propagates_repository_conflicts_and_write_failures(error):
    prepared = prepare_event_append(
        COLLET_ID,
        request(),
        task=make_task(),
        actor=BOT,
        workflow_kind="task_commission_goal",
    )

    class Writer:
        def append_event(self, collab_context_id, command):
            assert collab_context_id == COLLET_ID
            assert command is prepared.request
            raise error("write rejected")

    with pytest.raises(error, match="write rejected"):
        prepared.commit(Writer())


def test_message_cannot_advance_task_and_partial_legacy_work_is_rejected():
    message = request("message.posted", "Message")
    prepared = prepare_event_append(
        COLLET_ID,
        message,
        task=make_task(),
        actor=BOT,
        workflow_kind="task_commission_goal",
    )
    assert prepared.request.task_update is None
    prepared = prepare_event_append(
        COLLET_ID, message, task=None, actor=BOT, workflow_kind=None
    )
    assert prepared.request.task_update is None
    with pytest.raises(ContextCollabValidationError, match="without an L4 Task"):
        prepare_event_append(
            COLLET_ID,
            request(),
            task=None,
            actor=BOT,
            workflow_kind="task_commission_goal",
        )
    with pytest.raises(ContextCollabValidationError, match="must be resolved"):
        prepare_event_append(
            COLLET_ID,
            replace(
                message,
                task_update=prepare_event_append(
                    COLLET_ID,
                    request(),
                    task=make_task(),
                    actor=BOT,
                    workflow_kind="task_commission_goal",
                ).request.task_update,
            ),
            task=make_task(),
            actor=BOT,
            workflow_kind=None,
        )


def test_legacy_exports_preserve_canonical_contract_and_implementation_identity():
    base = "agentclaw.community.core.task.context_collab."
    for legacy, canonical, name in (
        (
            "service_api",
            "l4_context_primitives.operations.contracts",
            "EventAppendCommand",
        ),
        (
            "persistence.repository",
            "l4_context_primitives.operations.contracts",
            "AppendEventRecordRequest",
        ),
        (
            "persistence.collet_lifecycle_store",
            "l4_context_primitives.operations.contracts",
            "ColletLifecycleStore",
        ),
        (
            "method_service_api",
            "l5_methods.operations.contracts",
            "OpenOpportunityCommand",
        ),
        (
            "method_service_api",
            "l5_methods.operations.contracts",
            "MethodLifecycleServiceProtocol",
        ),
        (
            "services.collet_lifecycle_service",
            "l4_context_primitives.operations.collet_lifecycle",
            "ColletLifecycleService",
        ),
        (
            "services.single_group_method_service",
            "l5_methods.operations.single_group",
            "SingleGroupMethodService",
        ),
        (
            "services.open_method_service",
            "l5_methods.operations.open.service",
            "OpenMethodService",
        ),
        (
            "services.swarm_method_service",
            "l5_methods.operations.swarm.service",
            "SwarmMethodService",
        ),
    ):
        assert getattr(import_module(base + legacy), name) is getattr(
            import_module(base + canonical), name
        )
