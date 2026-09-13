from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import logger
from ..discovery.agent import DiscoveryAgentError, prepare_plan_workspace, run_openclaw_agent_message

_BEGIN_OBJECTIVE = "=== OBJECTIVE_MD_BEGIN ==="
_END_OBJECTIVE = "=== OBJECTIVE_MD_END ==="
_BEGIN_SPEC = "=== SPEC_V0_MD_BEGIN ==="
_END_SPEC = "=== SPEC_V0_MD_END ==="


def generate_plan_markdown_with_agent(
    *,
    plan: dict[str, Any],
    goal_text: str,
    discovery_notes: str,
    target_files: list[str],
    objective_template: str,
    spec_template: str,
    workspace: Path,
    task_id: str,
) -> tuple[str, str, dict[str, Any]]:
    """Generate both plan documents while preserving the supplied templates.

    The agent is deliberately asked for natural-language Markdown rather than a
    rigid business schema. The only machine protocol is the pair of delimiters;
    Python remains responsible for template structure validation and persistence.
    """
    prompt = _build_prompt(
        plan=plan,
        goal_text=goal_text,
        discovery_notes=discovery_notes,
        target_files=target_files,
        objective_template=objective_template,
        spec_template=spec_template,
    )
    logger.info(
        "plan document agent start",
        task_id=task_id,
        prompt_chars=len(prompt),
        target_count=len(target_files),
        case_count=len(plan.get("cases") or []),
    )
    try:
        prepare_plan_workspace(workspace)
        result = run_openclaw_agent_message(
            message=prompt,
            workspace_root=workspace,
            task_id=task_id,
            timeout_seconds=1200,
        )
    except DiscoveryAgentError:
        raise
    logger.info(
        "plan document agent done",
        task_id=task_id,
        status=result.status,
        elapsed_seconds=f"{result.elapsed_seconds:.2f}",
        response_chars=len(result.response_text or result.stdout_text),
    )
    if result.status not in {"success", "succeeded", "completed", "done", "ok"}:
        raise RuntimeError(f"plan document agent failed: {result.diagnostics}")
    objective_md, spec_md = _extract_documents(
        result.response_text or result.stdout_text
    )
    metadata = {
        "method": "openclaw_agent_template_fill",
        "agent_id": result.agent_id,
        "session_id": result.session_id,
        "elapsed_seconds": result.elapsed_seconds,
        "diagnostics": result.diagnostics,
    }
    return objective_md, spec_md, metadata


def load_plan_templates() -> tuple[str, str, dict[str, str]]:
    root = Path(__file__).resolve().parents[2]
    objective_path = root / "references" / "objective-template.md"
    spec_path = root / "references" / "evolution-strategy-spec-v0-template.md"
    if not objective_path.is_file():
        raise FileNotFoundError(f"objective template not found: {objective_path}")
    if not spec_path.is_file():
        raise FileNotFoundError(f"spec template not found: {spec_path}")
    return (
        objective_path.read_text(encoding="utf-8"),
        spec_path.read_text(encoding="utf-8"),
        {
            "objective": str(objective_path),
            "spec": str(spec_path),
        },
    )


def _build_prompt(
    *,
    plan: dict[str, Any],
    goal_text: str,
    discovery_notes: str,
    target_files: list[str],
    objective_template: str,
    spec_template: str,
) -> str:
    # Send the actual Spec template, not the surrounding documentation page.
    body = re.search(r"(?ms)^```(?:md|markdown)\s*\n(---\n.*?schema_version: evolution\.spec\.v0.*?)(?=^```\s*$)", spec_template)
    if body:
        spec_template = body.group(1).strip()
    generated_spec = plan.get("generated_spec_context") or {}
    normalized_intent = generated_spec.get("user_intent") or {}
    evidence = generated_spec.get("evidence") or {}
    discovery = generated_spec.get("agentic_discovery") or {}
    context = {
        "current_optimization_goal": (
            normalized_intent.get("intent_text")
            or (generated_spec.get("goal") or {}).get("goal_text")
            or goal_text
        ),
        "goal_contract": generated_spec.get("goal") or {},
        "acceptance_criteria": generated_spec.get("acceptance_criteria") or {},
        "input_mode": plan.get("input_mode") or "diagnose",
        "source_diagnose_intent": normalized_intent.get("source_diagnose_intent") or "",
        "goal_context": plan.get("goal_context") or {},
        "direct_goal_analysis": plan.get("direct_goal_analysis") or {},
        "root_cause_clusters": plan.get("root_cause_clusters") or [],
        "case_distribution": plan.get("case_distribution") or {},
        "clawweb_domain": plan.get("clawweb_domain") or {},
        "clawweb_domains": plan.get("clawweb_domains")
        or plan.get("clawweb_domain")
        or {},
        "discovery_notes": discovery_notes,
        "inspected_target_files": target_files,
        "evidence_summary": {
            "case_count": evidence.get("case_count"),
            "case_distribution": evidence.get("case_distribution") or {},
        },
        "discovery_summary": {
            "inspected_or_candidate_files": discovery.get(
                "inspected_or_candidate_files"
            )
            or [],
            "discovery_checklist": discovery.get("discovery_checklist") or [],
        },
        "generated_spec_context": {
            "objective_contract": generated_spec.get("objective_contract") or {},
            "current_strategy_summary": generated_spec.get("current_strategy_summary")
            or [],
            "active_optimization_directions": generated_spec.get(
                "active_optimization_directions"
            )
            or [],
            "tuning_scope": generated_spec.get("tuning_scope") or {},
            "failure_modes_to_address": generated_spec.get("failure_modes_to_address")
            or [],
            "allowed_update_targets": generated_spec.get("allowed_update_targets")
            or [],
            "required_behavior": generated_spec.get("required_behavior") or [],
        },
        "objective_document_context": plan.get("objective_document_context") or {},
    }
    return f"""你是 clawevolve-plan 文档作者。请根据用户的自然语言目标、冻结 Plan Source 证据和 discovery 结果，生成 objective.md 与 spec-v0.md。

核心要求：
1. `current_optimization_goal` 是本轮唯一业务优化目标，必须保留其真实语义、范围和指标，不得替换或并列为默认目标。
2. 文档正文可以自由、自然地表达；不要臆造用户未要求的业务规则、指标、文件或结果。
3. objective.md 说明为什么优化、优化范围、用户期望效果、质量门禁和停止条件。
4. spec-v0.md 说明本轮如何优化，并且必须是 strategy document，不得重写 objective。
5. 如果用户明确给出百分比、数量、时间范围、任务范围、交付物或副作用约束，必须在相关文档中明确体现。
6. `source_diagnose_intent`（若存在）只是上游证据采集请求，不是优化目标。不得把它写成“用户意图”或 Objective；如需追溯，只能简述为证据来源。
7. `goal_contract.primary_metric` 是主指标的唯一事实来源。不得增加与其冲突的默认成功率；两个文档都必须明确写出该指标名称、比较符和目标值。`unit=ratio` 时，必须把目标值换算成使用 ASCII `%` 的百分比字面量（例如 `target=0.9` 必须原样出现 `90%`，不能只写 `0.9`、`90％` 或文字描述）。
8. input_mode=direct_goal 时，goal 是唯一业务目标，prospective cases 是未来验证场景；不得声称“Diagnose 已发现”、不得伪造历史 session、历史失败或历史指标。
9. 只能修改下面模板的章节内容：frontmatter、标题、章节名称和章节顺序必须保持不变；不要添加顶层章节，不要删除章节，不要保留模板占位符。
10. 输出只能包含两个文档，并严格使用以下分隔符，不要在分隔符之外输出解释。

{_BEGIN_OBJECTIVE}
<完整 objective.md>
{_END_OBJECTIVE}
{_BEGIN_SPEC}
<完整 spec-v0.md>
{_END_SPEC}

用户和证据上下文：
{json.dumps(context, ensure_ascii=False, default=str)}

OBJECTIVE 模板：
{objective_template}

SPEC-V0 模板：
{spec_template}
"""


def _extract_documents(text: str) -> tuple[str, str]:
    raw = str(text or "")
    objective = _extract_between(raw, _BEGIN_OBJECTIVE, _END_OBJECTIVE)
    spec = _extract_between(raw, _BEGIN_SPEC, _END_SPEC)
    if not objective or not spec:
        # Permit a JSON transport envelope without making its fields a business
        # contract. This is useful when OpenClaw's --json wraps the response.
        candidate = _extract_json(raw)
        if isinstance(candidate, dict):
            objective = str(candidate.get("objective_markdown") or "").strip()
            spec = str(candidate.get("spec_markdown") or "").strip()
    if not objective or not spec:
        raise ValueError(
            "agent response did not contain both objective.md and spec-v0.md"
        )
    return _clean_markdown_document(objective), _clean_markdown_document(spec)


def _extract_between(text: str, begin: str, end: str) -> str:
    start = text.find(begin)
    if start < 0:
        return ""
    start += len(begin)
    finish = text.find(end, start)
    return text[start:finish].strip() if finish >= 0 else ""


def _extract_json(text: str) -> Any:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S
        ).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        for marker in ("{", "["):
            position = value.find(marker)
            if position >= 0:
                try:
                    return json.JSONDecoder().raw_decode(value[position:])[0]
                except json.JSONDecodeError:
                    continue
    return None


def _clean_markdown_document(document: str) -> str:
    """Remove transport-only code fences without changing document content."""
    value = str(document or "").strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value + "\n"
