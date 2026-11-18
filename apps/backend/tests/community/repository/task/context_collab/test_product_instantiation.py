"""Explicit application/L6 installation never becomes an L4 creation side effect."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import event as sql_event

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
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    ColletCreateCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.resource_binding import (
    ResourceBinding,
    resource_bindings_from_initial_event,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
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
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    ensure_top_level_binding,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_instantiation import (
    ProductInstantiationService,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = AgentRef("bot", "worker")
INPUT_RESOURCES = (
    ResourceRef("document", "requirements", "v1"),
    ResourceRef("repository", "calculator", "baseline-v1"),
)


@pytest.fixture
def runtime(db):
    # No publication/admission is needed to test definition selection itself.
    profile = create_product_profile(
        replace(
            TASK_COMMISSION_PROFILE,
            composition=replace(TASK_COMMISSION_PROFILE.composition, policies=()),
        )
    )
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    store = InMemoryMethodStore()
    service = ProductInstantiationService(
        repo, store, catalog, StructuralContextCollabAuthorizer()
    )
    created = repo.create_l4_collet(
        ColletCreateCommand(OWNER.actor),
        OWNER.actor,
    )
    created = repo.create_l4_task(
        created.collet.collet_id,
        TaskCreateCommand(1, OWNER.actor, "Deliver work"),
        OWNER.actor,
    )
    command = ProductInstantiateCommand(
        created.collet.collet_id,
        profile.ref,
        created.collet.version,
        (BOT,),
        resource_refs=INPUT_RESOURCES,
    )
    return service, repo, store, catalog, command


def test_l6_instantiation_is_explicit_and_preserves_initial_l4_authorities(runtime):
    service, repo, store, _, command = runtime
    before = repo.get_current_status(command.collet_id)
    assert before.product_instance is None
    assert store.list_bindings(current_axes(), command.collet_id) == []
    instance = service.instantiate(command, OWNER)
    after = repo.get_current_status(command.collet_id)
    assert after.product_instance == instance
    assert before.task.executor_refs == ()
    assert after.task.executor_refs == (BOT,)
    assert after.collet.version == 3 and after.task.version == 2
    assert after.collet.metadata is not None
    assert after.collet.etag != before.collet.etag
    assert after.events[:1] == before.events
    assert after.events[-1].event_kind == "task.participants_bound"
    assert after.participants and len(after.role_bindings) == 2
    assert after.creation_facts["product_profile_ref"] == {
        "profile_id": command.profile_ref.profile_id,
        "version": command.profile_ref.version,
    }
    bindings = store.list_bindings(current_axes(), command.collet_id)
    assert len(bindings) == 1
    assert bindings[0].method_binding_id in instance.method_binding_refs
    assert bindings[0].role_binding_refs == instance.role_binding_refs


def test_method_recovery_cannot_invent_binding_for_methodless_collet(runtime):
    _, repo, store, catalog, command = runtime
    with pytest.raises(ContextCollabValidationError, match="no selected Method"):
        ensure_top_level_binding(
            store,
            current_axes(),
            command.collet_id,
            repo,
            OWNER,
            now="2026-11-16T00:00:00+00:00",
            catalog=catalog,
        )
    assert store.list_bindings(current_axes(), command.collet_id) == []
    assert repo.get_current_status(command.collet_id).product_instance is None


@pytest.mark.parametrize(
    "actor",
    [
        ActorContext(PrincipalRef("bot", "worker"), "a2a"),
        ActorContext(PrincipalRef("human", "outsider"), "api"),
    ],
)
def test_non_owner_cannot_select_product_on_created_collet(runtime, actor):
    service, repo, store, _, command = runtime
    before = repo.get_current_status(command.collet_id)
    with pytest.raises(
        ContextCollabValidationError, match="initiator or responsible principal"
    ):
        service.instantiate(command, actor)
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == []


def test_responsible_principal_may_select_even_when_not_initiator(runtime):
    service, repo, _, _, command = runtime
    responsible = ActorContext(PrincipalRef("human", "responsible"), "api")
    created = repo.create_l4_collet(
        ColletCreateCommand(responsible.actor),
        OWNER.actor,
    )
    repo.create_l4_task(
        created.collet.collet_id,
        TaskCreateCommand(1, responsible.actor, "Delegated"),
        OWNER.actor,
    )
    instance = service.instantiate(
        replace(command, collet_id=created.collet.collet_id), responsible
    )
    assert instance.collet_ref == created.collet.collet_id


@pytest.mark.parametrize(
    "change, error",
    [
        ({"expected_version": 1}, ContextCollabVersionConflict),
        (
            {"profile_ref": VersionedProfileRef("missing", 1)},
            ContextCollabValidationError,
        ),
    ],
)
def test_stale_or_unknown_selection_does_not_mutate_l4(runtime, change, error):
    service, repo, store, _, command = runtime
    before = repo.get_current_status(command.collet_id)
    with pytest.raises(error):
        service.instantiate(replace(command, **change), OWNER)
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == []


def test_product_selection_is_immutable_even_with_same_profile_and_collet_revision(
    runtime,
):
    service, repo, store, _, command = runtime
    service.instantiate(command, OWNER)
    before = repo.get_current_status(command.collet_id)
    binding = store.list_bindings(current_axes(), command.collet_id)
    with pytest.raises(ContextCollabValidationError, match="untouched L4 Collet"):
        service.instantiate(
            replace(command, expected_version=before.collet.version), OWNER
        )
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == binding


def test_selection_rejects_collet_advanced_by_message(runtime):
    service, repo, store, _, command = runtime
    repo.append_event(
        command.collet_id,
        AppendEventRecordRequest(
            command.expected_version,
            "user_interaction.posted",
            "Message",
            OWNER.actor,
            None,
            "public",
            {"content": "note"},
        ),
    )
    before = repo.get_current_status(command.collet_id)
    assert before.task.version == 1
    with pytest.raises(ContextCollabValidationError, match="untouched L4 Collet"):
        service.instantiate(replace(command, expected_version=3), OWNER)
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == []


def test_multiple_participants_require_explicit_scalar_executor_designation(runtime):
    service, repo, _, _, command = runtime
    refs = (BOT, AgentRef("bot", "second"))
    before = repo.get_current_status(command.collet_id)
    with pytest.raises(ContextCollabValidationError, match="requires executor_ref"):
        service.instantiate(replace(command, participant_refs=refs), OWNER)
    assert repo.get_current_status(command.collet_id) == before
    instance = service.instantiate(
        replace(command, participant_refs=refs, executor_ref=BOT), OWNER
    )
    record = repo.get_current_status(command.collet_id)
    assert record.product_instance == instance
    assert {p.agent_ref for p in record.participants if p.agent_ref} == set(refs)
    assert record.task.executor_refs == (BOT,)


def test_sql_selection_failure_propagates_and_no_l5_binding_is_written(runtime, engine):
    service, repo, store, _, command = runtime
    before = repo.get_current_status(command.collet_id)

    def fail_update(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE"):
            raise RuntimeError("selection write failed")

    sql_event.listen(engine, "before_cursor_execute", fail_update)
    try:
        with pytest.raises(RuntimeError, match="selection write failed"):
            service.instantiate(command, OWNER)
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_update)
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == []


def test_method_store_failure_is_not_success_and_selection_can_be_recovered(
    runtime, monkeypatch
):
    service, repo, store, catalog, command = runtime
    before = repo.get_current_status(command.collet_id)
    original = store.upsert_binding

    def fail(*args, **kwargs):
        raise RuntimeError("method binding write failed")

    monkeypatch.setattr(store, "upsert_binding", fail)
    with pytest.raises(RuntimeError, match="method binding write failed"):
        service.instantiate(command, OWNER)
    after = repo.get_current_status(command.collet_id)
    assert (
        after.product_instance is not None
    )  # SQL and MethodStore are separate transactions.
    assert after.task.executor_refs == (BOT,)
    assert after.events[:1] == before.events
    assert after.events[-1].event_kind == "task.participants_bound"
    assert store.list_bindings(current_axes(), command.collet_id) == []
    monkeypatch.setattr(store, "upsert_binding", original)
    recovered = ensure_top_level_binding(
        store,
        current_axes(),
        command.collet_id,
        repo,
        OWNER,
        now="2026-11-16T00:00:00+00:00",
        catalog=catalog,
    )
    assert recovered.method_binding_id in after.product_instance.method_binding_refs
    assert recovered.role_binding_refs == after.product_instance.role_binding_refs
    assert repo.get_current_status(command.collet_id) == after


@pytest.mark.parametrize(
    "change",
    [
        {"collet_id": ""},
        {"profile_ref": "task_commission"},
        {"expected_version": True},
        {"expected_version": 0},
        {"resource_refs": None},
        {"resource_refs": "requirements"},
        {"resource_refs": ({"resource_id": "requirements"},)},
        {"resource_refs": (ResourceRef("document", "", "v1"),)},
        {"resource_refs": (ResourceRef("document", "requirements", 1),)},
    ],
)
def test_product_instantiation_command_rejects_invalid_input(runtime, change):
    *_, command = runtime
    with pytest.raises(ContextCollabValidationError):
        replace(command, **change)


def test_bare_collet_instantiation_binds_participants_without_creating_work(runtime):
    service, repo, store, _, command = runtime
    collet = repo.create_l4_collet(ColletCreateCommand(OWNER.actor), OWNER.actor).collet
    before = repo.get_current_status(collet.collet_id)
    instance = service.instantiate(
        replace(command, collet_id=collet.collet_id, expected_version=collet.version),
        OWNER,
    )
    after = repo.get_current_status(collet.collet_id)
    assert before.events == [] and before.task is None
    assert after.task is None and after.tasks == ()
    assert instance.root_task_ref is None
    assert after.product_instance == instance
    assert after.collet.version == before.collet.version + 1
    [evidence] = after.events
    assert evidence.event_kind == "collet.participants_bound"
    assert evidence.event_id == instance.creation_event_ref
    assert evidence.task_ref is None and evidence.task_version_after is None
    assert after.participants and after.role_bindings
    assert store.list_bindings(current_axes(), collet.collet_id)


def test_sql_installation_rechecks_untouched_work_after_intervening_message(
    runtime, monkeypatch
):
    service, repo, store, _, command = runtime
    original = repo.attach_product_instance

    def install_after_message(collet_id, **kwargs):
        # An intervening writer changes only the stream / Collet revision, not Task.
        repo.append_event(
            collet_id,
            AppendEventRecordRequest(
                kwargs["expected_version"],
                "user_interaction.posted",
                "Message",
                OWNER.actor,
                None,
                "public",
                {"content": "new evidence"},
            ),
        )
        return original(collet_id, **{**kwargs, "expected_version": 3})

    monkeypatch.setattr(repo, "attach_product_instance", install_after_message)
    with pytest.raises(ContextCollabValidationError, match="untouched L4 Collet"):
        service.instantiate(command, OWNER)
    record = repo.get_current_status(command.collet_id)
    assert record.product_instance is None and record.task.version == 1
    assert record.task.executor_refs == () and not record.participants
    assert len(record.events) == 2
    assert store.list_bindings(current_axes(), command.collet_id) == []


def test_existing_work_instance_cannot_masquerade_as_pre_start_installation(
    runtime, db
):
    service, repo, _, _, command = runtime
    instance = service.instantiate(command, OWNER)
    assert instance.root_task_ref is not None
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=command.collet_id)
            .one()
        )
        facts = json.loads(row.creation_facts)
        facts["product_instance"]["root_task_ref"] = None
        row.creation_facts = json.dumps(facts)
        session.commit()
    with pytest.raises(ContextCollabValidationError, match="missing its root Task"):
        repo.get_current_status(command.collet_id)


@pytest.mark.parametrize("bare_collet", [False, True])
@pytest.mark.parametrize("resource_refs", [(), INPUT_RESOURCES])
def test_instantiation_atomically_binds_input_resources(
    runtime, bare_collet, resource_refs
):
    service, repo, _, _, command = runtime
    if bare_collet:
        collet = repo.create_l4_collet(
            ColletCreateCommand(OWNER.actor), OWNER.actor
        ).collet
        command = replace(
            command, collet_id=collet.collet_id, expected_version=collet.version
        )
    command = replace(command, resource_refs=resource_refs)
    before = repo.get_current_status(command.collet_id)
    instance = service.instantiate(command, OWNER)
    after = repo.get_current_status(command.collet_id)
    assert after.product_instance == instance
    assert after.creation_facts["resource_refs"] == [
        ref.to_dict() for ref in resource_refs
    ]
    assert after.events[:-1] == before.events
    evidence = after.events[-1]
    assert evidence.collab_context_id == instance.collet_ref
    assert evidence.payload["resource_refs"] == [ref.to_dict() for ref in resource_refs]
    assert resource_bindings_from_initial_event(evidence) == tuple(
        ResourceBinding(resource_ref=ref) for ref in resource_refs
    )
    assert evidence.actor_ref == OWNER.actor
    assert after.collet.version == before.collet.version + 1
    if bare_collet:
        assert after.task is None and after.tasks == ()
        assert instance.root_task_ref is None
        assert evidence.event_id == instance.creation_event_ref
    else:
        assert after.task.task_id == before.task.task_id
        assert after.task.version == before.task.version + 1


@pytest.mark.parametrize("bare_collet", [False, True])
def test_resource_binding_event_failure_rolls_back_entire_installation(
    runtime, engine, bare_collet
):
    service, repo, store, _, command = runtime
    if bare_collet:
        collet = repo.create_l4_collet(
            ColletCreateCommand(OWNER.actor), OWNER.actor
        ).collet
        command = replace(
            command, collet_id=collet.collet_id, expected_version=collet.version
        )
    before = repo.get_current_status(command.collet_id)

    def fail_event_insert(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip()
            .upper()
            .startswith(f"INSERT INTO {ContextCollabEventModel.__tablename__.upper()}")
        ):
            raise RuntimeError("resource binding event write failed")

    sql_event.listen(engine, "before_cursor_execute", fail_event_insert)
    try:
        with pytest.raises(RuntimeError, match="resource binding event write failed"):
            service.instantiate(command, OWNER)
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_event_insert)
    assert repo.get_current_status(command.collet_id) == before
    assert store.list_bindings(current_axes(), command.collet_id) == []


def test_resource_input_list_is_normalized_without_retaining_mutable_input(runtime):
    *_, command = runtime
    inputs = list(INPUT_RESOURCES)
    supplied = replace(command, resource_refs=inputs)
    inputs.clear()
    assert isinstance(supplied.resource_refs, tuple)
    assert supplied.resource_refs == INPUT_RESOURCES
