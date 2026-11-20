"""Value types and sentinel constants for the task_experience module.

All results returned by task_experience services are immutable dataclass
projections defined here. KGBase row shapes that originate from the corporate
``PolicyRetrievalPort`` implementation use the lightweight :class:`KGBasePolicy`
projection and never leak KG schema types into community core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Sentinel strings — used to mark disabled upstreams without raising.
# Mirrors forum/shared/llm.py's BBS_LLM_DISABLED contract: business code treats
# the sentinel as "upstream unavailable", records a ``reason``, and skips —
# never propagates as an exception to the caller.
# ---------------------------------------------------------------------------
TASK_EXPERIENCE_LLM_DISABLED = "[task_experience llm disabled]"
TASK_EXPERIENCE_KGBASE_DISABLED = "[task_experience kgbase disabled]"


class PlanMethod(str, Enum):
    """The four task plan generation strategies shipped by task_experience."""

    A = "A"  # multibot_workflow direct reuse from KGBase experience
    B = "B"  # LLM decompose + KGBase success_pattern backfill
    C = "C"  # incremental rewrite + full-policy retrieval
    D = "D"  # LLM self-plan (no policy)


@dataclass(frozen=True)
class PlanTask:
    """A single executable step inside a plan — field set is the public
    contract used by both prototype generators and downstream consumers."""

    action: str
    bot_id: str
    duration: float
    gain: float
    policy_type: str | None = None
    policy_id: str | None = None


@dataclass(frozen=True)
class Plan:
    """One ranked candidate plan. ``method`` mirrors ``PlanMethod`` value str.

    ``reason`` (empty when the plan produced real tasks) carries a
    disabled/parse-fail reason when the method returned an empty plan —
    e.g. ``"llm_disabled"``, ``"invalid_model_output"``,
    ``"kgbase_disabled"``, ``"method_a_no_experience"``. Service-level tests
    assert on this field; callers must not treat ``reason != ""`` as an error.
    """

    method: str
    tasks: tuple[PlanTask, ...]
    total_duration: float
    gain: float
    has_policy_support: bool
    links: dict[str, str] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class PlanReport:
    """Aggregate result for ``generate_task_plans``: a fixed-ordered list of
    four method plans plus the recommended ``PlanMethod``."""

    question: str
    bot_id: str
    plans: tuple[Plan, ...]
    recommendation: str  # PlanMethod.value str — kept str for JSON stability


# ---------------------------------------------------------------------------
# Acceptance-expansion types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CapabilityBoundaryPolicy:
    """Projection of a KGBase ``Policy`` row with
    ``experience_type=capability_boundary``. ``boundary`` is the free-text
    "can_do but cannot_do" sentence parsed by ``_extract_can_do`` /
    ``_extract_cannot_do`` helpers in the service."""

    policy_id: str
    trigger: str
    boundary: str
    gain: float
    trace_duration: float


@dataclass(frozen=True)
class AcceptanceReport:
    """Aggregate result for ``expand_acceptance_criteria``. ``note`` is set
    when retrieval returned nothing or the upstream was disabled — it is the
    structured equivalent of ``Plan.reason``."""

    question: str
    bot_id: str
    can_do: tuple[str, ...]
    cannot_do: tuple[str, ...]
    estimated_duration: float
    source_policies: tuple[CapabilityBoundaryPolicy, ...]
    note: str = ""


# ---------------------------------------------------------------------------
# KGBase retrieval projection — properties pass through as a dict so that
# community core never has to encode the KGBase ``Policy`` schema (which is
# owned by the corp plugin implementation and could evolve).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KGBasePolicy:
    """Minimal projection returned by ``PolicyRetrievalPort.retrieve``.

    Corporate implementations construct this from raw KGBase rows; community
    services read the well-known keys (``bot_id``, ``experience_type``,
    ``trigger`` / ``success_pattern`` / ``boundary`` / ``gain`` /
    ``trace_duration``, ``status``) defensively — missing keys fall back to
    safe defaults rather than raising.
    """

    id: str
    properties: dict[str, Any]


__all__ = [
    "TASK_EXPERIENCE_LLM_DISABLED",
    "TASK_EXPERIENCE_KGBASE_DISABLED",
    "PlanMethod",
    "PlanTask",
    "Plan",
    "PlanReport",
    "CapabilityBoundaryPolicy",
    "AcceptanceReport",
    "KGBasePolicy",
]
