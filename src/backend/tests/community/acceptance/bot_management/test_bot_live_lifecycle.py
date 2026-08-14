"""Route-B acceptance: bot_management live lifecycle and provider facts.

The owner lifecycle creates a bot through the public API so coverage includes
Backend -> BaaS -> local OpenClaw allocation. The provider-fact test uses the
local SQL endpoint after backend boot to seed existing bindings for arca, baas
and teclaw; these are read branches for bot_management today, while true
create/restart lifecycle remains provider-owned by the device layer.
"""
from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from tests.community._flows.bot_management.api_lifecycle import BOT_MANAGEMENT_LIFECYCLE_FLOWS
from tests.community.acceptance._fixtures.live_personal_bot import (
    assert_success,
    create_live_personal_bot,
    fresh_id,
    wait_bot_ready,
)
from tests.community.framework.flow import FlowContext
from tests.community.framework.flow_runner_live import run_flow_live


def _flow_by_name(name: str):
    return next(c for c in BOT_MANAGEMENT_LIFECYCLE_FLOWS if c.name == name)


def _execute_local_sql(client: httpx.Client, statements: list[dict]) -> dict:
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post("/local/sql/execute", json={"statements": statements})
        if response.status_code == 200:
            return response.json()
        last_response = response
        if "SQL statements in progress" not in response.text:
            break
        time.sleep(0.2 * (attempt + 1))
    assert last_response is not None
    assert last_response.status_code == 200, last_response.text
    return last_response.json()


def _seed_provider_bot(
    client: httpx.Client,
    *,
    provider: str,
    owner_id: str,
    bot_id: str,
    bot_name: str,
    active_engine: str = "openclaw",
) -> None:
    """Seed one active bot + device binding in the live local backend DB."""
    device_id = f"{provider}_device_{bot_id}"
    device_props = {
        "provider": provider,
        "sandbox_id": f"sandbox_{bot_id}",
        "bot_uuid": device_id,
        "bolt_id": bot_id,
        "tenant": "default",
    }
    if provider == "teclaw":
        active_engine = "teclaw"
    statements = [
        {
            "sql": (
                "INSERT INTO ac_entity_device_binding ("
                "entity_id, entity_type, device_id, device_provider, env, device_props, "
                "status, apply_reason, applied_by, gmt_create, gmt_modified"
                ") VALUES ("
                ":entity_id, 'staff', :device_id, :device_provider, :env, :device_props, "
                "'ACTIVE', 'singlebox provider branch seed', :owner_id, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            "params": {
                "entity_id": owner_id,
                "device_id": device_id,
                "device_provider": provider,
                "device_props": json.dumps(device_props),
                "env": "dev",
                "owner_id": owner_id,
            },
        },
        {
            "sql": (
                "INSERT INTO ac_bots ("
                "bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id, "
                "owner_name, engine_types, active_engine, status, binding_id, device_id, "
                "gmt_create, gmt_modified, is_delete, public, ext, env, bot_type, "
                "call_type, caller_config_revision"
                ") VALUES ("
                ":bot_id, :bot_name, :bot_desc, :owner_id, 'staff', :owner_id, :owner_id, "
                ":owner_id, :engine_types, :active_engine, 'ACTIVE', "
                "(SELECT id FROM ac_entity_device_binding WHERE device_id = :device_id), "
                ":device_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '0', :ext, :env, 'personal', "
                "'owner', 0"
                ")"
            ),
            "params": {
                "bot_id": bot_id,
                "bot_name": bot_name,
                "bot_desc": f"singlebox seeded {provider} provider bot",
                "owner_id": owner_id,
                "engine_types": json.dumps(["openclaw", "claude_code", "teclaw"]),
                "active_engine": active_engine,
                "device_id": device_id,
                "ext": json.dumps({"singlebox_provider_branch": provider}),
                "env": "dev",
            },
        },
    ]
    _execute_local_sql(client, statements)


def _seed_plain_bot(
    client: httpx.Client,
    *,
    owner_id: str,
    bot_id: str,
    bot_name: str,
    status: str,
    ext: dict | None = None,
    active_engine: str = "openclaw",
    bot_type: str = "personal",
    template_type: str | None = None,
    binding_id: int | None = None,
    device_id: str | None = None,
) -> None:
    _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_bots ("
                    "bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id, "
                    "owner_name, engine_types, active_engine, status, binding_id, device_id, "
                    "gmt_create, gmt_modified, is_delete, public, ext, env, bot_type, template_type, "
                    "call_type, caller_config_revision"
                    ") VALUES ("
                    ":bot_id, :bot_name, :bot_desc, :owner_id, 'staff', :owner_id, :owner_id, "
                    ":owner_id, :engine_types, :active_engine, :status, :binding_id, :device_id, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '0', :ext, :env, "
                    ":bot_type, :template_type, 'owner', 0"
                    ")"
                ),
                "params": {
                    "bot_id": bot_id,
                    "bot_name": bot_name,
                    "bot_desc": "singlebox data-init state seed",
                    "owner_id": owner_id,
                    "engine_types": json.dumps(["openclaw", "claude_code", "aicoding"]),
                    "active_engine": active_engine,
                    "status": status,
                    "ext": json.dumps(ext or {}),
                    "env": "dev",
                    "bot_type": bot_type,
                    "template_type": template_type,
                    "binding_id": binding_id,
                    "device_id": device_id,
                },
            }
        ],
    )


def _seed_template(client: httpx.Client, *, bot_id: str, template_config: dict) -> None:
    _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_templates (bot_id, ext, gmt_create, gmt_modified) "
                    "VALUES (:bot_id, :ext, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                "params": {
                    "bot_id": bot_id,
                    "ext": json.dumps(template_config),
                },
            }
        ],
    )


def _seed_desktop_binding(
    client: httpx.Client,
    *,
    owner_id: str,
    bot_id: str,
    device_id: str,
) -> int:
    result = _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_entity_device_binding ("
                    "entity_id, entity_type, device_id, device_provider, env, device_props, "
                    "status, apply_reason, applied_by, gmt_create, gmt_modified"
                    ") VALUES ("
                    ":owner_id, 'staff', :device_id, 'baas', :env, :device_props, "
                    "'ACTIVE', 'singlebox desktop merge seed', :owner_id, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                "params": {
                    "owner_id": owner_id,
                    "device_id": device_id,
                    "env": "dev",
                    "device_props": json.dumps(
                        {
                            "provider": "baas",
                            "bot_uuid": device_id,
                            "bolt_id": bot_id,
                        }
                    ),
                },
            }
        ],
    )
    return int(result["results"][0]["lastrowid"])


@pytest.mark.acceptance
def test_bot_management_live_owner_lifecycle(live_backend, acceptance_fs_root):
    """Create/update/read one personal bot through the real BaaS-backed path."""
    user_id = fresh_id("bot_mgmt_owner")
    ctx = FlowContext()
    ctx["user_id"] = user_id
    ctx["bot_name"] = fresh_id("BotMgmtLive")
    ctx["delete_bot_name"] = fresh_id("BotMgmtDelete")
    ctx["service_bot_name"] = fresh_id("BotMgmtService")

    run_flow_live(
        _flow_by_name("bot_management-live-owner-crud-baas-backed"),
        base_url=live_backend,
        fs_root=acceptance_fs_root,
        default_headers={"x-user-id": user_id},
        initial_context=ctx,
    )


@pytest.mark.acceptance
@pytest.mark.parametrize("provider", ["arca", "baas", "teclaw"])
def test_bot_management_seeded_provider_status_paths(live_backend, provider):
    """Existing arca/baas/teclaw provider facts are observable via bot APIs."""
    owner_id = fresh_id(f"bot_mgmt_{provider}")
    bot_id = fresh_id(f"bot_{provider}")
    bot_name = f"BotMgmt {provider} seeded"

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        _seed_provider_bot(
            client,
            provider=provider,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name=bot_name,
        )

        detail = client.get(f"/api/bots/{bot_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["success"] is True

        status = client.get(f"/api/bots/{bot_id}/status")
        assert status.status_code == 200, status.text
        status_body = status.json()
        assert status_body["success"] is True, status_body
        assert status_body["data"]["device_provider"] == provider, status_body
        assert status_body["data"]["is_ready"] is True, status_body

        work_dir = client.get(f"/api/bots/{bot_id}/work-dir")
        assert work_dir.status_code == 200, work_dir.text
        assert work_dir.json()["success"] is True

        config_dir = client.get(f"/api/bots/{bot_id}/config-dir")
        assert config_dir.status_code == 200, config_dir.text
        assert config_dir.json()["success"] is True

        search = client.post(
            "/api/bots/search",
            json={"owner_id": owner_id, "key": provider, "page": 1, "page_size": 10},
        )
        assert search.status_code == 200, search.text
        assert search.json()["success"] is True

        # Give the router/plugin hit recorder a tiny window to flush JSONL on
        # slower filesystems before the coverage script collects artifacts.
        time.sleep(0.1)


@pytest.mark.acceptance
def test_bot_management_data_init_state_gates(live_backend):
    """Cover data-init state transitions that do not invoke the long LLM path."""
    owner_id = fresh_id("bot_mgmt_datainit")
    pending_bot_id = fresh_id("bot_pending_init")
    completed_bot_id = fresh_id("bot_completed_init")
    in_progress_bot_id = fresh_id("bot_inprogress_init")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=pending_bot_id,
            bot_name="BotMgmt pending data-init",
            status="PENDING",
        )
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=completed_bot_id,
            bot_name="BotMgmt completed data-init",
            status="ACTIVE",
            ext={"data_init_status": "completed"},
        )
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=in_progress_bot_id,
            bot_name="BotMgmt in-progress data-init",
            status="ACTIVE",
            ext={
                "data_init_status": "in_progress",
                "data_init_started_at": "2999-01-01T00:00:00+00:00",
            },
        )

        for bot_id in (pending_bot_id, completed_bot_id, in_progress_bot_id):
            response = client.post(f"/api/bots/{bot_id}/data-init", json={"force": False})
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True

        deadline = time.monotonic() + 5
        pending_ext = {}
        while time.monotonic() < deadline:
            payload = client.get(f"/api/bots/{pending_bot_id}").json()
            assert payload["success"] is True, payload
            pending_ext = payload["data"].get("ext") or {}
            if pending_ext.get("data_init_status") == "pending_init":
                break
            time.sleep(0.2)
        assert pending_ext.get("data_init_status") == "pending_init", pending_ext

        completed = client.get(f"/api/bots/{completed_bot_id}").json()
        assert completed["success"] is True, completed
        assert (completed["data"].get("ext") or {}).get("data_init_status") == "completed"

        in_progress = client.get(f"/api/bots/{in_progress_bot_id}").json()
        assert in_progress["success"] is True, in_progress
        assert (in_progress["data"].get("ext") or {}).get("data_init_status") == "in_progress"


@pytest.mark.acceptance
def test_bot_management_active_data_init_attempt(live_backend):
    """Trigger active data-init on a real bot with short CI timeouts."""
    user_id = fresh_id("bot_mgmt_active_datainit")
    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": user_id},
        timeout=60.0,
    ) as client:
        create_payload = assert_success(
            client.post(
                "/api/bots",
                json={
                    "bot_name": fresh_id("BotDataInit"),
                    "bot_desc": "singlebox active data-init coverage bot",
                    "entity_id": user_id,
                    "entity_type": "staff",
                    "engine_type": "openclaw",
                    "bot_type": "personal",
                },
            )
        )
        bot = create_payload["data"]["bot"]
        bot_id = bot["bot_id"]
        ready = wait_bot_ready(client, bot_id, timeout_sec=90)
        assert ready["is_ready"] is True, ready

        response = client.post(f"/api/bots/{bot_id}/data-init", json={"force": True})
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True

        deadline = time.monotonic() + 35
        last_ext = {}
        while time.monotonic() < deadline:
            payload = client.get(f"/api/bots/{bot_id}").json()
            assert payload["success"] is True, payload
            last_ext = payload["data"].get("ext") or {}
            if last_ext.get("data_init_status") in {"completed", "failed"}:
                break
            time.sleep(1)

        assert last_ext.get("data_init_status") in {"completed", "failed"}, last_ext


@pytest.mark.acceptance
def test_bot_management_unconfigured_admin_for_others_fails_closed(live_backend):
    """Unconfigured singlebox admin IDs fail closed without creating a bot."""
    admin_id = "100000"
    target_user_id = fresh_id("bot_mgmt_target")
    target_nick_name = fresh_id("BotMgmtTarget")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": admin_id},
        timeout=90.0,
    ) as admin_client:
        created = admin_client.post(
            "/api/bots/create-for-others",
            json={
                "target_user_id": target_user_id,
                "target_nick_name": target_nick_name,
                "bot_type": "personal",
            },
        )
        assert created.status_code == 200, created.text
        created_body = created.json()
        assert created_body["success"] is False, created_body
        assert created_body["error_code"] == 403, created_body

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": target_user_id},
        timeout=60.0,
    ) as target_client:
        bots = target_client.get(
            "/api/bots",
            params={
                "entity_id": target_user_id,
                "entity_type": "staff",
                "page": 1,
                "page_size": 20,
            },
        )
        assert bots.status_code == 200, bots.text
        bots_body = bots.json()
        assert bots_body["success"] is True, bots_body
        assert bots_body["data"] == {"total": 0, "items": []}, bots_body


@pytest.mark.acceptance
def test_bot_management_application_coding_template_update(live_backend):
    """An existing applicationCoding bot can update template config and read it back."""
    owner_id = fresh_id("bot_mgmt_appcoding")
    bot_id = fresh_id("bot_appcoding")
    admin_bot_name = fresh_id("BotMgmtAppcoding")
    old_template = {
        "devflow_workflow": "old-flow",
        "yuque_kb_repos": [],
        "code_repos": [],
        "token": "old-token",
    }
    new_template = {
        "devflow_workflow": "new-flow",
        "yuque_kb_repos": [],
        "code_repos": [],
        "token": "new-token",
    }

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="BotMgmt appcoding seed",
            status="ACTIVE",
            active_engine="claude_code",
            template_type="applicationCoding",
        )
        _seed_template(client, bot_id=bot_id, template_config=old_template)

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        update = client.put(
            f"/api/bots/{bot_id}",
            json={
                "bot_desc": "updated appcoding template in singlebox coverage",
                "template_config": new_template,
            },
        )
        assert update.status_code == 200, update.text
        update_body = update.json()
        assert update_body["success"] is True, update_body
        assert update_body["data"]["template_config"]["devflow_workflow"] == "new-flow"

        detail = client.get(f"/api/bots/{bot_id}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        assert detail_body["success"] is True, detail_body
        assert detail_body["data"]["template_config"]["devflow_workflow"] == "new-flow"

        template_with_dima = {**new_template, "dima_space_id": "singlebox-existing-dima-space"}
        _execute_local_sql(
            client,
            [
                {
                    "sql": "UPDATE ac_templates SET ext = :ext WHERE bot_id = :bot_id",
                    "params": {"bot_id": bot_id, "ext": json.dumps(template_with_dima)},
                }
            ],
        )

        dima_workspace = client.post(f"/api/aicoding/bot/{bot_id}/dima-workspace")
        assert dima_workspace.status_code == 200, dima_workspace.text
        dima_body = dima_workspace.json()
        assert dima_body["success"] is True, dima_body
        assert dima_body["data"]["dima_space_id"] == "singlebox-existing-dima-space"

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "100000"},
        timeout=60.0,
    ) as admin_client:
        admin_update = admin_client.put(
            f"/api/bots/{bot_id}/admin",
            json={
                "owner_id": owner_id,
                "bot_name": admin_bot_name,
                "bot_desc": "admin updated sandbox template in singlebox coverage",
                "template_config": {
                    "image": "registry.example.com/singlebox:v1",
                    "command": "/bin/bash",
                    "envs": {"SINGLEBOX_COVERAGE": "1"},
                    "resource_spec": {"cpu": 1, "memory": 2},
                    "devflow_workflow": "admin-flow",
                },
            },
        )
        assert admin_update.status_code == 200, admin_update.text
        admin_body = admin_update.json()
        assert admin_body["success"] is True, admin_body
        assert admin_body["data"]["warning"] == "沙箱配置变更将在下次重启后生效"


@pytest.mark.acceptance
def test_bot_management_domain_architect_and_desktop_views(live_backend):
    """Domain architect and desktop status list paths are visible via bot APIs."""
    owner_id = fresh_id("bot_mgmt_views")
    architect_bot_id = fresh_id("bot_arch")
    coding_bot_id = fresh_id("bot_coding")
    desktop_bot_id = fresh_id("bot_desktop")
    desktop_device_id = f"BOT-{fresh_id('desktop')}"

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=architect_bot_id,
            bot_name="BotMgmt domain architect",
            status="ACTIVE",
            ext={"is_domain_bot": True},
        )
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=coding_bot_id,
            bot_name="BotMgmt coding child",
            status="ACTIVE",
            active_engine="claude_code",
            template_type="applicationCoding",
        )
        _seed_template(
            client,
            bot_id=coding_bot_id,
            template_config={
                "architect_bot_id": architect_bot_id,
                "devflow_workflow": "child-flow",
            },
        )
        binding_id = _seed_desktop_binding(
            client,
            owner_id=owner_id,
            bot_id=desktop_bot_id,
            device_id=desktop_device_id,
        )
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=desktop_bot_id,
            bot_name="BotMgmt desktop live status",
            status="ACTIVE",
            active_engine="openclaw",
            bot_type="desktop",
            binding_id=binding_id,
            device_id=desktop_device_id,
        )

        domain = client.get(
            "/api/bots/search/domain-bots",
            params={"keyword": "domain architect", "page": 1, "page_size": 10},
        )
        assert domain.status_code == 200, domain.text
        domain_body = domain.json()
        assert domain_body["success"] is True, domain_body
        assert domain_body["data"]["total"] >= 1, domain_body

        appcoding = client.get(f"/api/bots/{architect_bot_id}/appcoding-bots")
        assert appcoding.status_code == 200, appcoding.text
        appcoding_body = appcoding.json()
        assert appcoding_body["success"] is True, appcoding_body
        assert any(
            bot.get("bot_id") == coding_bot_id for bot in appcoding_body["data"]
        ), appcoding_body

        owned_or_collab = client.get("/api/bots/by-owner-or-collaborator")
        assert owned_or_collab.status_code == 200, owned_or_collab.text
        owned_body = owned_or_collab.json()
        assert owned_body["success"] is True, owned_body
        assert any(
            bot.get("bot_id") == desktop_bot_id for bot in owned_body["data"]["items"]
        ), owned_body


@pytest.mark.acceptance
def test_bot_management_boundary_paths(live_backend):
    """Common guardrails stay explicit for invalid names, default, desktop, and service bots."""
    owner_id = fresh_id("bot_mgmt_bounds")
    default_bot_id = "default"
    desktop_bot_id = fresh_id("bot_desktop_guard")
    service_bot_id = fresh_id("bot_service_guard")
    appcoding_workspace_bot_id = fresh_id("bot_app_ws")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        invalid_name = client.post(
            "/api/bots",
            json={"bot_name": "bad@bot", "bot_desc": "invalid name guard"},
        )
        assert invalid_name.status_code == 200, invalid_name.text
        invalid_body = invalid_name.json()
        assert invalid_body["success"] is False, invalid_body
        assert invalid_body["error_code"] == 400, invalid_body

        blank_name = client.post(
            "/api/bots",
            json={"bot_name": "   ", "bot_desc": "blank name guard"},
        )
        assert blank_name.status_code == 200, blank_name.text
        blank_body = blank_name.json()
        assert blank_body["success"] is False, blank_body
        assert blank_body["error_code"] == 400, blank_body

        long_name = client.post(
            "/api/bots",
            json={"bot_name": "BotMgmt" * 8, "bot_desc": "long name guard"},
        )
        assert long_name.status_code == 200, long_name.text
        long_body = long_name.json()
        assert long_body["success"] is False, long_body
        assert long_body["error_code"] == 400, long_body

        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=default_bot_id,
            bot_name="BotMgmt default guard",
            status="ACTIVE",
        )
        # T4: delete protection is now count-based (keep >=1 bot), not bot_id=="default".
        # This owner has exactly one bot, so deleting it must be rejected.
        delete_default = client.delete(f"/api/bots/{default_bot_id}")
        assert delete_default.status_code == 200, delete_default.text
        delete_default_body = delete_default.json()
        assert delete_default_body["success"] is False, delete_default_body
        assert delete_default_body["error_code"] == 500, delete_default_body
        assert "至少保留一个 Bot" in delete_default_body["message"], delete_default_body

        desktop_device_id = f"BOT-{fresh_id('desktop_guard')}"
        binding_id = _seed_desktop_binding(
            client,
            owner_id=owner_id,
            bot_id=desktop_bot_id,
            device_id=desktop_device_id,
        )
        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=desktop_bot_id,
            bot_name="BotMgmt desktop guard",
            status="ACTIVE",
            bot_type="desktop",
            binding_id=binding_id,
            device_id=desktop_device_id,
        )
        restart_desktop = client.post(f"/api/bots/{desktop_bot_id}/restart")
        assert restart_desktop.status_code == 200, restart_desktop.text
        restart_desktop_body = restart_desktop.json()
        assert restart_desktop_body["success"] is False, restart_desktop_body
        assert restart_desktop_body["error_code"] == 500, restart_desktop_body

        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=service_bot_id,
            bot_name="BotMgmt service guard",
            status="ACTIVE",
            bot_type="service",
        )
        first_publish = client.post(
            "/api/service-bot/publish/create_first_publish",
            json={
                "bot_id": service_bot_id,
                "name": "BotMgmt first publish",
                "permission_owner": "owner",
                "description": "singlebox service bot publish guard",
            },
        )
        assert first_publish.status_code == 200, first_publish.text
        first_publish_body = first_publish.json()
        assert first_publish_body["success"] is True, first_publish_body

        owner_list = client.get("/api/bots", params={"page": 1, "page_size": 20})
        assert owner_list.status_code == 200, owner_list.text
        owner_list_body = owner_list.json()
        assert owner_list_body["success"] is True, owner_list_body
        service_rows = [
            bot for bot in owner_list_body["data"]["items"]
            if bot.get("bot_id") == service_bot_id
        ]
        assert service_rows, owner_list_body
        assert service_rows[0]["bot_type"] == "service"

        owner_search = client.post(
            "/api/bots/search",
            json={
                "key": "BotMgmt service guard",
                "owner_id": owner_id,
                "bot_type": "service",
                "page": 0,
                "page_size": 200,
            },
        )
        assert owner_search.status_code == 200, owner_search.text
        owner_search_body = owner_search.json()
        assert owner_search_body["success"] is True, owner_search_body
        search_rows = [
            bot for bot in owner_search_body["data"]["items"]
            if bot.get("bot_id") == service_bot_id
        ]
        assert search_rows, owner_search_body
        assert search_rows[0]["can_delete_bot"] in {True, False}
        assert search_rows[0]["can_upgrade_publish"] in {True, False}

        render_create = client.post(
            "/api/bot-render-screens",
            json={
                "bot_id": service_bot_id,
                "name": "ops screen",
                "cdn_url": "https://example.com/singlebox/screen.html",
            },
        )
        assert render_create.status_code == 200, render_create.text
        render_create_body = render_create.json()
        assert render_create_body["success"] is True, render_create_body
        record_id = render_create_body["data"]["id"]

        render_duplicate = client.post(
            "/api/bot-render-screens",
            json={
                "bot_id": service_bot_id,
                "name": "ops screen",
                "cdn_url": "https://example.com/singlebox/other.html",
            },
        )
        assert render_duplicate.status_code == 200, render_duplicate.text
        render_duplicate_body = render_duplicate.json()
        assert render_duplicate_body["success"] is False, render_duplicate_body
        assert render_duplicate_body["error_code"] == 409, render_duplicate_body

        render_duplicate_url = client.post(
            "/api/bot-render-screens",
            json={
                "bot_id": service_bot_id,
                "name": "ops screen duplicate url",
                "cdn_url": "https://example.com/singlebox/screen.html",
            },
        )
        assert render_duplicate_url.status_code == 200, render_duplicate_url.text
        render_duplicate_url_body = render_duplicate_url.json()
        assert render_duplicate_url_body["success"] is False, render_duplicate_url_body
        assert render_duplicate_url_body["error_code"] == 409, render_duplicate_url_body

        render_list = client.get(
            "/api/bot-render-screens",
            params={"bot_id": service_bot_id},
        )
        assert render_list.status_code == 200, render_list.text
        render_list_body = render_list.json()
        assert render_list_body["success"] is True, render_list_body
        assert any(item["id"] == record_id for item in render_list_body["data"])

        render_update = client.put(
            f"/api/bot-render-screens/{record_id}",
            json={
                "name": "ops screen updated",
                "cdn_url": "https://example.com/singlebox/screen-v2.html",
            },
        )
        assert render_update.status_code == 200, render_update.text
        assert render_update.json()["success"] is True

        render_delete = client.delete(f"/api/bot-render-screens/{record_id}")
        assert render_delete.status_code == 200, render_delete.text
        assert render_delete.json()["success"] is True

        readonly_tree = client.get(
            "/api/service-bot/read-only/tree",
            params={
                "bot_id": service_bot_id,
                "owner_id": owner_id,
                "path": "",
                "recursive": "false",
            },
        )
        assert readonly_tree.status_code == 200, readonly_tree.text
        readonly_body = readonly_tree.json()
        assert readonly_body["success"] is True, readonly_body
        assert "default_rules" in readonly_body["data"], readonly_body

        invalid_tree = client.get(
            "/api/service-bot/read-only/tree",
            params={
                "bot_id": service_bot_id,
                "owner_id": owner_id,
                "path": "../escape",
                "recursive": "false",
            },
        )
        assert invalid_tree.status_code == 200, invalid_tree.text
        invalid_tree_body = invalid_tree.json()
        assert invalid_tree_body["success"] is False, invalid_tree_body
        assert invalid_tree_body["error_code"] == 400, invalid_tree_body

        dima_non_app = client.post(
            f"/api/aicoding/bot/{service_bot_id}/dima-workspace",
        )
        assert dima_non_app.status_code == 200, dima_non_app.text
        dima_non_app_body = dima_non_app.json()
        assert dima_non_app_body["success"] is False, dima_non_app_body
        assert dima_non_app_body["error_code"] == 400, dima_non_app_body

        _seed_plain_bot(
            client,
            owner_id=owner_id,
            bot_id=appcoding_workspace_bot_id,
            bot_name="BotMgmt app workspace",
            status="ACTIVE",
            active_engine="claude_code",
            template_type="applicationCoding",
        )
        _seed_template(
            client,
            bot_id=appcoding_workspace_bot_id,
            template_config={
                "devflow_workflow": "workspace-flow",
                "yuque_kb_repos": [],
                "code_repos": [],
            },
        )
        dima_create = client.post(
            f"/api/aicoding/bot/{appcoding_workspace_bot_id}/dima-workspace",
        )
        assert dima_create.status_code == 200, dima_create.text
        dima_create_body = dima_create.json()
        assert dima_create_body["success"] is True, dima_create_body
        assert dima_create_body["data"]["dima_space_id"]

        codefuse_device_id = f"BOT-{fresh_id('codefuse')}"
        codefuse_binding_id = _seed_desktop_binding(
            client,
            owner_id=owner_id,
            bot_id=appcoding_workspace_bot_id,
            device_id=codefuse_device_id,
        )
        _execute_local_sql(
            client,
            [
                {
                    "sql": (
                        "UPDATE ac_bots SET binding_id = :binding_id, device_id = :device_id "
                        "WHERE bot_id = :bot_id AND owner_id = :owner_id"
                    ),
                    "params": {
                        "bot_id": appcoding_workspace_bot_id,
                        "owner_id": owner_id,
                        "binding_id": codefuse_binding_id,
                        "device_id": codefuse_device_id,
                    },
                }
            ],
        )
        auth_code = base64.b64encode(
            json.dumps({"t": "0123456789abcdef", "w": owner_id}).encode()
        ).decode()
        codefuse = client.put(
            f"/api/aicoding/bots/{appcoding_workspace_bot_id}/codefuse/auth",
            json={"token": auth_code},
        )
        assert codefuse.status_code == 502, codefuse.text

        invalid_auth_codes = [
            base64.b64encode(b"\xff").decode(),
            base64.b64encode(b"not json").decode(),
            base64.b64encode(json.dumps([]).encode()).decode(),
            base64.b64encode(json.dumps({"w": owner_id}).encode()).decode(),
            base64.b64encode(json.dumps({"t": "abc", "w": owner_id}).encode()).decode(),
            base64.b64encode(json.dumps({"t": "z" * 16, "w": owner_id}).encode()).decode(),
            base64.b64encode(json.dumps({"t": "0123456789abcdef"}).encode()).decode(),
        ]
        for invalid_auth_code in invalid_auth_codes:
            invalid_codefuse = client.put(
                f"/api/aicoding/bots/{appcoding_workspace_bot_id}/codefuse/auth",
                json={"token": invalid_auth_code},
            )
            assert invalid_codefuse.status_code == 400, invalid_codefuse.text

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": fresh_id("bot_mgmt_viewer")},
        timeout=60.0,
    ) as viewer_client:
        public_search = viewer_client.post(
            "/api/bots/search",
            json={
                "key": "BotMgmt service guard",
                "owner_id": owner_id,
                "bot_type": "service",
            },
        )
        assert public_search.status_code == 200, public_search.text
        public_body = public_search.json()
        assert public_body["success"] is True, public_body
        public_rows = [
            bot for bot in public_body["data"]["items"]
            if bot.get("bot_id") == service_bot_id
        ]
        assert public_rows, public_body
        assert "ext" not in public_rows[0]
        assert "engine_types" not in public_rows[0]
