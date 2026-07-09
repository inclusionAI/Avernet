"""Unit tests for ``ArcaSnapshotProducer`` — behavior-equivalent build wrap."""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
    ArcaSnapshotProducer,
)


class _RecordingBuild:
    """Stub build service that records its args and returns a canned result."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[dict[str, Any], int]] = []

    def build(self, bot: dict[str, Any], version: int = 1) -> dict[str, Any]:
        self.calls.append((bot, version))
        return self.result


@pytest.mark.unit
def test_passes_bot_and_version_through_to_build() -> None:
    stub = _RecordingBuild({"success": True})
    bot = {"bot_id": "b1", "entity_id": "u1"}
    ArcaSnapshotProducer(stub).produce_artifact(bot, 7)
    assert stub.calls == [(bot, 7)]


@pytest.mark.unit
def test_maps_success_and_both_paths_onto_ext() -> None:
    stub = _RecordingBuild(
        {
            "success": True,
            "migration_path": "/home/admin/nfs/bot-data/3/mcp",
            "build_target_path": "/data/bot/3/mcp",
            # extra build-result keys are not deployable pointers -> dropped from ext
            "bot_id": "b1",
            "version": "3",
        }
    )
    artifact = ArcaSnapshotProducer(stub).produce_artifact({}, 3)
    assert artifact.success is True
    assert artifact.message == ""
    assert artifact.ext == {
        "migration_path": "/home/admin/nfs/bot-data/3/mcp",
        "build_target_path": "/data/bot/3/mcp",
    }


@pytest.mark.unit
def test_failed_build_propagates_success_false_and_message() -> None:
    stub = _RecordingBuild({"success": False})
    artifact = ArcaSnapshotProducer(stub).produce_artifact({}, 1)
    assert artifact.success is False
    assert artifact.message == "构建失败"
    assert artifact.ext == {}


@pytest.mark.unit
def test_missing_paths_are_omitted_from_ext() -> None:
    # build() may legitimately omit a pointer (e.g. no device_id branch);
    # we must not invent keys.
    stub = _RecordingBuild({"success": True, "migration_path": "/only/mig"})
    artifact = ArcaSnapshotProducer(stub).produce_artifact({}, 1)
    assert artifact.ext == {"migration_path": "/only/mig"}
