"""Unit tests for Bot inventory create-combination policies."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_inventory.policies.combo_policy import (
    LOCAL_CAPABLE_ENGINES,
    PERSONAL_CLOUD_CAPABLE_ENGINES,
    assert_local_create,
    assert_personal_cloud_create,
)


@pytest.mark.unit
def test_personal_cloud_supported_engine_matrix_includes_teclaw() -> None:
    assert "teclaw" in PERSONAL_CLOUD_CAPABLE_ENGINES
    for engine in PERSONAL_CLOUD_CAPABLE_ENGINES:
        assert assert_personal_cloud_create(engine, "personal").ok
        assert assert_personal_cloud_create(engine, "team").ok


@pytest.mark.unit
def test_personal_cloud_rejects_unknown_engine_and_invalid_space() -> None:
    unsupported = assert_personal_cloud_create("unknown", "personal")
    assert not unsupported.ok
    assert unsupported.reason == "unsupported engine: unknown"

    invalid_space = assert_personal_cloud_create("openclaw", "org")
    assert not invalid_space.ok
    assert invalid_space.reason == "personal cloud bot requires a valid business space"


@pytest.mark.unit
def test_local_supported_engine_matrix() -> None:
    assert LOCAL_CAPABLE_ENGINES == frozenset({"openclaw", "claude_code"})
    for engine in LOCAL_CAPABLE_ENGINES:
        assert assert_local_create(engine, "personal").ok


@pytest.mark.unit
def test_local_rejects_unknown_engine_and_non_personal_space() -> None:
    unsupported = assert_local_create("hermes", "personal")
    assert not unsupported.ok
    assert unsupported.reason == "local bot does not support engine: hermes"

    invalid_space = assert_local_create("openclaw", "team")
    assert not invalid_space.ok
    assert invalid_space.reason == "local bot is personal business-space only"


@pytest.mark.unit
def test_local_rejects_teclaw_cloud_only_engine() -> None:
    # teclaw is a cloud-only engine (PRD §10.6 / §10.11-6): the local desktop
    # path does not provision it, so create is refused up front instead of
    # reaching a desktop-service that cannot serve it.
    rejected = assert_local_create("teclaw", "personal")
    assert not rejected.ok
    assert rejected.reason == "local bot does not support engine: teclaw"
    # Cloud personal bots still accept teclaw — see PERSONAL_CLOUD path above.
    assert assert_personal_cloud_create("teclaw", "personal").ok
