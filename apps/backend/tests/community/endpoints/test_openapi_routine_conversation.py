"""Real HTTP/relay coverage for the additive execution content projection."""

import pytest

from tests.community.endpoints.test_openapi_routine_targets import BASE, headers, seed_recording_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_devices", [
    ({"runtime_stage": "draft"}, {"single"}),
    ({"runtime_stage": "online"}, {"instance-a", "instance-b"}),
    ({"runtime_stage": "online", "device_uuid": "instance-b"}, {"instance-b"}),
    ({"runtime_stage": "online", "operator_id": "u_owner", "device_uuid": "instance-a"}, {"instance-a"}),
])
async def test_history_content_travels_through_real_targeted_relay(world, async_client, query, expected_devices):
    _, transport = seed_recording_runtime(world, caller="operator_id" in query)
    for device in expected_devices:
        transport.set_runs_response(device, {
            "input": f"prompt-{device}", "runs": [{
                "job_id": "same", "status": "error", "output": f"answer-{device}",
                "error": "partial failure", "messages": [{"role": "assistant", "content": f"message-{device}",
                                                            "private": "must-not-expose"}],
                "credentials": {"private": "must-not-expose"},
            }],
        })
    response = await async_client.get(BASE + "/job/runs", params={"user_id": "u_owner", **query}, headers=headers())
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["items"]
    assert len(rows) == len(expected_devices)
    assert {row["output"] for row in rows} == {f"answer-{device}" for device in expected_devices}
    for row in rows:
        device = row["output"].removeprefix("answer-")
        assert row["input"] == f"prompt-{device}"
        assert row["messages"] == [{"role": "assistant", "content": f"message-{device}"}]
        assert row["error"] == "partial failure"
        assert row["owner_id"] == "u_owner"
        if query["runtime_stage"] != "draft":
            assert row["runtime_stage"] == query["runtime_stage"]
            assert row["device_uuid"] == device
        else:
            # Draft relay does not report a stage; projection must not invent one.
            assert row["runtime_stage"] is None
    assert "must-not-expose" not in response.text


@pytest.mark.asyncio
async def test_content_does_not_bypass_existing_authorization(world, async_client):
    _, transport = seed_recording_runtime(world)
    response = await async_client.get(BASE + "/job/runs", params={"user_id": "stranger"}, headers=headers("stranger"))
    assert response.status_code in {403, 404}
    assert transport.calls == []
