"""End-to-end: per-stage engine_overrides (DingTalk channels) on publish.

Drives the real teclaw publish flow (collector → reader → publish flow → BaaS
create payload) and asserts each stage's delivered ``engine_overrides.channels``
matches that stage's own active channel rows — not the draft/previous stage's.
Reuses the publish-flow harness helpers; only adds per-stage channel seeding.

ARCA's no-op (no config_artifact → no fetch/overlay/store) is covered at the unit
level in ``tests/core/service_bot/services/test_publish_flow_service.py`` (the
``_arca_*`` cases); this teclaw-only HTTP harness asserts the positive path.
"""
from __future__ import annotations

from typing import Annotated

from agentclaw.community.core.channel.services.repositories import ChannelRepository
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.framework import CaseInput, ExpectSuccess, endpoint_test
from tests.community.endpoints.test_service_bot_publish_flow import (
    _BOT_ID,
    _HEADERS,
    _OWNER,
    _PROCESS,
    _V1,
    _baas,
    _binding,
    _ext,
    _install_baas,
    _install_engine,
    _seed_draft,
)

_DRAFT_CID = "cid-draft"
_VERIFY_CID = "cid-verify"
_ONLINE_CID = "cid-online"


def _seed_channel(world, *, stage: str, client_id: str) -> None:
    world.get(ChannelRepository).insert_channel(
        type="dingding",
        description=None,
        identity_id=_OWNER,
        bind_bot_id=_BOT_ID,
        config={"client_id": client_id},
        status="1",
        stage=stage,
    )


def _delivered_artifact(world) -> dict:
    create = next(
        c for c in _baas(world).calls_to("post") if c.args[0].endswith("/api/v1/bots")
    )
    return create.kwargs["json"]["config"]["deploy_config"]["teclaw_bot_config"]


def _delivered_client_ids(world) -> list[str]:
    eo = _delivered_artifact(world)["engine_overrides"]
    return [a["client_id"] for a in eo["channels"]["dingding"]["accounts"]]


# ── verify leg (via DRAFT /process background build+create) ──────────────────


def _seed_verify_channels(world) -> None:
    # All three stages have an active channel; the verify create must carry ONLY
    # the verify one — proving stage isolation through the whole pipeline.
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    _install_engine(world)
    _seed_channel(world, stage="draft", client_id=_DRAFT_CID)
    _seed_channel(world, stage="verify", client_id=_VERIFY_CID)
    _seed_channel(world, stage="online", client_id=_ONLINE_CID)


def _expect_verify_channel_delivered_and_stored(response, world):  # noqa: ARG001
    assert _delivered_client_ids(world) == [_VERIFY_CID], _delivered_artifact(world)[
        "engine_overrides"
    ]
    stored = _ext(world, _V1)["engine_overrides_by_stage"]["verify"]
    assert [a["client_id"] for a in stored["channels"]["dingding"]["accounts"]] == [
        _VERIFY_CID
    ]


@endpoint_test(
    method="POST", path=_PROCESS, scenario="draft_process_delivers_verify_stage_channels",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_verify_channels, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"data": {"status": "building"}}),
    extra_assertions=(_expect_verify_channel_delivered_and_stored,),
)
def draft_process_delivers_verify_stage_channels():
    """The verify first-release delivers the verify-stage DingTalk channel (not the
    draft/online ones) and persists it under engine_overrides_by_stage.verify."""


# ── online leg (via VALIDATING /process inline release) ──────────────────────


def _seed_online_channels(world) -> None:
    _seed_draft(world)
    _install_baas(world, progress_status="SUCCESS")
    bid = _binding(world, device_id="BOT-verify", status="ACTIVE")
    # Seeded past build: a non-empty config_artifact (engine_overrides empty — the
    # online leg overlays the online channels on top).
    artifact = {
        "schema_version": 3, "engine_type": "teclaw", "mcp": {"servers": []},
        "skills": [], "resources": [], "identity_files": [], "stores": {},
        "engine_overrides": {}, "engine_ext": {}, "version": 1,
    }
    world.get(BotPublishRepositoryProtocol).update_status_with_ext(
        publish_id=_V1, target_status=PublishStatus.VALIDATING,
        ext={"binding": {"verify": bid}, "publish": {"verify": 555},
             "config_artifact": artifact},
        source_status=PublishStatus.DRAFT,
    )
    _seed_channel(world, stage="verify", client_id=_VERIFY_CID)
    _seed_channel(world, stage="online", client_id=_ONLINE_CID)


def _expect_online_channel_delivered_and_stored(response, world):  # noqa: ARG001
    assert _delivered_client_ids(world) == [_ONLINE_CID], _delivered_artifact(world)[
        "engine_overrides"
    ]
    stored = _ext(world, _V1)["engine_overrides_by_stage"]["online"]
    assert [a["client_id"] for a in stored["channels"]["dingding"]["accounts"]] == [
        _ONLINE_CID
    ]


@endpoint_test(
    method="POST", path=_PROCESS, scenario="validating_process_delivers_online_stage_channels",
    input=CaseInput(json_body={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_online_channels,
    expect=ExpectSuccess(status=200, json_contains={"data": {"status": "online_pub"}}),
    extra_assertions=(_expect_online_channel_delivered_and_stored,),
)
def validating_process_delivers_online_stage_channels():
    """The online first-release overlays the online-stage DingTalk channel onto the
    shared build artifact (not the verify one) and persists it under
    engine_overrides_by_stage.online."""
