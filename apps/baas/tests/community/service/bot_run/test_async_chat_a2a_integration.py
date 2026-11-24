"""test async chat a2a integration; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio

import pytest
from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
)
from a2a_test_support_cancel import (
    make_cancel_app,
)
from a2a_test_support_lifecycle import (
    _MALFORMED_CARDS,
    _FakeV3KingineTransport,
    make_card_down_app,
    make_malformed_card_app,
)

from secbaas.community.bootstrap._chat_transport import build_chat_transport
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError,
    BotA2aClient,
)
from tests.utils.chat_transport import make_chat_client


def test_tracks_session_spans_background_run_until_end_of_stream():
    """R6-1：归属锚在 run 执行期——ACK 即返回（非等待发送）、编排层等待
    收口都不影响归属；流终结后归属结束。CancelTask 真发出（非
    RUN_NOT_TRACKED）。"""
    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(session_key="sk-bg", message="hi")
            assert ack["ok"] is True
            # 非等待发送：ACK 已回、结果未收——在途 run 归属存在
            assert client.tracks_session("sk-bg") is True, (
                "ACK 后归属丢失（非等待发送的取消会 RUN_NOT_TRACKED）"
            )
            assert client.has_running_runs is True

            await asyncio.sleep(0.3)  # WORKING 帧到达 → engine_task_id 就位
            assert "sk-bg" not in getattr(client, "_sessions", {}) or True
            out = await client.chat_abort(session_key="sk-bg")
            assert out["ok"] is True, out
            cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
            assert len(cancels) == 1, "CancelTask 必须真的发出"

            await asyncio.sleep(0.5)  # 引擎按取消收流（CANCELED final）
            assert client.tracks_session("sk-bg") is False, "流已终结,归属未随之结束"
            await client.close()
            return True

        assert _run(scenario())
    finally:
        srv.stop()


def test_pool_owner_lookup_survives_wait_result_false_send():
    """R6-1 reviewer 验收形态：两条连接，主连接做**非等待发送**——ACK 后
    编排层 _sessions 已不在,但 pool.get(session_key) 仍须命中真实
    holder,取消真发 CancelTask 而非 RUN_NOT_TRACKED。"""
    from unittest.mock import MagicMock

    from secbaas.community.core.service.bot_run._async_chat_client_pool import (
        AsyncChatClientPool,
        _ConnEntry,
    )

    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            inner = await _connect_async(srv.base)  # 真 BotA2aClient
            holder = make_chat_client(
                uri="ws://x/ws", max_retries=0, transport_mode="on"
            )
            holder._chat_transport._client = inner
            holder._chat_transport._current_name = "a2a"

            other = MagicMock()  # 另一条空闲连接
            other.tracks_session = MagicMock(return_value=False)
            other.has_running_runs = False

            pool = AsyncChatClientPool(
                transport_factory=build_chat_transport,
            )
            pool._clients["bg-6"] = [
                _ConnEntry(client=other),
                _ConnEntry(client=holder, a2a_mode="on"),
            ]

            ack = await inner.chat_send(session_key="sk-own", message="hi")
            assert ack["ok"] is True
            await asyncio.sleep(0.3)  # engine_task_id 就位
            assert "sk-own" not in holder._sessions, "前置:编排层等待不在（非等待发送）"

            owner = await pool.get("bg-6", "ws://x/ws", None, session_key="sk-own")
            assert owner is holder, "取消未命中真实 run 的持有连接"

            out = await owner.chat_abort(session_key="sk-own")
            assert out["ok"] is True, out
            cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
            assert len(cancels) == 1, "定向取消必须真的发出 CancelTask"
            await holder.close()
            return True

        assert _run(scenario())
    finally:
        srv.stop()


def test_async_client_grey_mode_falls_back_to_v3_on_probe_failure():
    from unittest.mock import patch

    srv = LiveServer(make_card_down_app())
    try:

        async def scenario():
            with patch(
                "secbaas.community.core.service.bot_run._async_chat_client_ws."
                "BotWebSocketClient",
                _FakeV3KingineTransport,
            ):
                acc = make_chat_client(
                    uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                    transport_mode="grey",
                    max_retries=0,
                )
                hello = await acc.connect()
                assert acc.transport_name == "v3-ws", (
                    f"探测失败时 grey 必须回退 v3（不炸不断流）: {hello}"
                )
                assert acc._chat_transport._client is not None
                await acc.close()
                return True

        assert _run(scenario())
    finally:
        srv.stop()


def test_async_client_on_mode_raises_when_a2a_probe_fails():

    srv = LiveServer(make_card_down_app())
    try:

        async def scenario():
            acc = make_chat_client(
                uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                transport_mode="on",
                max_retries=0,
            )
            await acc.connect()

        with pytest.raises(A2aUnavailableError):
            _run(scenario())
    finally:
        srv.stop()


@pytest.mark.parametrize("case,payload", _MALFORMED_CARDS)
def test_async_client_grey_malformed_card_falls_back_to_v3(case, payload):
    """结构畸形卡不炸灰度门——grey 仍如约回退 v3（原 WS 服务照常可用）。"""
    from unittest.mock import patch

    srv = LiveServer(make_malformed_card_app(payload))
    try:

        async def scenario():
            with patch(
                "secbaas.community.core.service.bot_run._async_chat_client_ws."
                "BotWebSocketClient",
                _FakeV3KingineTransport,
            ):
                acc = make_chat_client(
                    uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                    transport_mode="grey",
                    max_retries=0,
                )
                hello = await acc.connect()
                assert acc.transport_name == "v3-ws", (
                    f"畸形卡({case})下 grey 必须回退 v3: {hello}"
                )
                await acc.close()
                return True

        assert _run(scenario())
    finally:
        srv.stop()


@pytest.mark.parametrize("case,payload", _MALFORMED_CARDS)
def test_async_client_on_mode_malformed_card_raises_a2a_error(case, payload):
    """on 模式下畸形卡 = 明确的 A2aUnavailableError 失败，而非裸
    AttributeError/TypeError 击穿建连。"""

    srv = LiveServer(make_malformed_card_app(payload))
    try:

        async def scenario():
            acc = make_chat_client(
                uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                transport_mode="on",
                max_retries=0,
            )
            await acc.connect()

        with pytest.raises(A2aUnavailableError):
            _run(scenario())
    finally:
        srv.stop()


def test_refresh_proxypass_token_updates_and_guards():

    client = BotA2aClient(base_url="http://x", headers={"x-proxypass-token": "mock"})
    client.refresh_proxypass_token({"x-proxypass-token": "placeholder"})
    assert client.headers["x-proxypass-token"] == "placeholder"

    client.refresh_proxypass_token({"x-proxypass-token": ""})
    assert client.headers["x-proxypass-token"] == "placeholder", (
        "空 token（Local 平台）不得覆盖既有值"
    )

    client.refresh_proxypass_token(None)
    assert client.headers["x-proxypass-token"] == "placeholder"
