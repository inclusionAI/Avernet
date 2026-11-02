"""Release the independent caller runtimes created by each live API test."""
from __future__ import annotations

import json
import time

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import assert_success


def _query(client: httpx.Client, sql: str, params: dict) -> list[dict]:
    response = client.post("/local/sql/execute", json={"statements": [{
        "sql": sql, "params": params,
    }]})
    assert response.status_code == 200, response.text
    return response.json()["results"][0]["rows"]


def _release_instance(client: httpx.Client, instance: dict) -> None:
    ext = json.loads(instance["ext"] or "{}")
    if not ext.get("binding_id"):
        return  # Permission/error cases never allocate a runtime.
    try:
        # An asynchronous create must settle before destroy; otherwise its
        # worker can start a process after the test has released the binding.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = assert_success(client.post("/api/v1/expert-chats/caller-connection", params={
                "bot_id": instance["bot_id"], "owner_id": instance["owner_id"],
                "user_id": instance["user_id"],
            }))["data"]
            status = result["instance"]["status"]
            assert status != "failed", f"Caller runtime failed: instance={instance['id']}"
            if not result["need_poll"]:
                break
            time.sleep(1)
        else:
            raise AssertionError(f"Caller runtime did not settle: instance={instance['id']}")
    finally:
        path = f"/api/v1/devices/{ext['binding_id']}"
        headers = {"x-user-id": instance["owner_id"]}
        binding = assert_success(client.get(path, headers=headers))["data"]
        if binding["status"] != "RELEASED":
            assert_success(client.post(path + "/release", headers=headers, json={
                "release_reason": "caller connection acceptance finished"}))


@pytest.fixture(autouse=True)
def release_caller_runtimes(live_backend):
    with httpx.Client(base_url=live_backend, headers={"x-user-id": "100000"}, timeout=60.0) as client:
        last_id = _query(client, "SELECT COALESCE(MAX(id), 0) AS id FROM ac_expert_chat_instance", {})[0]["id"]
        yield
        # Only instances created by this test are owned by this fixture.
        failures = []
        instances = _query(client,
            "SELECT id, bot_id, owner_id, user_id, ext FROM ac_expert_chat_instance WHERE id > :after ORDER BY id",
            {"after": last_id})
        for instance in instances:
            try:
                _release_instance(client, instance)
            except Exception as error:
                failures.append(f"instance={instance['id']}: {type(error).__name__}: {error}")
        assert not failures, "Caller runtime cleanup failed: " + "; ".join(failures)
