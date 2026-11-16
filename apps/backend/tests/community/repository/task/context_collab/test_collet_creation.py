"""Pure L4 creation: real SQL, executor cardinality and no upper-layer effects."""

from dataclasses import fields

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
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService,
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


@pytest.mark.parametrize("refs", [None, (), WORKERS[:1], WORKERS, list(WORKERS)])
def test_create_retains_zero_one_or_many_executors_without_upper_access(
    runtime, db, refs
):
    service, repo = runtime
    # None in this parameterization means the argument is omitted, not null input.
    command = _command(**({} if refs is None else {"executor_refs": refs}))
    created = service.create_collet(command, OWNER)
    persisted = repo.get_current_status(created.collet_id)
    expected_refs = tuple(refs or ())
    assert ExportedColletCreateCommand is ColletCreateCommand
    assert {field.name for field in fields(command)} == {
        "to_space",
        "initiator_ref",
        "objective",
        "executor_refs",
    }
    assert command.initiator_ref == OWNER.actor
    assert command.executor_refs == expected_refs
    assert persisted.collet == created
    assert persisted.collet.version == persisted.task.version == 1
    assert persisted.task.task_id != created.collet_id
    assert persisted.task.principal_ref == OWNER.actor
    assert persisted.task.executor_refs == expected_refs
    assert persisted.task.executor_ref == (
        expected_refs[0] if len(expected_refs) == 1 else None
    )
    assert persisted.task.objective == command.objective
    assert persisted.product_instance is None
    assert len(persisted.events) == 1
    event = persisted.events[0]
    assert event.event_kind == "task.created" and event.primitive_type == "Task"
    assert event.actor_ref == OWNER.actor
    assert (
        event.event_id == created.current_event_id == persisted.task.created_event_ref
    )
    assert event.task_ref == persisted.task.task_id and event.task_version_after == 1
    lower_facts = {
        "objective": command.objective,
        "executor_refs": [ref.to_dict() for ref in expected_refs],
    }
    assert event.payload == persisted.creation_facts == lower_facts
    with db.orm_session() as session:
        for model in (
            ContextCollabColletModel,
            ContextCollabTaskModel,
            ContextCollabEventModel,
        ):
            assert session.query(model).count() == 1
    view = service.get_status(
        created.collet_id, trunk_events=False, context=OWNER
    ).current_snapshot
    assert view["method_kind"] is None
    if len(expected_refs) > 1:
        assert view["executor_refs"] == [ref.to_dict() for ref in expected_refs]
    else:
        assert view["executor_ref"] == (
            expected_refs[0].to_dict() if expected_refs else None
        )


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
    with pytest.raises(ContextCollabValidationError):
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


def test_list_input_is_copied_to_immutable_executor_set():
    refs = list(WORKERS)
    command = _command(executor_refs=refs)
    refs.clear()
    assert command.executor_refs == WORKERS


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
            service.create_collet(_command(executor_refs=WORKERS), OWNER)
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


def test_multiple_executors_keep_typed_append_and_visibility_permissions(runtime):
    service, repo = runtime
    collet = service.create_collet(_command(executor_refs=WORKERS), OWNER)
    for executor in WORKERS:
        actor = ActorContext(
            PrincipalRef(executor.agent_type, executor.agent_id), "a2a"
        )
        before = repo.get_current_status(collet.collet_id)
        receipt = service.append_event(
            collet.collet_id,
            EventAppendCommand(
                before.collet.version,
                "user_interaction.posted",
                "Message",
                actor.actor,
                None,
                "workspace",
                {"content": executor.agent_id},
            ),
            actor,
        )
        assert receipt.accepted
        assert len(service.get_events(collet.collet_id, context=actor)) >= 2
    before = repo.get_current_status(collet.collet_id)
    for principal in (
        PrincipalRef("human", "worker-1"),
        PrincipalRef("bot", "outsider"),
    ):
        actor = ActorContext(principal, "api")
        with pytest.raises(ContextCollabValidationError):
            service.append_event(
                collet.collet_id,
                EventAppendCommand(
                    before.collet.version,
                    "user_interaction.posted",
                    "Message",
                    principal,
                    None,
                    "workspace",
                    {"content": "not an executor"},
                ),
                actor,
            )
        assert len(service.get_events(collet.collet_id, context=actor)) == 1
    assert repo.get_current_status(collet.collet_id) == before
    assert before.task.executor_refs == WORKERS and before.task.version == 1


def test_task_plural_storage_roundtrip_and_explicit_single_legacy_read(runtime):
    service, repo = runtime
    collet = service.create_collet(_command(executor_refs=WORKERS), OWNER)
    task = repo.get_current_status(collet.collet_id).task
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
    collet = service.create_collet(_command(executor_refs=WORKERS), OWNER)
    task = repo.get_current_status(collet.collet_id).task
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
