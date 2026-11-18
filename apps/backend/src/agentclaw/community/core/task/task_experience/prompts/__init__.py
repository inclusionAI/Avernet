"""Prompt builders for task_experience LLM-driven methods.

Only methods B (LLM decompose + KGBase success_pattern backfill) and D (LLM
self-plan) actually call the LLM; method A and C, plus the acceptance expander,
operate on retrieved policies directly and do not need prompt templates.

Every builder returns the user-side prompt string; the system prompt is small
and owned by each service to keep per-method tuning readable and tested
individually. The mandatory cross-cutting constraint tail
(:const:`NO_WEB_SEARCH_CONSTRAINT` + :const:`OUTPUT_LANGUAGE_CONSTRAINT`) is
appended via :func:`_shared.append_constraints`, so no builder re-declares
those constants.
"""

from __future__ import annotations

__all__: list[str] = []
