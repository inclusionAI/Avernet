"""``ArcaAcliPort`` — every engine answer becomes a result or an ``AcliPortError``."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.acli_clis.arca_port import (
    INSTALL_PATH,
    MARKET_PATH,
    UNINSTALL_PATH,
    ArcaAcliPort,
)
from agentclaw.community.core.acli_clis.port import AcliPortError
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)

pytestmark = pytest.mark.asyncio

CONN = {"url": "http://engine"}


class _Resolver:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def resolve_for_bot(self, bot_id: str, user_id: str) -> Any:
        assert (bot_id, user_id) == ("bot-1", "owner-1")
        if self.error:
            raise self.error
        return SimpleNamespace(conn_info=CONN)


class _Transport:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple] = []

    async def invoke(self, conn_info, method, path, body=None, params=None, *, timeout=None):
        self.calls.append((conn_info, method, path, body, params, timeout))
        if self.error:
            raise self.error
        return self.response


def _port(transport: _Transport, resolver: _Resolver | None = None) -> ArcaAcliPort:
    return ArcaAcliPort(resolver=resolver or _Resolver(), transport=transport)  # type: ignore[arg-type]


async def test_install_posts_name_and_dir_and_reads_the_tool() -> None:
    transport = _Transport({"success": True, "data": {
        "name": "deepinsight-cli", "command": "di", "description": "DI", "version": "1.0.9",
    }})

    tool = await _port(transport).install(
        bot_id="bot-1", owner_id="owner-1", name="deepinsight-cli"
    )

    assert (tool.name, tool.command, tool.description, tool.version) == (
        "deepinsight-cli", "di", "DI", "1.0.9",
    )
    [(conn, method, path, body, params, timeout)] = transport.calls
    assert (conn, method, path, body, params) == (
        CONN, "POST", INSTALL_PATH, {"name": "deepinsight-cli"}, None,
    )
    assert timeout > 300


async def test_uninstall_reports_whether_anything_was_removed() -> None:
    transport = _Transport({"success": True, "data": {"name": "x", "uninstalled": False}})

    removed = await _port(transport).uninstall(
        bot_id="bot-1", owner_id="owner-1", name="x"
    )

    assert removed is False
    assert transport.calls[0][2] == UNINSTALL_PATH


async def test_market_passes_query_and_skips_malformed_rows() -> None:
    transport = _Transport({"success": True, "data": {
        "items": [{"name": "dataphin", "command": "dataphin", "description": "", "version": "1"}, 3],
        "total": 9, "has_more": True,
    }})

    page = await _port(transport).market(
        bot_id="bot-1", owner_id="owner-1", keyword="data", page_no=2, page_size=5
    )

    assert [t.name for t in page.items] == ["dataphin"]
    assert (page.total, page.has_more) == (9, True)
    assert transport.calls[0][1:5] == (
        "GET", MARKET_PATH, None, {"keyword": "data", "page_no": 2, "page_size": 5},
    )


async def test_market_without_total_counts_items() -> None:
    transport = _Transport({"success": True, "data": {"items": "not-a-list"}})

    page = await _port(transport).market(
        bot_id="bot-1", owner_id="owner-1", keyword="", page_no=1, page_size=5
    )

    assert (page.items, page.total, page.has_more) == ((), 0, False)


@pytest.mark.parametrize(
    ("resolver_error", "transport", "code"),
    [
        (RuntimeError("no binding"), _Transport(), "not_running"),
        (None, _Transport(error=DeviceAdapterEndpointNotFoundError("nope")), "engine_unsupported"),
        (None, _Transport(error=DeviceAdapterHTTPStatusError(501, "x")), "engine_unsupported"),
        (None, _Transport(error=DeviceAdapterHTTPStatusError(400, "x")), "invalid_input"),
        (None, _Transport(error=DeviceAdapterHTTPStatusError(503, "x")), "engine_unreachable"),
        (None, _Transport(error=DeviceAdapterTimeoutError("slow")), "timeout"),
        (None, _Transport(error=ValueError("socket")), "engine_unreachable"),
        (None, _Transport(response="not-a-dict"), "engine_unreachable"),
        (None, _Transport({"success": False, "error": "install_failed", "message": "no such cli"}),
         "install_failed"),
        (None, _Transport({"success": False}), "engine_refused"),
    ],
)
async def test_every_failure_becomes_a_coded_port_error(resolver_error, transport, code) -> None:
    with pytest.raises(AcliPortError) as caught:
        await _port(transport, _Resolver(resolver_error)).install(
            bot_id="bot-1", owner_id="owner-1", name="x"
        )

    assert caught.value.code == code
    if resolver_error is not None:
        assert transport.calls == []


async def test_engine_message_is_carried_through() -> None:
    transport = _Transport({"success": False, "error": "install_failed", "message": "no such cli"})

    with pytest.raises(AcliPortError) as caught:
        await _port(transport).install(bot_id="bot-1", owner_id="owner-1", name="x")

    assert caught.value.message == "no such cli"


async def test_success_without_data_reads_as_empty() -> None:
    transport = _Transport({"success": True})

    removed = await _port(transport).uninstall(
        bot_id="bot-1", owner_id="owner-1", name="x"
    )

    assert removed is False
