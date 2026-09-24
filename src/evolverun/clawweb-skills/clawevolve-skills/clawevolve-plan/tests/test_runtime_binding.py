from __future__ import annotations

import os
from pathlib import Path

from clawevolve_plan.cli import _bind_runtime_paths, _parser
from clawevolve_plan.discovery.service import resolve_workspace_root


def test_container_paths_are_bound_to_the_current_local_bot(tmp_path: Path, monkeypatch) -> None:
    openclaw_home = tmp_path / ".openclaw"
    candidate = openclaw_home / "clawevolve_workspaces" / "EV-1" / "workspace"
    target = candidate / "skills" / "skills-local" / "daily-report-zh"
    diagnose = openclaw_home / "workspace" / "clawevolve_results" / "EV-1" / "diagnose"
    target.mkdir(parents=True)
    diagnose.mkdir(parents=True)
    args = _parser().parse_args([
        "--task-id", "EV-1",
        "--step-id", "STEP-1",
        "--workspace", "/home/admin/.openclaw/clawevolve_workspaces/EV-1/workspace",
        "--target", "/home/admin/.openclaw/clawevolve_workspaces/EV-1/workspace/skills/skills-local/daily-report-zh",
        "--run-dir", "/home/admin/.openclaw/workspace/clawevolve_results/EV-1/diagnose",
    ])

    with monkeypatch.context() as runtime_env:
        runtime_env.setenv("CLAWEVOLVE_TARGET_WORKSPACE", "__test_unset__")
        _bind_runtime_paths(args, openclaw_home=str(openclaw_home))

        assert args.workspace == str(candidate)
        assert args.target == [str(target)]
        assert args.run_dir == str(diagnose)
        assert args.evolve_results_dir == str(openclaw_home / "workspace" / "clawevolve_results")
        assert os.environ["CLAWEVOLVE_TARGET_WORKSPACE"] == str(candidate)
        assert resolve_workspace_root({}) == candidate.resolve()
