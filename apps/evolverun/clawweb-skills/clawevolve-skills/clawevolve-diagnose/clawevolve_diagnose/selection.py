from __future__ import annotations

import copy
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .models import CasePreference, Diagnosis
from .utils import assess_replayability
from . import logger


@dataclass(frozen=True)
class CaseTypeQuota:
    """Absolute case-type targets derived from user preference.

    ``None`` means the selector may decide from ratios and available inventory.
    Absolute quotas are intentionally limited to good/bad case types; we do not
    support per-failure-mode hard quotas in diagnose because this stage should
    still discover representative self-evolution data instead of acting as a
    rigid benchmark curator.
    """

    bad: int | None = None
    good: int | None = None

    def requested_total(self) -> int | None:
        total = 0
        seen = False
        if self.bad is not None:
            total += max(0, self.bad)
            seen = True
        if self.good is not None:
            total += max(0, self.good)
            seen = True
        return total if seen else None


@dataclass(frozen=True)
class TimeWindow:
    """Optional source-session time filter."""

    since: datetime | None = None
    until: datetime | None = None
    label: str = ""

    def enabled(self) -> bool:
        return self.since is not None or self.until is not None

    def contains(self, raw: str) -> bool:
        return self.contains_datetime(parse_session_datetime(raw))

    def contains_datetime(self, dt: datetime | None) -> bool:
        if not self.enabled():
            return True
        if dt is None:
            return False
        if self.since and dt < self.since:
            return False
        if self.until and dt > self.until:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "label": self.label,
            "since": self.since.isoformat() if self.since else "",
            "until": self.until.isoformat() if self.until else "",
        }


@dataclass(frozen=True)
class SelectionRequest:
    preference: CasePreference
    quota: CaseTypeQuota
    time_window: TimeWindow

    @property
    def limit(self) -> int:
        return self.preference.case_limit


@dataclass
class SelectionResult:
    selected: list[Diagnosis]
    report: dict[str, Any]


@dataclass
class CandidatePool:
    all_diagnoses: list[Diagnosis]
    time_filtered: list[Diagnosis]
    qualified: list[Diagnosis]
    preferred: list[Diagnosis]
    rejected_counts: Counter[str] = field(default_factory=Counter)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "total_diagnoses": len(self.all_diagnoses),
            "time_filtered": len(self.time_filtered),
            "qualified": len(self.qualified),
            "preferred": len(self.preferred),
            "rejected_counts": dict(self.rejected_counts),
            "qualified_by_case_type": dict(Counter(d.case_type for d in self.qualified)),
            "qualified_by_failure_mode": dict(
                Counter(d.evolution_failure_mode for d in self.qualified)
            ),
            "preferred_by_case_type": dict(Counter(d.case_type for d in self.preferred)),
            "preferred_by_failure_mode": dict(
                Counter(d.evolution_failure_mode for d in self.preferred)
            ),
        }


class SelectionEngine:
    """Recall-first selector for self-evolution eval sets.

    The engine is intentionally object-oriented and stage-based: candidate pool
    construction, target planning, picking, backfill, and reporting are separate
    responsibilities.  This keeps future changes (for example better quality
    scoring) out of the upload/render pipeline.
    """

    def __init__(self, request: SelectionRequest):
        self.request = request
        self.policy = SelectorPolicy(request)
        # ``preferred`` and ``qualified`` contain references to the same copied
        # Diagnosis objects. Track object identity only to stop this selection
        # algorithm from adding one case twice across passes. Never compare
        # different OCSA cases by session, query, label, or semantic similarity.
        self._selected_case_objects: set[int] = set()

    def select(self, diagnoses: list[Diagnosis]) -> SelectionResult:
        self._selected_case_objects.clear()
        logger.info(
            "selection engine start",
            input_diagnoses=len(diagnoses),
            requested_cases=self.request.limit,
            requested_bad=self.request.quota.bad,
            requested_good=self.request.quota.good,
            time_window_enabled=self.request.time_window.enabled(),
        )
        pool = CandidatePoolBuilder(self.request).build(diagnoses)
        targets = self.policy.case_type_targets(pool.qualified)
        logger.info(
            "selection candidate pool built",
            total_diagnoses=len(pool.all_diagnoses),
            time_filtered=len(pool.time_filtered),
            qualified=len(pool.qualified),
            preferred=len(pool.preferred),
            targets=targets,
            qualified_by_case_type=dict(Counter(d.case_type for d in pool.qualified)),
            qualified_by_failure_mode=dict(Counter(d.evolution_failure_mode for d in pool.qualified)),
            rejected_counts=dict(pool.rejected_counts),
        )
        selected: list[Diagnosis] = []

        before = len(selected)
        self._take_by_case_type(
            selected,
            pool.preferred,
            targets,
            bucket="preferred",
            backfilled=False,
        )
        logger.info("selection preferred pass done", added=len(selected) - before, selected=len(selected))
        before = len(selected)
        self._ensure_good_minimum(selected, pool.qualified, targets)
        logger.info("selection good-minimum pass done", added=len(selected) - before, selected=len(selected))
        before = len(selected)
        self._backfill(selected, pool.preferred, pool.qualified, targets)
        logger.info("selection backfill pass done", added=len(selected) - before, selected=len(selected))

        selected = selected[: self.request.limit]
        report = SelectionReporter(self.request, pool, targets).build(selected)
        report["dedupe"] = {
            "enabled": False,
            "policy": "none_after_ocsa",
            "reason": "each OCSA case is selected independently",
            "duplicate_skipped_count": 0,
        }
        logger.info(
            "selection engine done",
            selected=len(selected),
            status=report.get("status"),
            selected_by_case_type=report.get("selected_by_case_type"),
            selected_by_failure_mode=report.get("selected_by_failure_mode"),
            quota_underfilled=report.get("quota_underfilled"),
            post_ocsa_dedupe_enabled=False,
        )
        return SelectionResult(selected=selected, report=report)

    def _take_by_case_type(
        self,
        selected: list[Diagnosis],
        candidates: list[Diagnosis],
        targets: dict[str, int],
        bucket: str,
        backfilled: bool,
    ) -> None:
        grouped = self._group_by_case_type(candidates)
        for case_type in self.policy.case_type_order():
            need = max(0, targets.get(case_type, 0) - self._count(selected, case_type))
            if need <= 0:
                continue
            for diag in self._round_robin_by_mode(grouped.get(case_type, [])):
                if need <= 0 or len(selected) >= self.request.limit:
                    break
                if not self._can_add(diag):
                    continue
                self._mark(diag, bucket, backfilled, self._reason(bucket, diag))
                selected.append(diag)
                need -= 1

    def _ensure_good_minimum(
        self,
        selected: list[Diagnosis],
        candidates: list[Diagnosis],
        targets: dict[str, int],
    ) -> None:
        good_target = targets.get("good", 0)
        if good_target <= 0:
            return
        good_now = self._count(selected, "good")
        if good_now >= good_target:
            return
        for diag in [d for d in candidates if d.case_type == "good"]:
            if good_now >= good_target or len(selected) >= self.request.limit:
                break
            if not self._can_add(diag):
                continue
            self._mark(
                diag,
                "backfill_good",
                True,
                "补充 good regression，用于保护优化后的稳定能力。",
            )
            selected.append(diag)
            good_now += 1

    def _backfill(
        self,
        selected: list[Diagnosis],
        preferred: list[Diagnosis],
        candidates: list[Diagnosis],
        targets: dict[str, int],
    ) -> None:
        preferred_objects = {id(d) for d in preferred}
        leftovers = [d for d in candidates if id(d) not in self._selected_case_objects]
        leftovers.sort(
            key=lambda d: (
                id(d) in preferred_objects,
                self.policy.optimization_rank(d),
                d.quality_score,
                d.confidence,
            ),
            reverse=True,
        )
        for diag in leftovers:
            if len(selected) >= self.request.limit:
                break
            if not self.policy.can_exceed_target(diag, selected, targets, leftovers):
                continue
            if not self._can_add(diag):
                continue
            self._mark(
                diag,
                "backfill_bad" if diag.case_type == "bad" else "backfill_good",
                True,
                "严格偏好候选不足，从全量高质量候选池回填。",
            )
            selected.append(diag)

    def _can_add(self, diag: Diagnosis) -> bool:
        """Reserve one candidate object without comparing it to other cases."""

        case_object = id(diag)
        if case_object in self._selected_case_objects:
            return False
        self._selected_case_objects.add(case_object)
        return True

    def _mark(self, diag: Diagnosis, bucket: str, backfilled: bool, reason: str) -> None:
        diag.selection_bucket = bucket
        diag.selection_reason = reason
        diag.backfilled = backfilled

    @staticmethod
    def _count(items: Iterable[Diagnosis], case_type: str) -> int:
        return sum(1 for d in items if d.case_type == case_type)

    @staticmethod
    def _group_by_case_type(items: list[Diagnosis]) -> dict[str, list[Diagnosis]]:
        grouped: dict[str, list[Diagnosis]] = defaultdict(list)
        for item in items:
            grouped[item.case_type].append(item)
        return grouped

    def _round_robin_by_mode(self, items: list[Diagnosis]) -> list[Diagnosis]:
        buckets: dict[str, list[Diagnosis]] = defaultdict(list)
        for d in sorted(
            items,
            key=lambda x: (self.policy.optimization_rank(x), x.quality_score, x.confidence),
            reverse=True,
        ):
            buckets[d.evolution_failure_mode].append(d)
        preferred_modes = self.request.preference.normalized_modes()
        keys = [m for m in preferred_modes if m in buckets] + [
            k for k in buckets if k not in preferred_modes
        ]
        out: list[Diagnosis] = []
        while keys:
            next_keys: list[str] = []
            for key in keys:
                bucket = buckets.get(key) or []
                if bucket:
                    out.append(bucket.pop(0))
                if bucket:
                    next_keys.append(key)
            keys = next_keys
        return out

    @staticmethod
    def _reason(bucket: str, diag: Diagnosis) -> str:
        if bucket == "preferred":
            return f"匹配用户偏好，failure_mode={diag.evolution_failure_mode}。"
        return "按选择策略选中。"


class CandidatePoolBuilder:
    def __init__(self, request: SelectionRequest):
        self.request = request

    def build(self, diagnoses: list[Diagnosis]) -> CandidatePool:
        safe_diags = [copy.copy(d) for d in diagnoses]
        for diag in safe_diags:
            annotate_optimization_metadata(diag)
        time_filtered = [
            d
            for d in safe_diags
            if self.request.time_window.contains_datetime(session_row_datetime(d.session))
        ]
        qualified, rejected = self._quality_filter(time_filtered)
        preferred = self._intent_filter(qualified)
        return CandidatePool(
            all_diagnoses=safe_diags,
            time_filtered=time_filtered,
            qualified=qualified,
            preferred=preferred,
            rejected_counts=rejected,
        )

    def _quality_filter(self, diagnoses: list[Diagnosis]) -> tuple[list[Diagnosis], Counter[str]]:
        out: list[Diagnosis] = []
        rejected: Counter[str] = Counter()
        for d in diagnoses:
            assessment = assess_replayability(d.query)
            if assessment.hard_failures:
                rejected["non_replayable_query"] += 1
                d.quality_notes.extend(
                    f"non_replayable:{issue}" for issue in assessment.hard_failures
                )
                continue
            d.quality_notes.extend(
                f"replayability_warning:{warning}" for warning in assessment.warnings
            )
            if assessment.warnings:
                d.quality_score = max(0.0, d.quality_score - min(0.12, 0.04 * len(assessment.warnings)))
            if d.quality_score < 0.35 and d.case_type != "good":
                rejected["low_quality_bad_case"] += 1
                continue
            out.append(d)
        out.sort(
            key=lambda d: (optimization_value_rank(d.optimization_value), d.quality_score, d.confidence),
            reverse=True,
        )
        return out, rejected

    def _intent_filter(self, diagnoses: list[Diagnosis]) -> list[Diagnosis]:
        """Return diagnoses after judge-owned relevance decisions.

        Diagnose used to re-apply brittle keyword/failure-mode rules here.  The
        current contract makes relevance a judge responsibility: both direct API
        and keyless subagent modes receive the raw user request in the shared
        diagnose-native single-session prompt.  Selection should therefore focus
        on quality, quotas, and per-case eligibility only.
        """

        logger.info(
            "selection intent filter skipped",
            reason="judge_owned_relevance",
            diagnosis_count=len(diagnoses),
            legacy_focus_terms=self.request.preference.focus_terms,
            legacy_target_modes=self.request.preference.normalized_modes(),
        )
        return list(diagnoses)


class SelectorPolicy:
    def __init__(self, request: SelectionRequest):
        self.request = request

    def case_type_targets(self, candidates: list[Diagnosis]) -> dict[str, int]:
        limit = self.request.limit
        quota = self.request.quota
        available = Counter(d.case_type for d in candidates)
        requested_total = quota.requested_total()
        if requested_total is not None:
            good = quota.good if quota.good is not None else max(0, limit - max(0, quota.bad or 0))
            bad = quota.bad if quota.bad is not None else max(0, limit - max(0, good or 0))
            if requested_total != limit:
                # User absolute quotas are authoritative about distribution, but
                # case_limit remains the overall cap.  Scale only when the user
                # gave inconsistent counts through natural language parsing.
                bad = min(max(0, bad), limit)
                good = min(max(0, good), max(0, limit - bad))
            return {"bad": min(max(0, bad), available.get("bad", 0)), "good": min(max(0, good), available.get("good", 0))}

        if not self.request.preference.include_good:
            return {"bad": min(limit, available.get("bad", 0)), "good": 0}

        if self.request.preference.dataset_profile == "regression":
            desired_good = min(available.get("good", 0), max(1, int(limit * 0.65)))
        else:
            desired_good = min(
                available.get("good", 0),
                max(1 if limit >= 4 and available.get("good", 0) else 0, int(limit * self.request.preference.good_min_ratio)),
                int(limit * self.request.preference.good_max_ratio),
            )
        desired_bad = min(available.get("bad", 0), max(0, limit - desired_good))
        # If bad inventory cannot fill the rest, allow extra good cases.  If good
        # inventory is missing, fill with bad.
        if desired_bad + desired_good < limit:
            remaining = limit - desired_bad - desired_good
            extra_good = min(remaining, max(0, available.get("good", 0) - desired_good))
            desired_good += extra_good
            remaining -= extra_good
            desired_bad += min(remaining, max(0, available.get("bad", 0) - desired_bad))
        return {"bad": desired_bad, "good": desired_good}

    def case_type_order(self) -> list[str]:
        return ["good", "bad"] if self.request.preference.dataset_profile == "regression" else ["bad", "good"]

    def optimization_rank(self, diag: Diagnosis) -> int:
        return optimization_value_rank(diag.optimization_value)

    def can_exceed_target(
        self,
        diag: Diagnosis,
        selected: list[Diagnosis],
        targets: dict[str, int],
        leftovers: list[Diagnosis],
    ) -> bool:
        current_same = sum(1 for d in selected if d.case_type == diag.case_type)
        target_same = targets.get(diag.case_type, 0)
        if current_same < target_same:
            return True
        for case_type, target in targets.items():
            if case_type == diag.case_type:
                continue
            current = sum(1 for d in selected if d.case_type == case_type)
            if current < target and any(d.case_type == case_type for d in leftovers):
                return False
        if diag.case_type != "good":
            return True
        max_good = self._max_good_allowed()
        current_good = sum(1 for d in selected if d.case_type == "good")
        has_bad_left = any(d.case_type == "bad" for d in leftovers)
        return current_good < max_good or not has_bad_left

    def _max_good_allowed(self) -> int:
        if self.request.preference.dataset_profile == "regression":
            return self.request.limit
        if not self.request.preference.include_good:
            return 0
        return max(1, int(self.request.limit * self.request.preference.good_max_ratio))


class SelectionReporter:
    def __init__(self, request: SelectionRequest, pool: CandidatePool, targets: dict[str, int]):
        self.request = request
        self.pool = pool
        self.targets = targets

    def build(self, selected: list[Diagnosis]) -> dict[str, Any]:
        requested = self.request.limit
        selected_by_case_type = Counter(d.case_type for d in selected)
        requested_quota = self._requested_case_type_counts()
        quota_underfilled = {
            case_type: max(0, required - selected_by_case_type.get(case_type, 0))
            for case_type, required in requested_quota.items()
            if selected_by_case_type.get(case_type, 0) < required
        }
        is_underfilled = len(selected) < requested or bool(quota_underfilled)
        return {
            "requested_case_count": requested,
            "selected_case_count": len(selected),
            "underfilled": is_underfilled,
            "status": "underfilled" if is_underfilled else "satisfied",
            "case_type_targets": self.targets,
            "requested_case_type_counts": requested_quota,
            "quota_underfilled": quota_underfilled,
            "time_window": self.request.time_window.as_dict(),
            "dataset_profile": self.request.preference.dataset_profile,
            "candidate_pool": self.pool.diagnostics(),
            "selected_by_case_type": dict(selected_by_case_type),
            "selected_by_failure_mode": dict(Counter(d.evolution_failure_mode for d in selected)),
            "selected_by_bucket": dict(Counter(d.selection_bucket or "unknown" for d in selected)),
            "backfilled_count": sum(1 for d in selected if d.backfilled),
            "strict_candidate_count": len(self.pool.preferred),
            "qualified_candidate_count": len(self.pool.qualified),
            "coverage_note": self._coverage_note(selected, quota_underfilled),
        }

    def _requested_case_type_counts(self) -> dict[str, int]:
        requested: dict[str, int] = {}
        if self.request.quota.bad is not None:
            requested["bad"] = max(0, min(self.request.limit, self.request.quota.bad))
        if self.request.quota.good is not None:
            requested["good"] = max(0, min(self.request.limit, self.request.quota.good))
        return requested

    def _coverage_note(self, selected: list[Diagnosis], quota_underfilled: dict[str, int]) -> str:
        if quota_underfilled:
            parts = ", ".join(f"{case_type} 缺 {missing}" for case_type, missing in sorted(quota_underfilled.items()))
            return (
                f"总量抽到 {len(selected)}/{self.request.limit}，但未满足显式 good/bad 配额：{parts}；"
                "已在本次本地扫描窗口内完成规则过滤、LLM 诊断与逐 Case 质量过滤，符合配额的候选不足。"
            )
        if len(selected) >= self.request.limit:
            if any(d.backfilled for d in selected):
                return "已抽满；部分 case 因严格偏好候选不足从全量候选池回填。"
            return "已从严格偏好候选池抽满。"
        return (
            f"仅抽到 {len(selected)}/{self.request.limit}；已在本次本地扫描窗口内完成规则过滤、"
            "LLM/规则诊断、时间范围、质量过滤与上下文独立性检查后候选不足。"
        )


class SelectionRequestFactory:
    @staticmethod
    def from_preference(pref: CasePreference) -> SelectionRequest:
        return SelectionRequest(
            preference=pref,
            quota=CaseTypeQuota(bad=pref.bad_case_count, good=pref.good_case_count),
            time_window=TimeWindow(
                since=parse_preference_datetime(pref.since),
                until=parse_preference_datetime(pref.until),
                label=pref.time_range_label,
            ),
        )


def select_diagnoses(diags: list[Diagnosis], pref: CasePreference) -> SelectionResult:
    request = SelectionRequestFactory.from_preference(pref)
    return SelectionEngine(request).select(diags)


def sample_diagnoses(diags: list[Diagnosis], pref: CasePreference) -> list[Diagnosis]:
    return select_diagnoses(diags, pref).selected


def annotate_optimization_metadata(diag: Diagnosis) -> None:
    mode = diag.evolution_failure_mode
    if diag.case_type == "good":
        diag.failure_controllability = "regression_guard"
        diag.optimization_value = "medium"
        return
    if mode in {
        "retrieval_not_called",
        "retrieval_bad_query",
        "retrieval_relevant_but_not_used",
        "tool_parameter_error",
        "tool_execution_failure",
        "unnecessary_user_blocking",
        "execution_interrupted",
        "async_task_pending",
        "context_or_process_truncated",
        "no_reply_idle",
        "incorrect_or_unverified_answer",
    }:
        diag.failure_controllability = "agent_fixable"
        diag.optimization_value = "high"
    elif mode in {"permission_or_network_blocked", "runtime_config_missing", "workspace_or_data_missing", "retrieval_or_knowledge_failure"}:
        diag.failure_controllability = "environment_dependent"
        diag.optimization_value = "medium"
    else:
        diag.failure_controllability = "unknown"
        diag.optimization_value = "low"


def optimization_value_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def parse_session_datetime(raw: str) -> datetime | None:
    """Parse common session timestamp formats into an aware UTC datetime.

    Online OpenClaw transcripts may store timestamps as seconds, milliseconds,
    microseconds, or nanoseconds.  Treat all numeric epochs consistently here so
    every diagnose stage applies the same time-window semantics.
    """

    value = str(raw or "").strip()
    if not value:
        return None
    if re.fullmatch(r"\d{8}(?:[_-]?\d{6})?", value):
        compact = value.replace("_", "").replace("-", "")
        try:
            return datetime(
                int(compact[0:4]),
                int(compact[4:6]),
                int(compact[6:8]),
                int(compact[8:10] or 0),
                int(compact[10:12] or 0),
                int(compact[12:14] or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        try:
            ts = float(value)
            while ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    normalized = value.replace("Z", "+00:00")
    normalized = re.sub(r"\s+UTC$", "+00:00", normalized, flags=re.I)
    normalized = re.sub(r"\s+GMT$", "+00:00", normalized, flags=re.I)
    tz_match = re.search(
        r"\s+(?:UTC|GMT)([+-])(\d{1,2})(?::?(\d{2}))?$",
        normalized,
        flags=re.I,
    )
    if tz_match:
        sign, hour, minute = tz_match.groups()
        normalized = normalized[: tz_match.start()] + f"{sign}{int(hour):02d}:{minute or '00'}"
    for candidate in [normalized, normalized.replace("/", "-")]:
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    compact_match = re.match(
        r"^(\d{4})(\d{2})(\d{2})(?:[_-]?(\d{2})(\d{2})(\d{2})?)?", value
    )
    if compact_match:
        year, month, day, hour, minute, second = compact_match.groups()
        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(value[: len(datetime.now().strftime(fmt))], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def parse_session_path_datetime(path: str) -> datetime | None:
    """Best-effort timestamp extraction from a session file path/name."""

    text = str(path or "")
    for pattern in [
        r"(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})[_-]?(\d{2})?(\d{2})?(\d{2})?",
    ]:
        match = re.search(pattern, text)
        if not match:
            continue
        year, month, day, hour, minute, second = match.groups()
        try:
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
    return None


def session_row_datetime(row: Any) -> datetime | None:
    """Return the effective timestamp used by diagnose for a session row."""

    dt = parse_session_datetime(getattr(row, "created_at", ""))
    if dt is not None:
        return dt
    dt = parse_session_path_datetime(getattr(row, "path", ""))
    if dt is not None:
        return dt
    try:
        return datetime.fromtimestamp(
            os.path.getmtime(getattr(row, "path", "")), tz=timezone.utc
        )
    except (OSError, TypeError, ValueError):
        return None


def parse_preference_datetime(value: str) -> datetime | None:
    return parse_session_datetime(value)


def _local_tz(now: datetime) -> timezone:
    return now.tzinfo or timezone.utc


def _end_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def _coerce_explicit_date(
    year: int,
    month: int,
    day: int,
    hour: int | None,
    minute: int | None,
    second: int | None,
    tz: timezone,
) -> tuple[datetime, bool] | None:
    try:
        has_time = hour is not None
        return (
            datetime(
                year,
                month,
                day,
                hour or 0,
                minute or 0,
                second or 0,
                tzinfo=tz,
            ),
            has_time,
        )
    except ValueError:
        return None


def _parse_optional_time(value: str) -> tuple[int | None, int | None, int | None]:
    if not value:
        return None, None, None
    m = re.search(r"(?:[ T_]|\s*)(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", value)
    if not m:
        return None, None, None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def _explicit_date_mentions(text: str, now: datetime) -> list[dict[str, Any]]:
    tz = _local_tz(now)
    mentions: list[dict[str, Any]] = []

    def add(span: tuple[int, int], dt: datetime, has_time: bool, raw: str) -> None:
        if any(not (span[1] <= old["span"][0] or span[0] >= old["span"][1]) for old in mentions):
            return
        mentions.append({"span": span, "dt": dt, "has_time": has_time, "raw": raw})

    patterns: list[tuple[str, str]] = [
        (
            "chinese_full",
            r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?(?:\s*(?:[T_ ]?\d{1,2}:\d{1,2}(?::\d{1,2})?))?",
        ),
        (
            "numeric_sep",
            r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T_ ]?\d{1,2}:\d{1,2}(?::\d{1,2})?)?",
        ),
        (
            "compact",
            r"(?<!\d)(\d{4})(\d{2})(\d{2})(?:[T_ ]?\d{1,2}:\d{1,2}(?::\d{1,2})?)?(?!\d)",
        ),
    ]
    for _, pattern in patterns:
        for m in re.finditer(pattern, text):
            raw = m.group(0)
            hour, minute, second = _parse_optional_time(raw)
            parsed = _coerce_explicit_date(
                int(m.group(1)), int(m.group(2)), int(m.group(3)), hour, minute, second, tz
            )
            if parsed:
                add(m.span(), parsed[0], parsed[1], raw)

    # Month/day without year: use the current year.  Keep this after full-date
    # patterns so 2026年7月1日 does not also become a duplicate 7月1日 mention.
    for m in re.finditer(
        r"(?<![\d年/-])(\d{1,2})\s*月\s*(\d{1,2})\s*日?(?:\s*(?:[T_ ]?\d{1,2}:\d{1,2}(?::\d{1,2})?))?",
        text,
    ):
        raw = m.group(0)
        hour, minute, second = _parse_optional_time(raw)
        parsed = _coerce_explicit_date(
            now.year, int(m.group(1)), int(m.group(2)), hour, minute, second, tz
        )
        if parsed:
            add(m.span(), parsed[0], parsed[1], raw)

    mentions.sort(key=lambda item: item["span"][0])
    return mentions


def _is_range_separator(value: str) -> bool:
    stripped = re.sub(r"\s+", "", value or "")
    if not stripped:
        return True
    return bool(
        re.fullmatch(
            r"(?:的)?(?:到|至|起至|起到|--|-|~|～|—|–|－|/|\.|,|，|和|以及|and|to|through|until|between)*(?:的)?",
            stripped,
            flags=re.I,
        )
    )


def _infer_explicit_time_window_from_message(
    message: str, now: datetime
) -> tuple[str, str, str] | None:
    mentions = _explicit_date_mentions(message, now)
    if not mentions:
        return None
    for left, right in zip(mentions, mentions[1:]):
        between = message[left["span"][1] : right["span"][0]]
        # Require an actual range marker unless the two dates are immediately
        # adjacent; this avoids treating unrelated dates in a long prompt as one
        # accidental window.
        if _is_range_separator(between) and (between.strip() or right["span"][0] - left["span"][1] <= 3):
            since_dt = left["dt"]
            until_dt = right["dt"] if right["has_time"] else _end_of_day(right["dt"])
            if until_dt < since_dt:
                since_dt, until_dt = until_dt, since_dt
            label = message[left["span"][0] : right["span"][1]].strip()
            return since_dt.isoformat(), until_dt.isoformat(), label
    # Single explicit date means that whole local day.
    only = mentions[0]
    start = only["dt"]
    end = only["dt"] if only["has_time"] else _end_of_day(only["dt"])
    return start.isoformat(), end.isoformat(), str(only["raw"]).strip()


DEFAULT_INFERRED_TIME_WINDOW_DAYS = 3


def infer_time_window_from_message(message: str, now: datetime | None = None) -> tuple[str, str, str]:
    """Infer the source-session time window from the slash-command message.

    Diagnose defaults to a recent local mining window to avoid silently spending
    LLM calls on stale sessions.  Users can widen/narrow the window with natural
    language such as ``昨天`` or ``最近7天``; explicit all-time wording disables
    the default filter.
    """

    text = message or ""
    current = now or datetime.now(timezone.utc)
    if re.search(r"不限时间|全部时间|所有时间|全量|不限制时间|all\s*time|no\s*time\s*limit", text, re.I):
        return "", "", "不限时间"
    explicit = _infer_explicit_time_window_from_message(text, current)
    if explicit:
        return explicit
    m = re.search(r"(?:最近|近|过去)\s*(\d+)\s*(天|日|周|个月|月)", text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit in {"周"}:
            days = amount * 7
        elif unit in {"个月", "月"}:
            days = amount * 30
        else:
            days = amount
        since = current - timedelta(days=days)
        return since.isoformat(), "", m.group(0)
    m = re.search(r"(?:last|past)\s*(\d+)\s*(day|days|week|weeks|month|months)", text, re.I)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("week"):
            days = amount * 7
        elif unit.startswith("month"):
            days = amount * 30
        else:
            days = amount
        since = current - timedelta(days=days)
        return since.isoformat(), "", m.group(0)
    if "今天" in text:
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), "", "今天"
    if "昨天" in text:
        start = (current - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.isoformat(), end.isoformat(), "昨天"
    if "前天" in text:
        start = (current - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.isoformat(), end.isoformat(), "前天"
    since = current - timedelta(days=DEFAULT_INFERRED_TIME_WINDOW_DAYS)
    return since.isoformat(), "", f"默认近{DEFAULT_INFERRED_TIME_WINDOW_DAYS}天"
