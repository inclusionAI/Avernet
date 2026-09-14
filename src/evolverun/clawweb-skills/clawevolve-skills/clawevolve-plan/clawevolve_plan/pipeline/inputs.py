from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .. import logger
from ..constants import DEFAULT_DIAGNOSE_HANDOFF_DIR
from ..io import find_plan_source, load_json, _existing_file_path
from .common import _write_json

def _discovery_notes_source(value: str) -> str:
    if not value:
        return "empty"
    path = _existing_file_path(value)
    return str(path) if path else "inline_text"


def _find_plan_path_or_none(run_dir_arg: str) -> Path | None:
    run_dir = Path(run_dir_arg or DEFAULT_DIAGNOSE_HANDOFF_DIR)
    plan_path = find_plan_source(run_dir)
    return plan_path if plan_path and plan_path.exists() else None


def _find_plan_path(run_dir_arg: str) -> Path:
    run_dir = Path(run_dir_arg or DEFAULT_DIAGNOSE_HANDOFF_DIR)
    plan_path = _find_plan_path_or_none(run_dir_arg)
    if plan_path is not None:
        return plan_path
    raise ValueError(
        f"No plan-source.json found under {run_dir}. "
        "Run clawevolve-diagnose first with the same --task-id, "
        "or pass its output directory with --run-dir."
    )


def _validate_planning_context(plan: dict[str, Any], plan_path: Path) -> None:
    if not isinstance(plan, dict):
        raise ValueError(f"Invalid Planning Context at {plan_path}: expected JSON object.")
    missing = [
        key
        for key in ("schema_version", "cases", "root_cause_clusters", "artifacts")
        if key not in plan
    ]
    if missing:
        raise ValueError(
            f"Invalid Planning Context at {plan_path}: missing required fields {missing}. "
            "Regenerate the selected Plan input or upgrade clawevolve-plan."
        )
    if not isinstance(plan.get("cases"), list) or not plan.get("cases"):
        raise ValueError(
            f"Invalid Planning Context at {plan_path}: no selected cases. "
            "Regenerate the selected Plan input with at least one usable case."
        )
    if not isinstance(plan.get("root_cause_clusters"), list):
        raise ValueError(
            f"Invalid Planning Context at {plan_path}: root_cause_clusters must be a list."
        )


def _validate_discovery_inputs(
    discovery_notes: str, targets: list[str], plan: dict[str, Any]
) -> None:
    notes, concrete_targets = _validate_common_discovery_inputs(
        discovery_notes, targets
    )
    problems = [
        problem
        for target in concrete_targets
        for problem in _target_guard_problems(
            target,
            notes,
            allow_open_skills_creation_scope=_is_open_skills_creation_scope(
                target, plan
            ),
        )
    ]
    if str(plan.get("input_mode") or "").strip() == "direct_goal":
        problems.extend(_direct_goal_discovery_problems(notes, plan))
    else:
        problems.extend(_historical_source_discovery_problems(notes, plan))
    if problems:
        raise ValueError("Invalid discovery/target preflight: " + "; ".join(problems))


def _validate_common_discovery_inputs(
    discovery_notes: str, targets: list[str]
) -> tuple[str, list[str]]:
    notes = str(discovery_notes or "").strip()
    if not notes:
        raise ValueError(
            "Missing discovery notes. clawevolve-plan 必须先完成目标 workspace 检查，"
            "再生成/上传 ClawBench 数据集与 spec-v0。"
        )
    concrete_targets = [
        str(target).strip() for target in targets if str(target).strip()
    ]
    if not concrete_targets:
        raise ValueError(
            "Missing discovery target. clawevolve-plan 必须至少记录一个 Agent 已实际检查的文件或目录。"
        )
    return notes, concrete_targets


def _historical_source_discovery_problems(
    notes: str, plan: dict[str, Any]
) -> list[str]:
    modes = [
        mode
        for mode in _plan_failure_modes(plan)
        if mode.lower() != "unknown_failure_mode"
    ]
    if modes and not any(
        _searchable_term(mode) in _searchable_notes(notes) for mode in modes
    ):
        return [
            "discovery notes must mention at least one source failure mode: "
            + ", ".join(modes[:8])
        ]
    return []


def _direct_goal_discovery_problems(
    notes: str, plan: dict[str, Any]
) -> list[str]:
    problems: list[str] = []
    searchable_notes = _searchable_notes(notes)
    if "direct_goal" not in searchable_notes:
        problems.append("direct goal discovery notes must declare input mode direct_goal")
    intent = plan.get("user_intent") if isinstance(plan.get("user_intent"), dict) else {}
    goal = str(
        (plan.get("goal_context") or {}).get("raw_goal")
        if isinstance(plan.get("goal_context"), dict)
        else ""
    ).strip() or str(intent.get("intent_text") or intent.get("raw_request") or "").strip()
    if not goal:
        problems.append("direct goal plan must contain the original user goal")
    elif _searchable_term(goal) not in searchable_notes:
        problems.append("direct goal discovery notes must preserve the original user goal")
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        problems.append("direct goal plan must contain prospective cases")
    return problems


def _plan_failure_modes(plan: dict[str, Any]) -> list[str]:
    modes: list[str] = []
    for cluster in plan.get("root_cause_clusters") or []:
        if isinstance(cluster, dict):
            mode = str(cluster.get("evolution_failure_mode") or "").strip()
            if mode and mode not in modes:
                modes.append(mode)
    for case in plan.get("cases") or []:
        if isinstance(case, dict):
            mode = str(case.get("evolution_failure_mode") or "").strip()
            if mode and mode not in modes:
                modes.append(mode)
    return modes


def _is_open_skills_creation_scope(target: str, plan: dict[str, Any]) -> bool:
    return (
        os.environ.get("CLAWWEB_VERSION") == "openversion"
        and str(plan.get("input_mode") or "").strip() == "direct_goal"
        and str(target or "").replace("\\", "/").strip("/") == "skills"
        and "skills" in {
            str(scope or "").replace("\\", "/").strip("/")
            for scope in plan.get("creation_scopes") or []
        }
        and any(
            isinstance(item, dict)
            and str(item.get("operation") or "").strip().lower() == "create"
            and str(item.get("creation_scope") or "").replace("\\", "/").strip("/")
            == "skills"
            and str(item.get("path") or "").replace("\\", "/").startswith("skills/")
            for item in plan.get("planned_deliverables") or []
        )
    )


def _target_guard_problems(
    target: str,
    notes: str,
    *,
    allow_open_skills_creation_scope: bool = False,
) -> list[str]:
    raw = str(target or "").strip()
    normalized = raw.replace("\\", "/").strip()
    lowered = normalized.lower()
    parts = [p for p in lowered.split("/") if p]
    problems: list[str] = []
    broad = {".", "./", "..", "../", "/", "~", "~/"}
    broad_roots = {
        "/home/admin/.openclaw",
        "/home/admin/.openclaw/workspace",
        "/users/ant-zy/work/cases/skills",
    }
    if lowered in broad or lowered in broad_roots or lowered.endswith("/..") or "/../" in lowered:
        problems.append(f"target `{raw}` is too broad or uses path traversal")
    if len(parts) <= 1 and lowered in {
        "skills",
        "workspace",
        "repo",
        "src",
        "project",
        "openclaw",
    } and not (allow_open_skills_creation_scope and lowered == "skills"):
        problems.append(
            f"target `{raw}` is too broad; pass a concrete inspected file or narrow package"
        )
    forbidden_parts = {"judge", "judges", "scorer", "scorers", "secrets", ".secrets"}
    if any(part in forbidden_parts for part in parts):
        problems.append(
            f"target `{raw}` is in a forbidden judge/scorer/secrets boundary"
        )
    forbidden_fragments = (
        "diagnose_cases/",
        "plan/output/templates/",
        "clawbench_dataset.zip",
        "clawweb_upload_result",
        "clawbench_manifest",
        "plan-source.json",
        "/plan/input/source.json",
        "_diagnose_result.json",
        "judge_result.json",
        "objective.md",
        "objective.json",
        "spec-v0.md",
        "spec-v0.json",
        "/.env",
        "token",
        "secret",
    )
    if any(fragment in lowered for fragment in forbidden_fragments):
        problems.append(
            f"target `{raw}` points at generated evidence, forbidden artifacts, or secrets"
        )
    searchable_notes = _searchable_notes(notes)
    target_terms = {_searchable_term(lowered), _searchable_term(Path(normalized).name)}
    if not any(term and term in searchable_notes for term in target_terms):
        problems.append(f"target `{raw}` is not mentioned in discovery notes")
    return problems


def _searchable_notes(value: str) -> str:
    """Returns notes normalized for robust inline-argument guard matching.

    Shell commands copied from wrapped text can turn a token such as
    ``clawevolve-diagnose`` into ``clawevolve-\n    diagnose``.  The guard is
    intended to verify that the user inspected/mentioned the requested target,
    not to fail on harmless whitespace introduced by wrapping or indentation.
    """
    return _searchable_term(value)


def _searchable_term(value: str) -> str:
    """Normalizes a short guard term for containment checks."""
    text = str(value or "").lower()
    text = text.replace("\\n", "").replace("\\r", "").replace("\\t", "")
    return re.sub(r"\s+", "", text)


def _archive_inputs(
    input_dir: Path,
    output_dir: Path,
    plan_path: Path,
    discovery_notes_arg: str,
    *,
    plan_label: str = "plan_source",
    invocation_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, str]] = []

    def copy_one(source: Path, dest_dir: Path, label: str) -> Path | None:
        if not source.exists() or not source.is_file():
            logger.warning("archive input skipped missing file", label=label, source=source)
            return None
        dest = dest_dir / source.name
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        items.append({"label": label, "source": str(source), "path": str(dest)})
        logger.info("archive file done", label=label, source=source, dest=dest, bytes=dest.stat().st_size if dest.exists() else 0)
        return dest

    copy_one(plan_path, input_dir, plan_label)
    notes_dest = input_dir / "discovery_notes.md"
    notes_path = _existing_file_path(discovery_notes_arg or "")
    if notes_path:
        if notes_path.resolve() != notes_dest.resolve():
            shutil.copy2(notes_path, notes_dest)
        items.append({"label": "discovery_notes", "source": str(notes_path), "path": str(notes_dest)})
        logger.info(
            "archive discovery notes done",
            source=notes_path,
            dest=notes_dest,
            bytes=notes_dest.stat().st_size if notes_dest.exists() else 0,
        )
    else:
        notes_dest.write_text(str(discovery_notes_arg or ""), encoding="utf-8")
        items.append({"label": "discovery_notes", "source": "inline", "path": str(notes_dest)})
        logger.info(
            "archive inline discovery notes done",
            dest=notes_dest,
            bytes=notes_dest.stat().st_size if notes_dest.exists() else 0,
        )
    manifest = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "items": items,
        "invocation_identity": invocation_identity or {},
    }
    _write_json(output_dir / "input_manifest.json", manifest)
    return manifest


def _archived_input_path(input_archive: dict[str, Any], label: str) -> str:
    for item in input_archive.get("items") or []:
        if isinstance(item, dict) and item.get("label") == label:
            return str(item.get("path") or "")
    return ""


def _existing_archived_path(output_dir: Path, label: str) -> str:
    manifest_json = output_dir / "input_manifest.json"
    if manifest_json.exists():
        try:
            manifest = load_json(manifest_json)
            return _archived_input_path(manifest, label)
        except Exception:
            return ""
    return ""
