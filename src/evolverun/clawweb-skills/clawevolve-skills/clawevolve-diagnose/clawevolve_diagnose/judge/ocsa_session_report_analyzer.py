from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

from .. import logger
from ..constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS, FAILURE_MODE_BY_ROOT
from ..models import (
    CasePreference,
    Diagnosis,
    JudgeRuntimeConfig,
    LlmRuntimeConfig,
    SessionRow,
    normalize_judge_backend,
)
from ..utils import (
    clean_query,
    compact_replay_text,
    is_context_dependent_query,
    redact_secrets,
    assess_replayability,
    slugify,
)
from .ocsa_session_adapter import load_session_payload
from .agent_session_contract import validate_agent_session_result
from .keyless_session_prompt import build_session_analysis_prompt
from .native_session_analysis import (
    build_native_session_analysis_input,
    failed_native_session_analysis_diagnosis,
    map_native_session_analysis_result,
)
from .ocsa_labels import (
    ocsa_analysis_failed,
    ocsa_case_type,
    ocsa_error_labels,
    ocsa_primary_label,
    ocsa_task_metadata,
)
from .openai_chat_client import chat_json, validate_chat_runtime
from .query_fidelity import ReplayQueryFidelityPolicy
from .query_rewriter import rewrite_eval_query_with_llm
from .ocsa_request_matcher import (
    OcsaRequestMatchConfig,
    OcsaRequestMatcher,
    has_explicit_user_diagnose_request,
)
from .._ocsa_session_report.evaluators.session_report.evaluator import SessionReportEvaluator
from .._ocsa_session_report.infrastructure.llm import configure_runtime


def _payload_has_real_user_message(payload: dict[str, Any]) -> bool:
    """Return true only when OCSA received an actual user task message."""

    messages = payload.get("messages")
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except (TypeError, ValueError):
            return False
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() not in {"user", "human"}:
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return True
        if isinstance(content, (list, dict)) and content:
            return True
    return False


@dataclass(frozen=True)
class OcsaSessionReportJudgeConfig:
    """Runtime knobs for the diagnose-local copy of OpenclawSessionAnalysis session_report."""

    runtime: JudgeRuntimeConfig
    timeout_seconds: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
    max_concurrent_tasks: int = 4
    session_report_version: str = "V1.0"
    preference: CasePreference | None = None


class OcsaSessionReportAnalyzer:
    """Analyze local sessions with the copied OpenclawSessionAnalysis session_report evaluator.

    The session-file loading/adaptation remains diagnose-owned because diagnose reads local
    OpenClaw JSONL files, but once a session payload is built, LLM task split, prompts,
    normalization, tool assessment, and task_failure_class generation are delegated to the
    copied OpenclawSessionAnalysis session_report code path without custom fallback classifiers.
    """

    def __init__(self, config: OcsaSessionReportJudgeConfig):
        self.config = config
        backend = normalize_judge_backend(config.runtime.backend)
        if backend != "api":
            raise ValueError(
                "OcsaSessionReportAnalyzer requires the API judge backend; "
                "keyless runs must use KeylessSubagentSessionAnalyzer."
            )
        validate_chat_runtime(
            config.runtime.api.api_key,
            config.runtime.api.base_url,
            config.runtime.api.model,
        )
        configure_runtime(config.runtime)
        self.evaluator = SessionReportEvaluator(
            timeout=config.timeout_seconds,
            max_concurrent_tasks=config.max_concurrent_tasks,
            session_report_version=config.session_report_version,
        )

    def analyze(self, rows: Iterable[SessionRow], bot_id: str = "") -> list[Diagnosis]:
        diagnoses: list[Diagnosis] = []
        rows_list = list(rows)
        logger.info(
            "ocsa session report chunk analysis start",
            session_count=len(rows_list),
            bot_id=bot_id,
            base_url=self.config.runtime.api.base_url,
            model=self.config.runtime.api.model,
            timeout_seconds=self.config.timeout_seconds,
            max_concurrent_tasks=self.config.max_concurrent_tasks,
            has_explicit_user_request=has_explicit_user_diagnose_request(self.config.preference),
        )
        for index, row in enumerate(rows_list, start=1):
            session_started_at = time.time()
            session_payload = row.raw_session or load_session_payload(row)
            if not session_payload:
                logger.warning(
                    "ocsa session report payload empty; using raw fallback",
                    index=f"{index}/{len(rows_list)}",
                    session_id=row.session_id,
                    path=row.path,
                    elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                )
            if not session_payload or not _payload_has_real_user_message(session_payload):
                diagnosis = self._analyze_raw_session(row)
                if diagnosis is not None:
                    diagnoses.append(diagnosis)
                continue
            messages = session_payload.get("messages")
            logger.info(
                "ocsa session report evaluate start",
                index=f"{index}/{len(rows_list)}",
                session_id=row.session_id,
                created_at=row.created_at,
                path=row.path,
                payload_keys=sorted(session_payload.keys()),
                message_count=len(messages) if isinstance(messages, list) else "unknown",
                all_exe_skill=session_payload.get("allExeSkill") or session_payload.get("all_exe_skill") or "",
                all_exe_mcp=session_payload.get("allExeMcp") or session_payload.get("all_exe_mcp") or "",
                first_question_preview=clean_query(row.first_question or row.user_text)[:300],
            )
            try:
                result = self.evaluator.evaluate(session_payload)
            except Exception as exc:  # noqa: BLE001 - expose per-session judge failures without fallback classifier.
                safe = redact_secrets(str(exc), _runtime_secrets(self.config.runtime))
                logger.warning(
                    "ocsa session report evaluate failed",
                    index=f"{index}/{len(rows_list)}",
                    session_id=row.session_id,
                    error_class=type(exc).__name__,
                    error=safe[:1000],
                    elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                )
                diagnoses.append(self._failed_judge_diagnosis(row, exc))
                continue
            result_dict = result.to_dict()
            judge_report = result_dict.get("judge_report") if isinstance(result_dict, dict) else {}
            report_body = judge_report.get("judge_report") if isinstance(judge_report, dict) else {}
            logger.info(
                "ocsa session report evaluate done",
                index=f"{index}/{len(rows_list)}",
                session_id=row.session_id,
                result_keys=sorted(result_dict.keys()) if isinstance(result_dict, dict) else [],
                task_count=report_body.get("task_count") if isinstance(report_body, dict) else "",
                is_multitask=report_body.get("is_multitask") if isinstance(report_body, dict) else "",
                llm_models=report_body.get("llm_models") if isinstance(report_body, dict) else {},
                all_tasks_assessment_failed=_result_all_tasks_assessment_failed(result_dict),
                elapsed_seconds=f"{time.time() - session_started_at:.2f}",
            )
            if _result_all_tasks_assessment_failed(result_dict):
                logger.warning(
                    "ocsa session report all task assessments failed",
                    index=f"{index}/{len(rows_list)}",
                    session_id=row.session_id,
                    errors=_assessment_failed_errors(result_dict),
                    elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                )
                diagnoses.append(self._failed_ocsa_result_diagnosis(row, result_dict))
                continue
            if has_explicit_user_diagnose_request(self.config.preference):
                try:
                    logger.info(
                        "ocsa request matcher start",
                        session_id=row.session_id,
                        elapsed_before_match_seconds=f"{time.time() - session_started_at:.2f}",
                    )
                    matcher = OcsaRequestMatcher(
                        OcsaRequestMatchConfig(
                            runtime=self.config.runtime,
                            preference=self.config.preference,
                            timeout_seconds=self.config.timeout_seconds,
                            max_concurrent_tasks=self.config.max_concurrent_tasks,
                        )
                    )
                    matched = matcher.match(row, session_payload, result_dict)
                    diagnoses.extend(matched)
                    logger.info(
                        "ocsa request matcher done",
                        session_id=row.session_id,
                        matched_diagnoses=len(matched),
                        elapsed_seconds=f"{time.time() - session_started_at:.2f}",
                    )
                    continue
                except Exception as exc:  # noqa: BLE001 - isolate per-session matcher failures.
                    safe = redact_secrets(str(exc), _runtime_secrets(self.config.runtime))
                    logger.warning(
                        "ocsa request matcher failed",
                        session_id=row.session_id,
                        error=f"{type(exc).__name__}: {safe}"[:800],
                    )
                    diagnoses.append(self._failed_request_match_diagnosis(row, exc))
                    continue
            mapped = map_session_report_result(row, result_dict, runtime=self.config.runtime)
            diagnoses.extend(mapped)
            logger.info(
                "ocsa session report mapped diagnoses",
                session_id=row.session_id,
                mapped_diagnoses=len(mapped),
                elapsed_seconds=f"{time.time() - session_started_at:.2f}",
            )
        logger.info(
            "ocsa session report chunk analysis done",
            session_count=len(rows_list),
            diagnosis_count=len(diagnoses),
        )
        return sorted(diagnoses, key=lambda d: (d.quality_score, d.confidence), reverse=True)

    def _analyze_raw_session(self, row: SessionRow) -> Diagnosis | None:
        """Use the existing diagnose-native contract once for an unadaptable payload."""

        logger.info(
            "api raw session fallback start",
            session_id=row.session_id,
            path=row.path,
        )
        try:
            native_input = build_native_session_analysis_input(
                row,
                self.config.preference,
                include_session_content=True,
                session_content_secrets=[self.config.runtime.api.api_key],
            )
            session = native_input.get("session") or {}
            content = str(session.get("content") or "")
            if not content:
                logger.warning(
                    "api raw session fallback skipped unreadable content",
                    session_id=row.session_id,
                    path=row.path,
                )
                return None
            prompt = build_session_analysis_prompt(native_input)
            result = chat_json(
                api_key=self.config.runtime.api.api_key,
                base_url=self.config.runtime.api.base_url,
                model=self.config.runtime.api.model,
                system="Follow the diagnose-native contract and return exactly one JSON object.",
                user=prompt,
                timeout=self.config.timeout_seconds,
                call_name=f"diagnose_api_raw_session:{row.session_id}",
            )
            validation = validate_agent_session_result(row, result)
            if not validation["valid"]:
                raise ValueError("; ".join(validation["errors"]))
            diagnosis = map_native_session_analysis_result(
                row,
                result,
                source_note="source:api_raw_session_fallback",
            )
            logger.info(
                "api raw session fallback done",
                session_id=row.session_id,
                accepted=diagnosis is not None,
                content_chars=len(content),
                content_truncated=bool(session.get("content_truncated")),
            )
            return diagnosis
        except Exception as exc:  # noqa: BLE001 - isolate one Session Judge failure.
            safe = redact_secrets(str(exc), [self.config.runtime.api.api_key])
            logger.warning(
                "api raw session fallback failed",
                session_id=row.session_id,
                error_class=type(exc).__name__,
                error=safe[:1000],
            )
            return failed_native_session_analysis_diagnosis(
                row,
                exc,
                source_label="api_raw_session_fallback",
                source_note="source:api_raw_session_fallback",
                secrets=[self.config.runtime.api.api_key],
            )

    def _failed_judge_diagnosis(self, row: SessionRow, exc: Exception) -> Diagnosis:
        return self._judge_error_diagnosis(
            row,
            symptom_class="OCSA_SESSION_REPORT_JUDGE_ERROR",
            error_class=type(exc).__name__,
            error_text=str(exc),
            source="openclaw_session_report_copy",
            summary_prefix="OCSA session_report 分析失败",
            quality_note="ocsa_session_report_judge_failed",
        )

    def _failed_request_match_diagnosis(
        self, row: SessionRow, exc: Exception
    ) -> Diagnosis:
        return self._judge_error_diagnosis(
            row,
            symptom_class="SESSION_JUDGE_ERROR",
            error_class=type(exc).__name__,
            error_text=str(exc),
            source="diagnose.ocsa_request_matcher",
            summary_prefix="Diagnose intent/eligibility 匹配失败",
            quality_note="ocsa_request_matcher_failed",
        )

    def _failed_ocsa_result_diagnosis(self, row: SessionRow, result_dict: dict[str, Any]) -> Diagnosis:
        errors = _assessment_failed_errors(result_dict)
        error_text = "; ".join(errors) if errors else "Assessment failed"
        return self._judge_error_diagnosis(
            row,
            symptom_class="OCSA_SESSION_REPORT_JUDGE_ERROR",
            error_class=_assessment_failed_error_class(result_dict) or "OCSAAssessmentFailed",
            error_text=error_text,
            source="openclaw_session_report_copy.task",
            summary_prefix="OCSA session_report 任务评估失败",
            quality_note="ocsa_session_report_judge_failed",
        )

    def _judge_error_diagnosis(
        self,
        row: SessionRow,
        *,
        symptom_class: str,
        error_class: str,
        error_text: str,
        source: str,
        summary_prefix: str,
        quality_note: str,
    ) -> Diagnosis:
        q, original_query, replay_notes = build_replayable_case_query({}, row)
        root = "UNKNOWN"
        failure = FAILURE_MODE_BY_ROOT[root]
        safe = redact_secrets(str(error_text), _runtime_secrets(self.config.runtime))
        return Diagnosis(
            session=row,
            case_type="bad",
            symptom_class=symptom_class,
            root_cause_class=root,
            common_problem_key=slugify(failure),
            evolution_failure_mode=failure,
            query=q,
            original_query=original_query,
            root_cause_summary=f"{summary_prefix}；错误: {error_class}: {safe}",
            confidence=0.1,
            quality_score=0.05,
            quality_notes=[quality_note, *replay_notes],
            evidence=[
                {
                    "source": source,
                    "path": row.path,
                    "snippet": safe[:500],
                }
            ],
        )


def analyze_sessions_with_ocsa_session_report(
    rows: list[SessionRow],
    bot_id: str,
    runtime: JudgeRuntimeConfig,
    preference: CasePreference | None = None,
) -> list[Diagnosis]:
    """Analyze sessions through OCSA plus optional diagnose request matching."""

    analyzer = OcsaSessionReportAnalyzer(
        OcsaSessionReportJudgeConfig(runtime=runtime, preference=preference)
    )
    return analyzer.analyze(rows, bot_id)


def analyze_sessions_with_llm_as_judge(
    rows: list[SessionRow],
    bot_id: str,
    runtime: JudgeRuntimeConfig,
    preference: CasePreference | None = None,
) -> list[Diagnosis]:
    """Compatibility alias for the older direct-judge helper name."""

    return analyze_sessions_with_ocsa_session_report(rows, bot_id, runtime, preference)


def _runtime_secrets(runtime: JudgeRuntimeConfig | LlmRuntimeConfig) -> list[str]:
    if isinstance(runtime, JudgeRuntimeConfig):
        return [runtime.api.api_key] if runtime.api.api_key else []
    return [runtime.api_key] if runtime.api_key else []


def map_session_report_result(
    row: SessionRow,
    result_dict: dict[str, Any],
    runtime: JudgeRuntimeConfig | LlmRuntimeConfig | None = None,
) -> list[Diagnosis]:
    judge_report = result_dict.get("judge_report") or {}
    inner = (
        judge_report.get("judge_report")
        if isinstance(judge_report.get("judge_report"), dict)
        else judge_report
    )
    tasks = inner.get("tasks", []) if isinstance(inner, dict) else []
    if not isinstance(tasks, list):
        tasks = []
    if not tasks:
        tasks = [
            {
                "task_index": 0,
                "task_description": row.first_question,
                "is_complete": "unknown",
                "reasoning": "OCSA session_report returned no task result",
                "task_failure_class": "UNKNOWN",
                "skills": [],
                "mcps": [],
            }
        ]
    diagnoses: list[Diagnosis] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if ocsa_analysis_failed(task):
            logger.warning(
                "ocsa task excluded because assessment failed",
                session_id=row.session_id,
                task_index=task.get("task_index"),
                error=str(task.get("judge_error") or task.get("reasoning") or "")[:800],
            )
            continue
        root = ocsa_primary_label(task)
        failure = root
        query, original_query, replay_notes = build_replayable_case_query(
            task, row, runtime=runtime
        )
        assessment = assess_replayability(query)
        if assessment.hard_failures:
            logger.warning(
                "session report task dropped due to non replayable query",
                session_id=row.session_id,
                task_index=task.get("task_index"),
                failure_class=task.get("task_failure_class"),
                query_preview=(query or "")[:1200],
                issues=list(assessment.hard_failures),
                replay_notes=replay_notes,
            )
            # Only unsafe or unresolved-context queries are hard failures.
            continue
        case_type = ocsa_case_type(task)
        quality, notes = _quality_from_judge(task, query, case_type)
        notes.extend(replay_notes)
        notes.extend(f"replayability_warning:{warning}" for warning in assessment.warnings)
        notes.append(f"query_type:{assessment.query_type}")
        symptom = (
            "OCSA_SESSION_REPORT_JUDGE_ERROR"
            if _is_assessment_failed_task(task, root)
            else root
        )
        diagnoses.append(
            Diagnosis(
                session=row,
                case_type=case_type,
                symptom_class=symptom,
                root_cause_class=root,
                common_problem_key=slugify(failure),
                evolution_failure_mode=failure,
                query=query,
                original_query=original_query,
                root_cause_summary=_summary_from_task(task, root),
                evidence=_evidence_from_task(row, task),
                requires_search=_requires_search(row, task),
                confidence=_confidence_from_task(task),
                quality_score=quality,
                quality_notes=notes,
                tool_hints=_tool_hints_from_task(task),
                ocsa={
                    **ocsa_task_metadata(task),
                    "session_report": result_dict,
                },
                eligibility={"eligible": True, "source": "ocsa_default_mapping"},
                query_fidelity={
                    "modified": bool(query and original_query and query != original_query),
                    "source": next(
                        (note.split(":", 1)[1] for note in replay_notes if note.startswith("replay_query_source:")),
                        "ocsa_task_or_original_user_message",
                    ),
                },
            )
        )
    return diagnoses


def build_replayable_case_query(
    task: dict[str, Any],
    row: SessionRow,
    runtime: JudgeRuntimeConfig | LlmRuntimeConfig | None = None,
) -> tuple[str, str, list[str]]:
    """Build the downstream eval prompt for a judged task.

    OCSA's ``task_description`` is still treated as judge output and preserved as
    ``original_query`` when available, but it is often a lossy task title.  The
    eval case ``query`` must be runnable by a fresh agent, so we recover concrete
    user goals and constraints from the session's user turns whenever the judge
    title is short, contextual, or less informative than the original user text.
    """

    notes: list[str] = []
    desc = clean_query(str(task.get("task_description") or ""))
    if desc.strip().lower() in {"unknown", "unk", "n/a", "na", "不确定", "未知", "无"}:
        desc = ""
    first = compact_replay_text(row.first_question, max_len=500)
    user = compact_replay_text(row.user_text or row.first_question, max_len=1100)
    original_query = desc or first or clean_query(user)

    fidelity = ReplayQueryFidelityPolicy().decide(row)
    if fidelity.source_invocation:
        notes.extend(fidelity.notes)
        original_query = fidelity.source_invocation
        logger.info(
            "eval query selected",
            session_id=row.session_id,
            source="source_slash_command",
            query_preview=fidelity.query[:1200],
            original_query_preview=(original_query or "")[:800],
            notes=notes,
        )
        return fidelity.query, original_query, notes

    rewrite_runtime = _api_rewrite_runtime(runtime)
    if rewrite_runtime:
        llm_notes: list[str] = []
        rewrite = rewrite_eval_query_with_llm(task, row, rewrite_runtime)
        llm_notes.extend(
            f"llm_eval_query_rewriter_warning:{w}" for w in rewrite.warnings[:6]
        )
        if rewrite.query:
            rewrite_assessment = assess_replayability(rewrite.query)
            if not rewrite_assessment.hard_failures:
                llm_notes.insert(0, "replay_query_source:llm_eval_query_rewriter")
                llm_notes.append("llm_eval_query_rewriter")
                llm_notes.append("context_independent_query")
                if rewrite.task_focus:
                    llm_notes.append("llm_task_focus:" + rewrite.task_focus[:180])
                if rewrite.source_turn_indices:
                    llm_notes.append(
                        "llm_source_turn_indices:"
                        + ",".join(str(i) for i in rewrite.source_turn_indices[:8])
                    )
                if rewrite.included_context:
                    llm_notes.append(
                        "llm_included_context:" + ",".join(rewrite.included_context[:4])
                    )
                if rewrite.dropped_context:
                    llm_notes.append(
                        "llm_dropped_context:" + ",".join(rewrite.dropped_context[:4])
                    )
                if original_query and rewrite.query != original_query:
                    llm_notes.append("original_query_preserved")
                logger.info(
                    "eval query selected",
                    session_id=row.session_id,
                    source="llm_eval_query_rewriter",
                    query_preview=rewrite.query[:1200],
                    original_query_preview=(original_query or "")[:800],
                    notes=llm_notes,
                )
                return rewrite.query, original_query, llm_notes
            llm_notes.extend(
                "llm_eval_query_rewriter_replayability:" + issue
                for issue in rewrite_assessment.hard_failures
            )
            logger.warning(
                "eval query rewrite output not replayable; fallback start",
                session_id=row.session_id,
                query_preview=rewrite.query[:1200],
                issues=list(rewrite_assessment.hard_failures),
            )
        else:
            logger.info(
                "eval query rewrite empty; fallback start",
                session_id=row.session_id,
                warnings=rewrite.warnings,
                missing_context=rewrite.missing_context,
            )
        notes.extend(llm_notes)
    else:
        logger.info(
            "eval query llm rewrite unavailable; deterministic fallback start",
            session_id=row.session_id,
            runtime_configured=bool(rewrite_runtime),
            has_base_url=bool(runtime and runtime.base_url),
            has_model=bool(runtime and runtime.model),
        )

    candidates: list[tuple[str, str]] = []
    user_has_replay_substance = _user_text_has_replay_substance(user)

    # Keep exported eval prompts minimal and context-independent: the agent under
    # evaluation should see one normal user task, but that task must include
    # parameters/constraints that may have appeared in earlier user turns.
    if user and user_has_replay_substance:
        contextual_user_task = _context_independent_user_query(first, user)
        if contextual_user_task:
            candidates.append(("session_user_context_independent", contextual_user_task))
    if desc and _should_prefer_judge_task_description(desc, first, user):
        candidates.append(("judge_task_description", desc))
    if first:
        candidates.append(("first_question", first))
    if user and user_has_replay_substance:
        single_user_task = _extract_single_task_query(user)
        if single_user_task:
            candidates.append(("session_user_single_task", single_user_task))
    if desc:
        candidates.append(("judge_task_description", desc))
    if user:
        candidates.append(("session_user_text_raw", compact_replay_text(user, max_len=700)))

    logger.info(
        "eval query deterministic fallback candidates prepared",
        session_id=row.session_id,
        candidate_count=len(candidates),
        candidate_sources=[source for source, _candidate in candidates],
        original_query_preview=(original_query or "")[:800],
    )

    seen: set[str] = set()
    for source, candidate in candidates:
        value = re.sub(r"\s+", " ", candidate or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        assessment = assess_replayability(value)
        logger.info(
            "eval query deterministic candidate checked",
            session_id=row.session_id,
            source=source,
            query_preview=value[:900],
            issues=list(assessment.hard_failures),
        )
        if not assessment.hard_failures:
            if source != "judge_task_description":
                notes.append(f"replay_query_source:{source}")
            if source in {"session_user_context_independent", "session_user_single_task", "first_question"}:
                notes.append("minimal_eval_query")
            if source == "session_user_context_independent":
                notes.append("context_independent_query")
            if original_query and value != original_query:
                notes.append("original_query_preserved")
            logger.info(
                "eval query selected",
                session_id=row.session_id,
                source=source,
                query_preview=value[:1200],
                original_query_preview=(original_query or "")[:800],
                notes=notes,
            )
            return value, original_query, notes

    fallback = desc or first or user or ""
    fallback_assessment = assess_replayability(fallback) if fallback else None
    fallback_issues = (
        list(fallback_assessment.hard_failures)
        if fallback_assessment
        else ["empty_query"]
    )
    if fallback:
        notes.extend(f"non_replayable:{issue}" for issue in fallback_issues)
    logger.warning(
        "eval query selected fallback non_replayable",
        session_id=row.session_id,
        query_preview=(fallback or "")[:1200],
        original_query_preview=(original_query or "")[:800],
        issues=fallback_issues,
        notes=notes,
    )
    return fallback, original_query, notes



def _api_rewrite_runtime(
    runtime: JudgeRuntimeConfig | LlmRuntimeConfig | None,
) -> LlmRuntimeConfig | None:
    """Return the direct API runtime usable by the eval-query rewriter."""

    if isinstance(runtime, JudgeRuntimeConfig):
        candidate = runtime.api
    else:
        candidate = runtime
    if candidate and candidate.api_key and candidate.base_url and candidate.model:
        return candidate
    return None

def _user_text_has_replay_substance(user: str) -> bool:
    """Return whether user text contains usable task material.

    Replayability warnings are intentionally not treated as rejection reasons.
    This predicate only excludes text that cannot safely become an eval query.
    """

    value = re.sub(r"\s+", " ", user or "").strip()
    if not value:
        return False
    return not assess_replayability(value).hard_failures


def _should_prefer_judge_task_description(desc: str, first: str, user: str) -> bool:
    if not desc or assess_replayability(desc).hard_failures:
        return False
    desc_len = len(re.sub(r"\s+", "", desc))
    first_len = len(re.sub(r"\s+", "", first or ""))
    user_len = len(re.sub(r"\s+", "", user or ""))
    if is_context_dependent_query(desc):
        return False
    # Prefer a judge title only when it is already a concise, self-contained
    # task and not obviously less informative than the original first ask.
    if first and not assess_replayability(first).hard_failures and first_len >= desc_len + 12:
        return False
    return desc_len >= 12 and (not user or desc_len >= min(user_len, 80) * 0.55)


def _context_independent_user_query(first: str, user: str) -> str:
    """Build one normal eval task from all user turns, preserving prior context.

    Case extraction is intentionally decoupled from diagnose internals.  The
    exported query must not require reading the original session, so parameters
    mentioned before an action turn are folded into the final prompt.  We still
    avoid replay wrappers such as "原始 session" and return only the user task.
    """

    lines = _substantive_user_lines(user)
    if not lines:
        return ""
    if len(lines) == 1:
        return clean_query(lines[0], max_len=700) or lines[0]

    first_clean = clean_query(first, max_len=700) if first else ""
    if (
        first_clean
        and not assess_replayability(first_clean).hard_failures
        and not _has_material_context_outside_first(first_clean, lines)
    ):
        return first_clean

    joined = _join_user_task_lines(lines, max_len=700)
    if joined and not assess_replayability(joined).hard_failures:
        return joined

    single = clean_query(joined or user, max_len=700)
    if single and not assess_replayability(single).hard_failures:
        return single
    return joined or single


def _substantive_user_lines(user: str) -> list[str]:
    compact = compact_replay_text(user, max_len=900)
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\n\r]+", compact):
        line = re.sub(r"\s+", " ", raw).strip(" ，,。.!！?？;；\t")
        if (
            not line
            or (
                is_context_dependent_query(line)
                and len(re.sub(r"\s+", "", line)) < 12
            )
        ):
            continue
        if re.fullmatch(r"(继续|同上|如上|好的|可以|ok|OK|收到|明白)", line):
            continue
        key = re.sub(r"\s+", "", line).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _has_material_context_outside_first(first: str, lines: list[str]) -> bool:
    first_key = re.sub(r"\s+", "", first or "").lower()
    material_re = re.compile(
        r"参数|配置|约束|要求|口径|时间|日期|范围|最近|近\d+|路径|目录|文件|仓库|分支|版本|模型|bot|session|task|step|id|ID|--|=|：|:",
        re.I,
    )
    for line in lines:
        key = re.sub(r"\s+", "", line).lower()
        if not key or key == first_key or key in first_key:
            continue
        if len(key) >= 18 or material_re.search(line):
            return True
    return False


def _join_user_task_lines(lines: list[str], max_len: int = 700) -> str:
    if not lines:
        return ""
    joined = "；".join(lines)
    if len(joined) <= max_len:
        return joined
    kept: list[str] = []
    total = 0
    for line in lines:
        next_len = len(line) + (1 if kept else 0)
        if kept and total + next_len > max_len:
            break
        kept.append(line)
        total += next_len
    return "；".join(kept)[:max_len]


def _extract_single_task_query(user: str) -> str:
    """Return one concise user task without diagnose/session wrapper text."""

    compact = compact_replay_text(user, max_len=700)
    if not compact:
        return ""
    # clean_query deliberately selects the clearest action sentence from noisy
    # user turns.  Use it to avoid exporting multi-turn replay instructions.
    single = clean_query(compact, max_len=700)
    if single and not assess_replayability(single).hard_failures:
        return single
    return compact


def _query_from_task_or_row(task: dict[str, Any], row: SessionRow) -> str:
    query, _original_query, _notes = build_replayable_case_query(task, row)
    return query


def _result_all_tasks_assessment_failed(result_dict: dict[str, Any]) -> bool:
    tasks = _result_tasks(result_dict)
    if not tasks:
        return False
    return all(
        isinstance(task, dict) and _is_assessment_failed_task(task, _root_from_task(task))
        for task in tasks
    )


def _assessment_failed_errors(result_dict: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for task in _result_tasks(result_dict):
        if not isinstance(task, dict):
            continue
        if not _is_assessment_failed_task(task, _root_from_task(task)):
            continue
        for value in (
            task.get("judge_error"),
            task.get("reasoning"),
            task.get("task_failure_class"),
        ):
            text = str(value or "").strip()
            if text and text not in errors:
                errors.append(text[:1000])
    return errors[:8]


def _assessment_failed_error_class(result_dict: dict[str, Any]) -> str:
    for task in _result_tasks(result_dict):
        if isinstance(task, dict) and task.get("judge_error_class"):
            return str(task.get("judge_error_class") or "")[:120]
    return ""


def _result_tasks(result_dict: dict[str, Any]) -> list[Any]:
    judge_report = result_dict.get("judge_report") or {}
    inner = (
        judge_report.get("judge_report")
        if isinstance(judge_report, dict) and isinstance(judge_report.get("judge_report"), dict)
        else judge_report
    )
    tasks = inner.get("tasks", []) if isinstance(inner, dict) else []
    return tasks if isinstance(tasks, list) else []


def _root_from_task(task: dict[str, Any]) -> str:
    """Compatibility wrapper returning OCSA's original primary label."""

    return ocsa_primary_label(task)


def _summary_from_task(task: dict[str, Any], root: str) -> str:
    parts = []
    reasoning = str(task.get("reasoning") or "").strip()
    if reasoning:
        parts.append(reasoning)
    for tool_type in ("skills", "mcps"):
        bad = []
        for item in task.get(tool_type, []) or []:
            if not isinstance(item, dict):
                continue
            exe = item.get("execution") or {}
            if item.get("is_correct") == 0 or exe.get("status") == "failure":
                cat = exe.get("failure_category") or "UNKNOWN"
                bad.append(f"{item.get('name')}:{cat}")
        if bad:
            parts.append(f"{tool_type}异常: {', '.join(bad[:5])}")
    return f"OpenclawSessionAnalysis session_report 判定 {root}: " + ("；".join(parts) if parts else "无详细原因。")


def _evidence_from_task(row: SessionRow, task: dict[str, Any]) -> list[dict[str, str]]:
    evidence = [{"source": "session", "path": row.path, "snippet": str(task.get("reasoning") or "")[:500]}]
    evidence.append({"source": "openclaw_session_report_copy.task_failure_class", "path": row.path, "snippet": str(task.get("task_failure_class") or "UNKNOWN")})
    for tool_type in ("skills", "mcps"):
        for item in task.get(tool_type, []) or []:
            if isinstance(item, dict):
                exe = item.get("execution") or {}
                if isinstance(exe, dict) and exe.get("failure_category"):
                    evidence.append({"source": f"openclaw_session_report_copy.{tool_type}", "path": str(item.get("name") or ""), "snippet": f"is_correct={item.get('is_correct')} status={exe.get('status')} failure_category={exe.get('failure_category')}"})
    return evidence


def _requires_search(row: SessionRow, task: dict[str, Any]) -> bool:
    text = "\n".join([row.user_text, row.assistant_text, row.tool_text, json.dumps(task, ensure_ascii=False)])
    return bool(re.search(r"搜索|查询|最新|最近|新闻|价格|政策|资料|官网|文档|web|联网|检索|知识库|Hermes|searxng", text, re.I))


def _confidence_from_task(task: dict[str, Any]) -> float:
    if task.get("is_complete") in (0, 1, "0", "1", True, False, "aborted"):
        return 0.82
    return 0.45


def _quality_from_judge(task: dict[str, Any], query: str, case_type: str) -> tuple[float, list[str]]:
    notes: list[str] = ["source_openclaw_session_report_copy"]
    if _is_assessment_failed_task(task, _root_from_task(task)):
        # OpenclawSessionAnalysis preserves the task result as UNKNOWN/Assessment failed
        # when its internal LLM call fails. Diagnose must not select those as real eval cases.
        return 0.05, notes + ["ocsa_session_report_assessment_failed"]
    score = 0.78 if case_type == "bad" else 0.72
    assessment = assess_replayability(query)
    if assessment.warnings:
        score -= min(0.12, 0.04 * len(assessment.warnings))
        notes.append("replayability_warnings_present")
    if is_context_dependent_query(query):
        score -= 0.15
        notes.append("context_dependent_query")
    if str(task.get("task_failure_class") or "").upper() == "UNKNOWN":
        score -= 0.2
        notes.append("unknown_failure_class")
    if task.get("skills") or task.get("mcps"):
        score += 0.08
    return max(0.0, min(1.0, score)), notes


def _is_assessment_failed_task(task: dict[str, Any], root: str) -> bool:
    """Compatibility wrapper around the OCSA failure-state detector."""

    del root
    return ocsa_analysis_failed(task)


def _tool_hints_from_task(task: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for tool_type in ("skills", "mcps"):
        for item in task.get(tool_type, []) or []:
            if isinstance(item, dict) and item.get("name") and item.get("name") not in hints:
                hints.append(str(item["name"]))
    return hints[:8]
