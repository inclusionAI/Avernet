"""test async chat events; focused regression tests and fixtures."""

import asyncio

import pytest

from tests.utils.chat_transport import make_chat_client

from ._async_chat_test_support import (
    _TEST_SESSION_KEY,
    _setup_session_state,
)
from ._async_chat_test_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_test_support import (
    mock_bot_ws_instance as mock_bot_ws_instance,
)


class TestOnChat:
    @pytest.mark.asyncio
    async def test_on_chat_delta_state(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "delta",
                "message": {"content": [{"text": "partial response"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.content == "partial response"
        assert state.state == ""
        assert state.chat_complete.is_set() is False

    @pytest.mark.asyncio
    async def test_on_chat_final_state(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "final",
                "message": {"content": [{"text": "complete response"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == "final"
        assert state.content == "complete response"
        assert state.chat_complete.is_set() is True

    @pytest.mark.asyncio
    async def test_on_chat_ignores_inject_run_id_final_state(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)
        state.stream_queue = asyncio.Queue()

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "runId": "inject-3678aa1a-4fac-45cf-b181-8430342c661a",
                "state": "final",
                "message": {"content": [{"text": "[BCS Group Context] leaked"}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == ""
        assert state.content == ""
        assert state.chat_complete.is_set() is False
        assert state.stream_queue.empty() is True

    @pytest.mark.asyncio
    async def test_on_chat_error_state(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "error",
                "errorMessage": "LLM is trying to invoke a non-exist tool",
            }
        )
        await asyncio.sleep(0)
        assert state.state == "error"
        assert state.chat_complete.is_set() is True

    @pytest.mark.asyncio
    async def test_on_chat_unknown_state(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "thinking",
                "message": {"content": [{"text": "thinking..."}]},
            }
        )
        await asyncio.sleep(0)
        assert state.state == ""
        assert state.content == ""
        assert state.chat_complete.is_set() is False

    @pytest.mark.asyncio
    async def test_on_chat_empty_content(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        client._on_chat(
            {
                "sessionKey": _TEST_SESSION_KEY,
                "state": "final",
                "message": {"content": []},
            }
        )
        await asyncio.sleep(0)
        assert state.state == "final"
        assert state.content == ""
        assert state.chat_complete.is_set() is True


class TestOnAgent:
    @pytest.mark.asyncio
    async def test_on_agent_tool_result_appends(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "tool",
            "data": {"phase": "result", "tool": "search"},
        }
        client._on_agent(payload)
        assert len(state.agent_payloads) == 1
        assert state.agent_payloads[0] == payload
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_tool_non_result_ignored(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "tool",
            "data": {"phase": "start", "tool": "search"},
        }
        client._on_agent(payload)
        assert len(state.agent_payloads) == 0

    @pytest.mark.asyncio
    async def test_on_agent_assistant_appends(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "assistant",
            "data": {"content": "Hello"},
        }
        client._on_agent(payload)
        assert len(state.agent_payloads) == 1
        assert state.agent_payloads[0] == payload
        assert state.last_stream_is_assistant is True

    @pytest.mark.asyncio
    async def test_on_agent_assistant_replaces_on_consecutive(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        first = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "assistant",
            "data": {"content": "First"},
        }
        second = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "assistant",
            "data": {"content": "Second"},
        }

        client._on_agent(first)
        assert len(state.agent_payloads) == 1

        client._on_agent(second)
        assert len(state.agent_payloads) == 1
        assert state.agent_payloads[0] == second
        assert state.last_stream_is_assistant is True

    @pytest.mark.asyncio
    async def test_on_agent_lifecycle_end_sets_event(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "lifecycle",
            "data": {"phase": "end"},
        }
        client._on_agent(payload)
        await asyncio.sleep(0)
        assert state.agent_complete.is_set() is True
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_lifecycle_non_end_ignored(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "lifecycle",
            "data": {"phase": "start"},
        }
        client._on_agent(payload)
        assert state.agent_complete.is_set() is False
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_unknown_stream_resets_assistant(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)

        state.last_stream_is_assistant = True

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "stream": "unknown",
            "data": {},
        }
        client._on_agent(payload)
        assert state.last_stream_is_assistant is False

    @pytest.mark.asyncio
    async def test_on_agent_final_sets_complete_events(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "state": "final",
            "message": {
                "content": [{"type": "text", "text": "agent final text"}],
            },
        }
        client._on_agent(payload)

        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == "agent final text"

        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == "agent final text"

    @pytest.mark.asyncio
    async def test_on_agent_final_empty_message(self, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws")
        state = _setup_session_state(client)
        state.stream_queue = asyncio.Queue()

        payload = {
            "sessionKey": _TEST_SESSION_KEY,
            "state": "final",
            "message": {},
        }
        client._on_agent(payload)

        assert state.state == "final"
        assert state.agent_complete.is_set()
        assert state.chat_complete.is_set()
        assert state.content == ""

        chunk = state.stream_queue.get_nowait()
        assert chunk.type == "final"
        assert chunk.content == ""


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_send_message_flow(self, mock_bot_ws, mock_bot_ws_instance):

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def simulate_chat_response(*args, **kwargs):
            sk = kwargs["session_key"]
            client._on_chat(
                {
                    "sessionKey": sk,
                    "state": "delta",
                    "message": {"content": [{"text": "partial"}]},
                }
            )
            client._on_chat(
                {
                    "sessionKey": sk,
                    "state": "final",
                    "message": {"content": [{"text": "full response"}]},
                }
            )
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = simulate_chat_response

        content, _ = await client.send_message("Hello!")
        assert content == "full response"

    @pytest.mark.asyncio
    async def test_send_message_with_agent_events(
        self, mock_bot_ws, mock_bot_ws_instance
    ):

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def simulate_with_agent(*args, **kwargs):
            sk = kwargs["session_key"]
            tool_payload = {
                "sessionKey": sk,
                "stream": "tool",
                "data": {"phase": "result", "name": "search"},
            }
            client._on_agent(tool_payload)
            client._on_chat(
                {
                    "sessionKey": sk,
                    "state": "final",
                    "message": {"content": [{"text": "done"}]},
                }
            )
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = simulate_with_agent

        content, agent_payloads = await client.send_message("Hello!")
        assert content == "done"
        assert len(agent_payloads) == 1
        assert agent_payloads[0]["stream"] == "tool"
        assert agent_payloads[0]["data"]["phase"] == "result"

    @pytest.mark.asyncio
    async def test_concurrent_sends_with_different_keys(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        import asyncio

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        client = make_chat_client(uri="ws://host/ws")
        await client.connect()

        async def slow_chat_send(*args, **kwargs):
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = slow_chat_send

        async def send_msg_1():
            return await client.send_message(
                "msg1", session_key="sess-1", wait_result=False
            )

        async def send_msg_2():
            return await client.send_message(
                "msg2", session_key="sess-2", wait_result=False
            )

        results = await asyncio.gather(send_msg_1(), send_msg_2())
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_concurrent_sends_same_key_raises(
        self, mock_bot_ws, mock_bot_ws_instance
    ):
        """同 sessionKey 并发发送在超时后抛 ConcurrentSessionError。"""
        import asyncio

        from secbaas.community.core.service.bot_run._async_chat_client import (
            ConcurrentSessionError,
        )

        mock_bot_ws_instance.connect.return_value = {
            "server": {"host": "srv"},
            "features": {},
        }

        # 使用极短超时，让排队快速超时
        client = make_chat_client(uri="ws://host/ws", session_key_timeout=0.05)
        await client.connect()

        async def never_complete(*args, **kwargs):
            # 模拟一个永远不完成的发送（不设置 chat_complete）
            await asyncio.sleep(10)
            return {"ok": True}

        mock_bot_ws_instance.chat_send.side_effect = never_complete

        async def send_msg():
            return await client.send_message("msg", session_key="same-key")

        t1 = asyncio.create_task(send_msg())
        await asyncio.sleep(0.01)  # 让 t1 开始

        with pytest.raises(ConcurrentSessionError, match="Timed out waiting"):
            await client.send_message("msg", session_key="same-key")

        t1.cancel()
        try:
            await t1
        except (asyncio.CancelledError, Exception):
            pass
