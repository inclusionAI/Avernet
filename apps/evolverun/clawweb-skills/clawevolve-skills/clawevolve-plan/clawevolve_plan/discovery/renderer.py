from __future__ import annotations

from typing import Any


def render_discovery_notes(discovery: dict[str, Any], *, targets: list[str]) -> str:
    lines: list[str] = ["# Agentic Discovery Notes", ""]
    lines.extend(["## 后续优化目标", *[f"- `{target}`：{_target_reason(discovery, target)}" for target in targets], ""])
    lines.append("## Case 发现")
    case_findings = discovery.get("case_findings") if isinstance(discovery.get("case_findings"), list) else []
    if case_findings:
        for item in case_findings:
            if not isinstance(item, dict):
                continue
            case_id = item.get("case_id") or "unknown_case"
            mode = item.get("failure_mode") or item.get("evolution_failure_mode") or "unknown_failure_mode"
            hypothesis = item.get("root_cause_hypothesis") or item.get("symptom") or "已基于 Plan Source 证据检查。"
            files = _strings(item.get("inspected_files"))
            lines.append(f"- `{case_id}` / `{mode}`：{hypothesis}")
            if files:
                lines.append(f"  - inspected: {', '.join(f'`{x}`' for x in files[:8])}")
    else:
        lines.append("- helper agent 未返回 case_findings；以 target_findings 和 Source failure modes 为准。")
    lines.append("")

    lines.append("## Target 结论")
    target_findings = discovery.get("target_findings") if isinstance(discovery.get("target_findings"), list) else []
    if target_findings:
        for item in target_findings:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or "unknown_target"
            modes = ", ".join(f"`{x}`" for x in _strings(item.get("failure_modes"))) or "`unknown_failure_mode`"
            cases = ", ".join(f"`{x}`" for x in _strings(item.get("related_case_ids"))) or "`unknown_case`"
            reason = item.get("reason") or "与 Source 失败模式相关。"
            lines.append(f"- `{path}`：{reason} 关联 failure_mode={modes}，case={cases}。")
    else:
        for target in targets:
            lines.append(f"- `{target}`：helper agent 建议作为本轮窄边界优化目标。")
    lines.append("")

    boundary = discovery.get("forbidden_boundary_check") or {}
    boundary_note = boundary.get("notes") if isinstance(boundary, dict) else ""
    lines.extend([
        "## 边界检查",
        f"- forbidden_boundary_check: passed。{boundary_note or '未建议 judge/scorer/secrets/generated artifacts。'}",
    ])
    warnings = _strings(discovery.get("warnings"))
    if warnings:
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in warnings]])
    return "\n".join(lines).rstrip() + "\n"


def _target_reason(discovery: dict[str, Any], target: str) -> str:
    findings = discovery.get("target_findings") if isinstance(discovery.get("target_findings"), list) else []
    for item in findings:
        if isinstance(item, dict) and str(item.get("path") or "").strip() == target:
            return str(item.get("reason") or "helper agent 已确认，作为本轮允许优化目标。")
    return "helper agent 已确认，作为本轮允许优化目标。"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
