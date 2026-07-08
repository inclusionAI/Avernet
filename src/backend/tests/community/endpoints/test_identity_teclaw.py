"""End-to-end tests for the identity routes on a teclaw bot.

teclaw forwards identity reads/writes **per-file** to the engine under the
``/identity`` namespace: the router appends no path — it passes the bare
filename under ``IDENTITY_NS`` and the dispatcher's mapper turns it into
``/identity/<file>``. PUT → ``/api/v1/file/upload``, GET → ``/api/v1/file/read``.

Seam: the two MockSeam ``HttpClient``s — the BAAS-qualified GET (ws-info +
http-info for ``invoke_http``) and the general POST (the container engine call).
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
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS, QUALIFIER_GENERAL
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectSuccess, endpoint_test
from tests.community.framework.endpoint_helpers import http_envelope_response


_OWNER = "u_teclaw_identity"
_ENGINE = "moltis"  # non-openclaw → no AGENTS.md re-sync
_BOT = "bot_teclaw_idn"
_FILE = "MEMORY.md"
_CONTENT = "remember: be concise."
_TECLAW_DEVICE_ID = "BOT-identity-1"


def _stub_baas_ws_info(world) -> None:
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


def _seed_teclaw_bot(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=_TECLAW_DEVICE_ID,
        device_provider="teclaw", env="dev", device_props={}, status="ACTIVE",
        apply_reason="test seed", applied_by=_OWNER,
    )
    world.get(BotRepository).insert({
        "bot_id": _BOT, "bot_name": "Bot idn", "owner_id": _OWNER,
        "owner_name": _OWNER, "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "user", "creator_id": _OWNER,
        "active_engine": _ENGINE, "device_id": _TECLAW_DEVICE_ID,
        "binding_id": binding_id,
    })
    _stub_baas_ws_info(world)


# ── PUT: identity write forwards a per-file upload under /identity ────

def _seed_write(world) -> None:
    _seed_teclaw_bot(world)
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response(
        "post",
        httpx.Response(
            200, json={"success": True},
            request=httpx.Request("POST", "http://engine/api/v1/file/upload"),
        ),
    )


@endpoint_test(
    method="PUT",
    path="/api/identity/staff/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_teclaw_identity_write_forwards",
    input=CaseInput(
        path_params={"entity_id": _OWNER, "bot_id": _BOT, "file_type": _FILE},
        headers={"x-user-id": _OWNER},
        json_body={"content": _CONTENT},
    ),
    seed=_seed_write,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def identity_teclaw_write_forwards():
    """Writing a teclaw identity file forwards an upload to /identity/<file>."""


# ── GET: identity read forwards a per-file read under /identity ───────

def _seed_read(world) -> None:
    _seed_teclaw_bot(world)
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response(
        "post",
        httpx.Response(
            200, content=_CONTENT.encode("utf-8"),
            request=httpx.Request("POST", "http://engine/api/v1/file/read"),
        ),
    )


@endpoint_test(
    method="GET",
    path="/api/identity/staff/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_teclaw_identity_read_forwards",
    input=CaseInput(
        path_params={"entity_id": _OWNER, "bot_id": _BOT, "file_type": _FILE},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_read,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "content": _CONTENT}),
)
def identity_teclaw_read_forwards():
    """Reading a teclaw identity file forwards a read to /identity/<file>."""


# ── GET with publish_id: read from the verify/online STAGE binding ────
#
# The stage binding lives in the publish record's ext.binding[stage], not on
# ac_bots.binding_id (draft). With publish_id set the read must resolve that
# stage binding (resolve_for_binding), never the draft. The bot's draft
# binding_id is bogus so a 200 proves the stage binding path was taken.

_PUBLISH_ID = 1  # first ac_bot_publish row in the fresh per-case DB
_PUB_BOT = "bot_teclaw_idn_pub"


def _seed_read_publish(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    stage_bid = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=_TECLAW_DEVICE_ID,
        device_provider="teclaw", env="dev", device_props={}, status="ACTIVE",
        apply_reason="stage seed", applied_by=_OWNER,
    )
    bot = world.get(BotRepository).insert({
        "bot_id": _PUB_BOT, "bot_name": "Bot idn pub", "owner_id": _OWNER,
        "owner_name": _OWNER, "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "user", "creator_id": _OWNER,
        "active_engine": _ENGINE, "device_id": _TECLAW_DEVICE_ID,
        "binding_id": 9_999_999,  # bogus draft → 200 can only come from stage binding
    })
    world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": bot["id"], "source_bot_id": _PUB_BOT, "publish_bot_id": _PUB_BOT,
        "name": "Pub idn", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 1, "env": "dev",
        "ext": {"binding": {"online": stage_bid}},
    })
    _stub_baas_ws_info(world)
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response(
        "post",
        httpx.Response(
            200, content=_CONTENT.encode("utf-8"),
            request=httpx.Request("POST", "http://engine/api/v1/file/read"),
        ),
    )


@endpoint_test(
    method="GET",
    path="/api/identity/staff/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_teclaw_identity_read_publish_stage_binding",
    input=CaseInput(
        path_params={"entity_id": _OWNER, "bot_id": _PUB_BOT, "file_type": _FILE},
        query_params={"publish_id": str(_PUBLISH_ID)},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_read_publish,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "content": _CONTENT}),
)
def identity_teclaw_read_publish_stage_binding():
    """With publish_id set, a teclaw identity file reads from the publish stage
    binding (ext.binding.online), not the draft binding."""
