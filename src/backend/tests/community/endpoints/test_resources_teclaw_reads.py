"""End-to-end read tests for the resources file routes on a teclaw bot.

Before this feature, ``TeclawDeviceFileSystem`` raised ``NotImplementedError`` on
``read_file``/``list_dir``, so list/preview/download of a teclaw bot's files
500'd. These pin the wired path: the file router selects the non-arca/non-baas
branch from the bot's teclaw device binding, resolves the device fs via
``DeviceContextResolver`` (driven to the teclaw provider through the real BAAS
ws-info boundary), and the teclaw fs serves the read by calling the running
container's engine file API through ``BaasService.invoke_http`` (the same
authenticated invoke-http transport the baas provider uses).

Seam: the substituted boundaries are the two MockSeam ``HttpClient``s — the
BAAS-qualified one's GET (serves the resolver's ``/ws-info`` **and**
``invoke_http``'s ``/http-info`` control-plane calls via one merged envelope) and
the general one's POST (the container engine read). ``invoke_http`` /
``get_http_info`` and the real binding lookup run for real — no instance is
mocked out (see ``tests/framework/test_no_mock_on_world_get``).
"""
from __future__ import annotations

from typing import Annotated

import httpx

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.plugin_api.http_client import QUALIFIER_BAAS, QUALIFIER_GENERAL, HttpClient
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test
from tests.community.framework.endpoint_helpers import http_envelope_response


_OWNER = "u_teclaw_read"
_DEVICE_ID = "teclaw_dev_read"
_ENGINE = "moltis"

# Teclaw conn_info the BaaS ws-info response will yield via TeclawConnInfoBuilder
# → build_baas_conn_info. device_provider="teclaw" routes the dispatcher to
# TeclawDeviceFileSystem, and the BaaS-transport fields build the engine
# invoke-http URL (the engine call itself is stubbed via the general HttpClient).
_TECLAW_DEVICE_ID = "BOT-read-1"
_TECLAW_ENGINE_PORT = 20003


def _stub_baas_ws_info(world) -> None:
    """Stub the BAAS-qualified HttpClient's GET (one merged envelope for both calls).

    The HTTP endpoints route through ``DeviceContextResolver.resolve_for_bot`` →
    ``TeclawConnInfoBuilder.build`` → ``baas_service.get_ws_info`` → BAAS
    ``GET /api/v1/bots/{device_id}/ws-info`` to build the conn_info. Then the read
    goes through ``BaasService.invoke_http`` → ``get_http_info`` → BAAS
    ``GET /api/v1/bots/{device_id}/http-info``. Both hit the same BAAS HttpClient
    GET (``set_response`` is method-keyed), so the envelope carries BOTH the
    ws-info fields (``ws_url``/``expires_at``, read by ``get_ws_info``) and the
    http-info field (``http_url``, read by ``get_http_info``); ``token``/``target``
    are shared. This keeps ``get_http_info`` + the real binding lookup exercised
    rather than mocking ``invoke_http`` out.
    """
    world.get(Annotated[HttpClient, QUALIFIER_BAAS]).set_response(
        "get",
        http_envelope_response({
            "ws_url": "ws://localhost:8890/api/openclaw/ws",
            "http_url": "http://localhost:8890/invoke-http",
            "token": "test-token",
            "target": _TECLAW_DEVICE_ID,
            "expires_at": 0,
        }),
    )


def _seed_teclaw_bot(world, *, bot_id: str) -> None:
    make_staff_user(world, user_id=_OWNER)
    # Teclaw device binding → DeviceContextResolver routes to teclaw branch, so the
    # file router takes the device-fs (non-arca/non-baas) branch.
    # ``device_id`` matches what BAAS ws-info reports as paas_device_id so the
    # composed conn_info wires the engine invoke-http URL to the right host path.
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id=_TECLAW_DEVICE_ID,
        device_provider="teclaw",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason="test seed",
        applied_by=_OWNER,
    )
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "user",
            "creator_id": _OWNER,
            "active_engine": _ENGINE,
            "device_id": _TECLAW_DEVICE_ID,
            # DeviceContextResolver JOINs ac_bots.binding_id == ac_entity_device_binding.id
            "binding_id": binding_id,
        }
    )
    _stub_baas_ws_info(world)


def _engine_response(*, json=None, content=b"") -> httpx.Response:
    """An engine invoke-http response. A ``request`` must be attached or
    ``raise_for_status()`` errors (real httpx responses always carry one)."""
    return httpx.Response(
        200,
        json=json,
        content=content if json is None else None,
        request=httpx.Request("POST", "http://engine/api/file"),
    )


def _stub_engine(world, response: httpx.Response) -> None:
    """Stub the general HttpClient's POST (the container engine read).

    ``invoke_http`` resolves the URL+token via ``get_http_info`` (real, served by
    the merged BAAS GET stub) then POSTs to the container through the general
    ``HttpClient`` — that POST is the boundary substituted here."""
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response("post", response)


# ============================================================
# list  (GET /api/resources/files)
# ============================================================

def _seed_list(world) -> None:
    _seed_teclaw_bot(world, bot_id="bot_teclaw_ls")
    _stub_engine(world, _engine_response(json={"data": {"files": [
        {"name": "report.csv", "is_dir": False, "size": 12},
        {"name": "sub", "is_dir": True},
    ]}}))


def _assert_listed(response, world) -> None:
    items = response.json().get("items", [])
    by_name = {i["name"]: i for i in items}
    assert "report.csv" in by_name, response.json()
    # absolute_path is the logic view (workspace/<rel>) — uniform across providers.
    assert by_name["report.csv"]["absolute_path"] == "workspace/report.csv", by_name["report.csv"]


@endpoint_test(
    method="GET",
    path="/api/resources/files",
    scenario="happy_teclaw_list_via_engine_api",
    input=CaseInput(
        query_params={"bot_id": "bot_teclaw_ls"},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_list,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_listed,),
)
def list_teclaw_files_returns_200():
    """Listing a teclaw bot's files returns 200 (served by the engine API),
    not the prior NotImplementedError 500."""


# ============================================================
# preview  (GET /api/resources/files/preview)
# ============================================================

def _seed_preview(world) -> None:
    _seed_teclaw_bot(world, bot_id="bot_teclaw_pv")
    _stub_engine(world, _engine_response(content=b"hello teclaw"))


@endpoint_test(
    method="GET",
    path="/api/resources/files/preview",
    scenario="happy_teclaw_preview_via_engine_api",
    input=CaseInput(
        query_params={"path": "notes.txt", "bot_id": "bot_teclaw_pv"},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_preview,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def preview_teclaw_file_returns_200():
    """Previewing a teclaw bot's text file returns its content (200)."""


# ============================================================
# download  (GET /api/resources/files/download)
# ============================================================

def _seed_download(world) -> None:
    _seed_teclaw_bot(world, bot_id="bot_teclaw_dl")
    _stub_engine(world, _engine_response(content=b"col1,col2\n1,2\n"))


def _assert_downloaded(response, world) -> None:
    assert response.content == b"col1,col2\n1,2\n", response.content


@endpoint_test(
    method="GET",
    path="/api/resources/files/download",
    scenario="happy_teclaw_download_via_engine_api",
    input=CaseInput(
        query_params={"path": "data/report.csv", "bot_id": "bot_teclaw_dl"},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_download,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_downloaded,),
)
def download_teclaw_file_returns_200():
    """Downloading a teclaw bot's file streams its bytes (200)."""


# ============================================================
# list with publish_id  — read from the verify/online STAGE binding
# ============================================================
#
# A service bot exists in multiple states; each deployed stage (verify/online)
# has its OWN device binding, stored in the publish record's
# ``ext.binding[stage]`` — NOT on ``ac_bots.binding_id`` (that's the draft). With
# ``publish_id`` set, the read must resolve the stage binding via
# ``DeviceContextResolver.resolve_for_binding``, never ``resolve_for_bot``
# (draft). The seed deliberately wires the bot's draft ``binding_id`` to a bogus
# id so a 200 can ONLY come from the stage binding path.

_PUBLISH_ID = 1  # first ac_bot_publish row in the fresh per-case DB


def _seed_teclaw_publish_bot(world, *, bot_id: str, stage: str = "online") -> int:
    make_staff_user(world, user_id=_OWNER)
    # The published (verify/online) container's binding — what the read must use.
    stage_bid = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id=_TECLAW_DEVICE_ID,
        device_provider="teclaw",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason="stage seed",
        applied_by=_OWNER,
    )
    bot = world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "user",
            "creator_id": _OWNER,
            "active_engine": _ENGINE,
            "device_id": _TECLAW_DEVICE_ID,
            # Bogus draft binding: resolve_for_bot(draft) would fail. A 200 proves
            # the read used the publish stage binding instead.
            "binding_id": 9_999_999,
        }
    )
    world.get(BotPublishRepositoryProtocol).insert(
        {
            "source_bot_pk": bot["id"],
            "source_bot_id": bot_id,
            "publish_bot_id": bot_id,
            "name": f"Pub {bot_id}",
            "owner_id": _OWNER,
            "permission_owner": _OWNER,
            "status": PublishStatus.SUCCESS,
            "version": 1,
            "env": "dev",
            "ext": {"binding": {stage: stage_bid}},
        }
    )
    _stub_baas_ws_info(world)
    return stage_bid


def _seed_publish_list(world) -> None:
    _seed_teclaw_publish_bot(world, bot_id="bot_teclaw_pub_ls")
    _stub_engine(world, _engine_response(json={"data": {"files": [
        {"name": "report.csv", "is_dir": False, "size": 12},
    ]}}))


@endpoint_test(
    method="GET",
    path="/api/resources/files",
    scenario="happy_teclaw_publish_list_via_stage_binding",
    input=CaseInput(
        query_params={"bot_id": "bot_teclaw_pub_ls", "publish_id": str(_PUBLISH_ID)},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_publish_list,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_listed,),
)
def list_teclaw_published_files_via_stage_binding_returns_200():
    """With publish_id set, a teclaw bot's files list from the publish stage
    binding (ext.binding.online), not the draft binding."""


def _seed_publish_download(world) -> None:
    _seed_teclaw_publish_bot(world, bot_id="bot_teclaw_pub_dl")
    _stub_engine(world, _engine_response(content=b"col1,col2\n1,2\n"))


@endpoint_test(
    method="GET",
    path="/api/resources/files/download",
    scenario="happy_teclaw_publish_download_via_stage_binding",
    input=CaseInput(
        query_params={"path": "data/report.csv", "bot_id": "bot_teclaw_pub_dl",
                      "publish_id": str(_PUBLISH_ID)},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_publish_download,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_downloaded,),
)
def download_teclaw_published_file_via_stage_binding_returns_200():
    """With publish_id set, a teclaw bot's file downloads from the publish stage
    binding, not the draft binding."""


def _seed_publish_preview(world) -> None:
    _seed_teclaw_publish_bot(world, bot_id="bot_teclaw_pub_pv")
    _stub_engine(world, _engine_response(content=b"hello teclaw"))


@endpoint_test(
    method="GET",
    path="/api/resources/files/preview",
    scenario="happy_teclaw_publish_preview_via_stage_binding",
    input=CaseInput(
        query_params={"path": "notes.txt", "bot_id": "bot_teclaw_pub_pv",
                      "publish_id": str(_PUBLISH_ID)},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_publish_preview,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def preview_teclaw_published_file_via_stage_binding_returns_200():
    """With publish_id set, a teclaw bot's text file previews from the publish
    stage binding, not the draft binding."""


# ============================================================
# publish_id with no resolvable stage binding → 400 (the fallback raise)
# ============================================================

def _seed_publish_no_binding(world) -> None:
    """Publish record exists but ext.binding is empty → neither arca sandbox nor
    a teclaw stage ctx resolves, so the read raises 400."""
    make_staff_user(world, user_id=_OWNER)
    world.get(BotRepository).insert({
        "bot_id": "bot_teclaw_pub_nob", "bot_name": "No binding", "owner_id": _OWNER,
        "owner_name": _OWNER, "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "user", "creator_id": _OWNER,
        "active_engine": _ENGINE, "device_id": _TECLAW_DEVICE_ID, "binding_id": 9_999_999,
    })
    world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": 1, "source_bot_id": "bot_teclaw_pub_nob",
        "publish_bot_id": "bot_teclaw_pub_nob", "name": "Pub nob", "owner_id": _OWNER,
        "permission_owner": _OWNER, "status": PublishStatus.SUCCESS, "version": 1,
        "env": "dev", "ext": {"binding": {}},
    })
    _stub_baas_ws_info(world)


@endpoint_test(
    method="GET",
    path="/api/resources/files",
    scenario="teclaw_publish_no_stage_binding_returns_400",
    input=CaseInput(
        query_params={"bot_id": "bot_teclaw_pub_nob", "publish_id": str(_PUBLISH_ID)},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_publish_no_binding,
    expect=ExpectError(status=400),
)
def list_teclaw_publish_no_stage_binding_returns_400():
    """publish_id whose record has no stage binding → 400 (not a draft fallback)."""
