"""Metadata remains display-only while accepting stable BCN references."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.bcn_metadata import resolve_metadata_pairs
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError


@pytest.mark.parametrize("locator", ["stable", "new-owner"])
def test_owner_or_entity_matches_return_current_owner_pair(locator):
    repo = Mock()
    repo.list_matches_by_legacy_locator.return_value = [
        {
            "id": 7,
            "bot_id": "bot",
            "owner_id": "new-owner",
            "entity_id": "stable",
        }
    ]
    assert resolve_metadata_pairs(repo, [("bot", locator)]) == [("bot", "new-owner")]


def test_converted_service_retains_original_registration_address():
    repo = Mock()
    repo.list_matches_by_legacy_locator.return_value = []
    repo.get_unique_by_id.return_value = {
        "id": 7,
        "bot_id": "bot",
        "owner_id": "new-owner",
        "entity_id": "staff_old",
        "bot_type": "service",
        "ext": {"bcn_provider_bot_ref": "bot:old"},
    }
    assert resolve_metadata_pairs(repo, [("bot", "old")]) == [("bot", "new-owner")]
    assert resolve_metadata_pairs(repo, [("bot", "unrelated")]) == []


def test_ambiguous_legacy_pair_is_not_silently_resolved():
    repo = Mock()
    repo.list_matches_by_legacy_locator.return_value = [{"id": 1}, {"id": 2}]
    with pytest.raises(BotLookupAmbiguousError):
        resolve_metadata_pairs(repo, [("default", "locator")])
    repo.get_unique_by_id.assert_not_called()


def test_missing_default_locator_keeps_empty_result_without_unique_fallback():
    repo = Mock()
    repo.list_matches_by_legacy_locator.return_value = []
    assert resolve_metadata_pairs(repo, [("default", "missing-person")]) == []
    repo.get_unique_by_id.assert_not_called()
