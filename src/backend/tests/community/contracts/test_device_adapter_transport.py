"""Rule 25 conformance — DeviceAdapterTransport.

Consumer under test: ``CronRelayService``. The relay resolves a device
connection and then forwards over ``DeviceAdapterTransport``; in
local/test mode that's the in-memory cron adapter
(``InMemoryDeviceAdapterTransport``). Driving the real relay end-to-end
is the conformance check.

Plugin-hit assertion: create a cron through the real relay, then list it
back. The listed cron is observable proof the *stateful* in-memory
transport executed — a canned no-op stub could not echo the created
item.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_local_device


def test_transport_resolves_to_local_impl(world) -> None:
    transport = world.get(DeviceAdapterTransport)
    assert isinstance(transport, DeviceAdapterTransport)


@pytest.mark.asyncio
async def test_relay_runs_end_to_end_over_transport(world) -> None:
    make_staff_user(world, user_id="u_owner")
    binding_id = make_active_local_device(world, owner_id="u_owner")
    make_bot(
        world,
        bot_id="bot_x",
        owner_id="u_owner",
        owner_name="Owner",
        bot_type="service",
        status="ACTIVE",
        binding_id=binding_id,
    )

    relay = world.get(CronRelayService)

    # Create through the real relay → in-memory adapter stores it.
    created = await relay.forward_request(
        bot_id="bot_x",
        user_id="u_owner",
        nick_name="Owner",
        method="POST",
        path="/api/cron",
        body={"name": "c1", "schedule": "0 9 * * *", "command": "hi"},
    )
    assert created["success"] is True
    # Relay response shaping ran (it injects bot_id/bot_name).
    assert created["data"]["bot_id"] == "bot_x"

    # Plugin-hit assertion: the created cron is listed back — proof the
    # stateful transport ran, not a canned stub.
    listed = await relay.list_all_crons(
        user_id="u_owner", nick_name="Owner", bot_id="bot_x"
    )
    assert listed["success"] is True
    assert any(c["name"] == "c1" for c in listed["data"])


@pytest.mark.asyncio
async def test_transport_streams_registered_materialized_content(world) -> None:
    transport = world.get(DeviceAdapterTransport)
    assert isinstance(transport, InMemoryDeviceAdapterTransport)
    transport.register_materialized_content(
        resource_id="sr_stream",
        content=b"ready bytes",
        filename="ready.txt",
        content_type="text/plain",
    )

    response = await transport.stream(
        {},
        "GET",
        "/api/resource-materializations/sr_stream/content",
        params={"disposition": "attachment"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain"
    assert response.headers["content-disposition"] == 'attachment; filename="ready.txt"'
    assert b"".join([chunk async for chunk in response.body]) == b"ready bytes"
    await response.close()
