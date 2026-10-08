"""Pure runtime-implementation identity rules shared by Backend domains."""

from __future__ import annotations

from typing import Any, Mapping

#: Server-managed form marker. Creation normalization writes it into the
#: template snapshot (``ac_templates.ext``) for template-backed bots, or the
#: bot record's ``ext`` column for plain bots, when a legacy ``aicoding``
#: engine value is folded into ``claude_code``. It marks the product form the
#: bot runs; it is never accepted from public create input (server-managed).
ENGINE_FORM_KEY = "engine_form"
AICODING_ENGINE_FORM = "aicoding"


def normalize_runtime_engine(active_engine: str | None) -> str:
    """Normalize an engine spelling to the registry key form."""
    return (active_engine or "").strip().lower().replace("-", "_")


def engine_form_of(*sources: Mapping[str, Any] | None) -> str | None:
    """First server-managed ``engine_form`` found across template/bot sources.

    Sources are probed in order (template snapshot first, bot ``ext`` second);
    a non-mapping or blank value is skipped, matching how ``bot`` records
    degrade when the template attach is missing.
    """
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        value = source.get(ENGINE_FORM_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def uses_aicoding_runtime(
    *,
    active_engine: str | None,
    template_type: str | None,
    template_config: Mapping[str, Any] | None = None,
) -> bool:
    """Whether a Bot runs the AICoding runtime implementation.

    Judgment order (engine/form vocabulary split, see
    ``docs/superpowers/specs/2026-08-31-engine-vocabulary-template-form-design.md``):

    1. Legacy short-circuit — a stored ``aicoding`` ``active_engine`` (rows
       created before the vocabulary split) always runs the AICoding
       implementation; read paths never rewrite stored engines.
    2. Form marker — a ``claude_code`` bot whose template snapshot carries
       the server-managed ``engine_form: aicoding`` marker (where creation
       normalization records the form; a plain no-template bot has no form).
    3. Historical semantics — ``claude_code`` plus a non-empty,
       non-``normalCC`` template type.
    """
    normalized_engine = normalize_runtime_engine(active_engine)
    if normalized_engine == "aicoding":
        return True
    if normalized_engine != "claude_code":
        return False
    if engine_form_of(template_config) == AICODING_ENGINE_FORM:
        return True
    normalized_template = (template_type or "").strip().lower()
    return bool(normalized_template) and normalized_template != "normalcc"


def claude_code_uses_aicoding_runtime(
    *,
    active_engine: str | None,
    template_type: str | None,
) -> bool:
    """Whether a logical Claude Code Bot runs the AICoding implementation."""

    normalized_engine = normalize_runtime_engine(active_engine)
    normalized_template = (template_type or "").strip().lower()
    return (
        normalized_engine == "claude_code"
        and bool(normalized_template)
        and normalized_template != "normalcc"
    )


__all__ = [
    "AICODING_ENGINE_FORM",
    "ENGINE_FORM_KEY",
    "claude_code_uses_aicoding_runtime",
    "engine_form_of",
    "normalize_runtime_engine",
    "uses_aicoding_runtime",
]
