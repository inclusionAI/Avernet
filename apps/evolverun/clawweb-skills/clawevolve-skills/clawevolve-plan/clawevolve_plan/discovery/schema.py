from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORBIDDEN_PARTS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "clawevolve-skills",
    "judge",
    "judges",
    "scorer",
    "scorers",
    "secrets",
    ".secrets",
}

FORBIDDEN_FRAGMENTS = (
    "diagnose_cases/",
    "plan/output/",
    "plan/input/",
    "templates/opt/",
    "templates/val/",
    "clawbench_dataset.zip",
    "clawweb_upload_result",
    "clawbench_manifest",
    "plan-source.json",
    "source.json",
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

BROAD_TARGETS = {
    "",
    ".",
    "./",
    "..",
    "../",
    "/",
    "~",
    "~/",
    "src",
    "repo",
    "workspace",
    "skills",
}


@dataclass(frozen=True)
class DiscoveryResult:
    raw: dict[str, Any]
    notes: str
    targets: list[str]
    warnings: list[str]


def validate_discovery_payload(
    payload: Any,
    *,
    workspace_root: Path,
    lenient_model_output: bool = False,
    allow_open_skills_creation_scope: bool = False,
) -> DiscoveryResult:
    if not isinstance(payload, dict):
        raise ValueError("discovery.json must contain a JSON object")
    _validate_required_shape(payload, lenient_model_output=lenient_model_output)
    targets = _strings(payload.get("merged_targets"))
    if not targets:
        raise ValueError("discovery.json must contain non-empty merged_targets")

    boundary = payload.get("forbidden_boundary_check") or {}
    if isinstance(boundary, dict) and boundary.get("passed") is False:
        raise ValueError("discovery forbidden_boundary_check did not pass")

    problems: list[str] = []
    safe_targets: list[str] = []
    for target in targets:
        target_problems = target_guard_problems(
            target,
            workspace_root=workspace_root,
            allow_open_skills_creation_scope=_is_open_skills_creation_scope(
                target, payload, enabled=allow_open_skills_creation_scope
            ),
        )
        if target_problems:
            problems.extend(target_problems)
        else:
            safe_targets.append(target)
    if problems:
        raise ValueError("Invalid discovery targets: " + "; ".join(problems))

    _validate_target_references(payload, targets)
    reference_files = _validate_reference_files(
        payload.get("reference_files"),
        targets=targets,
        workspace_root=workspace_root,
    )
    planned_deliverables = _validate_planned_deliverables(
        payload.get("planned_deliverables"),
        targets=targets,
        payload=payload,
        workspace_root=workspace_root,
        normalize_workspace_paths=lenient_model_output,
    )
    normalized_payload = dict(payload)
    normalized_payload["reference_files"] = reference_files
    normalized_payload["planned_deliverables"] = planned_deliverables
    notes = str(payload.get("discovery_notes") or "").strip()
    warnings = _strings(payload.get("warnings"))
    return DiscoveryResult(
        raw=normalized_payload,
        notes=notes,
        targets=safe_targets,
        warnings=warnings,
    )


def _validate_required_shape(
    payload: dict[str, Any], *, lenient_model_output: bool = False
) -> None:
    problems: list[str] = []
    if str(payload.get("schema_version") or "") != "clawevolve.plan.discovery.v1":
        problems.append("schema_version must be clawevolve.plan.discovery.v1")
    if not isinstance(payload.get("analysis_summary"), dict):
        problems.append("analysis_summary must be an object")
    case_findings = payload.get("case_findings")
    if not isinstance(case_findings, list) or not case_findings:
        problems.append("case_findings must be a non-empty list")
    else:
        for index, item in enumerate(case_findings):
            if not isinstance(item, dict):
                problems.append(f"case_findings[{index}] must be an object")
                continue
            required_keys = (
                ("case_id",)
                if lenient_model_output
                else (
                    "case_id",
                    "failure_mode",
                    "inspected_files",
                    "environment_analysis",
                    "optimization_ideas",
                )
            )
            for key in required_keys:
                if not item.get(key):
                    problems.append(f"case_findings[{index}].{key} is required")
    target_findings = payload.get("target_findings")
    if not isinstance(target_findings, list) or not target_findings:
        problems.append("target_findings must be a non-empty list")
    else:
        for index, item in enumerate(target_findings):
            if not isinstance(item, dict):
                problems.append(f"target_findings[{index}] must be an object")
                continue
            required_keys = (
                ("path",)
                if lenient_model_output
                else (
                    "path",
                    "reason",
                    "current_gap",
                    "proposed_change",
                    "related_case_ids",
                    "failure_modes",
                )
            )
            for key in required_keys:
                if not item.get(key):
                    problems.append(f"target_findings[{index}].{key} is required")
    if problems:
        raise ValueError("Invalid discovery schema: " + "; ".join(problems))


def _validate_target_references(payload: dict[str, Any], targets: list[str]) -> None:
    target_findings = payload.get("target_findings") or []
    finding_paths = {
        str(item.get("path") or "").strip()
        for item in target_findings
        if isinstance(item, dict)
    }
    missing = [target for target in targets if target not in finding_paths]
    if missing:
        raise ValueError(
            "Invalid discovery schema: merged_targets missing matching target_findings paths: "
            + ", ".join(missing)
        )


def _validate_reference_files(
    value: Any, *, targets: list[str], workspace_root: Path
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(
            "discovery reference_files must be a list of unique non-empty paths"
        )
    references = _strings(value)
    if len(references) != len(value):
        raise ValueError(
            "discovery reference_files must be a list of unique non-empty paths"
        )
    overlap = sorted(set(references) & set(targets))
    if overlap:
        raise ValueError(
            "Invalid discovery references: reference_files must not also be merged_targets: "
            + ", ".join(overlap)
        )
    problems = [
        problem
        for reference in references
        for problem in target_guard_problems(reference, workspace_root=workspace_root)
    ]
    for reference in references:
        if not _target_path(reference, workspace_root).is_file():
            problems.append(f"reference file `{reference}` must be an existing file")
    if problems:
        raise ValueError("Invalid discovery references: " + "; ".join(problems))
    return references


def _validate_planned_deliverables(
    value: Any,
    *,
    targets: list[str],
    payload: dict[str, Any],
    workspace_root: Path,
    normalize_workspace_paths: bool = False,
) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("discovery planned_deliverables must be a list")
    findings = {
        str(item.get("path") or "").strip(): item
        for item in payload.get("target_findings") or []
        if isinstance(item, dict)
    }
    normalized: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    problems: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            problems.append(f"planned_deliverables[{index}] must be an object")
            continue
        path = str(item.get("path") or "").strip()
        if normalize_workspace_paths:
            path = _workspace_relative_path(path, workspace_root=workspace_root)
        operation_default = "create" if normalize_workspace_paths else ""
        operation = str(item.get("operation") or operation_default).strip().lower()
        creation_scope = str(item.get("creation_scope") or "").strip()
        reason = str(item.get("reason") or "").strip()
        deliverable_type = (
            str(item.get("deliverable_type") or "other").strip() or "other"
        )
        if not path:
            problems.append(f"planned_deliverables[{index}].path is required")
        elif path in seen_paths:
            problems.append(f"duplicate planned deliverable path: {path}")
        else:
            seen_paths.add(path)
        if operation != "create":
            problems.append(f"planned_deliverables[{index}].operation must be create")
        if not creation_scope:
            problems.append(f"planned_deliverables[{index}].creation_scope is required")
        elif creation_scope not in targets:
            problems.append(
                f"planned_deliverables[{index}].creation_scope must be one of merged_targets"
            )
        if not reason and not normalize_workspace_paths:
            problems.append(f"planned_deliverables[{index}].reason is required")
        if creation_scope:
            finding = findings.get(creation_scope) or {}
            if str(finding.get("target_type") or "").strip() != "creation_scope":
                problems.append(
                    f"planned_deliverables[{index}].creation_scope must reference a "
                    "target_findings entry with target_type=creation_scope"
                )
            scope_path = _target_path(creation_scope, workspace_root)
            if not scope_path.is_dir():
                problems.append(
                    f"planned_deliverables[{index}].creation_scope must be an existing directory"
                )
        problems.extend(
            _planned_path_problems(
                path, creation_scope=creation_scope, workspace_root=workspace_root
            )
        )
        normalized.append(
            {
                "path": path,
                "operation": operation,
                "creation_scope": creation_scope,
                "deliverable_type": deliverable_type,
                "reason": reason or "由 Direct Goal 规划创建",
            }
        )
    if problems:
        raise ValueError("Invalid planned deliverables: " + "; ".join(problems))
    return normalized


def _workspace_relative_path(raw_path: str, *, workspace_root: Path) -> str:
    """Normalize an in-workspace absolute model path without weakening boundaries."""
    raw = str(raw_path or "").strip()
    path = Path(raw).expanduser()
    if not raw or not path.is_absolute():
        return raw
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except (OSError, ValueError):
        return raw


def _planned_path_problems(
    planned_path: str, *, creation_scope: str, workspace_root: Path
) -> list[str]:
    raw = str(planned_path or "").strip()
    normalized = raw.replace("\\", "/").strip()
    lowered = normalized.lower()
    parts = [part for part in lowered.split("/") if part]
    problems: list[str] = []
    path_obj = Path(raw).expanduser()
    if path_obj.is_absolute():
        problems.append(
            f"planned deliverable `{raw}` must use a workspace-relative path"
        )
    if lowered in BROAD_TARGETS or lowered.endswith("/..") or "/../" in lowered:
        problems.append(
            f"planned deliverable `{raw}` is too broad or uses path traversal"
        )
    if any(part in FORBIDDEN_PARTS for part in parts):
        problems.append(f"planned deliverable `{raw}` is in a forbidden boundary")
    if any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS):
        problems.append(
            f"planned deliverable `{raw}` points at generated evidence, forbidden artifacts, or secrets"
        )
    if not raw:
        return problems
    planned = _target_path(raw, workspace_root)
    if not _is_under_workspace(planned, workspace_root):
        problems.append(
            f"planned deliverable `{raw}` is outside workspace root {workspace_root}"
        )
    elif planned.exists():
        problems.append(
            f"planned deliverable `{raw}` already exists; use an update target instead"
        )
    if creation_scope:
        scope = _target_path(creation_scope, workspace_root)
        try:
            relative = planned.resolve().relative_to(scope.resolve())
            if not relative.parts:
                problems.append(
                    f"planned deliverable `{raw}` must be below creation_scope `{creation_scope}`"
                )
        except (OSError, ValueError):
            problems.append(
                f"planned deliverable `{raw}` is outside creation_scope `{creation_scope}`"
            )
    return problems


def _is_open_skills_creation_scope(
    target: str, payload: dict[str, Any], *, enabled: bool
) -> bool:
    if (
        not enabled
        or str(target or "").replace("\\", "/").strip("/") != "skills"
    ):
        return False
    findings = payload.get("target_findings") or []
    return any(
        isinstance(item, dict)
        and str(item.get("path") or "").replace("\\", "/").strip("/") == "skills"
        and str(item.get("target_type") or "").strip() == "creation_scope"
        for item in findings
    )


def target_guard_problems(
    target: str,
    *,
    workspace_root: Path,
    allow_open_skills_creation_scope: bool = False,
) -> list[str]:
    raw = str(target or "").strip()
    normalized = raw.replace("\\", "/").strip()
    lowered = normalized.lower()
    parts = [part for part in lowered.split("/") if part]
    problems: list[str] = []

    broad_target = lowered in BROAD_TARGETS and not (
        allow_open_skills_creation_scope and lowered == "skills"
    )
    if broad_target or lowered.endswith("/..") or "/../" in lowered:
        problems.append(f"target `{raw}` is too broad or uses path traversal")
    if any(part in FORBIDDEN_PARTS for part in parts):
        problems.append(f"target `{raw}` is in a forbidden boundary")
    if any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS):
        problems.append(
            f"target `{raw}` points at generated evidence, forbidden artifacts, or secrets"
        )
    target_path = _target_path(raw, workspace_root)
    root = workspace_root.expanduser().resolve()
    if not _is_under_workspace(target_path, workspace_root):
        problems.append(f"target `{raw}` is outside workspace root {workspace_root}")
    elif target_path.resolve() == root:
        problems.append(
            f"target `{raw}` is too broad because it resolves to workspace root"
        )
    elif not target_path.exists():
        problems.append(f"target `{raw}` does not exist under workspace root")
    return problems


def _target_path(target: str, workspace_root: Path) -> Path:
    root = workspace_root.expanduser().resolve()
    path = Path(target).expanduser()
    return path if path.is_absolute() else root / path


def _is_under_workspace(path: Path, workspace_root: Path) -> bool:
    root = workspace_root.expanduser().resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out
