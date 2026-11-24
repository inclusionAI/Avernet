"""The composition root selects only the required Swarm declaration's policy."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
    TaskAction,
    VersionedProfileRef,
    WorkflowProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.di.modules.swarm_runtime import (
    ProductSwarmRuntimeResolver,
    SwarmPlanningCapabilities,
)


def _resolver(composition, services, **profile_changes):
    profile = create_product_profile(
        replace(TASK_COMMISSION_PROFILE, composition=composition, **profile_changes)
    )
    principal = PrincipalRef("human", "swarm_owner")
    repository = Mock()
    repository.get_current_status.return_value = SimpleNamespace(
        task=SimpleNamespace(
            principal_ref=principal,
            executor_ref=AgentRef("bot", "root_executor"),
            executor_refs=(AgentRef("bot", "root_executor"),),
            status="created",
        ),
        events=(),
        product_instance=SimpleNamespace(profile_ref=profile.ref),
    )
    resolver = ProductSwarmRuntimeResolver(
        repository, ProductCatalog((profile,)), SwarmPlanningCapabilities(services)
    )
    return resolver, repository, principal


def test_resolver_uses_exact_swarm_local_policy_and_injected_capability():
    selected, other = Mock(), Mock()
    composition = MethodCompositionTemplate(
        (MethodTemplate(get_profile("Swarm", "0.2"), SwarmMethodPolicy("plan/2")),)
    )
    resolver, repository, principal = _resolver(
        composition, {"plan/1": other, "plan/2": selected}
    )
    config = resolver.resolve("collet_test")
    assert config.planner is selected
    assert config.actor.actor == principal
    assert config.actor.source == "swarm_driver"
    assert config.start_actor.actor == PrincipalRef("bot", "root_executor")
    assert config.start_actor.source == "swarm_driver"
    repository.get_current_status.assert_called_once_with("collet_test")
    assert not selected.mock_calls and not other.mock_calls


@pytest.mark.parametrize("mode", ["omitted", "unknown", "optional_only"])
def test_resolver_does_not_infer_or_fallback_to_a_planner(mode):
    policy = None if mode == "omitted" else SwarmMethodPolicy("unavailable/1")
    declaration = MethodTemplate(get_profile("Swarm", "0.2"), policy)
    composition = (
        MethodCompositionTemplate(
            (MethodTemplate(get_profile("Open", "0.2")),),
            optional_methods=(declaration,),
        )
        if mode == "optional_only"
        else MethodCompositionTemplate((declaration,))
    )
    installed = Mock()
    resolver, _, _ = _resolver(composition, {"some_other_plan/1": installed})
    with pytest.raises(
        ContextCollabValidationError,
        match="Swarm.*policy|installed Swarm plan capability",
    ):
        resolver.resolve("collet_test")
    assert not installed.mock_calls


@pytest.mark.parametrize("missing", ["record", "task", "product_instance"])
def test_resolver_requires_installed_product_and_work(missing):
    composition = MethodCompositionTemplate(
        (MethodTemplate(get_profile("Swarm", "0.2"), SwarmMethodPolicy("plan/1")),)
    )
    resolver, repository, _ = _resolver(composition, {})
    if missing == "record":
        repository.get_current_status.return_value = None
    else:
        setattr(repository.get_current_status.return_value, missing, None)
    with pytest.raises(
        ContextCollabValidationError, match="installed product and root Work"
    ):
        resolver.resolve("collet_test")


@pytest.mark.parametrize("role", ["responsible_user", None, "undeclared"])
def test_workflow_start_uses_principal_when_contract_permits(role):
    composition = MethodCompositionTemplate(
        (MethodTemplate(get_profile("Swarm", "0.2"), SwarmMethodPolicy("plan/1")),)
    )
    workflow = WorkflowProfile(
        ref=VersionedProfileRef("owner_start", 1),
        state_machine_ref=VersionedProfileRef("task_commission_goal", 1),
        completion_states=("done",),
        compensation_events=("task.cancelled",),
        actions=(
            (TaskAction("start", "task.started", "Task", ("note",), role),)
            if role != "undeclared"
            else ()
        ),
    )
    resolver, _, principal = _resolver(
        composition, {"plan/1": Mock()}, workflow=workflow, task_contract_ref=None
    )
    config = resolver.resolve("collet_test")
    assert config.start_actor.actor == principal
    assert config.actor.actor == principal


@pytest.mark.parametrize("mode", ["missing", "ambiguous", "wrong_role"])
def test_created_root_requires_an_unambiguous_startup_role_holder(mode):
    composition = MethodCompositionTemplate(
        (MethodTemplate(get_profile("Swarm", "0.2"), SwarmMethodPolicy("plan/1")),)
    )
    resolver, repository, _ = _resolver(composition, {"plan/1": Mock()})
    record = repository.get_current_status.return_value
    if mode == "wrong_role":
        record.events = (
            SimpleNamespace(
                event_kind="collet.participants_bound",
                payload={
                    "participation": {
                        "participants": [
                            {
                                "participant_id": "worker",
                                "principal_ref": None,
                                "agent_ref": record.task.executor_ref.to_dict(),
                            }
                        ],
                        "role_bindings": [
                            {"role": "coding", "participant_id": "worker"}
                        ],
                    }
                },
            ),
        )
    else:
        record.task.executor_refs = (
            ()
            if mode == "missing"
            else (AgentRef("bot", "one"), AgentRef("bot", "two"))
        )
        record.task.executor_ref = None
        if mode == "ambiguous":
            record.events = (
                SimpleNamespace(
                    event_kind="collet.participants_bound",
                    payload={
                        "participation": {
                            "participants": [
                                {
                                    "participant_id": ref.agent_id,
                                    "principal_ref": None,
                                    "agent_ref": ref.to_dict(),
                                }
                                for ref in record.task.executor_refs
                            ],
                            "role_bindings": [
                                {
                                    "role": "selected-agent",
                                    "participant_id": ref.agent_id,
                                }
                                for ref in record.task.executor_refs
                            ],
                        }
                    },
                ),
            )
    with pytest.raises(ContextCollabValidationError, match="startup role holder"):
        resolver.resolve("collet_test")


def test_running_root_does_not_require_a_new_startup_identity():
    composition = MethodCompositionTemplate(
        (MethodTemplate(get_profile("Swarm", "0.2"), SwarmMethodPolicy("plan/1")),)
    )
    resolver, repository, principal = _resolver(composition, {"plan/1": Mock()})
    record = repository.get_current_status.return_value
    record.task.status = "in_progress"
    record.task.executor_ref = None
    record.task.executor_refs = ()
    assert resolver.resolve("collet_test").actor.actor == principal
