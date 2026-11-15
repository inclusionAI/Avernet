"""Pin the runtime lookup's query budget and legacy address semantics."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.core.engine_runtime.bot_lookup import RuntimeBotLookup
from agentclaw.community.core.repository.protocols.bot import BotRepository


def row(**overrides):
    return {
        "id": 42, "bot_id": "default", "entity_id": "stable-entity",
        "owner_id": "current-owner", "active_engine": "openclaw",
        "bot_type": "service", "binding_id": 123, **overrides,
    }


@pytest.mark.parametrize("locator", ["stable-entity", "current-owner"])
def test_address_lookup_reads_only_the_resolved_row(locator):
    repository = Mock(spec=BotRepository)
    repository.list_matches_by_legacy_locator.return_value = [row()]
    lookup = RuntimeBotLookup(repository)

    result = lookup.get_bot_by_address("default", locator)

    assert result == row()
    assert "device_binding" not in result
    assert "template_config" not in result
    assert repository.mock_calls == [
        ("list_matches_by_legacy_locator", ("default", locator), {}),
    ]
    # Caller mutation must not mutate the persistence projection.
    result["owner_id"] = "changed"
    assert repository.list_matches_by_legacy_locator.return_value[0]["owner_id"] == "current-owner"


def test_missing_and_ambiguous_addresses_still_refuse():
    repository = Mock(spec=BotRepository)
    lookup = RuntimeBotLookup(repository)
    repository.list_matches_by_legacy_locator.return_value = []
    with pytest.raises(BotNotFoundError):
        lookup.get_bot_by_address("default", "other-owner")
    repository.list_matches_by_legacy_locator.return_value = [row(), row(id=43)]
    with pytest.raises(BotLookupAmbiguousError):
        lookup.get_bot_by_address("default", "stable-entity")
    repository.get_by_id_and_owner.assert_not_called()


def test_publish_key_lookup_stays_current_owner_scoped():
    repository = Mock(spec=BotRepository)
    lookup = RuntimeBotLookup(repository)
    repository.get_by_id_and_owner.return_value = row()
    assert lookup.get_bot_pk("default", "current-owner") == 42
    repository.get_by_id_and_owner.assert_called_once_with("default", "current-owner")
    repository.get_by_id_and_owner.return_value = None
    with pytest.raises(BotNotFoundError):
        lookup.get_bot_pk("default", "old-owner")


def test_repeated_requests_do_not_reuse_stale_owner_or_hide_read_errors():
    repository = Mock(spec=BotRepository)
    repository.list_matches_by_legacy_locator.side_effect = [
        [row()], [row(owner_id="new-owner")], RuntimeError("database unavailable"),
    ]
    lookup = RuntimeBotLookup(repository)
    assert lookup.get_bot_by_address("default", "stable-entity")["owner_id"] == "current-owner"
    assert lookup.get_bot_by_address("default", "stable-entity")["owner_id"] == "new-owner"
    with pytest.raises(RuntimeError, match="database unavailable"):
        lookup.get_bot_by_address("default", "stable-entity")
    assert repository.list_matches_by_legacy_locator.call_count == 3


def test_production_consumers_receive_lightweight_lookup(test_injector):
    from agentclaw.community.core.engine_runtime.relay import EngineRuntimeRelay
    from agentclaw.community.api.engine_connection_service import EngineConnectionServiceProtocol

    assert isinstance(test_injector.get(EngineRuntimeRelay)._bot_service, RuntimeBotLookup)
    assert isinstance(test_injector.get(EngineConnectionServiceProtocol)._bot_service, RuntimeBotLookup)
