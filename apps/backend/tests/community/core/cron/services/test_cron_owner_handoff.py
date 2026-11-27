"""A handed-off Bot retains its published runtimes and current authorization identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.service_bot.repository.models import PublishStatus


def make_service(owner, provider, *, validating=False):
    bot = dict(id=73, bot_id="scheduled-bot", bot_name="Scheduled", bot_type="service",
               entity_id="original", owner_id=owner, binding_id=10, active_engine=provider,
               call_type="owner")
    bots = MagicMock()
    bots.get_bot.return_value = bot
    bots.list_bots_by_owner_or_collaborator.return_value = {"items": [bot]}
    publishes = MagicMock()
    record = SimpleNamespace(id=91, source_bot_pk=73, owner_id="original",
                             status=PublishStatus.SUCCESS.value,
                             ext={"binding": {"verify": 20, "online": 30}})

    def lookup(*, source_bot_pk, status, env):
        assert source_bot_pk == 73
        if status == PublishStatus.SUCCESS.value:
            return record
        if validating and status == PublishStatus.VALIDATING.value:
            return SimpleNamespace(id=92, ext={"binding": {"verify": 21}}, status=status)
        return None

    publishes.get_latest_by_source_bot_and_status.side_effect = lookup
    publishes.get_latest_by_source_bot_id_and_owner_and_status.side_effect = AssertionError(
        "A current Owner must never address the historical publication"
    )
    devices = MagicMock()
    devices.get_device.return_value = {"status": "ACTIVE", "device_provider": provider}
    devices.list_devices_by_runtime_binding.return_value = ["instance"]
    resolver = MagicMock()
    for name in ("resolve_for_bot", "resolve_for_binding", "resolve_for_binding_invoke"):
        getattr(resolver, name).return_value = SimpleNamespace(conn_info={"route": "runtime"})
    transport = MagicMock()
    transport.invoke = AsyncMock(side_effect=lambda *args, **kwargs: {"success": True, "data": [{"id": "daily"}]})
    service = CronRelayService(bots, devices, transport, resolver, MagicMock(), publishes,
                               MagicMock(), MagicMock())
    return service, publishes, transport, resolver


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["original", "new-owner"])
@pytest.mark.parametrize("provider", ["arca", "baas", "teclaw"])
@pytest.mark.parametrize("actor", ["new-owner", "collaborator"])
@pytest.mark.parametrize("stage", [None, "draft", "verify", "online"])
async def test_list_keeps_all_published_targets_after_handoff(owner, provider, actor, stage):
    service, publishes, transport, _ = make_service(owner, provider)
    result = await service.list_all_crons(
        user_id=owner, nick_name="Actor", bot_id="scheduled-bot",
        runtime_stage=stage, authenticated_user_id=actor,
    )
    expected = {stage} if stage else {"draft", "verify", "online"}
    assert {row["runtime_stage"] for row in result["data"]} == expected
    assert all(row["owner_id"] == owner for row in result["data"])
    assert result["failed_targets"] == []
    assert transport.invoke.await_count == len(expected)
    publishes.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()
    # Same bounded number of indexed reads as before; no per-task publication queries.
    assert publishes.get_latest_by_source_bot_and_status.call_count == 3


@pytest.mark.parametrize("stage,validating,binding", [
    ("online", False, 30), ("verify", False, 20), ("verify", True, 21),
])
def test_published_operations_resolve_by_pk_and_keep_current_owner(stage, validating, binding):
    service, publishes, _, _ = make_service("new-owner", "arca", validating=validating)
    bot = service._bot_provider.get_bot.return_value
    target = service._resolve_published_runtime_target(bot, "collaborator", stage)
    assert target.binding_id == binding
    assert target.owner_id == "new-owner"
    assert target.runtime_stage == stage
    publishes.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()


def test_missing_publication_does_not_fall_back_to_another_bots_owner():
    service, publishes, _, _ = make_service("new-owner", "arca")
    publishes.get_latest_by_source_bot_and_status.side_effect = None
    publishes.get_latest_by_source_bot_and_status.return_value = None
    with pytest.raises(CronRelayError, match="publish record not found"):
        service._resolve_published_runtime_target(
            service._bot_provider.get_bot.return_value, "collaborator", "online"
        )
    publishes.get_latest_by_source_bot_id_and_owner_and_status.assert_not_called()
