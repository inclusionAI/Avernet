from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawevolve_plan.cli import _normalize_argv, _parser
from clawevolve_plan import cli


def test_complete_slash_command_argument_is_normalized() -> None:
    normalized = _normalize_argv(
        [
            "/clawevolve-plan --task-id EV-20260817-ABC123 "
            '--step-id STEP-PLAN-001 --goal "创建 json-log-analyzer Skill，统计错误日志"'
        ]
    )

    assert normalized == [
        "--task-id",
        "EV-20260817-ABC123",
        "--step-id",
        "STEP-PLAN-001",
        "--goal",
        "创建 json-log-analyzer Skill，统计错误日志",
    ]
    args = _parser().parse_args(normalized)
    assert args.task_id == "EV-20260817-ABC123"
    assert args.step_id == "STEP-PLAN-001"
    assert args.goal == "创建 json-log-analyzer Skill，统计错误日志"


def test_separate_slash_prefix_is_removed_without_rewriting_values() -> None:
    normalized = _normalize_argv(
        [
            "/clawevolve-plan",
            "--task-id",
            "EV-A-B_C.1",
            "--step-id",
            "STEP-X-Y_Z.2",
            "--goal",
            "新增 skill，保留自然语言原文",
        ]
    )

    assert normalized == [
        "--task-id",
        "EV-A-B_C.1",
        "--step-id",
        "STEP-X-Y_Z.2",
        "--goal",
        "新增 skill，保留自然语言原文",
    ]


def test_wrapper_flags_can_precede_complete_slash_command() -> None:
    normalized = _normalize_argv(
        [
            "--skip-clawweb-report",
            '/clawevolve-plan --task-id EV-1 --step-id STEP-1 --goal "新增 Skill"',
        ]
    )

    assert normalized == [
        "--skip-clawweb-report",
        "--task-id",
        "EV-1",
        "--step-id",
        "STEP-1",
        "--goal",
        "新增 Skill",
    ]


def test_local_results_directory_is_explicit_and_default_stays_internal(tmp_path) -> None:
    from clawevolve_plan.pipeline.paths import output_dirs, resolve_run_dir
    args = _parser().parse_args(["--task-id", "EV-local", "--step-id", "STEP-local", "--evolve-results-dir", str(tmp_path)])
    root, _, output = output_dirs(args.task_id, args.evolve_results_dir)
    assert root == tmp_path / "EV-local"
    assert output == root / "plan" / "output"
    assert resolve_run_dir("", args.task_id, args.evolve_results_dir) == str(root / "diagnose")
    defaults = _parser().parse_args(["--task-id", "EV-local", "--step-id", "STEP-local"])
    assert defaults.evolve_results_dir == ""


def test_plan_model_override_is_an_explicit_optional_argument() -> None:
    args = _parser().parse_args(
        [
            "--task-id",
            "EV-local",
            "--step-id",
            "STEP-local",
            "--model",
            "provider/custom-model",
        ]
    )
    assert args.model == "provider/custom-model"


def test_plan_model_override_changes_discovery_model_only_when_passed(monkeypatch) -> None:
    monkeypatch.setenv("CLAWEVOLVE_PLAN_DISCOVERY_MODEL", "provider/default-model")
    monkeypatch.setattr(
        cli,
        "run_plan_command",
        lambda *_args, **_kwargs: SimpleNamespace(payload={}, exit_code=0),
    )
    assert cli.main(["--task-id", "EV-default", "--step-id", "STEP-default"]) == 0
    assert cli.os.environ["CLAWEVOLVE_PLAN_DISCOVERY_MODEL"] == "provider/default-model"
    assert cli.main([
        "--task-id", "EV-local", "--step-id", "STEP-local",
        "--model", "provider/custom-model",
    ]) == 0
    assert cli.os.environ["CLAWEVOLVE_PLAN_DISCOVERY_MODEL"] == "provider/custom-model"
