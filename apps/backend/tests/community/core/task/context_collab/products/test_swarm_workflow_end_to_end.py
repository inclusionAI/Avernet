"""Actual DB-published Profile → pinned instance → fork/AND-join → named artifacts."""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    StageState,
    AllocationState,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
    SwarmWorkflowRef,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
    SqlProductProfileRepository,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    deploy,
    profile,
    node,
)


def test_published_dag_runs_serial_fork_join_and_keeps_root_acceptance_separate(
    db, tmp_path
):
    injector, driver, worker, validator = deploy(db, tmp_path)
    assert isinstance(injector.get(ProductProfileReader), SqlProductProfileRepository)
    driver.wake()
    driver.drain()
    assert driver.is_completed()
    assert [s.name for s in driver.stages()] == [
        "prepare",
        "build",
        "review",
        "finalize",
    ]
    prepare, build, review, finalize = driver.stages()
    assert build.input_bindings[0].artifacts == prepare.output_bindings[0].artifacts
    assert (
        not review.input_bindings and not review.input_artifact_refs
    )  # control edge only
    assert [b.name for b in finalize.input_bindings] == ["code", "review"]
    assert finalize.input_bindings[0].artifacts == build.output_bindings[0].artifacts
    assert finalize.input_bindings[1].artifacts == review.output_bindings[0].artifacts
    assert all(
        s.state is StageState.SUCCEEDED and s.output_bindings for s in driver.stages()
    )
    assert (
        validator.input_checks and len(validator.output_checks) >= 8
    )  # report AND direct completion validate real resources
    assert len(worker.calls) == 4
    assert not worker.consumed[review.stage_id]
    assert [name for name, _ in worker.consumed[finalize.stage_id]] == [
        "code",
        "review",
    ]
    assert worker.consumed[build.stage_id][0][1][0].startswith(prepare.objective)
    for stage, participant, execution_profile, allocation_ref, *_ in worker.calls:
        assert participant.agent_ref in stage.workflow_node.agents
        assert execution_profile.target_ref == participant.participant_id
        assert stage.objective == stage.workflow_node.objective
        assert allocation_ref == f"alloc_{stage.stage_id}_1"
    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        driver.collet_id
    )
    assert len(record.tasks) == 5 and record.task.status == "in_progress"
    assert all(t.status == "done" for t in record.tasks if t.parent_task_ref)
    assert all(
        a.state is AllocationState.RELEASED
        for a in driver.store.list_allocations(
            driver.axes, driver.binding_id, driver.collet_id
        )
    )
    for task in record.tasks:
        if task.parent_task_ref:
            evidence = [
                e
                for e in record.events
                if e.task_ref == task.task_id
                and e.event_kind in {"task.submitted", "task.accepted"}
            ]
            assert len(evidence) == 2
            assert (
                evidence[0].payload["swarm_artifact_manifest"]
                == evidence[1].payload["swarm_artifact_manifest"]
            )
    driver.wake()
    driver.drain()
    assert len(worker.calls) == 4


def test_sixty_sync_nodes_exceed_old_driver_budget_without_false_failure(db, tmp_path):
    nodes = tuple(node(str(i), () if not i else (str(i - 1),)) for i in range(60))
    graph = SwarmWorkflowDefinition(SwarmWorkflowRef("long_chain", 1), nodes)
    _, driver, worker, _ = deploy(db, tmp_path, profile(graph))
    driver.wake()
    driver.drain()
    assert driver.is_completed() and len(worker.calls) == 60


def test_repeated_wake_does_not_redispatch_inflight_or_pick_new_agent(db, tmp_path):
    _, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    driver.wake()
    driver.drain()
    first = driver.stages()[0]
    for _ in range(3):
        driver.wake()
        driver.drain()
    assert len(worker.calls) == 1 and driver.stages()[0] == first
    worker.finish()
    driver.wake()
    driver.drain()
    assert len(worker.calls) == 2


def test_method_history_loss_rejects_replay_of_persisted_child_tasks(db, tmp_path):
    injector, driver, worker, _ = deploy(db, tmp_path, automatic=False)
    driver.wake()
    driver.drain()
    # Deliberately simulate only in-memory Swarm history loss, keeping SQL child Work.
    driver.store._plans.clear()
    driver.store._stages.clear()
    driver.store._allocations.clear()
    driver.wake()
    with pytest.raises(ContextCollabValidationError, match="history missing"):
        driver.drain()
    assert len(worker.calls) == 1
