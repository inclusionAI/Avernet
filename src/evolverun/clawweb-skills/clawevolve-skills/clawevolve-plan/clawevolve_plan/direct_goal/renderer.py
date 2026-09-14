from __future__ import annotations

from typing import Any


def render_direct_goal_notes(payload: dict[str, Any], *, targets: list[str]) -> str:
    analysis = (
        payload.get("goal_analysis")
        if isinstance(payload.get("goal_analysis"), dict)
        else {}
    )
    discovery = (
        payload.get("discovery") if isinstance(payload.get("discovery"), dict) else {}
    )
    lines = [
        "# Discovery Notes",
        "",
        "## Input Mode",
        "- direct_goal",
        "",
        "## User Goal",
        f"- {analysis.get('raw_goal') or ''}",
        "",
        "## Goal Interpretation",
        f"- Task scope: {analysis.get('task_scope') or ''}",
        f"- Desired outcome: {analysis.get('desired_outcome') or ''}",
        "",
        "## Prospective Case Findings",
    ]
    for item in discovery.get("case_findings") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- `{item.get('case_id') or 'unknown_case'}` / "
            f"`{item.get('failure_mode') or 'unknown_failure_mode'}`："
            f"{item.get('environment_analysis') or item.get('symptom') or ''}"
        )
    lines.extend(["", "## Allowed Operation Scopes"])
    findings = {
        str(item.get("path") or "").strip(): item
        for item in discovery.get("target_findings") or []
        if isinstance(item, dict)
    }
    for target in targets:
        item = findings.get(target, {})
        lines.append(
            f"- `{target}`：{item.get('reason') or ''}；"
            f"current_gap={item.get('current_gap') or ''}；"
            f"proposed_change={item.get('proposed_change') or ''}"
        )
    references = [
        str(item).strip()
        for item in discovery.get("reference_files") or []
        if str(item).strip()
    ]
    lines.extend(["", "## Read-only Reference Files"])
    if references:
        lines.extend(f"- `{item}`" for item in references)
    else:
        lines.append("- none")
    lines.extend(["", "## Planned Deliverables (not created by Plan)"])
    deliverables = [
        item
        for item in discovery.get("planned_deliverables") or []
        if isinstance(item, dict)
    ]
    if deliverables:
        for item in deliverables:
            lines.append(
                f"- `{item.get('path') or ''}`：operation={item.get('operation') or ''}；"
                f"creation_scope=`{item.get('creation_scope') or ''}`；"
                f"reason={item.get('reason') or ''}"
            )
    else:
        lines.append("- none")
    boundary = discovery.get("forbidden_boundary_check") or {}
    lines.extend(
        [
            "",
            "## Forbidden Boundary Check",
            f"- passed={bool(boundary.get('passed'))}；{boundary.get('notes') or ''}",
        ]
    )
    warnings = [
        str(item).strip()
        for item in discovery.get("warnings") or []
        if str(item).strip()
    ]
    if warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in warnings]])
    return "\n".join(lines).rstrip() + "\n"
