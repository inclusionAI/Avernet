"""Endpoint tests for service-bot rollback endpoints.

Covers:
- GET /api/service-bot/publish/{publish_id}/can-rollback
- POST /api/service-bot/publish/{publish_id}/rollback
"""
from __future__ import annotations

from typing import Annotated

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.channel.services.repositories import ChannelRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.plugin_api.http_client import QUALIFIER_BAAS, HttpClient
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
    http_envelope_response,
)


_OWNER = "u_rollback"
_BOT_ID = "rollback_bot"
_SRC_UUID = "BOT-src"
_VERIFY_UUID = "BOT-verify"
_ONLINE_UUID = "BOT-online"
_BAAS_PUB_ID = 666
_ENGINE = "teclaw"
_V1 = 1  # original version
_V2 = 2  # upgraded version
_HEADERS = {"x-user-id": _OWNER}

_ARTIFACT = {
    "schema_version": 3, "engine_type": _ENGINE, "mcp": {"servers": []},
    "skills": [], "resources": [], "identity_files": [], "stores": {},
    "engine_overrides": {}, "engine_ext": {}, "version": 1,
}

_CAN_ROLLBACK = "/api/service-bot/publish/{publish_id}/can-rollback"
_ROLLBACK = "/api/service-bot/publish/{publish_id}/rollback"


# ── BaaS stub ────────────────────────────────────────────────────────────────


def _baas(world):
    return world.get(Annotated[HttpClient, QUALIFIER_BAAS])


def _install_baas_for_rollback(
    world, *, create_success: bool = True, progress_status: str = "SUCCESS"
) -> None:
    """Stub BaaS for rollback operations.

    ``progress_status`` drives the durable progress-poll flip: ``SUCCESS`` lets the
    enqueued poll land the rollback target at ``SUCCESS``; ``PENDING`` keeps the
    target parked at ``ONLINE_PUB`` (the poll reschedules)."""
    def _get(path, **_kw):
        if "/devices" in path:
            return http_envelope_response([{"items": [
                {"provider_type": "TECLAW", "device_uuid": _SRC_UUID}]}])
        if "/progress" in path:
            return http_envelope_response(
                {"status": progress_status, "device_details": [],
                 "overall_progress": {}, "failed_devices": []})
        return http_envelope_response({})

    def _post(path, **_kw):
        if "/approve" in path:
            return http_envelope_response({"publish_id": _BAAS_PUB_ID, "status": "APPROVED"})
        if "/update" in path:
            return http_envelope_response({"bot_uuid": _VERIFY_UUID, "publish_id": _BAAS_PUB_ID})
        if "/destroy" in path:
            return http_envelope_response({"publish_id": _BAAS_PUB_ID})
        if path.endswith("/api/v1/bots"):
            if not create_success:
                return http_envelope_response(code=1, message="create failed")
            return http_envelope_response({"bot_uuid": _ONLINE_UUID, "publish_id": _BAAS_PUB_ID})
        return http_envelope_response({})

    _baas(world).set_override("get", _get)
    _baas(world).set_override("post", _post)


# ── seeding ──────────────────────────────────────────────────────────────────


def _seed_base(world) -> dict:
    """Seed base entities: user, bot, skills, binding. Returns the bot dict."""
    make_staff_user(world, user_id=_OWNER)
    from agentclaw.community.core.skill_center.services.repositories import (
        SkillRepository,
        SkillSetRepository,
    )
    from agentclaw.community.core.resources.repository.protocol import ResourceRepositoryProtocol
    from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin

    world.get(SkillRepoSyncPlugin).set_override("get_local_skills_root", lambda: None)

    binding_repo = world.get(DeviceBindingRepository)
    src_binding_id = binding_repo.insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=_SRC_UUID,
        device_provider="teclaw", env="dev", device_props={},
        status="ACTIVE", apply_reason="seed", applied_by=_OWNER,
    )
    bot = world.get(BotRepository).insert({
        "bot_id": _BOT_ID, "bot_name": "Rollback Bot",
        "owner_id": _OWNER, "owner_name": _OWNER,
        "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "staff", "creator_id": _OWNER,
        "active_engine": _ENGINE, "binding_id": src_binding_id,
    })

    world.get(ResourceRepositoryProtocol).create({
        "name": "data.csv", "resource_type": "file", "status": "active",
        "attributes": {"path": "data/data.csv"},
        "user_id": _OWNER, "created_by": _OWNER, "source": "upload", "bolt_id": _BOT_ID,
    })

    skill = world.get(SkillRepository).create({
        "name": "skill1", "description": "test skill",
        "category": "custom", "tags": [], "input_schema": "{}", "output_schema": "{}",
        "is_public": False, "is_builtin": False, "user_id": _OWNER, "bolt_id": _BOT_ID,
        "status": "PUBLISHED", "version": 1, "skill_uuid": "skill1-uuid",
        "source_type": "local", "category_path": "/custom", "package_url": "", "zip_url": "",
    })
    ss_repo = world.get(SkillSetRepository)
    skill_set = ss_repo.create({
        "name": "Active Set", "description": "active", "user_id": _OWNER,
        "bolt_id": _BOT_ID, "is_default": False, "is_builtin": False,
        "is_active": 1, "engine_type": _ENGINE,
    })
    ss_repo.add_skill_to_set(str(skill_set["id"]), str(skill["id"]))
    return bot


def _binding(world, *, device_id: str, status: str) -> int:
    return world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=device_id,
        device_provider="teclaw", env="dev", device_props={"publish_id": _BAAS_PUB_ID},
        status=status, apply_reason="seed", applied_by=_OWNER,
    )


def _seed_v1_success(world) -> None:
    """Seed V1 publish in SUCCESS state with online binding."""
    bot = _seed_base(world)
    _install_baas_for_rollback(world)

    vbid = _binding(world, device_id=_VERIFY_UUID, status="ACTIVE")
    obid = _binding(world, device_id=_ONLINE_UUID, status="ACTIVE")

    repo = world.get(BotPublishRepositoryProtocol)
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V1 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 1, "env": "dev",
        "ext": {
            "binding": {"verify": vbid, "online": obid},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ARTIFACT,
        },
    })


def _seed_v2_success_with_v1(world, *, progress_status: str = "SUCCESS") -> None:
    """Seed V1 (UPGRADED) and V2 (SUCCESS) with version chain."""
    bot = _seed_base(world)
    _install_baas_for_rollback(world, progress_status=progress_status)

    vbid1 = _binding(world, device_id=f"{_VERIFY_UUID}-1", status="ACTIVE")
    obid1 = _binding(world, device_id=f"{_ONLINE_UUID}-1", status="RELEASED")
    vbid2 = _binding(world, device_id=f"{_VERIFY_UUID}-2", status="ACTIVE")
    obid2 = _binding(world, device_id=f"{_ONLINE_UUID}-2", status="ACTIVE")

    repo = world.get(BotPublishRepositoryProtocol)

    # V1: UPGRADED (replaced by V2)
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V1 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.UPGRADED, "version": 1, "env": "dev",
        "ext": {
            "binding": {"verify": vbid1, "online": obid1},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ARTIFACT,
        },
    })

    # V2: SUCCESS (current), linked to V1 via last_pub_id
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V2 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 2, "env": "dev",
        "last_pub_id": _V1,
        "ext": {
            "binding": {"verify": vbid2, "online": obid2},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ARTIFACT,
        },
    })


_ROLLBACK_CID = "cid-rollback"
_CARD_A = "card-A"  # V1's promoted card template (stored per-stage slot)
_CARD_B = "card-B"  # the current live channel config — the state rolled away from

_V1_STORED_ONLINE = {
    "channels": {"dingding": {"enabled": True, "accounts": [
        {"client_id": _ROLLBACK_CID, "robot_code": _ROLLBACK_CID,
         "card_template_id": _CARD_A}]}}
}


def _seed_v2_success_with_v1_channel_overrides(world) -> None:
    """Same shape as ``_seed_v2_success_with_v1``, plus DingTalk channel state:
    V1's ext stores online engine_overrides with card A (persisted at V1's
    original online promotion), while the live channel table holds card B (the
    user's current config, delivered by V2). Rollback must restore card A."""
    bot = _seed_base(world)
    _install_baas_for_rollback(world)

    vbid1 = _binding(world, device_id=f"{_VERIFY_UUID}-1", status="ACTIVE")
    obid1 = _binding(world, device_id=f"{_ONLINE_UUID}-1", status="RELEASED")
    vbid2 = _binding(world, device_id=f"{_VERIFY_UUID}-2", status="ACTIVE")
    obid2 = _binding(world, device_id=f"{_ONLINE_UUID}-2", status="ACTIVE")

    repo = world.get(BotPublishRepositoryProtocol)

    # V1: UPGRADED, with an enriched artifact and the stored online overrides slot.
    v1_artifact = dict(
        _ARTIFACT, engine_ext={"bot_id": _BOT_ID, "owner_id": _OWNER, "stage": "release"}
    )
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V1 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.UPGRADED, "version": 1, "env": "dev",
        "ext": {
            "binding": {"verify": vbid1, "online": obid1},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": v1_artifact,
            "engine_overrides_by_stage": {"online": _V1_STORED_ONLINE},
        },
    })

    # V2: SUCCESS (current), linked to V1 via last_pub_id
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V2 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 2, "env": "dev",
        "last_pub_id": _V1,
        "ext": {
            "binding": {"verify": vbid2, "online": obid2},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ARTIFACT,
        },
    })

    # Live channel table: card B. A (wrong) live re-fetch would deliver this.
    world.get(ChannelRepository).insert_channel(
        type="dingding",
        description=None,
        identity_id=_OWNER,
        bind_bot_id=_BOT_ID,
        config={"client_id": _ROLLBACK_CID, "card_template_id": _CARD_B},
        status="1",
        stage="online",
    )


def _seed_v2_success_with_v1_pending(world) -> None:
    """Same as ``_seed_v2_success_with_v1`` but the BaaS progress stays PENDING, so
    the durable poll enqueued by the rollback keeps the target parked at ONLINE_PUB."""
    _seed_v2_success_with_v1(world, progress_status="PENDING")


def _seed_v2_no_v1_artifact(world) -> None:
    """Seed V2 in SUCCESS but V1 has no artifact (cannot rollback)."""
    bot = _seed_base(world)
    _install_baas_for_rollback(world)

    vbid1 = _binding(world, device_id=f"{_VERIFY_UUID}-1", status="ACTIVE")
    obid1 = _binding(world, device_id=f"{_ONLINE_UUID}-1", status="RELEASED")
    vbid2 = _binding(world, device_id=f"{_VERIFY_UUID}-2", status="ACTIVE")
    obid2 = _binding(world, device_id=f"{_ONLINE_UUID}-2", status="ACTIVE")

    repo = world.get(BotPublishRepositoryProtocol)

    # V1: UPGRADED but NO config_artifact
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V1 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.UPGRADED, "version": 1, "env": "dev",
        "ext": {
            "binding": {"verify": vbid1, "online": obid1},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            # No config_artifact!
        },
    })

    # V2: SUCCESS
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V2 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 2, "env": "dev",
        "last_pub_id": _V1,
        "ext": {
            "binding": {"verify": vbid2, "online": obid2},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ARTIFACT,
        },
    })


def _seed_v1_draft(world) -> None:
    """Seed V1 in DRAFT state (not SUCCESS - cannot rollback)."""
    bot = _seed_base(world)
    _install_baas_for_rollback(world)

    vbid = _binding(world, device_id=_VERIFY_UUID, status="PENDING")

    world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "Draft Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.DRAFT, "version": 1, "env": "dev",
        "ext": {
            "binding": {"verify": vbid},
            "config_artifact": _ARTIFACT,
        },
    })


def _seed_missing_publish(world) -> None:
    """Just seed user - no publish record."""
    make_staff_user(world, user_id=_OWNER)


# ── assertions ───────────────────────────────────────────────────────────────


def _expect_publish_status(pid: int, status):
    def _check(response, world):  # noqa: ARG001
        repo = world.get(BotPublishRepositoryProtocol)
        record = repo.get_by_id(pid)
        assert record is not None, f"publish {pid} not found"
        assert record.status == status, f"expected {status}, got {record.status}"
    return _check


def _expect_v1_upgraded(response, world):  # noqa: ARG001
    """After rollback, V1 should be SUCCESS again."""
    repo = world.get(BotPublishRepositoryProtocol)
    v1 = repo.get_by_id(_V1)
    assert v1.status == PublishStatus.SUCCESS, f"V1 should be SUCCESS, got {v1.status}"


def _expect_v2_draft(response, world):  # noqa: ARG001
    """After rollback, V2 should be DRAFT."""
    repo = world.get(BotPublishRepositoryProtocol)
    v2 = repo.get_by_id(_V2)
    assert v2.status == PublishStatus.DRAFT, f"V2 should be DRAFT, got {v2.status}"


def _expect_online_binding_active(pid: int):
    """The publish's online device_binding should be ACTIVE (rollback re-activated it)."""
    def _check(response, world):  # noqa: ARG001
        repo = world.get(BotPublishRepositoryProtocol)
        record = repo.get_by_id(pid)
        assert record is not None, f"publish {pid} not found"
        online_binding_id = (record.ext or {}).get("binding", {}).get("online")
        assert online_binding_id, f"publish {pid} has no online binding: {record.ext}"
        binding = world.get(DeviceBindingRepository).get_by_id(online_binding_id)
        assert binding is not None, f"binding {online_binding_id} not found"
        assert binding.status == "ACTIVE", f"expected ACTIVE, got {binding.status}"
    return _check


def _expect_update_delivers_stored_card_a(response, world):  # noqa: ARG001
    """The rollback's BaaS ``/update`` payload must carry V1's STORED online
    channel overrides (card A), not the live channel table's card B."""
    update = next(
        c for c in _baas(world).calls_to("post") if c.args[0].endswith("/update")
    )
    delivered = update.kwargs["json"]["config"]["deploy_config"]["teclaw_bot_config"]
    accounts = delivered["engine_overrides"]["channels"]["dingding"]["accounts"]
    assert [a.get("card_template_id") for a in accounts] == [_CARD_A], (
        delivered["engine_overrides"]
    )
    assert delivered["engine_ext"]["stage"] == "release"


# ── can-rollback tests ─────────────────────────────────────────────────────────


@endpoint_test(
    method="GET", path=_CAN_ROLLBACK, scenario="happy_can_rollback",
    input=CaseInput(path_params={"publish_id": _V2}, headers=_HEADERS),
    seed=_seed_v2_success_with_v1,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"can_rollback": True}}),
)
def happy_can_rollback():
    """V2 is SUCCESS with a V1 UPGRADED predecessor → can_rollback=True."""


@endpoint_test(
    method="GET", path=_CAN_ROLLBACK, scenario="cannot_rollback_no_previous_version",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_v1_success,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"can_rollback": False}}),
)
def cannot_rollback_no_previous_version():
    """V1 is SUCCESS but has no last_pub_id → can_rollback=False."""


@endpoint_test(
    method="GET", path=_CAN_ROLLBACK, scenario="cannot_rollback_no_artifact",
    input=CaseInput(path_params={"publish_id": _V2}, headers=_HEADERS),
    seed=_seed_v2_no_v1_artifact,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"can_rollback": False}}),
)
def cannot_rollback_no_artifact():
    """V2 has V1 but V1 lacks config_artifact → can_rollback=False."""


@endpoint_test(
    method="GET", path=_CAN_ROLLBACK, scenario="cannot_rollback_not_success",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_v1_draft,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"can_rollback": False}}),
)
def cannot_rollback_not_success():
    """Publish is DRAFT (not SUCCESS) → can_rollback=False."""


@endpoint_test(
    method="GET", path=_CAN_ROLLBACK, scenario="error_not_found",
    input=CaseInput(path_params={"publish_id": 9999}, headers=_HEADERS),
    seed=_seed_missing_publish,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 404}),
)
def error_can_rollback_not_found():
    """Non-existent publish_id → 404 error."""


# ── rollback tests ─────────────────────────────────────────────────────────────


@endpoint_test(
    method="POST", path=_ROLLBACK, scenario="happy_rollback",
    input=CaseInput(path_params={"publish_id": _V2}, headers=_HEADERS),
    seed=_seed_v2_success_with_v1, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(
        # Regression for #162: the rollback endpoint enqueues the durable progress
        # poll on the TARGET, so the sync-case's single worker tick drives the
        # target (V1) ONLINE_PUB → SUCCESS with its online binding ACTIVE — no user
        # /sync polling. The rolled-back current version (V2) is left at DRAFT.
        _expect_publish_status(_V1, PublishStatus.SUCCESS),
        _expect_online_binding_active(_V1),
        _expect_v2_draft,
    ),
)
def happy_rollback():
    """Rollback V2 → target self-drives to SUCCESS via the durable queue."""


@endpoint_test(
    method="POST", path=_ROLLBACK, scenario="rollback_delivers_stored_online_channel_overrides",
    input=CaseInput(path_params={"publish_id": _V2}, headers=_HEADERS),
    seed=_seed_v2_success_with_v1_channel_overrides, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(
        # Regression for #168: the rollback delivery must overlay V1's STORED
        # online engine_overrides (card A) onto the artifact — not deliver the
        # raw artifact (no channels) nor re-fetch the live channel table (card B).
        _expect_update_delivers_stored_card_a,
        _expect_publish_status(_V1, PublishStatus.SUCCESS),
    ),
)
def rollback_delivers_stored_online_channel_overrides():
    """Rollback restores the target version's promoted DingTalk channel config
    (incl. card_template_id) from the stored per-stage slot."""


@endpoint_test(
    method="POST", path=_ROLLBACK, scenario="rollback_target_parks_at_online_pub_until_baas_terminal",
    input=CaseInput(path_params={"publish_id": _V2}, headers=_HEADERS),
    seed=_seed_v2_success_with_v1_pending, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(
        # BaaS still PENDING: the enqueued poll ran once and rescheduled, so the
        # target stays at ONLINE_PUB (it will drain once BaaS reports terminal).
        # This is the pre-#105 "stuck" shape — except now a durable driver exists,
        # so it is transient rather than permanent.
        _expect_publish_status(_V1, PublishStatus.ONLINE_PUB),
        _expect_v2_draft,
    ),
)
def rollback_target_parks_at_online_pub_until_baas_terminal():
    """Rollback with BaaS not yet terminal → target parked at ONLINE_PUB, poll pending."""


@endpoint_test(
    method="POST", path=_ROLLBACK, scenario="error_rollback_not_found",
    input=CaseInput(path_params={"publish_id": 9999}, headers=_HEADERS),
    seed=_seed_missing_publish,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def error_rollback_not_found():
    """Rollback non-existent publish → service error (can_rollback returns False)."""


@endpoint_test(
    method="POST", path=_ROLLBACK, scenario="error_rollback_invalid_status",
    input=CaseInput(path_params={"publish_id": _V1}, headers=_HEADERS),
    seed=_seed_v1_draft,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def error_rollback_invalid_status():
    """Rollback a DRAFT publish → service error (invalid status)."""


# ── per-stage channel restore on rollback (end-to-end #168 acceptance) ─────────

# V1 (rollback target) frozen artifact, last stamped at verify (canary); rollback
# must restamp it to the online (release) stage.
_ENRICHED_V1 = {
    **_ARTIFACT,
    "engine_ext": {"bot_id": _BOT_ID, "owner_id": _OWNER, "stage": "canary"},
}
# The online channel config V1 promoted (card-A), stored in its per-stage slot.
_STORED_ONLINE_CARD_A = {
    "channels": {"dingding": {"enabled": True,
                              "accounts": [{"client_id": "dt", "card_template_id": "card-A"}]}}
}


def _seed_rollback_stored_online_channel(world) -> None:
    """V1 UPGRADED (rollback target) carries a stored online slot with card-A; the
    LIVE channel table holds card-B (the post-change state being rolled away from).
    V2 SUCCESS is the current version. Rolling back V2 must deliver card-A, not card-B."""
    bot = _seed_base(world)
    _install_baas_for_rollback(world)

    from agentclaw.community.core.channel.services.repositories import ChannelRepository
    # Live table now holds card-B — rollback must NOT consult it (compose_stored).
    world.get(ChannelRepository).insert_channel(
        type="dingding", description=None, identity_id=_OWNER, bind_bot_id=_BOT_ID,
        config={"client_id": "dt", "card_template_id": "card-B"}, status="1", stage="online",
    )

    vbid1 = _binding(world, device_id=f"{_VERIFY_UUID}-1", status="ACTIVE")
    obid1 = _binding(world, device_id=f"{_ONLINE_UUID}-1", status="RELEASED")
    vbid2 = _binding(world, device_id=f"{_VERIFY_UUID}-2", status="ACTIVE")
    obid2 = _binding(world, device_id=f"{_ONLINE_UUID}-2", status="ACTIVE")

    repo = world.get(BotPublishRepositoryProtocol)
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V1 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.UPGRADED, "version": 1, "env": "dev",
        "ext": {
            "binding": {"verify": vbid1, "online": obid1},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ENRICHED_V1,
            "engine_overrides_by_stage": {"online": _STORED_ONLINE_CARD_A},
        },
    })
    repo.insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT_ID, "publish_bot_id": _BOT_ID,
        "name": "V2 Publish", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 2, "env": "dev", "last_pub_id": _V1,
        "ext": {
            "binding": {"verify": vbid2, "online": obid2},
            "publish": {"verify": _BAAS_PUB_ID, "online": _BAAS_PUB_ID},
            "config_artifact": _ENRICHED_V1,
        },
    })


def _delivered_update_artifact(world) -> dict:
    update = next(c for c in _baas(world).calls_to("post") if "/update" in c.args[0])
    return update.kwargs["json"]["config"]["deploy_config"]["teclaw_bot_config"]


def _expect_rollback_delivers_stored_card_a(response, world):  # noqa: ARG001
    art = _delivered_update_artifact(world)
    accounts = art["engine_overrides"]["channels"]["dingding"]["accounts"]
    # Delivered the STORED online card-A, never the live table's card-B.
    assert [a.get("card_template_id") for a in accounts] == ["card-A"], art["engine_overrides"]
    # Restamped from the stored canary stamp to the online (release) stage.
    assert art["engine_ext"]["stage"] == "release", art["engine_ext"]


@endpoint_test(
    method="POST", path=_ROLLBACK, scenario="rollback_restores_stored_online_channel",
    input=CaseInput(path_params={"publish_id": _V2}, headers=_HEADERS),
    seed=_seed_rollback_stored_online_channel, drain_background=True,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_expect_rollback_delivers_stored_card_a,),
)
def rollback_restores_stored_online_channel():
    """Rolling back V2 → V1 composes V1's STORED online channel overrides (card-A)
    onto the delivered artifact and restamps engine_ext.stage=release; the live
    channel table's post-change card-B is never consulted. End-to-end proof of the
    #168 fix on the real BaaS /update payload."""