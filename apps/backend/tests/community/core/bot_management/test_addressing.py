from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import (
    BotAddressConflictError,
    BotAddressNotFoundError,
    BotLookupAmbiguousError,
)


def _bot(*, pk: int = 7, entity: str = "entity-a", owner: str = "owner-a") -> dict:
    return {
        "id": pk,
        "bot_id": "service-bot",
        "entity_id": entity,
        "owner_id": owner,
        "env": "prod",
        "bot_type": "service",
    }


def test_entity_address_has_priority_and_returns_stable_resource_locator():
    repository = Mock()
    repository.get_by_id_and_entity.return_value = _bot(owner="owner-b")

    resolved = BotAddressResolver(repository).resolve(
        bot_id="service-bot",
        entity_id="entity-a",
        legacy_owner_id="owner-b",
    )

    assert resolved.bot_pk == 7
    assert resolved.entity_id == "entity-a"
    assert resolved.current_owner_id == "owner-b"
    assert resolved.resource_owner_id == "entity-a"
    repository.list_matches_by_legacy_locator.assert_not_called()


def test_entity_and_unrelated_owner_are_rejected():
    repository = Mock()
    repository.get_by_id_and_entity.return_value = _bot(owner="owner-b")

    with pytest.raises(BotAddressConflictError):
        BotAddressResolver(repository).resolve(
            bot_id="service-bot",
            entity_id="entity-a",
            legacy_owner_id="somebody-else",
        )


def test_legacy_locator_accepts_current_owner_after_handoff():
    repository = Mock()
    repository.list_matches_by_legacy_locator.return_value = [
        _bot(entity="entity-a", owner="owner-b")
    ]

    resolved = BotAddressResolver(repository).resolve(
        bot_id="service-bot", legacy_owner_id="owner-b"
    )

    assert resolved.current_owner_id == "owner-b"
    assert resolved.resource_owner_id == "entity-a"


def test_legacy_locator_fails_closed_on_ambiguity():
    repository = Mock()
    repository.list_matches_by_legacy_locator.return_value = [
        _bot(pk=1, entity="owner-a", owner="owner-b"),
        _bot(pk=2, entity="entity-c", owner="owner-a"),
    ]

    with pytest.raises(BotLookupAmbiguousError):
        BotAddressResolver(repository).resolve(
            bot_id="service-bot", legacy_owner_id="owner-a"
        )


def test_missing_locator_is_not_implicitly_replaced_by_actor_identity():
    with pytest.raises(BotAddressNotFoundError):
        BotAddressResolver(Mock()).resolve(bot_id="service-bot")

