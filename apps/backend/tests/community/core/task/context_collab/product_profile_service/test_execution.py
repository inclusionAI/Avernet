import asyncio
from copy import deepcopy
from dataclasses import replace
import pytest
from sqlalchemy import event as sql_event
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
    ContextCollabNotFoundError as Missing,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ArtifactContentText,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
    EventTypeEnum as E,
    AdjustTypeEnum as A,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    Adjustment,
    ReportContext,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductRuntimeRow,
    ProductIdentityRow,
    ProductReceiptRow,
    ProductIntentRow,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
    decode_snapshot,
    encode_snapshot,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.asyncio


def snapshot(h):
    return h.repository.get_collet(h.collet.id)


def stage_event(h, kind=E.ACCEPTANCE_SUCCEED, passed=True):
    saved = snapshot(h)
    stage = next(s for s in saved.current().stages if s.status is S.RUN_FINISHED)
    condition = next(s.conditions[0] for s in saved.specs if s.name == stage.name)
    return ProgressEvent(
        kind,
        stage.stage_rollout_id,
        [Artifact("doc", "result", ArtifactContentText("verified content"))],
        [AcceptanceResult(condition, passed, "checked against the contract")],
        "",
    )


def report_context(h, event, request_id, actor=None):
    saved = snapshot(h)
    record = next(r for r in saved.records if r.stage_id == event.stage_rollout_id)
    return ReportContext(actor or h.owner, request_id, record.attempt, saved.revision)


async def run_finished(h):
    h.state["value"] = "running"
    await h.engine.advance(h.collet.id, h.owner)  # dispatch only
    await h.engine.advance(h.collet.id, h.owner)  # actual runtime started
    h.state["value"] = "final"
    await h.engine.advance(h.collet.id, h.owner)  # actual runtime terminal


async def test_full_journey_failure_successor_old_report_and_real_messages(created):
    h = created
    await run_finished(h)
    old = snapshot(h)
    failed = stage_event(h, E.ACCEPTANCE_FAILED, False)
    failure_context = report_context(h, failed, "failure-1")
    accepted = await h.engine.report(failed, failure_context)
    assert accepted["accepted"] is True
    assert snapshot(h).current().status is S.ACCEPTANCE_FAILED
    assert snapshot(h).collet.status is S.ACCEPTANCE_FAILED
    assert len(h.calls) == 1
    assert await h.engine.report(failed, failure_context) == accepted
    h.evidence.verify.assert_awaited_once()
    h.interaction.propose.return_value = Adjustment(
        A.RE_DO, "retry rejected design", ("analysis",), "fix the missing requirement"
    )
    prior = snapshot(h)
    old_history = deepcopy(prior.current())
    result = await h.service.interactive(
        prior.current_rollout_id, "retry-design", "fix design"
    )
    assert result.type is A.RE_DO
    current = snapshot(h)
    assert current.current_rollout_id != prior.current_rollout_id
    assert current.parents[str(current.current_rollout_id)] == prior.current_rollout_id
    assert current.rollouts[0] == old_history
    assert not (
        {s.stage_rollout_id for s in current.current().stages}
        & {s.stage_rollout_id for s in prior.current().stages}
    )
    assert all(s.session_id == "" for s in current.current().stages)
    assert "fix the missing requirement" in current.current().content
    with pytest.raises(Conflict):
        await h.engine.report(
            failed,
            replace(
                failure_context,
                request_id="late-unseen",
                expected_revision=current.revision,
            ),
        )
    assert await h.engine.report(failed, failure_context) == accepted
    await run_finished(h)
    passed = stage_event(h)
    await h.engine.report(passed, report_context(h, passed, "accepted-new-design"))
    assert snapshot(h).current().status is S.RUNNING
    await run_finished(h)
    passed = stage_event(h)
    await h.engine.report(passed, report_context(h, passed, "accepted-implementation"))
    current = snapshot(h)
    assert current.collet.status is S.ACCEPTANCE_SUCCEED
    assert current.current().status is S.ACCEPTANCE_SUCCEED
    assert current.rollouts[0] == old_history
    values = await h.service.query_collet_rollout(h.collet.id)
    assert len(values) == 2
    assert (await h.service.query_collet_rollout(h.collet.id, False))[
        0
    ] == current.current()
    details = await h.service.query_stage_rollout_details(
        current.current().stages[1].stage_rollout_id
    )
    assert (
        details.session_id == "provider-session-3"
        and details.messages[0]["content"] == "actual message body"
    )
    assert len(h.calls) == 3
    another = await h.service.create_product_collet(h.profile.id, "build a feature")
    assert another.id != h.collet.id and another.status is S.CREATE
    assert snapshot(h).collet.status is S.ACCEPTANCE_SUCCEED


async def test_run_failed_without_artifacts_and_wrong_actor_rejected(created):
    h = created
    await h.engine.advance(h.collet.id, h.owner)
    await h.engine.advance(h.collet.id, h.owner)
    stage = snapshot(h).current().stages[0]
    failure = ProgressEvent(
        E.RUN_FAILED, stage.stage_rollout_id, [], [], "worker runtime crashed"
    )
    with pytest.raises(Forbidden):
        await h.engine.report(failure, report_context(h, failure, "forged-failure"))
    worker = ActorContext(PrincipalRef("bot", "worker-a"), "signed-test-bot")
    context = report_context(h, failure, "runtime-failure", worker)
    result = await h.engine.report(failure, context)
    assert result["accepted"]
    assert snapshot(h).current().status is S.RUN_FAILED
    assert snapshot(h).current().stages[0].error_msg == "worker runtime crashed"
    assert await h.engine.report(failure, context) == result
    h.evidence.verify.assert_not_awaited()
    await h.engine.advance(h.collet.id, h.owner)
    assert len(h.calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: replace(e, acceptances=[]),
        lambda e: replace(e, acceptances=[AcceptanceResult("unknown", True, "claim")]),
        lambda e: replace(e, acceptances=e.acceptances * 2),
        lambda e: replace(e, acceptances=[replace(e.acceptances[0], passed=False)]),
        lambda e: replace(e, artifacts=[]),
    ],
)
async def test_acceptance_cannot_bypass_conditions_or_evidence(created, mutation):
    h = created
    await run_finished(h)
    event = mutation(stage_event(h))
    revision = snapshot(h).revision
    with pytest.raises(Invalid):
        await h.engine.report(event, report_context(h, event, "invalid"))
    assert snapshot(h).revision == revision
    assert snapshot(h).current().stages[1].status is S.CREATE


async def test_untrusted_verifier_and_attempt_or_lease_rejected(created):
    h = created
    await run_finished(h)
    event = stage_event(h)
    outsider = ActorContext(PrincipalRef("bot", "worker-a"), "bot")
    with pytest.raises(Forbidden):
        await h.engine.report(
            event, report_context(h, event, "forged-passed", outsider)
        )
    with pytest.raises(Conflict):
        await h.engine.report(
            event,
            replace(report_context(h, event, "wrong-attempt"), attempt="unallocated"),
        )
    saved = snapshot(h)
    saved.records[0].lease_until = 1
    h.repository.save(saved, saved.revision)
    with pytest.raises(Conflict):
        await h.engine.report(event, report_context(h, event, "expired"))


async def test_dispatch_return_never_proves_runtime_completion(created):
    h = created
    await h.engine.advance(h.collet.id, h.owner)
    saved = snapshot(h)
    stage = saved.current().stages[0]
    event = ProgressEvent(
        E.ACCEPTANCE_SUCCEED,
        stage.stage_rollout_id,
        [Artifact("doc", "evidence", ArtifactContentText("result"))],
        [AcceptanceResult("design is complete", True, "passed")],
        "",
    )
    with pytest.raises(Conflict):
        await h.engine.report(event, report_context(h, event, "too-early"))
    assert snapshot(h).current().stages[0].status is S.CREATE
    assert len(h.calls) == 1


async def test_unknown_dispatch_is_not_blindly_retried_after_restart(created):
    h = created
    h.bot.send_message.side_effect = TimeoutError("unknown delivery")
    with pytest.raises(TimeoutError):
        await h.engine.advance(h.collet.id, h.owner)
    persisted = SqlProductRuntimeRepository(h.repository.db).get_collet(h.collet.id)
    assert persisted.records[0].dispatch_state == "unknown"
    assert persisted.current().stages[0].session_id == ""
    with pytest.raises(Conflict, match="reconciliation"):
        await h.engine.advance(h.collet.id, h.owner)
    h.bot.send_message.assert_awaited_once()


async def test_optimize_reuses_only_accepted_unaffected_evidence(created):
    h = created
    await run_finished(h)
    event = stage_event(h)
    await h.engine.report(event, report_context(h, event, "design-pass"))
    previous = snapshot(h)
    h.interaction.propose.return_value = Adjustment(
        A.OPTIMIZE, "improve implementation", ("implementation",), "add error handling"
    )
    await h.service.interactive(
        previous.current_rollout_id, "optimize-implementation", "improve"
    )
    current = snapshot(h)
    first = current.current().stages[0]
    assert (
        first.status is S.ACCEPTANCE_SUCCEED
        and first.artifacts == previous.current().stages[0].artifacts
    )
    assert first.stage_rollout_id != previous.current().stages[0].stage_rollout_id
    assert first.session_id == ""
    assert current.records[0] == previous.records[0]
    assert not any(i["kind"] == "cancel" for i in h.repository.pending_intents())
    assert (
        next(
            r for r in current.records if r.stage_id == first.stage_rollout_id
        ).reused_from
        == previous.current().stages[0].stage_rollout_id
    )
    assert current.current().stages[1].status is S.CREATE


async def test_shutdown_is_separate_and_cancellation_not_falsely_confirmed(created):
    h = created
    await h.engine.advance(h.collet.id, h.owner)
    await h.engine.advance(h.collet.id, h.owner)
    before = snapshot(h)
    h.interaction.propose.return_value = Adjustment(A.SHUTDOWN, "stop requested")
    result = await h.service.interactive(before.current_rollout_id, "shutdown", "stop")
    assert result.type is A.SHUTDOWN
    saved = snapshot(h)
    assert saved.stopped and saved.collet.status == before.collet.status
    assert saved.current().status == before.current().status
    await h.engine.advance(h.collet.id, h.owner)
    assert len(h.calls) == 1
    await h.worker.run_once()
    h.bot.cancel_run.assert_awaited_once_with("run-1")
    with h.repository.db.orm_session() as session:
        assert (
            session.query(ProductIntentRow).filter_by(kind="cancel").one().state
            == "requested"
        )


async def test_stop_racing_dispatch_records_late_delivery_cancel(created):
    h = created
    sent, proceed = asyncio.Event(), asyncio.Event()
    original = h.bot.send_message.side_effect

    async def delayed(**kwargs):
        result = await original(**kwargs)
        sent.set()
        await proceed.wait()
        return result

    h.bot.send_message.side_effect = delayed
    task = asyncio.create_task(h.engine.advance(h.collet.id, h.owner))
    await sent.wait()
    before = snapshot(h)
    h.interaction.propose.return_value = Adjustment(A.SHUTDOWN, "stop requested")
    await h.service.interactive(before.current_rollout_id, "shutdown", "stop")
    proceed.set()
    with pytest.raises(Conflict):
        await task
    assert snapshot(h).stopped
    assert snapshot(h).current().stages[0].session_id == ""
    pending = h.repository.pending_intents()
    assert any(
        i["kind"] == "cancel" and i["route"]["session_id"] == "provider-session-1"
        for i in pending
    )


async def test_real_database_transaction_failure_rolls_back_all_creation(
    runtime, engine
):
    h = runtime
    profile = await h.service.create_product_profile("coding", h.composition, h.stages)

    def fail_receipt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ac_product_receipt_v2"):
            raise RuntimeError("injected durable receipt write failure")

    sql_event.listen(engine, "before_cursor_execute", fail_receipt)
    try:
        with pytest.raises(RuntimeError, match="receipt write failure"):
            await h.service.create_product_collet(profile.id, "request")
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_receipt)
    with h.repository.db.orm_session() as session:
        assert session.query(ProductRuntimeRow).count() == 0
        assert session.query(ProductIdentityRow).count() == 0
        assert session.query(ProductIntentRow).count() == 0
        assert session.query(ProductReceiptRow).count() == 1
    h.bot.send_message.assert_not_awaited()


async def test_revision_cas_has_one_winner_and_persists_no_partial_successor(created):
    h = created
    one, two = snapshot(h), snapshot(h)
    h.repository.save(one, 1)
    with pytest.raises(Conflict):
        h.repository.save(
            two, 1, successor=("new context", {"analysis", "implementation"})
        )
    assert snapshot(h).revision == 2 and len(snapshot(h).rollouts) == 1
    with h.repository.db.orm_session() as session:
        assert session.query(ProductIdentityRow).count() == 4


async def test_missing_governance_snapshot_fails_closed(created):
    h = created
    saved = snapshot(h)
    saved.bindings = ()
    with pytest.raises(Invalid, match="governance"):
        decode_snapshot(encode_snapshot(saved))


async def test_tenant_isolation_survives_thread_offload(created):
    h = created
    with avernet_tenant_scope("other-public-tenant"):
        with pytest.raises(Missing):
            await h.service.query_collet_rollout(h.collet.id)
        with pytest.raises(Missing):
            await h.service.create_product_collet(h.profile.id, "request")
        assert h.repository.pending_intents() == []
    assert len(await h.service.query_collet_rollout(h.collet.id)) == 1


@pytest.mark.parametrize("report_wins", [True, False])
async def test_report_and_stop_race_on_persisted_revision(created, report_wins):
    h = created
    await run_finished(h)
    event = stage_event(h)
    context = report_context(h, event, "racing-report")
    before = snapshot(h)
    verify_entered, proposal_entered = asyncio.Event(), asyncio.Event()
    finish_verify, finish_proposal = asyncio.Event(), asyncio.Event()
    original_verify = h.evidence.verify.side_effect

    async def verify(*args):
        await original_verify(*args)
        verify_entered.set()
        await finish_verify.wait()

    async def propose(*args):
        proposal_entered.set()
        await finish_proposal.wait()
        return Adjustment(A.SHUTDOWN, "stop requested")

    h.evidence.verify.side_effect = verify
    h.interaction.propose.side_effect = propose
    reporting = asyncio.create_task(h.engine.report(event, context))
    await verify_entered.wait()
    stopping = asyncio.create_task(
        h.service.interactive(before.current_rollout_id, "shutdown", "stop")
    )
    await proposal_entered.wait()
    if report_wins:
        finish_verify.set()
        await reporting
        finish_proposal.set()
        with pytest.raises(Conflict):
            await stopping
        assert snapshot(h).current().stages[0].status is S.ACCEPTANCE_SUCCEED
        assert not snapshot(h).stopped
    else:
        finish_proposal.set()
        await stopping
        finish_verify.set()
        with pytest.raises(Conflict):
            await reporting
        assert snapshot(h).current().stages[0].status is S.RUN_FINISHED
        assert snapshot(h).stopped
    assert snapshot(h).revision == before.revision + 1


async def test_planner_cannot_mutate_authorized_snapshot(created):
    h = created
    before = snapshot(h)

    async def adversarial(snapshot, content, context):
        snapshot.owner = PrincipalRef("human", "attacker")
        snapshot.current_rollout_id = -1
        snapshot.bindings = ()
        return Adjustment(A.DO_NOTHING, "no change")

    h.interaction.propose.side_effect = adversarial
    await h.service.interactive(
        before.current_rollout_id, "planner-isolation", "feedback"
    )
    saved = snapshot(h)
    assert saved.owner == before.owner and saved.bindings == before.bindings
    assert saved.current_rollout_id == before.current_rollout_id
    assert any(i["kind"] == "advance" for i in h.repository.pending_intents())


async def test_whole_product_owner_acceptance_is_not_fabricated(runtime):
    h = runtime
    declaration = h.composition.required_methods[0]
    h.composition = replace(
        h.composition,
        required_methods=(
            replace(
                declaration,
                policy=replace(declaration.policy, overall_acceptance="owner"),
            ),
        ),
    )
    profile = await h.service.create_product_profile(
        "owner-contract", h.composition, h.stages
    )
    h.collet = await h.service.create_product_collet(profile.id, "work")
    for request in ("owner-design", "owner-implementation"):
        await run_finished(h)
        event = stage_event(h)
        await h.engine.report(event, report_context(h, event, request))
    assert snapshot(h).current().status is S.ACCEPTANCE_SUCCEED
    assert snapshot(h).collet.status is S.RUN_FINISHED
