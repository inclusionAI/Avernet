"""Execution API conformance against public DI/SQLite and real immutable artifacts."""

from dataclasses import replace
import pytest
from agentclaw.community.api.swarm_execution_service import (
    SwarmExecutionServiceProtocol,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    InteractionCommand,
    InteractionDecision,
    InteractionMode,
    StepCommand,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    deploy,
    OWNER,
)


def step(service, driver, key):
    version = service.stages(driver.collet_id, driver.binding_id, OWNER)["version"]
    command = StepCommand(key, version)
    return service.step(driver.collet_id, driver.binding_id, command, OWNER), command


def interact(service, driver, key, **kwargs):
    projection = service.stages(driver.collet_id, driver.binding_id, OWNER)
    target = projection["stages"][0]
    return InteractionCommand(
        key,
        projection["version"],
        target["stage_id"],
        target["session_ref"],
        "Please consider revised requirements",
        **kwargs,
    )


def test_step_advances_one_stage_and_replays_without_redispatch(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    preview = service.stages(driver.collet_id, driver.binding_id, OWNER)
    assert preview["status"] == "UNPLANNED" and len(preview["stages"]) == 4
    assert len(preview["edges"]) == 4
    for index in range(4):
        result, command = step(service, driver, f"step-{index}")
        assert result["dispatched"] and len(worker.calls) == index + 1
        assert (
            service.step(driver.collet_id, driver.binding_id, command, OWNER) == result
        )
    step(service, driver, "reconcile")
    projection = service.stages(driver.collet_id, driver.binding_id, OWNER)
    assert projection["status"] == "COMPLETED"
    assert all(
        s["state"] == "SUCCEEDED" and s["output_bindings"] for s in projection["stages"]
    )
    assert len(worker.calls) == 4


def test_async_step_observes_inflight_and_rejects_stale_or_changed_replay(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    service = injector.get(SwarmExecutionServiceProtocol)
    _, first = step(service, driver, "first")
    result, _ = step(service, driver, "inflight")
    assert not result["dispatched"] and len(worker.calls) == 1
    with pytest.raises(ContextCollabVersionConflict):
        service.step(
            driver.collet_id,
            driver.binding_id,
            replace(first, request_id="stale"),
            OWNER,
        )
    with pytest.raises(ContextCollabVersionConflict):
        service.step(
            driver.collet_id,
            driver.binding_id,
            replace(first, expected_version=999),
            OWNER,
        )
    worker.finish()
    step(service, driver, "next")
    assert len(worker.calls) == 2


def test_default_interaction_is_journaled_and_ignored_with_explicit_reason(
    db, tmp_path
):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "start")
    command = interact(service, driver, "message")
    result = service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    assert result["mode"] == "ignore"
    assert (
        service.interact(driver.collet_id, driver.binding_id, command, OWNER) == result
    )
    projection = service.stages(driver.collet_id, driver.binding_id, OWNER)
    assert projection["stages"][0]["messages"][0]["content"] == command.content
    assert len(worker.calls) == 1


@pytest.mark.parametrize(
    "mode", [InteractionMode.REPLAN_ALL, InteractionMode.REPLAN_FROM]
)
def test_planner_replans_preserving_history_and_reusing_only_unaffected_successes(
    db, tmp_path, mode
):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    for index in range(3):
        step(service, driver, f"initial-{index}")
    original, old_plan = driver.stages(), driver.plan()

    class Planner:
        def __init__(self):
            self.calls = []

        def decide(self, request):
            self.calls.append(request)
            return InteractionDecision(
                mode,
                "requirements changed",
                original[1].stage_id if mode is InteractionMode.REPLAN_FROM else None,
            )

    planner = Planner()
    service.interaction_planner = planner
    command = interact(service, driver, "replan")
    result = service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    assert result["plan_ref"] != old_plan.swarm_plan_id
    assert (
        service.interact(driver.collet_id, driver.binding_id, command, OWNER) == result
    )
    assert len(planner.calls) == 1
    assert (
        len(driver.store.list_plans(driver.axes, driver.binding_id, driver.collet_id))
        == 2
    )
    fresh = driver.stages()
    if mode is InteractionMode.REPLAN_FROM:
        assert fresh[0].task_ref == original[0].task_ref
        assert fresh[2].task_ref == original[2].task_ref
        assert fresh[0].output_bindings == original[0].output_bindings
        assert fresh[1].task_ref != original[1].task_ref
    else:
        assert all(new.task_ref != old.task_ref for new, old in zip(fresh, original))
    for index in range(5):
        step(service, driver, f"revised-{index}")
    assert driver.is_completed()


def test_clarification_notifies_owner_blocks_report_and_accepts_reply(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "start")
    report, agent = worker.produce()
    command = interact(service, driver, "question", kind="clarification_request")
    with pytest.raises(ContextCollabValidationError, match="allocated Agent"):
        service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    asked = service.interact(driver.collet_id, driver.binding_id, command, agent)
    projection = service.stages(driver.collet_id, driver.binding_id, OWNER)
    assert projection["status"] == "WAITING_FOR_INPUT"
    assert projection["notifications"][0]["recipient"] == OWNER.actor.to_dict()
    with pytest.raises(ContextCollabValidationError, match="clarification"):
        worker.reports.report(report, agent)
    result, _ = step(service, driver, "waiting")
    assert result["status"] == "WAITING_FOR_INPUT" and not result["dispatched"]
    reply = replace(
        command,
        request_id="answer",
        expected_version=service.stages(driver.collet_id, driver.binding_id, OWNER)[
            "version"
        ],
        kind="clarification_reply",
        clarification_ref=asked["event_ref"],
        content="Confirmed; continue",
    )
    replied = service.interact(driver.collet_id, driver.binding_id, reply, OWNER)
    assert (
        service.interact(driver.collet_id, driver.binding_id, reply, OWNER) == replied
    )
    worker.reports.report(report, agent)
    step(service, driver, "continue")
    assert len(worker.calls) == 2


def test_allocated_worker_can_read_own_reply_but_cannot_read_all_sessions(db, tmp_path):
    from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
        ActorContext,
    )
    from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
        PrincipalRef,
    )

    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "start")
    _, agent = worker.produce()
    projection = service.stages(driver.collet_id, driver.binding_id, agent)
    # The root's explicitly designated executor may see the task-wide view.
    if agent.actor.principal_id != "workflow_b":
        assert len(projection["stages"]) == 1 and not projection["plans"]
    outsider = ActorContext(PrincipalRef("human", "outsider"), "test")
    with pytest.raises(ContextCollabValidationError):
        service.stages(driver.collet_id, driver.binding_id, outsider)
    spoof = ActorContext(PrincipalRef("bot", OWNER.actor.principal_id), "test")
    with pytest.raises(ContextCollabValidationError):
        service.stages(driver.collet_id, driver.binding_id, spoof)


def test_replan_revokes_inflight_and_late_report_cannot_write_child_work(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "start")
    report, agent = worker.produce()
    original = driver.stages()[0]
    service.interaction_planner = type(
        "Planner",
        (),
        {
            "decide": lambda self, request: InteractionDecision(
                InteractionMode.REPLAN_ALL, "new objective"
            )
        },
    )()
    command = interact(service, driver, "replace")
    service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    version = service.stages(driver.collet_id, driver.binding_id, OWNER)["version"]
    with pytest.raises(ContextCollabValidationError, match="live Stage allocation"):
        worker.reports.report(report, agent)
    assert (
        service.stages(driver.collet_id, driver.binding_id, OWNER)["version"] == version
    )
    assert (
        driver.store.get_stage(
            driver.axes, original.stage_id, driver.binding_id, driver.collet_id
        ).state.value
        == "CANCELLED"
    )
    assert (
        driver.store.get_allocation(
            driver.axes, report.allocation_ref, driver.binding_id, driver.collet_id
        ).state.value
        == "REVOKED"
    )
    step(service, driver, "fresh")
    assert worker.calls[-1][0].task_ref != original.task_ref
    assert worker.calls[-1][0].context_event_refs


def test_step_result_write_failure_reconciles_without_dispatching_again(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    command = StepCommand(
        "retry", service.stages(driver.collet_id, driver.binding_id, OWNER)["version"]
    )
    writer = service.writer

    class FailingWriter:
        def append_event(self, collet_id, request):
            if request.event_kind == "swarm.step.completed":
                raise RuntimeError("journal unavailable")
            return writer.append_event(collet_id, request)

    service.writer = FailingWriter()
    with pytest.raises(RuntimeError, match="journal unavailable"):
        service.step(driver.collet_id, driver.binding_id, command, OWNER)
    assert len(worker.calls) == 1
    with pytest.raises(ContextCollabVersionConflict, match="outstanding"):
        step(service, driver, "must-retry")
    service.writer = writer
    result = service.step(driver.collet_id, driver.binding_id, command, OWNER)
    assert result["dispatched"] and len(worker.calls) == 1


def test_applied_journal_failure_reuses_accepted_planner_decision(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "start")

    class Planner:
        calls = 0

        def decide(self, request):
            self.calls += 1
            return InteractionDecision(InteractionMode.REPLAN_ALL, "updated brief")

    planner = Planner()
    service.interaction_planner = planner
    writer = service.writer

    class FailingWriter:
        def append_event(self, collet_id, request):
            if request.event_kind == "swarm.interaction.applied":
                raise RuntimeError("applied unavailable")
            return writer.append_event(collet_id, request)

    command = interact(service, driver, "retry-replan")
    service.writer = FailingWriter()
    with pytest.raises(RuntimeError, match="applied unavailable"):
        service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    service.writer = writer
    new_plan = driver.plan()
    result = service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    assert result["plan_ref"] == new_plan.swarm_plan_id and planner.calls == 1
    assert (
        len(driver.store.list_plans(driver.axes, driver.binding_id, driver.collet_id))
        == 2
    )


def test_invalid_planner_decision_and_cross_session_reply_rejected(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "start")
    service.interaction_planner = type(
        "Planner",
        (),
        {
            "decide": lambda self, request: InteractionDecision(
                InteractionMode.REPLAN_FROM, "invalid", "unknown"
            )
        },
    )()
    command = interact(service, driver, "invalid-plan")
    with pytest.raises(ContextCollabValidationError, match="unknown"):
        service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    assert (
        len(driver.store.list_plans(driver.axes, driver.binding_id, driver.collet_id))
        == 1
    )
    from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
        ConservativeInteractionPlanner,
    )

    service.interaction_planner = ConservativeInteractionPlanner()
    service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    reply = interact(
        service,
        driver,
        "invalid-reply",
        kind="clarification_reply",
        clarification_ref="no-question",
    )
    with pytest.raises(ContextCollabValidationError, match="not pending"):
        service.interact(driver.collet_id, driver.binding_id, reply, OWNER)


def test_reserved_events_cannot_be_forged_via_generic_append(db, tmp_path):
    from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
        ContextServiceProtocol,
    )
    from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
        EventAppendCommand,
    )

    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    projection = service.stages(driver.collet_id, driver.binding_id, OWNER)
    with pytest.raises(ContextCollabValidationError, match="execution Service API"):
        injector.get(ContextServiceProtocol).append_event(
            driver.collet_id,
            EventAppendCommand(
                projection["version"],
                "swarm.clarification.replied",
                "Message",
                OWNER.actor,
                None,
                "workspace",
                {
                    "binding_id": driver.binding_id,
                    "request_id": "forge",
                    "content": "fake",
                },
            ),
            OWNER,
        )


def test_two_concurrent_step_replays_dispatch_exactly_once(db, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    command = StepCommand(
        "same-step",
        service.stages(driver.collet_id, driver.binding_id, OWNER)["version"],
    )
    barrier = Barrier(2)

    def call():
        barrier.wait()
        return service.step(driver.collet_id, driver.binding_id, command, OWNER)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call) for _ in range(2)]
        results = [f.result() for f in futures]
    assert results[0] == results[1] and len(worker.calls) == 1


def test_projection_does_not_expose_mutable_method_state(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    step(service, driver, "first")
    projection = service.stages(driver.collet_id, driver.binding_id, OWNER)
    projection["plans"][0]["termination_condition"].clear()
    projection["stages"][0]["completion_condition"].clear()
    assert (
        driver.plan().termination_condition and driver.stages()[0].completion_condition
    )


def test_dispatch_failure_is_uncertain_not_retried_or_reported_as_success(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    service = injector.get(SwarmExecutionServiceProtocol)
    original_execute = worker.execute

    def fail(*args):
        original_execute(*args)
        raise RuntimeError("remote acknowledgement lost")

    worker.execute = fail
    command = StepCommand(
        "uncertain",
        service.stages(driver.collet_id, driver.binding_id, OWNER)["version"],
    )
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        service.step(driver.collet_id, driver.binding_id, command, OWNER)
    result = service.step(driver.collet_id, driver.binding_id, command, OWNER)
    assert result["status"] == "RECONCILIATION_REQUIRED"
    assert result["dispatch_state"] == "unknown" and not result["dispatched"]
    assert result["allocation_accepted"] and len(worker.calls) == 1
    graph = service.stages(driver.collet_id, driver.binding_id, OWNER)
    assert graph["status"] == graph["stages"][0]["status"] == "RECONCILIATION_REQUIRED"
    assert graph["stages"][0]["task_status"] == "in_progress"
    next_result, _ = step(service, driver, "observe-uncertain")
    assert next_result["status"] == "RECONCILIATION_REQUIRED" and len(worker.calls) == 1


def test_adoption_failure_retry_recovers_without_creating_another_plan(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    method = driver.swarm
    original = method.adopt_plan

    def fail(*args):
        raise RuntimeError("adoption unavailable")

    method.adopt_plan = fail
    command = StepCommand(
        "adoption",
        service.stages(driver.collet_id, driver.binding_id, OWNER)["version"],
    )
    with pytest.raises(RuntimeError, match="adoption unavailable"):
        service.step(driver.collet_id, driver.binding_id, command, OWNER)
    assert driver.plan().state.value == "DRAFT" and not worker.calls
    method.adopt_plan = original
    service.step(driver.collet_id, driver.binding_id, command, OWNER)
    assert len(worker.calls) == 1
    assert (
        len(driver.store.list_plans(driver.axes, driver.binding_id, driver.collet_id))
        == 1
    )


def test_synchronous_worker_can_request_clarification_inside_step(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    service = injector.get(SwarmExecutionServiceProtocol)
    original_execute = worker.execute

    def execute(*args):
        original_execute(*args)
        _, agent = worker.produce()
        command = interact(
            service, driver, "inside-question", kind="clarification_request"
        )
        service.interact(driver.collet_id, driver.binding_id, command, agent)

    worker.execute = execute
    result, _ = step(service, driver, "start")
    assert result["status"] == "WAITING_FOR_INPUT" and result["dispatched"]
    assert len(worker.calls) == 1


def test_di_installs_planner_capability_and_does_not_expose_mutable_authority(
    db, tmp_path
):
    from injector import InstanceProvider
    from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
        SwarmInteractionPlanner,
    )
    from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_service import (
        SwarmExecutionService,
    )

    injector, driver, worker, _ = deploy(db, tmp_path)

    class Planner:
        calls = 0

        def decide(self, request):
            self.calls += 1
            assert request.interaction_ref and request.content
            request.plan.termination_condition.clear()
            request.stages[0].completion_condition.clear()
            return InteractionDecision(InteractionMode.IGNORE, "message retained")

    planner = Planner()
    injector.binder.bind(SwarmInteractionPlanner, to=InstanceProvider(planner))
    service = injector.get(SwarmExecutionServiceProtocol)
    assert service is injector.get(SwarmExecutionService)
    step(service, driver, "start")
    command = interact(service, driver, "feedback")
    service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    assert planner.calls == 1
    assert (
        driver.plan().termination_condition and driver.stages()[0].completion_condition
    )


@pytest.mark.parametrize("operation", ["step", "interaction"])
def test_initial_request_cas_fences_event_between_validation_and_journal(
    db, tmp_path, operation
):
    from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
        AppendEventRecordRequest,
    )

    injector, driver, worker, _ = deploy(db, tmp_path)
    service = injector.get(SwarmExecutionServiceProtocol)
    if operation == "interaction":
        step(service, driver, "initial")
        command = interact(service, driver, "race-message")
        target_kind = "swarm.interaction.posted"
    else:
        command = StepCommand(
            "race-step",
            service.stages(driver.collet_id, driver.binding_id, OWNER)["version"],
        )
        target_kind = "swarm.step.requested"
    append = service._append

    def racing_append(collet_id, kind, payload, actor, **kwargs):
        if kind == target_kind:
            record = service._record(collet_id)
            service.writer.append_event(
                collet_id,
                AppendEventRecordRequest(
                    record.collet.version,
                    "user_interaction.posted",
                    "Message",
                    OWNER.actor,
                    record.events[-1].event_id,
                    "public",
                    {"content": "concurrent update"},
                ),
            )
        return append(collet_id, kind, payload, actor, **kwargs)

    service._append = racing_append
    with pytest.raises(ContextCollabVersionConflict, match="append snapshot"):
        if operation == "step":
            service.step(driver.collet_id, driver.binding_id, command, OWNER)
        else:
            service.interact(driver.collet_id, driver.binding_id, command, OWNER)
    assert len(worker.calls) == (1 if operation == "interaction" else 0)
    assert not any(
        e.event_kind == target_kind
        and e.payload.get("request_id") == command.request_id
        for e in service._record(driver.collet_id).events
    )
