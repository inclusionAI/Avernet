"""Address resolver and ``BotService.get_bot_by_address`` contract."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import (
    BotAddressConflictError,
    BotAddressNotFoundError,
    BotLookupAmbiguousError,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
)


def _bot(*, pk: int = 7, entity: str = "entity-a", owner: str = "owner-a") -> dict:
    return {
        "id": pk,
        "bot_id": "service-bot",
        "entity_id": entity,
        "owner_id": owner,
    }


def _make_service(repository) -> BotService:
    """A service with only the repository seam this contract touches."""
    service = BotService.__new__(BotService)
    service._repository = repository
    return service


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
    repository.get_unique_by_id.assert_not_called()


@pytest.mark.parametrize("locator", ["previous-owner", "entity-a", "unrelated-legacy-value"])
def test_publish_source_uses_pk_instead_of_reinterpreting_owner(locator):
    repository = Mock()
    repository.get_by_pk.return_value = _bot(owner="new-owner")

    resolved = BotAddressResolver(repository).resolve_publish_source(
        source_bot_pk=7, source_bot_id="service-bot", legacy_owner_id=locator,
    )

    assert resolved.bot_pk == 7
    assert resolved.entity_id == "entity-a"
    assert resolved.current_owner_id == "new-owner"
    repository.get_by_pk.assert_called_once_with(7)
    repository.list_matches_by_legacy_locator.assert_not_called()


def test_publish_source_rejects_mismatched_pk_without_trying_owner():
    repository = Mock()
    repository.get_by_pk.return_value = _bot()

    with pytest.raises(BotAddressConflictError):
        BotAddressResolver(repository).resolve_publish_source(
            source_bot_pk=7, source_bot_id="different-bot", legacy_owner_id="owner-a",
        )

    repository.list_matches_by_legacy_locator.assert_not_called()


def test_missing_service_publish_source_does_not_rebind_to_a_replacement():
    repository = Mock()
    repository.get_by_pk.return_value = None

    with pytest.raises(BotAddressNotFoundError):
        BotAddressResolver(repository).resolve_publish_source(
            source_bot_pk=7, source_bot_id="service-bot", legacy_owner_id="owner-a",
        )

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


def test_unique_address_without_locator_returns_the_single_row():
    """A unique-id lookup gives Cron a live Bot without per-owner metadata."""
    repository = Mock()
    bot = _bot(pk=11)
    repository.get_unique_by_id.return_value = bot

    resolved = BotAddressResolver(repository).resolve(
        bot_id="service-bot", allow_unique_bot_id=True
    )

    assert resolved.bot_pk == 11
    assert resolved.current_owner_id == "owner-a"
    repository.get_unique_by_id.assert_called_once_with("service-bot")


def test_unique_address_without_locator_fails_closed_on_ambiguity():
    """The no-locator resolver cannot answer an ambiguous service bot_id."""
    repository = Mock()
    repository.get_unique_by_id.side_effect = BotLookupAmbiguousError

    with pytest.raises(BotLookupAmbiguousError):
        BotAddressResolver(repository).resolve(
            bot_id="service-bot", allow_unique_bot_id=True
        )


def test_unique_address_without_locator_reports_missing_address():
    repository = Mock()
    repository.get_unique_by_id.return_value = None

    with pytest.raises(BotAddressNotFoundError):
        BotAddressResolver(repository).resolve(
            bot_id="missing-bot", allow_unique_bot_id=True
        )


def test_service_unique_address_uses_the_current_owner_read():
    """The service method finalizes a unique address with the current Owner."""
    repository = Mock()
    bot = _bot(pk=11, owner="owner-b")
    repository.get_unique_by_id.return_value = bot
    repository.get_by_id_and_owner.return_value = bot
    service = _make_service(repository=repository)

    result = service.get_bot_by_address(
        bot_id="service-bot",
        allow_unique_bot_id=True,
    )

    assert result == bot
    repository.get_unique_by_id.assert_called_once_with("service-bot")
    repository.get_by_id_and_owner.assert_called_once_with("service-bot", "owner-b")


def test_service_unique_address_fails_closed_on_duplicate_bot_id():
    """The service method does not select an arbitrary duplicate row."""
    repository = Mock()
    repository.get_unique_by_id.side_effect = BotLookupAmbiguousError
    service = _make_service(repository=repository)

    with pytest.raises(BotLookupAmbiguousError):
        service.get_bot_by_address(bot_id="service-bot", allow_unique_bot_id=True)


def test_service_source_address_requires_legacy_locator():
    """A source-only address cannot implicitly borrow the current-Owner lookup."""
    service = _make_service(repository=Mock())

    with pytest.raises(BotNotFoundError):
        service.get_bot_by_address("service-bot", None, source_bot_pk=7)
