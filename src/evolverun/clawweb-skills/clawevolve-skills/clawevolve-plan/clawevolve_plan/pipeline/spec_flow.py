from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import logger
from ..io import write_named_outputs
from ..spec.builder import build_objective_document, build_spec, validate_discovery
from ..spec.contract import validate_objective_markdown, validate_spec_markdown
from ..spec.renderer import render_goal_markdown, render_markdown
from ..product import PlanProductService
from .inputs import _archived_input_path


def build_and_write_spec(
    *,
    args: argparse.Namespace,
    plan: dict[str, Any],
    discovery_notes: str,
    input_archive: dict[str, Any],
    plan_path: Path,
    output_dir: Path,
    task_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, Path, Path]:
    logger.info("build spec start", target_count=len(args.target))
    spec = build_spec(plan, args.goal, discovery_notes, args.target)
    logger.info(
        "build spec done",
        active_direction_count=len(spec.get("active_optimization_directions") or []),
        root_cause_count=len(spec.get("root_cause_clusters") or []),
        allowed_target_count=len(spec.get("allowed_update_targets") or []),
    )
    logger.info("validate discovery start")
    validate_discovery(spec)
    logger.info("validate discovery done")

    archived_plan_path = _archived_input_path(input_archive, "plan_source")
    spec["input_mode"] = plan.get("input_mode") or "plan_source"
    spec["input_plan_schema_version"] = plan.get("schema_version")
    spec["input_plan_path"] = archived_plan_path or (
        str(plan_path) if plan_path else ""
    )
    PlanProductService().enrich(
        plan=plan,
        spec=spec,
        goal_override=args.goal,
        source_path=spec["input_plan_path"],
    )
    logger.info(
        "validate goal fidelity done",
        status=(spec.get("goal_fidelity") or {}).get("status"),
        constraint_count=len(
            (spec.get("goal_fidelity") or {}).get("constraints") or []
        ),
        warning_count=len((spec.get("goal_fidelity") or {}).get("warnings") or []),
    )

    logger.info("build objective start", output_dir=output_dir, task_id=task_id)
    objective_doc = build_objective_document(spec)
    objective_md = render_goal_markdown(objective_doc)
    spec_md = render_markdown(spec)
    validate_objective_markdown(objective_md, objective_doc)
    validate_spec_markdown(spec_md, spec)
    document_metadata = {
        "method": "deterministic_renderer",
        "generation_method": "deterministic_renderer",
        "model_used": False,
    }
    logger.info(
        "plan documents validated", task_id=task_id, method="deterministic_renderer"
    )

    objective_doc["document_generation"] = document_metadata
    spec["document_generation"] = document_metadata
    objective_md_path, objective_json_path = write_named_outputs(
        output_dir, "objective", objective_doc, objective_md
    )
    logger.info(
        "write objective done",
        objective_md=objective_md_path,
        objective_json=objective_json_path,
    )

    logger.info("write spec start", output_dir=output_dir)
    spec_md_path, spec_json_path = write_named_outputs(
        output_dir, "spec-v0", spec, spec_md
    )
    logger.info("write spec done", spec_md=spec_md_path, spec_json=spec_json_path)
    return (
        spec,
        objective_doc,
        objective_md_path,
        objective_json_path,
        spec_md_path,
        spec_json_path,
    )
