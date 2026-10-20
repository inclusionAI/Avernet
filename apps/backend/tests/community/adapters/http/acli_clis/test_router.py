"""Internal acli CLI routes: the actor is always the authenticated user."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.acli_clis.router import (
    add_bot_cli,
    list_bot_clis,
    remove_bot_cli,
    router,
)
from agentclaw.community.adapters.http.acli_clis.schemas import AddBotCliRequest
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.acli_cli_service import AcliTool, BotCli

pytestmark = pytest.mark.asyncio

_USER = AuthenticatedUser(id="owner-1", staffId="owner-1", operatorName="owner-1")


async def test_add_passes_the_authenticated_actor_and_the_entity() -> None:
    service = MagicMock()
    service.add = AsyncMock(return_value=AcliTool("deepinsight-cli", "di", "DI", "1.0.9"))

    added = await add_bot_cli(
        "bot-1", AddBotCliRequest(cli_code="deepinsight-cli", lock_epoch=7), "entity-1", _USER, service
    )

    service.add.assert_awaited_once_with(
        bot_id="bot-1", entity_id="entity-1", actor_id="owner-1",
        cli_code="deepinsight-cli", lock_epoch=7,
    )
    assert (added.cli_code, added.command, added.version) == ("deepinsight-cli", "di", "1.0.9")


async def test_remove_passes_the_authenticated_actor_and_the_lock_epoch() -> None:
    service = MagicMock()
    service.remove = AsyncMock(return_value=None)

    removed = await remove_bot_cli("bot-1", "yuque-cli", "entity-1", 3, _USER, service)

    service.remove.assert_awaited_once_with(
        bot_id="bot-1", entity_id="entity-1", actor_id="owner-1",
        cli_code="yuque-cli", lock_epoch=3,
    )
    assert (removed.cli_code, removed.deleted) == ("yuque-cli", True)


async def test_list_maps_every_grant() -> None:
    service = MagicMock()
    service.list_bot_clis.return_value = [BotCli("dataphin", "Dataphin", "", "owner", True)]

    items = await list_bot_clis("bot-1", "entity-1", _USER, service)

    service.list_bot_clis.assert_called_once_with(
        bot_id="bot-1", entity_id="entity-1", actor_id="owner-1"
    )
    assert [(i.cli_code, i.identity_mode, i.default) for i in items] == [("dataphin", "owner", True)]


def test_market_route_is_declared_before_the_bot_id_routes() -> None:
    paths = [route.path for route in router.routes]
    assert paths.index("/api/bots/market/clis") < paths.index("/api/bots/{bot_id}/clis")


def test_add_body_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        AddBotCliRequest(cli_code="x", user_id="someone-else")  # type: ignore[call-arg]
