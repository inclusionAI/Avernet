from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmPlanOpCommand,
    SwarmExecutionProfileCommand,
    SwarmAllocateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.workflow_compilation import (
    compile_workflow,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    deploy,
    profile,
    workflow,
    OWNER,
    AGENTS,
)


def prepare(db, directory, definition=None):
    injector, driver, worker, validator = deploy(
        db, directory, definition, automatic=False
    )
    driver.wake()  # Starts root Work, without running the queued planning step.
    graph = driver.planner.workflow
    command = compile_workflow(graph, driver.collet_id, driver.task_ref)
    return injector, driver, worker, validator, command


def adopt(driver, candidate):
    service = driver.swarm
    service.submit_plan_candidate(driver.collet_id, driver.binding_id, candidate, OWNER)
    for operation in (service.adopt_plan, service.start_plan):
        operation(
            driver.collet_id,
            driver.binding_id,
            candidate.swarm_plan_id,
            SwarmPlanOpCommand(),
            OWNER,
        )


def allocation(driver, participant, suffix="a", input_contract="input/1"):
    execution = driver.swarm.declare_execution_profile(
        driver.collet_id,
        driver.binding_id,
        SwarmExecutionProfileCommand(
            "A2A_AGENT",
            participant.participant_id,
            ["EXECUTE"],
            input_contract,
            output_contract_ref="output/1",
        ),
        OWNER,
    )
    return SwarmAllocateCommand(
        driver.stages()[0].stage_id,
        "AGENT",
        participant.participant_id,
        execution.object_id,
        (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        allocation_id=f"allocation_{suffix}",
    )


@pytest.mark.parametrize("change", ["objective", "agent", "node_count", "completion"])
def test_direct_candidate_cannot_override_managed_product_graph(db, tmp_path, change):
    _, driver, worker, _, candidate = prepare(db, tmp_path)
    if change == "objective":
        candidate.stages[0]["objective"] = "tampered"
    elif change == "agent":
        candidate.stages[0]["workflow_node"]["agents"] = []
    elif change == "node_count":
        candidate.stages.pop()
    else:
        candidate.termination_condition = {"anything": True}
    with pytest.raises(ContextCollabValidationError, match="exactly match"):
        driver.swarm.submit_plan_candidate(
            driver.collet_id, driver.binding_id, candidate, OWNER
        )
    assert driver.plan() is None and not worker.calls


def test_direct_allocate_enforces_node_whitelist_and_io_profile(db, tmp_path):
    graph = workflow()
    graph = replace(
        graph, nodes=(replace(graph.nodes[0], agents=AGENTS[:1]), *graph.nodes[1:])
    )
    injector, driver, worker, _, candidate = prepare(db, tmp_path, profile(graph))
    adopt(driver, candidate)
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        driver.collet_id
    )
    participants = [
        p for p in current_participation(record.events).participants if p.agent_ref
    ]
    outsider = next(p for p in participants if p.agent_ref == AGENTS[1])
    allowed = next(p for p in participants if p.agent_ref == AGENTS[0])
    for command in (
        allocation(driver, outsider),
        allocation(driver, allowed, input_contract="forged/1"),
    ):
        with pytest.raises(ContextCollabValidationError):
            driver.swarm.allocate_stage(
                driver.collet_id, driver.binding_id, command, OWNER
            )
    assert driver.stages()[0].state is StageState.READY and not worker.calls
    assert not driver.store.list_allocations(
        driver.axes, driver.binding_id, driver.collet_id
    )


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_allocation_commits_one_whole_snapshot_no_partial_write(
    db, tmp_path, same_request
):
    # Exercise the compound MethodStore capability directly: no shared SQLite
    # connection/SQL transaction is involved in this in-process CAS boundary.
    from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
        StageAllocation,
        AllocationState,
        AllocationTargetKind,
    )

    injector, driver, worker, validator, candidate = prepare(db, tmp_path)
    adopt(driver, candidate)
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        driver.collet_id
    )
    participants = [
        p for p in current_participation(record.events).participants if p.agent_ref
    ]
    commands = [allocation(driver, p, str(i)) for i, p in enumerate(participants)]
    if same_request:
        commands = [commands[0], commands[0]]
    expected_plan = driver.plan()
    expected_stages = tuple(driver.stages())
    updated = replace(expected_stages[0], state=StageState.RUNNING)
    barrier = Barrier(2)

    def attempt(command):
        allocation = StageAllocation(
            command.allocation_id,
            command.stage_ref,
            AllocationTargetKind.AGENT,
            command.target_ref,
            command.execution_profile_ref,
            f"lease_{command.allocation_id}",
            command.lease_expires_at,
            AllocationState.ACTIVE,
            1,
        )
        barrier.wait(timeout=30)
        try:
            driver.store.commit_workflow_allocation(
                driver.axes,
                driver.binding_id,
                driver.collet_id,
                expected_plan,
                expected_stages,
                updated,
                allocation,
            )
            return True
        except ContextCollabValidationError:
            return False

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, commands))
    assert results.count(True) == (2 if same_request else 1)
    allocations = driver.store.list_allocations(
        driver.axes, driver.binding_id, driver.collet_id
    )
    assert len(allocations) == 1 and driver.stages()[0].state is StageState.RUNNING
    assert not worker.calls


@pytest.mark.parametrize("phase", ["in_flight", "paused", "lease_elapsed"])
def test_managed_allocation_replay_preserves_first_result(
    db, tmp_path, monkeypatch, phase
):
    from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm import (
        stage as stage_ops,
    )

    graph = workflow()
    definition = profile(replace(graph, nodes=graph.nodes[:1]))
    _, driver, worker, _, candidate = prepare(db, tmp_path, definition)
    adopt(driver, candidate)
    driver.drain()
    first = driver.store.list_allocations(
        driver.axes, driver.binding_id, driver.collet_id
    )[0]
    command = SwarmAllocateCommand(
        first.stage_ref,
        first.target_kind.value,
        first.target_ref,
        first.execution_profile_ref,
        first.lease_expires_at,
        list(first.qualification_evidence_refs),
        first.allocation_id,
    )
    if phase == "paused":
        driver.swarm.pause_plan(
            driver.collet_id,
            driver.binding_id,
            driver.plan().swarm_plan_id,
            SwarmPlanOpCommand(),
            OWNER,
        )
    elif phase == "lease_elapsed":
        future = datetime.fromisoformat(first.lease_expires_at) + timedelta(hours=1)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return future if tz is not None else future.replace(tzinfo=None)

        monkeypatch.setattr(stage_ops, "datetime", Clock)
    before = tuple(driver.stages())
    result = driver.swarm.allocate_stage(
        driver.collet_id, driver.binding_id, command, OWNER
    )
    assert result.accepted and result.object_id == first.allocation_id
    assert result.state == first.state.value
    assert driver.store.list_allocations(
        driver.axes, driver.binding_id, driver.collet_id
    ) == [first]
    assert tuple(driver.stages()) == before and len(worker.calls) == 1
    with pytest.raises(ContextCollabValidationError, match="differs"):
        driver.swarm.allocate_stage(
            driver.collet_id,
            driver.binding_id,
            replace(
                command,
                lease_expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=5)
                ).isoformat(),
            ),
            OWNER,
        )
    assert tuple(driver.stages()) == before
