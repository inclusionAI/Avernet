"""Profile eligibility, initial L4 facts and actual-actor G1 fences (real SQL)."""

import json
from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
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
    EventAppendCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    actor_has_role,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductProfileCreateCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_instantiation import (
    ProductInstantiationService,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
    ParticipantScopeKind,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
)
from sqlalchemy import event as sql_event
from tests.community.core.task.context_collab.product_catalog import (
    standard_task_workflow,
)
from tests.community.core.task.context_collab.test_l6_runtime_contract import (
    OWNER,
    SPACE,
    _actor,
    _create_collet,
    _instantiate,
    _services,
)
from tests.community.repository.task.context_collab.test_product_instantiation import (
    runtime as runtime,
)

BOT = AgentRef("bot", "worker")
OUTSIDE = AgentRef("bot", "outsider")
SCOPE = ParticipantScope(ParticipantScopeKind.ALLOWLIST, (BOT,))


def test_scope_normalizes_immutable_typed_refs_and_round_trips():
    refs = [BOT, AgentRef("service", "worker")]
    scope = ParticipantScope(ParticipantScopeKind.ALLOWLIST, refs)
    refs.clear()
    assert scope.agent_refs == (BOT, AgentRef("service", "worker"))
    assert ParticipantScope.from_dict(scope.to_dict()) == scope
    assert scope.allows(BOT) and scope.allows(AgentRef("service", "worker"))
    assert not scope.allows(AgentRef("human", "worker"))
    assert not scope.allows("worker")
    assert not ParticipantScope(ParticipantScopeKind.ALLOWLIST).allows(BOT)
    assert ParticipantScope().allows(OUTSIDE)


@pytest.mark.parametrize(
    "kind,refs",
    [
        ("invalid", ()),
        ("OPEN", (BOT,)),
        ("ALLOWLIST", (BOT, BOT)),
        ("ALLOWLIST", ["worker"]),
        ("ALLOWLIST", (AgentRef("bot", ""),)),
        ("ALLOWLIST", None),
    ],
)
def test_invalid_scope_fails_closed(kind, refs):
    with pytest.raises(ContextCollabValidationError):
        ParticipantScope(kind, refs)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"kind": "OPEN", "agent_refs": [], "extra": True},
        {"kind": "OPEN", "agent_refs": None},
        {"kind": "ALLOWLIST", "agent_refs": [{"agent_id": "worker"}]},
        {"kind": "ALLOWLIST", "agent_refs": [{**BOT.to_dict(), "extra": True}]},
    ],
)
def test_scope_codec_rejects_unknown_or_missing_fields(data):
    with pytest.raises(ContextCollabValidationError):
        ParticipantScope.from_dict(data)


def test_authoring_and_catalog_preserve_scope_without_runtime_bindings():
    p = TASK_COMMISSION_PROFILE
    command = ProductProfileCreateCommand(
        p.ref,
        p.composition,
        p.scenario_roles,
        p.governance,
        p.compatibility,
        participant_scope=SCOPE,
    )
    profile = create_product_profile(command)
    assert profile.participant_scope == SCOPE
    assert ProductCatalog((profile,)).get(p.ref.profile_id, p.ref.version) == profile
    with pytest.raises(ContextCollabValidationError, match="participant_scope"):
        create_product_profile(replace(command, participant_scope="OPEN"))


@pytest.fixture
def scoped_runtime(db):
    profile = create_product_profile(
        replace(TASK_COMMISSION_PROFILE, participant_scope=SCOPE)
    )
    catalog = ProductCatalog((profile,))
    repo, store, generic, product = _services(db, catalog=catalog)
    collet = _create_collet(repo)
    return profile, catalog, repo, store, generic, product, collet


@pytest.mark.parametrize(
    "refs", [(OUTSIDE,), (BOT, OUTSIDE), (AgentRef("service", "worker"),)]
)
def test_all_initial_agents_must_be_eligible_before_any_write(scoped_runtime, refs):
    profile, catalog, repo, store, _, _, collet = scoped_runtime
    before = repo.get_current_status(collet.collet_id)
    # Explicitly designate the first agent: extra non-executors must also be checked.
    service = ProductInstantiationService(
        repo,
        store,
        catalog,
        StructuralContextCollabAuthorizer(),
        ContextService(
            repo,
            ProductCollaborationPolicy(catalog, StructuralContextCollabAuthorizer()),
        ),
    )
    with pytest.raises(ContextCollabValidationError, match="participant scope"):
        service.instantiate(
            ProductInstantiateCommand(
                collet.collet_id, profile.ref, collet.version, refs, refs[0]
            ),
            _actor(),
        )
    assert repo.get_current_status(collet.collet_id) == before
    assert store.list_bindings(current_axes(), collet.collet_id) == []


def test_initial_executor_must_be_among_supplied_participants():
    with pytest.raises(ContextCollabValidationError, match="initial participant"):
        ProductInstantiateCommand(
            "collet", TASK_COMMISSION_PROFILE.ref, 1, (BOT,), OUTSIDE
        )


def test_initial_participation_is_real_scoped_and_does_not_imply_open_admission(
    scoped_runtime,
):
    profile, catalog, repo, store, _, _, collet = scoped_runtime
    before = repo.get_current_status(collet.collet_id)
    assert not before.participants and not before.role_bindings
    instance = _instantiate(repo, store, collet, catalog=catalog)
    record = repo.get_current_status(collet.collet_id)
    assert instance.participant_scope == SCOPE
    assert (
        tuple(role_binding_ref(collet.collet_id, b) for b in record.role_bindings)
        == instance.role_binding_refs
    )
    assert {p.agent_ref for p in record.participants if p.agent_ref} == {BOT}
    assert record.events[:1] == before.events
    assert record.events[-1].actor_ref == OWNER
    assert record.events[-1].task_ref == record.task.task_id
    assert record.task.last_event_ref == record.events[-1].event_id
    assert record.task.created_event_ref == before.task.created_event_ref
    for binding in store.list_bindings(current_axes(), collet.collet_id):
        assert (
            store.list_opportunities(
                current_axes(), binding.method_binding_id, collet.collet_id
            )
            == []
        )
        assert (
            store.list_applications(
                current_axes(), binding.method_binding_id, collet.collet_id
            )
            == []
        )


@pytest.mark.parametrize("failure_stage", ["task", "collet", "event"])
def test_binding_write_failure_rolls_back_instance_assignment_roles_and_event(
    runtime, engine, failure_stage
):
    service, repo, store, _, command = runtime
    before = repo.get_current_status(command.collet_id)

    def fail(conn, cursor, statement, parameters, context, executemany):
        sql = statement.lstrip().upper()
        # The first Collet UPDATE installs selection; fail its later binding CAS.
        if (
            (
                failure_stage == "task"
                and sql.startswith("UPDATE AC_CONTEXT_COLLAB_TASK ")
            )
            or (
                failure_stage == "collet"
                and sql.startswith("UPDATE AC_CONTEXT_COLLAB_COLLET ")
                and "CONTEXT_VERSION=" in sql
            )
            or (
                failure_stage == "event"
                and sql.startswith("INSERT INTO AC_CONTEXT_COLLAB_EVENT ")
            )
        ):
            raise RuntimeError("initial participation write failed")

    sql_event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="participation write failed"):
            service.instantiate(command, ActorContext(OWNER, "api"))
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail)
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == []


def _replace_task_executors(db, agent):
    with db.orm_session() as session:
        row = session.query(ContextCollabTaskModel).one()
        task = json.loads(row.task_data)
        task["executor_refs"] = [agent.to_dict()]
        row.task_data = json.dumps(task)


@pytest.mark.parametrize("entrypoint", ["generic", "work_start", "product"])
def test_g1_rechecks_authenticated_agent_even_with_forged_assignment_and_claimed_actor(
    scoped_runtime, db, entrypoint
):
    _, catalog, repo, store, generic, product, collet = scoped_runtime
    _instantiate(repo, store, collet, catalog=catalog)
    _replace_task_executors(db, OUTSIDE)
    before = repo.get_current_status(collet.collet_id)
    context = _actor(PrincipalRef("bot", "outsider"))
    with pytest.raises(ContextCollabValidationError, match="participant scope"):
        if entrypoint == "generic":
            generic.append_event(
                collet.collet_id,
                EventAppendCommand(
                    before.collet.version,
                    "task.started",
                    "Task",
                    PrincipalRef("bot", BOT.agent_id),
                    None,
                    "public",
                    {},
                ),
                context,
            )
        elif entrypoint == "work_start":
            generic.start(
                collet.collet_id, WorkStartCommand(before.collet.version), context
            )
        else:
            product.start_task(TaskAdvanceCommand(collet.collet_id), context)
    assert repo.get_current_status(collet.collet_id) == before


@pytest.mark.parametrize("entrypoint", ["generic", "work_start", "product"])
def test_eligible_bound_executor_can_work(scoped_runtime, entrypoint):
    _, catalog, repo, store, generic, product, collet = scoped_runtime
    _instantiate(repo, store, collet, catalog=catalog)
    if entrypoint == "generic":
        result = generic.append_event(
            collet.collet_id,
            EventAppendCommand(
                3,
                "task.started",
                "Task",
                PrincipalRef("bot", "worker"),
                None,
                "public",
                {},
            ),
            _actor(PrincipalRef("bot", "worker")),
        )
    elif entrypoint == "work_start":
        result = generic.start(
            collet.collet_id, WorkStartCommand(3), _actor(PrincipalRef("bot", "worker"))
        )
    else:
        result = product.start_task(
            TaskAdvanceCommand(collet.collet_id), _actor(PrincipalRef("bot", "worker"))
        )
    assert result.context_version == 4


def test_open_eligibility_is_not_membership_or_executor_authority(runtime):
    service, repo, _, _, command = runtime
    instance = service.instantiate(command, _actor())
    assert instance.participant_scope.kind is ParticipantScopeKind.OPEN
    record = repo.get_current_status(command.collet_id)
    with pytest.raises(ContextCollabValidationError, match="only the principal"):
        StructuralContextCollabAuthorizer().check_append(
            record.collet,
            record.task,
            record.creation_facts,
            instance,
            EventAppendCommand(
                3,
                "task.started",
                "Task",
                PrincipalRef("bot", "outsider"),
                None,
                "public",
                {},
            ),
            _actor(PrincipalRef("bot", "outsider")),
        )


def test_g4_resolves_actual_roles_not_spoofed_task_identity(scoped_runtime, db):
    profile, catalog, repo, store, _, _, collet = scoped_runtime
    _instantiate(repo, store, collet, catalog=catalog)
    _replace_task_executors(db, OUTSIDE)
    record = repo.get_current_status(collet.collet_id)
    executor_role = next(
        r.l4_role
        for r in profile.scenario_roles
        if r.participant_source == "executor_ref"
    )
    assert not actor_has_role(
        record.task,
        PrincipalRef("bot", "outsider"),
        executor_role,
        profile,
        record.events,
    )
    assert actor_has_role(
        record.task,
        PrincipalRef("bot", "worker"),
        executor_role,
        profile,
        record.events,
    )


@pytest.mark.parametrize("tamper", ["open", "missing", "different_agent"])
def test_pinned_scope_cannot_be_widened_in_persisted_instance(
    scoped_runtime, db, tamper
):
    _, catalog, repo, store, _, _, collet = scoped_runtime
    _instantiate(repo, store, collet, catalog=catalog)
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.creation_facts)
        if tamper == "missing":
            del data["product_instance"]["participant_scope"]
        else:
            scope = (
                ParticipantScope()
                if tamper == "open"
                else ParticipantScope(ParticipantScopeKind.ALLOWLIST, (OUTSIDE,))
            )
            data["product_instance"]["participant_scope"] = scope.to_dict()
        row.creation_facts = json.dumps(data)
    with pytest.raises(ContextCollabValidationError):
        repo.get_current_status(collet.collet_id)


def test_instantiate_empty_initial_agents_when_executor_role_is_optional(db):
    profile = replace(
        TASK_COMMISSION_PROFILE,
        composition=replace(
            TASK_COMMISSION_PROFILE.composition,
            required_methods=(
                replace(
                    TASK_COMMISSION_PROFILE.composition.required_methods[0], policy=None
                ),
            ),
        ),
        workflow=standard_task_workflow(),
        task_contract_ref=None,
        participant_scope=ParticipantScope(ParticipantScopeKind.ALLOWLIST),
        scenario_roles=tuple(
            replace(r, required=False) if r.participant_source == "executor_ref" else r
            for r in TASK_COMMISSION_PROFILE.scenario_roles
        ),
    )
    catalog = ProductCatalog((profile,))
    repo, store, _, _ = _services(db, catalog=catalog)
    collet = _create_collet(repo)
    instance = ProductInstantiationService(
        repo,
        store,
        catalog,
        StructuralContextCollabAuthorizer(),
        ContextService(
            repo,
            ProductCollaborationPolicy(catalog, StructuralContextCollabAuthorizer()),
        ),
    ).instantiate(
        ProductInstantiateCommand(collet.collet_id, profile.ref, collet.version),
        _actor(),
    )
    record = repo.get_current_status(collet.collet_id)
    assert record.task.executor_refs == ()
    assert (
        len(record.participants)
        == len(record.role_bindings)
        == len(instance.role_binding_refs)
        == 1
    )


@pytest.mark.parametrize("tamper", ["missing_evidence", "missing_roles"])
def test_new_instance_requires_actual_participation_and_role_evidence(
    scoped_runtime, db, tamper
):
    _, catalog, repo, store, _, _, collet = scoped_runtime
    _instantiate(repo, store, collet, catalog=catalog)
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabEventModel)
            .filter_by(event_kind="task.participants_bound")
            .one()
        )
        payload = json.loads(row.payload)
        if tamper == "missing_evidence":
            del payload["participation"]
        else:
            payload["participation"]["role_bindings"] = []
        row.payload = json.dumps(payload)
    with pytest.raises(ContextCollabValidationError):
        repo.get_current_status(collet.collet_id)


def test_historical_open_scope_and_role_fallback_load_without_rewriting(runtime, db):
    service, repo, _, catalog, command = runtime
    instance = service.instantiate(command, _actor())
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.creation_facts)
        del data["product_instance"]["participant_scope"]
        historical_json = json.dumps(data)
        row.creation_facts = historical_json
        for event_row in session.query(ContextCollabEventModel).all():
            payload = json.loads(event_row.payload)
            payload.pop("participation", None)
            event_row.payload = json.dumps(payload)
    record = repo.get_current_status(command.collet_id)
    assert record.product_instance == instance
    assert not record.participants and not record.role_bindings
    profile = catalog.get(command.profile_ref.profile_id, command.profile_ref.version)
    role = next(
        r.l4_role
        for r in profile.scenario_roles
        if r.participant_source == "executor_ref"
    )
    assert actor_has_role(
        record.task, PrincipalRef("bot", "worker"), role, profile, record.events
    )
    with db.orm_session() as session:
        assert (
            session.query(ContextCollabColletModel).one().creation_facts
            == historical_json
        )


def test_newer_open_profile_cannot_widen_an_existing_restricted_instance(
    scoped_runtime, db
):
    profile, catalog, repo, store, _, _, collet = scoped_runtime
    instance = _instantiate(repo, store, collet, catalog=catalog)
    new_profile = create_product_profile(
        replace(
            profile,
            ref=VersionedProfileRef(profile.ref.profile_id, profile.ref.version + 1),
            participant_scope=ParticipantScope(),
        )
    )
    installed = ProductCatalog((profile, new_profile))
    restarted_repo, _, generic, _ = _services(db, catalog=installed)
    assert (
        restarted_repo.get_current_status(collet.collet_id).product_instance == instance
    )
    _replace_task_executors(db, OUTSIDE)
    with pytest.raises(ContextCollabValidationError, match="participant scope"):
        generic.start(
            collet.collet_id,
            WorkStartCommand(2),
            _actor(PrincipalRef("bot", "outsider")),
        )


def test_legacy_product_publisher_also_rejects_out_of_scope_agents_before_writes(db):
    profile = create_product_profile(
        replace(TASK_COMMISSION_PROFILE, participant_scope=SCOPE)
    )
    _, store, _, product = _services(db, catalog=ProductCatalog((profile,)))
    with pytest.raises(ContextCollabValidationError, match="participant scope"):
        product.publish_task(
            PublishTaskCommand("deliver", "details", SPACE, OUTSIDE.agent_id), _actor()
        )
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).count() == 0
        assert session.query(ContextCollabTaskModel).count() == 0
        assert session.query(ContextCollabEventModel).count() == 0
    assert store.list_bindings_in_axes(current_axes()) == []
