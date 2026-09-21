"""Seed real current bindings; simulate only external Engine transport."""
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from tests.community.factories.publish_ignore import seed_publish_ignore


def seed_file_count(world, *, success=True, stage="draft"):
    seed_publish_ignore(world, stage=stage)
    world.get(DeviceAdapterTransport).set_response("invoke", {
        "success": success,
        "data": {"path": "/workspace", "file_count": 0, "elapsed_ms": 1},
    })


def assert_file_count_result(response, world):
    result = response.json()["data"]
    calls = world.get(DeviceAdapterTransport).calls_to("invoke")
    assert len(calls) == 1
    assert calls[0].args[0]["device_uuid"] == "replica-a"
    assert calls[0].args[0]["bot_uuid"] == "ignore-runtime"
    assert calls[0].args[1:3] == ("GET", "/api/file/count")
    assert calls[0].kwargs["params"] == {"path": "/workspace", "request_id": result["request_id"]}
    item = result["results"][0]
    assert item["file_count"] == (0 if result["success"] else None)
    assert response.json()["error_code"] == (200 if result["success"] else 502)
