"""Keep the BaaS unnamed end result compatible with BCS coordination.

The shared fixture deliberately omits result.name. Do not add it to make a
consumer regression pass; BCS must associate the result with its cached start.
"""

import json
from pathlib import Path

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse import DefaultStreamConverter


def test_baas_command_output_end_without_name_is_standard_coordination_case():
    fixture_path = (
        Path(__file__).resolve().parents[7]
        / "apps/bcs/crates/test-support/fixtures"
        / "baas_command_output_end_without_name.json"
    )
    fixture = json.loads(fixture_path.read_text())
    assert fixture["engine_frame"]["data"]["toolName"] == "Bash"
    converter = DefaultStreamConverter()
    event = converter.convert(
        StreamChunk(
            type="agent",
            engine_type=fixture["engine_type"],
            metadata={"engine_frame": fixture["engine_frame"]},
        ),
        run_id="manager-run",
    )

    assert event is not None
    assert event.event == "agent"
    data = json.loads(event.data)
    assert "name" not in data
    assert "toolName" not in data
    for field in ("runId", "seq", "ts"):
        data.pop(field)
    assert data == fixture["provider_result"]
