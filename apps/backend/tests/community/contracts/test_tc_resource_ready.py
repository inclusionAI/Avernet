"""Conformance tests for the TC resource-ready Plugin API."""

from __future__ import annotations

import asyncio

from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)
from agentclaw.community.core.tc_file_upload_integrations.coordinator import (
    TcResourceReadyCoordinator,
)
from agentclaw.community.plugins.local.tc_resource_ready import (
    LocalTcResourceReadyPublisher,
)


def _resource() -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id="sr-contract",
        owner_id="user-1",
        bot_id="bot-1",
        scope_type="session",
        scope_key_hash="scope",
        session_key_hash="session",
        engine_type="openclaw",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        display_name="note.md",
        filename="note.md",
        device_path="workspace/note.md",
        workspace_relative_path="note.md",
        transfer_id="transfer-1",
        status=SessionResourceStatus.READY,
        size_bytes=7,
    )


def test_coordinator_consumes_local_resource_ready_plugin() -> None:
    async def scenario() -> None:
        publisher = LocalTcResourceReadyPublisher()
        coordinator = TcResourceReadyCoordinator(publisher=publisher)

        coordinator.notify_in_background(_resource())
        while coordinator._tasks:
            await asyncio.gather(*tuple(coordinator._tasks))

        calls = publisher.calls_to("publish")
        assert len(calls) == 1
        assert calls[0].args[0].as_payload() == {
            "schema_version": "1",
            "event_id": "tc.resource.ready:sr-contract",
            "res_id": "sr-contract",
        }

    asyncio.run(scenario())
