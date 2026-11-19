"""Minimal JSON Schema validation for the registration record (§2.2, §3.3).

The backend already depends on ``jsonschema``; we use it when present and fall back
to a small structural check otherwise, so this module never hard-fails on import.
"""

from __future__ import annotations

from typing import Any


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Validate ``instance`` against ``schema``; return human-readable problems."""

    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a declared dependency
        return _fallback(instance, schema)

    validator = jsonschema.Draft202012Validator(schema)
    problems = []
    for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        problems.append(f"{location}: {error.message}")
    return problems


def _fallback(instance: Any, schema: dict[str, Any]) -> list[str]:
    expected = schema.get("type")
    if expected == "object" and not isinstance(instance, dict):
        return ["<root>: expected an object"]
    required = schema.get("required") or []
    missing = [key for key in required if not isinstance(instance, dict) or key not in instance]
    return [f"<root>: missing required property {key!r}" for key in missing]
