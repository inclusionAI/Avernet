"""Coverage wiring owned by the real singlebox device profile."""
from __future__ import annotations

import json

import pytest

from agentclaw.community.di.modules.infrastructure.singlebox.devices import (
    SingleboxDevicesModule,
)


@pytest.mark.asyncio
async def test_singlebox_transport_records_runtime_invoke(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    transport = SingleboxDevicesModule().device_adapter_transport()

    response = await transport.invoke(
        {"bot_uuid": "bot-cron-coverage"},
        "GET",
        "/api/cron",
    )

    assert response == {"success": True, "data": []}
    hit_path = tmp_path / "backend" / "plugin_hits.jsonl"
    hits = [json.loads(line) for line in hit_path.read_text().splitlines()]
    assert [hit["key"] for hit in hits] == ["DeviceAdapterTransport.invoke"]
