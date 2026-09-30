from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optimize_local_bench_propagates_evolve_task_id() -> None:
    source = (
        ROOT
        / "clawevolve-workflow"
        / "scripts"
        / "handlers"
        / "clawevolve_optimize_run.py"
    ).read_text(encoding="utf-8")

    local_bench = source[source.index("def action_bench_local"):source.index("def action_bench_candidate_opt_targeted")]
    assert '"CLAWEVOLVE_TASK_ID": str(task_id)' in local_bench


def test_bench_workflow_propagates_frozen_evolve_task_id() -> None:
    source = (
        ROOT / "clawevolve-bench" / "scripts" / "clawbench-workflow.py"
    ).read_text(encoding="utf-8")

    assert '"CLAWEVOLVE_TASK_ID": str(identity.get("evolveTaskId") or "")' in source
    assert "EVOLVE_TASK_MARKER_MISSING" in source
