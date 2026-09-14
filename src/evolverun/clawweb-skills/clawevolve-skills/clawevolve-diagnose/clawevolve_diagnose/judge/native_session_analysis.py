from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import logger
from ..constants import FAILURE_MODE_BY_ROOT
from ..models import CasePreference, Diagnosis, SessionRow
from ..utils import assess_replayability, clean_query, compact_replay_text, redact_secrets, slugify
from .query_fidelity import ReplayQueryFidelityPolicy
from .keyless_session_prompt import (
    INPUT_SCHEMA_VERSION,
    LEGACY_OUTPUT_SCHEMA_VERSION,
    OUTPUT_SCHEMA_VERSION,
)

ALLOWED_CASE_TYPES = {"bad", "good", "unknown"}
ALLOWED_ROOTS = set(FAILURE_MODE_BY_ROOT)
ALLOWED_FAILURE_MODES = set(FAILURE_MODE_BY_ROOT.values())
ROOT_ALIASES = {
    "SUCCESS": "COMPLETED",
    "DONE": "COMPLETED",
    "TOOL_USAGE_ERROR": "PARAMETER_ERROR",
    "TOOL_CALL_ERROR": "TOOL_FAILURE",
    "TOOL_EXECUTION_ERROR": "TOOL_FAILURE",
    "NETWORK_PERMISSION": "PERMISSION_NETWORK",
    "PERMISSION_OR_NETWORK": "PERMISSION_NETWORK",
    "WORKFLOW": "WORKFLOW_FAILURE",
    "PLANNING_FAILURE": "WORKFLOW_FAILURE",
}
DEFAULT_DIRECT_SESSION_CONTENT_CHARS = 22000


def build_native_session_analysis_input(
    row: SessionRow,
    preference: CasePreference | None,
    *,
    include_session_content: bool = False,
    max_session_content_chars: int = DEFAULT_DIRECT_SESSION_CONTENT_CHARS,
    session_content_secrets: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build the transport-neutral diagnose-native single-session judge input.

    Subagent transport normally receives only the session locator and reads the
    file itself. Direct API transport cannot read local files, so it supplies a
        bounded, redacted raw session_content while preserving the same requirements,
    taxonomy, output schema, and result mapper.
    """

    session_path = Path(row.path).expanduser().resolve(strict=False)
    session: dict[str, Any] = {
        "session_id": row.session_id,
        "path": str(session_path),
        "created_at": row.created_at,
        "bot_id": row.bot_id,
        "original_model": row.original_model,
    }
    if include_session_content:
        content, truncated = build_raw_session_content_for_llm(
            row,
            max_chars=max_session_content_chars,
            secrets=session_content_secrets,
        )
        session["content"] = content
        session["content_format"] = "openclaw-session-jsonl.raw-bounded.v1"
        session["content_truncated"] = truncated
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "session": session,
        "requirements": {
            "user_request": str(preference.raw_message or preference.scoring_requirements or "")
            if preference
            else "",
            "intent_text": str(getattr(preference, "intent_text", "") or preference.raw_message or "")
            if preference
            else "",
            "intent_confidence": float(getattr(preference, "intent_confidence", 0.0) or 0.0)
            if preference
            else 0.0,
            "requires_broad_recall": bool(getattr(preference, "requires_broad_recall", False))
            if preference
            else False,
            "include_good": bool(preference.include_good) if preference else True,
            "drop_context_dependent": bool(preference.drop_context_dependent) if preference else True,
            "require_replayable_query": True,
        },
        "taxonomy": {
            "allowed_case_types": sorted(ALLOWED_CASE_TYPES),
            "allowed_root_cause_classes": sorted(ALLOWED_ROOTS),
            "failure_mode_by_root_cause": FAILURE_MODE_BY_ROOT,
        },
        "output_schema": OUTPUT_SCHEMA_VERSION,
    }


def build_session_content_for_llm(row: SessionRow, *, max_chars: int) -> str:
    """Return bounded session evidence suitable for direct LLM analysis."""

    sections: list[tuple[str, str, int]] = [
        ("first_question", row.first_question, 1800),
        ("user_text", row.user_text, 6000),
        ("assistant_text", row.assistant_text, 6000),
        ("tool_text", row.tool_text, 5000),
        ("raw_text_tail", row.raw_text, 6000),
    ]
    parts: list[str] = []
    for name, value, limit in sections:
        compact = compact_replay_text(value or "", max_len=limit)
        if compact:
            parts.append(f"<{name}>\n{compact}\n</{name}>")
    content = "\n\n".join(parts)
    if len(content) <= max_chars:
        return content
    head = content[: int(max_chars * 0.70)].rstrip()
    tail = content[-int(max_chars * 0.25) :].lstrip()
    return f"{head}\n\n...<session_content_truncated>...\n\n{tail}"[:max_chars]


def build_raw_session_content_for_llm(
    row: SessionRow,
    *,
    max_chars: int = DEFAULT_DIRECT_SESSION_CONTENT_CHARS,
    secrets: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, bool]:
    """Read a raw Session file with bounded memory and redact it for API Judge."""

    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    path = Path(row.path).expanduser()
    if not path.exists() or not path.is_file() or path.is_symlink():
        return "", False
    max_probe_bytes = max_chars * 4
    source_truncated = False
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size <= max_probe_bytes:
                raw = handle.read()
            else:
                source_truncated = True
                head_bytes = int(max_probe_bytes * 0.70)
                tail_bytes = int(max_probe_bytes * 0.25)
                head = handle.read(head_bytes)
                handle.seek(max(0, size - tail_bytes))
                tail = handle.read(tail_bytes)
                raw = head + b"\n...<raw_session_source_truncated>...\n" + tail
    except OSError:
        return "", False
    text = redact_secrets(raw.decode("utf-8", errors="ignore"), secrets)
    if len(text) <= max_chars and not source_truncated:
        return text, False
    head_chars = int(max_chars * 0.70)
    tail_chars = int(max_chars * 0.25)
    marker = "\n...<raw_session_content_truncated>...\n"
    bounded = f"{text[:head_chars].rstrip()}{marker}{text[-tail_chars:].lstrip()}"
    return bounded[:max_chars], True


def map_native_session_analysis_result(row: SessionRow, result: dict[str, Any], *, source_note: str) -> Diagnosis | None:
    """Convert diagnose-native LLM output into the shared Diagnosis model."""

    if not isinstance(result, dict):
        raise ValueError("native session analysis result must be a JSON object")
    result_schema = str(result.get("schema_version") or "").strip()
    if result_schema not in {OUTPUT_SCHEMA_VERSION, LEGACY_OUTPUT_SCHEMA_VERSION}:
        logger.warning(
            "native session diagnosis schema mismatch",
            session_id=row.session_id,
            schema=result.get("schema_version"),
            expected_schema=OUTPUT_SCHEMA_VERSION,
            source_note=source_note,
        )

    intent_match = _normalize_intent_match(result.get("intent_match"))
    if intent_match and not intent_match.get("is_match", True):
        logger.info(
            "native session diagnosis rejected by intent match",
            session_id=row.session_id,
            intent_match=intent_match,
            source_note=source_note,
        )
        return None
    if not _as_bool(result.get("is_evaluable"), default=True):
        logger.info(
            "native session diagnosis rejected as not evaluable",
            session_id=row.session_id,
            reason=str(result.get("reject_reason") or "")[:500],
            category=str(result.get("reject_category") or "")[:120],
            detail=str(result.get("reject_detail") or "")[:500],
            source_note=source_note,
        )
        return None

    root = _normalize_root(result.get("root_cause_class"), result.get("case_type"))
    case_type = _normalize_case_type(result.get("case_type"), root)
    failure = _normalize_failure_mode(result.get("evolution_failure_mode"), root)
    generated_query = clean_query(str(result.get("query") or ""), max_len=900)
    fidelity = ReplayQueryFidelityPolicy().decide(row, generated_query)
    query = fidelity.query
    original_query = fidelity.source_invocation or clean_query(
        str(result.get("original_query") or row.first_question or row.user_text),
        max_len=900,
    )
    notes = _string_list(result.get("quality_notes"))
    notes.insert(0, source_note)
    notes.extend(fidelity.notes)
    assessment = assess_replayability(query)
    if assessment.hard_failures:
        notes.extend(f"non_replayable:{issue}" for issue in assessment.hard_failures)
        logger.warning(
            "native session diagnosis has non replayable query",
            session_id=row.session_id,
            issues=list(assessment.hard_failures),
            query_preview=query[:900],
            source_note=source_note,
        )
        return None
    notes.extend(f"replayability_warning:{warning}" for warning in assessment.warnings)
    notes.append(f"query_type:{assessment.query_type}")
    if assessment.confidence < 0.7:
        notes.append(f"replayability_confidence:{assessment.confidence:.2f}")
    if not query:
        return None

    return Diagnosis(
        session=row,
        case_type=case_type,
        symptom_class=_non_empty(result.get("symptom_class"), root),
        root_cause_class=root,
        common_problem_key=slugify(failure),
        evolution_failure_mode=failure,
        query=query,
        original_query=original_query,
        root_cause_summary=_non_empty(
            result.get("root_cause_summary"),
            f"diagnose-native session analyzer 判定 {root}: {failure}",
        ),
        evidence=_evidence_list(result.get("evidence"), row, default_placeholder=True),
        requires_search=_as_bool(result.get("requires_search"), default=False),
        confidence=_clamp01(result.get("confidence"), 0.5),
        quality_score=_clamp01(result.get("quality_score"), 0.5),
        quality_notes=notes,
        tool_hints=_string_list(result.get("tool_hints")),
        evidence_file_hints=_evidence_list(result.get("evidence_file_hints"), row),
        failure_controllability=str(result.get("failure_controllability") or ""),
        optimization_value=str(result.get("optimization_value") or ""),
        intent_match=intent_match,
    )


def failed_native_session_analysis_diagnosis(
    row: SessionRow,
    exc: Exception,
    *,
    source_label: str,
    source_note: str,
    secrets: list[str] | tuple[str, ...] | None = None,
) -> Diagnosis:
    root = "UNKNOWN"
    failure = FAILURE_MODE_BY_ROOT[root]
    safe = redact_secrets(str(exc), secrets or [])
    fidelity = ReplayQueryFidelityPolicy().decide(row, row.first_question or row.user_text)
    query = fidelity.query or clean_query(row.first_question or row.user_text, max_len=700)
    if not query:
        query = "请基于一个失败的 agent 会话，定位可复现的任务目标并改进处理策略。"
    return Diagnosis(
        session=row,
        case_type="bad",
        symptom_class="SESSION_JUDGE_ERROR",
        root_cause_class=root,
        common_problem_key=slugify(failure),
        evolution_failure_mode=failure,
        query=query,
        original_query=row.first_question or row.user_text,
        root_cause_summary=(
            f"diagnose-native {source_label} 会话分析失败；"
            f"错误: {type(exc).__name__}: {safe}"
        ),
        confidence=0.1,
        quality_score=0.05,
        quality_notes=[f"{source_note}_failed"],
        evidence=[{"source": source_label, "path": row.path, "snippet": safe[:500]}],
    )



def _normalize_intent_match(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "is_match": _as_bool(value.get("is_match"), default=True),
        "confidence": _clamp01(value.get("confidence"), 0.5),
        "matched_aspects": _string_list(value.get("matched_aspects")),
        "unmatched_aspects": _string_list(value.get("unmatched_aspects")),
        "reason": str(value.get("reason") or "").strip()[:500],
    }

def _normalize_case_type(value: Any, root: str) -> str:
    case_type = str(value or "").strip().lower()
    if case_type in {"bad", "good"}:
        return case_type
    if root == "COMPLETED":
        return "good"
    return "bad"


def _normalize_root(value: Any, case_type: Any) -> str:
    root = str(value or "").strip().upper()
    root = ROOT_ALIASES.get(root, root)
    if root:
        return root
    if str(case_type or "").strip().lower() == "good":
        return "COMPLETED"
    return "UNKNOWN"


def _normalize_failure_mode(value: Any, root: str) -> str:
    failure = str(value or "").strip().upper()
    return failure or root


def _evidence_list(
    value: Any,
    row: SessionRow,
    default_placeholder: bool = False,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("snippet") or item.get("text") or "").strip()
            if not snippet:
                continue
            out.append(
                {
                    "source": str(item.get("source") or "session"),
                    "path": str(item.get("path") or row.path),
                    "snippet": snippet[:800],
                }
            )
    if not out and default_placeholder:
        out.append(
            {
                "source": "session",
                "path": row.path,
                "snippet": "native session analyzer did not provide explicit evidence",
            }
        )
    return out


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()][:20]
    if value:
        return [str(value).strip()]
    return []



def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, "0", "false", "False", "no", "NO"):
        return False
    if value in (1, "1", "true", "True", "yes", "YES"):
        return True
    return default


def _clamp01(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _non_empty(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def compact_hint(value: str, max_len: int) -> str:
    text = clean_query(value or "", max_len=max_len)
    return text if len(text) <= max_len else text[:max_len]
