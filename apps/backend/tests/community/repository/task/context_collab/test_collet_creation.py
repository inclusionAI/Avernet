"""Pure L4 creation: real SQL, executor cardinality and no upper-layer effects."""

from dataclasses import fields, replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ColletCreateCommand as ExportedColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.service_api import (
    EventAppendCommand,
)
from sqlalchemy import event as sql_event

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
WORKERS = (AgentRef("bot", "worker-1"), AgentRef("bot", "worker-2"))


class NoUpperAccess:
    def __getattr__(self, name):
        raise AssertionError(f"L4 creation must not access upper-layer port: {name}")


def _command(**kwargs):
    return ColletCreateCommand(
        to_space=SpaceRef("space", "test"),
        initiator_ref=OWNER.actor,
        objective="Deliver independently identified work",
        **kwargs,
    )


@pytest.fixture
def runtime(db):
    forbidden = NoUpperAccess()
    repo = ContextCollabRepository(db, product_catalog=forbidden)
    service = ContextCollabService(repo, forbidden, product_catalog=forbidden)
    return service, repo


def test_create_is_unassigned_without_upper_access(runtime, db):
    service, repo = runtime
    command = _command()
    collet = service.create_collet(command, OWNER)
    record = repo.get_current_status(collet.collet_id)
    assert ExportedColletCreateCommand is ColletCreateCommand
    assert {field.name for field in fields(command)} == {
        "to_space",
        "initiator_ref",
        "objective",
        "title",
        "description",
    }
    assert record.collet == collet
    assert record.task.executor_refs == ()
    assert record.task.task_id != collet.collet_id
    assert record.task.version == record.collet.version == 1
    assert record.product_instance is None
    assert record.participants == record.role_bindings == ()
    assert len(record.events) == 1
    event = record.events[0]
    assert event.event_kind == "task.created" and event.primitive_type == "Task"
    assert event.actor_ref == OWNER.actor
    assert event.event_id == record.task.created_event_ref
    assert event.task_ref == record.task.task_id and event.task_version_after == 1
    assert (
        event.payload
        == record.creation_facts
        == {"objective": command.objective, "executor_refs": []}
    )
    with db.orm_session() as session:
        for model in (
            ContextCollabColletModel,
            ContextCollabTaskModel,
            ContextCollabEventModel,
        ):
            assert session.query(model).count() == 1


def test_command_rejects_legacy_principal_keyword():
    with pytest.raises(TypeError, match="principal_ref"):
        _command(principal_ref=OWNER.actor)


@pytest.mark.parametrize(
    "initiator", [None, "owner", WORKERS[0], PrincipalRef("human", "")]
)
def test_command_requires_valid_initiator_reference(initiator):
    with pytest.raises(ContextCollabValidationError):
        ColletCreateCommand(
            to_space=SpaceRef("space", "test"),
            initiator_ref=initiator,
            objective="Deliver work",
        )


@pytest.mark.parametrize(
    "refs",
    [
        None,
        "worker",
        WORKERS[0],
        ("worker",),
        (None,),
        (WORKERS[0], WORKERS[0]),
        (AgentRef("bot", ""),),
    ],
)
def test_invalid_executor_collection_rejected(refs):
    with pytest.raises(TypeError, match="executor_refs"):
        _command(executor_refs=refs)


@pytest.mark.parametrize("objective", [None, "", "  ", 1])
def test_objective_required(objective):
    with pytest.raises(ContextCollabValidationError, match="objective"):
        ColletCreateCommand(SpaceRef("s", "test"), OWNER.actor, objective)


@pytest.mark.parametrize(
    "extra",
    [
        "product_profile_ref",
        "profile_id",
        "method_kind",
        "method_binding",
        "role_bindings",
        "resource_bindings",
    ],
)
def test_command_rejects_upper_layer_inputs(extra):
    with pytest.raises(TypeError, match=extra):
        _command(**{extra: "not-an-l4-input"})


@pytest.mark.parametrize(
    "failure_target", [ContextCollabTaskModel, ContextCollabEventModel]
)
def test_sql_failure_rolls_back_entire_creation(runtime, db, failure_target):
    service, _ = runtime

    def fail(*_):
        raise RuntimeError("injected L4 write failure")

    sql_event.listen(failure_target, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="injected L4 write failure"):
            service.create_collet(_command(), OWNER)
    finally:
        sql_event.remove(failure_target, "before_insert", fail)
    with db.orm_session() as session:
        for model in (
            ContextCollabColletModel,
            ContextCollabTaskModel,
            ContextCollabEventModel,
        ):
            assert session.query(model).count() == 0


def test_creation_authorization_runs_before_writer_and_failures_propagate():
    calls = []

    class Writer:
        def create_l4_collet(self, command, actor_ref):
            calls.append((command, actor_ref))
            raise RuntimeError("writer failed")

    class Deny:
        def check_create_collet(self, *, command, actor):
            raise ContextCollabValidationError("creation denied")

    command = _command()
    with pytest.raises(ContextCollabValidationError, match="creation denied"):
        ContextService(Writer(), NoUpperAccess(), Deny()).create_collet(command, OWNER)
    assert calls == []
    with pytest.raises(RuntimeError, match="writer failed"):
        ContextService(Writer(), NoUpperAccess()).create_collet(command, OWNER)
    assert calls == [(command, OWNER.actor)]


@pytest.mark.parametrize(
    "actor",
    [
        None,
        ActorContext(PrincipalRef("human", ""), "api"),
        ActorContext(OWNER.actor, ""),
    ],
)
def test_structural_creation_authorizer_rejects_invalid_actor(actor):
    service = ContextService(NoUpperAccess(), NoUpperAccess())
    with pytest.raises(ContextCollabValidationError):
        service.create_collet(_command(), actor)


def test_task_plural_value_roundtrip_and_explicit_single_legacy_read(runtime):
    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    task = replace(
        repo.get_current_status(collet.collet_id).task, executor_refs=WORKERS
    )
    assert Task.from_dict(task.to_dict()) == task
    legacy = task.to_dict()
    del legacy["executor_refs"]
    legacy["executor_ref"] = WORKERS[0].to_dict()
    assert Task.from_dict(legacy).executor_refs == WORKERS[:1]
    legacy["executor_ref"] = None
    assert Task.from_dict(legacy).executor_refs == ()


@pytest.mark.parametrize(
    "refs",
    [None, "bad", [None], ["bot"], [{}], [WORKERS[0].to_dict(), WORKERS[0].to_dict()]],
)
def test_malformed_persisted_executor_list_fails_closed(runtime, refs):
    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    data = repo.get_current_status(collet.collet_id).task.to_dict()
    data["executor_refs"] = refs
    with pytest.raises(ContextCollabValidationError):
        Task.from_dict(data)
    with pytest.raises(ContextCollabValidationError):
        decode_creation_facts({"objective": "work", "executor_refs": refs})


def test_persisted_task_rejects_ambiguous_single_and_plural(runtime):
    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    data = repo.get_current_status(collet.collet_id).task.to_dict()
    data["executor_ref"] = None
    with pytest.raises(ContextCollabValidationError, match="ambiguous"):
        Task.from_dict(data)


@pytest.mark.parametrize("executor", WORKERS)
def test_each_designated_executor_passes_l4_work_guard(runtime, executor):
    from agentclaw.community.core.task.context_collab.g4_contracts_consistency.workflow_resolution import (
        resolve_workflow_status,
    )
    from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
        STANDARD_TASK_CONTRACT,
    )

    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    task = replace(
        repo.get_current_status(collet.collet_id).task, executor_refs=WORKERS
    )
    machine = STANDARD_TASK_CONTRACT.state_machine_ref
    assert (
        resolve_workflow_status(
            workflow_kind=machine.profile_id,
            workflow_version=machine.version,
            task=task,
            event_kind="task.started",
            payload={},
            actor=PrincipalRef(executor.agent_type, executor.agent_id),
            require_transition=True,
        )
        == "in_progress"
    )


@pytest.mark.parametrize("executor", WORKERS)
def test_multiple_executors_bind_after_creation_and_roundtrip_authorize_typed_identity(
    runtime, db, executor
):
    from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
        Participant,
    )
    from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
        ParticipationFacts,
    )
    from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.participation import (
        bind_initial_participants,
    )

    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    task = repo.get_current_status(collet.collet_id).task
    participants = tuple(
        Participant(f"worker-{i}", None, ref) for i, ref in enumerate(WORKERS)
    )
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=collet.collet_id)
            .with_for_update()
            .one()
        )
        bind_initial_participants(
            session, row, task, ParticipationFacts(participants), WORKERS, OWNER.actor
        )
    record = repo.get_current_status(collet.collet_id)
    assert record.task.executor_refs == WORKERS
    assert record.participants == participants
    assert record.task.version == record.collet.version == 2
    actor = PrincipalRef(executor.agent_type, executor.agent_id)
    message = EventAppendCommand(
        2,
        "user_interaction.posted",
        "Message",
        actor,
        None,
        "workspace",
        {"content": "update"},
    )
    result = service.append_event(collet.collet_id, message, ActorContext(actor, "a2a"))
    assert result.context_version == 3
    before = repo.get_current_status(collet.collet_id)
    for impostor in (
        PrincipalRef("human", executor.agent_id),
        PrincipalRef("bot", "stranger"),
    ):
        with pytest.raises(ContextCollabValidationError, match="only the principal"):
            service.append_event(
                collet.collet_id,
                replace(message, expected_version=3, actor_ref=impostor),
                ActorContext(impostor, "api"),
            )
        assert repo.get_current_status(collet.collet_id) == before
