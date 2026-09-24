from __future__ import annotations

from pathlib import Path

from clawevolve_diagnose.cli import _normalize_argv, build_parser
from clawevolve_diagnose.run.command import _build_run_request, _resolve_intent
from clawevolve_diagnose.run.ids import resolve_output_dir, resolve_runtime_openclaw_home


def _args(argv: list[str]):
    return build_parser().parse_args(argv)


def test_explicit_intent_is_parsed_and_used() -> None:
    args = _args([
        "--task-id", "task-1",
        "--step-id", "step-1",
        "--api-key", "key",
        "--intent", "诊断工具调用失败，抽取10个case",
    ])

    assert args.intent == "诊断工具调用失败，抽取10个case"
    assert _resolve_intent(args) == args.intent
    request = _build_run_request(
        args, task_id="task-1", step_id="step-1", output_dir=Path("/tmp/out")
    )
    assert request.message == args.intent


def test_positional_natural_language_is_not_parsed_as_intent() -> None:
    args, unknown = build_parser().parse_known_args([
        "旧的位置参数意图",
        "--task-id", "task-1",
        "--step-id", "step-1",
        "--api-key", "key",
    ])

    assert args.intent == ""
    assert unknown == ["旧的位置参数意图"]

def test_slash_command_with_intent_is_normalized() -> None:
    normalized = _normalize_argv([
        '/clawevolve-diagnose --task-id task-1 --step-id step-1 '
        '--api-key key --intent "诊断异步任务未完成，抽取5个case"'
    ])

    assert normalized == [
        "--task-id", "task-1",
        "--step-id", "step-1",
        "--api-key", "key",
        "--intent", "诊断异步任务未完成，抽取5个case",
    ]
    args = _args(normalized)
    assert args.intent == "诊断异步任务未完成，抽取5个case"


def test_repeated_session_selectors_are_normalized_and_deduplicated() -> None:
    args = _args([
        "--task-id", "task-1", "--step-id", "step-1",
        "--session-identifier", " id-1 ",
        "--session-identifier", "agent:main:one",
        "--session-identifier", "id-1",
    ])
    request = _build_run_request(
        args, task_id="task-1", step_id="step-1", output_dir=Path("/tmp/out")
    )
    assert request.session_identifiers == ["id-1", "agent:main:one"]


def test_repeated_session_ids_are_frozen_in_the_run_request() -> None:
    args = _args([
        "--task-id", "task-1",
        "--step-id", "step-1",
        "--session-id", "session-a",
        "--session-id", "session-b",
    ])

    request = _build_run_request(
        args, task_id="task-1", step_id="step-1", output_dir=Path("/tmp/out")
    )

    assert request.session_ids == ["session-a", "session-b"]


def test_default_output_dir_follows_an_explicit_local_openclaw_home(tmp_path: Path) -> None:
    assert resolve_output_dir("", "task-1", openclaw_home=str(tmp_path)) == (
        tmp_path / "workspace" / "clawevolve_results" / "task-1" / "diagnose" / "output"
    )


def test_runtime_openclaw_home_is_inferred_from_the_installed_skill_path(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".openclaw" / "workspace" / "skills" / "skills-local" / "clawevolve-diagnose"
    skill_dir.mkdir(parents=True)

    assert resolve_runtime_openclaw_home("", invocation_cwd=str(skill_dir)) == str(
        tmp_path / ".openclaw"
    )


def test_parser_prefers_explicit_openclaw_state_dir_over_legacy_home(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_STATE_DIR", "/runtime/state")
    monkeypatch.setenv("OPENCLAW_HOME", "/legacy/home")

    args = _args(["--task-id", "task-1", "--step-id", "step-1"])

    assert args.openclaw_home == "/runtime/state"
