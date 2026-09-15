from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from .. import logger
from ..direct_goal import build_direct_goal_plan
from ..discovery import run_auto_discovery
from ..io import load_json, read_discovery_notes
from ..input.context import build_planning_context
from .artifacts import _skip_final_artifacts
from .generation import complete_generation
from .bench_flow import prepare_bench_artifacts
from .common import _read_text_file, _write_json
from .inputs import (
    _archive_inputs,
    _archived_input_path,
    _discovery_notes_source,
    _validate_discovery_inputs,
    _validate_planning_context,
)
from .paths import resolve_run_dir
from .reporting import (
    _log_step_report_done,
    _result,
    _step_report_output,
    submit_final_step_report,
)
from .spec_flow import build_and_write_spec
from .upload import upload_result_is_complete

StepReporter = Callable[..., dict[str, Any]]


def run_fresh_plan(
    *,
    args: argparse.Namespace,
    task_id: str,
    step_id: str,
    input_dir: Path,
    output_dir: Path,
    step_reporter: StepReporter,
    invocation_identity: dict[str, Any],
) -> dict[str, Any]:
    """Generate plan artifacts from Source, Diagnose, or Direct Goal input."""

    plan, input_archive, notes_path, discovery_notes, plan_path = (
        _load_and_validate_inputs(
            args, input_dir, output_dir, invocation_identity=invocation_identity
        )
    )
    template_dir, zip_path, template_names, template_manifest, upload_result = (
        prepare_bench_artifacts(
            args, plan, output_dir, discovery_notes=discovery_notes, task_id=task_id
        )
    )

    (
        spec,
        objective_doc,
        objective_md_path,
        objective_json_path,
        spec_md_path,
        spec_json_path,
    ) = build_and_write_spec(
        args=args,
        plan=plan,
        discovery_notes=discovery_notes,
        input_archive=input_archive,
        plan_path=plan_path,
        output_dir=output_dir,
        task_id=task_id,
    )

    oss_upload_result = _skip_final_artifacts(task_id=task_id, output_dir=output_dir)
    plan.setdefault("artifacts", {})["oss_upload_result"] = str(
        output_dir / "oss_upload_result.json"
    )
    logger.info(
        "final artifact publish skip recorded",
        status=oss_upload_result.get("status"),
        reason=oss_upload_result.get("reason"),
    )

    step_report_output = _step_report_output(
        spec,
        objective_doc,
        template_manifest,
        upload_result,
        _read_text_file(spec_md_path),
    )
    upload_required = bool(upload_result.get("required", True))
    upload_complete = upload_result_is_complete(upload_result)
    final_status = "succeeded" if upload_complete or not upload_required else "failed"
    final_summary = (
        "完成优化目标、Bench 规划及 train/test Domain 发布"
        if final_status == "succeeded"
        else "Plan 文档已生成，但 train/test Bench Domain 未完整发布或验证"
    )
    logger.info(
        "clawweb final step report start",
        task_id=task_id,
        step_id=step_id,
        status=final_status,
        payload_keys=list(step_report_output.keys()),
    )
    final_report_result = submit_final_step_report(
        step_reporter,
        task_id,
        step_id,
        status=final_status,
        summary=final_summary,
        output=step_report_output,
        error=None
        if final_status == "succeeded"
        else {
            "code": "CLAWBENCH_DOMAIN_PUBLISH_INCOMPLETE",
            "message": final_summary,
            "uploadStatus": upload_result.get("status"),
        },
    )
    clawweb_step_report = {"final": final_report_result}
    _write_json(output_dir / "clawweb_step_report_result.json", clawweb_step_report)
    _log_step_report_done(
        "clawweb final step report done", phase="final", result=final_report_result
    )

    result = _result(
        spec,
        objective_md_path,
        objective_json_path,
        spec_md_path,
        spec_json_path,
        template_dir,
        zip_path,
        upload_result,
        oss_upload_result,
        input_archive,
        clawweb_step_report,
    )
    complete_generation(output_dir, invocation_identity=invocation_identity)
    logger.info(
        "plan done",
        objective_md=objective_md_path,
        objective_json=objective_json_path,
        spec_md=spec_md_path,
        spec_json=spec_json_path,
        template_dir=template_dir,
        zip_path=zip_path,
        input_dir=input_dir,
        output_dir=output_dir,
        discovery_notes=notes_path,
        input_manifest=output_dir / "input_manifest.json",
        template_count=len(template_names),
        clawweb_upload_status=upload_result.get("status"),
        clawweb_step_report_status=final_report_result.get("status"),
        oss_upload_status=oss_upload_result.get("status"),
        log_file=logger.log_path(),
    )
    return result


def _load_and_validate_inputs(
    args: argparse.Namespace,
    input_dir: Path,
    output_dir: Path,
    *,
    invocation_identity: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str, str, Path]:
    if invocation_identity is None:
        from .invocation import build_invocation_identity

        invocation_identity = build_invocation_identity(
            args, task_id=str(getattr(args, "task_id", "") or "local-plan")
        )

    resolved_run_dir = resolve_run_dir(
        args.run_dir, args.task_id, getattr(args, "evolve_results_dir", "") or ""
    )
    resolved_source_arg = str(getattr(args, "plan_source_path", "") or "").strip()
    logger.info(
        "plan source mode resolution",
        run_dir=resolved_run_dir,
        explicit_plan_source=bool(resolved_source_arg),
        goal_chars=len(str(getattr(args, "goal", "") or "").strip()),
    )

    if resolved_source_arg:
        source_plan_path = Path(resolved_source_arg)
        plan_label = "plan_source"
    else:
        direct = build_direct_goal_plan(
            goal=str(getattr(args, "goal", "") or ""),
            task_id=args.task_id,
            bot_id=str(getattr(args, "bot_id", "") or ""),
            input_dir=input_dir,
        )
        source_plan_path = direct.plan_path
        plan_label = "plan_source"
        args.discovery_notes = str(direct.notes_path)
        args.target = list(direct.targets)
        logger.info(
            "direct goal inputs injected",
            plan_path=source_plan_path,
            discovery_notes=direct.notes_path,
            discovery_json=direct.discovery_json_path,
            case_count=len(direct.plan.get("cases") or []),
            target_count=len(direct.targets),
            warnings=direct.warnings,
        )

    if not source_plan_path.is_file():
        raise ValueError(f"Resolved Plan input does not exist: {source_plan_path}")
    logger.info(
        "plan source selected",
        source_plan_path=source_plan_path,
        plan_label=plan_label,
        invocation_identity=invocation_identity,
    )

    logger.info("load plan source start", plan_path=source_plan_path)
    source_plan = load_json(source_plan_path)
    validated_plan = build_planning_context(source_plan, source_plan_path)
    input_mode = str(validated_plan.get("input_mode") or "")
    _validate_planning_context(validated_plan, source_plan_path)
    logger.info(
        "load plan source done",
        plan_path=source_plan_path,
        input_mode=input_mode,
        schema_version=validated_plan.get("schema_version", ""),
        case_count=len(validated_plan.get("cases") or []),
        cluster_count=len(validated_plan.get("root_cause_clusters") or []),
    )

    has_notes = bool(str(getattr(args, "discovery_notes", "") or "").strip())
    has_targets = bool(
        [
            str(target).strip()
            for target in getattr(args, "target", [])
            if str(target).strip()
        ]
    )
    if input_mode != "direct_goal" and (not has_notes or not has_targets):
        discovery = run_auto_discovery(
            plan=validated_plan,
            plan_path=source_plan_path,
            input_dir=input_dir,
            task_id=args.task_id,
        )
        args.discovery_notes = str(discovery.notes_path)
        args.target = discovery.targets
        logger.info(
            "auto discovery inputs injected",
            input_mode=input_mode,
            discovery_notes=args.discovery_notes,
            discovery_json=discovery.discovery_json_path,
            target_count=len(args.target),
            warnings=discovery.warnings,
        )

    logger.info(
        "archive plan source inputs start",
        input_mode=input_mode,
        input_dir=input_dir,
        source_plan_path=source_plan_path,
        discovery_notes=_discovery_notes_source(args.discovery_notes),
    )
    input_archive = _archive_inputs(
        input_dir,
        output_dir,
        source_plan_path,
        args.discovery_notes,
        plan_label=plan_label,
        invocation_identity=invocation_identity,
    )
    logger.info(
        "archive plan source inputs done",
        input_mode=input_mode,
        input_dir=input_archive.get("input_dir"),
        output_dir=input_archive.get("output_dir"),
        archived_count=len(input_archive.get("items") or []),
    )

    plan_path = Path(_archived_input_path(input_archive, plan_label))
    notes_path = _archived_input_path(input_archive, "discovery_notes")

    logger.info("load archived plan source start", plan_path=plan_path)
    loaded = load_json(plan_path)
    plan = build_planning_context(loaded, plan_path)
    _validate_planning_context(plan, plan_path)
    logger.info(
        "load archived plan source done",
        input_mode=input_mode,
        plan_path=plan_path,
        schema_version=plan.get("schema_version", ""),
        case_count=len(plan.get("cases") or []),
        cluster_count=len(plan.get("root_cause_clusters") or []),
    )

    logger.info("read archived discovery notes start", discovery_notes=notes_path)
    discovery_notes = read_discovery_notes(notes_path) if notes_path else ""
    logger.info(
        "read archived discovery notes done",
        path=notes_path,
        char_count=len(discovery_notes),
        preview=discovery_notes[:300],
    )
    _validate_discovery_inputs(discovery_notes, args.target, plan)
    logger.info(
        "discovery preflight passed",
        input_mode=input_mode,
        target_count=len(args.target),
    )
    return plan, input_archive, notes_path, discovery_notes, plan_path
