from __future__ import annotations

from pathlib import Path

from clawevolve_diagnose.cli import _normalize_argv, build_parser
from clawevolve_diagnose.run.command import _build_run_request, _resolve_intent


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
