"""Conformance suite for ``AcceptanceExpanderServiceProtocol``.

Documented invariants:
  - ``expand_acceptance_criteria`` always returns an
    :class:`AcceptanceReport`, never raises on disabled / missing KGBase.
  - the ``question`` / ``bot_id`` echoed return match the call args.
  - ``can_do`` / ``cannot_do`` / ``source_policies`` are tuples; ``note`` is
    a ``str`` (including ``""`` when retrieval matched something).
"""

from __future__ import annotations

from typing import Callable

from agentclaw.community.core.task.task_experience.protocols import (
    AcceptanceExpanderServiceProtocol,
)

__all__ = [
    "assert_acceptance_expander_service_protocol_invariants",
    "AcceptanceServiceFactory",
]

AcceptanceServiceFactory = Callable[[], AcceptanceExpanderServiceProtocol]


async def assert_acceptance_expander_service_protocol_invariants(
    make_service: AcceptanceServiceFactory,
) -> None:
    service = make_service()
    report = await service.expand_acceptance_criteria(question="q", bot_id="b1")

    assert report.question == "q"
    assert report.bot_id == "b1"
    assert isinstance(report.can_do, tuple)
    assert isinstance(report.cannot_do, tuple)
    assert isinstance(report.source_policies, tuple)
    assert isinstance(report.note, str)
    assert isinstance(report.estimated_duration, float)

    # Repeated calls remain stable.
    report2 = await service.expand_acceptance_criteria(question="q2", bot_id="b2")
    assert report2.question == "q2"
    assert report2.bot_id == "b2"
