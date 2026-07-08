"""Rule #13 — 定时任务 (/api/cron) 契约测试。

These drive the **real** ``CronRelayService`` end-to-end: a real bot +
ACTIVE local device binding are seeded, and the only swapped component
is the HTTP-to-adapter boundary (``DeviceAdapterTransport``), which the
test runtime binds to an in-memory cron adapter. So device resolution,
permission checks and response shaping all run as production code.

验证 GET/POST /api/cron, GET /api/cron/status,
GET/PUT/DELETE /api/cron/{taskId},
POST /api/cron/{taskId}/run, GET /api/cron/{taskId}/runs, GET /api/cron/running。
"""
from __future__ import annotations

from tests.community.contracts.gateway.conftest import (
    assert_response_schema, assert_success, assert_has_fields,
    assert_response_data_contract,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_local_device


# Matches MOCK_USER.outUserNo (staffId) in the gateway conftest.
USER_ID = "448524"
BOT_ID = "bot_test_001"


def _seed_bot_with_device(world) -> None:
    """Seed an owner + ACTIVE local device + bot linked to that binding,
    so the real relay resolves a usable connection to the in-memory adapter."""
    make_staff_user(world, user_id=USER_ID)
    binding_id = make_active_local_device(
        world, owner_id=USER_ID, device_id="cron_contract_device"
    )
    make_bot(
        world, bot_id=BOT_ID, owner_id=USER_ID, owner_name="TestUser",
        bot_type="service", status="ACTIVE", binding_id=binding_id,
    )


def _create_cron(gw_client, name: str = "Test Cron") -> dict:
    """Create one cron via the real POST endpoint; return the response body."""
    resp = gw_client.post("/api/cron", json={
        "bot_id": BOT_ID,
        "name": name,
        "schedule": "0 9 * * *",
        "command": "hello",
    })
    return resp.json()


class TestListCrons:
    """GET /api/cron — 任务列表。"""

    def test_list_schema(self, gw_client, world, contract_snapshot_update):
        _seed_bot_with_device(world)
        _create_cron(gw_client)

        resp = gw_client.get("/api/cron", params={"bot_id": BOT_ID})
        body = resp.json()

        assert_success(body, "GET /api/cron")
        assert_response_data_contract(body, "rule13_GET_api_cron", update=contract_snapshot_update)
        data = body["data"]
        assert isinstance(data, list), f"GET /api/cron data should be list, got {type(data).__name__}"
        assert_has_fields(
            data[0],
            {"id": str, "name": str, "enabled": bool, "schedule": dict, "payload": dict, "state": dict, "bot_id": str},
            label="GET /api/cron data[0]",
        )


class TestGetCronStatus:
    """GET /api/cron/status — 任务状态汇总。"""

    def test_status_schema(self, gw_client, world, contract_snapshot_update):
        _seed_bot_with_device(world)
        _create_cron(gw_client)

        resp = gw_client.get("/api/cron/status", params={"bot_id": BOT_ID})
        body = resp.json()

        assert_success(body, "GET /api/cron/status")
        assert_response_data_contract(body, "rule13_GET_api_cron_status", update=contract_snapshot_update)
        assert_has_fields(
            body["data"], {"running": bool, "job_count": int, "enabled_count": int, "bot_id": str},
            label="GET /api/cron/status data",
        )


class TestCreateCron:
    """POST /api/cron — 创建任务。"""

    def test_create_schema(self, gw_client, world, contract_snapshot_update):
        _seed_bot_with_device(world)

        body = _create_cron(gw_client, name="New Cron")

        assert_success(body, "POST /api/cron")
        assert_response_data_contract(body, "rule13_POST_api_cron", update=contract_snapshot_update)
        assert_has_fields(
            body["data"], {"id": str, "name": str, "bot_id": str},
            label="POST /api/cron data",
        )


class TestDeleteCron:
    """DELETE /api/cron/{taskId} — 删除任务。"""

    def test_delete_schema(self, gw_client, world):
        _seed_bot_with_device(world)
        created = _create_cron(gw_client)
        task_id = created["data"]["id"]

        resp = gw_client.delete(f"/api/cron/{task_id}", params={"bot_id": BOT_ID})
        body = resp.json()

        assert_response_schema(
            body, required_top={"success": bool, "message": str},
            label="DELETE /api/cron/{taskId}",
        )


class TestRunCron:
    """POST /api/cron/{taskId}/run — 手动触发执行。"""

    def test_run_schema(self, gw_client, world, contract_snapshot_update):
        _seed_bot_with_device(world)
        created = _create_cron(gw_client)
        task_id = created["data"]["id"]

        resp = gw_client.post(f"/api/cron/{task_id}/run", params={"bot_id": BOT_ID})
        body = resp.json()

        assert_success(body, "POST /api/cron/{taskId}/run")
        assert_response_data_contract(body, "rule13_POST_api_cron_task_id_run", update=contract_snapshot_update)
        assert_has_fields(
            body["data"], {"ok": bool, "ran": bool, "bot_id": str},
            label="POST /api/cron/{taskId}/run data",
        )


class TestGetCronRuns:
    """GET /api/cron/{taskId}/runs — 执行历史列表。"""

    def test_runs_schema(self, gw_client, world, contract_snapshot_update):
        _seed_bot_with_device(world)
        created = _create_cron(gw_client)
        task_id = created["data"]["id"]

        resp = gw_client.get(f"/api/cron/{task_id}/runs", params={"bot_id": BOT_ID})
        body = resp.json()

        assert_success(body, "GET /api/cron/{taskId}/runs")
        assert_response_data_contract(body, "rule13_GET_api_cron_task_id_runs", update=contract_snapshot_update)
        data = body["data"]
        assert_has_fields(
            data, {"runs": list, "bot_id": str},
            label="GET /api/cron/{taskId}/runs data",
        )
        assert_has_fields(
            data["runs"][0],
            {"job_id": str, "started_at_ms": int, "status": str, "duration_ms": int},
            label="GET /api/cron/{taskId}/runs data.runs[0]",
        )
