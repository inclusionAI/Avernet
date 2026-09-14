from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from .constants import (
    DEFAULT_CASE_LIMIT,
    DEFAULT_DIAGNOSIS_LIMIT,
    DEFAULT_TIMEOUT_SECONDS,
    GOOD_CASE_MAX_RATIO,
    GOOD_CASE_MIN_RATIO,
)
from .judge.openai_chat_client import chat_json
from .models import CasePreference, LlmRuntimeConfig
from .selection import infer_time_window_from_message
from . import logger

_NAMED_TERM_STOP_CLAUSE_RE = (
    r"(?:，?timeout|，?关键词|，?关键字|，?必须包含|，?重点看|，?聚焦|，?关于|"
    r"，?排除|，?抽取|，?抽|，?包含good|，?包含bad|，?bad占比|，?good占比|"
    r"，?bad比例|，?good比例|，?只要|，?评分|，?高分|，?今天|，?昨天|"
    r"，?最近\d+天|，?近\d+天|，?本周|，?上周)"
)


_ALLOWED_MODES = {
    "retrieval_not_called",
    "retrieval_bad_query",
    "retrieval_relevant_but_not_used",
    "retrieval_or_knowledge_failure",
    "runtime_config_missing",
    "tool_parameter_error",
    "permission_or_network_blocked",
    "workspace_or_data_missing",
    "tool_execution_failure",
    "workflow_planning_failure",
    "premature_capability_boundary",
    "unnecessary_user_blocking",
    "execution_interrupted",
    "async_task_pending",
    "context_or_process_truncated",
    "no_reply_idle",
    "no_task",
    "incorrect_or_unverified_answer",
    "good_regression",
    "unknown_failure_mode",
}


def _normalize_modes(values: list[Any], message: str = "") -> list[str]:
    """Validate model-proposed taxonomy values without semantic keyword rules.

    The LLM owns interpretation of the user's problem statement.  This helper
    only enforces the closed taxonomy contract at the boundary.
    """

    del message
    modes: list[str] = []
    for value in values:
        mode = str(value or "").strip()
        if mode in _ALLOWED_MODES and mode not in modes:
            modes.append(mode)
    return modes


def _rule_parse(message: str) -> CasePreference:
    limit = _parse_case_limit(message)
    timeout = DEFAULT_TIMEOUT_SECONDS
    tm = re.search(r"timeout\s*[=为:]?\s*(\d+)\s*s?", message, re.I)
    if tm:
        timeout = max(DEFAULT_TIMEOUT_SECONDS, int(tm.group(1)))

    focus_terms = _parse_focus_terms(message)
    required_terms = _parse_named_terms(
        message, ("关键词", "关键字", "必须包含", "包含关键词", "include", "must")
    )
    excluded_terms = _parse_named_terms(
        message, ("排除", "不要包含", "不包含", "exclude", "without")
    )
    modes = _normalize_modes([], message)
    diagnosis_mode, hypothesis_text = _infer_diagnosis_mode(message)
    bad_count, good_count = _parse_case_type_counts(message, limit)
    if bad_count is not None or good_count is not None:
        limit = max(limit, (bad_count or 0) + (good_count or 0))
    since, until, time_label = infer_time_window_from_message(message)
    max_sessions = _parse_max_sessions(message)
    include_good = (
        "不要good" not in message.lower()
        and "不需要good" not in message.lower()
        and "只要bad" not in message.lower()
    )
    if good_count == 0:
        include_good = False
    if good_count and good_count > 0:
        include_good = True
    return CasePreference(
        raw_message=message,
        intent_text=message.strip(),
        intent_confidence=0.45 if message.strip() else 0.0,
        requires_broad_recall=False,
        diagnosis_mode=diagnosis_mode,
        hypothesis_text=hypothesis_text,
        case_limit=limit,
        diagnosis_limit=max(DEFAULT_DIAGNOSIS_LIMIT, limit * 4),
        include_good=include_good,
        good_min_ratio=GOOD_CASE_MIN_RATIO,
        good_max_ratio=GOOD_CASE_MAX_RATIO,
        bad_case_count=bad_count,
        good_case_count=good_count,
        dataset_profile=_parse_dataset_profile(message),
        since=since,
        until=until,
        time_range_label=time_label,
        focus_terms=focus_terms,
        required_terms=required_terms,
        excluded_terms=excluded_terms,
        target_failure_modes=modes,
        scoring_requirements=message,
        timeout_seconds=timeout,
        drop_context_dependent=True,
        rewrite_multi_sentence_query=True,
        search_evidence_required_for_high_score=True,
        stable_run_required=True,
        max_sessions=max_sessions,
    )


def _preference_log_payload(pref: CasePreference) -> dict[str, Any]:
    return {
        "intent_text": pref.intent_text[:500],
        "intent_confidence": pref.intent_confidence,
        "requires_broad_recall": pref.requires_broad_recall,
        "diagnosis_mode": pref.diagnosis_mode,
        "hypothesis_text": pref.hypothesis_text,
        "case_limit": pref.case_limit,
        "diagnosis_limit": pref.diagnosis_limit,
        "include_good": pref.include_good,
        "bad_case_count": pref.bad_case_count,
        "good_case_count": pref.good_case_count,
        "dataset_profile": pref.dataset_profile,
        "since": pref.since,
        "until": pref.until,
        "focus_terms": pref.focus_terms,
        "required_terms": pref.required_terms,
        "excluded_terms": pref.excluded_terms,
        "target_failure_modes": pref.target_failure_modes,
        "timeout_seconds": pref.timeout_seconds,
        "max_sessions": pref.max_sessions,
        "drop_context_dependent": pref.drop_context_dependent,
    }


def _message_preview(message: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) > limit:
        return text[:limit] + f"...<truncated {len(text) - limit} chars>"
    return text


def parse_preference(
    message: str, llm: LlmRuntimeConfig
) -> tuple[CasePreference, list[str]]:
    warnings: list[str] = []
    raw_message = message or ""
    logger.info(
        "preference parse start",
        message_chars=len(raw_message),
        message_preview=_message_preview(raw_message),
        llm_preference_enabled=bool(raw_message.strip() and llm.api_key),
        llm_base_url=llm.base_url,
        llm_model=llm.model,
    )
    if not raw_message.strip() or not llm.api_key:
        pref = _rule_parse(raw_message)
        logger.info(
            "preference parse done",
            source="rule_only",
            reason="empty_message"
            if not raw_message.strip()
            else "llm_api_key_missing",
            warning_count=len(warnings),
            **_preference_log_payload(pref),
        )
        return pref, warnings

    try:
        data = chat_json(
            llm.api_key,
            llm.base_url,
            llm.model,
            _preference_parse_system_prompt(),
            raw_message,
            call_name="preference_parse",
        )
        logger.info(
            "preference llm parse raw result",
            result_keys=sorted(data.keys()) if isinstance(data, dict) else [],
            llm_since=str(data.get("since") or "") if isinstance(data, dict) else "",
            llm_until=str(data.get("until") or "") if isinstance(data, dict) else "",
            llm_case_limit=data.get("case_limit") if isinstance(data, dict) else None,
            llm_bad_case_count=data.get("bad_case_count")
            if isinstance(data, dict)
            else None,
            llm_good_case_count=data.get("good_case_count")
            if isinstance(data, dict)
            else None,
            llm_target_failure_modes=data.get("target_failure_modes")
            if isinstance(data, dict)
            else None,
        )
        pref = _preference_from_llm_data(raw_message, data)
        logger.info(
            "preference parse done",
            source="llm_json",
            warning_count=len(warnings),
            **_preference_log_payload(pref),
        )
        return pref, warnings
    except Exception as exc:
        warning = f"LLM preference parsing failed; used rule fallback: {type(exc).__name__}: {exc}"
        warnings.append(warning)
        pref = _rule_parse(raw_message)
        logger.warning(
            "preference llm parse failed; using rule fallback",
            error=f"{type(exc).__name__}: {exc}",
            warning=warning,
            **_preference_log_payload(pref),
        )
        logger.info(
            "preference parse done",
            source="rule_fallback_after_llm_failure",
            warning_count=len(warnings),
            **_preference_log_payload(pref),
        )
        return pref, warnings


def _preference_parse_system_prompt() -> str:
    now = datetime.now(timezone.utc).isoformat()
    modes = ", ".join(sorted(_ALLOWED_MODES))
    return f"""
你是 clawevolve-diagnose 的自然语言参数解析器。你的任务是把用户的一句话需求解析成后续程序可直接使用的 JSON 参数。

当前时间: {now}
时间要求:
- 所有相对时间都必须基于“当前时间”换算。
- since/until 使用 ISO-8601 字符串，推荐带时区；没有边界则填空字符串 ""。
- “近/最近/过去 N 天” => since=当前时间-N天, until=""。
- “近/最近/过去 N 周” => N*7 天。
- “近/最近/过去 N 个月/月” => N*30 天；中文数字如 一/两/二/三 也要识别。
- “今天/昨天/前天/本周/上周/本月/上月”要换算为明确 since/until。
- 用户明确说“不限时间/全部时间/全量/all time”时 since="", until=""。
- 用户没有时间要求时，默认近3天：since=当前时间-3天, until=""。

必须只输出一个 JSON object，不要 markdown，不要解释，不要多余文本。字段必须完整：
{{
  "case_limit": int,
  "bad_case_count": int|null,
  "good_case_count": int|null,
  "include_good": bool,
  "dataset_profile": "default"|"focused"|"regression",
  "since": string,
  "until": string,
  "focus_terms": [string],
  "required_terms": [string],
  "excluded_terms": [string],
  "target_failure_modes": [string],
  "scoring_requirements": string,
  "intent_text": string,
  "intent_confidence": number,
  "requires_broad_recall": bool,
  "diagnosis_mode": "exploratory"|"hypothesis",
  "hypothesis_text": string,
  "timeout_seconds": int,
  "max_sessions": int|null,
  "drop_context_dependent": bool
}}

字段含义和硬规则：
1. case_limit 是最终要输出的 case 总数，范围 1..100；未指定时用 5。
2. diagnosis 内部预算由程序计算，不要输出 diagnosis_limit。
3. 用户说“2个bad case / 2条失败case / bad case两个”时，表示最终只要 2 个 bad：case_limit=2, bad_case_count=2, good_case_count=0, include_good=false。
4. 用户同时指定 good/bad 数量时，case_limit 应等于二者之和，除非用户另有明确总数且更大。
5. 用户只指定 bad 数量且没有说还要 good 时，good_case_count=0, include_good=false。
6. 用户只指定 good 数量或“回归保护/成功样例”为主时，bad_case_count=0, include_good=true, dataset_profile="regression"。
7. 用户说“包含/混合/搭配/一些 good”才给 good_case_count 或 include_good=true；否则不要自行补 good 配额。
8. focus_terms 是主题或问题关键词，用于优先分析 session；不要把 good/bad/case/数量/时间词放进去。
9. required_terms/excluded_terms 只在用户明确“必须包含/关键词/include/must”或“排除/不要包含/exclude/without”时填写。
10. target_failure_modes 只能使用以下枚举，不能发明新值：{modes}
11. 常见映射：搜索/检索 => retrieval_or_knowledge_failure；没搜/未搜索 => retrieval_not_called；query错误 => retrieval_bad_query；证据没用 => retrieval_relevant_but_not_used；工具失败 => tool_execution_failure；参数错误 => tool_parameter_error；权限/网络 => permission_or_network_blocked；配置缺失 => runtime_config_missing；等待用户/不必要澄清 => unnecessary_user_blocking；中断/重跑 => execution_interrupted；异步/pending => async_task_pending；无回复/空转 => no_reply_idle；编造/幻觉/答案错误 => incorrect_or_unverified_answer。
12. dataset_profile: 普通需求 default；聚焦某类问题/主题 focused；只抽 good 或回归保护 regression。
13. timeout_seconds 未指定用 {DEFAULT_TIMEOUT_SECONDS}，不能小于 {DEFAULT_TIMEOUT_SECONDS}。
14. max_sessions 只在用户明确“最多分析/扫描 N 个 session/会话”时填写，否则 null。
15. drop_context_dependent 默认 true，除非用户明确要保留上下文依赖 case。
16. scoring_requirements 保留用户原始需求的简洁复述，不能包含密钥。
17. intent_text 是后续 session 判断和 plan 使用的自然语言语义意图；保留用户目标、对象、问题范围和期望结果，不要退化成关键词或固定 failure mode 列表；没有明确意图时填空字符串。
18. requires_broad_recall：兼容字段，固定为 false。Diagnose 在已选 case 满足用户要求的数量、good/bad 配额和质量约束后立即停止，不为扩大召回继续扫描。
19. intent_confidence 为 0..1；只有能明确理解用户语义时才给高分。
20. diagnosis_mode：用户要求“找出/分析主要问题”时为 exploratory；要求“验证/确认是否存在某个具体问题”时为 hypothesis。
21. hypothesis_text：hypothesis 模式必须保留待验证的具体假设；exploratory 模式填空字符串。

示例：
输入: 近一个月的2个bad case
输出: {{"case_limit":2,"bad_case_count":2,"good_case_count":0,"include_good":false,"dataset_profile":"default","since":"<当前时间减30天的ISO>","until":"","focus_terms":[],"required_terms":[],"excluded_terms":[],"target_failure_modes":[],"scoring_requirements":"近一个月的2个bad case","timeout_seconds":{DEFAULT_TIMEOUT_SECONDS},"max_sessions":null,"drop_context_dependent":true}}

输入: 最近7天抽10个case，bad占比80%，重点看工具参数错误，最多分析200个session
输出: {{"case_limit":10,"bad_case_count":8,"good_case_count":2,"include_good":true,"dataset_profile":"focused","since":"<当前时间减7天的ISO>","until":"","focus_terms":["工具参数错误"],"required_terms":[],"excluded_terms":[],"target_failure_modes":["tool_parameter_error"],"scoring_requirements":"最近7天抽10个case，bad占比80%，重点看工具参数错误","timeout_seconds":{DEFAULT_TIMEOUT_SECONDS},"max_sessions":200,"drop_context_dependent":true}}
""".strip()


def _preference_from_llm_data(raw_message: str, data: dict[str, Any]) -> CasePreference:
    if not isinstance(data, dict):
        raise ValueError("preference parser must return a JSON object")

    case_limit = _bounded_int(data.get("case_limit"), DEFAULT_CASE_LIMIT, 1, 100)
    bad_count = _bounded_nullable_int(data.get("bad_case_count"), 0, 100)
    good_count = _bounded_nullable_int(data.get("good_case_count"), 0, 100)
    if bad_count is not None and good_count is not None:
        case_limit = max(case_limit, bad_count + good_count)
    case_limit = max(1, min(100, case_limit))
    if bad_count is not None:
        bad_count = max(0, min(case_limit, bad_count))
    if good_count is not None:
        good_count = max(0, min(case_limit, good_count))
    if (
        bad_count is not None
        and good_count is not None
        and bad_count + good_count > case_limit
    ):
        overflow = bad_count + good_count - case_limit
        if good_count >= overflow:
            good_count -= overflow
        else:
            bad_count = max(0, bad_count - (overflow - good_count))
            good_count = 0
    include_good = bool(data.get("include_good", True))
    if good_count == 0:
        include_good = False
    if good_count and good_count > 0:
        include_good = True

    profile = str(data.get("dataset_profile") or "default").strip()
    if profile not in {"default", "focused", "regression"}:
        profile = "default"

    timeout = _bounded_int(
        data.get("timeout_seconds"),
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
        24 * 3600,
    )
    max_sessions = _bounded_nullable_int(data.get("max_sessions"), 1, 10000)
    modes = _normalize_modes(list(data.get("target_failure_modes") or []), raw_message)
    fallback_mode, fallback_hypothesis = _infer_diagnosis_mode(raw_message)
    diagnosis_mode = str(data.get("diagnosis_mode") or fallback_mode).strip().lower()
    if diagnosis_mode not in {"exploratory", "hypothesis"}:
        diagnosis_mode = fallback_mode
    hypothesis_text = str(data.get("hypothesis_text") or fallback_hypothesis).strip()
    if diagnosis_mode == "exploratory":
        hypothesis_text = ""
    if _mentions_good_bad_mix(raw_message):
        modes = [m for m in modes if m != "good_regression"]

    return CasePreference(
        raw_message=raw_message,
        intent_text=str(data.get("intent_text") or raw_message).strip(),
        intent_confidence=_clamp01(
            data.get("intent_confidence"), 0.6 if raw_message.strip() else 0.0
        ),
        requires_broad_recall=False,
        diagnosis_mode=diagnosis_mode,
        hypothesis_text=hypothesis_text,
        case_limit=case_limit,
        diagnosis_limit=max(DEFAULT_DIAGNOSIS_LIMIT, case_limit * 4),
        include_good=include_good,
        good_min_ratio=GOOD_CASE_MIN_RATIO,
        good_max_ratio=GOOD_CASE_MAX_RATIO,
        bad_case_count=bad_count,
        good_case_count=good_count,
        dataset_profile=profile,
        since=str(data.get("since") or "").strip(),
        until=str(data.get("until") or "").strip(),
        time_range_label="",
        focus_terms=_dedupe_terms(
            [
                str(x)
                for x in data.get("focus_terms") or []
                if str(x).strip() and not _is_case_mix_control_term(str(x))
            ]
        )[:12],
        required_terms=_dedupe_terms(
            [
                str(x)
                for x in data.get("required_terms") or []
                if str(x).strip() and not _is_case_mix_control_term(str(x))
            ]
        )[:12],
        excluded_terms=_dedupe_terms(
            [str(x) for x in data.get("excluded_terms") or [] if str(x).strip()]
        )[:12],
        target_failure_modes=modes,
        scoring_requirements=str(
            data.get("scoring_requirements") or raw_message
        ).strip(),
        timeout_seconds=timeout,
        drop_context_dependent=bool(data.get("drop_context_dependent", True)),
        rewrite_multi_sentence_query=True,
        search_evidence_required_for_high_score=True,
        stable_run_required=True,
        max_sessions=max_sessions,
    )


def _infer_diagnosis_mode(message: str) -> tuple[str, str]:
    """Classify the user's product intent without changing filter semantics."""

    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text:
        return "exploratory", ""
    hypothesis_patterns = (
        r"(?:验证|确认|检查|判断|分析).{0,20}(?:是否|有没有|有无|是不是)",
        r"(?:是否|有没有|有无|是不是).{0,30}(?:问题|失败|错误|现象|情况)",
        r"(?:验证|确认).{0,40}(?:问题|失败|错误|假设)",
    )
    if any(re.search(pattern, text, re.I) for pattern in hypothesis_patterns):
        return "hypothesis", text
    return "exploratory", ""


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _bounded_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(lower, min(upper, parsed))


def _bounded_nullable_int(value: Any, lower: int, upper: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(lower, min(upper, int(value)))
    except Exception:
        return None


def _parse_case_limit(message: str) -> int:
    text = message or ""
    patterns = [
        r"(?:抽取|抽|选择|选|生成|构建)\s*(\d+)\s*(?:个|条)?\s*(?:case|cases|用例)?",
        r"(\d+)\s*(?:个|条)?\s*(?:case|cases|用例)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            if _case_limit_match_is_date_fragment(text, m):
                logger.info(
                    "case limit candidate ignored as date fragment",
                    candidate=m.group(1),
                    span=m.span(1),
                    context=_match_context(text, m.span(1)),
                )
                continue
            return max(1, min(100, int(m.group(1))))
    return DEFAULT_CASE_LIMIT


def _case_limit_match_is_date_fragment(text: str, match: re.Match[str]) -> bool:
    number = match.group(1)
    start, end = match.span(1)
    before = text[max(0, start - 2) : start]
    after = text[end : min(len(text), end + 2)]
    if after.startswith(("-", "/", "年")):
        return True
    if before.endswith(("-", "/", "年")) or after.startswith(("月", "日")):
        return True
    if len(number) == 8 and re.fullmatch(r"\d{8}", number):
        return True
    return False


def _match_context(text: str, span: tuple[int, int], radius: int = 16) -> str:
    start, end = span
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _parse_max_sessions(message: str) -> int | None:
    """Parse natural-language controls for the maximum local sessions to analyze."""

    text = message or ""
    patterns = [
        r"最多\s*(?:分析|处理|扫描|看)?\s*(\d+)\s*(?:个|条)?\s*(?:session|sessions|会话)",
        r"(?:分析|处理|扫描|看)\s*(?:前|最多)?\s*(\d+)\s*(?:个|条)?\s*(?:session|sessions|会话)",
        r"max[_\s-]*sessions?\s*[=:：]?\s*(\d+)",
        r"最大(?:session|sessions|会话)数\s*[=:：]?\s*(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        return max(1, min(10000, int(m.group(1))))
    return None


def _mentions_good_bad_mix(message: str) -> bool:
    text = (message or "").lower()
    has_good = bool(re.search(r"good|成功|好case|回归", text, re.I))
    has_bad = bool(re.search(r"bad|失败|错误|坏case", text, re.I))
    return (
        has_good
        and has_bad
        and not re.search(r"只(?:要|抽|需要).*(?:good|好|成功|回归)", text, re.I)
    )


def _is_case_mix_control_term(value: str) -> bool:
    return value.strip().lower() in {
        "good",
        "bad",
        "好",
        "坏",
        "成功",
        "失败case",
        "good case",
        "bad case",
    }


def _parse_focus_terms(message: str) -> list[str]:
    """Extract explicitly labelled terms only; do not infer domain semantics."""

    extra = _parse_named_terms(message, ("重点看", "聚焦", "关于", "topic", "focus"))
    return _dedupe_terms(extra)[:12]


def _parse_named_terms(message: str, labels: tuple[str, ...]) -> list[str]:
    terms: list[str] = []
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[=:：]?\s*([^；;。\n]+)"
        for match in re.finditer(pattern, message or "", re.I):
            segment = match.group(1)
            # Stop before the next common control clause so free-form scoring
            # requirements do not become dozens of accidental terms.
            segment = re.split(
                (_NAMED_TERM_STOP_CLAUSE_RE),
                segment,
                maxsplit=1,
                flags=re.I,
            )[0]
            for token in re.split(r"[,，、/|\s]+", segment):
                token = token.strip(" '\"`[]()（）")
                if 1 < len(token) <= 40 and not re.fullmatch(r"\d+个?", token):
                    terms.append(token)
    return _dedupe_terms(terms)[:12]


def _dedupe_terms(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = re.sub(r"\s+", " ", str(value or "")).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def _nullable_int(value: Any, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return max(0, int(value))
    except Exception:
        return default


def _parse_case_type_counts(message: str, limit: int) -> tuple[int | None, int | None]:
    text = message or ""
    bad: int | None = None
    good: int | None = None
    for pattern, target in [
        (r"(\d+)\s*(?:个|条)?\s*(?:bad|坏|失败|错误)", "bad"),
        (r"(?:bad|坏|失败|错误)\s*(\d+)\s*(?:个|条)?", "bad"),
        (r"(\d+)\s*(?:个|条)?\s*(?:good|好|成功|回归)", "good"),
        (r"(?:good|好|成功|回归)\s*(\d+)\s*(?:个|条)?", "good"),
    ]:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        if target == "bad":
            bad = int(m.group(1))
        else:
            good = int(m.group(1))
    ratio_bad, ratio_good = _parse_case_type_ratio_counts(text, limit)
    if ratio_bad is not None:
        bad = ratio_bad
    if ratio_good is not None:
        good = ratio_good
    if re.search(r"只(?:要|抽|需要).*(?:good|好|成功|回归)", text, re.I):
        bad = 0
    if re.search(r"只(?:要|抽|需要).*(?:bad|坏|失败|错误)", text, re.I):
        good = 0
    return bad, good


def _parse_case_type_ratio_counts(
    message: str, limit: int
) -> tuple[int | None, int | None]:
    text = message or ""
    bad_ratio = _parse_ratio_for_case_type(text, "bad")
    good_ratio = _parse_ratio_for_case_type(text, "good")
    if bad_ratio is None and good_ratio is None:
        return None, None
    total = max(1, limit)
    bad: int | None = None
    good: int | None = None
    if bad_ratio is not None:
        bad = max(0, min(total, math.ceil(total * bad_ratio)))
        if _mentions_good_bad_mix(text):
            good = max(0, total - bad)
    if good_ratio is not None:
        good = max(0, min(total, math.ceil(total * good_ratio)))
        if _mentions_good_bad_mix(text):
            bad = max(0, total - good)
    if bad is not None and good is not None and bad + good > total:
        if bad_ratio is not None and good_ratio is None:
            good = max(0, total - bad)
        elif good_ratio is not None and bad_ratio is None:
            bad = max(0, total - good)
    return bad, good


def _parse_ratio_for_case_type(text: str, case_type: str) -> float | None:
    words = {
        "bad": r"bad|坏|失败|错误",
        "good": r"good|好|成功|回归",
    }[case_type]
    comparator = r"(?:至少|不少于|不低于|>=|≥|大于等于|超过|大于)?"
    patterns = [
        # bad占比至少80% / bad 比例 >= 80% / good rate 20%
        rf"(?:{words})\s*(?:的)?\s*(?:占比|比例|ratio|rate)\s*{comparator}\s*(\d+(?:\.\d+)?)\s*%",
        # bad至少80% / good >= 20%
        rf"(?:{words})\s*{comparator}\s*(\d+(?:\.\d+)?)\s*%",
        # 至少80%bad / 20% 的 good
        rf"{comparator}\s*(\d+(?:\.\d+)?)\s*%\s*(?:的)?\s*(?:{words})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return max(0.0, min(1.0, float(m.group(1)) / 100.0))
    return None


def _parse_dataset_profile(message: str) -> str:
    """Return a neutral fallback when the semantic parser is unavailable."""

    text = (message or "").lower()
    if re.search(r"回归保护|regression|只.*good|good.*为主", text, re.I):
        return "regression"
    return "default"
