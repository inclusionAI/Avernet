"""``ArcaAcliPort`` — every engine answer becomes an operation, a listing or an ``AcliPortError``."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.acli_clis.arca_port import (
    INSTALL_PATH,
    TOOLS_PATH,
    UNINSTALL_PATH,
    ArcaAcliPort,
)
from agentclaw.community.core.acli_clis.operations import AcliAction, AcliRuntime
from agentclaw.community.core.acli_clis.port import AcliPortError
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)

pytestmark = pytest.mark.asyncio

CONN = {"url": "http://engine"}
RUNTIME = AcliRuntime(bot_id="bot-1", owner_id="owner-1", stage=PublishStage.VERIFY)
OP = {
    "id": "e" * 32, "action": "install", "name": "deepinsight-cli", "state": "running",
    "started_at": "2026-10-21T00:00:00Z", "finished_at": None, "result": None, "error": None,
}


class _Contexts:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def resolve(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(conn_info=CONN)


class _Transport:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple] = []

    async def invoke(self, conn_info, method, path, body=None, params=None, *, timeout=None):
        self.calls.append((conn_info, method, path, body, timeout))
        if self.error:
            raise self.error
        return self.response


def _port(transport: _Transport, contexts: _Contexts | None = None) -> ArcaAcliPort:
    return ArcaAcliPort(contexts=contexts or _Contexts(), transport=transport)  # type: ignore[arg-type]


def _envelope(op: dict | None = OP, **fields: Any) -> dict:
    return {"success": True, "data": {"operation": op} if op else None, **fields}


async def test_submit_posts_async_to_the_stage_and_returns_the_operation() -> None:
    transport = _Transport(_envelope())
    contexts = _Contexts()

    op = await _port(transport, contexts).submit(RUNTIME, action=AcliAction.INSTALL, name="deepinsight-cli")

    assert (op.id, op.state, op.active) == ("e" * 32, "running", True)
    [(conn, method, path, body, timeout)] = transport.calls
    assert (conn, method, path, body) == (
        CONN, "POST", INSTALL_PATH, {"name": "deepinsight-cli", "async": True},
    )
    assert timeout <= 30
    assert contexts.calls == [{"bot_id": "bot-1", "owner_id": "owner-1", "stage": "verify"}]


async def test_uninstall_and_cancel_paths() -> None:
    transport = _Transport(_envelope())
    port = _port(transport)

    await port.submit(RUNTIME, action=AcliAction.UNINSTALL, name="x")
    await port.cancel(RUNTIME, action=AcliAction.INSTALL, name="x")
    await port.cancel(RUNTIME, action=AcliAction.UNINSTALL, name="x")

    assert [c[2] for c in transport.calls] == [
        UNINSTALL_PATH, f"{INSTALL_PATH}/cancel", f"{UNINSTALL_PATH}/cancel",
    ]


async def test_an_ended_failed_operation_is_returned_not_raised() -> None:
    failed = {**OP, "state": "failed", "error": {"code": "install_failed", "message": "nope"}}
    transport = _Transport({"success": False, "error": "install_failed",
                            "data": {"operation": failed}})

    op = await _port(transport).get_operation(RUNTIME, "e" * 32)

    assert (op.state, op.error_code, op.error_message) == ("failed", "install_failed", "nope")
    assert transport.calls[0][1:3] == ("GET", f"/api/cli/acli/operations/{'e' * 32}")


async def test_a_refusal_without_an_operation_raises_its_code() -> None:
    transport = _Transport({"success": False, "error": "install_failed",
                            "message": "cannot record", "data": None})

    with pytest.raises(AcliPortError) as caught:
        await _port(transport).submit(RUNTIME, action=AcliAction.INSTALL, name="x")

    assert (caught.value.code, caught.value.message) == ("install_failed", "cannot record")


async def test_success_without_an_operation_is_bad_output() -> None:
    with pytest.raises(AcliPortError) as caught:
        await _port(_Transport({"success": True, "data": {}})).submit(
            RUNTIME, action=AcliAction.INSTALL, name="x"
        )

    assert caught.value.code == "bad_output"


async def test_engine_busy_carries_the_active_operation() -> None:
    body = json.dumps({"success": False, "error": "engine_busy", "message": "busy",
                       "data": {"operation": OP}})
    transport = _Transport(error=DeviceAdapterHTTPStatusError(409, body))

    with pytest.raises(AcliPortError) as caught:
        await _port(transport).submit(RUNTIME, action=AcliAction.UNINSTALL, name="x")

    assert caught.value.code == "engine_busy"
    assert caught.value.operation.name == "deepinsight-cli"


async def test_not_active_is_its_own_code() -> None:
    body = json.dumps({"success": False, "error": "not_active", "message": "nothing", "data": None})
    transport = _Transport(error=DeviceAdapterHTTPStatusError(409, body))

    with pytest.raises(AcliPortError) as caught:
        await _port(transport).cancel(RUNTIME, action=AcliAction.INSTALL, name="x")

    assert (caught.value.code, caught.value.operation) == ("not_active", None)


async def test_an_unknown_operation_reads_as_none() -> None:
    body = json.dumps({"success": False, "error": "operation_not_found", "data": None})
    transport = _Transport(error=DeviceAdapterEndpointNotFoundError(body))

    assert await _port(transport).get_operation(RUNTIME, "f" * 32) is None


async def test_list_tools_reads_the_listing() -> None:
    transport = _Transport({"success": True, "data": {"tools": [
        {"name": "deepinsight-cli", "command": "di", "description": "d", "version": "1.0.9"},
        {"name": "yuntu-cli"},
    ]}})

    tools = await _port(transport).list_tools(RUNTIME)

    assert [(t.name, t.command) for t in tools] == [("deepinsight-cli", "di"), ("yuntu-cli", "")]
    assert transport.calls[0][1:3] == ("GET", TOOLS_PATH)


@pytest.mark.parametrize("data", [None, {}, {"tools": "x"}, {"tools": [{"name": ""}]}])
async def test_an_unreadable_listing_is_bad_output(data) -> None:
    with pytest.raises(AcliPortError) as caught:
        await _port(_Transport({"success": True, "data": data})).list_tools(RUNTIME)

    assert caught.value.code == "bad_output"


async def test_a_failed_listing_raises_its_code() -> None:
    transport = _Transport({"success": False, "error": "list_failed", "message": "busy"})

    with pytest.raises(AcliPortError) as caught:
        await _port(transport).list_tools(RUNTIME)

    assert caught.value.code == "list_failed"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DeviceAdapterEndpointNotFoundError('{"detail":"Not Found"}', standard_route_missing=True),
         "engine_outdated"),
        # A 404 in any other shape is a missing endpoint too, e.g. an engine
        # serving its own API: never a transient failure.
        (DeviceAdapterEndpointNotFoundError('{"error":"not found"}'), "engine_outdated"),
        (DeviceAdapterEndpointNotFoundError("gateway says no"), "engine_outdated"),
        (DeviceAdapterEndpointNotFoundError('{"success": false, "error": "other"}'),
         "engine_outdated"),
        (DeviceAdapterHTTPStatusError(501, ""), "engine_unsupported"),
        (DeviceAdapterHTTPStatusError(400, "bad"), "invalid_input"),
        (DeviceAdapterHTTPStatusError(409, "not json"), "engine_unreachable"),
        (DeviceAdapterHTTPStatusError(502, ""), "engine_unreachable"),
        (DeviceAdapterTimeoutError(), "timeout"),
        (RuntimeError("boom"), "engine_unreachable"),
    ],
)
async def test_transport_failures_map_to_codes(error: Exception, code: str) -> None:
    with pytest.raises(AcliPortError) as caught:
        await _port(_Transport(error=error)).submit(RUNTIME, action=AcliAction.INSTALL, name="x")

    assert caught.value.code == code


async def test_no_container_is_not_running() -> None:
    transport = _Transport(_envelope())

    with pytest.raises(AcliPortError) as caught:
        await _port(transport, _Contexts(error=LookupError("no binding"))).submit(
            RUNTIME, action=AcliAction.INSTALL, name="x"
        )

    assert caught.value.code == "not_running"
    assert transport.calls == []


async def test_a_non_mapping_answer_is_unreachable() -> None:
    with pytest.raises(AcliPortError) as caught:
        await _port(_Transport(["not", "an", "envelope"])).submit(
            RUNTIME, action=AcliAction.INSTALL, name="x"
        )

    assert caught.value.code == "engine_unreachable"


async def test_a_malformed_operation_is_bad_output() -> None:
    with pytest.raises(AcliPortError) as caught:
        await _port(_Transport(_envelope({"id": "x"}))).submit(
            RUNTIME, action=AcliAction.INSTALL, name="x"
        )

    assert caught.value.code == "bad_output"
