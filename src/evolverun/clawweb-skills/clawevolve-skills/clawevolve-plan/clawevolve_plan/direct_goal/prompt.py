from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..discovery.prompt import open_skill_layout_instruction
from .schema import requested_bench_template_count


def direct_goal_schema_example(*, goal: str, workspace_root: Path) -> dict[str, Any]:
    requested_count = requested_bench_template_count(goal)
    openversion = os.environ.get("CLAWWEB_VERSION") == "openversion"
    skill_scope = "skills" if openversion else "skills/skills-local"
    reference_files = [] if openversion else [f"{skill_scope}/example/SKILL.md"]
    example = {
        "goal_analysis": {
            "raw_goal": goal,
            "task_scope": "用户希望新增或增强的任务能力范围",
            "desired_outcome": "用户明确期望达到的结果",
            "required_capabilities": ["完成目标所需能力"],
            "quality_requirements": ["可验证的质量要求"],
            "constraints": ["用户明确约束；无明确约束时写最小安全边界"],
            "requested_deliverables": ["skill 或其他明确交付物"],
        },
        "prospective_cases": [
            {
                "case_id": "goal-case-001",
                "case_type": "prospective",
                "query": "可独立回放的完整用户任务",
                "scenario": "该场景为什么能验证用户目标",
                "expected_behavior": "成功行为",
                "forbidden_behavior": ["不可接受行为"],
                "success_criteria": ["可由 LLM judge 判断的成功标准"],
                "scoring_hints": ["个性化评分关注点"],
                "failure_mode": "missing_skill_capability",
            }
        ],
        "discovery": {
            "analysis_summary": {
                "diagnosed_problem": "本次没有 Diagnose；概括用户想补齐的能力",
                "environment_root_cause": "当前 workspace 中的能力缺口",
                "optimization_strategy": "后续 patch loop 的窄范围策略",
            },
            "case_findings": [
                {
                    "case_id": "goal-case-001",
                    "case_type": "prospective",
                    "failure_mode": "missing_skill_capability",
                    "symptom": "若不优化，预期无法满足的行为",
                    "evidence": ["来自 --goal、已检查的创建范围和只读参考文件"],
                    "inspected_files": reference_files,
                    "environment_analysis": "基于已检查文件说明能力缺口",
                    "optimization_ideas": ["可执行优化方向"],
                    "root_cause_hypothesis": "goal 与 workspace 缺口的连接",
                    "confidence": "high|medium|low",
                }
            ],
            "target_findings": [
                {
                    "path": skill_scope,
                    "target_type": "creation_scope",
                    "reason": "已检查且足够窄的现有目录，可作为后续创建新 Skill 的安全范围",
                    "current_gap": "该范围内尚无满足用户目标的 Skill",
                    "proposed_change": "后续 patch loop 在该目录下创建 planned_deliverables",
                    "related_case_ids": ["goal-case-001"],
                    "failure_modes": ["missing_skill_capability"],
                    "confidence": "high|medium|low",
                }
            ],
            "merged_targets": [skill_scope],
            "reference_files": reference_files,
            "planned_deliverables": [
                {
                    "path": f"{skill_scope}/new-skill/SKILL.md",
                    "operation": "create",
                    "creation_scope": skill_scope,
                    "deliverable_type": "skill",
                    "reason": "用户明确要求创建的新 Skill 主文件",
                }
            ],
            "forbidden_boundary_check": {
                "passed": True,
                "checked": [
                    "no judge/scorer changes",
                    "no generated artifacts",
                    "no secrets or production credentials",
                    "targets are inside workspace_root and narrow",
                ],
                "notes": "边界检查说明",
            },
            "warnings": [],
        },
    }
    if requested_count is not None:
        base_case = example["prospective_cases"][0]
        base_finding = example["discovery"]["case_findings"][0]
        example["prospective_cases"] = []
        example["discovery"]["case_findings"] = []
        for index in range(1, requested_count + 1):
            case_id = f"goal-case-{index:03d}"
            case = dict(base_case)
            case["case_id"] = case_id
            case["query"] = f"第 {index} 个可独立回放的完整用户任务"
            finding = dict(base_finding)
            finding["case_id"] = case_id
            example["prospective_cases"].append(case)
            example["discovery"]["case_findings"].append(finding)
        example["discovery"]["target_findings"][0]["related_case_ids"] = [
            item["case_id"] for item in example["prospective_cases"]
        ]
    return example


def _open_empty_skill_instruction() -> str:
    if os.environ.get("CLAWWEB_VERSION") != "openversion":
        return ""
    return (
        "开源运行环境中，workspace/skills 为空是合法状态；此时无需寻找参考 Skill，"
        "reference_files 返回空数组，且不要搜索当前 workspace 之外的 Skill。确认该目录为空后停止调用工具，"
        "直接根据 user_goal 和 JSON 契约输出完整 JSON，不得只完成分析而不输出最终答案。\n"
    )


def build_direct_goal_prompt(
    *,
    goal: str,
    task_id: str,
    workspace_root: Path,
    output_path: Path,
) -> str:
    requested_count = requested_bench_template_count(goal)
    example = direct_goal_schema_example(goal=goal, workspace_root=workspace_root)
    inspection_rule = (
        "只读检查与目标最相关的 0-5 个 workspace 文件；workspace/skills 为空时可以不读取参考文件；"
        if os.environ.get("CLAWWEB_VERSION") == "openversion"
        else "只读检查与目标最相关的 1-5 个 workspace 文件；"
    )
    requested_count_rule = (
        f"用户明确要求 {requested_count} 个 bench templates；必须生成恰好 "
        f"{requested_count} 个 prospective_cases，最终一一生成 {requested_count} 个 templates。"
        if requested_count is not None
        else "用户未明确指定 bench template 数量时，生成 3-8 个最小高价值 prospective_cases。"
    )
    return f"""你是 clawevolve-plan 的 Direct Goal 规划 Agent。本次没有可用的 Diagnose 输出。请把用户 --goal 转换为可评测的预期 cases，并只读检查当前 bot workspace，定位安全、具体、真实存在的优化 target。

{open_skill_layout_instruction()}{_open_empty_skill_instruction()}
必须遵守：
1. --goal 是唯一用户需求来源。goal_analysis.raw_goal 可以用自然语言改写或展开，不要求逐字复述；不得擅自改变用户指定的回复内容、数值、范围或约束。原始输入由程序单独保存用于追溯。
2. 不得假设或伪造历史 session、Diagnose 结论、历史失败次数或历史成功率。
3. prospective_cases 是未来验证场景，每个 query 必须上下文独立、可直接回放。
4. {requested_count_rule}
5. {inspection_rule}不得全仓扫描、安装依赖、运行测试、访问网络或执行 git。
6. merged_targets 表示已存在、实际检查过的后续安全操作范围；必须位于 workspace_root 内且边界窄。
7. 新建 Skill/文件时，不得把尚不存在的未来路径放入 merged_targets；应把最近的、已存在且足够窄的父目录作为 target_type=creation_scope 的 merged target，并把未来路径写入 planned_deliverables。
8. reference_files 只表示已存在且实际检查过的只读参考，绝不能同时出现在 merged_targets。
9. planned_deliverables.operation 必须为 create，creation_scope 必须引用 merged_targets 中 target_type=creation_scope 的现有目录；未来路径必须位于该 scope 下。
10. 不得读取 secrets/token/.env，不得把 judge、scorer、templates、plan 输入输出、历史 artifacts 作为 target、reference 或 planned deliverable。
11. 只读检查后，在最终回复直接输出一个完整JSON对象；不写文件、不生成或运行Python/shell脚本。解析、校验与落盘由调用程序负责。JSON外不要输出解释或代码围栏。
12. 每个 prospective case 必须有对应 case_findings；每个 merged target 必须有对应 target_findings。
13. 最终JSON回复后立即结束，不继续调用工具。
14. 顶层和 discovery 中都不要输出 schema_version 或 workspace_root，也不要输出 goal_digest 或 original_goal；这些是调用程序绑定的字段。

任务信息：
- task_id: {task_id}
- workspace_root: {workspace_root}
- output_path: {output_path}
- user_goal: {goal}

JSON 契约示例：
{json.dumps(example, ensure_ascii=False, indent=2)}
"""
