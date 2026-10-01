from __future__ import annotations

import time
from typing import Any, Iterable

from ..intent import normalize_primary_metric, normalize_user_intent
from ..product import PlanProductService
from ..constants import (
    DEFAULT_FORBIDDEN_CHANGES,
    DEFAULT_UPDATE_TARGETS,
    MODE_BEHAVIOR,
    MODE_TARGETS,
)


SPEC_EVOLUTION_RULES = [
    "保持 spec 是调优策略文档，不重写 objective。",
    "后续轮次应让优化方向逐步细化，避免一次性扩大范围。",
    "下一轮最多只激活 `max_active_directions` 个优化方向。",
    "MVP 阶段优先激活 1-3 个方向；仅当失败证据明确需要时才扩展到 4-5 个。",
    "优先复用 `direction_pool_ref` 中已有的 direction ID。",
    "只有现有方向无法表达所需优化时才新增 direction ID，并在 spec_update_report 中说明原因。",
    "即使本轮未激活，也要在外部 direction pool 中保留有价值的方向。",
    "不得编码针对验证集样本的特定答案。",
    "不得为了提升分数而削弱 objective 对齐或质量门禁。",
    "接受、拒绝和回滚由 orchestrator 负责，不由 spec 自行决定。",
]

DEFAULT_ALLOWED_CHANGE_AREAS = [
    "skill metadata 与 `SKILL.md`",
    "persona、tool、agent 行为文档等 md 配置文件",
    "skill/md 配置中的 MCP 调用指导",
    "必要的轻量 glue/config 改动",
]

DEFAULT_DISALLOWED_CHANGE_AREAS = [
    "MCP server/tool 实现本身",
    "优化/验证样本数据",
    "bench 结果文件",
    "scoring/grading 评分逻辑",
    "objective 目标文档",
    "历史轮次 artifacts",
]

SPEC_CONSUMPTION_MAP = [
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

DIRECTION_CATALOG = {
    "SKILL-TRIGGER-001": {
        "label": "Skill 触发与路由",
        "tc_code": "TC.SKILL.INSTRUCTION",
        "module": "SKILL",
        "problem_type": "INSTRUCTION",
        "modes": {
            "retrieval_not_called",
            "premature_capability_boundary",
            "workspace_or_data_missing",
        },
    },
    "MCP-CALL-SELECT-001": {
        "label": "Tool/MCP 选择与调用",
        "tc_code": "TC.MCP.INSTRUCTION",
        "module": "MCP",
        "problem_type": "INSTRUCTION",
        "modes": {
            "retrieval_bad_query",
            "retrieval_or_knowledge_failure",
            "tool_parameter_error",
            "tool_execution_failure",
            "permission_or_network_blocked",
            "runtime_config_missing",
        },
    },
    "MD-BEHAVIOR-001": {
        "label": "Markdown/prompt 行为指导",
        "tc_code": "TC.WORKFLOW_PLANNER.INSTRUCTION",
        "module": "WORKFLOW_PLANNER",
        "problem_type": "INSTRUCTION",
        "modes": {
            "retrieval_relevant_but_not_used",
            "workflow_planning_failure",
            "unnecessary_user_blocking",
            "context_or_process_truncated",
            "incorrect_or_unverified_answer",
            "good_regression",
            "unknown_failure_mode",
        },
    },
}


MODE_TO_DIRECTION = {
    mode: direction_id
    for direction_id, spec in DIRECTION_CATALOG.items()
    for mode in spec["modes"]
}


def build_goal(plan: dict[str, Any], goal_override: str = "") -> dict[str, Any]:
    raw_goal = plan.get("default_optimization_goal") or {}
    if isinstance(raw_goal, dict):
        goal = dict(raw_goal)
    else:
        goal = {"goal_text": str(raw_goal)} if str(raw_goal).strip() else {}
    goal.setdefault("max_iterations", 10)
    goal.setdefault("target_task_success_rate", 0.90)
    goal.setdefault("regression_drop_max", 0.02)
    goal.setdefault("case_timeout_seconds", 600)
    goal["goal_text"] = goal_override.strip() or str(
        goal.get("goal_text") or _default_goal_text(goal)
    )
    goal["source"] = "cli_goal" if goal_override.strip() else "inherited_or_default"
    goal["primary_metric"] = normalize_primary_metric(
        goal["goal_text"], goal.get("target_task_success_rate", 0.90)
    )
    return goal


def build_spec(
    plan: dict[str, Any],
    goal_override: str = "",
    discovery_notes: str = "",
    target_files: list[str] | None = None,
) -> dict[str, Any]:
    normalized_plan = normalize_planning_context(plan)
    input_mode = str(normalized_plan.get("input_mode") or "diagnose")
    clusters = list(normalized_plan.get("root_cause_clusters") or [])[:5]
    cases = list(normalized_plan.get("cases") or [])
    goal = build_goal(normalized_plan, goal_override)
    user_intent = normalize_user_intent(normalized_plan, goal_override)
    inspected_targets = _inspected_targets(target_files)
    creation_scopes = _unique(normalized_plan.get("creation_scopes") or [])
    reference_files = _unique(normalized_plan.get("reference_files") or [])
    planned_deliverables = [
        dict(item)
        for item in normalized_plan.get("planned_deliverables") or []
        if isinstance(item, dict)
    ]
    update_targets = [
        target for target in inspected_targets if target not in creation_scopes
    ]
    required_behavior = _required_behavior(clusters)
    preliminary_plan = _preliminary_optimization_plan(
        clusters, inspected_targets, input_mode
    )
    discovery = _agentic_discovery(
        normalized_plan, discovery_notes, inspected_targets, clusters
    )
    acceptance_criteria = _acceptance_criteria(goal)
    evidence = {
        "artifacts": normalized_plan.get("artifacts", {}),
        "case_distribution": normalized_plan.get("case_distribution", {}),
        "case_count": len(cases),
        "original_model_distribution": _original_model_distribution(normalized_plan),
        "clawweb_domain": normalized_plan.get("clawweb_domain", {}),
        "clawweb_domains": normalized_plan.get(
            "clawweb_domains", normalized_plan.get("clawweb_domain", {})
        ),
    }
    spec_id = f"spec_v0_{time.strftime('%Y%m%d_%H%M%S')}"
    active_directions = _active_optimization_directions(
        clusters, discovery, max_active=3, input_mode=input_mode
    )
    spec = {
        # New template-aligned strategy SPEC contract.
        "schema_version": "evolution.spec.v0",
        "spec_version": "v0",
        "parent_spec_version": None,
        "created_by": "clawweb",
        "objective_ref": "objective.md",
        "direction_pool_ref": "clawevolve-workflow/references/direction-pool.md",
        "max_active_directions": 3,
        "input_mode": input_mode,
        "user_intent": user_intent,
        "intent_alignment": {
            "how_strategy_serves_intent": [],
            "requested_deliverables": user_intent["requested_deliverables"],
        },
        "objective_alignment": _objective_alignment(
            goal, clusters, evidence, user_intent, input_mode
        ),
        "objective_contract": _objective_contract(
            goal,
            required_behavior,
            acceptance_criteria,
            user_intent,
            input_mode,
        ),
        "current_strategy_summary": _current_strategy_summary(
            clusters, discovery, evidence, user_intent, input_mode
        ),
        "active_optimization_directions": active_directions,
        "tuning_scope": {
            "allowed_change_areas": _allowed_change_areas(inspected_targets),
            "disallowed_change_areas": DEFAULT_DISALLOWED_CHANGE_AREAS,
        },
        "failure_modes_to_address": _failure_modes_to_address(
            clusters, active_directions
        ),
        "spec_evolution_rules": SPEC_EVOLUTION_RULES,
        "history": [_initial_history(input_mode)],
        "consumption_map": SPEC_CONSUMPTION_MAP,
        # Compatibility and traceability fields used by objective and downstream agents.
        "spec_id": spec_id,
        "bot_id": normalized_plan.get("bot_id", "current-bot"),
        "goal": goal,
        "deliverables": {
            "clawweb_domain": normalized_plan.get("clawweb_domain", {}),
            "clawweb_domains": normalized_plan.get(
                "clawweb_domains", normalized_plan.get("clawweb_domain", {})
            ),
            "objective_file": "objective.md",
            "spec_version": "v0",
        },
        "evidence": evidence,
        "problem_statement": _problem_statement(clusters, cases, input_mode),
        "root_cause_clusters": clusters,
        "optimizable_contents": _optimizable_contents(clusters, inspected_targets),
        "preliminary_optimization_plan": preliminary_plan,
        "agentic_discovery": discovery,
        "allowed_update_targets": update_targets,
        "allowed_creation_scopes": creation_scopes,
        "reference_files": reference_files,
        "planned_deliverables": planned_deliverables,
        "optimization_topics": _allowed_target_hints(clusters),
        "forbidden_changes": DEFAULT_FORBIDDEN_CHANGES,
        "required_behavior": required_behavior,
        "acceptance_criteria": acceptance_criteria,
        "requested_deliverables": user_intent["requested_deliverables"],
        "patch_generator_instruction": _patch_instruction(
            input_mode=input_mode,
            domain=normalized_plan.get("clawweb_domains")
            or normalized_plan.get("clawweb_domain", {}),
            creation_scopes=creation_scopes,
            reference_files=reference_files,
            planned_deliverables=planned_deliverables,
        ),
    }
    return PlanProductService().enrich(
        plan=normalized_plan,
        spec=spec,
        goal_override=goal_override,
    )


def build_objective_document(spec: dict[str, Any]) -> dict[str, Any]:
    criteria = spec.get("acceptance_criteria") or {}
    input_mode = str(spec.get("input_mode") or "diagnose")
    quality_gates = {
        "case_timeout_seconds": criteria.get("case_timeout_seconds", 600),
        "stable_run_required": criteria.get("stable_run_required", True),
        "search_required_cases_must_search": criteria.get(
            "search_required_cases_must_search", True
        ),
        "relevant_evidence_required_for_high_score": criteria.get(
            "relevant_evidence_required_for_high_score", True
        ),
        "no_hallucination_without_evidence": criteria.get(
            "no_hallucination_without_evidence", True
        ),
    }
    if input_mode == "direct_goal":
        quality_gates["prospective_case_pass_rate_min"] = (
            criteria.get("primary_metric") or {}
        ).get("target")
    else:
        quality_gates["good_regression_drop_max"] = criteria.get(
            "regression_drop_max", 0.02
        )
    return {
        "schema_version": "clawevolve.objective.v1",
        "goal_id": f"goal_{spec.get('spec_id', time.strftime('%Y%m%d_%H%M%S'))}",
        "bot_id": spec.get("bot_id"),
        "clawweb_domain": (spec.get("deliverables") or {}).get("clawweb_domain", {}),
        "clawweb_domains": (spec.get("deliverables") or {}).get(
            "clawweb_domains",
            (spec.get("deliverables") or {}).get("clawweb_domain", {}),
        ),
        "goal": spec.get("goal", {}),
        "user_intent": spec.get("user_intent", {}),
        "intent_alignment": spec.get("intent_alignment", {}),
        "requested_deliverables": spec.get("requested_deliverables", []),
        "planning_basis": spec.get("planning_basis", {}),
        "plan_summary": spec.get("plan_summary", {}),
        "goal_fidelity": spec.get("goal_fidelity", {}),
        "primary_metric": {
            **(criteria.get("primary_metric") or {}),
            "scope": _evaluation_scope(spec),
        },
        "input_mode": input_mode,
        "quality_gates": quality_gates,
        "case_distribution": (spec.get("evidence") or {}).get("case_distribution", {}),
        "tracked_problem_modes": [
            c.get("evolution_failure_mode")
            for c in spec.get("root_cause_clusters") or []
        ],
        "stop_condition": (
            "当在 max_iterations 内所有质量门禁通过，或连续两轮没有可衡量提升且没有安全可改目标时停止。"
        ),
    }


def normalize_planning_context(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize the private PlanningContext for SPEC generation."""

    out = dict(plan)
    if not isinstance(out.get("clawweb_domain"), dict):
        out["clawweb_domain"] = {}
    if not isinstance(out.get("clawweb_domains"), dict):
        out["clawweb_domains"] = out["clawweb_domain"]
    out["root_cause_clusters"] = [
        _normalize_cluster(c) for c in list(out.get("root_cause_clusters") or [])
    ]
    return out


def _normalize_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    c = dict(cluster)
    mode = str(c.get("evolution_failure_mode") or "unknown_failure_mode")
    c["evolution_failure_mode"] = mode
    c["affected_case_count"] = int(c.get("affected_case_count") or 0)
    c["example_case_ids"] = list(c.get("example_case_ids") or [])
    c["example_queries"] = list(c.get("example_queries") or [])
    c["problem_analysis"] = str(c.get("problem_analysis") or f"`{mode}` 失败聚类。")
    c["optimization_goal"] = c.get("optimization_goal") or MODE_BEHAVIOR.get(
        mode, MODE_BEHAVIOR["unknown_failure_mode"]
    )
    c["allowed_update_targets_hint"] = list(
        c.get("allowed_update_targets_hint")
        or MODE_TARGETS.get(mode, MODE_TARGETS["unknown_failure_mode"])
    )
    c["evidence_file_hints"] = list(c.get("evidence_file_hints") or [])
    c["tool_hints"] = list(c.get("tool_hints") or [])
    return c


def validate_discovery(spec: dict[str, Any]) -> None:
    discovery = spec.get("agentic_discovery") or {}
    notes = str(discovery.get("notes") or "").strip()
    targets = [
        t for t in discovery.get("inspected_or_candidate_files") or [] if str(t).strip()
    ]
    has_only_candidates = bool(discovery.get("requires_manual_file_inspection"))
    if not notes or not targets or has_only_candidates:
        raise ValueError(
            "clawevolve-plan 在最终渲染 SPEC 前必须完成 agentic discovery。"
            "请先检查目标 agent 文件，然后传入 --discovery-notes 和至少一个具体 --target。"
        )


def _default_goal_text(goal: dict[str, Any]) -> str:
    return (
        f"在最多 {goal['max_iterations']} 轮优化内，使当前 ClawWeb 评测集任务完成率 > "
        f"{int(float(goal['target_task_success_rate']) * 100)}%。"
    )


def _unique(items: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _mode(cluster: dict[str, Any]) -> str:
    return str(cluster.get("evolution_failure_mode") or "unknown_failure_mode")


def _allowed_target_hints(clusters: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for cluster in clusters:
        hints = cluster.get("allowed_update_targets_hint") or MODE_TARGETS.get(
            _mode(cluster), MODE_TARGETS["unknown_failure_mode"]
        )
        values.extend(hints)
    return _unique(values or DEFAULT_UPDATE_TARGETS)


def _required_behavior(clusters: list[dict[str, Any]]) -> list[str]:
    return _unique(
        cluster.get("optimization_goal")
        or MODE_BEHAVIOR.get(_mode(cluster), MODE_BEHAVIOR["unknown_failure_mode"])
        for cluster in clusters
    )


def _inspected_targets(target_files: list[str] | None) -> list[str]:
    return _unique(target_files or [])


def _agentic_discovery(
    plan: dict[str, Any], notes: str, targets: list[str], clusters: list[dict[str, Any]]
) -> dict[str, Any]:
    clean_notes = notes.strip()
    return {
        "notes": clean_notes,
        "inspected_or_candidate_files": targets,
        "agent_context": plan.get("agent_context", {}),
        "discovery_checklist": _discovery_checklist(clusters),
        "requires_manual_file_inspection": not clean_notes or not targets,
    }


def _discovery_checklist(clusters: list[dict[str, Any]]) -> list[str]:
    checklist: list[str] = []
    for cluster in clusters[:5]:
        mode = _mode(cluster)
        hints = ", ".join(
            cluster.get("allowed_update_targets_hint") or MODE_TARGETS.get(mode, [])
        )
        tools = ", ".join(cluster.get("tool_hints") or []) or "N/A"
        checklist.append(
            f"检查 `{mode}`：围绕 {hints} 验证文件；工具提示：{tools}；将发现映射到示例 {cluster.get('example_case_ids') or cluster.get('example_queries') or []}。"
        )
    return checklist


def _objective_alignment(
    goal: dict[str, Any],
    clusters: list[dict[str, Any]],
    evidence: dict[str, Any],
    user_intent: dict[str, Any] | None = None,
    input_mode: str = "diagnose",
) -> str:
    dominant_modes = ", ".join(_mode(c) for c in clusters[:3]) or "unknown_failure_mode"
    intent_text = str((user_intent or {}).get("intent_text") or "").strip()
    intent_prefix = f"当前优化目标：{intent_text}。" if intent_text else ""
    if input_mode == "direct_goal":
        return (
            intent_prefix
            + f"本策略将 {evidence.get('case_count', 0)} 个 prospective cases 作为未来验证场景，"
            f"围绕能力风险（{dominant_modes}）规划改动；这些场景不代表历史失败证据。"
            "优化必须满足 objective 质量门禁，并避免修改 judge 或 objective。"
        )
    return (
        intent_prefix
        + "本策略使用冻结 Plan Source 作为问题证据，并以 objective 中的质量门禁作为约束。"
        f"本轮聚焦 {evidence.get('case_count', 0)} 个选中样本中影响最大的失败模式（{dominant_modes}），"
        "同时保护已通过样本行为，避免修改 judge 或 objective。"
    )


def _objective_contract(
    goal: dict[str, Any],
    required_behavior: list[str],
    acceptance_criteria: dict[str, Any],
    user_intent: dict[str, Any] | None = None,
    input_mode: str = "diagnose",
) -> dict[str, Any]:
    summary = []
    intent_text = str((user_intent or {}).get("intent_text") or "").strip()
    if intent_text:
        summary.append(f"当前优化目标：{intent_text}")
    goal_text = str(goal.get("goal_text") or "").strip()
    if goal_text and goal_text != intent_text:
        summary.append(goal_text)
    source_intent = str((user_intent or {}).get("source_diagnose_intent") or "").strip()
    if source_intent:
        summary.append("上游证据采集请求仅用于追溯，不作为新的业务目标。")
    summary.append(
        "本轮 spec 继承 objective 的业务目标和质量门禁，只定义如何更稳、更安全地达到它。"
    )
    hard_constraints = list(DEFAULT_FORBIDDEN_CHANGES)
    hard_constraints.append("只在 agent 实际检查过且允许的 target 范围内修改。")
    if required_behavior:
        hard_constraints.append("必须满足的行为：")
        hard_constraints.extend(required_behavior[:5])
    optimization_bias = [
        "generalization_first：默认优先稳健、泛化、少回归。",
        "objective_specific：当 objective 明确是窄业务目标时启用，优先关键路径、业务约束和输出契约；但不得发明 objective 之外的新业务规则，不得硬编码 case 答案。",
    ]
    success_criteria = [
        _metric_requirement(acceptance_criteria.get("primary_metric") or {}),
    ]
    return {
        "objective_summary": summary,
        "business_hard_constraints": hard_constraints,
        "optimization_bias": optimization_bias,
        "success_criteria": success_criteria,
    }


def _current_strategy_summary(
    clusters: list[dict[str, Any]],
    discovery: dict[str, Any],
    evidence: dict[str, Any],
    user_intent: dict[str, Any] | None = None,
    input_mode: str = "diagnose",
) -> list[str]:
    intent_text = str((user_intent or {}).get("intent_text") or "").strip()
    evidence_summary = (
        f"面向由用户目标推导的 {evidence.get('case_count', 0)} 个 prospective cases 优化，"
        "不得将其表述为历史 session 事实。"
        if input_mode == "direct_goal"
        else _historical_evidence_summary(evidence)
    )
    cluster_summary = (
        "将目标能力风险聚类作为策略输入，但不能修改评测样本、评分逻辑或 objective 定义。"
        if input_mode == "direct_goal"
        else "将证据归纳出的根因聚类作为策略输入，但不能据此修改评测样本、评分逻辑或 objective 定义。"
    )
    summary = (
        [f"所有策略选择必须服务当前优化目标：{intent_text}。"] if intent_text else []
    ) + [
        evidence_summary,
        cluster_summary,
        "仅在 agentic discovery（文件发现）已检查并确认的候选 agent/workspace 目标中实施改动。",
    ]
    if clusters:
        summary.append(
            "按受影响样本数优先处理失败模式："
            + "; ".join(
                f"{_mode(c)} ({c.get('affected_case_count', 0)})" for c in clusters[:3]
            )
            + "。"
        )
    targets = discovery.get("inspected_or_candidate_files") or []
    if targets:
        summary.append(
            "初始安全改动面：" + "; ".join(str(t) for t in targets[:5]) + "。"
        )
    return summary[:5]


def _active_optimization_directions(
    clusters: list[dict[str, Any]],
    discovery: dict[str, Any],
    max_active: int,
    input_mode: str = "diagnose",
) -> list[dict[str, Any]]:
    scored: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        mode = _mode(cluster)
        direction_id = MODE_TO_DIRECTION.get(mode, "MD-BEHAVIOR-001")
        entry = scored.setdefault(
            direction_id,
            {"direction_id": direction_id, "affected": 0, "modes": [], "analyses": []},
        )
        entry["affected"] += int(cluster.get("affected_case_count") or 0)
        entry["modes"].append(mode)
        entry["analyses"].append(str(cluster.get("problem_analysis") or ""))
    if not scored:
        scored["MD-BEHAVIOR-001"] = {
            "direction_id": "MD-BEHAVIOR-001",
            "affected": 0,
            "modes": ["unknown_failure_mode"],
            "analyses": ["没有可用的具体失败聚类。"],
        }
    ordered = sorted(
        scored.values(), key=lambda x: (-int(x["affected"]), x["direction_id"])
    )
    directions: list[dict[str, Any]] = []
    for idx, item in enumerate(ordered[:max_active], 1):
        priority = "high" if idx <= 2 and item["affected"] > 0 else "medium"
        direction_id = item["direction_id"]
        meta = DIRECTION_CATALOG.get(direction_id, {})
        label = meta.get("label", direction_id)
        modes = _unique(item["modes"])
        directions.append(
            {
                "direction_id": direction_id,
                "tc_code": meta.get("tc_code", "TC.WORKFLOW_PLANNER.INSTRUCTION"),
                "module": meta.get("module", "WORKFLOW_PLANNER"),
                "problem_type": meta.get("problem_type", "INSTRUCTION"),
                "priority": priority,
                "why_this_round": (
                    f"{item['affected']} 个 prospective cases 指向 {label}："
                    if input_mode == "direct_goal"
                    else f"{item['affected']} 个证据样本指向 {label}："
                )
                + ", ".join(modes[:4])
                + "。",
                "expected_effect": _expected_effect(direction_id, modes, discovery),
                "related_failure_modes": modes,
            }
        )
    return directions


def _expected_effect(
    direction_id: str, modes: list[str], discovery: dict[str, Any]
) -> str:
    target_phrase = "已检查的目标文件"
    targets = discovery.get("inspected_or_candidate_files") or []
    if targets:
        target_phrase = ", ".join(str(t) for t in targets[:3])
    if direction_id == "SKILL-TRIGGER-001":
        return f"提升 agent 在回答前从 {target_phrase} 选择正确 skill/tool 路径的能力。"
    if direction_id == "MCP-CALL-SELECT-001":
        return f"提升 {target_phrase} 中工具参数质量、重试/降级行为和证据检索可靠性。"
    return f"改进 {target_phrase} 中的 prompt/Markdown 行为指导，使答案使用证据、保持验证，并避免不必要阻塞。"


def _failure_modes_to_address(
    clusters: list[dict[str, Any]], directions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    active_ids = {d["direction_id"] for d in directions}
    rows: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters[:8], 1):
        mode = _mode(cluster)
        direction_id = MODE_TO_DIRECTION.get(mode, "MD-BEHAVIOR-001")
        meta = DIRECTION_CATALOG.get(direction_id, {})
        if direction_id not in active_ids and directions:
            direction_id = directions[-1]["direction_id"]
            meta = DIRECTION_CATALOG.get(direction_id, {})
        rows.append(
            {
                "id": f"FM-{index:03d}",
                "tc_code": meta.get("tc_code", "TC.WORKFLOW_PLANNER.INSTRUCTION"),
                "related_direction_id": direction_id,
                "failure_mode": mode,
                "evidence": _failure_evidence(cluster),
                "priority": _cluster_priority(cluster, index),
                "suggested_direction": cluster.get("optimization_goal")
                or MODE_BEHAVIOR.get(mode, MODE_BEHAVIOR["unknown_failure_mode"]),
            }
        )
    if not rows:
        rows.append(
            {
                "id": "FM-001",
                "related_direction_id": directions[0]["direction_id"]
                if directions
                else "MD-BEHAVIOR-001",
                "failure_mode": "unknown_failure_mode",
                "evidence": "没有可用的具体证据失败聚类。",
                "priority": "medium",
                "suggested_direction": MODE_BEHAVIOR["unknown_failure_mode"],
            }
        )
    return rows


def _failure_evidence(cluster: dict[str, Any]) -> str:
    examples = cluster.get("example_case_ids") or cluster.get("example_queries") or []
    example_text = "; ".join(str(x) for x in examples[:3]) or "N/A"
    return (
        f"影响样本数：{cluster.get('affected_case_count', 0)}；"
        f"示例：{example_text}；分析：{cluster.get('problem_analysis', '')}"
    )


def _cluster_priority(cluster: dict[str, Any], index: int) -> str:
    affected = int(cluster.get("affected_case_count") or 0)
    if index <= 2 or affected >= 3:
        return "high"
    if affected >= 1:
        return "medium"
    return "low"


def _problem_statement(
    clusters: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    input_mode: str = "diagnose",
) -> dict[str, Any]:
    top = clusters[:3]
    summary = (
        f"用户目标被展开为 {len(cases)} 个 prospective cases 和 {len(clusters)} 类能力风险。"
        "它们用于定义未来验证范围，不构成历史失败证据。"
        if input_mode == "direct_goal"
        else f"当前评测集暴露 {len(clusters)} 类主要问题，覆盖 {len(cases)} 个样本。"
        "优化必须优先解决高频失败模式，同时保护 good regression 回归门禁。"
    )
    return {
        "summary": summary,
        "problems": [
            {
                "failure_mode": c.get("evolution_failure_mode"),
                "affected_case_count": c.get("affected_case_count"),
                "analysis": c.get("problem_analysis"),
                "example_queries": c.get("example_queries") or [],
            }
            for c in top
        ],
    }


def _initial_history(input_mode: str) -> str:
    if input_mode == "direct_goal":
        return "初始版本由用户 --goal、prospective cases 和只读 workspace discovery 生成；无历史 Diagnose/session 证据。"
    return "初始版本由冻结 plan-source 和 agentic discovery（文件发现）生成。"


def _optimizable_contents(
    clusters: list[dict[str, Any]], inspected_targets: list[str]
) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    if inspected_targets:
        contents.append(
            {
                "type": "inspected_target",
                "items": inspected_targets,
                "reason": "agentic discovery（文件发现）已确认这些具体文件/目录可作为候选改动目标。",
            }
        )
    for cluster in clusters:
        contents.append(
            {
                "type": "candidate_target_hint",
                "failure_mode": cluster.get("evolution_failure_mode"),
                "items": cluster.get("allowed_update_targets_hint") or [],
                "evidence_file_hints": cluster.get("evidence_file_hints") or [],
                "tool_hints": cluster.get("tool_hints") or [],
                "reason": cluster.get("problem_analysis"),
            }
        )
    if not contents:
        contents.append(
            {
                "type": "default",
                "items": DEFAULT_UPDATE_TARGETS,
                "reason": "没有可用的具体聚类提示；先从保守的候选 bot/workspace 指令优化开始。",
            }
        )
    return contents


def _preliminary_optimization_plan(
    clusters: list[dict[str, Any]],
    inspected_targets: list[str],
    input_mode: str = "diagnose",
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for priority, cluster in enumerate(clusters[:5], 1):
        mode = _mode(cluster)
        steps.append(
            {
                "priority": priority,
                "failure_mode": mode,
                "hypothesis": cluster.get("problem_analysis"),
                "proposed_change": _proposed_change(mode, cluster, inspected_targets),
                "candidate_targets": inspected_targets
                or cluster.get("allowed_update_targets_hint")
                or MODE_TARGETS.get(mode, DEFAULT_UPDATE_TARGETS),
                "validation": (
                    f"运行 prospective cases {cluster.get('example_case_ids') or cluster.get('example_queries') or []}；"
                    f"验证 `{mode}` 风险得到控制且用户目标满足。"
                    if input_mode == "direct_goal"
                    else f"重跑样本 {cluster.get('example_case_ids') or cluster.get('example_queries') or []}；"
                    f"验证 `{mode}` 消失且 good regression 指标不下降。"
                ),
                "risk_control": "只修改候选 bot/workspace 文件；不要修改 judge/templates/生产配置/密钥。",
            }
        )
    if not steps:
        steps.append(
            {
                "priority": 1,
                "failure_mode": "unknown_failure_mode",
                "hypothesis": "没有可用的具体聚类。",
                "proposed_change": "先补充轻量 trace/指令优化，然后重新生成上游 Source 收集更强证据。",
                "candidate_targets": inspected_targets or DEFAULT_UPDATE_TARGETS,
                "validation": "重新运行生成的 ClawWeb domain，并对比总分和 transcript 稳定性。",
                "risk_control": "保持改动小且可回滚。",
            }
        )
    return steps


def _proposed_change(
    mode: str, cluster: dict[str, Any], inspected_targets: list[str]
) -> str:
    if mode == "retrieval_not_called":
        return "新增或强化路由策略，确保需要证据的问题在答案合成前必须调用检索。"
    if mode == "retrieval_bad_query":
        return "增加单意图 query rewrite 示例，并在检索调用前做参数校验。"
    if mode == "retrieval_relevant_but_not_used":
        return "在答案合成中加入证据使用 checklist，要求最终答案引用并使用检索到的相关事实。"
    if mode == "retrieval_or_knowledge_failure":
        return (
            "改进检索降级：改写 query，在安全时重试一次；仍无证据时说明不确定，不编造。"
        )
    if mode == "tool_parameter_error":
        return "增加调用前参数校验，并对必填参数做自动修复。"
    if mode == "tool_execution_failure":
        return "为工具错误增加有限重试、替代路径和明确的失败降级行为。"
    if mode == "runtime_config_missing":
        return "为缺失运行时配置补充候选侧配置诊断文档和安全降级说明。"
    if mode == "good_regression":
        return "将这些样本标记为回归保护；不要通过收窄已通过行为来优化。"
    return cluster.get("optimization_goal") or MODE_BEHAVIOR.get(
        mode, MODE_BEHAVIOR["unknown_failure_mode"]
    )


def _original_model_distribution(plan: dict[str, Any]) -> dict[str, int]:
    dist = (plan.get("case_distribution") or {}).get("by_original_model") or {}
    if isinstance(dist, dict) and dist:
        return {str(k): _safe_int(v) for k, v in dist.items() if str(k)}
    counts: dict[str, int] = {}
    for case in plan.get("cases") or []:
        model = str(case.get("original_model") or "unknown")
        counts[model] = counts.get(model, 0) + 1
    return counts


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _acceptance_criteria(goal: dict[str, Any]) -> dict[str, Any]:
    primary_metric = dict(goal.get("primary_metric") or {})
    criteria = {
        "max_optimization_iterations": goal["max_iterations"],
        "primary_metric": primary_metric,
        "regression_drop_max": goal["regression_drop_max"],
        "case_timeout_seconds": goal["case_timeout_seconds"],
        "search_required_cases_must_search": True,
        "relevant_evidence_required_for_high_score": True,
        "no_hallucination_without_evidence": True,
        "step_budget_default": 10,
        "stable_run_required": True,
    }
    return criteria


def _patch_instruction(
    *,
    input_mode: str,
    domain: dict[str, Any],
    creation_scopes: list[str],
    reference_files: list[str],
    planned_deliverables: list[dict[str, Any]],
) -> str:
    validation_scope = (
        "用本轮 prospective cases 回放验证"
        if input_mode == "direct_goal"
        else "用本轮已发布的 train/test ClawWeb domains 回放评测"
        if _domain_is_published(domain)
        else "用本轮生成的本地评测集回放验证"
    )
    base = (
        "只在 candidate bot/workspace 中修改 allowed_update_targets 指向的 skill、脚本、md/reference、"
        "agent prompt/bootstrap 或候选配置诊断文档；每个改动必须映射到 root_cause_clusters 中的一个问题。"
    )
    if planned_deliverables:
        paths = ", ".join(
            str(item.get("path") or "").strip()
            for item in planned_deliverables
            if str(item.get("path") or "").strip()
        )
        scopes = ", ".join(creation_scopes)
        base += (
            f" 允许在 allowed_creation_scopes（{scopes}）内部创建 planned_deliverables（{paths}）；"
            "Plan 本身不创建这些文件，创建动作只能由后续 patch/evolve loop 执行。"
        )
    if reference_files:
        base += (
            " reference_files 仅供只读参考，不得因为其被检查过就修改："
            + ", ".join(reference_files)
            + "。"
        )
    return base + f" 完成后{validation_scope}。"


def _allowed_change_areas(inspected_targets: list[str]) -> list[str]:
    if not inspected_targets:
        return list(DEFAULT_ALLOWED_CHANGE_AREAS)
    return [
        f"`{target}`：仅实施与证据根因和当前优化目标直接相关的最小改动。"
        for target in inspected_targets
    ]


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


def _domain_is_published(domain: dict[str, Any]) -> bool:
    nested = domain.get("domains") if isinstance(domain, dict) else None
    if isinstance(nested, dict) and nested:
        expected = [nested.get("train"), nested.get("test")]
        return all(isinstance(item, dict) and _single_domain_is_published(item) for item in expected)
    return _single_domain_is_published(domain)


def _single_domain_is_published(domain: Any) -> bool:
    if not isinstance(domain, dict):
        return False
    status = str(domain.get("status") or "").strip().lower()
    return bool(
        domain.get("published") is True
        or status in {"ok", "success", "succeeded", "uploaded", "published"}
    )


def _evaluation_scope(spec: dict[str, Any]) -> str:
    if str(spec.get("input_mode") or "diagnose") == "direct_goal":
        return "由用户目标生成的 prospective cases"
    deliverables = spec.get("deliverables") or {}
    domain = deliverables.get("clawweb_domains") or deliverables.get("clawweb_domain") or {}
    if _domain_is_published(domain):
        return "已发布的 ClawWeb domain 评测样本"
    return "本轮生成的本地评测样本"


def _historical_evidence_summary(evidence: dict[str, Any]) -> str:
    domain = evidence.get("clawweb_domains") or evidence.get("clawweb_domain") or {}
    scope = (
        "已发布的 ClawWeb 评测集（train/test 双 Domain）"
        if _domain_is_published(domain)
        else "本轮生成的本地评测集"
    )
    return f"面向{scope}（{evidence.get('case_count', 0)} 个样本）优化，而不是手工定制答案。"
