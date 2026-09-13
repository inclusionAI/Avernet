"""
Report Generator Module for PinchBench Doctor.

Generates human-readable diagnosis reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from lib_patterns import Issue, IssueSeverity
from lib_llm_analyzer import LLMInsight


class ReportGenerator:
    """Generate human-readable diagnosis reports."""

    def generate_markdown(self, report: Any) -> str:
        """Generate a markdown summary report."""
        lines = []

        # Header
        lines.append("# 🏥 PinchBench Doctor 诊断报告")
        lines.append("")
        lines.append(f"- **运行 ID**: {report.run_id}")
        lines.append(f"- **模型**: {report.model}")
        lines.append(f"- **基准**: {report.benchmark}")
        lines.append(f"- **诊断时间**: {report.timestamp}")
        lines.append("")

        # Summary
        lines.append("## 📊 诊断摘要")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 诊断任务数 | {report.tasks_diagnosed} |")
        lines.append(f"| 发现问题数 | {report.total_issues} |")
        lines.append(f"| 生成补丁数 | {len(report.patches)} |")
        lines.append("")

        # Issues by severity
        if report.issues_by_severity:
            lines.append("### 问题严重度分布")
            lines.append("")
            lines.append("| 严重度 | 数量 |")
            lines.append("|--------|------|")
            for sev in ["high", "medium", "low"]:
                count = report.issues_by_severity.get(sev, 0)
                emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
                lines.append(f"| {emoji} {sev.upper()} | {count} |")
            lines.append("")

        # Issues by pattern
        if report.issues_by_pattern:
            lines.append("### 问题类型分布")
            lines.append("")
            lines.append("| 模式 ID | 数量 | 模式名称 |")
            lines.append("|---------|------|----------|")
            pattern_names = {
                "P001": "工具调用反复失败",
                "P002": "参数上下文丢失",
                "P004": "必要工具未调用",
            }
            for pattern_id, count in sorted(report.issues_by_pattern.items()):
                name = pattern_names.get(pattern_id, "未知")
                lines.append(f"| {pattern_id} | {count} | {name} |")
            lines.append("")

        # Task details
        if report.task_diagnoses:
            lines.append("## 📋 任务诊断详情")
            lines.append("")

            for task_diag in report.task_diagnoses:
                lines.append(f"### {task_diag.task_id}")
                lines.append("")
                lines.append(f"- **状态**: {task_diag.status}")
                lines.append(f"- **得分**: {task_diag.score:.2f}")
                lines.append(f"- **工具调用次数**: {len(task_diag.tool_calls)}")
                lines.append(f"- **问题数**: {len(task_diag.issues)}")
                lines.append("")

                if task_diag.issues:
                    lines.append("| 问题 ID | 模式 | 严重度 | 工具 | 描述 |")
                    lines.append("|---------|------|--------|------|------|")

                    for issue in task_diag.issues:
                        emoji = {
                            "high": "🔴",
                            "medium": "🟡",
                            "low": "🟢",
                        }.get(issue.severity.value, "⚪")
                        lines.append(
                            f"| {issue.issue_id[:8]} | {issue.pattern_id} | "
                            f"{emoji} {issue.severity.value} | "
                            f"{issue.tool_name or '-'} | "
                            f"{issue.description[:50]}... |"
                        )
                    lines.append("")

                    # Issue details
                    for issue in task_diag.issues:
                        lines.append(f"#### 问题 {issue.issue_id[:8]}")
                        lines.append("")
                        lines.append(f"**模式**: {issue.pattern_name} ({issue.pattern_id})")
                        lines.append("")
                        lines.append(f"**描述**: {issue.description}")
                        lines.append("")

                        if issue.evidence:
                            lines.append("**证据**:")
                            lines.append("```json")
                            import json
                            lines.append(json.dumps(issue.evidence, indent=2, ensure_ascii=False))
                            lines.append("```")
                            lines.append("")

                        if issue.suggestion:
                            lines.append(f"**建议**: {issue.suggestion}")
                            lines.append("")

                # LLM Insights
                if task_diag.llm_insights:
                    lines.append("#### 🤖 LLM 深度分析")
                    lines.append("")
                    for insight in task_diag.llm_insights:
                        emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(insight.severity, "⚪")
                        lines.append(f"**{emoji} {insight.category}** (置信度: {insight.confidence:.0%})")
                        lines.append("")
                        lines.append(f"- **描述**: {insight.description}")
                        lines.append(f"- **根本原因**: {insight.root_cause}")
                        lines.append(f"- **建议**: {insight.suggestion}")
                        lines.append("")
                        if insight.evidence:
                            lines.append("<details>")
                            lines.append("<summary>分析证据</summary>")
                            lines.append("")
                            lines.append("```json")
                            lines.append(json.dumps(insight.evidence, indent=2, ensure_ascii=False)[:500])
                            lines.append("```")
                            lines.append("</details>")
                            lines.append("")

        # Patches summary
        if report.patches:
            lines.append("## 🔧 生成的补丁")
            lines.append("")
            lines.append("| 补丁 ID | 类型 | 目标工具 | 相关问题 |")
            lines.append("|---------|------|----------|----------|")

            for patch in report.patches:
                lines.append(
                    f"| {patch.patch_id} | {patch.patch_type} | "
                    f"{patch.target_tool} | {', '.join(patch.related_issues[:3])} |"
                )
            lines.append("")
            lines.append(f"补丁文件位置: `doctor/output/{report.model.replace('/', '-')}/{report.run_id}/patches/`")
            lines.append("")

        # Footer
        lines.append("---")
        lines.append("")
        lines.append("*此报告由 PinchBench Doctor 自动生成*")

        return "\n".join(lines)

    def generate_json_summary(self, report: Any) -> Dict[str, Any]:
        """Generate a JSON summary of the diagnosis."""
        return {
            "run_id": report.run_id,
            "model": report.model,
            "benchmark": report.benchmark,
            "timestamp": report.timestamp,
            "summary": {
                "tasks_diagnosed": report.tasks_diagnosed,
                "total_issues": report.total_issues,
                "patches_generated": len(report.patches),
                "issues_by_severity": report.issues_by_severity,
                "issues_by_pattern": report.issues_by_pattern,
            },
            "tasks": [
                {
                    "task_id": td.task_id,
                    "status": td.status,
                    "score": td.score,
                    "issue_count": len(td.issues),
                }
                for td in report.task_diagnoses
            ],
        }

    def print_summary(self, report: Any) -> None:
        """Print a brief summary to stdout."""
        print("\n" + "=" * 60)
        print("🏥 Doctor 诊断摘要")
        print("=" * 60)
        print(f"运行 ID: {report.run_id}")
        print(f"模型: {report.model}")
        print(f"诊断任务数: {report.tasks_diagnosed}")
        print(f"发现问题数: {report.total_issues}")
        print(f"生成补丁数: {len(report.patches)}")
        print("")

        if report.issues_by_severity:
            print("问题严重度:")
            for sev in ["high", "medium", "low"]:
                count = report.issues_by_severity.get(sev, 0)
                if count > 0:
                    emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
                    print(f"  {emoji} {sev.upper()}: {count}")
            print("")

        if report.issues_by_pattern:
            print("问题类型:")
            for pattern_id, count in sorted(report.issues_by_pattern.items()):
                print(f"  - {pattern_id}: {count}")
            print("")

        print("=" * 60 + "\n")