"""AcceptanceExpanderService — expand acceptance criteria from ``capability_boundary`` experiences.

Retrieves active ``capability_boundary`` policies for ``(question, bot_id)``
from :class:`PolicyRetrievalPort`, picks the highest-``gain`` match and splits
its ``boundary`` text into ``can_do`` / ``cannot_do`` parts using the
five prototype separators (``但无法`` / ``但不可`` / ``但不能`` / ``，无法`` /
``。无法``). The result is :class:`AcceptanceReport` — empty lists with a
``note`` (``kgbase_disabled`` / ``无匹配...``) when retrieval is empty or
disabled.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.task.task_experience.protocols import (
    AcceptanceExpanderServiceProtocol,
    BotTagsPort,
    PolicyRetrievalPort,
)
from agentclaw.community.core.task.task_experience.types import (
    AcceptanceReport,
    CapabilityBoundaryPolicy,
)

__all__ = ["AcceptanceExpanderService"]

#: Boundary sentence separators from prototype ``acceptance_expander.py``;
#: kept in priority order — the first one found in ``boundary`` wins.
_BOUNDARY_SEPARATORS = ("但无法", "但不可", "但不能", "，无法", "。无法")

_NO_MATCH_NOTE = "无匹配的 active capability_boundary 策略"


class AcceptanceExpanderService(AcceptanceExpanderServiceProtocol):
    """Default :class:`AcceptanceExpanderServiceProtocol` implementation."""

    @inject
    def __init__(
        self,
        policy: PolicyRetrievalPort,
        bot_tags: BotTagsPort,
    ) -> None:
        self._policy = policy
        self._bot_tags = bot_tags

    async def expand_acceptance_criteria(
        self,
        question: str,
        bot_id: str,
    ) -> AcceptanceReport:
        if self._policy is None:
            return self._empty_report(question, bot_id, note="kgbase_disabled")
        try:
            results = await self._policy.retrieve(
                question,
                bot_id=bot_id,
                entity_type="Policy",
                property_name="trigger",
                top_k=5,
                min_score=0.3,
                experience_type="capability_boundary",
                active_only=True,
            )
        except Exception:
            return self._empty_report(question, bot_id, note="kgbase_disabled")

        matched: list[CapabilityBoundaryPolicy] = []
        for item in results:
            props = dict(item.properties)
            try:
                gain = float(props.get("gain", 0) or 0)
            except (TypeError, ValueError):
                gain = 0.0
            try:
                trace_duration = float(props.get("trace_duration", 0) or 0)
            except (TypeError, ValueError):
                trace_duration = 0.0
            matched.append(
                CapabilityBoundaryPolicy(
                    policy_id=item.id,
                    trigger=str(props.get("trigger", "")),
                    boundary=str(props.get("boundary", "")),
                    gain=gain,
                    trace_duration=trace_duration,
                )
            )

        if not matched:
            return self._empty_report(question, bot_id, note=_NO_MATCH_NOTE)

        best = max(matched, key=lambda p: p.gain)
        return AcceptanceReport(
            question=question,
            bot_id=bot_id,
            can_do=(self._extract_can_do(best.boundary),),
            cannot_do=(self._extract_cannot_do(best.boundary),),
            estimated_duration=best.trace_duration,
            source_policies=tuple(matched),
            note="",
        )

    @staticmethod
    def _empty_report(
        question: str, bot_id: str, *, note: str,
    ) -> AcceptanceReport:
        return AcceptanceReport(
            question=question,
            bot_id=bot_id,
            can_do=(),
            cannot_do=(),
            estimated_duration=0.0,
            source_policies=(),
            note=note,
        )

    @staticmethod
    def _extract_can_do(boundary: str) -> str:
        """Return the can-do portion of ``boundary`` — text before the separator."""
        for sep in _BOUNDARY_SEPARATORS:
            if sep in boundary:
                return boundary[: boundary.index(sep)].strip()
        return boundary

    @staticmethod
    def _extract_cannot_do(boundary: str) -> str:
        """Return the cannot-do portion of ``boundary`` — text after the separator."""
        for sep in _BOUNDARY_SEPARATORS:
            if sep in boundary:
                return boundary[boundary.index(sep) + len(sep) :].strip()
        return ""
