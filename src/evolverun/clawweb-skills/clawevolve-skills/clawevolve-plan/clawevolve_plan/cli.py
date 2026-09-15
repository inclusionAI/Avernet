from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from . import logger
from .constants import DEFAULT_DIAGNOSE_HANDOFF_DIR
from .integration.clawweb import post_step_report
from .pipeline.runner import run_plan_command


_INVOCATION_PREFIXES = (
    "/clawevolve-plan",
    "clawevolve-plan",
    "$clawevolve-plan",
)


def _normalize_invocation_message(message: str) -> str:
    """Strip a preserved skill-command prefix from one invocation string."""

    text = (message or "").strip()
    for prefix in _INVOCATION_PREFIXES:
        if text == prefix:
            return ""
        if text.startswith(prefix + " "):
            return text[len(prefix) :].strip()
    return text


def _normalize_argv(argv: list[str] | None) -> list[str]:
    """Accept a slash command as normal argv or as one quoted argument.

    Online bot runtimes may invoke the Skill script with the complete user
    command preserved as one argument.  Split only arguments that start with a
    known command prefix; leave ordinary values, including ``--goal`` text and
    IDs, unchanged.
    """

    source = list(sys.argv[1:] if argv is None else argv)
    normalized: list[str] = []
    for arg in source:
        text = str(arg or "").strip()
        if not any(
            text == prefix or text.startswith(prefix + " ")
            for prefix in _INVOCATION_PREFIXES
        ):
            normalized.append(arg)
            continue

        command_text = _normalize_invocation_message(text)
        if not command_text:
            continue
        try:
            normalized.extend(shlex.split(command_text))
        except ValueError:
            # Preserve malformed input so argparse returns the normal structured
            # parse error instead of guessing how the user intended to quote it.
            normalized.append(command_text)
    return normalized


def main(argv: list[str] | None = None) -> int:
    logger.configure(secrets=_default_secrets())
    try:
        args = _parser().parse_args(_normalize_argv(argv))
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        print(json.dumps(_parse_error_payload(exc), ensure_ascii=False, indent=2))
        return int(exc.code) if isinstance(exc.code, int) else 2

    os.environ["CLAWEVOLVE_CLAWWEB_URL"] = str(args.clawweb_url).rstrip("/")
    os.environ["CLAWWEB_URL"] = str(args.clawweb_url).rstrip("/")
    if args.model:
        os.environ["CLAWEVOLVE_PLAN_DISCOVERY_MODEL"] = args.model

    result = run_plan_command(
        args, step_reporter=post_step_report, secrets=_default_secrets()
    )
    print(json.dumps(result.payload, ensure_ascii=False, indent=2))
    return result.exit_code


def _parse_error_payload(exc: SystemExit) -> dict[str, object]:
    return {
        "status": "error",
        "agent_next_action": "inspect_warnings",
        "error": f"argument_parse_failed: exit_code={exc.code}",
        "warnings": [f"argument_parse_failed: exit_code={exc.code}"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate templates, objective and spec-v0 from one canonical Plan Source."
        )
    )
    parser.add_argument("--evolve-results-dir", default="", help="Optional local results root; internal default is unchanged.")
    parser.add_argument(
        "--run-dir",
        default="",
        help=(
            "Optional clawevolve-diagnose output directory containing plan-source.json. "
            "If omitted, plan reads /home/admin/.openclaw/workspace/clawevolve_results/{task_id}/diagnose/. "
            "If no Diagnose input exists, plan switches to Direct Goal mode. "
            f"Pass --run-dir explicitly to read another directory such as {DEFAULT_DIAGNOSE_HANDOFF_DIR}"
        ),
    )
    parser.add_argument(
        "--goal",
        default="",
        help=(
            "用户自然语言进化目标。存在 Diagnose/Plan Source 时用于强化目标；"
            "不存在前序输入时，它是 Direct Goal 模式的唯一需求输入。"
        ),
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional model for the Plan discovery agent; existing environment defaults remain unchanged when omitted.",
    )
    parser.add_argument(
        "--task-id",
        dest="task_id",
        required=True,
        help="Required ClawWeb task id for step report upload.",
    )
    parser.add_argument(
        "--step-id",
        dest="step_id",
        required=True,
        help="Required ClawWeb step id for step report upload.",
    )
    parser.add_argument(
        "--clawweb-url",
        default=os.environ.get("CLAWEVOLVE_CLAWWEB_URL")
        or os.environ.get("CLAWWEB_URL")
        or "http://127.0.0.1:5173",
        help="ClawWeb base URL supplied by the task creator.",
    )
    parser.add_argument(
        "--owner-id",
        dest="owner_id",
        default="",
        help=(
            "ClawWeb Bench Domain owner. ClawWeb workflow dispatch should pass the "
            "task owner explicitly so Plan upload and Optimize use the same identity."
        ),
    )
    parser.add_argument(
        "--discovery-notes",
        default="",
        help="Internal compatibility input. Usually omitted; plan auto-generates discovery notes via helper agent.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Internal compatibility input. Usually omitted; plan auto-generates targets via helper agent.",
    )
    parser.add_argument(
        "--bot-id",
        default="",
        help="Target Bot ID supplied by ClawWeb and recorded in Plan Source metadata.",
    )
    parser.add_argument(
        "--skip-clawweb-report",
        action="store_true",
        help="本地测试可选。跳过 ClawWeb Domain 创建、模板上传和 final step report，仅写本地产物。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing task output directory. Default is idempotent: return existing plan artifacts.",
    )
    return parser


def _default_secrets() -> list[str]:
    # Plan does not accept LLM credentials and must not infer parameters from
    # environment variables. Keep this list empty; explicit secrets can still be
    # passed to logger.configure by future CLI flags if needed.
    return []


if __name__ == "__main__":
    raise SystemExit(main())
