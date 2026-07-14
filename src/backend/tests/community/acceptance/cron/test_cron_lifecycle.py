"""Route-B acceptance: cron CRUD reaches the live singlebox engine adapter."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from tests.community._flows.cron.api_lifecycle import CRON_FLOWS
from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)
from tests.community.framework.flow import FlowContext
from tests.community.framework.flow_runner_live import run_flow_live


def _execute_local_sql(client: httpx.Client, statements: list[dict]) -> None:
    """Execute fixture SQL after the live backend has finished pending writes."""
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post("/local/sql/execute", json={"statements": statements})
        if response.status_code == 200:
            return
        last_response = response
        if "SQL statements in progress" not in response.text:
            break
        time.sleep(0.2 * (attempt + 1))

    assert last_response is not None
    assert last_response.status_code == 200, last_response.text


def _seed_success_publish(
    client: httpx.Client,
    *,
    bot_id: str,
    owner_id: str,
    binding_id: int,
) -> None:
    """Promote a live BaaS bot to a service Bot with retained verify runtime."""
    publish_ext = json.dumps({"binding": {"verify": binding_id, "online": binding_id}})
    _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "UPDATE ac_bots SET bot_type = 'service', "
                    "gmt_modified = CURRENT_TIMESTAMP "
                    "WHERE bot_id = :bot_id AND owner_id = :owner_id"
                ),
                "params": {"bot_id": bot_id, "owner_id": owner_id},
            },
            {
                "sql": (
                    "INSERT INTO ac_bot_publish ("
                    "source_bot_pk, source_bot_id, publish_bot_id, name, description, "
                    "owner_id, owner_name, status, version, last_pub_id, env, ext, "
                    "permission_owner, gmt_create, gmt_modified"
                    ") VALUES ("
                    "(SELECT id FROM ac_bots WHERE bot_id = :bot_id "
                    "AND owner_id = :owner_id), :bot_id, :publish_bot_id, "
                    ":name, :description, :owner_id, :owner_id, 'success', 1, 0, "
                    "'dev', :ext, 'owner', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                    ")"
                ),
                "params": {
                    "bot_id": bot_id,
                    "publish_bot_id": f"{bot_id}_pub_1",
                    "name": "Cron published runtime",
                    "description": "singlebox retained verify runtime",
                    "owner_id": owner_id,
                    "ext": publish_ext,
                },
            },
        ],
    )


@pytest.mark.acceptance
def test_cron_crud_and_published_baas_runtime_live(live_backend, acceptance_fs_root):
    """Cover personal CRUD and published service runtime with one live BaaS bot."""
    user_id = fresh_id("e2e_cron_user")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="Cron Acceptance",
            bot_desc="cron live adapter acceptance bot",
        )

    ctx = FlowContext()
    ctx["bot_id"] = bot["bot_id"]
    result_ctx = run_flow_live(
        CRON_FLOWS[0],
        base_url=live_backend,
        fs_root=acceptance_fs_root,
        default_headers=headers,
        initial_context=ctx,
    )

    assert result_ctx["cron_task_id"]

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        create_response = client.post(
            "/api/cron",
            json={
                "bot_id": bot["bot_id"],
                "name": "singlebox-published-cron",
                "schedule": "0 * * * *",
                "command": "echo published runtime",
            },
        )
        assert create_response.status_code == 200, create_response.text
        create_body = create_response.json()
        assert create_body["success"] is True, create_body
        task_id = create_body["data"]["id"]

        _seed_success_publish(
            client,
            bot_id=bot["bot_id"],
            owner_id=user_id,
            binding_id=int(bot["binding_id"]),
        )

        verify_response = client.get(
            f"/api/cron/{task_id}",
            params={"bot_id": bot["bot_id"], "runtime_stage": "verify"},
        )
        assert verify_response.status_code == 200, verify_response.text
        verify_body = verify_response.json()
        assert verify_body["success"] is True, verify_body
        assert verify_body["data"]["runtime_stage"] == "verify", verify_body
        assert verify_body["data"]["publish_status"] == "success", verify_body

        running_response = client.get(
            "/api/cron/running",
            params={"bot_id": bot["bot_id"], "runtime_stage": "online"},
        )
        assert running_response.status_code == 200, running_response.text
        running_body = running_response.json()
        assert running_body["success"] is True, running_body
        assert running_body["failed_targets"] == [], running_body
