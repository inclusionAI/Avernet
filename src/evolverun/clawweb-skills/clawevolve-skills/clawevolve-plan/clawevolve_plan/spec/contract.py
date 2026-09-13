from __future__ import annotations

import re
from typing import Any

SPEC_HEADINGS = [
    "## 1. Objective Contract", "### 1.1 Objective Summary", "### 1.2 Business Hard Constraints",
    "### 1.3 Optimization Bias", "### 1.4 Success Criteria", "## 2. Current Strategy Summary",
    "## 3. Active Optimization Directions", "## 4. Tuning Scope", "### Allowed Change Areas",
    "### Disallowed Change Areas", "## 5. Failure Modes to Address", "## 6. Spec Evolution Rules",
    "## 7. History", "## Appendix A: Project Consumption Map", "## Appendix B: Diagnose Evidence and Agentic Discovery",
]
TEMPLATE_HEADINGS = ["## Prompt", "## Expected Behavior", "## Grading Criteria", "## Automated Checks", "## LLM Judge Rubric", "## Workspace Files", "## Additional Notes"]


def validate_spec_markdown(markdown: str, spec: dict[str, Any] | None = None) -> None:
    text = str(markdown or "")
    if not text.strip():
        raise ValueError("spec-v0.md is empty")
    if not text.startswith("---\n") or "schema_version: evolution.spec.v0" not in text:
        raise ValueError("spec-v0.md violates required frontmatter contract")
    if "# Evolution Strategy Spec v0" not in text:
        raise ValueError("spec-v0.md title is missing")
    positions = [_heading_position(text, h) for h in SPEC_HEADINGS]
    if any(p < 0 for p in positions) or positions != sorted(positions):
        raise ValueError("spec-v0.md headings are missing or out of template order")
    if "| TC Code | Module | Problem Type | Direction ID | Priority | Why This Round | Expected Effect |" not in text:
        raise ValueError("spec-v0.md active directions table is missing")
    if "| ID | TC Code | Related Direction ID | Failure Mode | Evidence | Priority | Suggested Direction |" not in text:
        raise ValueError("spec-v0.md failure modes table is missing")
    if re.search(r"<(?:在|写出|只写|path-or-dir)[^>]*>", text):
        raise ValueError("spec-v0.md contains unresolved template placeholders")
    _validate_primary_metric(text, (spec or {}).get("acceptance_criteria") or {}, "spec-v0.md")
    strategic_body = text.split("## Appendix B:", 1)[0]
    _validate_diagnose_intent_not_promoted(
        strategic_body,
        (spec or {}).get("user_intent") or {},
        "spec-v0.md",
    )


def validate_objective_markdown(markdown: str, objective: dict[str, Any] | None = None) -> None:
    text = str(markdown or "")
    if not text.strip():
        raise ValueError("objective.md is empty")
    headings = ("# clawEvolve Objective", "## 目标", "## 质量门禁", "## 跟踪的问题模式", "## 停止条件")
    positions = [_objective_heading_position(text, heading) for heading in headings]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("objective.md headings are missing or out of template order")
    if "{用自然语言" in text or "{列出本轮" in text:
        raise ValueError("objective.md contains unresolved template placeholders")
    _validate_primary_metric(text, {"primary_metric": (objective or {}).get("primary_metric") or {}}, "objective.md")
    _validate_diagnose_intent_not_promoted(
        text,
        (objective or {}).get("user_intent") or {},
        "objective.md",
    )


def _validate_diagnose_intent_not_promoted(
    text: str,
    user_intent: dict[str, Any],
    document_name: str,
) -> None:
    """Reject Diagnose acquisition text only when an explicit goal superseded it."""
    if str(user_intent.get("source") or "").strip() != "cli_goal":
        return
    current_intent = str(user_intent.get("intent_text") or "").strip()
    diagnose_intent = str(user_intent.get("source_diagnose_intent") or "").strip()
    if diagnose_intent and diagnose_intent != current_intent and diagnose_intent in text:
        raise ValueError(
            f"{document_name} promotes Diagnose acquisition intent to optimization objective"
        )


def validate_task_template_markdown(markdown: str) -> None:
    text = str(markdown or "")
    if not text.startswith("---\n") or "# Task Template" not in text:
        raise ValueError("task template violates TASK_TEMPLATE.md contract")
    positions = [_heading_position(text, h) for h in TEMPLATE_HEADINGS]
    if any(p < 0 for p in positions) or positions != sorted(positions):
        raise ValueError(
            "task template headings are missing or out of order; required headings "
            "include Prompt, Expected Behavior, Grading Criteria, Automated Checks, "
            "LLM Judge Rubric, Workspace Files, and Additional Notes"
        )


def _heading_position(text: str, heading: str) -> int:
    return text.find("\n" + heading + "\n")


def _objective_heading_position(text: str, heading: str) -> int:
    if heading == "# clawEvolve Objective":
        return text.find("# clawEvolve Objective")
    return _heading_position(text, heading)


def _validate_primary_metric(text: str, criteria: dict[str, Any], document_name: str) -> None:
    metric = criteria.get("primary_metric")
    if not isinstance(metric, dict) or not metric:
        return
    target = metric.get("target")
    # Natural-language documents need not repeat the canonical metric label verbatim.
    if metric.get("unit") == "ratio":
        try:
            percent = f"{float(target) * 100:g}%"
        except (TypeError, ValueError):
            percent = ""
        if percent and percent not in text:
            raise ValueError(f"{document_name} does not preserve primary metric target")
    if metric.get("name") not in {"task_success_rate", "task_completion_rate"}:
        conflicting = re.search(r"任务(?:成功率|完成率)[^\n]{0,16}(?:90%|0\.9(?:0)?)", text)
        if conflicting:
            raise ValueError(f"{document_name} contains a conflicting default task success metric")
