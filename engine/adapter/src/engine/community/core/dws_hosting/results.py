"""Deterministic decision parsing; ambiguous model output is never sent."""

import json

from pydantic import ValidationError

from engine.community.kernel.dws_execution import ExecutionError, HostingDecision


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("invalid JSON constant")


def strict_json(raw: str):
    return json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )


def _decision_object(text: str):
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )
    value, end = decoder.raw_decode(text)
    if not isinstance(value, dict):
        raise ValueError("decision object required")
    first, position, count = text[:end], end, 1
    # COSEC: consume the entire text; never pick a convenient JSON substring.
    # Only byte-for-byte identical object text may repeat (separating whitespace allowed).
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            break
        if count >= 8:
            raise ValueError("too many repeated decisions")
        _, end = decoder.raw_decode(text, position)
        if text[position:end] != first:
            raise ValueError("conflicting decisions")
        count += 1
        position = end
    return value


def _fenced_decision_object(text: str):
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )
    position, count, first, value = 0, 0, None, None
    # COSEC: accept only complete, identical fenced objects; consume every byte.
    # A fence inside a JSON string is data, not a separator.
    while position < len(text):
        newline = text.find("\n", position)
        if newline < 0 or text[position:newline].strip().lower() not in (
            "```",
            "```json",
        ):
            raise ValueError("opening fence required")
        position = newline + 1
        while position < len(text) and text[position].isspace():
            position += 1
        current, end = decoder.raw_decode(text, position)
        raw = text[position:end]
        if not isinstance(current, dict) or (first is not None and raw != first):
            raise ValueError("conflicting fenced decisions")
        first, value = raw, current
        position = end
        while position < len(text) and text[position].isspace():
            position += 1
        if not text.startswith("```", position):
            raise ValueError("closing fence required")
        position += 3
        count += 1
        if count > 8:
            raise ValueError("too many fenced decisions")
        while position < len(text) and text[position].isspace():
            position += 1
    return value


def parse_result_object(result_json: str) -> dict:
    if len(result_json) > 262_144:
        raise ExecutionError("result_too_large")
    try:
        result = strict_json(result_json)
    except (ValueError, RecursionError):
        raise ExecutionError("invalid_result") from None
    if not isinstance(result, dict):
        raise ExecutionError("invalid_result")
    meta = result.get("meta", {})
    if not isinstance(meta, dict) or meta.get("aborted") is True or meta.get("error"):
        raise ExecutionError("execution_failed")
    payloads = result.get("payloads")
    if not isinstance(payloads, list) or not 1 <= len(payloads) <= 100:
        raise ExecutionError("missing_result")
    decisions = []
    for payload in payloads:
        if (
            not isinstance(payload, dict)
            or payload.get("isError")
            or payload.get("mediaUrl")
            or payload.get("mediaUrls")
            or not isinstance(payload.get("text"), str)
        ):
            raise ExecutionError("unsupported_result")
        text = payload["text"].strip()
        if not text:
            continue
        # Only whole code fences are removed; never fish a JSON substring out of prose.
        original = text
        lines = text.splitlines()
        if (
            len(lines) >= 3
            and lines[0].lower() in ("```", "```json")
            and lines[-1] == "```"
        ):
            text = "\n".join(lines[1:-1])
        try:
            try:
                value = _decision_object(text)
            except ValueError:
                value = _fenced_decision_object(original)
        except (ValueError, ValidationError, RecursionError):
            raise ExecutionError("invalid_decision") from None
        decisions.append(value)
    if len(decisions) != 1:
        raise ExecutionError("ambiguous_decision" if decisions else "missing_result")
    return decisions[0]


def parse_decision(result_json: str, rule_version: int) -> HostingDecision:
    value = parse_result_object(result_json)
    try:
        decision = HostingDecision.model_validate(value)
    except (ValueError, ValidationError):
        raise ExecutionError("invalid_decision") from None
    if decision.rule_version != rule_version:
        raise ExecutionError("rule_version_mismatch")
    return decision
