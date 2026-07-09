"""Integration tests (real DI injector + real in-memory DB) for the teclaw
channel-delivery feature. These resolve the two Task 6 "decide-in-tasks" unknowns
against real seeded rows rather than mocks:

* **Identity scoping** — which id the collector's channel lookup keys on, so the
  composed artifact actually contains the bot's channels.
* **Engine resolution** — that ``ChannelService._is_teclaw_bot`` reads the bot's
  real ``active_engine`` to route delivery.

Only the DB is real here; no HTTP boundary is exercised (the dispatch/ordering and
best-effort delivery behavior is covered by the service unit tests in
``test_channel_service.py``).
"""
from __future__ import annotations

import pytest

from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.channel.services.repositories import ChannelRepository
from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.config_compose.services.collector import (
    ConfigComposerInputCollector,
)
from tests.community.factories.access import make_staff_user

# Re-export the framework fixtures into this module's collection scope so this
# test — outside ``tests/endpoints/`` — can request ``world`` by name (same
# pattern as ``tests/core/devices/test_device_connection_manager_wiring.py``).
from tests.community.framework.fixtures import (  # noqa: F401 — re-export for collection
    app_with_testing_modules,
    world,
)


_OWNER = "u_chan"
_BOT = "chan_bot"


def _seed_bot(world, *, active_engine: str) -> None:
    make_staff_user(world, user_id=_OWNER)
    world.get(BotRepository).insert({
        "bot_id": _BOT, "bot_name": "Chan Bot",
        "owner_id": _OWNER, "owner_name": _OWNER,
        "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "staff", "creator_id": _OWNER,
        "active_engine": active_engine, "binding_id": None,
    })


def _seed_channel(world, *, identity_id: str = _OWNER, status: str = "1",
                  stage: str | None = None) -> int:
    return world.get(ChannelRepository).insert_channel(
        type="dingding", description="seed", identity_id=identity_id,
        bind_bot_id=_BOT, status=status, stage=stage,
        config={"client_id": "cid-1", "client_secret": "sec-1", "dm_policy": "open"},
    )


def _req() -> ComposeRequest:
    return ComposeRequest(
        entity_id=_OWNER, bot_id=_BOT, user_id=_OWNER, engine_type="teclaw",
    )


@pytest.mark.integration
def test_collector_reads_real_active_channel_keyed_on_owner(world):
    """Identity scoping RESOLVED: a channel row keyed on the bot owner's
    ``identity_id`` is found by the collector's ``[user_id, aideskdingding]``
    lookup when ``req.user_id == owner`` — confirming ``user_id`` is the correct
    compose-path key, against a real DB row."""
    _seed_bot(world, active_engine="teclaw")
    _seed_channel(world)

    out = world.get(ConfigComposerInputCollector).engine_overrides(_req())

    accounts = out["channels"]["dingding"]["accounts"]
    assert [a["client_id"] for a in accounts] == ["cid-1"]
    assert accounts[0]["client_secret"] == "sec-1"


@pytest.mark.integration
def test_inactive_channel_absent_from_real_compose(world):
    """A ``status='0'`` row is not emitted — real-DB regression guard for the
    no-active-channels → ``{}`` case."""
    _seed_bot(world, active_engine="teclaw")
    _seed_channel(world, status="0")

    out = world.get(ConfigComposerInputCollector).engine_overrides(_req())

    assert out == {}


@pytest.mark.integration
def test_is_teclaw_bot_true_for_real_teclaw_bot(world):
    """Engine resolution RESOLVED: ``_is_teclaw_bot`` reads the real bot's
    ``active_engine`` and returns True for a teclaw bot."""
    _seed_bot(world, active_engine="teclaw")
    svc = world.get(ChannelServiceProtocol)
    assert svc._is_teclaw_bot(_BOT, _OWNER) is True


@pytest.mark.integration
def test_is_teclaw_bot_false_for_real_openclaw_bot(world):
    """An openclaw bot resolves to non-teclaw, so the existing openclaw.json
    write path runs unchanged."""
    _seed_bot(world, active_engine="openclaw")
    svc = world.get(ChannelServiceProtocol)
    assert svc._is_teclaw_bot(_BOT, _OWNER) is False
