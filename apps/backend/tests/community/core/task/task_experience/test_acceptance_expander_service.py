"""AcceptanceExpanderService unit tests.

Cover the A6 acceptance points:
  - happy path: best (highest-gain) capability_boundary policy drives
    AcceptanceReport with can_do / cannot_do split;
  - boundary separator variants ("但无法", "但不可", "但不能", "，无法", "。无法");
  - disabled (policy=None) and exception-throwing retrieval both yield
    note="kgbase_disabled";
  - empty retrieval yields note="无匹配的 active capability_boundary 策略";
  - no urllib / ssl / kgbase_client imports.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
)
from agentclaw.community.core.task.task_experience.services.acceptance_expander_service import (
    AcceptanceExpanderService,
    _BOUNDARY_SEPARATORS,
)
from agentclaw.community.core.task.task_experience.types import (
    AcceptanceReport,
    KGBasePolicy,
)


class _IdentityBotTags(BotTagsPort):
    def bot_name(self, bot_id: str) -> str:
        return bot_id


class _StubPolicy(PolicyRetrievalPort):
    """Returns canned fixtures for the ``capability_boundary`` experience_type."""

    def __init__(
        self,
        *,
        fixtures: list[KGBasePolicy] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._fixtures = fixtures or []
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self,
        query: str,
        *,
        bot_id: str,
        entity_type: str = "Policy",
        property_name: str = "trigger",
        top_k: int = 5,
        min_score: float = 0.3,
        experience_type: str | None = None,
        active_only: bool = True,
    ) -> list[KGBasePolicy]:
        self.calls.append(
            {"query": query, "bot_id": bot_id,
             "experience_type": experience_type,
             "top_k": top_k, "min_score": min_score,
             "active_only": active_only},
        )
        if self._raises is not None:
            raise self._raises
        if experience_type != "capability_boundary":
            return []
        return list(self._fixtures)


def _cap_policy(
    *,
    pid: str,
    boundary: str,
    gain: float,
    trace_duration: float = 60.0,
) -> KGBasePolicy:
    return KGBasePolicy(
        id=pid,
        properties={
            "bot_id": "b1", "status": "active",
            "experience_type": "capability_boundary",
            "boundary": boundary,
            "gain": str(gain),
            "trace_duration": str(trace_duration),
        },
    )


# --- retrieve contract ----------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_invoked_with_capability_boundary_filter() -> None:
    policy = _StubPolicy(fixtures=[])
    service = AcceptanceExpanderService(policy=policy, bot_tags=_IdentityBotTags())

    await service.expand_acceptance_criteria(question="如何诊断 cron", bot_id="b1")

    assert len(policy.calls) == 1
    call = policy.calls[0]
    assert call["experience_type"] == "capability_boundary"
    assert call["top_k"] == 5
    assert call["min_score"] == 0.3
    assert call["active_only"] is True
    assert call["bot_id"] == "b1"


# --- disabled / empty ------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_when_policy_is_none() -> None:
    service = AcceptanceExpanderService(
        policy=None,  # type: ignore[arg-type]
        bot_tags=_IdentityBotTags(),
    )
    report = await service.expand_acceptance_criteria(question="q", bot_id="b1")
    assert report.note == "kgbase_disabled"
    assert report.can_do == ()
    assert report.cannot_do == ()
    assert report.estimated_duration == 0.0
    assert report.source_policies == ()


@pytest.mark.asyncio
async def test_disabled_when_retrieve_raises() -> None:
    policy = _StubPolicy(raises=RuntimeError("kgbase down"))
    service = AcceptanceExpanderService(policy=policy, bot_tags=_IdentityBotTags())

    report = await service.expand_acceptance_criteria(question="q", bot_id="b1")

    assert report.note == "kgbase_disabled"
    assert report.can_do == ()


@pytest.mark.asyncio
async def test_empty_retrieval_note_no_match() -> None:
    policy = _StubPolicy(fixtures=[])
    service = AcceptanceExpanderService(policy=policy, bot_tags=_IdentityBotTags())

    report = await service.expand_acceptance_criteria(question="q", bot_id="b1")

    assert report.note == "无匹配的 active capability_boundary 策略"
    assert report.can_do == ()
    assert report.cannot_do == ()
    assert report.estimated_duration == 0.0


# --- happy path / best gain ------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_splits_boundary_and_picks_highest_gain() -> None:
    low = _cap_policy(
        pid="cap-low", boundary="能够查日志 但无法编辑配置", gain=2.0, trace_duration=20.0,
    )
    high = _cap_policy(
        pid="cap-high", boundary="能够执行诊断 但不可改动线上参数", gain=5.0, trace_duration=30.0,
    )
    policy = _StubPolicy(fixtures=[low, high])
    service = AcceptanceExpanderService(policy=policy, bot_tags=_IdentityBotTags())

    report: AcceptanceReport = await service.expand_acceptance_criteria(
        question="诊断 cron", bot_id="b1",
    )

    assert report.note == ""
    # best is high-gain policy
    assert len(report.source_policies) == 2
    assert {p.policy_id for p in report.source_policies} == {"cap-low", "cap-high"}
    assert report.estimated_duration == 30.0
    assert report.can_do == ("能够执行诊断",)
    assert report.cannot_do == ("改动线上参数",)


# --- boundary separator variants ------------------------------------------

@pytest.mark.parametrize("sep", list(_BOUNDARY_SEPARATORS))
@pytest.mark.asyncio
async def test_boundary_separator_variants(sep: str) -> None:
    """All five prototype separators split boundary into can_do/cannot_do."""
    boundary = f"CAN-DO-TEXT{sep}CANNOT-DO-TEXT"
    policy = _StubPolicy(
        fixtures=[_cap_policy(pid="cap-1", boundary=boundary, gain=1.0)],
    )
    service = AcceptanceExpanderService(policy=policy, bot_tags=_IdentityBotTags())

    report = await service.expand_acceptance_criteria(question="q", bot_id="b1")

    assert report.can_do == ("CAN-DO-TEXT",)
    assert report.cannot_do == ("CANNOT-DO-TEXT",)


@pytest.mark.asyncio
async def test_boundary_without_separator_can_do_is_full_text_cannot_do_empty() -> None:
    """When no separator is present, ``can_do`` is the full boundary text and
    ``cannot_do`` is empty — matches prototype's ``_extract_*`` fallback."""
    policy = _StubPolicy(
        fixtures=[_cap_policy(
            pid="cap-1", boundary="没有约束句的纯描述", gain=1.0,
        )],
    )
    service = AcceptanceExpanderService(policy=policy, bot_tags=_IdentityBotTags())

    report = await service.expand_acceptance_criteria(question="q", bot_id="b1")

    assert report.can_do == ("没有约束句的纯描述",)
    # Prototype's _extract_cannot_do returns "" when no separator is present;
    # the service wraps that single-element return into a 1-tuple, matching
    # prototype's "cannot_do": [""] shape.
    assert report.cannot_do == ("",)


# --- service forbidden imports guard --------------------------------------

def test_service_does_not_import_forbidden_transport() -> None:
    import agentclaw.community.core.task.task_experience.services.acceptance_expander_service as svc  # noqa: F401
    src = open(svc.__file__).read()
    assert "import urllib" not in src
    assert "import ssl" not in src
    assert "import requests" not in src
    assert "import httpx" not in src
