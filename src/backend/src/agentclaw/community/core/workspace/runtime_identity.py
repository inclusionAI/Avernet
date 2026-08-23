"""Pure runtime-implementation identity rules shared by Backend domains."""

from __future__ import annotations


def claude_code_uses_aicoding_runtime(
    *,
    active_engine: str | None,
    template_type: str | None,
) -> bool:
    """Whether a logical Claude Code Bot runs the AICoding implementation."""

    normalized_engine = (active_engine or "").strip().lower().replace("-", "_")
    normalized_template = (template_type or "").strip().lower()
    return (
        normalized_engine == "claude_code"
        and bool(normalized_template)
        and normalized_template != "normalcc"
    )


__all__ = ["claude_code_uses_aicoding_runtime"]
