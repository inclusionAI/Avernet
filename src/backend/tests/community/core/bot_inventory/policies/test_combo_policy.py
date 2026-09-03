"""Unit tests for Bot inventory create-combination policies."""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_inventory.policies.combo_policy import (
    APPLICATION_CODING_ENGINES,
    LOCAL_CAPABLE_ENGINES,
    PERSONAL_CLOUD_CAPABLE_ENGINES,
    SERVICE_CAPABLE_ENGINES,
    assert_application_coding_create,
    assert_local_create,
    assert_personal_cloud_create,
    assert_service_upgrade,
)
from agentclaw.community.core.bot_inventory.types import DeployMode


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


@pytest.mark.unit
def test_service_upgrade_engine_matrix() -> None:
    assert SERVICE_CAPABLE_ENGINES == frozenset({"openclaw", "claude_code", "teclaw"})
    for engine in SERVICE_CAPABLE_ENGINES:
        assert assert_service_upgrade(engine).ok

    rejected = assert_service_upgrade("hermes")
    assert not rejected.ok
    assert rejected.reason == "engine cannot be serviced: hermes"


@pytest.mark.unit
def test_application_coding_engine_matrix() -> None:
    assert APPLICATION_CODING_ENGINES == frozenset({"claude_code"})


@pytest.mark.unit
@pytest.mark.parametrize("space_kind", ["personal", "team"])
def test_application_coding_supported_combination(space_kind: str) -> None:
    # No space-kind gate: coding bots may be created in any business space.
    ok = assert_application_coding_create(
        engine="claude_code",
        bot_type="personal",
        space_kind=space_kind,
        deployment_mode=DeployMode.CLOUD,
    )
    assert ok.ok


@pytest.mark.unit
def test_application_coding_rejects_aicoding_engine() -> None:
    # aicoding is the internal adapter, not an external engine value — the only
    # external engine for applicationCoding is claude_code.
    rejected = assert_application_coding_create(
        engine="aicoding",
        bot_type="personal",
        space_kind="personal",
        deployment_mode=DeployMode.CLOUD,
    )
    assert not rejected.ok
    assert "aicoding" in (rejected.reason or "")


@pytest.mark.unit
def test_application_coding_rejects_service_and_local() -> None:
    assert not assert_application_coding_create(
        engine="claude_code",
        bot_type="service",
        space_kind="personal",
        deployment_mode=DeployMode.CLOUD,
    ).ok
    assert not assert_application_coding_create(
        engine="claude_code",
        bot_type="personal",
        space_kind="personal",
        deployment_mode=DeployMode.LOCAL,
    ).ok
