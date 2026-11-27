"""Durable intervention identities fence repeated writes, including SQL CAS races."""

import asyncio
from threading import Event

import pytest
from sqlalchemy import create_engine, event as sql_event

from agentclaw.community.core.base import Base
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum as A,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    Adjustment,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductIdentityRow,
    ProductIntentRow,
    ProductReceiptRow,
    ProductRuntimeRow,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.session_messages import (
    OpenApiStageSessionMessages,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
    encode_snapshot,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

from .helpers import persist_profile

pytestmark = pytest.mark.asyncio


@pytest.fixture
def engine(tmp_path):
    # These races need independent connections, not the parent's single-connection
    # StaticPool harness. All SQL and transactional behavior remain real.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'interventions.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def rebuilt_service(h):
    return ProductProfileService(
        SqlProductRuntimeRepository(h.repository.db),
        ProfilePublisherAllowlist((h.owner.actor,)),
        h.authorization,
        h.execution,
        h.interaction,
        OpenApiStageSessionMessages(h.bot),
    )


def persisted(h):
    snapshot = encode_snapshot(h.repository.get_collet(h.collet.id))
    with h.repository.db.orm_session() as session:
        counts = tuple(
            session.query(model).count()
            for model in (
                ProductRuntimeRow,
                ProductIdentityRow,
                ProductReceiptRow,
                ProductIntentRow,
            )
        )
    return snapshot, counts


@pytest.mark.parametrize("action", list(A))
async def test_every_adjustment_replays_after_service_reconstruction(created, action):
    h = created
    await h.engine.advance(h.collet.id, h.owner)
    before = h.repository.get_collet(h.collet.id)
    stages = ("analysis",) if action in {A.RE_DO, A.OPTIMIZE} else ()
    h.interaction.propose.return_value = Adjustment(action, "accepted", stages)
    rid = before.current_rollout_id
    first = await h.service.interactive(rid, "intervention-1", "feedback")
    assert first.type is action
    saved = persisted(h)
    service = rebuilt_service(h)
    for _ in range(3):
        assert await service.interactive(rid, "intervention-1", "feedback") == first
        assert persisted(h) == saved
    h.interaction.propose.assert_awaited_once()
    h.bot.send_message.assert_awaited_once()
    after = h.repository.get_collet(h.collet.id)
    assert after.revision == before.revision + 1
    assert len(after.rollouts) == len(before.rollouts) + bool(stages)
    with h.repository.db.orm_session() as session:
        assert (
            session.query(ProductReceiptRow)
            .filter_by(operation="interactive", request_id="intervention-1")
            .count()
            == 1
        )
    if stages:
        with pytest.raises(Conflict, match="stale rollout"):
            await service.interactive(rid, "new-intervention", "feedback")
        assert persisted(h) == saved


async def test_uuid_reuse_with_changed_content_or_rollout_conflicts(created):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    h.interaction.propose.return_value = Adjustment(A.RE_DO, "replan", ("analysis",))
    await h.service.interactive(rid, "intervention-1", "feedback")
    saved = persisted(h)
    for target, content in (
        (rid, "changed"),
        (h.repository.get_collet(h.collet.id).current_rollout_id, "feedback"),
    ):
        with pytest.raises(Conflict):
            await h.service.interactive(target, "intervention-1", content)
        assert persisted(h) == saved
    h.interaction.propose.assert_awaited_once()


async def test_replay_does_not_roll_back_later_intervention_or_skip_authorization(
    created,
):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    first = await h.service.interactive(rid, "intervention-1", "first feedback")
    h.interaction.propose.return_value = Adjustment(A.SHUTDOWN, "stop requested")
    await h.service.interactive(rid, "intervention-2", "stop")
    saved = persisted(h)
    assert await h.service.interactive(rid, "intervention-1", "first feedback") == first
    outsider = ActorContext(PrincipalRef("human", "outsider"), "test")
    with actor_context_scope(outsider), pytest.raises(Forbidden):
        await h.service.interactive(rid, "intervention-1", "first feedback")
    assert persisted(h) == saved
    assert h.interaction.propose.await_count == 2
    assert h.repository.get_collet(h.collet.id).stopped


@pytest.mark.parametrize("identity", ["", " ", "x" * 129, "界" * 43, None, 1, True])
async def test_uuid_must_be_a_nonblank_bounded_string(created, identity):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    saved = persisted(h)
    with pytest.raises(Invalid):
        await h.service.interactive(rid, identity, "feedback")
    assert persisted(h) == saved
    h.interaction.propose.assert_not_awaited()


async def test_same_uuid_is_isolated_by_trusted_tenant_scope(created):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    await h.service.interactive(rid, "intervention-1", "first tenant")
    saved = persisted(h)
    with avernet_tenant_scope("other-public-tenant"):
        profile = persist_profile(h, "coding", h.composition, h.stages)
        collet = await h.service.create_product_collet(profile.id, "other request")
        other = h.repository.get_collet(collet.id)
        await h.service.interactive(
            other.current_rollout_id, "intervention-1", "other tenant"
        )
        assert h.repository.get_collet(collet.id).revision == 2
    assert encode_snapshot(h.repository.get_collet(h.collet.id)) == saved[0]
    assert h.interaction.propose.await_count == 2


async def test_receipt_write_failure_rolls_back_adjustment_and_allows_retry(
    created, engine
):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    h.interaction.propose.return_value = Adjustment(A.RE_DO, "replan", ("analysis",))
    saved = persisted(h)

    def fail_receipt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ac_product_receipt_v2"):
            raise RuntimeError("injected intervention receipt write failure")

    sql_event.listen(engine, "before_cursor_execute", fail_receipt)
    try:
        with pytest.raises(RuntimeError, match="receipt write failure"):
            await h.service.interactive(rid, "intervention-1", "feedback")
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_receipt)
    assert persisted(h) == saved
    await h.service.interactive(rid, "intervention-1", "feedback")
    assert h.repository.get_collet(h.collet.id).revision == 2
    assert len(h.repository.get_collet(h.collet.id).rollouts) == 2


async def test_concurrent_duplicate_replays_when_receipt_appears_during_proposal(
    created,
):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    entered, finish = asyncio.Event(), asyncio.Event()

    async def propose(snapshot, content, context):
        if not entered.is_set():
            entered.set()
            await finish.wait()
        return Adjustment(A.RE_DO, "replan", ("analysis",))

    h.interaction.propose.side_effect = propose
    duplicate = asyncio.create_task(
        h.service.interactive(rid, "intervention-1", "feedback")
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        winner = await rebuilt_service(h).interactive(rid, "intervention-1", "feedback")
        saved = persisted(h)
    finally:
        finish.set()
    assert await duplicate == winner
    assert persisted(h) == saved
    assert h.repository.get_collet(h.collet.id).revision == 2
    assert len(h.repository.get_collet(h.collet.id).rollouts) == 2


async def test_concurrent_duplicate_replays_after_losing_internal_revision_cas(
    created, engine
):
    h = created
    rid = h.repository.get_collet(h.collet.id).current_rollout_id
    at_cas, proceed = Event(), Event()
    # DO_NOTHING reaches CAS without earlier identity inserts taking SQLite's
    # single-writer lock. Successor rollback is exercised by the existing CAS test.
    h.interaction.propose.return_value = Adjustment(A.DO_NOTHING, "unchanged")

    def delay_loser(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.startswith("UPDATE ac_product_runtime_v2")
            and current_actor_context().source == "loser"
        ):
            at_cas.set()
            assert proceed.wait(10), "winner never released duplicate CAS"

    async def losing_call():
        with actor_context_scope(ActorContext(h.owner.actor, "loser")):
            return await h.service.interactive(rid, "intervention-1", "feedback")

    sql_event.listen(engine, "before_cursor_execute", delay_loser)
    duplicate = asyncio.create_task(losing_call())
    try:
        assert await asyncio.to_thread(at_cas.wait, 10), "duplicate never reached CAS"
        winner = await rebuilt_service(h).interactive(rid, "intervention-1", "feedback")
        saved = persisted(h)
        proceed.set()
        assert await duplicate == winner
        assert persisted(h) == saved
        assert h.repository.get_collet(h.collet.id).revision == 2
        assert len(h.repository.get_collet(h.collet.id).rollouts) == 1
    finally:
        proceed.set()
        # Do not remove an SQLAlchemy listener while its callback is running.
        await asyncio.gather(duplicate, return_exceptions=True)
        sql_event.remove(engine, "before_cursor_execute", delay_loser)
