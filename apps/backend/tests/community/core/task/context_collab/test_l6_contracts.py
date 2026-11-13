"""Machine-checkable L6 definitions and exact-match compatibility contracts."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    VERSIONED_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.workflow_resolution import (
    resolve_workflow_status,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    prepare_product_spec,
    product_definition,
    validate_product_spec,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodRequirement,
    VersionedProfileRef,
    negotiate,
)
from tests.community.core.task.context_collab.product_catalog import (
    PRODUCT_CATALOG,
    PRODUCT_PROFILES,
)


def _spec(**overrides):
    fields = dict(
        space=SpaceRef("space", "test"),
        principal_ref=PrincipalRef("human", "owner"),
        method_kind="Open",
        executor_ref=AgentRef("bot", "worker"),
        objective="deliver",
        resource_refs=(),
        product_kind="task_commission",
        workflow_kind="task_commission_goal",
    )
    return ContextSpec(**(fields | overrides))


@pytest.mark.parametrize("profile", PRODUCT_PROFILES, ids=lambda p: p.ref.profile_id)
def test_builtins_have_exact_dependencies_and_reuse_g4(profile):
    selected = negotiate(profile, PRODUCT_CATALOG.capabilities())
    assert selected.accepted
    assert selected.selected_profile == profile.ref
    assert selected.missing_capabilities == ()
    if profile.workflow.state_machine_ref is not None:
        ref = profile.workflow.state_machine_ref
        machine = VERSIONED_MACHINE_REGISTRY[(ref.profile_id, ref.version)]
        assert set(profile.workflow.completion_states) <= {
            state.name for state in machine.states
        }
        assert set(profile.workflow.compensation_events) <= {
            rule.event_name for rule in machine.transitions
        }
    assert all(
        mapping.authority.startswith(("L2.", "L3.", "L4.", "L5."))
        for mapping in profile.domain.mappings
    )


@pytest.mark.parametrize(
    "field", ["profiles", "methods", "accs_revisions", "layers", "governance"]
)
def test_required_capability_missing_is_rejected_without_fallback(field):
    profile = PRODUCT_CATALOG.get("task_commission", 1)
    result = negotiate(profile, replace(PRODUCT_CATALOG.capabilities(), **{field: ()}))
    assert not result.accepted
    assert result.missing_capabilities
    assert result.selected_profile == VersionedProfileRef("task_commission", 1)
    assert "downgrade forbidden" in result.reason


def test_required_and_optional_extensions_and_methods_are_distinct():
    profile = PRODUCT_CATALOG.get("task_commission", 1)
    profile = replace(
        profile,
        compatibility=replace(
            profile.compatibility,
            required_extensions=("evidence/1",),
            optional_extensions=("trace/1",),
        ),
        composition=replace(
            profile.composition, optional_methods=(MethodRequirement("Swarm", "999"),)
        ),
    )
    offer = PRODUCT_CATALOG.capabilities()
    result = negotiate(profile, offer)
    assert not result.accepted
    assert result.missing_capabilities == ("extension:evidence/1",)
    assert result.omitted_optional_capabilities == (
        "method:Swarm@999",
        "extension:trace/1",
    )
    assert negotiate(profile, replace(offer, extensions=("evidence/1",))).accepted


def test_method_version_never_silently_uses_installed_newer_version():
    profile = PRODUCT_CATALOG.get("task_commission", 1)
    result = negotiate(
        profile,
        replace(
            PRODUCT_CATALOG.capabilities(),
            methods=(MethodRequirement("Open", "0.3"),),
        ),
    )
    assert result.missing_capabilities == ("method:Open@0.2",)


@pytest.mark.parametrize("version", [0, -1, True, "1"])
def test_bad_profile_version_rejected(version):
    with pytest.raises(ContextCollabValidationError, match="positive integer"):
        VersionedProfileRef("product", version)
    with pytest.raises(ContextCollabValidationError):
        product_definition(
            _spec(product_profile_version=version), catalog=PRODUCT_CATALOG
        )


def test_code_owned_catalog_does_not_require_extending_enum():
    original = PRODUCT_CATALOG.get("task_commission", 1)
    custom = replace(original, ref=VersionedProfileRef("custom_product", 3))
    catalog = ProductCatalog((original, custom))
    assert catalog.get("custom_product", 3) == custom
    assert negotiate(custom, catalog.capabilities()).accepted
    with pytest.raises(ContextCollabValidationError, match="unknown"):
        catalog.get("custom_product", 1)
    with pytest.raises(ContextCollabValidationError, match="duplicate"):
        ProductCatalog((custom, custom))
    with pytest.raises(ContextCollabValidationError, match="disagree"):
        ProductCatalog(
            (
                replace(
                    custom,
                    composition=replace(custom.composition, method_kind="Single"),
                ),
            )
        )


def test_unversioned_spec_maps_only_to_v1_and_pinned_role_facts_are_checked():
    spec = _spec()
    assert product_definition(spec, catalog=PRODUCT_CATALOG).ref.version == 1
    pinned = prepare_product_spec(spec, catalog=PRODUCT_CATALOG)
    assert pinned.product_profile_version == pinned.workflow_profile_version == 1
    assert len(pinned.participants) == len(pinned.role_bindings) == 2
    assert validate_product_spec(pinned, catalog=PRODUCT_CATALOG) == product_definition(
        spec, catalog=PRODUCT_CATALOG
    )
    with pytest.raises(ContextCollabValidationError, match="RoleBinding"):
        validate_product_spec(
            replace(pinned, role_bindings=()), catalog=PRODUCT_CATALOG
        )
    with pytest.raises(ContextCollabValidationError, match="unknown"):
        product_definition(
            replace(pinned, product_profile_version=2), catalog=PRODUCT_CATALOG
        )
    with pytest.raises(ContextCollabValidationError, match="version"):
        product_definition(
            replace(pinned, workflow_profile_version=2), catalog=PRODUCT_CATALOG
        )
    with pytest.raises(ContextCollabValidationError, match="requires product_kind"):
        product_definition(replace(pinned, product_kind=None), catalog=PRODUCT_CATALOG)


def test_versioned_workflow_lookup_does_not_use_name_alias(monkeypatch):
    from agentclaw.community.core.task.context_collab.g4_contracts_consistency import (
        state_machine,
    )

    spec = _spec()
    from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
        Task,
    )

    task = Task(
        task_id="task_versioned",
        collet_ref="collet_versioned",
        objective=spec.objective,
        status="created",
        version=1,
        principal_ref=spec.principal_ref,
        executor_ref=spec.executor_ref,
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_created",
        last_event_ref="evt_created",
    )
    monkeypatch.setitem(state_machine.MACHINE_REGISTRY, "task_commission_goal", None)
    kwargs = dict(
        workflow_kind=spec.workflow_kind,
        task=task,
        event_kind="task.started",
        payload={},
        actor=spec.principal_ref,
    )
    assert resolve_workflow_status(**kwargs, workflow_version=1) == "in_progress"
    with pytest.raises(ContextCollabValidationError, match="unavailable"):
        resolve_workflow_status(**kwargs, workflow_version=2)


@pytest.mark.parametrize(
    "principal, agent",
    [
        (None, None),
        (
            {"principal_type": "human", "principal_id": "owner"},
            {"agent_type": "bot", "agent_id": "worker"},
        ),
    ],
)
def test_participant_requires_exactly_one_identity(principal, agent):
    with pytest.raises(ContextCollabValidationError, match="exactly one"):
        Participant.from_dict(
            dict(participant_id="p", principal_ref=principal, agent_ref=agent)
        )


def test_l6_contracts_do_not_import_runtime_or_transport():
    import ast
    from pathlib import Path

    from agentclaw.community.core.task.context_collab import l6_products

    root = "agentclaw.community.core.task.context_collab."
    allowed = (
        "errors",
        "l2_agent_network",
        "l3_spaces_resources",
        "l4_context_primitives",
        "l5_methods",
        "l6_products",
    )
    for path in Path(l6_products.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                if module.startswith("agentclaw."):
                    assert module.startswith(tuple(root + item for item in allowed)), (
                        path.name,
                        module,
                    )
                assert module.split(".")[0] not in {
                    "fastapi",
                    "sqlalchemy",
                    "requests",
                    "httpx",
                    "injector",
                }, (path.name, module)


def test_workflow_role_requirement_must_name_a_declared_role():
    profile = PRODUCT_CATALOG.get("task_commission", 1)
    action = replace(
        profile.workflow.action("report_progress"), required_role="unknown"
    )
    workflow = replace(profile.workflow, actions=(action,))
    with pytest.raises(ContextCollabValidationError, match="undeclared scenario role"):
        ProductCatalog((replace(profile, workflow=workflow),))
