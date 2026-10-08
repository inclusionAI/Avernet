from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from .constants import MODE_OPTIMIZATION_GOALS
from .models import CasePreference, Diagnosis, SessionRow


class DiagnosisMode(str, Enum):
    """User-facing diagnosis strategy."""

    EXPLORATORY = "exploratory"
    HYPOTHESIS = "hypothesis"

    @classmethod
    def normalize(cls, value: Any) -> "DiagnosisMode":
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.EXPLORATORY


class DiagnosisStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class HypothesisVerdict(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    NOT_SUPPORTED = "not_supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class PrioritizedIssue:
    failure_mode: str
    title: str
    case_count: int
    frequency: float
    confidence: float
    severity: str
    optimization_value: str
    controllability: str
    evidence_sessions: tuple[str, ...]
    evidence_examples: tuple[str, ...]
    evolution_direction: str
    priority_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_mode": self.failure_mode,
            "title": self.title,
            "severity": self.severity,
            "case_count": self.case_count,
            "frequency": round(self.frequency, 4),
            "confidence": round(self.confidence, 4),
            "optimization_value": self.optimization_value,
            "controllability": self.controllability,
            "evidence_sessions": list(self.evidence_sessions),
            "evidence_examples": list(self.evidence_examples),
            "evolution_direction": self.evolution_direction,
            "priority_score": round(self.priority_score, 4),
        }


class IssuePrioritizer:
    """Turn session-level judge results into actionable product issues."""

    def prioritize(self, diagnoses: Iterable[Diagnosis]) -> list[PrioritizedIssue]:
        bad = [item for item in diagnoses if item.case_type == "bad"]
        if not bad:
            return []
        groups: dict[str, list[Diagnosis]] = defaultdict(list)
        for item in bad:
            groups[item.evolution_failure_mode or "unknown_failure_mode"].append(item)
        total = len(bad)
        issues = [self._build(mode, items, total) for mode, items in groups.items()]
        return sorted(
            issues,
            key=lambda item: (
                -item.priority_score,
                -item.case_count,
                item.failure_mode,
            ),
        )

    def _build(self, mode: str, items: list[Diagnosis], total: int) -> PrioritizedIssue:
        frequency = len(items) / max(1, total)
        confidence = sum(_clamp01(item.confidence) for item in items) / len(items)
        optimization_value = _dominant_level(item.optimization_value for item in items)
        controllability = _dominant_level(
            item.failure_controllability for item in items
        )
        priority_score = (
            frequency * 0.45
            + confidence * 0.25
            + _level_score(optimization_value) * 0.20
            + _level_score(controllability) * 0.10
        )
        severity = (
            "high"
            if priority_score >= 0.72 or (frequency >= 0.5 and confidence >= 0.65)
            else "medium"
            if priority_score >= 0.42
            else "low"
        )
        summaries = _unique(
            item.root_cause_summary for item in items if item.root_cause_summary
        )
        return PrioritizedIssue(
            failure_mode=mode,
            title=_human_title(mode, items),
            case_count=len(items),
            frequency=frequency,
            confidence=confidence,
            severity=severity,
            optimization_value=optimization_value,
            controllability=controllability,
            evidence_sessions=tuple(
                _unique(item.session.session_id for item in items)[:5]
            ),
            evidence_examples=tuple(summaries[:3]),
            evolution_direction=MODE_OPTIMIZATION_GOALS.get(
                mode, MODE_OPTIMIZATION_GOALS["unknown_failure_mode"]
            ),
            priority_score=priority_score,
        )


class HypothesisEvaluator:
    """Evaluate a concrete hypothesis only against relevant diagnosed modes."""

    def evaluate(
        self,
        *,
        preference: CasePreference,
        diagnoses: list[Diagnosis],
        selected: list[Diagnosis],
        status: DiagnosisStatus,
    ) -> dict[str, Any]:
        if (
            DiagnosisMode.normalize(preference.diagnosis_mode)
            is not DiagnosisMode.HYPOTHESIS
        ):
            return {}

        evidence = selected or diagnoses
        target_modes = {
            str(mode).strip()
            for mode in preference.target_failure_modes
            if str(mode).strip() and str(mode).strip() != "good_regression"
        }
        bad = [item for item in evidence if item.case_type == "bad"]
        supporting = [
            item
            for item in bad
            if not target_modes or item.evolution_failure_mode in target_modes
        ]
        counterexamples = [item for item in evidence if item.case_type == "good"]

        if status is DiagnosisStatus.INSUFFICIENT_EVIDENCE or not evidence:
            verdict = HypothesisVerdict.INSUFFICIENT_EVIDENCE
        elif supporting and counterexamples:
            verdict = HypothesisVerdict.PARTIALLY_SUPPORTED
        elif supporting:
            average_confidence = sum(
                _clamp01(item.confidence) for item in supporting
            ) / len(supporting)
            verdict = (
                HypothesisVerdict.SUPPORTED
                if average_confidence >= 0.65
                else HypothesisVerdict.PARTIALLY_SUPPORTED
            )
        else:
            verdict = HypothesisVerdict.NOT_SUPPORTED

        relevant_evidence = supporting + counterexamples
        return {
            "hypothesis": preference.hypothesis_text or preference.intent_text,
            "verdict": verdict.value,
            "target_failure_modes": sorted(target_modes),
            "supporting_case_count": len(supporting),
            "counterexample_case_count": len(counterexamples),
            "alternative_failure_case_count": len(bad) - len(supporting),
            "evidence_sessions": _unique(
                item.session.session_id for item in relevant_evidence
            )[:5],
        }


class RecoveryAdvisor:
    """Provide concrete next actions instead of terminal technical failures."""

    def recommend(
        self,
        *,
        rows: list[SessionRow],
        diagnoses: list[Diagnosis],
        selected: list[Diagnosis],
        preference: CasePreference,
        selection_report: dict[str, Any],
    ) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        judge = selection_report.get("judge_lookup") or {}
        stop_reason = (
            str(judge.get("judge_stop_reason") or "") if isinstance(judge, dict) else ""
        )
        if not rows:
            actions.append(
                _action(
                    "expand_session_scope",
                    "扩大 session 范围",
                    "当前范围内没有发现 session；扩大时间范围或确认 bot 的 session 数据目录后重试。",
                )
            )
        elif not diagnoses:
            actions.append(
                _action(
                    "repair_judge_or_relax_filter",
                    "恢复判定能力或放宽条件",
                    "已发现 session 但没有形成有效诊断；检查 judge 可用性，并减少过窄的主题/过滤条件。",
                )
            )
        if stop_reason in {"judge_auth_failed", "judge_unavailable"}:
            actions.insert(
                0,
                _action(
                    "repair_judge_runtime",
                    "修复诊断运行环境",
                    "session 已找到，但 judge 未能稳定完成分析；修复模型认证或运行配置后原范围重试。",
                ),
            )
        if len(selected) < preference.case_limit and rows:
            actions.append(
                _action(
                    "increase_analysis_budget",
                    "增加分析覆盖",
                    f"仅获得 {len(selected)}/{preference.case_limit} 个合格 case；增加最多分析 session 数或扩大时间范围。",
                )
            )
        quota = selection_report.get("quota_underfilled") or {}
        if quota:
            actions.append(
                _action(
                    "relax_case_mix",
                    "调整 good/bad 配额",
                    "当前 session 范围无法满足指定的 good/bad 分布；扩大范围或放宽配额后重试。",
                )
            )
        if not actions and not selected:
            actions.append(
                _action(
                    "review_scope",
                    "检查诊断范围",
                    "当前证据不足以形成可执行问题；检查时间、主题和 session 上限是否符合预期。",
                )
            )
        return _dedupe_actions(actions)


class DiagnosisOutcomeBuilder:
    """Build the stable product view consumed by reports and summaries."""

    def __init__(
        self,
        prioritizer: IssuePrioritizer | None = None,
        recovery_advisor: RecoveryAdvisor | None = None,
        hypothesis_evaluator: HypothesisEvaluator | None = None,
    ) -> None:
        self._prioritizer = prioritizer or IssuePrioritizer()
        self._recovery_advisor = recovery_advisor or RecoveryAdvisor()
        self._hypothesis_evaluator = hypothesis_evaluator or HypothesisEvaluator()

    def build(
        self,
        *,
        rows: list[SessionRow],
        diagnoses: list[Diagnosis],
        selected: list[Diagnosis],
        preference: CasePreference,
        selection_report: dict[str, Any],
    ) -> dict[str, Any]:
        issues = self._prioritizer.prioritize(selected or diagnoses)
        status = self._status(rows, diagnoses, selected, preference, selection_report)
        scope = self._scope(rows, diagnoses, selected, preference, selection_report)
        hypothesis = self._hypothesis_evaluator.evaluate(
            preference=preference,
            diagnoses=diagnoses,
            selected=selected,
            status=status,
        )
        recovery = self._recovery_advisor.recommend(
            rows=rows,
            diagnoses=diagnoses,
            selected=selected,
            preference=preference,
            selection_report=selection_report,
        )
        return {
            "diagnosis_status": status.value,
            "diagnosis_mode": DiagnosisMode.normalize(preference.diagnosis_mode).value,
            "diagnosis_scope": scope,
            "hypothesis_result": hypothesis,
            "prioritized_issues": [item.as_dict() for item in issues],
            "overall_conclusion": self._conclusion(status, issues, hypothesis, scope),
            "recovery_actions": recovery
            if status is not DiagnosisStatus.COMPLETE
            else [],
        }

    @staticmethod
    def _status(
        rows: list[SessionRow],
        diagnoses: list[Diagnosis],
        selected: list[Diagnosis],
        preference: CasePreference,
        selection_report: dict[str, Any],
    ) -> DiagnosisStatus:
        judge = selection_report.get("judge_lookup") or {}
        stop_reason = (
            str(judge.get("judge_stop_reason") or "") if isinstance(judge, dict) else ""
        )
        if (
            not rows
            or not diagnoses
            or stop_reason in {"judge_auth_failed", "judge_unavailable"}
        ):
            return DiagnosisStatus.INSUFFICIENT_EVIDENCE
        if len(selected) < preference.case_limit or selection_report.get(
            "quota_underfilled"
        ):
            return DiagnosisStatus.PARTIAL
        return DiagnosisStatus.COMPLETE

    @staticmethod
    def _scope(
        rows: list[SessionRow],
        diagnoses: list[Diagnosis],
        selected: list[Diagnosis],
        preference: CasePreference,
        selection_report: dict[str, Any],
    ) -> dict[str, Any]:
        judge = selection_report.get("judge_lookup") or {}
        judged_value = (
            judge.get("judged_session_count")
            if isinstance(judge, dict)
            else len(diagnoses)
        )
        judged = _nonnegative_int(judged_value, default=len(diagnoses))
        sampling_applied = len(selected) < len(diagnoses)
        coverage_ratio = min(1.0, judged / len(rows)) if rows else 0.0
        return {
            "intent": preference.intent_text or preference.raw_message,
            "mode": DiagnosisMode.normalize(preference.diagnosis_mode).value,
            "hypothesis": preference.hypothesis_text,
            "time_range": {
                "label": preference.time_range_label,
                "since": preference.since,
                "until": preference.until,
            },
            "discovered_session_count": len(rows),
            "analyzed_session_count": judged,
            "diagnosed_case_count": len(diagnoses),
            "selected_case_count": len(selected),
            "requested_case_count": preference.case_limit,
            "sampling_applied": sampling_applied,
            "coverage_ratio": round(coverage_ratio, 4),
            "selection_ratio": round(len(selected) / max(1, preference.case_limit), 4),
            "analysis_limit": preference.max_sessions,
        }

    @staticmethod
    def _conclusion(
        status: DiagnosisStatus,
        issues: list[PrioritizedIssue],
        hypothesis: dict[str, Any],
        scope: dict[str, Any],
    ) -> str:
        if status is DiagnosisStatus.INSUFFICIENT_EVIDENCE:
            return "当前范围内证据不足，尚不能形成可靠的进化结论；请按恢复建议补充数据后重试。"
        if hypothesis:
            verdict = hypothesis.get("verdict")
            mapping = {
                HypothesisVerdict.SUPPORTED.value: "现有 session 证据支持该假设。",
                HypothesisVerdict.PARTIALLY_SUPPORTED.value: "现有证据部分支持该假设，但存在反例或置信度不足。",
                HypothesisVerdict.NOT_SUPPORTED.value: "当前分析范围内未发现支持该假设的失败证据。",
            }
            prefix = mapping.get(str(verdict), "当前证据不足以判断该假设。")
        else:
            prefix = "已完成主要问题探索。"
        if issues:
            top = issues[0]
            return (
                f"{prefix} 首要问题是“{top.title}”，覆盖 {top.case_count} 个 bad case，"
                f"平均置信度 {top.confidence:.0%}。"
            )
        return f"{prefix} 在选中的 {scope.get('selected_case_count', 0)} 个 case 中未发现可归纳的 bad 问题。"


def _action(code: str, title: str, instruction: str) -> dict[str, str]:
    return {"code": code, "title": title, "instruction": instruction}


def _dedupe_actions(actions: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for action in actions:
        code = action["code"]
        if code not in seen:
            seen.add(code)
            result.append(action)
    return result


def _unique(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _nonnegative_int(value: Any, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _level_score(value: str) -> float:
    return {"high": 1.0, "medium": 0.6, "low": 0.25}.get(
        str(value or "").strip().lower(), 0.45
    )


def _dominant_level(values: Iterable[Any]) -> str:
    normalized = [str(value or "").strip().lower() for value in values]
    normalized = [value for value in normalized if value in {"high", "medium", "low"}]
    if not normalized:
        return "unknown"
    scores = {level: normalized.count(level) for level in {"high", "medium", "low"}}
    return max(scores, key=lambda level: (scores[level], _level_score(level)))


def _human_title(mode: str, items: list[Diagnosis]) -> str:
    common = _unique(item.common_problem_key for item in items)
    if common and common[0] not in {mode, "unknown_failure_mode"}:
        return common[0]
    return mode.replace("_", " ")
