"""Smoke test: Lifecycle.startup() subscribes the skill-symlink listener."""

from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import DeviceActivatedEvent


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def _resolve_listener():
    from agentclaw.community.adapters.http.app import app
    from agentclaw.community.core.skill_center.services.skill_symlink_listener import (
        SkillSymlinkListener,
    )
    # The conftest-level fixture attaches the test injector to ``app.state``
    # via ``attach_injector``; reach for it there instead of a module global.
    return app.state.injector.get(SkillSymlinkListener)


def test_skill_symlink_listener_is_registered_by_startup_hook():
    listener = _resolve_listener()
    asyncio.run(listener.startup())

    bus = get_event_bus()
    handlers = bus._handlers.get(DeviceActivatedEvent, [])  # type: ignore[attr-defined]
    assert listener.handle in handlers


def test_register_is_idempotent():
    listener = _resolve_listener()
    asyncio.run(listener.startup())
    asyncio.run(listener.startup())

    bus = get_event_bus()
    handlers = bus._handlers.get(DeviceActivatedEvent, [])  # type: ignore[attr-defined]
    assert handlers.count(listener.handle) == 1
