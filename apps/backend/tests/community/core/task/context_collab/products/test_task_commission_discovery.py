"""Product discovery keeps exact task/binding policy after unscoped L5 discovery."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    OpenOpportunity,
    OpportunityState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_payload,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    BotSelfApplicationAdmission,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.di.modules.task.task_commission_self_application import (
    TaskCommissionSelfApplicationAdapter,
)

BOT_ID = "bot_discoverer"
OWNER = PrincipalRef("human", "owner")
# Exercise the same explicit Bot-self-application selection as the direct L5
# journey, without changing the installed legacy designated-admission profile.
PROFILE = create_product_profile(
    replace(
        TASK_COMMISSION_PROFILE,
        composition=replace(
            TASK_COMMISSION_PROFILE.composition,
            required_methods=(
                replace(
                    TASK_COMMISSION_PROFILE.composition.required_methods[0],
                    policy=OpenMethodPolicy(
                        publication=OpenPublicationProfile(
                            admission=BotSelfApplicationAdmission(
                                applicant_role="assignee",
                                accountable_role="owner",
                                participation_type=ParticipationType.COLLET_PARTICIPANT,
                                capacity=1,
                            )
                        ),
                    ),
                ),
                *TASK_COMMISSION_PROFILE.composition.required_methods[1:],
            ),
        ),
    )
)
PRODUCT_CATALOG = ProductCatalog((PROFILE,))
PROFILE_REF = PROFILE.ref


def _record(collet_id: str, bot_id: str = BOT_ID) -> ContextStatusRecord:
    executor = AgentRef("bot", bot_id)
    task = Task(
        task_id=f"task_{collet_id}",
        collet_ref=collet_id,
        objective=f"Objective for {collet_id}",
        status="created",
        version=1,
        principal_ref=OWNER,
        executor_refs=(executor,),
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_created",
        last_event_ref="evt_created",
    )
    return ContextStatusRecord(
        collet=Collet(
            collet_id=collet_id,
            initiator_ref=OWNER,
            state=ColletState.ACTIVE,
            scope=SpaceRef("space", "test"),
            version=1,
        ),
        creation_facts=creation_payload(
            objective=task.objective,
            executor_ref=executor,
            resource_refs=(),
            method_kind="Open",
            product_kind="task_commission",
            workflow_kind="task_commission_goal",
            product_profile_ref=PROFILE_REF,
        ),
        task=task,
        tasks=(task,),
        events=[],
    )


def test_product_filters_global_l5_results_and_discovers_only_once():
    records = {
        record.collet.collet_id: record
        for record in (
            _record("first"),
            _record("second"),
            _record("other", "other_bot"),
        )
    }
    repo = Mock(spec=ContextCollabRepositoryProtocol)
    repo.get_current_status.side_effect = records.get
    repo.list_collets_by_product.return_value = [
        (record.collet, record.creation_facts, datetime.now(timezone.utc))
        for record in records.values()
    ]
    store = InMemoryMethodStore()
    opportunities = []
    for collet_id in records:
        binding_id = top_level_binding_id(collet_id)
        store.upsert_binding(
            current_axes(),
            MethodBinding(
                method_binding_id=binding_id,
                collab_context_id=collet_id,
                method_kind="Open",
                method_names=("Open",),
                profile_versions={"Open": "0.2"},
                state=BindingState.ACTIVE,
            ),
        )
        opportunities.append(
            OpenOpportunity(
                opportunity_id=f"opp_{collet_id}",
                method_binding_id=binding_id,
                scope_ref=collet_id,
                participation_type=ParticipationType.COLLET_PARTICIPANT,
                opens_at="2026-11-01T00:00:00+00:00",
                state=OpportunityState.OPEN,
                capacity=1,
                admission_policy_refs=(
                    "task_commission.designated_bot_self_application/1",
                ),
            )
        )
    # Same Collet/policy/type/capacity is insufficient: an opportunity on a
    # different binding must not be relabelled as the product's top-level binding.
    first = opportunities[0]
    unrelated = [
        replace(first, opportunity_id="opp_nested", method_binding_id="mb_nested"),
        replace(first, opportunity_id="opp_foreign_scope", scope_ref="foreign_collet"),
        replace(first, opportunity_id="opp_other_policy", admission_policy_refs=()),
        replace(
            first,
            opportunity_id="opp_other_type",
            participation_type=ParticipationType.GROUP_MEMBER,
        ),
        replace(first, opportunity_id="opp_other_capacity", capacity=2),
    ]
    open_ops = Mock(spec=OpenMethodOpsServiceProtocol)
    open_ops.query_opportunities.return_value = tuple(opportunities + unrelated)
    adapter = TaskCommissionSelfApplicationAdapter(
        open_ops, PRODUCT_CATALOG, store, repo, group_ops=Mock()
    )
    actor_context = ActorContext(PrincipalRef("bot", BOT_ID), "a2a")

    views = adapter.query_opportunities(PROFILE_REF, actor_context)

    assert [
        (view.collab_context_id, view.opportunity_id, view.method_binding_ref)
        for view in views
    ] == [
        ("first", "opp_first", top_level_binding_id("first")),
        ("second", "opp_second", top_level_binding_id("second")),
    ]
    open_ops.query_opportunities.assert_called_once_with(actor_context=actor_context)
    repo.list_collets_by_product.assert_called_once_with("task_commission")
