"""Pure revision/CAS conformance, including unmanaged scheduling compatibility."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    InteractionDecision,
    InteractionMode,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_state import (
    current_plan,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_replanning import (
    build_revision,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    deploy,
    OWNER,
)


def test_revision_commit_compares_whole_execution_before_any_write(db, tmp_path):
    _, driver, _, _ = deploy(db, tmp_path, automatic=False)
    driver.wake()
    driver.step()
    driver.step()
    plan, stages = driver.plan(), tuple(driver.stages())
    allocations = tuple(
        driver.store.list_allocations(driver.axes, driver.binding_id, driver.collet_id)
    )
    revised = build_revision(
        driver.collet_id,
        driver.binding_id,
        plan,
        stages,
        allocations,
        InteractionDecision(InteractionMode.REPLAN_ALL, "change"),
        "event-cas",
        driver.planner.workflow,
        OWNER,
    )
    with pytest.raises(ContextCollabVersionConflict):
        driver.store.commit_swarm_revision(
            driver.axes,
            driver.collet_id,
            driver.binding_id,
            plan,
            stages[:-1],
            allocations,
            *revised,
        )
    assert driver.plan() == plan and tuple(driver.stages()) == stages
    assert (
        driver.store.get_plan(
            driver.axes, revised[-2].swarm_plan_id, driver.binding_id, driver.collet_id
        )
        is None
    )


def test_unmanaged_revision_retains_graph_and_reuses_unaffected_done_work(db, tmp_path):
    _, driver, _, _ = deploy(db, tmp_path)
    driver.wake()
    driver.step()
    driver.step()
    stages = tuple(
        replace(s, workflow_node=None, workflow_digest="", context_event_refs=())
        for s in driver.stages()
    )
    plan = replace(driver.plan(), termination_condition={"all_done": True})
    allocations = tuple(
        driver.store.list_allocations(driver.axes, driver.binding_id, driver.collet_id)
    )
    revised = build_revision(
        driver.collet_id,
        driver.binding_id,
        plan,
        stages,
        allocations,
        InteractionDecision(
            InteractionMode.REPLAN_FROM, "adjust build", stages[1].stage_id
        ),
        "event-dynamic",
        None,
        OWNER,
    )
    fresh_plan, fresh = revised[-2:]
    assert fresh_plan.swarm_plan_id != plan.swarm_plan_id
    assert fresh[0].task_ref == stages[0].task_ref
    assert all(s.task_ref != old.task_ref for s, old in zip(fresh[1:], stages[1:]))
    assert set(fresh[3].dependency_stage_refs) == {fresh[1].stage_id, fresh[2].stage_id}
    assert fresh[1].context_event_refs == ("event-dynamic",)
    assert all(s.workflow_node is None for s in fresh)


def test_direct_candidate_cannot_forge_execution_supersession(db, tmp_path):
    from agentclaw.community.core.task.context_collab.l5_methods.workflow_compilation import (
        compile_workflow,
    )

    _, driver, _, _ = deploy(db, tmp_path)
    candidate = compile_workflow(
        driver.planner.workflow,
        driver.collet_id,
        driver.task_ref,
        {"revision_id": "forged", "supersedes_plan_ref": "other", "reused_tasks": {}},
    )
    with pytest.raises(
        ContextCollabValidationError, match="execution revisions require"
    ):
        driver.swarm.submit_plan_candidate(
            driver.collet_id, driver.binding_id, candidate, OWNER
        )
    assert driver.plan() is None


def test_local_coordination_guard_is_scoped_by_tenant_environment_and_collet(
    db, tmp_path
):
    _, driver, _, _ = deploy(db, tmp_path)
    store = driver.store
    axes = driver.axes
    assert store.swarm_guard(axes, driver.collet_id) is store.swarm_guard(
        axes, driver.collet_id
    )
    assert store.swarm_guard(axes, "other") is not store.swarm_guard(
        axes, driver.collet_id
    )
    assert store.swarm_guard(
        StoreAxes("other", axes.env), driver.collet_id
    ) is not store.swarm_guard(axes, driver.collet_id)


def test_current_plan_rejects_broken_or_ambiguous_revision_chains(db, tmp_path):
    _, driver, _, _ = deploy(db, tmp_path)
    driver.wake()
    driver.step()
    driver.step()
    plan = driver.plan()
    unrelated = replace(plan, swarm_plan_id="unrelated")
    with pytest.raises(ContextCollabValidationError, match="ambiguous"):
        current_plan([plan, unrelated])
    broken = replace(
        unrelated,
        termination_condition={
            "execution_revision": {"supersedes_plan_ref": "missing"}
        },
    )
    with pytest.raises(ContextCollabValidationError, match="broken"):
        current_plan([plan, broken])
