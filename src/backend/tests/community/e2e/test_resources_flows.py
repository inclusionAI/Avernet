"""resources e2e business flows (real endpoints, LOCAL+SQLITE).

RESOURCES_LIFECYCLE_FLOWS (tests/_flows/resources/api_lifecycle.py) is the
single source of truth for this runner and the E3 coverage guard.

LOCAL device_provider defaults to non-arca (no Arca binding in fresh DB), so
file_router falls through to the daas branch (real local filesystem via
FileService). bot_data_dir lands under
~/.aidesktop/aidesktop_dev/bolt_data/staff_<user>/<bot>/openclaw/workspace/.

The file round-trip below issues real Form (mkdir) and multipart (upload)
requests via TestClient — FlowStep body= is JSON-encoded only, so the file
lifecycle's middle steps (mkdir, upload, preview, delete) can't be modeled
as FlowSteps. Flow 3 (resources-file-daas-list) only pins the list envelope.
"""
from __future__ import annotations

import io
import time

import pytest

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from tests.community._flows.resources.api_lifecycle import RESOURCES_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner import run_flow


def _seed_local_bot(world, *, bot_id: str, owner_id: str = "e2e_user") -> None:
    """Phase 2 后 DeviceContextResolver 强校验 active binding;e2e flow 走的
    LOCAL daas 文件分支(无 arca binding 时 fall through)同样需要 resolver
    返回一个 DeviceContext。seed 一个 local provider binding + bot 行(其
    binding_id 指向该 binding),让 resolver.resolve_for_bot 通过。

    注意:**故意不写 device_props={"adapter_port": ...}** —
    ``DeviceFilesystemDispatcher._build_binding_ctx`` 看到没有 adapter_port
    就走 pathlib fallback (binding_ctx=None → baas_mode=False),否则
    LocalDeviceFileSystem.mkdir 会走 BaaS HTTP → LocalHttpClient.get 抛
    HttpNotConfiguredError。e2e LOCAL daas 路径正好需要 pathlib fs。
    """
    binding_repo = world.get(DeviceBindingRepository)
    binding_id = binding_repo.insert_binding(
        entity_id=owner_id,
        entity_type="staff",
        device_id=f"local_dev_{bot_id}",
        device_provider="local",
        env="dev",
        device_props={},  # 故意不带 adapter_port,见 docstring
        status="ACTIVE",
        apply_reason="e2e seed",
        applied_by=owner_id,
    )
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": owner_id,
            "owner_name": owner_id,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": owner_id,
            "entity_type": "user",
            "creator_id": owner_id,
            "active_engine": "openclaw",
            "device_id": f"local_dev_{bot_id}",
            # DeviceContextResolver JOINs ac_bots.binding_id == ac_entity_device_binding.id
            "binding_id": binding_id,
        }
    )


# Flow-name → list of bot_ids the flow's steps hit. e2e flows do NOT have a
# seed hook on FlowCase; seed here based on the case name so resolver finds
# a binding before run_flow drives the request.
_FLOW_SEED_BOTS: dict[str, list[str]] = {
    "resources-url-resource-crud": ["bot_e2e_res"],
    "resources-node-resource-crud": ["bot_e2e_res_n"],
    "resources-file-daas-list": ["bot_e2e_res_f"],
}


def _mkdir_form(client, *, bot_id, path):
    """mkdir via fastapi.Form — FileActionResponse {success, message}.

    The endpoint takes ?bot_id= (Query) + path (Form). entity_id/entity_type
    are not declared params; _resolve_params uses ctx.user_id (from x-user-id
    header) as owner_id.
    """
    return client.post(
        f"/api/resources/files/mkdir?bot_id={bot_id}",
        data={"path": path},
    )


def _upload_multipart(client, *, bot_id, target_dir, filename, content):
    """Multipart upload — FileUploadResponse {success, uploaded: [FileItem], errors}.

    The endpoint takes ?bot_id= and ?path= (target dir relative to workspace
    root) as Query params, files as multipart. NOT `parent_path`.
    """
    return client.post(
        f"/api/resources/files/upload?bot_id={bot_id}&path={target_dir}",
        files={"files": (filename, io.BytesIO(content), "text/plain")},
    )


@pytest.mark.e2e
@pytest.mark.parametrize("case", RESOURCES_LIFECYCLE_FLOWS, ids=lambda c: c.name)
def test_resources_flow(case, app_with_testing_modules, world):
    """Parametrize over the 3 FlowCases. The file-daas flow pins only the
    file_router list envelope (success/path/items); mkdir + upload + preview +
    delete can't be FlowSteps (Form/multipart) and live in the round-trip below.
    """
    for bot_id in _FLOW_SEED_BOTS.get(case.name, []):
        _seed_local_bot(world, bot_id=bot_id)
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None
    # Belt-and-suspenders: Flow 2 extracts node_resource_id then re-extracts
    # the first listed id; if the chain works, they must be equal.
    if "node_resource_id" in ctx and "first_listed_id" in ctx:
        assert ctx["first_listed_id"] == ctx["node_resource_id"]


@pytest.mark.e2e
def test_resources_file_round_trip(app_with_testing_modules, world):
    """Multipart upload + list + preview + delete round-trip on LOCAL daas
    filesystem. Not in FlowCase: mkdir is Form, upload is multipart — both
    incompatible with FlowStep body=.

    bot_id and the mkdir dir name include a unique suffix (ns-time) so
    the host filesystem from prior pytest runs doesn't 409 us on mkdir, and
    so the list endpoint sees only this test's file. The workspace dir lands
    under ~/.aidesktop/aidesktop_dev/bolt_data/staff_e2e_user/<bot>/openclaw/
    workspace/<dir>/ — a fresh per-bot subtree on every invocation.
    """
    from fastapi.testclient import TestClient
    client = TestClient(app_with_testing_modules, headers={"x-user-id": "e2e_user"})

    # Per-run unique suffix avoids cross-run collisions on the shared host fs.
    suffix = f"{time.time_ns()}"
    bot_id = f"bot_e2e_round_trip_{suffix}"
    parent = f"e2e_round_trip_dir_{suffix}"

    # seed local binding + bot 行 — Phase 2 后 resolver 强校验 active binding,
    # 无 seed 会在第一步 mkdir 拿不到 DeviceContext 直接 DeviceNotBoundError。
    _seed_local_bot(world, bot_id=bot_id, owner_id="e2e_user")

    # mkdir — FileActionResponse (Form `path=`, Query `bot_id=`)
    r = _mkdir_form(client, bot_id=bot_id, path=parent)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    # upload — multipart; FileUploadResponse envelope (`uploaded`, not `data`)
    # Target dir goes via Query ?path=, NOT Form parent_path.
    payload = b"E2E daas content\n"
    r = _upload_multipart(
        client, bot_id=bot_id, target_dir=parent,
        filename="e2e_test.txt", content=payload,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert len(body["uploaded"]) == 1, body
    assert body["uploaded"][0]["name"] == "e2e_test.txt"

    # list — FileListResponse envelope (`items`, not `data`); Query ?path= for dir
    r = client.get(
        f"/api/resources/files?bot_id={bot_id}&path={parent}"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(item.get("name") == "e2e_test.txt" for item in items), items

    # preview — PreviewResponse {success, data: {content, size}}
    r = client.get(
        f"/api/resources/files/preview?bot_id={bot_id}&path={parent}/e2e_test.txt"
    )
    assert r.status_code == 200, r.text
    assert payload.decode() in r.json()["data"]["content"]

    # delete — FileActionResponse
    r = client.delete(
        f"/api/resources/files?bot_id={bot_id}&path={parent}/e2e_test.txt"
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
