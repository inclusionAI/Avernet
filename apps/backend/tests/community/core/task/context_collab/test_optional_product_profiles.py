"""Workflow omission keeps shared Task enforcement, exact selection and recovery."""

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
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_collab_service import (
    ContextCollabService,
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
    VersionedProfileRef,
    negotiate,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT_REF,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskExecutionReportCommand,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ColletCreateCommand,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.method_bootstrap import (
    ensure_top_level_binding,
)
from agentclaw.community.di.modules.task_commission_method_ops_adapter import (
    TaskCommissionOpenMethodAdapter,
)

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = ActorContext(PrincipalRef("bot", "worker"), "a2a")
SPACE = SpaceRef("space", "test")


def _profile(*, workflow=False, version=2):
    extra = (
        {
            "workflow": TASK_COMMISSION_PROFILE.workflow,
            "domain": TASK_COMMISSION_PROFILE.domain,
        }
        if workflow
        else {}
    )
    return create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("task_commission", version),
            composition=TASK_COMMISSION_PROFILE.composition,
            scenario_roles=TASK_COMMISSION_PROFILE.scenario_roles,
            governance=TASK_COMMISSION_PROFILE.governance,
            # Local library integration is not an L1 interoperability capability offer.
            compatibility=replace(
                TASK_COMMISSION_PROFILE.compatibility,
                required_layers=("L2", "L3", "L4", "L5"),
            ),
            publication=TASK_COMMISSION_PROFILE.publication,
            **extra,
        )
    )


@pytest.fixture(params=[False, True], ids=["standard_task", "explicit_workflow"])
def runtime(db, request):
    profile = _profile(workflow=request.param)
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    store = InMemoryMethodStore()
    authorizer = StructuralContextCollabAuthorizer()
    generic = ContextCollabService(repo, store, authorizer, product_catalog=catalog)
    port = TaskCommissionOpenMethodAdapter(
        OpenMethodService(store, StructuralMethodOpsAuthorizer(), collab_repo=repo),
        catalog,
    )
    product = TaskCommissionService(
        repo, authorizer, store, port, product_catalog=catalog
    )
    return profile, catalog, repo, store, generic, product


def _create(runtime):
    profile, _, _, _, generic, _ = runtime
    return generic.create_collet(
        ColletCreateCommand(
            space=SPACE,
            principal_ref=OWNER.actor,
            product_kind=profile.ref.profile_id,
            workflow_kind=profile.workflow.ref.profile_id if profile.workflow else None,
            method_kind=profile.composition.method_kind,
            executor_ref=AgentRef("bot", "worker"),
            objective="autonomous work",
            resource_refs=[],
            product_profile_ref=profile.ref,
        ),
        OWNER,
    )


def _append(runtime, collet_id, event="task.started", *, actor=BOT, **overrides):
    _, _, repo, _, generic, _ = runtime
    record = repo.get_current_status(collet_id)
    values = dict(
        expected_version=record.collet.version,
        event_kind=event,
        primitive_type="Task",
        actor_ref=actor.actor,
        parent_event=None,
        visibility="public",
        payload={},
    )
    return generic.append_event(
        collet_id, EventAppendCommand(**(values | overrides)), actor
    )


def _unchanged(runtime, collet_id, attempt, error=ContextCollabValidationError):
    repo = runtime[2]
    before = repo.get_current_status(collet_id)
    with pytest.raises(error):
        attempt()
    after = repo.get_current_status(collet_id)
    assert after.collet == before.collet
    assert after.task == before.task
    assert after.events == before.events
    assert after.product_instance == before.product_instance


def test_both_entrypoints_keep_roles_sequence_acceptance_and_cas(runtime):
    profile, _, repo, _, _, product = runtime
    created = _create(runtime)
    cid = created.collet_id
    command = TaskAdvanceCommand(cid, artifacts=["artifact-1"])
    for attempt in (
        lambda: product.submit_task(command, BOT),
        lambda: _append(
            runtime, cid, "task.submitted", payload={"artifacts": ["artifact-1"]}
        ),
        lambda: product.start_task(TaskAdvanceCommand(cid), OWNER),
        lambda: _append(runtime, cid, actor=OWNER),
        lambda: _append(
            runtime, cid, actor=ActorContext(PrincipalRef("bot", "other"), "a2a")
        ),
    ):
        _unchanged(runtime, cid, attempt)
    started = product.start_task(TaskAdvanceCommand(cid), BOT)
    assert started.status == "in_progress"
    for attempt in (
        lambda: product.report_progress(TaskAdvanceCommand(cid, percent=20), OWNER),
        lambda: _append(
            runtime, cid, "task.progress.updated", actor=OWNER, payload={"percent": 20}
        ),
        lambda: product.submit_task(TaskAdvanceCommand(cid), BOT),  # artifacts required
        lambda: _append(runtime, cid, "task.submitted"),
    ):
        _unchanged(runtime, cid, attempt)
    _unchanged(
        runtime,
        cid,
        lambda: _append(runtime, cid, expected_version=1),
        ContextCollabVersionConflict,
    )
    progressed = _append(
        runtime, cid, "task.progress.updated", payload={"percent": 100}
    )
    assert progressed.current_status == "in_progress"  # Not completion.
    report = TaskExecutionReportCommand(
        "report_progress",
        TaskAdvanceCommand(cid, percent=50),
        started.context_version,
        profile.ref,
        repo.get_current_status(cid).product_instance.method_binding_refs[0],
    )
    _unchanged(
        runtime,
        cid,
        lambda: product.report_execution(report, BOT),
        ContextCollabVersionConflict,
    )
    submitted = product.submit_task(command, BOT)
    assert submitted.status == "reviewing"
    _unchanged(runtime, cid, lambda: product.accept_task(command, BOT))
    _unchanged(runtime, cid, lambda: _append(runtime, cid, "task.accepted"))
    assert product.reject_task(command, OWNER).status == "in_progress"
    assert (
        _append(
            runtime, cid, "task.submitted", payload={"artifacts": ["artifact-2"]}
        ).current_status
        == "reviewing"
    )
    assert _append(runtime, cid, "task.accepted", actor=OWNER).current_status == "done"
    for attempt in (
        lambda: product.start_task(command, BOT),
        lambda: _append(runtime, cid),
    ):
        _unchanged(runtime, cid, attempt)


@pytest.mark.parametrize(
    "overrides",
    [
        {"primitive_type": "Message"},
        {"visibility": "private"},
        {"payload": {"percent": True}},
        {"payload": {"percent": 101}},
        {"event": "task.completed"},
        {
            "event": "user_interaction.posted",
            "primitive_type": "Message",
            "payload": {"content": "x", "percent": 10},
        },
    ],
)
def test_no_workflow_is_not_a_generic_write_bypass(runtime, overrides):
    cid = _create(runtime).collet_id
    _unchanged(runtime, cid, lambda: _append(runtime, cid, **overrides))


def test_product_creation_message_authority_and_cancellation(runtime):
    profile, _, repo, _, _, product = runtime
    created = product.publish_task(
        PublishTaskCommand(
            "deliver", "body", SPACE, "worker", product_profile_ref=profile.ref
        ),
        OWNER,
    )
    record = repo.get_current_status(created.collet_id)
    assert record.collet.version == 2 and record.task.version == 1
    assert record.task.status == "created"
    assert record.task.completion_conditions
    assert record.product_instance.profile_ref == profile.ref
    assert (
        product.cancel_task(TaskAdvanceCommand(created.collet_id), OWNER).status
        == "cancelled"
    )
    _unchanged(runtime, created.collet_id, lambda: _append(runtime, created.collet_id))


def test_pinned_refs_roundtrip_and_recovery_do_not_renegotiate(
    runtime, db, monkeypatch
):
    profile, catalog, repo, _, _, product = runtime
    created = _create(runtime)
    before = repo.get_current_status(created.collet_id)
    instance = before.product_instance
    raw = instance.to_dict()
    assert ProductInstance.from_dict(raw) == instance
    if profile.workflow is None:
        assert raw["domain_ref"] is None and raw["workflow_ref"] is None
        assert instance.task_contract_ref == STANDARD_TASK_CONTRACT_REF
    else:
        assert "task_contract_ref" not in raw  # Historical payload shape unchanged.

    def forbidden():
        raise AssertionError("historical reads must not renegotiate")

    monkeypatch.setattr(catalog, "capabilities", forbidden)
    assert repo.get_current_status(created.collet_id).product_instance == instance
    store = InMemoryMethodStore()
    binding = ensure_top_level_binding(
        store,
        current_axes(),
        created.collet_id,
        repo,
        OWNER,
        now="2026-11-16T00:00:00+00:00",
        catalog=catalog,
    )
    assert (binding.method_binding_id,) == instance.method_binding_refs
    assert binding.role_binding_refs == instance.role_binding_refs
    assert (
        product.start_task(TaskAdvanceCommand(created.collet_id), BOT).status
        == "in_progress"
    )
    assert repo.get_current_status(created.collet_id).product_instance.to_dict() == raw
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=created.collet_id)
            .one()
        )
        stored = json.loads(row.creation_facts)
    assert stored["product_instance"] == raw


@pytest.mark.parametrize(
    "corruption",
    ["domain_ref", "workflow_ref", "task_contract_ref", "missing_task_contract"],
)
def test_omitted_profile_metadata_is_validated_not_guessed(db, corruption):
    profile = _profile()
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    generic = ContextCollabService(repo, InMemoryMethodStore(), product_catalog=catalog)
    runtime = (profile, catalog, repo, None, generic, None)
    created = _create(runtime)
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabColletModel)
            .filter_by(collab_context_id=created.collet_id)
            .one()
        )
        data = json.loads(row.creation_facts)
        raw = data["product_instance"]
        if corruption == "missing_task_contract":
            del raw["task_contract_ref"]
        else:
            raw[corruption] = {"profile_id": "wrong", "version": 999}
        row.creation_facts = json.dumps(data)
    with pytest.raises(ContextCollabValidationError):
        repo.get_current_status(created.collet_id)


def test_l1_requirement_is_not_silently_downgraded():
    profile = create_product_profile(TASK_COMMISSION_PROFILE)
    result = negotiate(profile, ProductCatalog((profile,)).capabilities())
    assert not result.accepted and "layer:L1" in result.missing_capabilities


@pytest.mark.parametrize(
    "case", ["legacy_registry_changed", "versioned_machine_missing"]
)
def test_standard_task_uses_only_exact_machine_version(db, monkeypatch, case):
    from agentclaw.community.core.task.context_collab.g4_contracts_consistency import (
        product_contract,
        workflow_resolution,
    )

    profile = _profile()
    catalog = ProductCatalog((profile,))
    repo = ContextCollabRepository(db, product_catalog=catalog)
    generic = ContextCollabService(repo, InMemoryMethodStore(), product_catalog=catalog)
    runtime = (profile, catalog, repo, None, generic, None)
    cid = _create(runtime).collet_id
    if case == "legacy_registry_changed":
        monkeypatch.setitem(
            workflow_resolution.MACHINE_REGISTRY, "task_commission_goal", None
        )
        assert _append(runtime, cid).current_status == "in_progress"
    else:
        monkeypatch.setattr(product_contract, "VERSIONED_MACHINE_REGISTRY", {})
        monkeypatch.setattr(workflow_resolution, "VERSIONED_MACHINE_REGISTRY", {})
        _unchanged(runtime, cid, lambda: _append(runtime, cid))


@pytest.mark.parametrize("case", ["workflow_mismatch", "executor_missing"])
def test_optional_profiles_still_validate_creation_before_persistence(
    runtime, db, case
):
    profile, _, _, _, generic, _ = runtime
    workflow_kind = profile.workflow.ref.profile_id if profile.workflow else None
    command = ColletCreateCommand(
        space=SPACE,
        principal_ref=OWNER.actor,
        product_kind=profile.ref.profile_id,
        workflow_kind="wrong" if case == "workflow_mismatch" else workflow_kind,
        method_kind=profile.composition.method_kind,
        executor_ref=None if case == "executor_missing" else AgentRef("bot", "worker"),
        objective="deliver",
        resource_refs=[],
        product_profile_ref=profile.ref,
    )
    with pytest.raises(ContextCollabValidationError):
        generic.create_collet(command, OWNER)
    with db.orm_session() as session:
        assert session.query(ContextCollabColletModel).count() == 0


@pytest.mark.parametrize("case", ["profile_version", "product_id", "method_binding"])
def test_reports_remain_correlated_with_the_exact_persisted_selection(runtime, case):
    profile, _, repo, _, _, product = runtime
    cid = _create(runtime).collet_id
    instance = repo.get_current_status(cid).product_instance
    report = TaskExecutionReportCommand(
        action="start_task",
        task=TaskAdvanceCommand(cid),
        expected_version=1,
        product_profile_ref=(
            VersionedProfileRef("other", profile.ref.version)
            if case == "product_id"
            else VersionedProfileRef(profile.ref.profile_id, profile.ref.version + 1)
            if case == "profile_version"
            else profile.ref
        ),
        method_binding_ref=(
            "wrong" if case == "method_binding" else instance.method_binding_refs[0]
        ),
    )
    _unchanged(runtime, cid, lambda: product.report_execution(report, BOT))
