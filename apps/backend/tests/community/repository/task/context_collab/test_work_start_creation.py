"""Taskless installation → Work.start: real SQL creation and rollback contracts."""

import json
from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
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
    EventAppendCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_instantiation import (
    ProductInstantiationService,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
    ParticipantScopeKind,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from sqlalchemy import event as sql_event

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = ActorContext(PrincipalRef("bot", "worker"), "a2a")
CANDIDATE = ActorContext(PrincipalRef("bot", "eligible-unbound"), "a2a")
OUTSIDER = ActorContext(PrincipalRef("bot", "outside-scope"), "a2a")
OBJECTIVE = "交付可验收的成果"


@pytest.fixture
def runtime(db):
    # Keep real scope/role/lifecycle enforcement; Open/Single admission is covered
    # through actual DI in the journey and composition regression suites.
    profile = create_product_profile(
        replace(
            TASK_COMMISSION_PROFILE,
            composition=replace(TASK_COMMISSION_PROFILE.composition, policies=()),
            participant_scope=ParticipantScope(
                ParticipantScopeKind.ALLOWLIST,
                (AgentRef("bot", "worker"), AgentRef("bot", "eligible-unbound")),
            ),
        )
    )
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    authorizer = StructuralContextCollabAuthorizer()
    service = ContextService(
        repo, ProductCollaborationPolicy(catalog, authorizer), authorizer
    )
    collet = service.create_collet(
        ColletCreateCommand(OWNER.actor, title="这是上下文标题，不是任务目标"), OWNER
    )
    ProductInstantiationService(
        repo, InMemoryMethodStore(), catalog, authorizer, service
    ).instantiate(
        ProductInstantiateCommand(
            collet.collet_id, profile.ref, collet.version, (AgentRef("bot", "worker"),)
        ),
        OWNER,
    )
    return service, repo, collet.collet_id


def _command(repo, cid, **kwargs):
    return WorkStartCommand(
        repo.get_current_status(cid).collet.version, objective=OBJECTIVE, **kwargs
    )


def _sql_snapshot(db, cid):
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=cid)
            .one()
        )
        return (
            row.root_task_ref,
            row.creation_facts,
            session.query(ContextCollabTaskModel)
            .filter_by(collab_context_id=cid)
            .count(),
            session.query(ContextCollabEventModel)
            .filter_by(collab_context_id=cid)
            .count(),
        )


def test_start_is_first_task_write_and_preserves_instantiation_facts(runtime, db):
    service, repo, cid = runtime
    before = repo.get_current_status(cid)
    root, facts, task_count, event_count = _sql_snapshot(db, cid)
    assert root is None and task_count == 0 and event_count == 1
    assert before.task is None and before.tasks == ()
    assert before.product_instance.root_task_ref is None
    assert before.participants and before.role_bindings
    result = service.start(
        cid, _command(repo, cid, completion_conditions=("用户验收",)), BOT
    )
    after = repo.get_current_status(cid)
    task = after.task
    assert after.tasks == (task,)
    assert task.task_id != cid and task.parent_task_ref is None
    assert task.objective == OBJECTIVE and task.principal_ref == OWNER.actor
    assert task.executor_refs == (AgentRef("bot", "worker"),)
    assert task.completion_conditions == ("用户验收",)
    assert task.status == result.current_status == "in_progress"
    assert task.version == 2
    assert result.context_version == after.collet.version == before.collet.version + 2
    assert after.events[:1] == before.events
    created, started = after.events[-2:]
    assert (created.event_kind, started.event_kind) == ("task.created", "task.started")
    assert created.event_id == task.created_event_ref
    assert started.event_id == task.last_event_ref == result.event_id
    assert started.parent_event == result.parent_event == created.event_id
    assert created.task_ref == started.task_ref == task.task_id
    assert (created.task_version_after, started.task_version_after) == (1, 2)
    assert (created.context_version_after, started.context_version_after) == (
        before.collet.version + 1,
        before.collet.version + 2,
    )
    assert after.product_instance == replace(
        before.product_instance, root_task_ref=task.task_id
    )
    assert (
        after.participants == before.participants
        and after.role_bindings == before.role_bindings
    )
    assert _sql_snapshot(db, cid) == (task.task_id, facts, 1, 3)
    # The immutable installation record remains taskless; read projection resolves
    # the current authoritative root without letting L4 rewrite L6 metadata.
    assert json.loads(facts)["product_instance"]["root_task_ref"] is None


@pytest.mark.parametrize(
    "actor, message",
    [
        (OUTSIDER, "participant scope"),
        (CANDIDATE, "only the principal"),
        (OWNER, "designated executor"),  # G1 passes owner; G4 refuses executor role.
    ],
)
def test_rejected_start_does_not_create_task_or_events(runtime, db, actor, message):
    service, repo, cid = runtime
    before, sql_before = repo.get_current_status(cid), _sql_snapshot(db, cid)
    with pytest.raises(ContextCollabValidationError, match=message):
        service.start(cid, _command(repo, cid), actor)
    assert repo.get_current_status(cid) == before
    assert _sql_snapshot(db, cid) == sql_before


def test_start_requires_real_objective_not_context_title(runtime, db):
    service, repo, cid = runtime
    before = _sql_snapshot(db, cid)
    with pytest.raises(ContextCollabValidationError, match="requires objective"):
        service.start(
            cid, WorkStartCommand(repo.get_current_status(cid).collet.version), BOT
        )
    assert _sql_snapshot(db, cid) == before


@pytest.mark.parametrize("objective", ["", " ", 1, {}])
def test_start_rejects_invalid_objective(objective):
    with pytest.raises(ContextCollabValidationError, match="objective"):
        WorkStartCommand(1, objective=objective)


def test_stale_first_start_does_not_create_task(runtime, db):
    service, repo, cid = runtime
    before = _sql_snapshot(db, cid)
    with pytest.raises(ContextCollabVersionConflict):
        service.start(cid, WorkStartCommand(1, objective=OBJECTIVE), BOT)
    assert _sql_snapshot(db, cid) == before


def test_repeated_start_cannot_create_second_root_or_redefine_work(runtime, db):
    service, repo, cid = runtime
    first_command = _command(repo, cid)
    service.start(cid, first_command, BOT)
    before = repo.get_current_status(cid)
    sql_before = _sql_snapshot(db, cid)
    for command, error in (
        (first_command, ContextCollabVersionConflict),
        (_command(repo, cid), ContextCollabValidationError),
        (
            replace(_command(repo, cid), objective="different"),
            ContextCollabValidationError,
        ),
    ):
        with pytest.raises(error):
            service.start(cid, command, BOT)
        assert repo.get_current_status(cid) == before
        assert _sql_snapshot(db, cid) == sql_before


def test_generic_append_cannot_implicitly_create_work(runtime, db):
    service, repo, cid = runtime
    before = _sql_snapshot(db, cid)
    with pytest.raises(ContextCollabValidationError):
        service.append_event(
            cid,
            EventAppendCommand(
                repo.get_current_status(cid).collet.version,
                "task.started",
                "Task",
                BOT.actor,
                None,
                "public",
                {},
            ),
            BOT,
        )
    assert _sql_snapshot(db, cid) == before


@pytest.mark.parametrize(
    "failure", ["task", "created_event", "started_event", "collet", "cas"]
)
def test_creation_and_start_share_one_rollback_boundary(runtime, db, engine, failure):
    service, repo, cid = runtime
    before, sql_before = repo.get_current_status(cid), _sql_snapshot(db, cid)
    fired = []

    def fail_write(conn, cursor, statement, parameters, context, executemany):
        sql = statement.lstrip().upper()
        table = "AC_CONTEXT_COLLAB_"
        if sql.startswith("UPDATE") and table + "COLLET" in sql:
            if failure == "cas" and not fired:
                fired.append(failure)
                # Make the actual SQL CAS affect zero rows, then require rollback
                # of this change as well as every queued Task/Event insertion.
                conn.exec_driver_sql(
                    "UPDATE ac_context_collab_collet SET context_version = context_version + 1 "
                    "WHERE collab_context_id = ?",
                    (cid,),
                )
            elif failure == "collet":
                fired.append(failure)
                raise RuntimeError("start write failed")
        if not sql.startswith("INSERT"):
            return
        is_failure = (
            failure == "task"
            and table + "TASK" in sql
            or failure == "created_event"
            and table + "EVENT" in sql
            and "task.created" in repr(parameters)
            or failure == "started_event"
            and table + "EVENT" in sql
            and "task.started" in repr(parameters)
        )
        if is_failure:
            fired.append(failure)
            raise RuntimeError("start write failed")

    sql_event.listen(engine, "before_cursor_execute", fail_write)
    try:
        with pytest.raises(
            ContextCollabVersionConflict if failure == "cas" else RuntimeError
        ):
            service.start(cid, _command(repo, cid), BOT)
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_write)
    assert fired == [failure]
    assert repo.get_current_status(cid) == before
    assert _sql_snapshot(db, cid) == sql_before


def test_initial_bindings_are_visible_before_work_but_not_to_other_agents(runtime):
    service, repo, cid = runtime
    [evidence] = repo.get_current_status(cid).events
    assert service.get_events(cid, context=OWNER) == [evidence]
    assert service.get_events(cid, context=BOT) == [evidence]
    assert service.get_events(cid, context=CANDIDATE) == []
    assert service.get_events(cid, context=OUTSIDER) == []


@pytest.mark.parametrize(
    "fault, message",
    [
        ("metadata_executor", "does not match initial Collet binding evidence"),
        ("event_executor", "initial executor is not a Participant"),
    ],
)
def test_start_rejects_conflicting_initial_executor_evidence(
    runtime, db, fault, message
):
    service, _, cid = runtime
    before_version = 2
    with db.orm_session() as session:
        if fault == "metadata_executor":
            row = (
                session.query(ContextCollabColletModel)
                .filter_by(collab_context_id=cid)
                .one()
            )
            facts = json.loads(row.creation_facts)
            facts["executor_ref"] = AgentRef("bot", "eligible-unbound").to_dict()
            row.creation_facts = json.dumps(facts)
        else:
            row = (
                session.query(ContextCollabEventModel)
                .filter_by(collab_context_id=cid)
                .one()
            )
            payload = json.loads(row.payload)
            payload["executor_refs"] = [AgentRef("bot", "eligible-unbound").to_dict()]
            row.payload = json.dumps(payload)
        session.commit()
    before = _sql_snapshot(db, cid)
    with pytest.raises(ContextCollabValidationError, match=message):
        service.start(cid, WorkStartCommand(before_version, objective=OBJECTIVE), BOT)
    assert _sql_snapshot(db, cid) == before
