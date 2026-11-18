"""One L6 Instantiate owns new Collet creation, binding and partial-write recovery."""

from dataclasses import replace

import pytest
from sqlalchemy import event as sql_event, func, select

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ProductInstantiationIncompleteError,
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
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
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductInstantiateNewCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    ProductMethodBindingRecovery,
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
    OPEN_SINGLE_COMMISSION_PROFILE,
)

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = AgentRef("bot", "worker")
RESOURCE = ResourceRef("document", "requirements", "v1")


@pytest.fixture
def runtime(db):
    profile = replace(
        OPEN_SINGLE_COMMISSION_PROFILE,
        participant_scope=ParticipantScope(ParticipantScopeKind.ALLOWLIST, (BOT,)),
    )
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    store = InMemoryMethodStore()
    authorizer = StructuralContextCollabAuthorizer()
    l4 = ContextService(
        repo, ProductCollaborationPolicy(catalog, authorizer), authorizer
    )
    service = ProductInstantiationService(repo, store, catalog, authorizer, l4)
    command = ProductInstantiateNewCommand(
        ColletCreateCommand(
            OWNER.actor, title="Commission", description="A bounded collaboration"
        ),
        profile.ref,
        participant_refs=(BOT,),
        resource_refs=(RESOURCE,),
    )
    return service, repo, store, catalog, authorizer, l4, command


def _counts(db):
    with db.orm_session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                ContextCollabColletModel,
                ContextCollabEventModel,
                ContextCollabTaskModel,
            )
        )


def _assert_bound(repo, store, catalog, instance):
    record = repo.get_current_status(instance.collet_ref)
    assert record.product_instance == instance
    assert record.task is None and record.tasks == ()
    assert instance.root_task_ref is None
    assert record.collet.version == 2
    assert record.collet.title == "Commission"
    assert record.collet.description == "A bounded collaboration"
    assert record.collet.metadata.created_by == OWNER.actor
    assert record.collet.metadata.updated_by == OWNER.actor
    profile = catalog.get(instance.profile_ref.profile_id, instance.profile_ref.version)
    assert instance.governance == profile.governance
    assert instance.negotiation.accepted
    assert len(record.participants) == 2 and len(record.role_bindings) == 2
    assert record.creation_facts["resource_refs"] == [RESOURCE.to_dict()]
    [evidence] = record.events
    assert evidence.event_kind == "collet.participants_bound"
    assert evidence.event_id == instance.creation_event_ref
    assert evidence.task_ref is None
    bindings = store.list_bindings(current_axes(), instance.collet_ref)
    assert {b.method_kind for b in bindings} == {"Open", "Single"}
    assert {b.method_binding_id for b in bindings} == set(instance.method_binding_refs)
    assert len(store.list_composition_edges(current_axes(), instance.collet_ref)) == 1
    for binding in bindings:
        assert binding.role_binding_refs == instance.role_binding_refs
        args = (current_axes(), binding.method_binding_id, instance.collet_ref)
        assert store.list_opportunities(*args) == []
        assert store.list_applications(*args) == []
        assert store.list_selections(*args) == []


def test_one_l6_call_creates_collet_through_public_l4_then_binds(
    runtime, db, monkeypatch
):
    service, repo, store, catalog, _, l4, command = runtime
    original = l4.create_collet
    created_ids = []

    def record_l4_create(collet_command, context):
        assert collet_command == command.collet and context == OWNER
        collet = original(collet_command, context)
        before = repo.get_current_status(collet.collet_id)
        assert before.product_instance is None and before.events == []
        assert before.task is None and before.collet.version == 1
        created_ids.append(collet.collet_id)
        return collet

    monkeypatch.setattr(l4, "create_collet", record_l4_create)
    instance = service.instantiate(command, OWNER)
    assert created_ids == [instance.collet_ref]
    assert _counts(db) == (1, 1, 0)
    _assert_bound(repo, store, catalog, instance)


@pytest.mark.parametrize(
    "change",
    [
        {"profile_ref": VersionedProfileRef("missing", 1)},
        {"participant_refs": ()},
        {"participant_refs": (AgentRef("bot", "outsider"),)},
        {
            "participant_refs": (BOT, AgentRef("bot", "outsider")),
            "executor_ref": BOT,
        },
        {"collet": ColletCreateCommand(PrincipalRef("human", "other"))},
    ],
)
def test_invalid_selection_fails_before_l4_creation(runtime, db, change):
    service, _, store, _, _, _, command = runtime
    with pytest.raises(ContextCollabValidationError):
        service.instantiate(replace(command, **change), OWNER)
    assert _counts(db) == (0, 0, 0)
    assert store.list_bindings_in_axes(current_axes()) == []


def test_incompatible_profile_is_rejected_before_l4_creation(runtime, db):
    _, repo, store, catalog, authorizer, l4, command = runtime
    profile = catalog.get(command.profile_ref.profile_id, command.profile_ref.version)
    incompatible = replace(
        profile,
        compatibility=replace(
            profile.compatibility, required_extensions=("unavailable",)
        ),
    )
    service = ProductInstantiationService(
        repo, store, ProductCatalog((incompatible,)), authorizer, l4
    )
    with pytest.raises(ContextCollabValidationError, match="negotiation rejected"):
        service.instantiate(command, OWNER)
    assert _counts(db) == (0, 0, 0)


@pytest.mark.parametrize("check", ["check_create", "check_create_collet"])
def test_l6_and_l4_authorization_are_both_enforced(runtime, db, monkeypatch, check):
    service, _, _, _, authorizer, _, command = runtime

    def deny(*args, **kwargs):
        raise ContextCollabValidationError("creation denied")

    monkeypatch.setattr(authorizer, check, deny)
    with pytest.raises(ContextCollabValidationError, match="creation denied"):
        service.instantiate(command, OWNER)
    assert _counts(db) == (0, 0, 0)


@pytest.mark.parametrize("stage", ["collet", "binding"])
def test_sql_failure_propagates_with_committed_identity_only_if_created(
    runtime, db, engine, stage
):
    service, repo, store, catalog, _, _, command = runtime
    model = ContextCollabColletModel if stage == "collet" else ContextCollabEventModel

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip()
            .upper()
            .startswith(f"INSERT INTO {model.__tablename__.upper()}")
        ):
            raise RuntimeError("SQL write failed")

    sql_event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        error_type = (
            RuntimeError if stage == "collet" else ProductInstantiationIncompleteError
        )
        with pytest.raises(error_type) as caught:
            service.instantiate(command, OWNER)
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_insert)
    assert store.list_bindings_in_axes(current_axes()) == []
    if stage == "collet":
        assert _counts(db) == (0, 0, 0)
        return
    error = caught.value
    assert isinstance(error.__cause__, RuntimeError)
    assert str(error.__cause__) == "SQL write failed"
    assert error.expected_version == 1
    bare = repo.get_current_status(error.collet_id)
    assert bare.product_instance is None and bare.events == [] and bare.task is None
    assert _counts(db) == (1, 0, 0)
    # Resume binding on the committed context; never repeat the new-create command.
    instance = service.instantiate(
        ProductInstantiateCommand(
            error.collet_id,
            command.profile_ref,
            error.expected_version,
            command.participant_refs,
            resource_refs=command.resource_refs,
        ),
        OWNER,
    )
    _assert_bound(repo, store, catalog, instance)
    assert _counts(db) == (1, 1, 0)


@pytest.mark.parametrize("stage", ["second_binding", "edge"])
def test_partial_method_failure_recovers_pinned_instance_without_new_collet(
    runtime, db, monkeypatch, stage
):
    service, repo, store, catalog, _, _, command = runtime
    operation = (
        "upsert_binding" if stage == "second_binding" else "insert_composition_edge"
    )
    original = getattr(store, operation)
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if stage == "edge" or calls == 2:
            raise RuntimeError("MethodStore write failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, operation, fail)
    with pytest.raises(ProductInstantiationIncompleteError) as caught:
        service.instantiate(command, OWNER)
    error = caught.value
    assert isinstance(error.__cause__, RuntimeError)
    before = repo.get_current_status(error.collet_id)
    assert before.product_instance is not None and before.collet.version == 2
    assert error.expected_version == 1  # Creation revision, not a recovery CAS token.
    assert _counts(db) == (1, 1, 0)
    assert len(store.list_bindings(current_axes(), error.collet_id)) == (
        1 if stage == "second_binding" else 2
    )
    monkeypatch.setattr(store, operation, original)
    instance = before.product_instance
    ProductMethodBindingRecovery(store, repo, catalog).recover(
        error.collet_id,
        OWNER,
        method_binding_id=instance.method_binding_refs[0],
        now="2026-11-18T00:00:00+00:00",
    )
    assert repo.get_current_status(error.collet_id) == before
    _assert_bound(repo, store, catalog, instance)
    assert _counts(db) == (1, 1, 0)


def test_repeating_new_creation_is_not_an_idempotent_retry(runtime, db):
    service, repo, store, catalog, _, _, command = runtime
    first = service.instantiate(command, OWNER)
    second = service.instantiate(command, OWNER)
    assert first.collet_ref != second.collet_ref
    assert set(first.method_binding_refs).isdisjoint(second.method_binding_refs)
    _assert_bound(repo, store, catalog, first)
    _assert_bound(repo, store, catalog, second)
    assert _counts(db) == (2, 2, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"collet": None},
        {"collet": {}},
        {"profile_ref": "task_commission"},
        {"resource_refs": None},
        {"resource_refs": ({"resource_id": "bad"},)},
        {"executor_ref": AgentRef("bot", "outsider")},
    ],
)
def test_new_command_rejects_invalid_types(runtime, change):
    *_, command = runtime
    with pytest.raises(ContextCollabValidationError):
        replace(command, **change)


def test_new_command_copies_mutable_inputs(runtime):
    *_, command = runtime
    participants, resources = [BOT], [RESOURCE]
    supplied = replace(command, participant_refs=participants, resource_refs=resources)
    participants.clear()
    resources.clear()
    assert supplied.participant_refs == (BOT,)
    assert supplied.resource_refs == (RESOURCE,)
