"""Pure mapping and filtering helpers for bot chat traces.

This module keeps provider payload shaping separate from the service
orchestration while preserving the service module's existing helper imports.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_chat.schemas import (
    ConversationObservation,
    ConversationSession,
    SessionMetadata,
)


def _extract_user_input(trace_input: Any) -> str | None:
    """Extract the last user message from trace input."""
    if trace_input is None:
        return None
    if isinstance(trace_input, str):
        return trace_input
    if isinstance(trace_input, list):
        for msg in reversed(trace_input):
            if (
                isinstance(msg, dict)
                and msg.get("role") == "user"
            ):
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    for part in content:
                        if (
                            isinstance(part, dict)
                            and part.get("type") == "text"
                        ):
                            return part.get("text", "")
                    first_text = next(
                        (
                            p
                            for p in content
                            if isinstance(p, str)
                        ),
                        None,
                    )
                    if first_text:
                        return first_text
        first = trace_input[0] if trace_input else None
        if isinstance(first, dict):
            return first.get("content", str(first))
        return str(first) if first else None
    if isinstance(trace_input, dict):
        return trace_input.get("content", str(trace_input))
    return str(trace_input)


def _trace_owner_id(trace: dict[str, Any], attributes: dict[str, Any]) -> Any:
    return trace.get("userId") or attributes.get("identity.owner_id") or attributes.get("user.id")


def _map_trace_to_session(trace: dict[str, Any]) -> ConversationSession:
    """Map a Langfuse trace dict to a ConversationSession."""
    metadata_raw = trace.get("metadata") or {}
    attributes = (metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
    usage = trace.get("usage") or {}

    return ConversationSession(
        id=trace.get("id", ""),
        biz_task_id=(
            trace.get("biz_task_id")
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id")
        ),
        biz_scene=(
            trace.get("biz_scene")
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene")
        ),
        session_id=attributes.get("gen_ai.session.id"),
        session_key=(
            attributes.get("session_id")
            or attributes.get("gen_ai.conversation.id")
        ),
        bot_id=attributes.get("identity.bot_id"),
        name=trace.get("name") or "未命名会话",
        input=_extract_user_input(trace.get("input")),
        output_preview=(
            str(trace.get("output"))[:500]
            if trace.get("output") is not None
            else None
        ),
        search_output=(
            str(trace.get("output"))
            if trace.get("output") is not None
            else None
        ),
        status="FAILED" if trace.get("success") is False else "SUCCESS",
        timestamp=trace.get("timestamp", ""),
        user_id=_trace_owner_id(trace, attributes),
        metadata=SessionMetadata(attributes=attributes),
        total_cost=float(trace.get("totalCost") or 0),
        latency_ms=float(trace.get("latency") or 0),
        total_tokens=int(usage.get("totalTokens") or 0),
    )


def _map_observation(obs: dict[str, Any]) -> ConversationObservation:
    """Map a Langfuse observation dict to a ConversationObservation."""
    metadata_raw = obs.get("metadata") or {}
    attributes = (metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
    usage = obs.get("usage") or {}

    return ConversationObservation(
        id=obs.get("id", ""),
        biz_task_id=(
            obs.get("biz_task_id")
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id")
        ),
        biz_scene=(
            obs.get("biz_scene")
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene")
        ),
        name=obs.get("name", ""),
        type=obs.get("type", "SPAN"),
        latency_ms=float(obs.get("latency") or 0),
        total_cost=float(obs.get("totalCost") or 0),
        total_tokens=int(usage.get("totalTokens") or 0),
        input=obs.get("input"),
        output=obs.get("output"),
        metadata=metadata_raw if isinstance(metadata_raw, dict) else None,
        model_name=attributes.get("gen_ai.request.model"),
        parent_observation_id=obs.get("parentObservationId"),
        children=[],
    )


def _build_observation_tree(observations: list[dict[str, Any]]) -> list[ConversationObservation]:
    """Build a nested observation tree from a flat list using parentObservationId."""
    mapped = [
        _map_observation(obs)
        for obs in observations
    ]
    by_id: dict[str, ConversationObservation] = {obs.id: obs for obs in mapped}
    roots: list[ConversationObservation] = []

    for obs in mapped:
        parent_id = obs.parent_observation_id
        if (
            parent_id
            and parent_id in by_id
        ):
            by_id[parent_id].children.append(obs)
        else:
            roots.append(obs)

    return roots


def _matches_session_key(attributes: dict[str, Any], session_key: str) -> bool:
    """Check if attributes match session_key in either legacy or GenAI field."""
    return (
        attributes.get("session_id") == session_key
        or attributes.get("gen_ai.conversation.id") == session_key
    )


def _apply_client_side_filters(
    sessions: list[ConversationSession],
    bot_id: str | None,
    trace_id: str | None,
    session_id: str | None,
    session_key: str | None,
    query: str | None = None,
    biz_scene: str | None = None,
    biz_task_id: str | None = None,
    match_mode: str = "exact",
    include_output_match: bool = False,
) -> list[ConversationSession]:
    """Apply client-side filters that Langfuse API doesn't support natively."""
    result = sessions
    def matches(actual: str | None, expected: str) -> bool:
        if actual is None:
            return False
        return expected in actual if match_mode == "contains" else actual == expected

    if trace_id:
        result = [
            s
            for s in result
            if matches(s.id, trace_id)
        ]
    if bot_id:
        result = [
            s
            for s in result
            if (
                s.metadata
                and s.metadata.attributes.get("identity.bot_id") == bot_id
            )
        ]
    if session_key:
        result = [
            s
            for s in result
            if (
                s.metadata
                and (
                    (
                        session_key
                        in str(s.metadata.attributes.get("session_id") or "")
                        or session_key
                        in str(s.metadata.attributes.get("gen_ai.conversation.id") or "")
                    )
                    if match_mode == "contains"
                    else _matches_session_key(s.metadata.attributes, session_key)
                )
            )
        ]
    if session_id:
        result = [
            s
            for s in result
            if (
                s.metadata
                and matches(
                    s.metadata.attributes.get("gen_ai.session.id"), session_id
                )
            )
        ]
    if biz_scene:
        result = [s for s in result if matches(s.biz_scene, biz_scene)]
    if biz_task_id:
        result = [s for s in result if matches(s.biz_task_id, biz_task_id)]
    if query:
        q = query.lower()
        result = [
            s
            for s in result
            if (
                (
                    s.name is not None
                    and q in s.name.lower()
                )
                or (
                    s.input is not None
                    and q in s.input.lower()
                )
                or (
                    include_output_match
                    and s.search_output is not None
                    and q in s.search_output.lower()
                )
            )
        ]
    return result
