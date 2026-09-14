from __future__ import annotations

from typing import Any

from ..models import SessionRow
from .keyless_session_prompt import OUTPUT_SCHEMA_VERSION
from .native_session_analysis import ALLOWED_ROOTS


def validate_agent_session_result(row: SessionRow, result: Any) -> dict[str, Any]:
    """Validate the minimum contract needed to safely consume an agent result."""

    errors: list[str] = []
    if not isinstance(result, dict):
        return {"valid": False, "errors": ["response must be a JSON object"]}
    required = {"schema_version", "session_id", "is_evaluable", "intent_match"}
    missing = sorted(required - set(result))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    if result.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {OUTPUT_SCHEMA_VERSION}")
    if str(result.get("session_id") or "") != str(row.session_id or ""):
        errors.append("session_id does not match input")
    if not isinstance(result.get("is_evaluable"), bool):
        errors.append("is_evaluable must be boolean")
    intent_match = result.get("intent_match")
    if not isinstance(intent_match, dict) or not isinstance(intent_match.get("is_match"), bool):
        errors.append("intent_match.is_match must be boolean")
    if result.get("is_evaluable") is True:
        if result.get("case_type") not in {"bad", "good"}:
            errors.append("case_type must be bad or good when is_evaluable=true")
        root = str(result.get("root_cause_class") or "").upper()
        if root not in ALLOWED_ROOTS:
            errors.append("root_cause_class is not in the allowed taxonomy")
        if not str(result.get("query") or "").strip():
            errors.append("query is required when is_evaluable=true")
        if not isinstance(result.get("evidence"), list) or not result.get("evidence"):
            errors.append("evidence is required when is_evaluable=true")
    elif not str(result.get("reject_reason") or "").strip():
        errors.append("reject_reason is required when is_evaluable=false")
    return {
        "valid": not errors,
        "errors": errors,
        "top_level_keys": sorted(str(key) for key in result),
    }
