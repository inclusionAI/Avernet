from __future__ import annotations

import asyncio
import json

from conftest import send_frame
from pydantic_ai import ModelResponse, ToolCallPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel
from websockets.asyncio.server import serve

from pydanticai_player.brain import PydanticPlayer
from pydanticai_player.config import Settings
from pydanticai_player.domain import PlayerAnswer
from pydanticai_player.transport import Connection


async def handshake(ws, *, reconnect=False):
    request = json.loads(await ws.recv())
    assert request["params"]["protocol_version"] == 3
    if reconnect:
        assert request["params"]["token"] == "test-token"
    await ws.send(
        json.dumps(
            {
                "type": "res",
                "id": request["id"],
                "ok": True,
                "payload": {
                    "token": "test-token",
                    "bot_uuid": "test-bot",
                    "protocol_version": 3,
                    "capabilities": {"unified_run_events": True, "canonical_session_id": True},
                },
            }
        )
    )


async def test_websocket_to_real_framework_and_back(tmp_path):
    received = []
    onboarded = []
    model_calls = 0

    def model(messages, info):
        nonlocal model_calls
        model_calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"text": "雨伞。" if model_calls == 1 else "天气变化时会带着。"},
                )
            ]
        )

    async def server(ws):
        await handshake(ws)
        frame = send_frame()
        frame["params"]["message"]["content"][0]["text"] = (
            "[from:谁是卧底主持人] " + frame["params"]["message"]["content"][0]["text"]
        )
        await ws.send(json.dumps(frame))
        received.append(json.loads(await ws.recv()))
        assert received[-1].get("ok") is True
        received.append(json.loads(await ws.recv()))
        await ws.close()

    async def onboard(settings, token):
        onboarded.append(token)

    async with serve(server, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        settings = Settings(
            base_url="http://unused.invalid",
            api_key="test-key",
            model="unused",
            bcs_ws_url=f"ws://127.0.0.1:{port}/ws/bot",
            state_file=tmp_path / "identity.json",
        )
        await asyncio.wait_for(
            Connection(settings, PydanticPlayer(FunctionModel(model)), onboard_bot=onboard).once(),
            5,
        )
    assert onboarded == ["test-token"]
    assert received[0]["type"] == "res"
    assert received[1]["payload"]["content"] == "天气变化时会带着。"
    assert model_calls == 2 and len(received) == 2
    assert received[1]["payload"]["usage"]["output"] >= 0
    assert settings.state_file.stat().st_mode & 0o777 == 0o600


async def test_reconnect_retains_only_the_same_sessions_private_seat(tmp_path):
    connections, prompts, received = 0, [], []

    def model(messages, info):
        prompt = next(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart))
        prompts.append(json.loads(prompt))
        text = "天气变化时会带着。" if len(prompts) == 1 else "我最怀疑3号，描述前后不一致。"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"text": text})])

    async def server(ws):
        nonlocal connections
        connections += 1
        await handshake(ws, reconnect=connections > 1)
        if connections == 1:
            await ws.send(json.dumps(send_frame(request_id="speech")))
        else:
            text = "你在第2轮被投出局了。请说一句遗言，不超过60个字。"
            await ws.send(json.dumps(send_frame("wrong-session", "g:other", text)))
            received.append(json.loads(await ws.recv()))
            await ws.send(json.dumps(send_frame("eulogy", "g:a", text)))
        received.append(json.loads(await ws.recv()))
        assert received[-1].get("ok") is True
        received.append(json.loads(await ws.recv()))
        if connections == 2:
            await ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": "history",
                        "method": "chat.history",
                        "params": {"session_key": "g:a"},
                    }
                )
            )
            received.append(json.loads(await ws.recv()))
        await ws.close()

    async def onboard(settings, token):
        pass

    async with serve(server, "127.0.0.1", 0) as ws_server:
        settings = Settings(
            base_url="http://unused.invalid",
            api_key="test-key",
            model="unused",
            bcs_ws_url=f"ws://127.0.0.1:{ws_server.sockets[0].getsockname()[1]}/ws/bot",
            state_file=tmp_path / "identity.json",
        )
        connection = Connection(settings, PydanticPlayer(FunctionModel(model)), onboard_bot=onboard)
        await asyncio.wait_for(connection.once(), 5)
        await asyncio.wait_for(connection.once(), 5)

    finals = [f["payload"] for f in received if f.get("type") == "event"]
    assert [f["state"] for f in finals] == ["final", "final"]
    assert finals[-1]["runId"] == "eulogy" and finals[-1]["sessionId"] == "g:a"
    wrong = next(f for f in received if f.get("id") == "wrong-session")
    assert wrong["ok"] is False and wrong["error"]["code"] == "invalid_request"
    assert len(prompts) == 2 and prompts[-1]["private_context"] == {"seat": 2, "word": "雨伞"}
    assert received[-1]["payload"]["messages"] == []
    assert set(json.loads(settings.state_file.read_text())) == {"endpoint", "token", "bot_uuid"}


async def test_reconnect_cancels_old_work_before_answering_new_connection(tmp_path):
    started, cancelled = asyncio.Event(), asyncio.Event()
    received, connections = [], 0

    class LatePlayer:
        async def answer(self, task):
            if task.kind == "speech":
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    return PlayerAnswer("旧连接的迟到结果", 1, 0, 0)
            assert cancelled.is_set() and task.seat == 2 and task.word == "雨伞"
            return PlayerAnswer("我最怀疑3号，描述前后不一致。", 1, 0, 0)

    async def server(ws):
        nonlocal connections
        connections += 1
        await handshake(ws, reconnect=connections > 1)
        if connections == 1:
            await ws.send(json.dumps(send_frame(request_id="old-work")))
            received.append(json.loads(await ws.recv()))
            await asyncio.wait_for(started.wait(), 1)
        else:
            text = "你在第2轮被投出局了。请说一句遗言，不超过60个字。"
            await ws.send(json.dumps(send_frame("eulogy", "g:a", text)))
            received.append(json.loads(await ws.recv()))
            assert received[-1].get("ok") is True
            received.append(json.loads(await ws.recv()))
        await ws.close()

    async def onboard(settings, token):
        pass

    async with serve(server, "127.0.0.1", 0) as ws_server:
        settings = Settings(
            base_url="http://unused.invalid",
            api_key="test-key",
            model="unused",
            bcs_ws_url=f"ws://127.0.0.1:{ws_server.sockets[0].getsockname()[1]}/ws/bot",
            state_file=tmp_path / "identity.json",
        )
        connection = Connection(settings, LatePlayer(), onboard_bot=onboard)
        await asyncio.wait_for(connection.once(), 5)
        assert cancelled.is_set()
        await asyncio.wait_for(connection.once(), 5)
    finals = [f["payload"] for f in received if f.get("type") == "event"]
    assert len(finals) == 1 and finals[0]["runId"] == "eulogy"
    assert "旧连接的迟到结果" not in str(received)


async def test_already_connected_retries_with_the_saved_identity(tmp_path):
    connected = asyncio.Event()
    attempts, onboarded = 0, []

    def model(messages, info):
        raise AssertionError("Reconnect must not start inference")

    async def server(ws):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            request = json.loads(await ws.recv())
            assert request["params"]["token"] == "test-token"
            await ws.send(
                json.dumps(
                    {
                        "type": "res",
                        "id": request["id"],
                        "ok": False,
                        "error": {
                            "code": "already_connected",
                            "message": "Previous socket closing",
                        },
                    }
                )
            )
            await ws.close()
        else:
            await handshake(ws, reconnect=True)
            await ws.wait_closed()

    async def onboard(settings, token):
        onboarded.append(token)
        connected.set()

    async with serve(server, "127.0.0.1", 0) as ws_server:
        settings = Settings(
            base_url="http://unused.invalid",
            api_key="test-key",
            model="unused",
            bcs_ws_url=f"ws://127.0.0.1:{ws_server.sockets[0].getsockname()[1]}/ws/bot",
            state_file=tmp_path / "identity.json",
        )
        connection = Connection(settings, PydanticPlayer(FunctionModel(model)), onboard_bot=onboard)
        connection.identity.save("test-token", "test-bot")
        work = asyncio.create_task(connection.run())
        try:
            await asyncio.wait_for(connected.wait(), 4)
        finally:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
    assert attempts == 2 and onboarded == ["test-token"]
    assert connection.identity.load_token() == "test-token"
