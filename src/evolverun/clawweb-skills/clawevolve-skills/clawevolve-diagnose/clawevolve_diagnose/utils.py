from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_CONTEXTUAL_RE = re.compile(
    r"^(这个|那个|上述|上面|前面|刚才|继续|还是|按你说的|就这样|它|他们|这些|这个问题|这个case)\b|"
    r"(如上|同上|继续处理|接着来|再试一次|重新来|上一步)",
    re.I,
)
_META_RE = re.compile(r"<[^>]{1,40}>|```.*?```", re.S)
_UNTRUSTED_METADATA_RE = re.compile(
    r"[^\n]{0,120}?\(untrusted metadata\):\s*```json\s*.*?```", re.S | re.I
)
_BCS_CONTEXT_RE = re.compile(r"\[BCS Group Context\]\s*\n(?:[- ].+\n)*", re.I)
_TIMESTAMP_PREFIX_RE = re.compile(r"^\s*\[[A-Za-z]{3}\s+\d{4}-\d{2}-\d{2}[^\]]*\]\s*")


def _path_expand(value: str | Path) -> Path:
    return Path(value).expanduser()


def _read_json_safe(path: str | Path) -> Any:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return None
        return json.loads(p.read_text(errors="ignore"))
    except Exception:
        return None


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, max_len: int = 64) -> str:
    text = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value.strip().lower()).strip("_")
    return (text or "case")[:max_len]


def strip_runtime_metadata(text: str) -> str:
    value = _UNTRUSTED_METADATA_RE.sub(" ", text or "")
    value = _BCS_CONTEXT_RE.sub(" ", value)
    value = re.sub(r"\[消息内容\]\s*", " ", value)
    value = _META_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return _TIMESTAMP_PREFIX_RE.sub("", value).strip()


def split_sentences(text: str) -> list[str]:
    normalized = strip_runtime_metadata(text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized)
    return [p.strip(" ，,。.!！？?；;") for p in parts if len(p.strip()) >= 2]


def is_context_dependent_query(text: str) -> bool:
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value:
        return True
    if _CONTEXTUAL_RE.search(value):
        return True
    # Too many deictic terms with no concrete noun usually makes replay unstable.
    deictic = len(re.findall(r"这个|那个|上述|上面|前面|刚才|它|他们|这些", value))
    concrete = len(re.findall(r"[A-Za-z][A-Za-z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", value))
    return deictic >= 2 and concrete <= 4


def clean_query(text: str, max_len: int = 500) -> str:
    """Convert noisy first_question/user_text into a single replayable query.

    It is deliberately conservative: choose the clearest user-intent sentence instead of
    concatenating unrelated sentences.  LLM rewriting can be added above this primitive, but the
    baseline must be deterministic and safe for online skill execution.
    """

    sentences = split_sentences(text)
    if not sentences:
        return ""

    def score(sentence: str) -> float:
        length = len(sentence)
        s = min(length, 160) / 160
        if is_context_dependent_query(sentence):
            s -= 0.55
        if re.search(
            r"请|帮|如何|怎么|为什么|查询|搜索|生成|分析|修复|上传|总结|抽取|实现",
            sentence,
            re.I,
        ):
            s += 0.25
        if re.search(r"^(好的|可以|嗯|啊|ok|OK|hi|hello|你好)$", sentence):
            s -= 1
        if re.search(r"api[_ -]?key|token|cookie|secret", sentence, re.I):
            s -= 0.35
        return s

    best = max(sentences, key=score)
    best = re.sub(r"\s+", " ", best).strip()
    return best[:max_len]



_REPLAY_TRIVIAL_RE = re.compile(
    r"^(你好|你是谁|在吗|hi|hello|hey|ok|好的|谢谢|继续|同上|如上|thank(s| you)?|test|测试|ping)[。.!！?？\s]*$",
    re.I,
)
_REPLAY_ACTION_RE = re.compile(
    r"请|帮|如何|怎么|为什么|查询|搜索|检索|生成|分析|处理|修复|上传|总结|抽取|实现|新增|创建|修改|优化|运行|执行|测试|验证|排查|定位|对比|下载|安装|发布|写|读|查看|列出|统计|解释|翻译|create|build|implement|generate|add|fix|debug|analy[sz]e|run|execute|test|search|query|look\s*up|summari[sz]e|upload|publish|install|read|write|list",
    re.I,
)
_REPLAY_OBJECT_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_./\-]{2,}|[\u4e00-\u9fff]{2,}|\b\d+[./-]\d+\b|`[^`]{2,}`|['\"][^'\"]{2,}['\"]",
    re.I,
)
_REPLAY_SLASH_COMMAND_RE = re.compile(
    r"^/(?P<command>[A-Za-z][A-Za-z0-9._/-]*)(?:\s+(?P<args>.*))?$",
    re.S,
)
_REPLAY_CLI_RE = re.compile(
    r"^(?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+"
    r"(?:\s+--?[A-Za-z0-9][A-Za-z0-9_-]*(?:[= ]|$)"
    r"|\s+[A-Za-z0-9][A-Za-z0-9_.:/-]*)",
    re.S,
)
_SECRET_LIKE_RE = re.compile(r"(?:api[_ -]?key|token|authorization|cookie|secret)\s*[:=]\s*[^\s*]+", re.I)
_REPLAY_CONTEXT_ANCHOR_RE = re.compile(
    r"(?:https?://|/[^\s]+|\b(?:id|task[_ -]?id|step[_ -]?id|path|file|url|name)\s*[:=]\s*[^\s]+|\b[0-9a-f]{8,}\b)",
    re.I,
)


def is_trivial_query(text: str) -> bool:
    value = re.sub(r"\s+", " ", strip_runtime_metadata(text or "")).strip()
    return not value or bool(_REPLAY_TRIVIAL_RE.fullmatch(value))


@dataclass(frozen=True)
class ReplayabilityAssessment:
    """Structured replayability result used by diagnosis and selection.

    Only ``hard_failures`` make a case ineligible.  Warnings describe
    uncertainty and are used for ranking/observability, not silent rejection.
    """

    replayable: bool
    query_type: str
    hard_failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    confidence: float = 0.0
    action: str = ""
    objects: tuple[str, ...] = ()


def assess_replayability(text: str) -> ReplayabilityAssessment:
    """Assess whether a query is safe and useful for a fresh evaluation agent.

    The evaluator recognizes executable command-shaped requests before applying
    natural-language heuristics. This prevents short but complete slash/CLI/API
    requests from being discarded merely because they lack prose verbs.
    """
    value = re.sub(r"\s+", " ", strip_runtime_metadata(text or "")).strip()
    if not value:
        return ReplayabilityAssessment(False, "empty", ("empty_query",), confidence=0.0)
    if is_trivial_query(value):
        return ReplayabilityAssessment(False, "trivial", ("trivial_or_greeting_query",), confidence=0.0)
    if _SECRET_LIKE_RE.search(value):
        return ReplayabilityAssessment(False, "unsafe", ("contains_secret_like_material",), confidence=0.0)

    slash = _REPLAY_SLASH_COMMAND_RE.fullmatch(value)
    if slash:
        args = slash.group("args") or ""
        objects = tuple(re.findall(r"(?:--?[A-Za-z][\w-]*(?:=|\s+))?[^\s]+", args))
        if not args.strip():
                return ReplayabilityAssessment(
                True,
                "slash_command",
                warnings=("command_arguments_not_observed",),
                confidence=0.82,
                action="/" + slash.group("command"),
                objects=objects,
            )
        return ReplayabilityAssessment(
            True,
            "slash_command",
            confidence=0.98,
            action="/" + slash.group("command"),
            objects=objects,
        )

    has_url_or_path = bool(re.search(r"(?:https?://|(?:^|\s)/[A-Za-z0-9_.-]+)", value))
    has_assignment = bool(re.search(r"\b[A-Za-z][A-Za-z0-9_-]*\s*[:=]\s*[^\s]+", value))
    has_cli_shape = bool(_REPLAY_CLI_RE.search(value))
    objects = tuple(_REPLAY_OBJECT_RE.findall(value))
    action = bool(_REPLAY_ACTION_RE.search(value))
    if has_url_or_path or has_assignment or has_cli_shape:
        warnings: list[str] = []
        hard: list[str] = []
        has_context_reference = is_context_dependent_query(value) or bool(
            re.search(r"这个|那个|上述|上面|前面|刚才|它|他们|这些", value)
        )
        if has_context_reference:
            if _REPLAY_CONTEXT_ANCHOR_RE.search(value):
                warnings.append("context_reference_resolved_by_explicit_anchor")
            else:
                hard.append("unresolved_context_reference")
        if not action:
            warnings.append("structured_request_without_natural_language_action")
        return ReplayabilityAssessment(
            not hard,
            "structured_request",
            tuple(hard),
            tuple(warnings),
            0.9 if not hard else 0.0,
            objects=objects,
        )

    warnings: list[str] = []
    hard: list[str] = []
    compact_len = len(re.sub(r"\s+", "", value))
    if is_context_dependent_query(value):
        if _REPLAY_CONTEXT_ANCHOR_RE.search(value) and (action or _REPLAY_ACTION_RE.search(value)):
            warnings.append("context_reference_resolved_by_explicit_anchor")
        else:
            hard.append("unresolved_context_reference")
    if compact_len < 12:
        warnings.append("query_too_short_for_replay")
    if len(objects) < 2 and compact_len < 40:
        warnings.append("insufficient_task_objects_or_constraints")
    if not action and compact_len < 60:
        warnings.append("missing_action_intent")
    if not hard:
        confidence = 0.72 if action or len(objects) >= 2 or compact_len >= 40 else 0.45
        return ReplayabilityAssessment(True, "natural_language", tuple(hard), tuple(warnings), confidence, objects=objects)
    return ReplayabilityAssessment(False, "natural_language", tuple(hard), tuple(warnings), 0.0, objects=objects)


def replayability_issues(text: str) -> list[str]:
    """Return only hard replayability failures; warnings never silently drop a case."""
    return list(assess_replayability(text).hard_failures)

def is_replayable_query(text: str) -> bool:
    return not replayability_issues(text)


def compact_replay_text(text: str, max_len: int = 1200) -> str:
    """Clean and bound text while preserving enough task context for replay."""

    value = strip_runtime_metadata(text or "")
    value = redact_secrets(value, [])
    lines: list[str] = []
    for raw in re.split(r"\n+", value):
        line = re.sub(r"\s+", " ", raw).strip(" ，,。\t")
        line = re.sub(r"^(继续|同上|如上|按上面来|按你说的|接着来)[，,。.!！?？\s]+", "", line).strip()
        if not line or is_trivial_query(line):
            continue
        if is_context_dependent_query(line) and len(re.sub(r"\s+", "", line)) < 30:
            continue
        if line not in lines:
            lines.append(line)
    if not lines:
        return ""
    joined = "\n".join(lines)
    if len(joined) <= max_len:
        return joined
    head = joined[: int(max_len * 0.72)].rstrip()
    tail = joined[-int(max_len * 0.20) :].lstrip()
    return f"{head}\n...\n{tail}"[:max_len]

def redact_secrets(text: str, secrets: list[str] | tuple[str, ...] | None = None) -> str:
    """Redact API keys/tokens from diagnostic text before persisting artifacts.

    The slash command may carry an API key inline.  Error strings from HTTP
    clients or imported judge code must never echo that key into JSON/Markdown
    outputs.
    """

    value = str(text or "")
    for secret in secrets or []:
        secret_value = str(secret or "").strip()
        if len(secret_value) >= 6:
            value = value.replace(secret_value, "[REDACTED_SECRET]")
    # Header/key-value forms: Authorization: Bearer ..., api_key=..., token: ...
    value = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\]}]+",
        r"\1[REDACTED_SECRET]",
        value,
    )
    value = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|access[_-]?key)\s*[:=]\s*)['\"]?[^'\"\s,;\]}]+['\"]?",
        r"\1[REDACTED_SECRET]",
        value,
    )
    # Common long key-looking tokens; keep conservative to avoid mangling normal text.
    value = re.sub(r"\b(?:sk|ak|I7)[A-Za-z0-9_\-]{16,}\b", "[REDACTED_SECRET]", value)
    return value
