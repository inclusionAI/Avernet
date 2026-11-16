"""Metadata remains display-only while accepting stable BCN references."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.bcn_metadata import resolve_metadata_pairs
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError


@pytest.mark.parametrize("locator", ["stable", "new-owner"])
def test_owner_or_entity_matches_return_current_owner_pair(locator):
    repo = Mock()
    repo.list_metadata_locator_candidates.return_value = {("bot", locator): [
        {
            "id": 7,
            "bot_id": "bot",
            "owner_id": "new-owner",
            "entity_id": "stable",
            "_metadata_locator_match": True,
        }
    ]}
    assert resolve_metadata_pairs(repo, [("bot", locator)]) == [("bot", "new-owner")]
    repo.list_metadata_locator_candidates.assert_called_once_with([("bot", locator)])
    repo.list_matches_by_legacy_locator.assert_not_called()


def test_converted_service_retains_original_registration_address():
    repo = Mock()
    row = {
        "id": 7,
        "bot_id": "bot",
        "owner_id": "new-owner",
        "entity_id": "staff_old",
        "bot_type": "service",
        "ext": {"bcn_provider_bot_ref": "bot:old"},
        "_metadata_locator_match": False,
    }
    repo.list_metadata_locator_candidates.return_value = {
        ("bot", "old"): [row], ("bot", "unrelated"): [row],
    }
    assert resolve_metadata_pairs(repo, [("bot", "old")]) == [("bot", "new-owner")]
    assert resolve_metadata_pairs(repo, [("bot", "unrelated")]) == []


def test_ambiguous_legacy_pair_is_not_silently_resolved():
    repo = Mock()
    repo.list_metadata_locator_candidates.return_value = {("default", "locator"): [
        {"id": 1, "_metadata_locator_match": True},
        {"id": 2, "_metadata_locator_match": True},
    ]}
    with pytest.raises(BotLookupAmbiguousError):
        resolve_metadata_pairs(repo, [("default", "locator")])
    repo.get_unique_by_id.assert_not_called()


def test_missing_default_locator_keeps_empty_result_without_unique_fallback():
    repo = Mock()
    repo.list_metadata_locator_candidates.return_value = {}
    assert resolve_metadata_pairs(repo, [("default", "missing-person")]) == []
    repo.get_unique_by_id.assert_not_called()


def test_library_batches_over_http_limit_are_chunked_without_losing_results():
    repo = Mock()
    pairs = [(f"bot-{index}", "owner") for index in range(201)]
    repo.list_metadata_locator_candidates.side_effect = lambda batch: {
        pair: [{"bot_id": pair[0], "owner_id": pair[1], "_metadata_locator_match": True}]
        for pair in batch
    }
    assert resolve_metadata_pairs(repo, pairs + pairs) == pairs
    assert [len(call.args[0]) for call in repo.list_metadata_locator_candidates.call_args_list] == [100, 100, 1]


def test_database_failure_propagates_instead_of_becoming_empty_metadata():
    repo = Mock()
    repo.list_metadata_locator_candidates.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        resolve_metadata_pairs(repo, [("bot", "owner")])
