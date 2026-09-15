from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import logger
from ..agent_artifact import (
    StructuredArtifactError,
    StructuredArtifactResult,
    build_structured_artifact_correction_prompt,
    materialize_structured_artifact,
    quarantine_stale_artifact,
)
from ..discovery.agent import run_openclaw_agent_message
from ..discovery.service import resolve_workspace_root
from ..io import atomic_write_json, atomic_write_text
from ..input.contract import (
    PLAN_SOURCE_DESCRIPTOR_VERSION,
    PLAN_SOURCE_SCHEMA_VERSION,
    digest_json,
    validate_plan_source,
)
from .prompt import build_direct_goal_prompt, direct_goal_schema_example
from .renderer import render_direct_goal_notes
from .schema import (
    SCHEMA_VERSION,
    DirectGoalPayload,
    goal_digest,
    normalize_direct_goal_source,
    validate_direct_goal_payload,
)


@dataclass(frozen=True)
class DirectGoalResult:
    plan: dict[str, Any]
    plan_path: Path
    discovery_json_path: Path
    notes_path: Path
    targets: list[str]
    warnings: list[str]


def build_direct_goal_plan(
    *,
    goal: str,
    task_id: str,
    bot_id: str,
    input_dir: Path,
) -> DirectGoalResult:
    normalized_goal = _validate_goal(goal)
    workspace_root = resolve_workspace_root({}, workspace_hint=input_dir)
    raw_path = input_dir / "direct_goal.json"
    candidate_path = input_dir / "direct_goal.candidate.json"
    plan_path = input_dir / "source.json"
    discovery_json_path = input_dir / "discovery.json"
    notes_path = input_dir / "discovery_notes.md"
    input_dir.mkdir(parents=True, exist_ok=True)

    reused = _try_reuse_direct_goal(
        raw_path=raw_path,
        plan_path=plan_path,
        discovery_json_path=discovery_json_path,
        notes_path=notes_path,
        goal=normalized_goal,
        task_id=task_id,
        bot_id=bot_id,
        workspace_root=workspace_root,
    )
    if reused is not None:
        logger.info(
            "direct goal input reused",
            task_id=task_id,
            goal_digest=goal_digest(normalized_goal),
            case_count=len(reused.plan.get("cases") or []),
            target_count=len(reused.targets),
        )
        return reused

    stale_candidate = quarantine_stale_artifact(
        candidate_path, reason="stale-candidate"
    )
    if stale_candidate is not None:
        logger.warning(
            "stale direct goal candidate quarantined",
            candidate_path=candidate_path,
            archive_path=stale_candidate,
        )

    prompt = build_direct_goal_prompt(
        goal=normalized_goal,
        task_id=task_id,
        workspace_root=workspace_root,
        output_path=candidate_path,
    )
    logger.info(
        "direct goal agent start",
        task_id=task_id,
        workspace_root=workspace_root,
        goal_chars=len(normalized_goal),
        goal_digest=goal_digest(normalized_goal),
        prompt_chars=len(prompt),
    )
    result = run_openclaw_agent_message(
        message=prompt,
        workspace_root=workspace_root,
        task_id=f"{task_id}-direct-goal",
        timeout_seconds=1200,
        output_path=candidate_path,
    )
    logger.info(
        "direct goal agent done",
        task_id=task_id,
        status=result.status,
        elapsed_seconds=f"{result.elapsed_seconds:.2f}",
        response_chars=len(result.response_text or result.stdout_text),
    )
    try:
        artifact = _materialize_direct_goal_candidate(
            candidate_path=candidate_path,
            raw_path=raw_path,
            response_text=result.response_text or result.stdout_text,
            goal=normalized_goal,
            workspace_root=workspace_root,
            attempt=1,
        )
    except StructuredArtifactError as first_error:
        if first_error.repair_source_path is None:
            raise ValueError(
                "direct goal agent produced no repairable structured artifact; "
                f"{first_error}"
            ) from first_error
        logger.warning(
            "direct goal artifact rejected; requesting one correction",
            task_id=task_id,
            candidate_path=candidate_path,
            error=str(first_error),
            archives=[str(path) for path in first_error.archive_paths],
        )
        correction_prompt = build_structured_artifact_correction_prompt(
            label="direct goal",
            response_only=True,
            candidate_path=candidate_path,
            final_path=raw_path,
            validation_error=str(first_error),
            schema_example=direct_goal_schema_example(
                goal=normalized_goal, workspace_root=workspace_root
            ),
            semantic_constraints=[
                "Preserve goal meaning, literal requested replies, numeric targets and template count; rephrasing is allowed.",
                "Do not invent historical sessions or Diagnose evidence.",
                "Preserve only targets and references actually inspected by the first attempt.",
                "Every merged target must exist inside workspace_root; future files belong only in planned_deliverables.",
                "planned_deliverables.path must be workspace-relative; do not repeat the absolute workspace_root prefix.",
            ],
        )
        correction = run_openclaw_agent_message(
            message=correction_prompt,
            workspace_root=workspace_root,
            task_id=f"{task_id}-direct-goal-correction",
            timeout_seconds=1200,
            output_path=candidate_path,
        )
        try:
            artifact = _materialize_direct_goal_candidate(
                candidate_path=candidate_path,
                raw_path=raw_path,
                response_text=correction.response_text or correction.stdout_text,
                goal=normalized_goal,
                workspace_root=workspace_root,
                attempt=2,
            )
        except StructuredArtifactError as correction_error:
            raise ValueError(
                "direct goal artifact remained invalid after one bounded correction: "
                f"{correction_error}; initial_agent_status={result.status}; "
                f"correction_agent_status={correction.status}; "
                f"diagnostics={str(correction.diagnostics)[-2000:]}"
            ) from correction_error

    candidate_path.unlink(missing_ok=True)
    logger.info(
        "direct goal structured artifact materialized",
        task_id=task_id,
        source=artifact.source,
        warnings=list(artifact.warnings),
    )
    return _materialize_result(
        raw_path=raw_path,
        plan_path=plan_path,
        discovery_json_path=discovery_json_path,
        notes_path=notes_path,
        goal=normalized_goal,
        task_id=task_id,
        bot_id=bot_id,
        workspace_root=workspace_root,
    )


def _materialize_direct_goal_candidate(
    *,
    candidate_path: Path,
    raw_path: Path,
    response_text: str,
    goal: str,
    workspace_root: Path,
    attempt: int,
) -> StructuredArtifactResult[DirectGoalPayload]:
    def bind_program_fields(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        bound = dict(payload)
        # COSEC: bind request identity from trusted inputs before validation;
        # agent output must not choose the goal or workspace it belongs to.
        bound["schema_version"] = SCHEMA_VERSION
        bound["goal_digest"] = goal_digest(goal)
        bound["workspace_root"] = str(workspace_root.resolve())
        bound["original_goal"] = goal
        discovery = bound.get("discovery")
        if isinstance(discovery, dict):
            bound_discovery = dict(discovery)
            bound_discovery["schema_version"] = "clawevolve.plan.discovery.v1"
            bound_discovery["workspace_root"] = str(workspace_root.resolve())
            bound["discovery"] = bound_discovery
        return bound

    def canonicalize(validated: DirectGoalPayload) -> dict[str, Any]:
        payload = dict(validated.raw)
        payload["discovery"] = validated.discovery.raw
        payload.setdefault(
            "generated_at",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        return payload

    return materialize_structured_artifact(
        label="direct goal",
        candidate_path=candidate_path,
        final_path=raw_path,
        response_text=response_text,
        validator=lambda payload: validate_direct_goal_payload(
            bind_program_fields(payload), goal=goal, workspace_root=workspace_root
        ),
        canonicalizer=canonicalize,
        attempt=attempt,
    )


def _validate_goal(goal: str) -> str:
    normalized = str(goal or "").strip()
    if not normalized:
        raise ValueError(
            "No local Plan Source found; --goal is required for Direct Goal generation."
        )
    return normalized


def _try_reuse_direct_goal(
    *,
    raw_path: Path,
    plan_path: Path,
    discovery_json_path: Path,
    notes_path: Path,
    goal: str,
    task_id: str,
    bot_id: str,
    workspace_root: Path,
) -> DirectGoalResult | None:
    if not raw_path.is_file():
        return None
    try:
        return _materialize_result(
            raw_path=raw_path,
            plan_path=plan_path,
            discovery_json_path=discovery_json_path,
            notes_path=notes_path,
            goal=goal,
            task_id=task_id,
            bot_id=bot_id,
            workspace_root=workspace_root,
        )
    except Exception as exc:  # noqa: BLE001 - stale input should be regenerated.
        archive_path = quarantine_stale_artifact(raw_path, reason="stale")
        logger.warning(
            "existing direct goal input invalid and quarantined; regenerate",
            task_id=task_id,
            raw_path=raw_path,
            archive_path=archive_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def _materialize_result(
    *,
    raw_path: Path,
    plan_path: Path,
    discovery_json_path: Path,
    notes_path: Path,
    goal: str,
    task_id: str,
    bot_id: str,
    workspace_root: Path,
) -> DirectGoalResult:
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    validated = validate_direct_goal_payload(
        payload, goal=goal, workspace_root=workspace_root
    )
    plan = normalize_direct_goal_source(validated, task_id=task_id, bot_id=bot_id)
    validate_plan_source(plan)
    atomic_write_json(plan_path, plan)
    atomic_write_json(
        plan_path.with_name("source-descriptor.json"),
        {
            "descriptorVersion": PLAN_SOURCE_DESCRIPTOR_VERSION,
            "sourceType": plan["source"]["type"],
            "schemaVersion": PLAN_SOURCE_SCHEMA_VERSION,
            "digest": digest_json(plan),
            "delivery": {"type": "inline"},
        },
    )
    atomic_write_json(discovery_json_path, validated.discovery.raw)
    atomic_write_text(
        notes_path,
        render_direct_goal_notes(validated.raw, targets=validated.discovery.targets),
    )
    logger.info(
        "direct goal input materialized",
        task_id=task_id,
        plan_path=plan_path,
        discovery_json=discovery_json_path,
        notes_path=notes_path,
        case_count=len(plan.get("cases") or []),
        cluster_count=len((plan.get("analysis") or {}).get("root_cause_clusters") or []),
        target_count=len(validated.discovery.targets),
    )
    return DirectGoalResult(
        plan=plan,
        plan_path=plan_path,
        discovery_json_path=discovery_json_path,
        notes_path=notes_path,
        targets=validated.discovery.targets,
        warnings=validated.discovery.warnings,
    )
