"""Creation history stays readable after later ordinary configuration writes."""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.platform import TaskQueueRepositoryProtocol
from agentclaw.community.core.task_queue.types import DEFAULT_APP, TaskStatus
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.api.apply_run_service import ApplyRunServiceProtocol
from agentclaw.community.core.apply_engine.apply_task import APPLY_TASK_TYPE
from tests.community.endpoints.test_openapi_create_with_manifest import (
    _APPLY_ID,
    _BOT_ID,
    _HEADERS,
    _QUERY,
    _poll,
    _seed_a_running_bot_with_a_failed_apply,
    client as client,
)


def test_creation_keeps_its_partial_report_after_ordinary_apply_succeeds(client, world):
    _seed_a_running_bot_with_a_failed_apply(world)
    jobs = world.get(TaskQueueRepositoryProtocol)
    for job in jobs.claim_batch(
        worker_id="creation-history", env=get_current_env(), app=DEFAULT_APP,
        limit=10, lease_seconds=60,
    ):
        jobs.complete(task_id=job.id, worker_id="creation-history")

    initial = _poll(client, _BOT_ID)
    assert initial.status_code == 200
    assert initial.json()["data"]["state"] == "APPLY_FAILED"
    accepted = client.post(
        f"/openapi/v1/bots/{_BOT_ID}/config-manifest/apply",
        params=_QUERY, headers=_HEADERS,
    )
    assert accepted.status_code == 202
    for status in TaskStatus:
        for task in jobs.list_by_status(status=status, env=get_current_env(), app=DEFAULT_APP):
            if task.task_type == APPLY_TASK_TYPE:
                world.get(ApplyRunServiceProtocol).run_apply_task(task.payload)
    latest = client.get(
        f"/openapi/v1/bots/{_BOT_ID}/config-manifest/last-apply",
        params=_QUERY, headers=_HEADERS,
    ).json()["data"]
    assert latest["result"] == "SUCCEEDED"
    assert latest["apply_id"] != _APPLY_ID

    repeated = _poll(client, _BOT_ID)
    assert repeated.status_code == 200
    history = repeated.json()["data"]
    assert history["state"] == "APPLY_FAILED"
    assert history["bot"]["bot_id"] == _BOT_ID
    assert history["apply"]["apply_id"] == _APPLY_ID
    assert history["apply"]["result"] == "PARTIAL"
    assert history["apply"]["entries"][0]["error"] == "the device refused the write"
