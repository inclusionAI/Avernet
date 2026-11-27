from dataclasses import replace
import pytest
from injector import Injector
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
    ContextCollabNotFoundError as Missing,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
    AdjustTypeEnum as A,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductConfigurationRow,
    ProductIdentityRow,
    ProductRuntimeRow,
    ProductReceiptRow,
    ProductIntentRow,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.capabilities import (
    UnavailableRolloutExecution,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_profile,
    decode_profile,
)
from agentclaw.community.di.modules.task.product_profile_runtime import (
    ProductProfileRuntimeModule,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

from .helpers import persist_profile

pytestmark = pytest.mark.asyncio


async def test_existing_profile_reconstructs_and_reads_without_running(created):
    h = created
    assert decode_profile(encode_profile(h.profile)) == h.profile
    assert h.collet.status is S.CREATE
    assert not h.calls
    other = SqlProductRuntimeRepository(h.repository.db)
    assert other.get_profile(h.profile.id)[0] == h.profile
    saved = other.get_collet(h.collet.id)
    assert len(saved.rollouts) == 1
    assert len(saved.current().stages) == 2
    assert {b.method_kind for b in saved.bindings} == {"Swarm"}
    assert all(s.session_id == "" for s in saved.current().stages)
    all_values = await h.service.query_collet_rollout(h.collet.id)
    current = await h.service.query_collet_rollout(h.collet.id, False)
    assert len(all_values) == len(current) == 1
    assert current.current_rollout_id == saved.current_rollout_id
    assert current.revision == 1
    sid = saved.current().stages[0].stage_rollout_id
    details = await h.service.query_stage_rollout_details(sid)
    assert not details.bound and details.messages == [] and details.session_id == ""
    h.bot.get_session_messages.assert_not_awaited()


async def test_legacy_collet_creations_are_independent_without_client_request_ids(
    created,
):
    h = created
    another_profile = persist_profile(h, "coding", h.composition, h.stages)
    assert another_profile.id != h.profile.id
    assert another_profile.composition == h.profile.composition
    assert another_profile.stages == h.profile.stages
    another_collet = await h.service.create_product_collet(
        h.profile.id, "build a feature"
    )
    assert another_collet.id != h.collet.id
    assert another_collet.status is S.CREATE
    assert another_collet.profile_id == h.profile.id
    with h.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 2
        assert session.query(ProductRuntimeRow).count() == 2
        receipts = session.query(ProductReceiptRow).all()
        assert len(receipts) == len({r.request_id for r in receipts}) == 4
    assert not h.calls


async def test_repository_receipts_remain_actor_operation_and_content_scoped(runtime):
    h = runtime
    profile = h.repository.create_profile(
        "coding", h.composition, h.stages, context=h.owner, request_id="profile-1"
    )
    collet = h.repository.create_collet(
        profile, "build a feature", context=h.owner, request_id="collet-1"
    )
    assert (
        h.repository.create_profile(
            "coding", h.composition, h.stages, context=h.owner, request_id="profile-1"
        )
        == profile
    )
    assert (
        h.repository.create_collet(
            profile, "build a feature", context=h.owner, request_id="collet-1"
        ).collet
        == collet.collet
    )
    changed = replace(
        h.composition,
        required_methods=(
            replace(
                h.composition.required_methods[0],
                policy=replace(
                    h.composition.required_methods[0].policy, overall_acceptance="owner"
                ),
            ),
        ),
    )
    with pytest.raises(Conflict):
        h.repository.create_profile(
            "coding", changed, h.stages, context=h.owner, request_id="profile-1"
        )
    with pytest.raises(Conflict):
        h.repository.create_collet(
            profile, "changed", context=h.owner, request_id="collet-1"
        )
    with pytest.raises(Conflict):
        h.repository.create_collet(
            profile, "build a feature", context=h.owner, request_id="profile-1"
        )
    with h.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 1
        assert session.query(ProductRuntimeRow).count() == 1
        assert session.query(ProductIdentityRow).count() == 4
        assert session.query(ProductReceiptRow).count() == 2
        assert session.query(ProductIntentRow).count() == 1


async def test_authorization_precedes_persistence_and_provider(created):
    h = created
    outsider = ActorContext(PrincipalRef("human", "outsider"), "test")
    with actor_context_scope(outsider), pytest.raises(Invalid):
        await h.service.create_product_profile("bad", h.stages)
    with actor_context_scope(outsider), pytest.raises(Forbidden):
        await h.service.create_product_collet(h.profile.id, "build a feature")
    with actor_context_scope(outsider), pytest.raises(Forbidden):
        await h.service.query_collet_rollout(h.collet.id)
    saved = h.repository.get_collet(h.collet.id)
    with actor_context_scope(outsider), pytest.raises(Forbidden):
        await h.service.query_stage_rollout_details(
            saved.current().stages[0].stage_rollout_id
        )
    assert not h.calls
    h.bot.get_session_messages.assert_not_awaited()


async def test_profile_requires_real_capabilities(runtime):
    h = runtime
    h.service._execution = (
        UnavailableRolloutExecution()
    )  # capability binding, not mocking tested behavior
    with pytest.raises(Invalid, match="not configured"):
        await h.service.create_product_profile("coding", h.stages)
    with h.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 0


async def test_query_is_read_only_and_uses_explicit_current_pointer(created):
    h = created
    values = await h.service.query_collet_rollout(h.collet.id)
    original_revision = values.revision
    with pytest.raises(Missing):
        await h.service.query_collet_rollout(99999)
    with pytest.raises(Invalid):
        await h.service.query_collet_rollout(h.collet.id, 1)
    assert (
        await h.service.query_collet_rollout(h.collet.id)
    ).revision == original_revision
    assert len(h.repository.get_collet(h.collet.id).rollouts) == 1


async def test_noop_replays_by_interactive_uuid_without_caller_revision(created):
    h = created
    values = await h.service.query_collet_rollout(h.collet.id)
    rid = values[0].rollout_id
    result = await h.service.interactive(rid, "feedback-1", "feedback")
    assert result.type is A.DO_NOTHING
    assert await h.service.interactive(rid, "feedback-1", "feedback") == result
    assert h.repository.get_collet(h.collet.id).revision == 2
    h.interaction.propose.assert_awaited_once()
    with pytest.raises(Conflict):
        await h.service.interactive(rid, "feedback-1", "changed")
    assert await h.service.interactive(rid, "feedback-2", "feedback") == result
    assert h.interaction.propose.await_count == 2
    saved = h.repository.get_collet(h.collet.id)
    assert saved.revision == 3 and len(saved.rollouts) == 1


async def test_real_session_adapter_is_called_with_saved_route(created):
    h = created
    await h.engine.advance(h.collet.id, h.owner)
    stage = h.repository.get_collet(h.collet.id).current().stages[0]
    assert stage.session_id == "provider-session-1"
    assert stage.status is S.CREATE  # dispatch return is not observed runtime start
    details = await h.service.query_stage_rollout_details(
        stage.stage_rollout_id, limit=12
    )
    assert details.bound and details.messages[0]["id"] == "m-1"
    assert details.complete is None and details.order == "provider-defined"
    h.bot.get_session_messages.assert_awaited_once_with(
        stage.session_id,
        limit=12,
        bot_id="worker-a:actual-owner",
        lifecycle_stage="production",
    )
    assert h.calls[0]["bot_id"] == "worker-a:actual-owner"
    assert (
        h.calls[0]["message_id"]
        == h.repository.get_collet(h.collet.id).records[0].attempt
    )
    h.bot.get_session_messages.side_effect = TimeoutError("upstream timeout")
    from agentclaw.community.core.errors import InternalError

    with pytest.raises(InternalError):
        await h.service.query_stage_rollout_details(stage.stage_rollout_id)


@pytest.mark.parametrize("limit", [True, 0, -1, 201, "50"])
async def test_invalid_message_limits(created, limit):
    stage = created.repository.get_collet(created.collet.id).current().stages[0]
    with pytest.raises(Invalid):
        await created.service.query_stage_rollout_details(
            stage.stage_rollout_id, limit=limit
        )
    created.bot.get_session_messages.assert_not_awaited()


async def test_di_resolves_protocol_and_single_storage_instance(runtime):
    h = runtime
    injector = Injector([ProductProfileRuntimeModule()], auto_bind=False)
    injector.binder.bind(DatabasePlugin, to=h.repository.db)
    injector.binder.bind(
        ProductProfilePublicationAuthorizer,
        to=ProfilePublisherAllowlist((h.owner.actor,)),
    )
    service = injector.get(ProductProfileServiceProtocol)
    assert isinstance(service, ProductProfileServiceProtocol)
    assert injector.get(ProductProfileServiceProtocol) is service
    with pytest.raises(Invalid):
        await service.create_product_profile("blocked", h.stages)


async def test_method_bindings_relations_and_optional_selection_are_frozen(runtime):
    from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
        get_profile,
    )
    from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
        CompositionRelation,
    )
    from agentclaw.community.core.task.context_collab.l6_products.contracts import (
        MethodTemplate,
        MethodRelationTemplate,
    )

    h = runtime
    composition = replace(
        h.composition,
        required_methods=(
            MethodTemplate(get_profile("Open")),
            *h.composition.required_methods,
        ),
        optional_methods=(MethodTemplate(get_profile("Group")),),
        relations=(
            MethodRelationTemplate("Swarm", "Open", CompositionRelation.SUPPORT),
        ),
    )
    profile = persist_profile(h, "composed", composition, h.stages)
    collet = await h.service.create_product_collet(profile.id, "composed request")
    saved = SqlProductRuntimeRepository(h.repository.db).get_collet(collet.id)
    assert h.repository.get_profile(profile.id)[0].composition == composition
    assert {b.method_kind for b in saved.bindings} == {"Open", "Swarm"}
    assert len(saved.relations) == 1
    edge = saved.relations[0]
    by_id = {b.method_binding_id: b.method_kind for b in saved.bindings}
    assert (
        by_id[edge.parent_binding],
        by_id[edge.child_binding],
        edge.relation_kind,
    ) == ("Swarm", "Open", CompositionRelation.SUPPORT)
    assert saved.current().stages[1].name == "implementation"
    assert saved.specs[1].depends_on == ("analysis",)  # a different level of edges
    assert not h.calls
