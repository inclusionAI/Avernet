from __future__ import annotations

import json
from typing import Any


CONSUMPTION_MAP_DEFAULT = [
    {
        "consumer": "clawevolve-plan",
        "reads": "plan-source/v2 + discovery notes + inspected targets + upload result",
        "uses": "生成 objective.md/json、spec-v0.md/json，以及 ClawWeb step-report 载荷。",
    },
    {
        "consumer": "clawevolve-tune",
        "reads": "objective.md + spec-v{N-1}.md + round_state.json/bench.optimization",
        "uses": "选择小而可解释的 patch 方案，并写出 tune_report、changed_files、diff、change_manifest。",
    },
    {
        "consumer": "clawevolve-review",
        "reads": "objective.md + spec-v{N-1}.md + round_state.json/bench.validation + tune_report",
        "uses": "决定 keep/strengthen/split/retire/reject，并输出下一版 spec-vN.md 与 spec_update_report.md。",
    },
    {
        "consumer": "clawevolve-workflow / runner",
        "reads": "plan/output/spec-v0.md、input/spec-v{N-1}.md、spec/spec-vN.md",
        "uses": "在轮次之间搬运、版本化并注入 spec 到后续 prompt/round 输入。",
    },
]


def render_markdown(spec: dict[str, Any]) -> str:
    """Render spec-v0.md using the shared evolution-strategy template."""

    lines = _frontmatter(spec)
    lines.extend(
        [
            "",
            "# Evolution Strategy Spec v0",
            "",
            "## 1. Objective Contract",
            "",
            "### 1.1 Objective Summary",
            "",
            *_bulleted(_objective_summary(spec)),
            "",
            "### 1.2 Business Hard Constraints",
            "",
            *_bulleted(_business_hard_constraints(spec)),
            "",
            "### 1.3 Optimization Bias",
            "",
            *_bulleted(_optimization_bias(spec)),
            "",
            "### 1.4 Success Criteria",
            "",
            *_bulleted(_success_criteria(spec)),
            "",
            "## 2. Current Strategy Summary",
            "",
            *_bulleted(_current_strategy_summary(spec)),
            "",
            "## 3. Active Optimization Directions",
            "",
            "| TC Code | Module | Problem Type | Direction ID | Priority | Why This Round | Expected Effect |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in spec.get("active_optimization_directions") or []:
        lines.append(
            "| {tc_code} | {module} | {problem_type} | {direction_id} | {priority} | {why_this_round} | {expected_effect} |".format(
                tc_code=_cell(item.get("tc_code")),
                module=_cell(item.get("module")),
                problem_type=_cell(item.get("problem_type")),
                direction_id=_cell(item.get("direction_id")),
                priority=_cell(_priority_text(item.get("priority"))),
                why_this_round=_cell(item.get("why_this_round")),
                expected_effect=_cell(item.get("expected_effect")),
            )
        )
    scope = spec.get("tuning_scope") or {}
    lines.extend(
        [
            "",
            "## 4. Tuning Scope",
            "",
            "### Allowed Change Areas",
            "",
            *_bulleted(scope.get("allowed_change_areas") or []),
            "",
            "### Disallowed Change Areas",
            "",
            *_bulleted(scope.get("disallowed_change_areas") or []),
            "",
            "## 5. Failure Modes to Address",
            "",
            "| ID | TC Code | Related Direction ID | Failure Mode | Evidence | Priority | Suggested Direction |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in spec.get("failure_modes_to_address") or []:
        lines.append(
            "| {id} | {tc_code} | {related_direction_id} | {failure_mode} | {evidence} | {priority} | {suggested_direction} |".format(
                id=_cell(item.get("id")),
                tc_code=_cell(item.get("tc_code")),
                related_direction_id=_cell(item.get("related_direction_id")),
                failure_mode=_cell(item.get("failure_mode")),
                evidence=_cell(item.get("evidence")),
                priority=_cell(_priority_text(item.get("priority"))),
                suggested_direction=_cell(item.get("suggested_direction")),
            )
        )
    lines.extend(
        [
            "",
            "## 6. Spec Evolution Rules",
            "",
            *_bulleted(spec.get("spec_evolution_rules") or []),
            "",
            "## 7. History",
            "",
            *_bulleted(spec.get("history") or []),
            "",
            "---",
            "",
            "## Appendix A: Project Consumption Map",
            "",
            "| Consumer | Reads | Uses It For |",
            "|---|---|---|",
        ]
    )
    for item in _consumption_map(spec):
        lines.append(
            "| {consumer} | {reads} | {uses} |".format(
                consumer=_cell(item.get("consumer")),
                reads=_cell(item.get("reads")),
                uses=_cell(item.get("uses")),
            )
        )
    lines.extend(
        [
            "",
            "## Appendix B: Diagnose Evidence and Agentic Discovery",
            "",
        ]
    )
    lines.extend(_appendix_sections(spec))
    return "\n".join(lines)


def render_goal_markdown(goal_doc: dict[str, Any]) -> str:
    goal = goal_doc.get("goal") or {}
    gates = goal_doc.get("quality_gates") or {}
    metric = goal_doc.get("primary_metric") or {}
    domain = goal_doc.get("clawweb_domains") or goal_doc.get("clawweb_domain") or {}
    train_domain = (domain.get("domains") or {}).get("train") or {}
    test_domain = (domain.get("domains") or {}).get("test") or {}
    input_mode = str(goal_doc.get("input_mode") or "diagnose")
    basis = goal_doc.get("planning_basis") or {}
    regression_gate = (
        f"- Prospective case 通过率 >= {gates.get('prospective_case_pass_rate_min')}"
        if input_mode == "direct_goal"
        else f"- Good regression 下降 <= {gates.get('good_regression_drop_max')}"
    )
    return "\n".join(
        [
            f"# clawEvolve Objective（目标）：{goal_doc.get('goal_id')}",
            "",
            f"- Bot id：`{goal_doc.get('bot_id')}`",
            f"- ClawWeb train domain：`{train_domain.get('domain_id') or ''}`",
            f"- ClawWeb train URL：`{train_domain.get('domain_url') or ''}`",
            f"- ClawWeb test domain：`{test_domain.get('domain_id') or ''}`",
            f"- ClawWeb test URL：`{test_domain.get('domain_url') or ''}`",
            "",
            "## 目标",
            f"- 用户原始需求：{((goal_doc.get('user_intent') or {}).get('raw_request') or goal.get('goal_text') or '无')}",
            f"- 用户优化目标：{((goal_doc.get('user_intent') or {}).get('intent_text') or goal.get('goal_text') or '无额外意图')}",
            f"- 规划依据：{basis.get('mode_label') or basis.get('mode') or '未标明'}",
            f"- 证据/验证场景数量：{basis.get('evidence_case_count', 0)}",
            f"- 最大迭代轮数：{goal.get('max_iterations')}",
            f"- 主指标：{_metric_requirement(metric)}",
            f"- 指标范围：{metric.get('scope')}",
            "",
            "## 质量门禁",
            regression_gate,
            f"- 单样本超时：{gates.get('case_timeout_seconds')}s",
            f"- 要求稳定运行：{gates.get('stable_run_required')}",
            f"- 必须检索的样本需要实际检索：{gates.get('search_required_cases_must_search')}",
            f"- 高分必须使用相关证据：{gates.get('relevant_evidence_required_for_high_score')}",
            f"- 无证据不得编造：{gates.get('no_hallucination_without_evidence')}",
            "",
            "## 跟踪的问题模式",
            _md_list(goal_doc.get("tracked_problem_modes") or []),
            "",
            "## 停止条件",
            str(goal_doc.get("stop_condition") or ""),
            "",
        ]
    )


def _frontmatter(spec: dict[str, Any]) -> list[str]:
    parent = spec.get("parent_spec_version")
    parent_text = "null" if parent is None else str(parent)
    return [
        "---",
        f"schema_version: {spec.get('schema_version', 'evolution.spec.v0')}",
        f"spec_version: {spec.get('spec_version', 'v0')}",
        f"parent_spec_version: {parent_text}",
        f"created_by: {spec.get('created_by', 'clawweb')}",
        f"objective_ref: {spec.get('objective_ref', 'objective.md')}",
        f"direction_pool_ref: {spec.get('direction_pool_ref', 'clawevolve-workflow/references/direction-pool.md')}",
        f"max_active_directions: {spec.get('max_active_directions', 3)}",
        "---",
    ]


def _objective_summary(spec: dict[str, Any]) -> list[str]:
    contract = spec.get("objective_contract") or {}
    if isinstance(contract, dict):
        summary = contract.get("objective_summary") or []
        if isinstance(summary, list) and summary:
            return [str(item) for item in summary[:3]]
    goal = spec.get("goal") or {}
    summary = str(goal.get("goal_text") or spec.get("objective_alignment") or "")
    out = [summary] if summary else []
    if spec.get("objective_alignment") and spec.get("objective_alignment") not in out:
        out.append(str(spec.get("objective_alignment")))
    if not out:
        out.append(
            "本轮策略继承 objective 的目标与质量门禁，并仅针对已确认的证据选择有限优化方向。"
        )
    return out[:3]


def _current_strategy_summary(spec: dict[str, Any]) -> list[str]:
    summary = [str(item) for item in spec.get("current_strategy_summary") or []]
    product = spec.get("plan_summary") or {}
    basis = product.get("planning_basis") or spec.get("planning_basis") or {}
    boundary = product.get("change_boundary") or {}
    prefix = [
        f"规划依据：{basis.get('mode_label') or basis.get('mode') or '未标明'}；"
        f"证据/验证场景 {basis.get('evidence_case_count', 0)} 个。",
        "Plan 只输出后续进化目标与边界，不在本阶段修改目标 workspace。",
    ]
    if boundary.get("planned_deliverables"):
        prefix.append(
            "计划新增内容："
            + "; ".join(
                str(item.get("path") or "")
                for item in boundary.get("planned_deliverables") or []
                if isinstance(item, dict)
            )
            + "。"
        )
    return _unique_lines(prefix + summary)


def _business_hard_constraints(spec: dict[str, Any]) -> list[str]:
    contract = spec.get("objective_contract") or {}
    if isinstance(contract, dict):
        hard_constraints = contract.get("business_hard_constraints") or []
        if isinstance(hard_constraints, list) and hard_constraints:
            return [str(item) for item in hard_constraints[:8]]
    constraints = list(spec.get("forbidden_changes") or [])
    required_behavior = spec.get("required_behavior") or []
    if required_behavior:
        constraints.append("必须满足的行为：")
        constraints.extend(f"- {item}" for item in required_behavior[:5])
    if not constraints:
        constraints = [
            "遵守 objective 已定义的业务约束，并不得越过本轮允许的修改边界。"
        ]
    return constraints[:8]


def _optimization_bias(spec: dict[str, Any]) -> list[str]:
    contract = spec.get("objective_contract") or {}
    if isinstance(contract, dict):
        bias = contract.get("optimization_bias") or []
        if isinstance(bias, list) and bias:
            return [str(item) for item in bias[:4]]
    return [
        "`generalization_first`：默认取向，优先稳健、泛化、少回归。",
        "`objective_specific`：当 objective 明确是窄业务目标时启用，优先关键路径、业务约束和输出契约；但不得发明 objective 之外的新业务规则，不得硬编码 case 答案。",
    ]


def _success_criteria(spec: dict[str, Any]) -> list[str]:
    contract = spec.get("objective_contract") or {}
    if isinstance(contract, dict):
        success_criteria = contract.get("success_criteria") or []
        if isinstance(success_criteria, list) and success_criteria:
            return [str(item) for item in success_criteria[:10]]
    criteria = spec.get("acceptance_criteria") or {}
    return [_metric_requirement(criteria.get("primary_metric") or {})] if criteria else []


def _appendix_sections(spec: dict[str, Any]) -> list[str]:
    domain = (
        (spec.get("deliverables") or {}).get("clawweb_domains")
        or (spec.get("deliverables") or {}).get("clawweb_domain")
        or {}
    )
    train_domain = (domain.get("domains") or {}).get("train") or {}
    test_domain = (domain.get("domains") or {}).get("test") or {}
    evidence = spec.get("evidence") or {}
    discovery = spec.get("agentic_discovery") or {}
    criteria = spec.get("acceptance_criteria") or {}
    lines = [
        f"- Spec id：`{spec.get('spec_id')}`",
        f"- Bot id：`{spec.get('bot_id')}`",
        f"- ClawWeb train domain：`{train_domain.get('domain_id') or ''}`",
        f"- ClawWeb train URL：`{train_domain.get('domain_url') or ''}`",
        f"- ClawWeb test domain：`{test_domain.get('domain_id') or ''}`",
        f"- ClawWeb test URL：`{test_domain.get('domain_url') or ''}`",
        f"- ClawWeb 双 Domain 上传状态：`{domain.get('status') or 'unknown'}`",
        f"- 样本数量：{evidence.get('case_count')}",
        f"- 样本分布：`{json.dumps(evidence.get('case_distribution', {}), ensure_ascii=False)}`",
        f"- 原始模型分布：`{json.dumps(evidence.get('original_model_distribution', {}), ensure_ascii=False)}`",
        f"- 来源产物：`{json.dumps(_artifact_summary(evidence.get('artifacts', {})), ensure_ascii=False)}`",
        "",
        "### Agentic Discovery（文件发现）",
        "",
        f"- Discovery 上下文摘要：`{json.dumps(_agent_context_summary(discovery.get('agent_context', {})), ensure_ascii=False)}`",
        "- 已检查/候选文件：",
        _md_list(discovery.get("inspected_or_candidate_files", []), 2),
        "",
        "#### 发现 Checklist",
        "",
        _md_list(discovery.get("discovery_checklist") or []),
        "",
        "#### 发现笔记",
        "",
        discovery.get("notes")
        or "未提供 discovery notes；下一个 agent 在 patch 前必须先检查目标文件。",
        "",
        "### 允许更新目标",
        "",
        _md_list(spec.get("allowed_update_targets") or []),
        "",
        "### 允许创建范围",
        "",
        _md_list(spec.get("allowed_creation_scopes") or []),
        "",
        "### 只读参考文件",
        "",
        _md_list(spec.get("reference_files") or []),
        "",
        "### 计划交付物（由后续 patch/evolve 创建）",
        "",
        _md_list(
            [
                f"{item.get('path') or ''} (operation={item.get('operation') or ''}, "
                f"creation_scope={item.get('creation_scope') or ''})"
                for item in spec.get("planned_deliverables") or []
                if isinstance(item, dict)
            ]
        ),
        "",
        "### 禁止改动",
        "",
        _md_list(spec.get("forbidden_changes") or []),
        "",
        "### 必须满足的行为",
        "",
        _md_list(spec.get("required_behavior") or []),
        "",
        "### 验收标准",
        "",
        f"- {_metric_requirement(criteria.get('primary_metric') or {})}。",
        (
            "- Prospective cases 必须覆盖用户目标，且不得依赖虚构的历史上下文。"
            if str(spec.get("input_mode") or "diagnose") == "direct_goal"
            else f"- Good regression 回退必须 <= {criteria.get('regression_drop_max')}。"
        ),
        f"- 每个样本超时时间为 {criteria.get('case_timeout_seconds')}s。",
        "- 必须检索的样本需要体现真实检索/召回过程，并使用相关证据。",
        "- 无证据样本不得编造业务事实。",
        "- 除非样本明确需要更多步骤，候选 agent 必须在默认 step budget 10 内稳定运行。",
        "",
        "### Patch 生成指令",
        "",
        str(spec.get("patch_generator_instruction") or ""),
        "",
    ]
    return lines


def _consumption_map(spec: dict[str, Any]) -> list[dict[str, Any]]:
    items = spec.get("consumption_map")
    if isinstance(items, list) and items:
        return items
    return CONSUMPTION_MAP_DEFAULT


def _md_list(items: list[Any], indent: int = 0) -> str:
    prefix = " " * indent + "- "
    return "\n".join(prefix + str(item) for item in items) if items else prefix + "无"


def _bulleted(items: list[Any]) -> list[str]:
    if not items:
        return ["- 无"]
    return [f"- {item}" for item in items]


def _unique_lines(items: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _priority_text(value: Any) -> str:
    mapping = {"high": "高", "medium": "中", "low": "低"}
    text = str(value or "").strip().lower()
    return mapping.get(text, value)


def _cell(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|")
    return " ".join(text.split())


def _metric_requirement(metric: dict[str, Any]) -> str:
    display_name = str(metric.get("display_name") or metric.get("name") or "主指标")
    operator = str(metric.get("operator") or ">=")
    target = metric.get("target")
    if metric.get("unit") == "ratio":
        try:
            target_text = f"{float(target) * 100:g}%"
        except (TypeError, ValueError):
            target_text = str(target)
    else:
        target_text = str(target)
    return f"{display_name} {operator} {target_text}"


def _artifact_summary(artifacts: Any) -> dict[str, str]:
    if not isinstance(artifacts, dict):
        return {}
    preferred = (
        "diagnosis_jsonl",
        "eval_cases_jsonl",
        "diagnose_result_json",
        "analysis_report_md",
        "case_contracts",
        "clawbench_manifest",
    )
    summary: dict[str, str] = {}
    for key in preferred:
        value = str(artifacts.get(key) or "").strip()
        if value:
            summary[key] = value
    return summary


def _agent_context_summary(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("workspace", "config", "openclaw_state", "agent_id"):
        value = context.get(key)
        if value not in (None, "", [], {}):
            summary[key] = value
    skill_dirs = context.get("skill_dirs")
    if isinstance(skill_dirs, list) and skill_dirs:
        summary["skill_dirs"] = skill_dirs[:3]
    return summary
