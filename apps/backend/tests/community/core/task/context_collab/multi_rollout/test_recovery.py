import asyncio
import json
import pytest
from sqlalchemy import event as sql_event

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum as A,
    StatusEnum as S,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    Adjustment,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    AdjustmentProposal,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    ParallelBestOf,
    AdaptiveUntilAccepted,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductIntentRow,
    ProductCandidateSlotRow,
    ProductIdentityRow,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
    encode_snapshot,
    decode_snapshot,
)
from .conftest import policy
from .helpers import saved, finish, report, ready_retries

pytestmark = pytest.mark.asyncio


async def test_claim_lease_recovery_and_old_owner_cannot_finish(multi):
    h = await multi.create()
    item = h.repository.pending_intents()[0]
    one = h.repository.claim_intent(item["id"])
    assert one and not h.repository.claim_intent(item["id"])
    with h.repository.db.transactional_orm_session() as session:
        session.query(ProductIntentRow).filter_by(id=item["id"]).update(
            {ProductIntentRow.lease_until: 0}
        )
    rebuilt = SqlProductRuntimeRepository(h.repository.db)
    two = rebuilt.claim_intent(item["id"])
    assert two and two != one
    with pytest.raises(Conflict):
        h.repository.finish_intent(item["id"], "done", one)
    rebuilt.finish_intent(item["id"], "done", two)


async def test_sibling_revision_does_not_supersede_valid_outbox_work(multi):
    h = await multi.create()
    initial = h.repository.pending_intents()
    assert len(initial) == 4
    snapshot = saved(h)
    h.repository.save(snapshot, snapshot.revision)
    assert len(h.repository.pending_intents()) == 4
    assert all(i["revision"] < saved(h).revision for i in initial)
    await h.worker.run_once()
    assert sum(v.started for v in saved(h).multi.candidates) == 2


async def test_allocation_outbox_failure_rolls_back_budget_slot_and_attempt(
    multi, engine
):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    before = encode_snapshot(saved(h))

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ac_product_intent_v2"):
            raise RuntimeError("outbox write failed")

    sql_event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="outbox"):
            await h.engine.advance(
                h.collet.id, h.owner, rollout_id=saved(h).current_rollout_id
            )
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail)
    assert encode_snapshot(saved(h)) == before
    with h.repository.db.orm_session() as session:
        assert session.query(ProductCandidateSlotRow).count() == 0
    h.bot.send_message.assert_not_awaited()


async def test_receipt_failure_rolls_back_successor_but_call_stays_charged(
    multi, engine
):
    h = await multi.create(
        policy(AdaptiveUntilAccepted("interactive", "stage-agent@1", 600))
    )
    rid = saved(h).current_rollout_id
    await finish(h, rid, False)
    before_ids = {r.rollout_id for r in saved(h).rollouts}
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(A.RE_DO, "retry")

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ac_product_receipt_v2"):
            raise RuntimeError("receipt write failed")

    sql_event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="receipt"):
            await h.service.interactive(rid, "retry-after-sql", "feedback")
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail)
    assert {r.rollout_id for r in saved(h).rollouts} == before_ids
    assert saved(h).multi.strategy_calls == 1
    with h.repository.db.orm_session() as session:
        assert session.query(ProductIdentityRow).count() == 3
    await h.service.interactive(rid, "retry-after-sql", "feedback")
    assert saved(h).multi.strategy_calls == 2
    assert len(saved(h).rollouts) == 2
    assert h.strategy.propose_adjustment.await_count == 2


async def test_unknown_start_never_blindly_redelivers_and_slot_survives_restart(multi):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    h.bot.send_message.side_effect = TimeoutError("delivery unknown")
    rid = saved(h).current_rollout_id
    with pytest.raises(TimeoutError):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    h.engine.repository = SqlProductRuntimeRepository(h.repository.db)
    with pytest.raises(Conflict, match="unknown"):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    h.bot.send_message.assert_awaited_once()
    assert saved(h).records[0].dispatch_state == "unknown"
    assert saved(h).multi.candidates[0].slot
    with h.repository.db.orm_session() as session:
        assert session.query(ProductCandidateSlotRow).count() == 1


async def test_stop_during_dispatch_retains_late_route_until_real_confirmation(multi):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.bot.send_message.side_effect

    async def delayed(**kwargs):
        result = await original(**kwargs)
        entered.set()
        await release.wait()
        return result

    h.bot.send_message.side_effect = delayed
    rid = saved(h).current_rollout_id
    task = asyncio.create_task(h.engine.advance(h.collet.id, h.owner, rollout_id=rid))
    await entered.wait()
    h.interaction.propose.return_value = Adjustment(A.SHUTDOWN, "stop")
    await h.service.interactive(rid, "stop-once", "stop")
    release.set()
    with pytest.raises(Conflict, match="revocation"):
        await task
    assert saved(h).multi.phase == "STOPPED" and saved(h).multi.candidates[0].slot
    assert any(i["kind"] == "cancel" for i in h.repository.pending_intents())
    await h.worker.run_once()
    assert (
        saved(h).multi.cancellations[str(saved(h).current().stages[0].stage_rollout_id)]
        == "requested"
    )
    assert saved(h).multi.candidates[0].slot
    h.state["value"] = "final"  # Real observation settles a request-only cancel.
    ready_retries(h)
    await h.worker.run_once()
    assert not saved(h).multi.candidates[0].slot
    with h.repository.db.orm_session() as session:
        assert session.query(ProductCandidateSlotRow).count() == 0
    assert len(h.calls) == 1


async def test_stop_fences_selection_response_without_replacing_business_facts(multi):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    rid = saved(h).current_rollout_id
    await finish(h, rid)
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.strategy.evaluate.side_effect

    async def select(*args):
        result = await original(*args)
        entered.set()
        await release.wait()
        return result

    h.strategy.evaluate.side_effect = select
    task = asyncio.create_task(h.controller.select(h.collet.id, h.owner))
    await entered.wait()
    h.interaction.propose.return_value = Adjustment(A.SHUTDOWN, "stop before adoption")
    await h.service.interactive(rid, "stop-selecting", "stop")
    release.set()
    with pytest.raises(Conflict):
        await task
    assert saved(h).multi.phase == "STOPPED" and not saved(h).multi.winner
    assert saved(h).current().status is S.ACCEPTANCE_SUCCEED


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(schema="product-runtime/multi-rollout@999"),
        lambda d: d.pop("multi"),
        lambda d: d.update(multi=None),
        lambda d: d.update(multi=list(d["multi"].items())),
        lambda d: d.update(collet=list(d["collet"].items())),
        lambda d: d["multi"].update(controller_binding="unknown"),
        lambda d: d["multi"].update(dispatches=True),
        lambda d: d["multi"]["candidates"][0].update(rollout_id=99999),
        lambda d: d["multi"]["candidates"][0].update(generation=999),
        lambda d: d["multi"]["candidates"][0].update(namespace="shared"),
        lambda d: d["multi"]["plans"][0].update(digest="forged"),
        lambda d: d["rollouts"][1]["stages"][0].update(
            stage_rollout_id=d["rollouts"][0]["stages"][0]["stage_rollout_id"]
        ),
        lambda d: d["multi"].update(
            winner=d["rollouts"][0]["rollout_id"], phase="SUCCEEDED"
        ),
    ],
)
async def test_closed_snapshot_corruption_rejected(multi, mutation):
    h = await multi.create()
    value = json.loads(encode_snapshot(saved(h)))
    mutation(value)
    with pytest.raises(Invalid):
        decode_snapshot(json.dumps(value))


@pytest.mark.parametrize("value", ["null", "[]", "true", '"runtime"', "1"])
async def test_stored_snapshot_root_must_be_an_object(value):
    with pytest.raises(Invalid, match="runtime must be an object"):
        decode_snapshot(value)


async def test_snapshot_size_limit_no_silent_history_truncation(multi, monkeypatch):
    h = await multi.create()
    snapshot = saved(h)
    value = encode_snapshot(snapshot)
    from agentclaw.community.core.task.context_collab.l6_products.product_profile_service import (
        snapshot_codec,
    )

    assert snapshot_codec.MAX_SNAPSHOT_BYTES == 12 * 1024 * 1024
    monkeypatch.setattr(snapshot_codec, "MAX_SNAPSHOT_BYTES", len(value.encode()) - 1)
    with pytest.raises(Invalid, match="snapshot limit"):
        encode_snapshot(snapshot)
    assert len(snapshot.rollouts) == 4


async def test_deadline_preserves_incomplete_facts_and_cancel_unknown(
    multi, monkeypatch
):
    h = await multi.create()
    rid = saved(h).current_rollout_id
    await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    from agentclaw.community.core.task.context_collab.l5_methods import (
        multi_rollout_controller as control,
    )

    monkeypatch.setattr(control, "time", lambda: saved(h).multi.deadline)
    await h.controller.tick(h.collet.id)
    snapshot = saved(h)
    assert snapshot.multi.outcome == "DEADLINE"
    assert all(v.incomplete for v in snapshot.multi.candidates)
    assert (
        snapshot.rollouts[0].status is not S.RUN_FAILED
    )  # No fabricated runtime report.
    assert snapshot.multi.candidates[0].slot


async def test_candidate_resource_revocation_blocks_dispatch_and_report(multi):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    rid = saved(h).current_rollout_id
    h.isolation.revoked = True
    from agentclaw.community.core.errors import Forbidden

    with pytest.raises(Forbidden, match="revoked"):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    h.bot.send_message.assert_not_awaited()
    h.isolation.revoked = False
    h.state["value"] = "final"
    await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    event, context = report(h, saved(h).current().stages[0])
    h.isolation.revoked = True
    with pytest.raises(Forbidden, match="revoked"):
        await h.engine.report(event, context)
    assert saved(h).current().status is S.RUN_FINISHED


async def test_real_method_run_port_collects_only_recorded_candidate(multi):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
        MethodRunPort,
    )

    assert isinstance(h.worker.runs, MethodRunPort)
    rid = saved(h).current_rollout_id
    await h.worker.runs.start(h.collet.id, rid, h.owner)
    h.state["value"] = "final"
    await h.worker.runs.observe(h.collet.id, rid, h.owner)
    actual = await h.worker.runs.collect(h.collet.id, rid, h.owner)
    assert actual.stages[0].session_id == "provider-session-1"
    assert actual.status is S.RUN_FINISHED
    assert actual == saved(h).current()
    with pytest.raises(Conflict):
        await h.worker.runs.collect(h.collet.id, 999999, h.owner)


async def test_corrupt_admission_is_still_rejected_by_atomic_snapshot_read(multi):
    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    await h.engine.advance(h.collet.id, h.owner, rollout_id=saved(h).current_rollout_id)
    with h.repository.db.transactional_orm_session() as session:
        session.query(ProductCandidateSlotRow).delete()
    with pytest.raises(Invalid, match="admission facts"):
        h.repository.get_collet(h.collet.id)
