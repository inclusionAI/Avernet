from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from .constants import DEFAULT_MODEL
from .integration.clawweb_events import post_step_report
from .run.command import run_diagnose_command


_INVOCATION_PREFIXES = (
    "/clawevolve-diagnose",
    "clawevolve-diagnose",
    "$clawevolve-diagnose",
)


def _normalize_invocation_message(message: str) -> str:
    """Allow bot runtimes to pass the full slash command as argv text."""

    text = (message or "").strip()
    for prefix in _INVOCATION_PREFIXES:
        if text == prefix:
            return ""
        if text.startswith(prefix + " "):
            return text[len(prefix) :].strip()
    return text


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    """Accept slash-command text as either real argv or one quoted string.

    Bot runtimes often hand the whole user slash command to the skill script as
    one argument, e.g. ``'/clawevolve-diagnose --task-id task_001 --api-key sk ... 抽取10个case'``.
    Split that form before argparse so flags embedded in the slash command are
    parsed as CLI options rather than natural-language message text.  Also handle
    hidden wrapper flags before the slash text, such as ``--output-dir DIR
    '/clawevolve-diagnose --task-id task_001 --api-key ...'``.
    """

    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv

    normalized: list[str] = []
    for arg in argv:
        text = str(arg or "").strip()
        command_text = None
        for prefix in _INVOCATION_PREFIXES:
            if text == prefix or text.startswith(prefix + " "):
                command_text = _normalize_invocation_message(text)
                break
        if command_text is None:
            normalized.append(arg)
            continue
        if not command_text:
            continue
        try:
            normalized.extend(shlex.split(command_text))
        except ValueError:
            normalized.append(command_text)
    return normalized


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawevolve-diagnose")
    p.add_argument(
        "--intent",
        default="",
        help=(
            "本次诊断的自然语言意图。可描述问题类型、case 数量、时间范围和筛选偏好；"
            "诊断自然语言只能通过该参数传入。"
        ),
    )
    p.add_argument(
        "--api-key",
        default="",
        help=(
            "提供 OpenAI-compatible API key；未传时读取 OPENAI_API_KEY。"
            "命令行参数优先，主要用于本地调试；BaaS 通过环境变量注入。"
        ),
    )
    p.add_argument(
        "--judge-backend",
        choices=("api", "subagent"),
        default="",
        help=(
            "Session Judge 运行方式。subagent 使用当前 Bot 的 OpenClaw Agent；"
            "api 使用 --api-key/OPENAI_API_KEY。未指定时，有 API Key 兼容走 api，"
            "否则默认 subagent。"
        ),
    )
    p.add_argument("--output-dir", default="", help=argparse.SUPPRESS)
    p.add_argument(
        "--task-id",
        dest="task_id",
        default="",
        help=(
            "Required ClawWeb task id. When --output-dir is not supplied, diagnose artifacts "
            "are written under /home/admin/.openclaw/workspace/clawevolve_results/{task_id}/diagnose/output/"
        ),
    )
    p.add_argument(
        "--step-id",
        dest="step_id",
        default="",
        help="Required ClawWeb step id used with --task-id when reporting diagnose status/output.",
    )
    p.add_argument(
        "--source",
        choices=("local", "service_export"),
        default="local",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--source-user-id", default="", help=argparse.SUPPRESS)
    p.add_argument("--source-bot-id", default="", help=argparse.SUPPRESS)
    p.add_argument(
        "--source-download-network",
        choices=("office", "production"),
        default="office",
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--llm-base-url",
        default="",
        help="local 链路可选。OpenAI-compatible base URL；未显式传入时固定使用代码默认值",
    )
    p.add_argument("--model", default=DEFAULT_MODEL, help="local 链路可选。LLM model name")
    p.add_argument(
        "--max-sessions",
        type=int,
        default=0,
        help="最多分析多少个本地 session；默认 10。可在 --intent 中描述自然语言筛选要求；不读取环境变量",
    )
    p.add_argument(
        "--debug-session-path",
        default="",
        help="调试入口：只从指定 session JSONL 文件读取并使用所选 Judge 分析该 session。",
    )
    p.add_argument(
        "--openclaw-home",
        default="",
        help=(
            "本地测试可选。指定要读取的 .openclaw 根目录；未提供时使用 ~/.openclaw。"
            "线上启动脚本不变，仅需额外追加该参数。"
        ),
    )
    p.add_argument(
        "--skip-clawweb-report",
        action="store_true",
        help="本地测试可选。跳过 ClawWeb step report 网络上报，仅写本地产物。",
    )
    p.add_argument(
        "--clawweb-url",
        default=os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL") or "http://127.0.0.1:5173",
        help="ClawWeb base URL supplied by the task creator.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args, unknown = parser.parse_known_args(_normalize_argv(argv))
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        print(
            json.dumps(
                {
                    "status": "error",
                    "ready_for_plan": False,
                    "agent_next_action": "inspect_warnings",
                    "source": "",
                    "error": f"argument_parse_failed: exit_code={exc.code}",
                    "warnings": [f"argument_parse_failed: exit_code={exc.code}"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return int(exc.code) if isinstance(exc.code, int) else 2
    os.environ["CLAWEVOLVE_CLAWWEB_URL"] = str(args.clawweb_url).rstrip("/")
    os.environ["CLAWWEB_URL"] = str(args.clawweb_url).rstrip("/")

    result = run_diagnose_command(args, unknown, step_reporter=post_step_report)
    print(json.dumps(result.payload, ensure_ascii=False, indent=2))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
