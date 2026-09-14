from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import logger
from ..integration.clawweb import post_step_report
from ..input.contract import PlanSourceError
from ..bench.template_builder import ensure_split_packages
from ..io import atomic_write_text, load_json
from ..input.resolver import PlanSourceResolution, resolve_plan_source
from ..io import find_plan_source
from .artifacts import _skip_final_artifacts
from .common import _write_json
from .fresh import run_fresh_plan
from .generation import begin_generation
from .invocation import build_invocation_identity
from .existing import (
    _existing_plan_result,
    _existing_reusable_clawweb_upload_result,
)
from .upload import (
    dual_domain_meta,
    skipped_dual_domain_upload_result,
    upload_bench_domains,
    upload_result_is_complete,
)
from .paths import output_dirs, resolve_run_dir, validate_step_id, validate_task_id
from .reporting import (
    _existing_step_report_output,
    _log_step_report_done,
    FinalStepReportGate,
    final_step_report_is_complete,
    submit_final_step_report,
)
from ..spec.contract import validate_objective_markdown, validate_spec_markdown
from ..spec.renderer import render_goal_markdown, render_markdown

StepReporter = Callable[..., dict[str, Any]]
PlanSourceResolver = Callable[..., PlanSourceResolution]


@dataclass(frozen=True)
class PlanCommandResult:
    exit_code: int
    payload: dict[str, Any]


def run_plan_command(
    args: argparse.Namespace,
    *,
    step_reporter: StepReporter = post_step_report,
    plan_source_resolver: PlanSourceResolver = resolve_plan_source,
    secrets: list[str] | None = None,
) -> PlanCommandResult:
    """Execute clawevolve-plan after CLI parsing.

    This is the single orchestration boundary for plan.  The CLI owns only
    argument parsing/printing; this runner owns validation, local artifacts,
    ClawBench upload, ClawWeb step reports, and failure reporting.
    """

    task_id = ""
    step_id = ""
    output_dir: Path | None = None
    secrets = secrets or []
    if getattr(args, "skip_clawweb_report", False):
        step_reporter = _skipped_step_reporter
    final_report_gate = FinalStepReportGate(step_reporter)
    try:
        task_id = validate_task_id(args.task_id)
        step_id = validate_step_id(args.step_id)
        evolve_results_dir = getattr(args, "evolve_results_dir", "") or ""
        run_root_dir, input_dir, output_dir = output_dirs(task_id, evolve_results_dir)
        logger.configure(output_dir / "clawevolve-plan.log", secrets=secrets)
        logger.info(
            "plan output layout ready",
            input_dir=input_dir,
            output_dir=output_dir,
            log_file=logger.log_path(),
        )
        logger.info(
            "plan start",
            run_dir=resolve_run_dir(args.run_dir, task_id, evolve_results_dir),
            task_id=task_id,
            step_id=step_id,
            run_root_dir=run_root_dir,
            input_dir=input_dir,
            output_dir=output_dir,
            target_count=len(args.target),
            overwrite=args.overwrite,
            skip_clawweb_report=bool(getattr(args, "skip_clawweb_report", False)),
        )
        # ClawWeb currently accepts only the final Plan report. Domain creation and
        # template publication happen before that single terminal callback.

        args.plan_source_path = ""
        local_source = find_plan_source(
            Path(resolve_run_dir(args.run_dir, task_id, evolve_results_dir))
        )
        explicit_goal = str(getattr(args, "goal", "") or "").strip()
        if local_source is not None or not explicit_goal:
            resolution = plan_source_resolver(
                task_id=task_id,
                step_id=step_id,
                evolve_results_dir=getattr(args, "evolve_results_dir", "") or "",
                local_source_path=str(local_source or ""),
                allow_network=not bool(
                    getattr(args, "skip_clawweb_report", False)
                ),
            )
            args.plan_source_path = str(resolution.source_path)
            logger.info(
                "plan source resolved",
                status=resolution.status,
                digest=resolution.digest,
                source_path=resolution.source_path,
                descriptor_path=resolution.descriptor_path,
                producer=(resolution.source.get("source") or {}).get("type"),
            )

        invocation_identity = build_invocation_identity(args, task_id=task_id)
        logger.info(
            "plan invocation identity resolved",
            input_mode=invocation_identity.get("input_mode"),
            fingerprint=invocation_identity.get("fingerprint"),
        )

        if not args.overwrite:
            existing = _handle_existing_plan(
                args=args,
                task_id=task_id,
                step_id=step_id,
                output_dir=output_dir,
                step_reporter=final_report_gate,
                invocation_identity=invocation_identity,
            )
            if existing is not None:
                return PlanCommandResult(
                    0 if existing.get("status") == "already_exists" else 2,
                    existing,
                )

        begin_generation(
            output_dir,
            invocation_identity=invocation_identity,
        )
        result = run_fresh_plan(
            args=args,
            task_id=task_id,
            step_id=step_id,
            input_dir=input_dir,
            output_dir=output_dir,
            step_reporter=final_report_gate,
            invocation_identity=invocation_identity,
        )
        return PlanCommandResult(0 if result.get("status") == "ok" else 2, result)
    except Exception as exc:  # noqa: BLE001 - command boundary returns parseable JSON.
        payload = _handle_plan_failure(
            exc,
            task_id=task_id,
            step_id=step_id,
            output_dir=output_dir,
            step_reporter=final_report_gate,
        )
        return PlanCommandResult(2, payload)


def _handle_existing_plan(
    *,
    args: argparse.Namespace,
    task_id: str,
    step_id: str,
    output_dir: Path,
    step_reporter: StepReporter,
    invocation_identity: dict[str, Any],
) -> dict[str, Any] | None:
    existing = _existing_plan_result(
        output_dir, expected_identity=invocation_identity
    )
    if not existing:
        return None
    logger.info(
        "existing plan detected",
        spec_md=existing.get("spec_md"),
        objective_md=existing.get("objective_md"),
    )
    upload_result = existing.get("clawweb_upload_result") or {}
    manifest: dict[str, Any] | None = None
    if upload_result_is_complete(upload_result) and not getattr(
        args, "skip_clawweb_report", False
    ):
        manifest = ensure_split_packages(
            output_dir,
            output_dir / "templates",
            load_json(output_dir / "clawbench_manifest.json"),
        )
        reusable = _existing_reusable_clawweb_upload_result(
            output_dir, manifest, revalidate_remote=True
        )
        if reusable is None:
            logger.warning(
                "existing dual-domain cache is stale; starting upload repair",
                output_dir=output_dir,
            )
            upload_result = {}
            existing["clawweb_upload_result"] = upload_result
        else:
            upload_result = reusable
            existing["clawweb_upload_result"] = upload_result

    if not upload_result_is_complete(upload_result) and not getattr(
        args, "skip_clawweb_report", False
    ):
        logger.info(
            "existing local plan requires dual-domain upload repair",
            output_dir=output_dir,
        )
        if manifest is None:
            manifest_path = output_dir / "clawbench_manifest.json"
            manifest = ensure_split_packages(
                output_dir, output_dir / "templates", load_json(manifest_path)
            )
        spec = load_json(output_dir / "spec-v0.json")
        objective = load_json(output_dir / "objective.json")
        plan_context = {
            "bot_id": spec.get("bot_id") or objective.get("bot_id") or "current-bot",
            "agent_context": {},
        }
        upload_result = upload_bench_domains(
            plan=plan_context, manifest=manifest, task_id=task_id
        )
        upload_result["required"] = True
        _write_json(output_dir / "clawweb_upload_result.json", upload_result)
        domain_meta = dual_domain_meta(upload_result)
        spec.setdefault("deliverables", {})["clawweb_domain"] = domain_meta
        spec.setdefault("deliverables", {})["clawweb_domains"] = domain_meta
        objective["clawweb_domain"] = domain_meta
        objective["clawweb_domains"] = domain_meta
        _write_json(output_dir / "spec-v0.json", spec)
        _write_json(output_dir / "objective.json", objective)
        objective_md = render_goal_markdown(objective)
        spec_md = render_markdown(spec)
        validate_objective_markdown(objective_md, objective)
        validate_spec_markdown(spec_md, spec)
        atomic_write_text(output_dir / "objective.md", objective_md)
        atomic_write_text(output_dir / "spec-v0.md", spec_md)
        existing["clawweb_upload_result"] = upload_result
        existing["ready_for_patch_loop"] = upload_result_is_complete(upload_result)
        existing["status"] = (
            "already_exists"
            if existing["ready_for_patch_loop"]
            else "upload_incomplete"
        )
        existing["agent_next_action"] = (
            "use_existing_plan"
            if existing["ready_for_patch_loop"]
            else "retry_clawweb_upload"
        )
    elif getattr(args, "skip_clawweb_report", False):
        manifest = ensure_split_packages(
            output_dir,
            output_dir / "templates",
            load_json(output_dir / "clawbench_manifest.json"),
        )
        upload_result = skipped_dual_domain_upload_result(manifest=manifest)
        upload_result["required"] = False
        existing["clawweb_upload_result"] = upload_result
        existing["ready_for_patch_loop"] = True
        existing["status"] = "already_exists"
    if not existing.get("oss_upload_result"):
        logger.info(
            "existing plan missing oss upload result; final artifact publish skipped",
            output_dir=output_dir,
        )
        oss_result = _skip_final_artifacts(task_id=task_id, output_dir=output_dir)
        existing["oss_upload_result"] = oss_result
        logger.info(
            "existing plan final artifact publish skip recorded",
            status=oss_result.get("status"),
            reason=oss_result.get("reason"),
        )
    existing_report_output = _existing_step_report_output(
        output_dir, existing.get("clawweb_upload_result") or {}
    )
    upload_complete = upload_result_is_complete(
        existing.get("clawweb_upload_result") or {}
    )
    report_status = (
        "succeeded"
        if upload_complete or getattr(args, "skip_clawweb_report", False)
        else "failed"
    )
    final_report_result = submit_final_step_report(
        step_reporter,
        task_id,
        step_id,
        status=report_status,
        summary=(
            "完成优化目标、Bench 规划及 train/test Domain 发布"
            if report_status == "succeeded"
            else "Plan 本地产物完整，但 train/test Bench Domain 未完整发布或验证"
        ),
        output=existing_report_output,
        error=None
        if report_status == "succeeded"
        else {"code": "CLAWBENCH_DOMAIN_PUBLISH_INCOMPLETE"},
    )
    _log_step_report_done(
        "clawweb final step report done",
        phase="existing_final",
        result=final_report_result,
    )
    existing["clawweb_step_report"] = {"final": final_report_result}
    report_complete = final_step_report_is_complete(existing["clawweb_step_report"])
    if not getattr(args, "skip_clawweb_report", False) and not report_complete:
        existing["status"] = "report_incomplete"
        existing["ready_for_patch_loop"] = False
        existing["agent_next_action"] = (
            "retry_clawweb_report" if upload_complete else "retry_clawweb_upload"
        )
    _write_json(
        output_dir / "clawweb_step_report_result.json",
        existing["clawweb_step_report"],
    )
    return existing


def _handle_plan_failure(
    exc: Exception,
    *,
    task_id: str,
    step_id: str,
    output_dir: Path | None,
    step_reporter: StepReporter,
) -> dict[str, Any]:
    safe_error = f"{type(exc).__name__}: {exc}"
    error_detail = exc.report_error() if isinstance(exc, PlanSourceError) else None
    logger.error("plan failed", error=safe_error)
    clawweb_step_report = None
    failure_report_error = ""
    if task_id and step_id:
        if isinstance(step_reporter, FinalStepReportGate) and step_reporter.attempted:
            final_report_result = step_reporter.result or {
                "status": "unknown",
                "reason": "terminal_report_already_attempted_without_result",
            }
            clawweb_step_report = {
                "final": final_report_result,
                "post_report_local_error": safe_error,
            }
            logger.warning(
                "plan failed after terminal report; duplicate failure report suppressed",
                task_id=task_id,
                step_id=step_id,
                error=safe_error,
            )
        else:
            logger.info(
                "clawweb failure step report start",
                task_id=task_id,
                step_id=step_id,
                status="failed",
            )
            final_report_result = submit_final_step_report(
                step_reporter,
                task_id,
                step_id,
                status="failed",
                summary="Plan运行失败",
                error=error_detail if error_detail is not None else safe_error,
            )
            _log_step_report_done(
                "clawweb failure step report done",
                phase="failure_final",
                result=final_report_result,
            )
            clawweb_step_report = {"final": final_report_result}
            if str(final_report_result.get("status") or "").lower() == "error":
                failure_report_error = str(final_report_result.get("error") or "")
        if output_dir is not None:
            try:
                _write_json(
                    output_dir / "clawweb_step_report_result.json", clawweb_step_report
                )
            except Exception as write_exc:  # noqa: BLE001 - preserve the primary failure.
                write_error = f"{type(write_exc).__name__}: {write_exc}"
                failure_report_error = "; ".join(
                    value for value in (failure_report_error, write_error) if value
                )
                logger.error(
                    "write failure step report result failed", error=write_error
                )
    payload = {
        "status": "error",
        "agent_next_action": "inspect_warnings",
        "task_id": task_id,
        "step_id": step_id,
        "error": safe_error,
        "warnings": [safe_error],
        "clawweb_step_report": clawweb_step_report,
        "log_file": logger.log_path(),
    }
    if error_detail is not None:
        payload["error_detail"] = error_detail
    if failure_report_error:
        payload["failure_report_error"] = failure_report_error
        payload["warnings"].append(
            "Primary Plan failure was preserved, but failure reporting also failed: "
            + failure_report_error
        )
    return payload


def _skipped_step_reporter(
    task_id: str,
    step_id: str,
    *,
    status: str,
    summary: str = "",
    output: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    error: str | dict[str, Any] = "",
) -> dict[str, Any]:
    """Local no-network step reporter used by --skip-clawweb-report."""

    logger.info(
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
