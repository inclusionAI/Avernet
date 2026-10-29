"""The OpenAPI facade selects historical publications through the resolved PK."""

from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.errors import ServicePublicationNotFoundError
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.core.service_bot.services.test_service_publication_facade import (
    deps,  # noqa: F401 - shared facade fixture
    record,
)


def _handoff(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    bot = {"id": 10, "bot_id": "bot-1", "entity_id": "owner",
           "owner_id": "new-owner", "bot_type": "service", "env": "dev"}
    deps.bot_repo.list_matches_by_legacy_locator.side_effect = (
        lambda bot_id, locator: [bot] if locator in {"owner", "new-owner"} else []
    )
    draft = record(7, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [draft]
    deps.flow_service.process = AsyncMock()
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.NONE
    return draft


@pytest.mark.asyncio
@pytest.mark.parametrize("locator", ["owner", "new-owner"])
async def test_new_owner_advances_original_draft(deps, monkeypatch, locator):
    draft = _handoff(deps, monkeypatch)
    result = await deps.facade.advance(
        "bot-1", "prestable", actor_id="new-owner", owner_id=locator
    )
    assert result["publication_id"] == draft.id
    deps.publish_repo.list_by_source_bot.assert_called_once_with(10, "dev")
    deps.flow_service.process.assert_awaited_once_with(draft.id, "new-owner")
    deps.lock_service.get_lock_info.assert_called_once_with("bot-1", "new-owner", "new-owner")
    assert draft.owner_id == "owner"


@pytest.mark.asyncio
async def test_former_owner_cannot_advance_original_draft(deps, monkeypatch):
    _handoff(deps, monkeypatch)
    with pytest.raises(ServicePublicationNotFoundError):
        await deps.facade.advance(
            "bot-1", "prestable", actor_id="owner", owner_id="owner"
        )
    deps.flow_service.process.assert_not_awaited()
