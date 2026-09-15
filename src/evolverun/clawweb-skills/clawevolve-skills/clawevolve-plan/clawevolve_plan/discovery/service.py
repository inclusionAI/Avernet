from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
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
from ..io import atomic_write_text
from .agent import run_openclaw_agent_message
from .prompt import (
    build_discovery_prompt,
    discovery_schema_example,
    validate_discovery_prompt_size,
)
from .renderer import render_discovery_notes
from .schema import DiscoveryResult, validate_discovery_payload


@dataclass(frozen=True)
class AutoDiscoveryResult:
    notes_path: Path
    discovery_json_path: Path
    targets: list[str]
    warnings: list[str]


def run_auto_discovery(
    *,
    plan: dict[str, Any],
    plan_path: Path,
    input_dir: Path,
    task_id: str,
) -> AutoDiscoveryResult:
    workspace_root = resolve_workspace_root(plan, workspace_hint=input_dir)
    source_path = _resolve_discovery_source_path(plan_path)
    discovery_json_path = input_dir / "discovery.json"
    candidate_path = input_dir / "discovery.candidate.json"
    notes_path = input_dir / "discovery_notes.md"
    discovery_json_path.parent.mkdir(parents=True, exist_ok=True)

    reused = _try_reuse_discovery(
        discovery_json_path=discovery_json_path,
        notes_path=notes_path,
        workspace_root=workspace_root,
    )
    if reused is not None:
        return reused

    stale_candidate = quarantine_stale_artifact(
        candidate_path, reason="stale-candidate"
    )
    if stale_candidate is not None:
        logger.warning(
            "stale automatic discovery candidate quarantined",
            candidate_path=candidate_path,
            archive_path=stale_candidate,
        )

    cases = list(plan.get("cases") or [])
    clusters = list(plan.get("root_cause_clusters") or [])
    source_size_bytes = source_path.stat().st_size
    prompt = build_discovery_prompt(
        source_path=source_path,
        workspace_root=workspace_root,
        output_path=candidate_path,
        source_schema=str(plan.get("schema_version") or ""),
        source_size_bytes=source_size_bytes,
        case_count=len(cases),
        cluster_count=len(clusters),
        input_mode=str(plan.get("input_mode") or ""),
    )
    prompt_bytes = validate_discovery_prompt_size(prompt, phase="initial discovery")
    logger.info(
        "auto discovery start",
        workspace_root=workspace_root,
        discovery_json=discovery_json_path,
        transport="workflow_cli_agent",
        source_path=source_path,
        source_schema=str(plan.get("schema_version") or ""),
        source_size_bytes=source_size_bytes,
        case_count=len(cases),
        cluster_count=len(clusters),
        prompt_chars=len(prompt),
        prompt_bytes=prompt_bytes,
    )
    result = run_openclaw_agent_message(
        message=prompt,
        workspace_root=workspace_root,
        task_id=task_id,
        output_path=candidate_path,
    )
    logger.info(
        "auto discovery agent exited",
        status=result.status,
        agent_id=result.agent_id,
        session_id=result.session_id,
        response_chars=len(result.response_text or ""),
        elapsed_seconds=f"{result.elapsed_seconds:.2f}",
        failure_diagnostics=(
            _agent_failure_summary(result)
            if result.status not in {"success", "succeeded", "completed", "done", "ok"}
            else {}
        ),
    )
    try:
        artifact = _materialize_discovery_candidate(
            candidate_path=candidate_path,
            discovery_json_path=discovery_json_path,
            workspace_root=workspace_root,
            response_text=result.response_text or result.stdout_text,
            attempt=1,
        )
    except StructuredArtifactError as first_error:
        if first_error.repair_source_path is None:
            failure_summary = _agent_failure_summary(result)
            logger.error(
                "automatic discovery agent produced no candidate or response",
                task_id=task_id,
                agent_id=result.agent_id,
                session_id=result.session_id,
                failure_diagnostics=failure_summary,
            )
            raise ValueError(
                "automatic discovery agent produced no repairable structured artifact; "
                f"{first_error}; agent_diagnostics="
                f"{json.dumps(failure_summary, ensure_ascii=False, default=str)}"
            ) from first_error
        logger.warning(
            "automatic discovery artifact rejected; requesting one correction",
            task_id=task_id,
            candidate_path=candidate_path,
            error=str(first_error),
            archives=[str(path) for path in first_error.archive_paths],
        )
        correction_prompt = build_structured_artifact_correction_prompt(
            label="automatic discovery",
            candidate_path=candidate_path,
            final_path=discovery_json_path,
            validation_error=str(first_error),
            schema_example=discovery_schema_example(workspace_root),
            semantic_constraints=[
                "The canonical Plan Source remains at "
                f"{json.dumps(str(source_path), ensure_ascii=False)}. Read it only "
                "when needed to verify case IDs or Source fields; never copy its full "
                "contents into the correction response.",
                "Preserve only files and directories that the first discovery attempt actually inspected.",
                "merged_targets must remain non-empty and every target must exist inside workspace_root.",
                "Do not turn references, generated Plan artifacts, judge/scorer files, or secrets into targets.",
            ],
        )
        correction_prompt_bytes = validate_discovery_prompt_size(
            correction_prompt, phase="discovery correction"
        )
        logger.info(
            "automatic discovery correction prompt prepared",
            task_id=task_id,
            source_path=source_path,
            prompt_chars=len(correction_prompt),
            prompt_bytes=correction_prompt_bytes,
        )
        correction = run_openclaw_agent_message(
            message=correction_prompt,
            workspace_root=workspace_root,
            task_id=f"{task_id}-discovery-correction",
            output_path=candidate_path,
        )
        try:
            artifact = _materialize_discovery_candidate(
                candidate_path=candidate_path,
                discovery_json_path=discovery_json_path,
                workspace_root=workspace_root,
                response_text=correction.response_text or correction.stdout_text,
                attempt=2,
            )
        except StructuredArtifactError as correction_error:
            detail = _agent_failure_detail(correction) or _agent_failure_detail(result)
            raise ValueError(
                "automatic discovery artifact remained invalid after one bounded correction: "
                f"{correction_error}; initial_agent_status={result.status}; "
                f"correction_agent_status={correction.status}; detail={detail[-2000:]}"
            ) from correction_error

    discovery = artifact.validated
    notes = render_discovery_notes(discovery.raw, targets=discovery.targets)
    atomic_write_text(notes_path, notes)
    candidate_path.unlink(missing_ok=True)
    warnings = [*discovery.warnings, *artifact.warnings]
    logger.info(
        "auto discovery done",
        discovery_json=discovery_json_path,
        notes_path=notes_path,
        target_count=len(discovery.targets),
        artifact_source=artifact.source,
        warnings=warnings,
    )
    return AutoDiscoveryResult(
        notes_path=notes_path,
        discovery_json_path=discovery_json_path,
        targets=discovery.targets,
        warnings=warnings,
    )


def _resolve_discovery_source_path(plan_path: Path) -> Path:
    raw_path = str(plan_path)
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_path):
        raise ValueError("Plan Source path contains unsupported control characters")
    try:
        source_path = plan_path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Plan Source is not readable for Discovery: {raw_path}") from exc
    if not source_path.is_file() or not os.access(source_path, os.R_OK):
        raise ValueError(f"Plan Source is not a readable file for Discovery: {source_path}")
    return source_path


def _materialize_discovery_candidate(
    *,
    candidate_path: Path,
    discovery_json_path: Path,
    workspace_root: Path,
    response_text: str,
    attempt: int,
) -> StructuredArtifactResult[DiscoveryResult]:
    return materialize_structured_artifact(
        label="automatic discovery",
        candidate_path=candidate_path,
        final_path=discovery_json_path,
        response_text=response_text,
        validator=lambda payload: validate_discovery_payload(
            payload, workspace_root=workspace_root
        ),
        canonicalizer=lambda discovery: discovery.raw,
        attempt=attempt,
    )


def _try_reuse_discovery(
    *,
    discovery_json_path: Path,
    notes_path: Path,
    workspace_root: Path,
) -> AutoDiscoveryResult | None:
    if not discovery_json_path.exists():
        return None
    try:
        result = _load_validate_render_discovery(
            discovery_json_path=discovery_json_path,
            notes_path=notes_path,
            workspace_root=workspace_root,
            reused=True,
        )
    except Exception as exc:  # noqa: BLE001 - invalid stale discovery should be regenerated.
        archive_path = quarantine_stale_artifact(discovery_json_path, reason="stale")
        logger.warning(
            "existing discovery.json invalid and quarantined; rerun automatic discovery",
            discovery_json=discovery_json_path,
            archive_path=archive_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None
    return result


def _load_validate_render_discovery(
    *,
    discovery_json_path: Path,
    notes_path: Path,
    workspace_root: Path,
    reused: bool,
) -> AutoDiscoveryResult:
    payload = json.loads(discovery_json_path.read_text(encoding="utf-8"))
    discovery = validate_discovery_payload(payload, workspace_root=workspace_root)
    notes = render_discovery_notes(discovery.raw, targets=discovery.targets)
    atomic_write_text(notes_path, notes)
    logger.info(
        "auto discovery reused" if reused else "auto discovery done",
        discovery_json=discovery_json_path,
        notes_path=notes_path,
        target_count=len(discovery.targets),
        warnings=discovery.warnings,
    )
    return AutoDiscoveryResult(
        notes_path=notes_path,
        discovery_json_path=discovery_json_path,
        targets=discovery.targets,
        warnings=discovery.warnings,
    )


def _agent_failure_detail(result: Any) -> str:
    parts = []
    for attr in ("response_text", "stderr", "stdout"):
        try:
            value = getattr(result, attr, "")
        except Exception:
            value = ""
        if value:
            parts.append(f"{attr}={str(value)[-1200:]}")
    diagnostics = getattr(result, "diagnostics", None)
    if diagnostics:
        try:
            parts.append(
                "diagnostics="
                + json.dumps(diagnostics, ensure_ascii=False, default=str)[-2000:]
            )
        except Exception:
            parts.append("diagnostics=" + str(diagnostics)[-2000:])
    return "\n".join(parts)


def _agent_failure_summary(result: Any) -> dict[str, Any]:
    diagnostics = getattr(result, "diagnostics", None)
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    visibility_attempts = diagnostics.get("gatewayVisibilityAttempts")
    visibility_attempts = (
        visibility_attempts if isinstance(visibility_attempts, list) else []
    )
    last_visibility = (
        visibility_attempts[-1]
        if visibility_attempts and isinstance(visibility_attempts[-1], dict)
        else {}
    )
    return {
        "status": str(getattr(result, "status", "") or ""),
        "transport": str(
            diagnostics.get("selectedTransport")
            or getattr(result, "transport", "")
            or ""
        ),
        "failureCode": str(diagnostics.get("failureCode") or ""),
        "agentExecution": str(diagnostics.get("agentExecution") or ""),
        "localExitCode": diagnostics.get("localAgentExitCode"),
        "localTimedOut": bool(diagnostics.get("localAgentTimedOut")),
        "localFallbackReason": _redact_diagnostic_text(
            diagnostics.get("localFallbackReason") or ""
        ),
        "gatewayAgentVisible": diagnostics.get("gatewayAgentVisible"),
        "gatewayVisibilityAttemptCount": len(visibility_attempts),
        "gatewayVisibilityLastAttempt": {
            "exitCode": last_visibility.get("exitCode"),
            "timedOut": bool(last_visibility.get("timedOut")),
            "parseError": _redact_diagnostic_text(
                last_visibility.get("parseError") or "", limit=400
            ),
            "stderr": _redact_diagnostic_text(
                last_visibility.get("stderr") or "", limit=800
            ),
        },
        "gatewayExitCode": diagnostics.get("gatewayAgentExitCode"),
        "gatewayTimedOut": bool(diagnostics.get("gatewayAgentTimedOut")),
        "stderr": _redact_diagnostic_text(
            getattr(result, "stderr_text", "")
            or getattr(result, "stderr", "")
            or ""
        ),
    }


def _redact_diagnostic_text(value: Any, *, limit: int = 1200) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", text)
    text = re.sub(
        r"(?i)((?:api[_ -]?key|token|authorization|secret|cookie)\s*[:=]\s*)"
        r"[^\s,;]+",
        r"\1<redacted>",
        text,
    )
    return text[-limit:]


def resolve_workspace_root(
    plan: dict[str, Any], *, workspace_hint: str | Path | None = None
) -> Path:
    candidates = _workspace_candidates(plan, workspace_hint=workspace_hint)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists() and path.is_dir() and not _looks_like_plan_skill(path):
            return path.resolve()
    raise ValueError(
        "cannot infer target bot workspace for automatic discovery; "
        "Plan Source must contain agent_context.workspace/workspace_root/skill_root, "
        "or clawevolve-plan must be invoked from the target bot workspace"
    )


def _workspace_candidates(
    plan: dict[str, Any], *, workspace_hint: str | Path | None = None
) -> list[str]:
    candidates: list[str] = []
    agent_context = (
        plan.get("agent_context") if isinstance(plan.get("agent_context"), dict) else {}
    )
    for key in ("workspace_root", "workspace", "skill_root", "skill_path", "repo_root"):
        value = agent_context.get(key)
        if value:
            candidates.append(str(value))
    for key in ("workspace_root", "skill_root", "repo_root"):
        value = plan.get(key)
        if value:
            candidates.append(str(value))
    if workspace_hint:
        _append_openclaw_workspace(candidates, str(workspace_hint))
    invocation_cwd = os.environ.get("CLAWEVOLVE_INVOCATION_CWD")
    if invocation_cwd:
        _append_openclaw_workspace(candidates, invocation_cwd)
        candidates.append(invocation_cwd)
    current_cwd = os.getcwd()
    _append_openclaw_workspace(candidates, current_cwd)
    candidates.append(current_cwd)
    _append_openclaw_workspace(candidates, str(Path(__file__)))
    return candidates


def _append_openclaw_workspace(candidates: list[str], value: str) -> None:
    workspace = _openclaw_workspace_ancestor(Path(value))
    if workspace is not None and str(workspace) not in candidates:
        candidates.append(str(workspace))


def _openclaw_workspace_ancestor(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    start = resolved if resolved.is_dir() else resolved.parent
    for candidate in (start, *start.parents):
        if (
            candidate.name == "workspace"
            and candidate.parent.name == ".openclaw"
            and (candidate / "skills").is_dir()
        ):
            return candidate
    return None


def _looks_like_plan_skill(path: Path) -> bool:
    return (path / "clawevolve_plan").is_dir() and (path / "SKILL.md").is_file()


# Backward-compatible private alias for existing callers and tests.
_resolve_workspace_root = resolve_workspace_root
