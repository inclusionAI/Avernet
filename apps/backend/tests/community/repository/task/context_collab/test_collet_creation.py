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
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    TaskCreateCommand,
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
        initiator_ref=OWNER.actor,
        **kwargs,
    )


@pytest.fixture
def runtime(db):
    forbidden = NoUpperAccess()
    repo = ContextCollabRepository(db, product_catalog=forbidden)
    service = ContextCollabService(repo, forbidden, product_catalog=forbidden)
    return service, repo


def test_create_only_context_without_work_or_upper_access(runtime, db):
    service, repo = runtime
    command = _command()
    collet = service.create_collet(command, OWNER)
    record = repo.get_current_status(collet.collet_id)
    assert ExportedColletCreateCommand is ColletCreateCommand
    assert {field.name for field in fields(command)} == {
        "initiator_ref",
        "title",
        "description",
        "objective",
        "acceptance_criteria",
        "resources",
    }
    assert command.resources == collet.resources == ()
    assert command.objective == command.acceptance_criteria == ""
    assert collet.metadata.objective == collet.metadata.acceptance_criteria == ""
    assert record.collet == collet and collet.version == 1 and collet.scope is None
    assert record.task is None and record.tasks == () and record.events == []
    assert record.creation_facts == {} and record.product_instance is None
    assert record.participants == record.role_bindings == ()
    assert not hasattr(collet, "current_event_id")
    assert "current_event_id" not in collet.to_dict()
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).count() == 1
        assert session.query(ContextCollabTaskModel).count() == 0
        assert session.query(ContextCollabEventModel).count() == 0
        row = session.query(ContextCollabColletModel).one()
        assert row.root_task_ref is None and row.current_snapshot is None


def _create_work(service, repo):
    collet = service.create_collet(_command(), OWNER)
    task = service.create_task(
        collet.collet_id,
        TaskCreateCommand(
            collet.version, OWNER.actor, "Deliver independently identified work"
        ),
        OWNER,
    )
    return repo.get_current_status(collet.collet_id).collet, task


def test_task_is_created_only_by_explicit_l4_operation(runtime, db):
    service, repo = runtime
    collet, task = _create_work(service, repo)
    record = repo.get_current_status(collet.collet_id)
    assert record.task == task and task.task_id != collet.collet_id
    assert collet.version == 2 and task.version == 1 and task.executor_refs == ()
    assert task.principal_ref == OWNER.actor and task.parent_task_ref is None
    assert record.product_instance is None and not record.participants
    [event] = record.events
    assert event.event_kind == "task.created" and event.primitive_type == "Task"
    assert event.context_version_after == 2 and event.actor_ref == OWNER.actor
    assert event.event_id == task.created_event_ref == task.last_event_ref
    assert event.task_ref == task.task_id and event.task_version_after == 1
    assert record.collet.updated_at == event.occurred_at
    assert (
        event.payload
        == record.creation_facts
        == {"objective": task.objective, "executor_refs": []}
    )
    before = record
    with pytest.raises(ContextCollabValidationError, match="already exists"):
        service.create_task(
            collet.collet_id, TaskCreateCommand(2, OWNER.actor, "again"), OWNER
        )
    assert repo.get_current_status(collet.collet_id) == before


def test_command_rejects_legacy_principal_keyword():
    with pytest.raises(TypeError, match="principal_ref"):
        _command(principal_ref=OWNER.actor)


@pytest.mark.parametrize(
    "initiator", [None, "owner", WORKERS[0], PrincipalRef("human", "")]
)
def test_command_requires_valid_initiator_reference(initiator):
    with pytest.raises(ContextCollabValidationError):
        ColletCreateCommand(
            initiator_ref=initiator,
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


@pytest.mark.parametrize("name", ["objective", "acceptance_criteria"])
@pytest.mark.parametrize("value", [None, 1, False, [], {}, ("criterion",)])
def test_collet_command_rejects_non_string_intent(name, value):
    with pytest.raises(ContextCollabValidationError, match=name):
        _command(**{name: value})


@pytest.mark.parametrize("name", ["objective", "acceptance_criteria"])
@pytest.mark.parametrize("value", ["", "  ", "实现计算器求和并通过测试"])
def test_collet_command_accepts_string_intent(name, value):
    assert getattr(_command(**{name: value}), name) == value


@pytest.mark.parametrize("objective", [None, "", "  ", 1])
def test_objective_required(objective):
    with pytest.raises(ContextCollabValidationError, match="objective"):
        TaskCreateCommand(1, OWNER.actor, objective)


@pytest.mark.parametrize(
    "extra",
    [
        "to_space",
        "completion_conditions",
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


@pytest.mark.parametrize("failure_target", [ContextCollabColletModel])
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
    collet, created_task = _create_work(service, repo)
    task = replace(created_task, executor_refs=WORKERS)
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
    collet, task = _create_work(service, repo)
    data = task.to_dict()
    data["executor_refs"] = refs
    with pytest.raises(ContextCollabValidationError):
        Task.from_dict(data)
    with pytest.raises(ContextCollabValidationError):
        decode_creation_facts({"objective": "work", "executor_refs": refs})


def test_persisted_task_rejects_ambiguous_single_and_plural(runtime):
    service, repo = runtime
    collet, task = _create_work(service, repo)
    data = task.to_dict()
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
    collet, created_task = _create_work(service, repo)
    task = replace(created_task, executor_refs=WORKERS)
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
    collet, task = _create_work(service, repo)
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
    assert record.task.version == 2 and record.collet.version == 3
    actor = PrincipalRef(executor.agent_type, executor.agent_id)
    message = EventAppendCommand(
        3,
        "user_interaction.posted",
        "Message",
        actor,
        None,
        "workspace",
        {"content": "update"},
    )
    result = service.append_event(collet.collet_id, message, ActorContext(actor, "a2a"))
    assert result.context_version == 4
    before = repo.get_current_status(collet.collet_id)
    for impostor in (
        PrincipalRef("human", executor.agent_id),
        PrincipalRef("bot", "stranger"),
    ):
        with pytest.raises(ContextCollabValidationError, match="only the principal"):
            service.append_event(
                collet.collet_id,
                replace(message, expected_version=4, actor_ref=impostor),
                ActorContext(impostor, "api"),
            )
        assert repo.get_current_status(collet.collet_id) == before


@pytest.mark.parametrize("target", [ContextCollabTaskModel, ContextCollabEventModel])
def test_task_creation_failure_keeps_existing_collet_unchanged(runtime, db, target):
    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    before = repo.get_current_status(collet.collet_id)

    def fail(*_):
        raise RuntimeError("Task insert failed")

    sql_event.listen(target, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Task insert failed"):
            service.create_task(
                collet.collet_id, TaskCreateCommand(1, OWNER.actor, "work"), OWNER
            )
    finally:
        sql_event.remove(target, "before_insert", fail)
    assert repo.get_current_status(collet.collet_id) == before
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).count() == 1
        assert session.query(ContextCollabTaskModel).count() == 0
        assert session.query(ContextCollabEventModel).count() == 0


def test_task_creation_checks_actor_and_stale_collet_without_side_effects(runtime):
    from agentclaw.community.core.task.context_collab.errors import (
        ContextCollabVersionConflict,
    )

    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    before = repo.get_current_status(collet.collet_id)
    command = TaskCreateCommand(1, OWNER.actor, "work")
    stranger = ActorContext(PrincipalRef("human", "stranger"), "api")
    with pytest.raises(ContextCollabValidationError, match="initiator"):
        service.create_task(collet.collet_id, command, stranger)
    with pytest.raises(ContextCollabVersionConflict):
        service.create_task(
            collet.collet_id, replace(command, expected_version=2), OWNER
        )
    assert repo.get_current_status(collet.collet_id) == before


@pytest.mark.parametrize("expected_version", [None, True, 0, -1, "1"])
def test_task_command_requires_positive_integer_revision(expected_version):
    with pytest.raises(ContextCollabValidationError, match="expected_version"):
        TaskCreateCommand(expected_version, OWNER.actor, "work")


@pytest.mark.parametrize("conditions", [None, [], "done", ("",), ("  ",), (1,)])
def test_task_command_validates_completion_conditions(conditions):
    with pytest.raises(ContextCollabValidationError, match="completion_conditions"):
        TaskCreateCommand(1, OWNER.actor, "work", conditions)


def test_task_creation_preserves_responsible_principal_and_completion_conditions(
    runtime,
):
    service, repo = runtime
    collet = service.create_collet(
        _command(objective="协作目标", acceptance_criteria="协作验收标准"), OWNER
    )
    before = repo.get_current_status(collet.collet_id)
    assert before.task is None and before.tasks == () and before.events == []
    assert before.collet.metadata.objective == "协作目标"
    assert before.collet.metadata.acceptance_criteria == "协作验收标准"
    responsible = PrincipalRef("human", "responsible")
    command = TaskCreateCommand(1, responsible, "Deliver work", ("Owner accepts",))
    task = service.create_task(collet.collet_id, command, OWNER)
    record = repo.get_current_status(collet.collet_id)
    assert record.task == task
    assert task.principal_ref == responsible != collet.initiator_ref
    assert task.objective == "Deliver work"
    assert task.completion_conditions == ("Owner accepts",)
    assert record.collet.metadata.objective == "协作目标"
    assert record.collet.metadata.acceptance_criteria == "协作验收标准"
    assert task.executor_refs == ()
    assert record.events[0].actor_ref == OWNER.actor


def test_task_creation_authorizer_receives_actual_command_before_any_write():
    from unittest.mock import Mock

    from agentclaw.community.core.task.context_collab.g1_secure_govern.collet_creation import (
        ColletCreationAuthorizer,
    )

    writer = NoUpperAccess()
    authorizer = Mock(spec=ColletCreationAuthorizer)
    authorizer.check_create_task.side_effect = ContextCollabValidationError("denied")
    command = TaskCreateCommand(1, OWNER.actor, "work")
    service = ContextService(writer, NoUpperAccess(), authorizer)
    with pytest.raises(ContextCollabValidationError, match="denied"):
        service.create_task("collet-test", command, OWNER)
    authorizer.check_create_task.assert_called_once_with(
        collet_id="collet-test", command=command, actor=OWNER
    )
    authorizer.check_create_collet.assert_not_called()


def test_task_creation_collet_cas_failure_rolls_back_task_and_event(
    runtime, db, engine
):
    from agentclaw.community.core.task.context_collab.errors import (
        ContextCollabVersionConflict,
    )

    service, repo = runtime
    collet = service.create_collet(_command(), OWNER)
    before = repo.get_current_status(collet.collet_id)

    def force_zero_matches(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE ac_context_collab_collet"):
            return statement + " AND 1=0", parameters
        return statement, parameters

    sql_event.listen(engine, "before_cursor_execute", force_zero_matches, retval=True)
    try:
        with pytest.raises(
            ContextCollabVersionConflict, match="Task creation Collet CAS"
        ):
            service.create_task(
                collet.collet_id, TaskCreateCommand(1, OWNER.actor, "work"), OWNER
            )
    finally:
        sql_event.remove(engine, "before_cursor_execute", force_zero_matches)
    assert repo.get_current_status(collet.collet_id) == before
    with db.orm_session() as session:
        assert session.query(ContextCollabTaskModel).count() == 0
        assert session.query(ContextCollabEventModel).count() == 0
