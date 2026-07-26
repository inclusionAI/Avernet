"""End-to-end write tests for the resources file routes on a teclaw bot.

teclaw now forwards every write **per-file** to the engine (like arca): upload →
``/api/v1/file/upload``, delete → ``/api/v1/file/remove``, mkdir → a ``.keep``
upload. The running container owns its files — the backend keeps no metadata
mirror of them. These pin that the write succeeds through the device-fs boundary.

Seam: the two MockSeam ``HttpClient``s — the BAAS-qualified GET (serves both
``ws-info`` and ``http-info`` for ``invoke_http``) and the general POST (the
container engine call). ``invoke_http`` / ``get_http_info`` and the real binding
lookup run for real — no instance is mocked out.
"""
from __future__ import annotations

from typing import Annotated

import httpx

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_BAAS, QUALIFIER_GENERAL
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_arca_device
from tests.community.framework import CaseInput, ExpectSuccess, endpoint_test
from tests.community.framework.endpoint_helpers import http_envelope_response


_OWNER = "u_teclaw_write"
_ENGINE = "moltis"
_CSV = b"col1,col2\n1,2\n"
# device_id matches what BAAS ws-info reports as paas_device_id so the composed
# conn_info wires the engine invoke-http URL to the right host path.
_TECLAW_DEVICE_ID = "BOT-write-1"


def _stub_baas_ws_info(world) -> None:
    """Stub the BAAS-qualified HttpClient's GET (one merged envelope).

    Routes: DeviceContextResolver → TeclawConnInfoBuilder → ``get_ws_info`` builds
    the conn_info; then each write goes ``device_fs.write/delete`` →
    ``BaasService.invoke_http`` → ``get_http_info``. Both hit the same BAAS GET, so
    the envelope carries the ws-info fields AND the ``http_url`` http-info field.
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


def _stub_engine_ok(world) -> None:
    """Stub the general HttpClient POST (the container engine call) → 200.

    The teclaw write path POSTs to the engine through the general ``HttpClient``;
    ``write_file`` raises on non-2xx, so a 200 here means the per-file forward
    succeeded."""
    world.get(Annotated[HttpClient, QUALIFIER_GENERAL]).set_response(
        "post",
        httpx.Response(
            200,
            json={"success": True},
            request=httpx.Request("POST", "http://engine/api/v1/file"),
        ),
    )


def _seed_teclaw_bot(world, *, bot_id: str) -> None:
    make_staff_user(world, user_id=_OWNER)
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=_TECLAW_DEVICE_ID,
        device_provider="teclaw", env="dev", device_props={}, status="ACTIVE",
        apply_reason="test seed", applied_by=_OWNER,
    )
    world.get(BotRepository).insert({
        "bot_id": bot_id, "bot_name": f"Bot {bot_id}", "owner_id": _OWNER,
        "owner_name": _OWNER, "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "user", "creator_id": _OWNER,
        "active_engine": _ENGINE, "device_id": _TECLAW_DEVICE_ID,
        # DeviceContextResolver JOINs ac_bots.binding_id == ac_entity_device_binding.id
        "binding_id": binding_id,
    })
    _stub_baas_ws_info(world)
    _stub_engine_ok(world)


# ── teclaw upload forwards to the engine ─────────────────────────────

def _assert_upload_paths(response, world) -> None:
    body = response.json()
    assert body.get("success") is True, body
    # absolute_path is the logic view (workspace/<rel>) — uniform across providers,
    # display-only (frontend "copy path" button).
    item = body["uploaded"][0]
    assert item["absolute_path"] == "workspace/report.csv", item
    assert item["path"] == "report.csv", item


@endpoint_test(
    method="POST",
    path="/api/resources/files/upload",
    scenario="happy_teclaw_upload_forwards",
    input=CaseInput(
        query_params={"bot_id": "bot_teclaw_up"},
        headers={"x-user-id": _OWNER},
        files=[("files", ("report.csv", _CSV))],
    ),
    seed=lambda world: _seed_teclaw_bot(world, bot_id="bot_teclaw_up"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_upload_paths,),
)
def upload_teclaw_forwards():
    """Uploading to a teclaw bot forwards per-file to the engine."""


# ── teclaw mkdir forwards a .keep upload ─────────────────────────────

@endpoint_test(
    method="POST",
    path="/api/resources/files/mkdir",
    scenario="happy_teclaw_mkdir_forwards",
    input=CaseInput(
        query_params={"bot_id": "bot_teclaw_mkdir"},
        headers={"x-user-id": _OWNER},
        form_data={"path": "newdir"},
    ),
    seed=lambda world: _seed_teclaw_bot(world, bot_id="bot_teclaw_mkdir"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def mkdir_teclaw_forwards():
    """mkdir on a teclaw bot uploads a .keep via the engine."""


# ── teclaw delete forwards to the engine ─────────────────────────────

@endpoint_test(
    method="DELETE",
    path="/api/resources/files",
    scenario="happy_teclaw_delete_forwards",
    input=CaseInput(
        query_params={"path": "docs/a.md", "bot_id": "bot_teclaw_del"},
        headers={"x-user-id": _OWNER},
    ),
    seed=lambda world: _seed_teclaw_bot(world, bot_id="bot_teclaw_del"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def delete_teclaw_forwards():
    """Deleting a teclaw file forwards a per-file remove to the engine."""


# ── arca upload is unchanged by the teclaw write path (regression) ───

def _seed_arca_bot(world) -> None:
    make_staff_user(world, user_id=_OWNER)
    # make_active_arca_device seeds a local-provider binding (see factory
    # docstring) so the resolver routes through LocalConnInfoBuilder and the
    # dispatcher builds LocalDeviceFileSystem — the FileService write lands on
    # disk, the arca path this regression pins.
    binding_id = make_active_arca_device(world, owner_id=_OWNER, device_id="arca_dev_w")
    world.get(BotRepository).insert({
        "bot_id": "bot_arca_up", "bot_name": "Bot arca", "owner_id": _OWNER,
        "owner_name": _OWNER, "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "user", "creator_id": _OWNER,
        "active_engine": "openclaw", "device_id": "arca_dev_w",
        # DeviceContextResolver JOINs ac_bots.binding_id == ac_entity_device_binding.id
        "binding_id": binding_id,
    })


@endpoint_test(
    method="POST",
    path="/api/resources/files/upload",
    scenario="arca_upload_unchanged",
    input=CaseInput(
        query_params={"bot_id": "bot_arca_up"},
        headers={"x-user-id": _OWNER},
        files=[("files", ("report.csv", _CSV))],
    ),
    seed=_seed_arca_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def upload_arca_unchanged():
    """arca upload still writes the live FS through the device-fs boundary."""
