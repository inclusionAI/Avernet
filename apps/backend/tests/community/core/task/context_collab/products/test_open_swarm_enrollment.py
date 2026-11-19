"""Deferred Open roles, safe defaults and SQL-backed pre-Work enrollment fences."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from injector import AssistedBuilder

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    EventAppendCommand,
    TaskCreateCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    ADMITTED_BINDING_EVENT,
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenDecisionOpCommand,
    OpenOpportunityCommand,
    OpenScreenCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver import (
    SwarmDriver,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ScenarioRole,
)
from agentclaw.community.core.task.context_collab.l6_products.open_enrollment import (
    AdmittedAgent,
    OpenEnrollmentCommand,
    ProductOpenEnrollmentServiceProtocol,
    scenario_role_ref,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.open_enrollment import (
    ProductOpenEnrollmentService,
)
from tests.community.core.task.context_collab.products import (
    test_task_commission_end_to_end_open_swarm as journey,
)
from tests.community.core.task.context_collab.products.test_task_commission_end_to_end_open_swarm import (
    deploy_open_swarm as deploy_open_swarm,
)


@pytest.fixture
def runtime(deploy_open_swarm, tmp_path, request, monkeypatch):
    if getattr(request, "param", False):
        profile = journey._create_product_profile()
        declared = replace(
            profile,
            scenario_roles=profile.scenario_roles
            + tuple(
                ScenarioRole(role, role, "open_admission", True, (role,))
                for role in ("coding", "design")
            ),
        )
        monkeypatch.setattr(journey, "_create_product_profile", lambda: declared)
    profile, injector, work, open_ops, store, axes, cid, oid, sid, refs, _ = (
        journey.publish_todo_task(deploy_open_swarm, tmp_path)
    )
    published = open_ops.publish_opportunity(
        OpenOpportunityCommand(
            cid,
            ParticipationType.COLLET_PARTICIPANT,
            requirement_refs=list(refs.values()),
            capacity=7,
        )
    )
    return SimpleNamespace(
        profile=profile,
        injector=injector,
        work=work,
        open=open_ops,
        store=store,
        axes=axes,
        cid=cid,
        oid=oid,
        sid=sid,
        refs=refs,
        opportunity=published.object_id,
    )


def _record(runtime):
    return journey._record(runtime.injector, runtime.cid)


def _application(
    runtime,
    role="coding",
    agent=AgentRef("bot", "unknown_until_runtime"),
    *,
    admit=True,
    ref=None,
):
    result = journey.SimulatedApplicantBot(
        agent, role, ("capability://ai-coding/coding",)
    ).discover_and_apply(runtime.open, runtime.opportunity, ref or runtime.refs[role])
    if admit:
        runtime.open.screen(
            OpenScreenCommand(result.object_id, journey.OWNER, runtime.cid)
        )
        runtime.open.admit(
            OpenAdmitCommand(result.object_id, journey.OWNER, runtime.cid)
        )
    return AdmittedAgent(result.object_id, agent)


def _command(runtime, admission):
    return OpenEnrollmentCommand(
        runtime.cid, _record(runtime).collet.version, (admission,), admission.agent_ref
    )


def _bind(runtime, admission):
    return runtime.injector.get(ProductOpenEnrollmentServiceProtocol).bind(
        _command(runtime, admission), journey.OWNER
    )


def _create_task(runtime):
    return runtime.work.create_task(
        runtime.cid,
        TaskCreateCommand(
            _record(runtime).collet.version,
            journey.OWNER.actor,
            objective=journey.OBJECTIVE,
        ),
        journey.OWNER,
    )


def _executor(admission):
    return ActorContext(
        PrincipalRef(admission.agent_ref.agent_type, admission.agent_ref.agent_id),
        "local_agent",
    )


def test_profile_has_no_predeclared_workers_and_instances_start_taskless(runtime):
    assert {
        (r.l4_role, r.participant_source) for r in runtime.profile.scenario_roles
    } == {("responsible_user", "principal_ref"), ("selected-agent", "open_admission")}
    assert all(r.agent_ref is None for r in runtime.profile.scenario_roles)
    assert not any(r.name in journey.ROLES for r in runtime.profile.scenario_roles)
    assert _record(runtime).task is None
    assert len(_record(runtime).participants) == 1


def test_enrollment_reads_real_admission_and_keeps_initial_instance_pinned(runtime):
    admission = _application(runtime)
    before = _record(runtime)
    added = _bind(runtime, admission)
    reloaded = _record(runtime)
    assert added.participants[0].agent_ref == admission.agent_ref
    assert {r.role for r in added.role_bindings} == {"coding", "selected-agent"}
    assert reloaded.task is None and not reloaded.tasks
    assert reloaded.product_instance == before.product_instance
    assert reloaded.collet.version == before.collet.version + 1
    assert len(reloaded.participants) == 2
    assert (
        current_participation(reloaded.events).role_bindings == reloaded.role_bindings
    )
    _create_task(runtime)
    assert _record(runtime).task.status == "created"
    assert _record(runtime).task.executor_refs == (admission.agent_ref,)


@pytest.mark.parametrize(
    "state", ["pending", "rejected", "revoked", "wrong_agent", "foreign_application"]
)
def test_non_admitted_or_mismatched_subject_never_materializes(runtime, state):
    admission = _application(runtime, admit=state not in {"pending", "rejected"})
    if state == "rejected":
        runtime.open.screen(
            OpenScreenCommand(admission.application_ref, journey.OWNER, runtime.cid)
        )
        runtime.open.reject(
            runtime.cid,
            runtime.oid,
            admission.application_ref,
            OpenDecisionOpCommand("not suitable"),
            journey.OWNER,
        )
    if state == "revoked":
        runtime.open.revoke_admission(
            runtime.cid,
            runtime.oid,
            admission.application_ref,
            OpenDecisionOpCommand("revoked"),
            journey.OWNER,
        )
    if state == "wrong_agent":
        admission = replace(admission, agent_ref=AgentRef("bot", "impostor"))
    if state == "foreign_application":
        admission = replace(admission, application_ref="foreign_application")
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError, match="ADMITTED"):
        _bind(runtime, admission)
    after = _record(runtime)
    assert after.collet.version == before.collet.version
    assert after.events == before.events and len(after.participants) == 1


@pytest.mark.parametrize(
    "role", ["responsible_user", "selected-agent", "foreign_profile"]
)
def test_published_requirement_cannot_mint_protected_or_foreign_roles(runtime, role):
    ref = (
        scenario_role_ref(runtime.profile.ref, role)
        if role != "foreign_profile"
        else "profiles/other@1/roles/coding"
    )
    # Publishing the requirement is not sufficient to grant a product role.
    opportunity = runtime.store.get_opportunity(
        runtime.axes, runtime.opportunity, runtime.oid, runtime.cid
    )
    runtime.store.update_opportunity_state(
        runtime.axes, replace(opportunity, requirement_refs=(ref,)), runtime.cid
    )
    admission = _application(runtime, ref=ref)
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError, match="protected|pinned profile"):
        _bind(runtime, admission)
    assert _record(runtime).events == before.events


def test_enrollment_rejects_stale_owner_and_duplicate_calls(runtime):
    admission = _application(runtime)
    service = runtime.injector.get(ProductOpenEnrollmentServiceProtocol)
    command = _command(runtime, admission)
    with pytest.raises(ContextCollabVersionConflict):
        service.bind(
            replace(command, expected_version=command.expected_version - 1),
            journey.OWNER,
        )
    with pytest.raises(ContextCollabValidationError, match="initiator"):
        service.bind(command, _executor(admission))
    service.bind(command, journey.OWNER)
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError, match="pre-Work"):
        service.bind(_command(runtime, admission), journey.OWNER)
    assert _record(runtime).events == before.events


def test_post_task_enrollment_is_not_supported(runtime):
    admission = _application(runtime)
    _create_task(runtime)
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError, match="pre-Work"):
        _bind(runtime, admission)
    assert _record(runtime).events == before.events


def test_generic_append_cannot_forge_enrollment(runtime):
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError):
        runtime.work.append_event(
            runtime.cid,
            EventAppendCommand(
                before.collet.version,
                ADMITTED_BINDING_EVENT,
                "Collet",
                journey.OWNER.actor,
                None,
                "public",
                {"participation": {"participants": [], "role_bindings": []}},
            ),
            journey.OWNER,
        )
    assert _record(runtime).events == before.events


@pytest.mark.parametrize("failure", ["authorization", "persistence"])
def test_authorization_and_write_failure_propagate(runtime, failure):
    admission = _application(runtime)
    before = _record(runtime)
    real = runtime.injector.get(ProductOpenEnrollmentServiceProtocol)
    repository = Mock(wraps=real.repository)
    authorizer = Mock(wraps=real.authorizer)
    if failure == "authorization":
        authorizer.check_open_op.side_effect = RuntimeError("denied")
    else:
        repository.append_event.side_effect = RuntimeError("write failed")
    service = ProductOpenEnrollmentService(
        repository, real.store, real.catalog, authorizer
    )
    with pytest.raises(RuntimeError, match="denied|write failed"):
        service.bind(_command(runtime, admission), journey.OWNER)
    assert _record(runtime).events == before.events
    if failure == "authorization":
        repository.append_event.assert_not_called()


@pytest.mark.parametrize("running", [False, True])
def test_revocation_blocks_driver_and_root_work_even_after_role_binding(
    runtime, running
):
    admission = _application(runtime)
    _bind(runtime, admission)
    _create_task(runtime)
    driver = runtime.injector.get(AssistedBuilder[SwarmDriver]).build(
        collet_id=runtime.cid
    )
    if running:
        driver.wake()
        assert _record(runtime).task.status == "in_progress"
    runtime.open.revoke_admission(
        runtime.cid,
        runtime.oid,
        admission.application_ref,
        OpenDecisionOpCommand("revoked"),
        journey.OWNER,
    )
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError, match="ADMITTED"):
        driver.step() if running else driver.wake()
    with pytest.raises(ContextCollabValidationError, match="ADMITTED"):
        if running:
            journey._append(
                runtime.injector,
                runtime.cid,
                "task.submitted",
                _executor(admission),
                artifacts=[],
            )
        else:
            runtime.work.start(
                runtime.cid,
                WorkStartCommand(before.collet.version),
                _executor(admission),
            )
    assert _record(runtime).events == before.events


@pytest.mark.parametrize("runtime", [True], indirect=True)
@pytest.mark.parametrize("complete", [False, True])
def test_explicit_deferred_roles_still_require_full_coverage(runtime, complete):
    coding = _application(runtime)
    design = _application(runtime, "design", AgentRef("bot", "late_designer"))
    command = OpenEnrollmentCommand(
        runtime.cid,
        _record(runtime).collet.version,
        (coding, design) if complete else (coding,),
        coding.agent_ref,
    )
    service = runtime.injector.get(ProductOpenEnrollmentServiceProtocol)
    before = _record(runtime)
    if not complete:
        with pytest.raises(
            ContextCollabValidationError, match="required deferred roles"
        ):
            service.bind(command, journey.OWNER)
        assert _record(runtime).events == before.events
    else:
        added = service.bind(command, journey.OWNER)
        assert {r.role for r in added.role_bindings} == {
            "coding",
            "design",
            "selected-agent",
        }
        assert _record(runtime).task is None


def test_inactive_open_binding_blocks_previously_admitted_enrollment(runtime):
    admission = _application(runtime)
    binding = runtime.store.get_binding(runtime.axes, runtime.oid, runtime.cid)
    runtime.store.update_binding(
        runtime.axes, replace(binding, state=BindingState.SUSPENDED), runtime.cid
    )
    before = _record(runtime)
    with pytest.raises(ContextCollabValidationError, match="ACTIVE Open"):
        _bind(runtime, admission)
    assert _record(runtime).events == before.events


def test_same_profile_runs_with_entirely_different_applicants(
    deploy_open_swarm, tmp_path, monkeypatch
):
    profile = journey._create_product_profile()
    monkeypatch.setattr(
        journey,
        "AGENTS",
        tuple(AgentRef("bot", f"late_applicant_{i}") for i in range(5)),
    )
    assert journey._create_product_profile() == profile
    journey.test_task_commission_end_to_end_open_swarm(deploy_open_swarm, tmp_path)
