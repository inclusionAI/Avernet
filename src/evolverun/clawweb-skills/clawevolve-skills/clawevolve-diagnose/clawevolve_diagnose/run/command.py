from __future__ import annotations

from dataclasses import dataclass
import os
import traceback
from typing import Any

from .. import logger as diag_logger
from ..integration.clawweb_events import post_step_report
from ..integration.output import build_execution_output, build_success_summary
from ..models import RunRequest
from .runner import (
    JudgeExecutionFailedError,
    raise_if_all_judge_assessments_failed,
    run_pipeline,
)
from ..utils import redact_secrets
from .ids import require_step_id, require_task_id, resolve_output_dir
from .invocation import normalize_invocation_message, scrub_message_secrets, unsupported_flag_args
from .payloads import error_payload, rewrite_summary_with_upload, success_payload
from .step_reports import (
    StepReporter,
    post_failure_report,
    post_success_report,
)


@dataclass(frozen=True)
class DiagnoseCommandResult:
    exit_code: int
    payload: dict[str, Any]


def run_diagnose_command(
    args: Any,
    unknown: list[str] | None = None,
    *,
    step_reporter: StepReporter = post_step_report,
) -> DiagnoseCommandResult:
    """Execute clawevolve-diagnose after CLI parsing.

    CLI 只负责 argv/argparse/print/exit；这里负责命令级生命周期：
    id 校验、输出目录、ClawWeb 最终成功/失败上报、真实链路执行、
    以及可解析的命令返回 payload。
    """

    args, unsupported_error = _merge_unknown_message_args(args, unknown or [])
    if getattr(args, "skip_clawweb_report", False):
        step_reporter = _skipped_step_reporter
    if unsupported_error:
        return DiagnoseCommandResult(
            2, error_payload(args, unsupported_error, clawweb_upload=None)
        )

    try:
        task_id = require_task_id(args.task_id)
        step_id = require_step_id(args.step_id)
        output_dir = resolve_output_dir(args.output_dir, task_id)
        resolved_api_key, api_key_source = _resolve_api_key_for_run(args.api_key)
        args.api_key = resolved_api_key
        args._api_key_source = api_key_source
        diag_logger.configure(
            output_dir / "clawevolve-diagnose.log", secrets=[args.api_key]
        )
        _log_command_start(
            args, task_id=task_id, step_id=step_id, output_dir=output_dir
        )
    except ValueError as exc:
        return DiagnoseCommandResult(2, error_payload(args, str(exc), clawweb_upload=None))

    diag_logger.info(
        "diagnose execution path selected",
        task_id=task_id,
        step_id=step_id,
        path="real",
        report_policy="final_only",
        final_report_will_be_sent_after="pipeline_returns_or_raises",
        preflight_report_sent=False,
    )
    req = _build_run_request(
        args, task_id=task_id, step_id=step_id, output_dir=output_dir
    )
    try:
        diag_logger.info(
            "diagnose pipeline invoke start",
            task_id=req.task_id,
            step_id=req.step_id,
            path="real",
            output_dir=req.output_dir,
        )
        result = run_pipeline(req)
        # Defensive final-boundary validation also protects callers/tests that
        # supply a pre-built RunResult instead of using the real pipeline.
        raise_if_all_judge_assessments_failed(
            (result.summary.get("selection_report") or {})
            if isinstance(result.summary, dict)
            else {}
        )
        diag_logger.info(
            "diagnose pipeline invoke done",
            task_id=req.task_id,
            step_id=req.step_id,
            path="real",
            summary_path=result.summary_path,
            summary_keys=sorted(result.summary.keys()) if isinstance(result.summary, dict) else [],
        )
        diagnose_output = build_execution_output(result)
        diag_logger.info(
            "diagnose final report payload build done",
            task_id=req.task_id,
            step_id=req.step_id,
            output_keys=sorted(diagnose_output.keys()) if isinstance(diagnose_output, dict) else [],
            case_total=((diagnose_output.get("cases") or {}).get("total") if isinstance(diagnose_output, dict) else None),
            good_count=((diagnose_output.get("cases") or {}).get("goodCount") if isinstance(diagnose_output, dict) else None),
            bad_count=((diagnose_output.get("cases") or {}).get("badCount") if isinstance(diagnose_output, dict) else None),
        )
        final_report = post_success_report(
            step_reporter,
            task_id=req.task_id,
            step_id=req.step_id,
            summary=build_success_summary(result),
            output=diagnose_output,
        )
        result.summary["clawweb_upload"] = {"final": final_report}
        rewrite_summary_with_upload(result)
        return DiagnoseCommandResult(0, success_payload(result))
    except Exception as exc:  # noqa: BLE001 - slash-command skills must fail in a parseable way.
        safe_error = redact_secrets(f"{type(exc).__name__}: {exc}", [req.api_key])
        safe_traceback = redact_secrets(traceback.format_exc(), [req.api_key])
        diag_logger.error(
            "diagnose pipeline raised; final failure report will be sent",
            task_id=req.task_id,
            step_id=req.step_id,
            path="real",
            error=safe_error,
            traceback=safe_traceback,
            report_policy="final_only",
        )
        judge_failed = isinstance(exc, JudgeExecutionFailedError)
        final_report = post_failure_report(
            step_reporter,
            task_id=req.task_id,
            step_id=req.step_id,
            summary="Diagnose运行失败：Judge执行失败" if judge_failed else "Diagnose运行失败",
            error=safe_error,
            error_code=(
                "DIAGNOSE_JUDGE_EXECUTION_FAILED"
                if judge_failed
                else "DIAGNOSE_PIPELINE_FAILED"
            ),
        )
        return DiagnoseCommandResult(
            1,
            error_payload(
                req,
                safe_error,
                clawweb_upload={"final": final_report},
            ),
        )


def _merge_unknown_message_args(args: Any, unknown: list[str]) -> tuple[Any, str]:
    unsupported_args = unsupported_flag_args(unknown)
    if unknown:
        rendered = " ".join(str(arg) for arg in unknown)
        if unsupported_args:
            return args, (
                "unsupported_argument: use only supported flags; natural-language intent "
                "must be provided with --intent; use --task-id/--step-id for ClawWeb ids, "
                "--judge-backend to select subagent/api, --api-key (or OPENAI_API_KEY) for the API judge, --model for model name, "
                "--debug-session-path for single-session debugging, --openclaw-home for local "
                ".openclaw fixture testing, and --skip-clawweb-report for local no-network runs."
            )
        return args, (
            "unsupported_argument: positional arguments are not accepted; provide all "
            f"natural-language intent with --intent (received: {rendered!r})"
        )
    return args, ""


def _log_command_start(args: Any, *, task_id: str, step_id: str, output_dir: Any) -> None:
    diag_logger.info(
        "cli invocation parsed",
        task_id=task_id,
        step_id=step_id,
        output_dir=output_dir,
        llm_base_url=args.llm_base_url or "code_default",
        model=args.model,
        max_sessions=args.max_sessions,
        debug_session_path=getattr(args, "debug_session_path", ""),
        openclaw_home=getattr(args, "openclaw_home", "") or "~/.openclaw",
        judge_backend=getattr(args, "judge_backend", "") or "implicit",
        judge_selection_policy="explicit_backend_else_api_key_else_subagent",
        api_key_configured=bool(args.api_key),
        api_key_source=getattr(args, "_api_key_source", "cli" if args.api_key else "missing"),
        api_key_had_bearer_prefix=str(args.api_key or "").strip().lower().startswith("bearer "),
        skip_clawweb_report=bool(getattr(args, "skip_clawweb_report", False)),
        intent_source=_intent_source(args),
        intent_chars=len(_resolve_intent(args)),
    )


def _resolve_api_key_for_run(cli_api_key: str) -> tuple[str, str]:
    """Resolve API key without persisting or logging the secret value.

    ``--api-key "$OPENAI_API_KEY"`` remains the primary path.  The fallback is
    intentionally narrow: if the CLI value is empty (for example an empty quoted
    shell expansion), use the current process environment.  This makes diagnose
    robust across wrapper scripts while still keeping the key out of artifacts.
    """

    value = str(cli_api_key or "")
    if value:
        return value, "cli:--api-key"
    env_value = os.environ.get("OPENAI_API_KEY", "")
    if env_value:
        return env_value, "env:OPENAI_API_KEY"
    return "", "missing"


def _resolve_intent(args: Any) -> str:
    """Return the sole supported natural-language input: ``--intent``."""

    return str(getattr(args, "intent", "") or "").strip()


def _intent_source(args: Any) -> str:
    return "flag" if _resolve_intent(args) else "empty"


def _build_run_request(args: Any, *, task_id: str, step_id: str, output_dir: Any) -> RunRequest:
    api_key = args.api_key
    message = scrub_message_secrets(
        normalize_invocation_message(_resolve_intent(args)), api_key
    )
    return RunRequest(
        api_key=api_key,
        message=message,
        output_dir=output_dir,
        task_id=task_id,
        step_id=step_id,
        llm_base_url=args.llm_base_url,
        model=args.model,
        max_sessions=args.max_sessions if args.max_sessions > 0 else None,
        debug_session_path=getattr(args, "debug_session_path", ""),
        openclaw_home=getattr(args, "openclaw_home", ""),
        judge_backend=getattr(args, "judge_backend", ""),
        session_source=getattr(args, "source", "local"),
        source_user_id=getattr(args, "source_user_id", ""),
        source_bot_id=getattr(args, "source_bot_id", ""),
        source_download_network=getattr(args, "source_download_network", "office"),
        clawweb_url=getattr(args, "clawweb_url", ""),
    )


def _skipped_step_reporter(
    task_id: str,
    step_id: str,
    *,
    status: str,
    summary: str = "",
    output: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    error: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Local no-network step reporter used by --skip-clawweb-report."""

    diag_logger.info(
        "clawweb step report skipped",
        task_id=task_id,
        step_id=step_id,
        status=status,
        summary=summary,
        has_output=output is not None,
        has_progress=progress is not None,
        has_error=bool(error),
        reason="--skip-clawweb-report",
    )
    return {
        "status": "skipped",
        "reason": "--skip-clawweb-report",
        "reported_status": status,
    }
