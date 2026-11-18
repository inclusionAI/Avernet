"""Shared helpers for task_experience per-method generators.

``_build_plan`` and ``_empty_plan`` translate the per-method intermediate task
dicts (kept close to the prototype shape to ease review) into the immutable
``Plan`` value type declared in :mod:`task_experience.types`. ``_safe_float``
defends against malformed KGBase property numbers; ``_safe_chain`` parses the
``collaboration_chain`` JSON string with a safe fallback to an empty list.
``_retrieve_for`` is the shared KGBase retrieve wrapper used by methods B and
C — it casts transport exceptions into an empty list so service-level callers
can treat "upstream disabled" and "no match" with the same code path.
``parse_json_steps`` strips fenced / trailing noise from a model chat reply
and returns the parsed JSON list, or ``None`` when unparseable.
"""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.task.task_experience.protocols import (
    PolicyRetrievalPort,
)
from agentclaw.community.core.task.task_experience.types import (
    KGBasePolicy,
    Plan,
    PlanTask,
)

__all__ = [
    "_build_plan",
    "_empty_plan",
    "_safe_float",
    "_safe_chain",
    "_retrieve_for",
    "parse_json_steps",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to ``float`` defensively; NaN / TypeError → default."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return f


def _safe_chain(raw: Any) -> list[str]:
    """Parse a KGBase ``collaboration_chain`` JSON string into a list[str].

    The property is stored as a JSON-serialised list of bot ids; malformed /
    missing values yield an empty list rather than raising.
    """
    if isinstance(raw, list):
        return [str(b) for b in raw]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, list):
        return [str(b) for b in parsed]
    return []


def _build_plan(
    method: str,
    task_dicts: list[dict[str, Any]],
    *,
    has_policy: bool = True,
    reason: str = "",
) -> Plan:
    """Build an immutable :class:`Plan` from intermediate task dicts.

    Mirrors the prototype's ``_build_plan`` summary (total duration, min gain)
    while consuming the KGBase-driven per-method task dicts.
    """
    tasks: list[PlanTask] = []
    valid_gains: list[float] = []
    for td in task_dicts:
        gain = _safe_float(td.get("gain", 0))
        duration = _safe_float(td.get("duration", 0))
        if gain > 0:
            valid_gains.append(gain)
        tasks.append(
            PlanTask(
                action=str(td.get("action", "")),
                bot_id=str(td.get("bot_id", "")),
                duration=duration,
                gain=gain,
                policy_type=td.get("policy_type"),
                policy_id=td.get("policy_id"),
            )
        )
    total_duration = round(sum(t.duration for t in tasks), 1) if tasks else 0.0
    gain_value = round(min(valid_gains), 3) if valid_gains else 0.0
    has_policy_support = has_policy and any(t.policy_id for t in tasks)
    return Plan(
        method=method,
        tasks=tuple(tasks),
        total_duration=total_duration,
        gain=gain_value,
        has_policy_support=has_policy_support,
        links={},
        reason=reason,
    )


def _empty_plan(method: str, *, reason: str = "") -> Plan:
    """Return an empty :class:`Plan` carrying a skip ``reason``.

    Used when KGBase retrieve returns no match (``method_a_no_experience``),
    the retrieve upstream is disabled (``kgbase_disabled``), the method is
    not yet implemented in this commit (``not_implemented_in_a4``), or the LLM
    upstream is disabled / returns unparseable JSON (``llm_disabled`` /
    ``invalid_model_output``).
    """
    return Plan(
        method=method,
        tasks=tuple(),
        total_duration=0.0,
        gain=0.0,
        has_policy_support=False,
        links={},
        reason=reason,
    )


async def _retrieve_for(
    policy: PolicyRetrievalPort,
    *,
    action_text: str,
    bot_id: str,
    experience_type: str,
) -> list[KGBasePolicy]:
    """Retrieve KGBase policies for one (action, bot_id, experience_type).

    Returns an empty list when the policy port is ``None`` or the upstream
    raises — methods B / C treat disabled upstream and "no match"
    identically and emit a fallback (no-policy) plan task.
    """
    if policy is None:
        return []
    try:
        return await policy.retrieve(
            action_text,
            bot_id=bot_id,
            entity_type="Policy",
            property_name="trigger",
            top_k=3,
            min_score=0.3,
            experience_type=experience_type,
            active_only=True,
        )
    except Exception:
        return []


def parse_json_steps(raw: str) -> list | None:
    """Parse a model chat-completion reply into a JSON list, defensively.

    Strips surrounding whitespace, leading English fenced blocks
    (` ``` ` or ` ```json `), and trailing commentary; returns the parsed
    list when ``json.loads`` succeeds and yields a ``list``, otherwise
    ``None``. Mirrors the prototype's `.strip('`').strip()` shape with the
    addition that any text-only trailing comments are not tolerated — the
    prompt's ``只输出JSON字符串数组/对象, 不要解释。`` line is expected to
    keep trailing text out of the reply.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        # Strip one fenced block prefix; tolerate ```json / ```lang variants
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        else:
            text = text.lstrip("`").lstrip()
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()
    else:
        text = text.strip("`").strip()
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed
