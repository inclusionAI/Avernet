from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .. import logger
from ..constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
from .openai_chat_client import chat_json
from ..models import LlmRuntimeConfig, SessionRow
from ..utils import compact_replay_text, redact_secrets, replayability_issues, strip_runtime_metadata


@dataclass
class EvalQueryRewriteResult:
    """Result of converting a judged session/task into one standalone eval query."""

    query: str = ""
    source: str = "llm_eval_query_rewriter"
    confidence: float = 0.0
    original_user_intent: str = ""
    task_focus: str = ""
    included_context: list[str] = field(default_factory=list)
    dropped_context: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    source_turn_indices: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


SYSTEM_PROMPT = """你是评测集 case query 设计器，不是简单摘要器。你的任务是基于真实多轮 session、完整对话线索、工具执行线索和 session judge 的诊断结果，抽取/改写出一个新的评测 agent 可以直接执行的单轮用户任务 query。

目标：这个 query 将进入后续评测集，用来评测一个全新的 agent 是否能独立完成同一类用户任务，或暴露同一类能力短板。因此 query 必须像真实用户请求，而不是诊断报告、复盘指令或 session 摘要。

工作方法：
1. 先识别原 session 中用户最终真正想完成的任务；如果用户分多轮补充了参数/路径/时间/ID/数量/比例/约束，必须合并。
2. 使用 judge 的 task_description、task_failure_class、reasoning、失败工具摘要来判断“哪个用户任务值得抽成 eval case”，但不要把诊断结论写进 query，除非诊断结论本来就是用户要求。
3. 结合 conversation_outline 与 source_turn_indices，优先选取导致该 judge task 的用户轮次及其前置必要上下文。
4. 如果原 session 中出现多项任务，只输出与当前 judge task 最匹配的一项任务，不要混入无关任务。
5. 如果原任务依赖密钥/API key/token/cookie/Authorization 等凭证，query 只能写“使用调用方显式传入的凭证/密钥参数”，绝不能泄露具体值。

严格要求：
1. 只输出 JSON 对象，不要 Markdown，不要解释。
2. query 必须是上下文无关的单个用户任务；新 agent 不会看到原 session。
3. 如果原始用户任务以 `/skill-name ...` 形式调用 Skill，query 必须逐字保留经过密钥脱敏后的 `/命令` 及其参数，不得改写成自然语言；slash command 是任务路由契约。
4. 必须保留执行所需的具体对象和约束：命令参数名（例如 --task-id、--step-id）、ClawWeb task/step id 值、日期/时间范围、数量、比例、路径、仓库、文件、模型、URL、领域对象等。
5. query 不要写“复现原 session”“根据上文”“继续”“刚刚”“这个”“该任务”等依赖历史上下文的表达。
6. query 不要包含“judge 认为”“失败原因是”“原 agent 出错”等诊断包装。
7. 严禁输出 API key、token、Authorization、cookie、secret 等密钥值；遇到密钥只能省略、占位为 ***，或说明由调用方显式传入。
8. 如果信息不足以构造可执行任务，返回空 query，并在 missing_context 中说明缺什么。

输出 JSON schema：
{
  "query": "一个上下文无关、可直接执行的用户任务；不足则为空字符串",
  "original_user_intent": "你理解的原始用户意图",
  "task_focus": "为什么选择这个任务作为评测 case，简短说明",
  "source_turn_indices": [0, 2],
  "included_context": ["已合并进 query 的关键上下文"],
  "dropped_context": ["丢弃的无关上下文、诊断包装或密钥说明"],
  "missing_context": ["缺失但执行该任务需要的信息"],
  "confidence": 0.0
}
"""


def rewrite_eval_query_with_llm(
    task: dict[str, Any],
    row: SessionRow,
    runtime: LlmRuntimeConfig,
) -> EvalQueryRewriteResult:
    """Ask the configured LLM to produce one replayable, context-free eval query.

    This is deliberately richer than a text rewriter: it gives the model the
    upstream judge result, a structured conversation outline, extracted concrete
    context signals, and failure evidence.  Any LLM call/parse/validation failure
    is returned as warnings so callers can fall back to the deterministic query
    builder.
    """

    session_id = row.session_id or ""
    task_desc = _safe_text(task.get("task_description"), limit=1200)
    failure_class = str(task.get("task_failure_class") or "").strip()
    user_turns = _extract_user_turns(row)
    conversation_outline = _build_conversation_outline(row)
    context_signals = _extract_context_signals(row, task, user_turns, conversation_outline)
    diagnosis_context = _build_diagnosis_context(task, row)
    assistant_preview = _safe_text(row.assistant_text, limit=2400)
    tool_preview = _safe_text(row.tool_text, limit=2400)
    logger.info(
        "eval query rewrite start",
        session_id=session_id,
        path=row.path,
        failure_class=failure_class,
        task_description_preview=_preview(task_desc, 700),
        first_question_preview=_preview(row.first_question, 700),
        user_turn_count=len(user_turns),
        conversation_item_count=len(conversation_outline),
        assistant_chars=len(assistant_preview),
        tool_chars=len(tool_preview),
        context_signal_keys=sorted(context_signals.keys()),
        failed_tool_count=len(diagnosis_context.get("failed_tools") or []),
        runtime_configured=bool(runtime and runtime.api_key),
        model=runtime.model if runtime else "",
        base_url=runtime.base_url if runtime else "",
    )
    if not runtime or not runtime.api_key or not runtime.base_url or not runtime.model:
        warning = "llm_runtime_incomplete"
        logger.warning("eval query rewrite skipped", session_id=session_id, reason=warning)
        return EvalQueryRewriteResult(warnings=[warning])

    payload = {
        "task": {
            "task_description": task_desc,
            "task_failure_class": failure_class,
            "is_complete": task.get("is_complete"),
            "reasoning": _safe_text(task.get("reasoning"), limit=2400),
            "skills": _summarise_tools(task.get("skills")),
            "mcps": _summarise_tools(task.get("mcps")),
        },
        "diagnosis_context": diagnosis_context,
        "session": {
            "session_id": session_id,
            "path": row.path,
            "created_at": row.created_at,
            "bot_id": row.bot_id,
            "original_model": row.original_model,
            "first_question": _safe_text(row.first_question, limit=1200),
            "user_turns": user_turns,
            "conversation_outline": conversation_outline,
            "assistant_preview": assistant_preview,
            "tool_preview": tool_preview,
        },
        "extracted_context_signals": context_signals,
        "eval_case_design_goal": {
            "consumer": "fresh downstream evaluation agent",
            "desired_query_shape": "one natural user request with all necessary parameters embedded",
            "optimize_for": [
                "可执行性",
                "上下文无关",
                "覆盖原用户目标",
                "保留导致任务成败差异的关键约束",
                "避免诊断/复盘包装",
            ],
        },
        "requirements": {
            "goal": "generate one standalone eval query for a fresh agent",
            "must_include_all_necessary_parameters": True,
            "must_be_single_user_task": True,
            "must_not_include_secrets": True,
            "must_not_mention_original_session": True,
            "must_not_be_context_dependent": True,
            "must_not_include_diagnosis_wrapper": True,
            "must_return_empty_query_when_required_context_missing": True,
        },
    }
    user_prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    logger.info(
        "eval query rewrite llm input prepared",
        session_id=session_id,
        payload_bytes=len(user_prompt.encode("utf-8")),
        payload_keys=sorted(payload.keys()),
        task_keys=sorted(payload["task"].keys()),
        diagnosis_keys=sorted(diagnosis_context.keys()),
        user_turn_count=len(user_turns),
        conversation_item_count=len(conversation_outline),
        user_turn_previews=[_preview(x, 280) for x in user_turns[:10]],
        conversation_outline_preview=[_preview(item, 360) for item in conversation_outline[:12]],
        context_signals_preview=_preview(json.dumps(context_signals, ensure_ascii=False, separators=(",", ":")), 2200),
        failed_tools_preview=_preview(json.dumps(diagnosis_context.get("failed_tools") or [], ensure_ascii=False, separators=(",", ":")), 1800),
        assistant_preview=_preview(assistant_preview, 800),
        tool_preview=_preview(tool_preview, 800),
    )

    try:
        raw = chat_json(
            runtime.api_key,
            runtime.base_url,
            runtime.model,
            SYSTEM_PROMPT,
            user_prompt,
            timeout=DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
            call_name=f"query_rewriter:{session_id}",
        )
    except Exception as exc:  # noqa: BLE001 - query rewriting is best-effort; judge fallback must continue.
        warning = f"llm_eval_query_rewriter_failed:{type(exc).__name__}: {exc}"
        logger.warning(
            "eval query rewrite llm failed",
            session_id=session_id,
            error=_preview(warning, 1200),
        )
        return EvalQueryRewriteResult(warnings=[warning])

    result = _result_from_raw(raw)
    result.query = _normalise_query(result.query)
    result.raw = raw if isinstance(raw, dict) else {}
    issues = replayability_issues(result.query)
    logger.info(
        "eval query rewrite llm result",
        session_id=session_id,
        query_preview=_preview(result.query, 1600),
        query_chars=len(result.query),
        confidence=result.confidence,
        original_user_intent=_preview(result.original_user_intent, 800),
        task_focus=_preview(result.task_focus, 800),
        source_turn_indices=result.source_turn_indices,
        included_context=result.included_context,
        dropped_context=result.dropped_context,
        missing_context=result.missing_context,
        warnings=result.warnings,
        replayability_issues=issues,
        raw_keys=sorted(raw.keys()) if isinstance(raw, dict) else [],
    )

    validation_warnings = _validate_rewrite_result(result)
    if issues:
        validation_warnings.extend(f"replayability:{issue}" for issue in issues)
    blocking_warnings = [
        warning for warning in validation_warnings
        if warning != "llm_reported_missing_context"
    ]
    if blocking_warnings:
        result.warnings.extend(validation_warnings)
        logger.warning(
            "eval query rewrite rejected",
            session_id=session_id,
            query_preview=_preview(result.query, 1600),
            reasons=validation_warnings,
            original_user_intent=_preview(result.original_user_intent, 800),
            task_focus=_preview(result.task_focus, 800),
            source_turn_indices=result.source_turn_indices,
        )
        result.query = ""
        return result

    logger.info(
        "eval query rewrite accepted",
        session_id=session_id,
        query_preview=_preview(result.query, 1600),
        confidence=result.confidence,
        original_user_intent=_preview(result.original_user_intent, 800),
        task_focus=_preview(result.task_focus, 800),
        source_turn_indices=result.source_turn_indices,
        included_context=result.included_context,
    )
    return result


def _result_from_raw(raw: dict[str, Any]) -> EvalQueryRewriteResult:
    if not isinstance(raw, dict):
        return EvalQueryRewriteResult(warnings=["llm_response_not_object"])
    return EvalQueryRewriteResult(
        query=str(raw.get("query") or ""),
        original_user_intent=str(raw.get("original_user_intent") or ""),
        task_focus=str(raw.get("task_focus") or ""),
        included_context=_string_list(raw.get("included_context")),
        dropped_context=_string_list(raw.get("dropped_context")),
        missing_context=_string_list(raw.get("missing_context")),
        source_turn_indices=_int_list(raw.get("source_turn_indices")),
        warnings=_string_list(raw.get("warnings")),
        confidence=_float_value(raw.get("confidence")),
        raw=raw,
    )


def _validate_rewrite_result(result: EvalQueryRewriteResult) -> list[str]:
    query = result.query or ""
    warnings: list[str] = []
    if not query:
        warnings.append("empty_llm_query")
    if result.missing_context:
        # Missing context is diagnostic information; replayability assessment
        # decides whether the query is actually unsafe to export.
        warnings.append("llm_reported_missing_context")
    if re.search(r"原始\s*session|原会话|复现|根据上文|继续|刚才|上述|如上|这个问题|该任务|LLM\s*judge|失败原因|原\s*agent", query, re.I):
        warnings.append("contextual_or_diagnosis_wrapper_detected")
    if re.search(r"(?i)(authorization\s*[:=]\s*bearer\s+|api[_-]?key\s*[:=]\s*[^\s*]|token\s*[:=]\s*[^\s*]|cookie\s*[:=]\s*[^\s*]|secret\s*[:=]\s*[^\s*])", query):
        warnings.append("secret_like_material_detected")
    compact_len = len(re.sub(r"\s+", "", query))
    if compact_len > 900:
        warnings.append("query_too_long")
    return warnings


def _extract_user_turns(row: SessionRow) -> list[str]:
    text = row.user_text or row.first_question or ""
    raw_parts = re.split(r"[\n\r]+", str(text or ""))
    turns: list[str] = []
    seen: set[str] = set()
    for raw in raw_parts:
        compact = compact_replay_text(raw, max_len=900) or raw
        turn = _safe_text(compact, limit=900)
        if not turn:
            continue
        key = re.sub(r"\s+", "", turn).lower()
        if key in seen:
            continue
        seen.add(key)
        turns.append(turn)
    if not turns and row.first_question:
        turns.append(_safe_text(row.first_question, limit=900))
    if len(turns) > 24:
        # Preserve the beginning and the tail where users often add the final concrete ask.
        turns = turns[:14] + turns[-10:]
    return turns


def _build_conversation_outline(row: SessionRow) -> list[dict[str, Any]]:
    raw_messages = _raw_messages(row.raw_session)
    outline: list[dict[str, Any]] = []
    if raw_messages:
        for idx, msg in enumerate(raw_messages):
            item = _outline_item(idx, msg)
            if item:
                outline.append(item)
    if not outline:
        for idx, text in enumerate(_extract_user_turns(row)):
            outline.append({"idx": idx, "role": "user", "text": text})
        if row.assistant_text:
            outline.append({"idx": len(outline), "role": "assistant", "text": _safe_text(row.assistant_text, limit=900)})
        if row.tool_text:
            outline.append({"idx": len(outline), "role": "toolResult", "is_error": bool(re.search(r"error|exception|failed|失败|报错", row.tool_text, re.I)), "text": _safe_text(row.tool_text, limit=900)})
    if len(outline) > 36:
        outline = outline[:18] + outline[-18:]
    return outline


def _raw_messages(raw_session: dict[str, Any]) -> list[Any]:
    if not isinstance(raw_session, dict):
        return []
    messages = raw_session.get("messages")
    if isinstance(messages, str):
        try:
            parsed = json.loads(messages)
        except Exception:
            return []
        messages = parsed
    return messages if isinstance(messages, list) else []


def _outline_item(idx: int, msg: Any) -> dict[str, Any]:
    if not isinstance(msg, dict):
        text = _safe_text(msg, limit=1200)
        return {"idx": idx, "role": "unknown", "text": text} if text else {}
    role = str(msg.get("role") or msg.get("type") or msg.get("speaker") or "unknown")
    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
    tool_name = str(msg.get("toolName") or msg.get("tool_name") or msg.get("name") or data.get("toolName") or data.get("tool_name") or "").strip()
    content = _message_text(msg)
    is_error = bool(msg.get("isError") is True or re.search(r"error|exception|traceback|failed|失败|报错", content, re.I))
    item = {
        "idx": idx,
        "role": role,
        "text": _safe_text(content, limit=1200),
    }
    if tool_name:
        item["tool_name"] = _safe_text(tool_name, limit=160)
    if is_error:
        item["is_error"] = True
    return item if item["text"] or tool_name else {}


def _message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_message_text(x) for x in value if x is not None)
    if isinstance(value, dict):
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        for container in (value, data):
            for key in ("finalPromptText", "final_prompt_text", "content", "text", "message", "query", "prompt", "input", "result", "error", "arguments", "args"):
                if key in container and container.get(key) is not None:
                    return _message_text(container.get(key))
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _extract_context_signals(
    row: SessionRow,
    task: dict[str, Any],
    user_turns: list[str],
    outline: list[dict[str, Any]],
) -> dict[str, list[str]]:
    text_parts = [row.first_question, row.user_text, str(task.get("task_description") or "")]
    text_parts.extend(str(item.get("text") or "") for item in outline if item.get("role") in {"user", "human"})
    text = redact_secrets("\n".join(text_parts), [])
    signals = {
        "ids": _unique_regex(text, r"\b(?:EV|STEP|TASK|RUN|BOT|SESSION)-[A-Za-z0-9][A-Za-z0-9_-]{2,}\b", limit=20),
        "cli_flags": _extract_cli_flags(text),
        "date_ranges": _extract_date_ranges(text),
        "dates": _unique_regex(text, r"\b(?:20\d{2}[-/]?\d{2}[-/]?\d{2}|\d{8})\b|\b\d{1,2}\s*月\s*\d{1,2}\s*[日号]?\b", limit=20),
        "paths": _unique_regex(text, r"(?:~|/|\./|\.\./)[A-Za-z0-9_./\-]+|`[^`]*(?:/|\.json|\.md|\.py|\.sql|\.zip)[^`]*`", limit=20),
        "urls": _safe_urls(text),
        "counts_and_ratios": _unique_regex(text, r"\d+\s*(?:个\s*)?(?:case|cases)\b|\d+\s*(?:个|条|份|%|天|小时|次)|至少\s*\d+\s*%|占比\s*(?:至少)?\s*\d+\s*%", limit=20),
        "models": _unique_regex(text, r"\b(?:GLM|Kimi|Qwen|GPT|Claude|DeepSeek)[A-Za-z0-9_.\-]*\b", flags=re.I, limit=12),
        "likely_actions": _unique_regex(text, r"执行|运行|抽取|生成|分析|诊断|上传|创建|修改|实现|修复|查询|搜索|验证|发布|打包", limit=24),
    }
    return {k: v for k, v in signals.items() if v}


def _build_diagnosis_context(task: dict[str, Any], row: SessionRow) -> dict[str, Any]:
    skills = _summarise_tools(task.get("skills"))
    mcps = _summarise_tools(task.get("mcps"))
    failed_tools = [x for x in [*skills, *mcps] if str(x.get("status") or "").lower() == "failure" or x.get("is_correct") in {0, "0", False} or x.get("failure_category")]
    return {
        "judge_task_description": _safe_text(task.get("task_description"), limit=1200),
        "judge_failure_class": str(task.get("task_failure_class") or ""),
        "judge_is_complete": task.get("is_complete"),
        "judge_reasoning": _safe_text(task.get("reasoning"), limit=2400),
        "failed_tools": failed_tools[:16],
        "all_tools": [*skills, *mcps][:24],
        "session_error_evidence": _extract_error_evidence(row),
    }


def _extract_error_evidence(row: SessionRow) -> list[str]:
    text = "\n".join([row.assistant_text or "", row.tool_text or "", row.raw_text or ""])
    evidence: list[str] = []
    for raw in re.split(r"[\n\r]+", text):
        if re.search(r"error|exception|traceback|failed|失败|报错|缺失|missing|invalid|timeout|404|409|502", raw, re.I):
            line = _safe_text(raw, limit=700)
            if line and line not in evidence:
                evidence.append(line)
        if len(evidence) >= 12:
            break
    return evidence


def _summarise_tools(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return out
    for item in value[:16]:
        if not isinstance(item, dict):
            continue
        exe = item.get("execution") if isinstance(item.get("execution"), dict) else {}
        out.append(
            {
                "name": str(item.get("name") or "")[:160],
                "is_correct": item.get("is_correct"),
                "status": exe.get("status"),
                "failure_category": exe.get("failure_category"),
                "summary": _safe_text(item.get("summary") or exe.get("summary") or exe.get("error") or item.get("reason"), limit=700),
            }
        )
    return out


def _extract_cli_flags(text: str) -> list[str]:
    out: list[str] = []
    secret_re = re.compile(r"api[_-]?key|token|secret|authorization|cookie", re.I)
    for match in re.finditer(r"--[A-Za-z][A-Za-z0-9_-]*(?:[=\s]+(?:'[^']*'|\"[^\"]*\"|[^\s，,。；;]+))?", text):
        value = match.group(0).strip()
        if secret_re.search(value):
            flag = value.split("=", 1)[0].split()[0]
            value = f"{flag}=***"
        if value not in out:
            out.append(value[:220])
        if len(out) >= 24:
            break
    return out


def _extract_date_ranges(text: str) -> list[str]:
    patterns = [
        r"(?:20\d{2}[-/]?\d{2}[-/]?\d{2}|\d{8}|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)(?:\s*(?:至|到|~|～|-)\s*)(?:20\d{2}[-/]?\d{2}[-/]?\d{2}|\d{8}|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)",
        r"近\s*\d+\s*天|最近\s*\d+\s*天",
    ]
    out: list[str] = []
    for pattern in patterns:
        for value in re.findall(pattern, text):
            if isinstance(value, tuple):
                value = "".join(value)
            value = re.sub(r"\s+", "", str(value)).strip()
            if value and value not in out:
                out.append(value)
            if len(out) >= 12:
                return out
    return out


def _safe_urls(text: str) -> list[str]:
    urls = _unique_regex(text, r"https?://[^\s，,。；;'\"]+", flags=re.I, limit=16)
    safe: list[str] = []
    for url in urls:
        value = re.sub(r"(?i)(api[_-]?key|token|secret|authorization|cookie)=([^&#]+)", r"\1=***", url)
        safe.append(value)
    return safe


def _unique_regex(text: str, pattern: str, flags: int = 0, limit: int = 20) -> list[str]:
    out: list[str] = []
    for match in re.finditer(pattern, text, flags):
        value = match.group(0) if hasattr(match, "group") else str(match)
        value = _safe_text(value, limit=260)
        if value and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def _safe_text(value: Any, limit: int = 1200) -> str:
    text = strip_runtime_metadata(str(value or ""))
    text = redact_secrets(text, [])
    text = re.sub(r"\[REDACTED_SECRET\]\]+", "[REDACTED_SECRET]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _normalise_query(value: str) -> str:
    text = redact_secrets(str(value or ""), [])
    text = re.sub(r"\[REDACTED_SECRET\]\]+", "[REDACTED_SECRET]", text)
    text = strip_runtime_metadata(text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n`，,。")
    # LLMs sometimes return an imperative wrapper despite the instruction.
    text = re.sub(r"^(?:请)?(?:让)?(?:评测\s*)?agent\s*[:：]\s*", "", text, flags=re.I)
    return text


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_safe_text(x, limit=360) for x in value if _safe_text(x, limit=360)]
    text = _safe_text(value, limit=360)
    return [text] if text else []


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except Exception:
            continue
    return out[:24]


def _float_value(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _preview(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + f"...<truncated {len(text) - limit} chars>"
    return text
