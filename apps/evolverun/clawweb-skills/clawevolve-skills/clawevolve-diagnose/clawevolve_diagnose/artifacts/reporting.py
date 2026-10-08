from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..models import CasePreference, Diagnosis, SessionRow
from ..artifacts.case_ids import case_id


@dataclass(frozen=True)
class RelatedFile:
    path: str
    reason: str
    score: int


class RelatedFileLocator:
    """Locate likely source/docs files for selected problem sessions.

    This is intentionally a lightweight workspace locator.  It does not replace
    the agent's own code-reading judgement; it creates a concise starting point
    for the report and the downstream plan stage.
    """

    def __init__(self, layout: dict[str, Any], max_files_per_case: int = 8):
        self.layout = layout
        self.max_files_per_case = max_files_per_case

    def locate(self, diagnosis: Diagnosis) -> list[RelatedFile]:
        terms = self._terms(diagnosis)
        candidates: list[RelatedFile] = []
        for root in self._roots():
            root_path = Path(root).expanduser()
            if not root_path.exists():
                continue
            for path in self._iter_candidate_files(root_path):
                score, reason = self._score(path, terms)
                if score > 0:
                    candidates.append(RelatedFile(str(path), reason, score))
        candidates.sort(key=lambda x: (-x.score, len(x.path), x.path))
        return candidates[: self.max_files_per_case]

    def _roots(self) -> list[str]:
        roots: list[str] = []
        for key in ("self_skill", "skill_dirs", "doc_dirs"):
            for value in self.layout.get(key, []) or []:
                if value not in roots:
                    roots.append(value)
        workspace = self.layout.get("workspace")
        if workspace and workspace not in roots:
            roots.append(str(workspace))
        return roots

    def _iter_candidate_files(self, root: Path) -> Iterable[Path]:
        suffixes = {
            ".md",
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".sh",
        }
        ignored = {
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "dist",
            "build",
            "target",
            ".cache",
            ".next",
        }
        max_dirs = 3000
        max_files = 1200
        seen_dirs = 0
        yielded = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                seen_dirs += 1
                if seen_dirs > max_dirs:
                    break
                dirnames[:] = [
                    d for d in dirnames if d not in ignored and not d.startswith(".")
                ]
                for filename in filenames:
                    path = Path(dirpath) / filename
                    if filename == "SKILL.md" or path.suffix in suffixes:
                        yielded += 1
                        yield path
                        if yielded >= max_files:
                            return
        except OSError:
            return

    def _terms(self, diagnosis: Diagnosis) -> list[str]:
        terms = [
            diagnosis.common_problem_key,
            diagnosis.evolution_failure_mode,
            diagnosis.symptom_class,
        ]
        terms.extend(diagnosis.tool_hints or [])
        for hint in diagnosis.evidence_file_hints:
            if hint.get("term"):
                terms.append(str(hint["term"]))
        out: list[str] = []
        seen: set[str] = set()
        for term in terms:
            value = str(term or "").strip()
            if value and value not in seen:
                out.append(value)
                seen.add(value)
        return out

    def _score(self, path: Path, terms: list[str]) -> tuple[int, str]:
        name = path.name.lower()
        path_text = str(path).lower()
        score = 0
        reasons: list[str] = []
        if path.name == "SKILL.md":
            score += 5
            reasons.append("skill entrypoint")
        for term in terms:
            t = term.lower().replace("_", "-")
            alt = term.lower().replace("-", "_")
            if t and (t in path_text or alt in path_text):
                score += 4
                reasons.append(f"path matches {term}")
        if name in {"readme.md", "claude.md"}:
            score += 2
            reasons.append("operator documentation")
        return score, "; ".join(reasons)


class AnalysisReportBuilder:
    """Render a product-oriented diagnosis report with an auditable appendix."""

    def __init__(self, locator: RelatedFileLocator):
        self.locator = locator

    def build(
        self,
        bot_id: str,
        diagnoses: list[Diagnosis],
        selection_report: dict[str, Any],
        *,
        rows: list[SessionRow] | None = None,
        preference: CasePreference | None = None,
        product_outcome: dict[str, Any] | None = None,
    ) -> str:
        rows = rows or []
        preference = preference or CasePreference()
        outcome = product_outcome or {}
        scope = outcome.get("diagnosis_scope") or {}
        lines = [f"# clawevolve-diagnose 分析报告：{bot_id}", ""]
        lines.extend(self._overview(outcome))
        lines.extend(self._scope(scope, rows, diagnoses, preference))
        lines.extend(self._issues(outcome))
        lines.extend(self._evidence(diagnoses))
        lines.extend(self._recovery(outcome))
        lines.extend(self._technical_appendix(diagnoses, selection_report))
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _overview(outcome: dict[str, Any]) -> list[str]:
        lines = ["## 总体诊断", ""]
        lines.append(f"- 状态：`{outcome.get('diagnosis_status') or 'unknown'}`")
        lines.append(f"- 模式：`{outcome.get('diagnosis_mode') or 'exploratory'}`")
        lines.append(f"- 结论：{outcome.get('overall_conclusion') or '未形成结论。'}")
        hypothesis = outcome.get("hypothesis_result") or {}
        if hypothesis:
            lines.append(f"- 待验证假设：{hypothesis.get('hypothesis') or '未提供'}")
            lines.append(f"- 假设结论：`{hypothesis.get('verdict') or 'unknown'}`")
        return lines + [""]

    @staticmethod
    def _scope(
        scope: dict[str, Any],
        rows: list[SessionRow],
        diagnoses: list[Diagnosis],
        preference: CasePreference,
    ) -> list[str]:
        discovered = scope.get("discovered_session_count", len(rows))
        analyzed = scope.get("analyzed_session_count", len(diagnoses))
        selected = scope.get("selected_case_count", len(diagnoses))
        requested = scope.get("requested_case_count", preference.case_limit)
        coverage = _percent(scope.get("coverage_ratio"))
        return [
            "## 实际诊断范围",
            "",
            f"- 用户关注：{scope.get('intent') or preference.intent_text or preference.raw_message or '未限定'}",
            f"- 时间范围：{_time_range(scope.get('time_range') or {})}",
            f"- 发现 session：{discovered}",
            f"- 实际分析 session：{analyzed}（覆盖率 {coverage}）",
            f"- 最终选中 case：{selected}/{requested}",
            f"- 是否发生抽样：{bool(scope.get('sampling_applied'))}",
            "",
        ]

    @staticmethod
    def _issues(outcome: dict[str, Any]) -> list[str]:
        lines = ["## 优先问题", ""]
        issues = outcome.get("prioritized_issues") or []
        if not issues:
            return lines + ["- 当前证据中没有可归纳的 bad 问题。", ""]
        for index, issue in enumerate(issues, start=1):
            lines.extend(
                [
                    f"### {index}. {issue.get('title') or issue.get('failure_mode') or '未命名问题'}",
                    "",
                    f"- 严重度：`{issue.get('severity') or 'unknown'}`",
                    f"- 频次：{issue.get('case_count', 0)} cases（{_percent(issue.get('frequency'))}）",
                    f"- 平均置信度：{_percent(issue.get('confidence'))}",
                    f"- 证据 session：{', '.join(f'`{item}`' for item in issue.get('evidence_sessions') or []) or '无'}",
                    f"- 进化方向：{issue.get('evolution_direction') or '待进一步分析'}",
                    "",
                ]
            )
        return lines

    def _evidence(self, diagnoses: list[Diagnosis]) -> list[str]:
        lines = ["## Session 证据", ""]
        if not diagnoses:
            return lines + ["- 无可展示的 session 证据。", ""]
        for diagnosis in diagnoses:
            lines.append(
                f"### `{case_id(diagnosis)}` / session `{diagnosis.session.session_id}` / {diagnosis.case_type}"
            )
            lines.append("")
            lines.append(
                f"- 用户任务：{diagnosis.query or diagnosis.original_query or '无'}"
            )
            lines.append(f"- 问题判断：{diagnosis.root_cause_summary or '无'}")
            lines.append(f"- failure mode：`{diagnosis.evolution_failure_mode}`")
            lines.append(f"- 置信度：{diagnosis.confidence:.0%}")
            related = self.locator.locate(diagnosis)
            if related:
                lines.append("- 后续进化建议优先检查：")
                lines.extend(
                    f"  - `{item.path}`（{item.reason}）" for item in related[:5]
                )
            lines.append("")
        return lines

    @staticmethod
    def _recovery(outcome: dict[str, Any]) -> list[str]:
        actions = outcome.get("recovery_actions") or []
        if not actions:
            return []
        lines = ["## 数据不足时如何恢复", ""]
        for action in actions:
            lines.append(
                f"- **{action.get('title') or action.get('code')}**：{action.get('instruction') or ''}"
            )
        return lines + [""]

    @staticmethod
    def _technical_appendix(
        diagnoses: list[Diagnosis], selection_report: dict[str, Any]
    ) -> list[str]:
        judge_lookup = selection_report.get("judge_lookup")
        if not isinstance(judge_lookup, dict):
            judge_lookup = {}
        return [
            "## 技术附录",
            "",
            "本报告的 case 类型、失败模式与根因来自本次 session judge；"
            "文件线索仅作为后续进化的阅读起点，不代表 Diagnose 已修改任何环境。",
            "",
            f"- case type 分布：`{dict(Counter(item.case_type for item in diagnoses))}`",
            f"- failure mode 分布：`{dict(Counter(item.evolution_failure_mode for item in diagnoses))}`",
            f"- selection status：`{selection_report.get('status') or 'unknown'}`",
            f"- judge stop reason：`{judge_lookup.get('judge_stop_reason') or ''}`",
            "",
            "后续 Plan 应基于上述证据和实际文件检查生成目标文档；"
            "不得把候选路径当作已完成修改，也不得仅凭 failure mode 名称泛泛推断。",
        ]


def _percent(value: Any) -> str:
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "0%"


def _time_range(value: dict[str, Any]) -> str:
    label = str(value.get("label") or "").strip()
    since = str(value.get("since") or "").strip()
    until = str(value.get("until") or "").strip()
    if label:
        return label
    if since or until:
        return f"{since or '不限'} 至 {until or '不限'}"
    return "未限定"
