"""Slim service signatures retain fail-closed, task-local caller authorization."""

import asyncio
import inspect
from contextvars import Context

import pytest

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductConfigurationRow,
    ProductRuntimeRow,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)

METHOD_PARAMETERS = {
    "create_product_profile": ("self", "name", "stages"),
    "update_product_profile": (
        "self",
        "profile_id",
        "name",
        "description",
        "stages",
        "status",
    ),
    "create_product_collet": ("self", "profile_id", "content"),
    "query_collet_rollout": ("self", "collet_id", "all_rollout"),
    "interactive": (
        "self",
        "collet_rollout_id",
        "interactive_uuid",
        "interactive_context",
    ),
    "query_stage_rollout_details": ("self", "stage_rollout_id", "limit"),
}


@pytest.mark.parametrize(
    "contract", [ProductProfileServiceProtocol, ProductProfileService]
)
def test_product_service_signatures_exclude_context_and_request_id(contract):
    for method, names in METHOD_PARAMETERS.items():
        assert tuple(inspect.signature(getattr(contract, method)).parameters) == names


def test_actor_scope_restores_outer_actor_and_unbound_state_even_on_error():
    owner = ActorContext(PrincipalRef("human", "owner"), "test")
    other = ActorContext(PrincipalRef("human", "other"), "test")

    def isolated():
        with pytest.raises(Forbidden, match="authenticated actor scope"):
            current_actor_context()
        with actor_context_scope(owner):
            assert current_actor_context() == owner
            with pytest.raises(RuntimeError, match="interrupted"):
                with actor_context_scope(other):
                    assert current_actor_context() == other
                    raise RuntimeError("interrupted")
            assert current_actor_context() == owner
        with pytest.raises(Forbidden, match="authenticated actor scope"):
            current_actor_context()

    Context().run(isolated)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", METHOD_PARAMETERS)
async def test_all_product_operations_require_a_trusted_actor_scope(created, method):
    h = created
    saved = h.repository.get_collet(h.collet.id)
    arguments = {
        "create_product_profile": (("blocked", h.stages), {}),
        "update_product_profile": (
            (h.profile.id,),
            {"name": "blocked"},
        ),
        "create_product_collet": ((h.profile.id, "blocked"), {}),
        "query_collet_rollout": ((h.collet.id,), {}),
        "interactive": (
            (saved.current_rollout_id, "blocked-intervention", "blocked"),
            {},
        ),
        "query_stage_rollout_details": (
            (saved.current().stages[0].stage_rollout_id,),
            {},
        ),
    }
    args, kwargs = arguments[method]
    call = getattr(h.service, method)(*args, **kwargs)
    with pytest.raises(Forbidden, match="authenticated actor scope"):
        await Context().run(asyncio.create_task, call)
    with h.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 1
        assert session.query(ProductRuntimeRow).count() == 1
    assert h.repository.get_collet(h.collet.id).revision == 1
    assert current_actor_context() == h.owner
    h.interaction.propose.assert_not_awaited()
    h.bot.get_session_messages.assert_not_awaited()
    assert not h.calls


@pytest.mark.asyncio
async def test_concurrent_callers_cannot_share_the_singleton_services_actor(created):
    h = created
    outsider = ActorContext(PrincipalRef("human", "outsider"), "test")
    # StaticPool gives this fixture one SQLite connection, not independent
    # concurrent transactions. Overlap caller scopes and thread propagation,
    # but serialize SQL reads on that shared connection (not in production).
    storage_read = asyncio.Lock()

    async def read(actor):
        with actor_context_scope(actor):
            await asyncio.sleep(0)
            assert current_actor_context() == actor
            assert await asyncio.to_thread(current_actor_context) == actor
            async with storage_read:
                assert current_actor_context() == actor
                if actor == outsider:
                    with pytest.raises(Forbidden):
                        await h.service.query_collet_rollout(h.collet.id)
                    return "forbidden"
                return (await h.service.query_collet_rollout(h.collet.id)).revision

    assert await asyncio.gather(read(h.owner), read(outsider)) == [1, "forbidden"]
    assert current_actor_context() == h.owner
