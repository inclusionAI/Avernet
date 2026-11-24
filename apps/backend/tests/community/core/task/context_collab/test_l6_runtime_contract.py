"""Repository-backed evidence: pinned selections and no generic/product bypass."""

from unittest.mock import Mock
import json
from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    DomainMapping,
    DomainProfile,
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_policy import (
    ProductCollaborationPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductInstantiateNewCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_instantiation import (
    ProductInstantiationService,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT_REF,
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
from agentclaw.community.core.task.context_collab.products.legacy_profiles import (
    LEGACY_PRODUCT_PROFILES,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    SwarmExecutionControl,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    CollaborationCreateCommand,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    ensure_top_level_binding,
)
from agentclaw.community.di.modules.task_commission_method_ops_adapter import (
    TaskCommissionOpenMethodAdapter,
)
from sqlalchemy import event as sql_event
from sqlalchemy import func, select

from tests.community.core.task.context_collab.product_catalog import (
    standard_task_workflow,
)

LEGACY_PRODUCT_CATALOG = ProductCatalog(
    (*LEGACY_PRODUCT_PROFILES, TASK_COMMISSION_PROFILE)
)

OWNER = PrincipalRef("human", "owner")
BOT = PrincipalRef("bot", "worker")
SPACE = SpaceRef("space", "test")


def _actor(principal=OWNER):
    return ActorContext(actor=principal, source="api")


def _command(**overrides):
    fields = dict(
        to_space=SPACE,
        principal_ref=OWNER,
        method_kind="Open",
        executor_ref=AgentRef("bot", "worker"),
        objective="deliver",
        resource_refs=[],
        product_kind="task_commission",
        workflow_kind=None,
        product_profile_ref=VersionedProfileRef("task_commission", 1),
    )
    return CollaborationCreateCommand(**(fields | overrides))


def _services(db, dispatch_port=None, *, catalog=LEGACY_PRODUCT_CATALOG):
    repo = ContextCollabRepository(db, product_catalog=catalog)
    store = InMemoryMethodStore()
    authorizer = StructuralContextCollabAuthorizer()
    generic = ContextCollabService(
        repository=repo,
        method_store=store,
        authorizer=authorizer,
        product_catalog=catalog,
    )
    port = TaskCommissionOpenMethodAdapter(
        open_ops=OpenMethodService(
            store=store, authorizer=StructuralMethodOpsAuthorizer(), collab_repo=repo
        ),
        product_catalog=catalog,
    )
    product = TaskCommissionService(
        collab_repo=repo,
        method_store=store,
        authorizer=authorizer,
        method_ops_port=port,
        dispatch_port=dispatch_port,
        swarm_execution=Mock(spec=SwarmExecutionControl),
        product_catalog=catalog,
    )
    return repo, store, generic, product


def _create_collet(repo):
    collet = repo.create_l4_collet(ColletCreateCommand(OWNER), OWNER).collet
    return repo.create_l4_task(
        collet.collet_id, TaskCreateCommand(collet.version, OWNER, "deliver"), OWNER
    ).collet


def _instantiate(
    repo,
    store,
    collet,
    *,
    catalog=LEGACY_PRODUCT_CATALOG,
    profile_ref=VersionedProfileRef("task_commission", 1),
):
    return ProductInstantiationService(
        repo,
        store,
        catalog,
        StructuralContextCollabAuthorizer(),
        ContextService(
            repo,
            ProductCollaborationPolicy(catalog, StructuralContextCollabAuthorizer()),
        ),
    ).instantiate(
        ProductInstantiateCommand(
            collet_id=collet.collet_id,
            expected_version=collet.version,
            profile_ref=profile_ref,
            participant_refs=(AgentRef("bot", "worker"),),
        ),
        _actor(),
    )


def _append(
    service, collet_id, version, event="task.started", actor=OWNER, **overrides
):
    fields = dict(
        expected_version=version,
        event_kind=event,
        primitive_type="Task",
        actor_ref=actor,
        parent_event=None,
        visibility="public",
        payload={},
    )
    return service.append_event(
        collet_id, EventAppendCommand(**(fields | overrides)), _actor(actor)
    )


def _counts(db):
    with db.orm_session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ContextCollabColletModel, ContextCollabEventModel)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        dict(workflow_kind="bbs_public_discussion"),
        dict(method_kind="Single"),
        dict(executor_ref=None),
        dict(product_kind="not_registered"),
        dict(product_kind=None, workflow_kind="not_registered"),
    ],
)
def test_invalid_product_composition_rejected_before_any_write(db, overrides):
    _, store, service, _ = _services(db)
    with pytest.raises(ContextCollabValidationError):
        service.create_collaboration(_command(**overrides), _actor())
    assert _counts(db) == (0, 0)
    assert store.list_bindings(current_axes(), "missing") == []


def test_both_entrypoints_use_same_executor_and_sequence_contract(db):
    repo, _, generic, product = _services(db)
    created = product.publish_task(
        PublishTaskCommand("deliver", "details", SPACE, "worker"), _actor()
    )
    collet_id = created.collet_id
    assert repo.get_current_status(collet_id).product_instance is not None
    command = TaskAdvanceCommand(collet_id, artifacts=["artifact-1"])
    for attempt in (
        lambda: product.submit_task(command, _actor(BOT)),
        lambda: _append(
            generic,
            collet_id,
            2,
            "task.submitted",
            BOT,
            payload={"artifacts": ["artifact-1"]},
        ),
    ):
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            attempt()
    assert _counts(db) == (1, 2)
    started = _append(generic, collet_id, 2, actor=BOT)
    for attempt in (
        lambda: product.submit_task(command, _actor()),
        lambda: _append(
            generic,
            collet_id,
            started.context_version,
            "task.submitted",
            payload={"artifacts": ["artifact-1"]},
        ),
    ):
        with pytest.raises(ContextCollabValidationError, match="designated executor"):
            attempt()
    assert _counts(db) == (1, 3)
    submitted = product.submit_task(command, _actor(BOT))
    assert submitted.status == "reviewing"
    accepted = _append(generic, collet_id, submitted.context_version, "task.accepted")
    assert accepted.current_status == "done"
    for attempt in (
        lambda: product.start_task(TaskAdvanceCommand(collet_id), _actor(BOT)),
        lambda: _append(generic, collet_id, accepted.context_version, actor=BOT),
    ):
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            attempt()
    assert _counts(db) == (1, 5)


def test_generic_message_is_nontransition_but_stale_control_is_conflict(db):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    started = _append(service, created.collet_id, 3, actor=BOT)
    message = _append(
        service,
        created.collet_id,
        started.context_version,
        "user_interaction.posted",
        primitive_type="Message",
        payload={"content": "update"},
    )
    assert message.current_status == "in_progress"
    with pytest.raises(ContextCollabVersionConflict):
        _append(service, created.collet_id, 1)
    assert _counts(db) == (1, 4)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(primitive_type="Message"),
        dict(visibility="private"),
        dict(payload={"percent": 12}),
        dict(event_kind="task.completed"),
    ],
)
def test_generic_control_cannot_bypass_product_shape(db, overrides):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    with pytest.raises(ContextCollabValidationError):
        _append(service, created.collet_id, 3, actor=BOT, **overrides)
    assert _counts(db) == (1, 2)


@pytest.mark.parametrize(
    "impostor", [PrincipalRef("human", "worker"), PrincipalRef("bot", "owner")]
)
def test_pinned_l4_roles_use_typed_identity_not_just_same_id(db, impostor):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    with pytest.raises(ContextCollabValidationError, match="only the principal"):
        _append(service, created.collet_id, 3, actor=impostor)
    assert _counts(db) == (1, 2)


def test_pinned_instance_round_trip_and_heal_do_not_renegotiate(db, monkeypatch):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    record = repo.get_current_status(created.collet_id)
    instance = record.product_instance
    assert instance.profile_ref.version == 1
    assert instance.workflow_ref is None
    assert instance.task_contract_ref == STANDARD_TASK_CONTRACT_REF
    assert (
        instance.creation_event_ref
        == repo.get_current_status(created.collet_id).task.created_event_ref
    )
    binding = store.get_binding(
        current_axes(), instance.method_binding_refs[0], created.collet_id
    )
    assert binding.role_binding_refs == instance.role_binding_refs
    assert binding.profile_versions == {
        method.method: method.version for method in instance.method_profiles
    }
    # L6 role references live in ProductInstance; creation facts remain plain
    # facts and do not duplicate Collet/Task identity or role aggregates.
    assert len(instance.role_binding_refs) == 2
    assert record.creation_facts["resource_refs"] == []
    assert set(record.creation_facts) == {
        "objective",
        "resource_refs",
        "method_kind",
        "executor_ref",
        "product_kind",
        "workflow_kind",
        "product_profile_ref",
        "product_instance",
    }

    def cannot_negotiate(_self):
        pytest.fail(
            "historical read must not recompute negotiation against installed capabilities"
        )

    monkeypatch.setattr(ProductCatalog, "capabilities", cannot_negotiate)
    restarted_repo = ContextCollabRepository(db, product_catalog=LEGACY_PRODUCT_CATALOG)
    recovered = restarted_repo.get_current_status(created.collet_id)
    assert recovered.product_instance == instance
    healed = ensure_top_level_binding(
        InMemoryMethodStore(),
        current_axes(),
        created.collet_id,
        restarted_repo,
        _actor(),
        now="2026-11-13T00:00:00+00:00",
        catalog=LEGACY_PRODUCT_CATALOG,
    )
    assert healed.profile_versions == binding.profile_versions
    assert healed.role_binding_refs == binding.role_binding_refs


@pytest.mark.parametrize(
    "corruption",
    ["missing_instance", "role", "version", "evidence", "method", "negotiation"],
)
def test_corrupt_pinned_metadata_fails_closed(db, corruption):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.creation_facts)
        if corruption == "missing_instance":
            del data["product_instance"]
        elif corruption == "role":
            data["product_instance"]["role_binding_refs"] = []
        elif corruption == "version":
            data["product_profile_ref"]["version"] = 999
        elif corruption == "evidence":
            data["product_instance"]["creation_event_ref"] = "not-the-initial-event"
        elif corruption == "method":
            data["product_instance"]["method_binding_refs"] = ["not-the-binding"]
        else:
            data["product_instance"]["negotiation"]["missing_capabilities"] = [
                "method:Open@0.2"
            ]
        row.creation_facts = json.dumps(data)
    with pytest.raises(ContextCollabValidationError):
        repo.get_current_status(created.collet_id)


def test_legacy_rows_remain_readable_without_fabricated_negotiation(db):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.creation_facts)
        for key in (
            "product_profile_ref",
            "product_instance",
        ):
            del data[key]
        row.creation_facts = json.dumps(data)
    record = repo.get_current_status(created.collet_id)
    assert record.product_instance is None
    assert record.creation_facts["product_profile_ref"] is None
    assert record.creation_facts == {**data, "product_profile_ref": None}
    assert _counts(db) == (1, 2)


def test_unprofiled_generic_context_keeps_legacy_wire_contract(db):
    repo, _, service, _ = _services(db)
    created = service.create_collaboration(
        _command(product_kind=None, workflow_kind=None, product_profile_ref=None),
        _actor(),
    )
    record = repo.get_current_status(created.collet_id)
    assert record.product_instance is None
    assert record.creation_facts["product_profile_ref"] is None
    assert set(record.events[0].payload) == {
        "objective",
        "resource_refs",
        "method_kind",
        "executor_ref",
        "product_kind",
        "workflow_kind",
        "product_profile_ref",
    }


def test_bbs_generic_cannot_reply_to_task_skeleton(db):
    repo, _, service, _ = _services(db)
    created = service.create_collaboration(
        _command(
            product_kind="bbs_public_discussion",
            workflow_kind="bbs_public_discussion",
            method_kind="Open + Group",
            product_profile_ref=VersionedProfileRef("bbs_public_discussion", 1),
            executor_ref=None,
        ),
        _actor(),
    )
    with pytest.raises(ContextCollabValidationError, match="not a Message"):
        _append(
            service,
            created.collet_id,
            1,
            "user_interaction.posted",
            primitive_type="Message",
            payload={"content": "reply"},
            parent_event=repo.get_current_status(
                created.collet_id
            ).task.created_event_ref,
        )
    assert _counts(db) == (1, 1)


def test_message_cannot_smuggle_commission_progress_evidence(db):
    repo, store, service, _ = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    with pytest.raises(ContextCollabValidationError, match="only reportable"):
        _append(
            service,
            created.collet_id,
            3,
            "user_interaction.posted",
            primitive_type="Message",
            payload={"content": "update", "percent": 50},
        )
    assert _counts(db) == (1, 2)


@pytest.mark.parametrize("legacy", [False, True])
def test_product_work_uses_exact_machine_even_for_legacy_v1(db, monkeypatch, legacy):
    from agentclaw.community.core.task.context_collab.g4_contracts_consistency import (
        state_machine,
    )

    repo, store, generic, product = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    if legacy:
        with db.orm_session() as session:
            row = session.query(ContextCollabColletModel).one()
            data = json.loads(row.creation_facts)
            for key in (
                "product_profile_ref",
                "product_instance",
            ):
                del data[key]
            row.creation_facts = json.dumps(data)
    monkeypatch.setitem(state_machine.MACHINE_REGISTRY, "task_commission_goal", None)
    started = product.start_task(TaskAdvanceCommand(created.collet_id), _actor(BOT))
    assert started.status == "in_progress"


def test_commission_dispatch_correlates_persisted_selection_and_actual_l5_binding(db):
    class CaptureDispatch:
        def __init__(self):
            self.sent = []

        def dispatch_task_info(self, info):
            self.sent.append(info)

    delivery = CaptureDispatch()
    repo, store, _, product = _services(db, dispatch_port=delivery)
    created = product.publish_task(
        PublishTaskCommand("deliver", "details", SPACE, "worker"), _actor()
    )
    record = repo.get_current_status(created.collet_id)
    instance = record.product_instance
    [sent] = delivery.sent
    assert sent.product_profile_ref == instance.profile_ref
    assert (sent.method_binding_ref,) == instance.method_binding_refs
    binding = store.get_binding(
        current_axes(), sent.method_binding_ref, created.collet_id
    )
    assert binding.role_binding_refs == instance.role_binding_refs
    [application] = store.list_applications(
        current_axes(), sent.method_binding_ref, created.collet_id
    )
    assert application.state.value == "ADMITTED"
    assert record.current_snapshot.status == "created"  # Delivery != completion.


def test_uninstalled_catalog_rejects_selection_without_work_or_method_writes(db):
    catalog = ProductCatalog(())
    repo, store, _, product = _services(db, catalog=catalog)
    with pytest.raises(ContextCollabValidationError, match="unknown product"):
        product.publish_task(
            PublishTaskCommand("deliver", "details", SPACE, "worker"), _actor()
        )
    assert _counts(db) == (0, 0)
    created = _create_collet(repo)
    with pytest.raises(ContextCollabValidationError, match="unknown product"):
        _instantiate(repo, store, created, catalog=catalog)
    # L4 creation is a separate committed operation, not rolled back by L6.
    record = repo.get_current_status(created.collet_id)
    assert record.product_instance is None
    assert record.task.status == "created" and record.task.version == 1
    assert _counts(db) == (1, 1)
    assert store.list_bindings(current_axes(), created.collet_id) == []


def test_injected_exact_profile_survives_storage_recovery_and_shared_g4(
    db, monkeypatch
):
    # The isolated catalog deliberately allows private messages, unlike the
    # installed initial contract.
    v2 = replace(
        TASK_COMMISSION_PROFILE,
        ref=VersionedProfileRef("task_commission", 2),
        domain=DomainProfile(
            ref=VersionedProfileRef("custom.commission.domain", 2),
            mappings=(DomainMapping("assignment", "L4.Task"),),
        ),
        workflow=replace(standard_task_workflow(), public_events_only=False),
    )
    catalog = ProductCatalog((v2,))
    repo, store, _, _ = _services(db, catalog=catalog)
    created = _create_collet(repo)
    _instantiate(repo, store, created, catalog=catalog, profile_ref=v2.ref)
    collet_id = created.collet_id
    instance = repo.get_current_status(collet_id).product_instance
    assert instance.profile_ref == v2.ref
    assert instance.domain_ref == v2.domain.ref

    def cannot_negotiate(_self):
        pytest.fail("historical read/recovery must not renegotiate")

    monkeypatch.setattr(ProductCatalog, "capabilities", cannot_negotiate)
    restarted_repo, recovered_store, generic, product = _services(db, catalog=catalog)
    record = restarted_repo.get_current_status(collet_id)
    assert record.product_instance == instance
    healed = ensure_top_level_binding(
        recovered_store,
        current_axes(),
        collet_id,
        restarted_repo,
        _actor(),
        now="2026-11-13T00:00:00+00:00",
        catalog=catalog,
    )
    assert healed.profile_versions == {"Open": "0.2"}
    assert healed.role_binding_refs == instance.role_binding_refs
    assert (healed.method_binding_id,) == instance.method_binding_refs
    # Generic G4 must use the injected profile, not the installed public-only rule.
    message = _append(
        generic,
        collet_id,
        3,
        "user_interaction.posted",
        primitive_type="Message",
        visibility="private",
        payload={"content": "note"},
    )
    assert message.current_status == "created"
    started = product.start_task(TaskAdvanceCommand(collet_id), _actor(BOT))
    assert started.context_version == 5
    assert restarted_repo.get_current_status(collet_id).product_instance == instance
    assert _counts(db) == (1, 4)


def test_legacy_commission_progress_requires_typed_executor(db):
    repo, store, generic, product = _services(db)
    created = _create_collet(repo)
    _instantiate(repo, store, created)
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        data = json.loads(row.creation_facts)
        for key in (
            "product_profile_ref",
            "product_instance",
        ):
            del data[key]
        row.creation_facts = json.dumps(data)
    product.start_task(TaskAdvanceCommand(created.collet_id), _actor(BOT))
    for principal, message in (
        (OWNER, "designated executor"),
        (PrincipalRef("human", BOT.principal_id), "only the principal"),
    ):
        with pytest.raises(ContextCollabValidationError, match=message):
            product.report_progress(
                TaskAdvanceCommand(created.collet_id, percent=20),
                _actor(principal),
            )
    progressed = product.report_progress(
        TaskAdvanceCommand(created.collet_id, percent=20),
        _actor(BOT),
    )
    assert progressed.context_version == 5


@pytest.fixture
def instantiated_without_work(db):
    repo, store, _, _ = _services(db)
    authorizer = StructuralContextCollabAuthorizer()
    l4 = ContextService(
        repo, ProductCollaborationPolicy(LEGACY_PRODUCT_CATALOG, authorizer)
    )
    instance = ProductInstantiationService(
        repo, store, LEGACY_PRODUCT_CATALOG, authorizer, l4
    ).instantiate(
        ProductInstantiateNewCommand(
            collet=ColletCreateCommand(OWNER),
            profile_ref=VersionedProfileRef("task_commission", 1),
            participant_refs=(AgentRef("bot", "worker"),),
        ),
        _actor(),
    )
    record = repo.get_current_status(instance.collet_ref)
    assert record.task is None and record.tasks == ()
    return l4, repo, record


def test_create_task_after_instantiation_preserves_selection_and_bindings(
    instantiated_without_work, db
):
    l4, repo, before = instantiated_without_work
    cid = before.collet.collet_id
    with db.orm_session() as session:
        raw_facts = session.query(ContextCollabColletModel).one().creation_facts
    task = l4.create_task(
        cid,
        TaskCreateCommand(before.collet.version, OWNER, "deliver", ("accepted",)),
        _actor(),
    )
    after = repo.get_current_status(cid)
    assert task == after.task and task.status == "created" and task.version == 1
    assert task.executor_refs == (AgentRef("bot", "worker"),)
    assert task.completion_conditions == ("accepted",)
    assert after.collet.version == before.collet.version + 1
    assert after.product_instance == replace(
        before.product_instance, root_task_ref=task.task_id
    )
    assert after.creation_facts == before.creation_facts
    assert after.participants == before.participants
    assert after.role_bindings == before.role_bindings
    assert after.events[:-1] == before.events
    event = after.events[-1]
    assert event.event_kind == "task.created" and event.task_ref == task.task_id
    assert event.payload["executor_refs"] == [AgentRef("bot", "worker").to_dict()]
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).one().creation_facts == raw_facts
        assert session.query(ContextCollabTaskModel).count() == 1


@pytest.mark.parametrize("rejection", ["stale", "stranger", "duplicate"])
def test_create_task_after_instantiation_rejects_without_writes(
    instantiated_without_work, rejection
):
    l4, repo, before = instantiated_without_work
    cid = before.collet.collet_id
    command = TaskCreateCommand(before.collet.version, OWNER, "deliver")
    if rejection == "duplicate":
        l4.create_task(cid, command, _actor())
        before = repo.get_current_status(cid)
        command = replace(command, expected_version=before.collet.version)
    elif rejection == "stale":
        command = replace(command, expected_version=before.collet.version - 1)
    actor = (
        _actor(PrincipalRef("human", "stranger"))
        if rejection == "stranger"
        else _actor()
    )
    error = (
        ContextCollabVersionConflict
        if rejection == "stale"
        else ContextCollabValidationError
    )
    with pytest.raises(error):
        l4.create_task(cid, command, actor)
    assert repo.get_current_status(cid) == before


@pytest.mark.parametrize("target", [ContextCollabTaskModel, ContextCollabEventModel])
def test_create_task_after_instantiation_write_failure_preserves_selection(
    instantiated_without_work, db, target
):
    l4, repo, before = instantiated_without_work
    cid = before.collet.collet_id

    def fail(*_):
        raise RuntimeError("Task creation insert failed")

    sql_event.listen(target, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Task creation insert failed"):
            l4.create_task(
                cid,
                TaskCreateCommand(before.collet.version, OWNER, "deliver"),
                _actor(),
            )
    finally:
        sql_event.remove(target, "before_insert", fail)
    assert repo.get_current_status(cid) == before
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).count() == 1
        assert session.query(ContextCollabTaskModel).count() == 0
        assert session.query(ContextCollabEventModel).count() == len(before.events)
