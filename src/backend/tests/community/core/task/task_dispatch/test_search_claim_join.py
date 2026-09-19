"""Tests for unrestricted task-dispatch candidate selection.

BBS claim eligibility is intentionally separate from task dispatch search.
"""
from __future__ import annotations

from agentclaw.community.core.task.task_dispatch.strategies import (
    _candidate_dispatch_ids,
    _offpath_normal,
    SearchOutcome,
)


def test_candidate_dispatch_ids_prefers_bot_uuid():
    candidates = [
        {"bot_id": "market", "owner_id": "owner-a", "bot_uuid": "market:owner-a"},
        {"bot_id": "market", "owner_id": "owner-b", "bot_uuid": "market:owner-b"},
    ]
    assert _candidate_dispatch_ids(candidates) == ["market:owner-a", "market:owner-b"]


def test_candidate_dispatch_ids_composes_legacy_identity_without_claim_roster():
    candidates = [
        {"bot_id": "market", "owner_id": "owner-a"},
        {"bot_id": "research"},
    ]
    assert _candidate_dispatch_ids(candidates) == ["market:owner-a", "research"]


def test_candidate_dispatch_ids_deduplicates_identity_in_candidate_order():
    candidates = [
        {"bot_id": "a", "owner_id": "1", "bot_uuid": "a:1"},
        {"bot_id": "a", "owner_id": "1", "bot_uuid": "a:1"},
        {"bot_id": "b", "owner_id": "2", "bot_uuid": "b:2"},
    ]
    assert _candidate_dispatch_ids(candidates) == ["a:1", "b:2"]


def test_offpath_empty_candidates_misses():
    result = _offpath_normal([])
    assert result.outcome == SearchOutcome.MISS
    assert result.miss_reason == "no_candidates"


def test_offpath_one_or_two_candidates_selects_single_without_claim_filter():
    result = _offpath_normal(["unregistered:1", "unregistered:2"])
    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "unregistered:1"
    assert result.owner_id == "1"


def test_offpath_three_or_more_candidates_forms_group_without_claim_filter():
    result = _offpath_normal(["a:1", "b:2", "c:3", "d:4"])
    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation.bot_ids == ["a:1", "b:2", "c:3"]
