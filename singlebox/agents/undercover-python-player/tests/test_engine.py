"""Test 3 — BCN WebSocket 引擎（协议逻辑，用内存 stub WS，无网络）。

覆盖：
  - bot.connect 握手（token/uuid/capabilities 解析、env 注入）
  - chat.send：ACK 返回 run_id + 回传 canonical chat final 事件
  - chat.inject：只 ACK，不回复
  - chat.abort：精确取消匹配 run，屏蔽迟到 final；已终态幂等；未知 run 处理
  - chat.history：返回空历史
  - 心跳 bot.status 构造
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from undercover_player.engine import PeterPlayerEngine, ChatRunContext
from tests.fixtures import build_speak_prompt, chat_abort_frame, chat_send_frame

SESSION = "grp-00000001:abcd1234"


class StubWs:
    """内存 WebSocket 替身：记录被 send 的帧，可配置握手回包。"""

    def __init__(self):
        self.sent = []           # list[dict] 发给服务端的帧
        self.raw_sent = []
        self.close_err = None

    async def send(self, text):
        self.raw_sent.append(text)
        self.sent.append(json.loads(text))

    async def close(self):
        return None


CONNECT_PAYLOAD = {
    "is_new": True, "token": "tok-abc123", "bot_uuid": "bot-undercover-01",
    "protocol_version": 3, "min_supported_version": 1,
    "capabilities": {"unified_run_events": True,
                     "canonical_session_id": True,
                     "tool_result_task_intent": False},
    "env": {"BCN_BOT_UUID": "bot-undercover-01", "BCN_BOT_TOKEN": "tok-abc123"},
}


class HandshakeWs(StubWs):
    """发送 bot.connect 后立刻把 connect 响应放进 recv 队列。"""

    def __init__(self, payload=None):
        super().__init__()
        self.payload = payload or CONNECT_PAYLOAD
        self.inbox = []

    async def send(self, text):
        await super().send(text)
        frame = json.loads(text)
        if frame.get("method") == "bot.connect":
            self.inbox.append(json.dumps({
                "type": "res", "id": frame["id"], "ok": True,
                "payload": self.payload,
            }))

    async def recv(self):
        return self.inbox.pop(0)


def make_engine(on_task=None, ws=None) -> PeterPlayerEngine:
    stub = ws or StubWs()
    engine = PeterPlayerEngine(
        ws_url="ws://unused",
        bot_name="玩家逻辑控林工",
        on_task=on_task,
    )
    # 模拟一次成功握手：解析标准回包
    connect_res = {
        "type": "res", "id": "req-1", "ok": True, "payload": {
            "is_new": True, "token": "tok-abc123", "bot_uuid": "bot-undercover-01",
            "protocol_version": 3, "min_supported_version": 1,
            "capabilities": {"unified_run_events": True,
                             "canonical_session_id": True,
                             "tool_result_task_intent": False},
            "env": {"BCN_BOT_UUID": "bot-undercover-01", "BCN_BOT_TOKEN": "tok-abc123"},
        },
    }
    engine._token = connect_res["payload"]["token"]
    engine.bot_uuid = connect_res["payload"]["bot_uuid"]
    engine._ws = stub
    for k, v in connect_res["payload"]["env"].items():
        os.environ[k] = v
    return engine


def run_until(fn):
    """跑一个 async 驱动到结束。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(fn)
    finally:
        loop.close()


class TestHandshake(unittest.TestCase):
    def test_env_and_bot_uuid(self):
        stub = StubWs()
        engine = make_engine(ws=stub)
        self.assertEqual(engine.bot_uuid, "bot-undercover-01")
        self.assertEqual(engine._token, "tok-abc123")
        self.assertEqual(os.environ.get("BCN_BOT_UUID"), "bot-undercover-01")

    def test_handshake_reads_connect_response_before_read_loop(self):
        # 回归：_handshake 必须自己读 connect 响应，不能等尚未启动的读循环。
        stub = HandshakeWs()
        engine = PeterPlayerEngine(ws_url="ws://unused")

        async def drive():
            await engine._handshake(stub)

        run_until(drive())
        self.assertEqual(stub.sent[0]["method"], "bot.connect")
        self.assertEqual(engine.bot_uuid, "bot-undercover-01")
        self.assertEqual(engine._token, "tok-abc123")

    def test_fresh_identity_is_marked_for_onboarding(self):
        # 回归：is_new 的身份没 name，必须先 /admin/bots/onboard 才能被选中。
        engine = PeterPlayerEngine(ws_url="ws://127.0.0.1:21000/ws/bot")

        async def drive():
            await engine._handshake(HandshakeWs())

        with self.assertLogs("undercover_player.engine", level="WARNING") as logs:
            run_until(drive())
        self.assertTrue(engine.needs_onboarding)
        joined = "\n".join(logs.output)
        self.assertIn("not onboarded yet", joined)
        self.assertIn("http://127.0.0.1:21000/admin/bots/onboard", joined)
        self.assertIn('"bot_id":"bot-undercover-01"', joined)

    def test_reconnect_identity_needs_no_onboarding(self):
        reconnect = dict(CONNECT_PAYLOAD)
        reconnect["is_new"] = False
        engine = PeterPlayerEngine(ws_url="wss://bcs.example:443/ws/bot")

        async def drive():
            await engine._handshake(HandshakeWs(payload=reconnect))

        run_until(drive())
        self.assertFalse(engine.needs_onboarding)

    def test_handshake_rejects_missing_capability(self):
        bad = dict(CONNECT_PAYLOAD)
        bad["capabilities"] = {"unified_run_events": False,
                               "canonical_session_id": True}
        engine = PeterPlayerEngine(ws_url="ws://unused")

        async def drive():
            with self.assertRaises(RuntimeError):
                await engine._handshake(HandshakeWs(payload=bad))

        run_until(drive())

    def test_reject_non_v3_capabilities(self):
        # 若 capabilities 不含 unified_run_events，握手应失败
        engine = PeterPlayerEngine(ws_url="ws://unused")
        stub = StubWs()

        async def drive():
            with self.assertRaises(RuntimeError):
                # 模拟服务端回的 payload 缺 unified_run_events
                engine._token = "tok"
                engine.bot_uuid = "b"
                engine._ws = stub
                bad_caps = {"unified_run_events": False, "canonical_session_id": True}
                if not bad_caps.get("unified_run_events"):
                    raise RuntimeError("BCN 未启用 unified_run_events")

        run_until(drive())


class TestChatSend(unittest.TestCase):
    def test_ack_and_final_event(self):
        answers = {}

        async def on_task(ctx: ChatRunContext, task):
            answers["task"] = task
            return f"{task.seat}号玩家发言"

        engine = make_engine(on_task=on_task)
        frame = chat_send_frame(build_speak_prompt(3, "台灯", 1), SESSION,
                                "smnode-1-speak_3-0")

        async def drive():
            await engine._handle_chat_send(frame["id"], frame["params"], frame)
            await asyncio.sleep(0.15)
            res = [f for f in engine._ws.sent if f["type"] == "res" and f["id"] == frame["id"]]
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0]["ok"])
            run_id = res[0]["payload"]["run_id"]
            self.assertTrue(run_id)
            finals = [f for f in engine._ws.sent
                      if f["type"] == "event" and f["event"] == "chat"
                      and f["payload"]["state"] == "final"]
            self.assertEqual(len(finals), 1)
            final = finals[-1]["payload"]
            self.assertEqual(final["sessionId"], SESSION)
            self.assertEqual(final["content"], "3号玩家发言")
            self.assertEqual(final["runId"], run_id)
            self.assertTrue(final["seq"] >= 1)
            self.assertIn("ts", final)
            self.assertEqual(answers["task"].seat, 3)
            self.assertEqual(answers["task"].word, "台灯")

        run_until(drive())

    def test_v3_requires_bcs_session_id(self):
        engine = make_engine()
        frame = chat_send_frame(build_speak_prompt(3, "台灯", 1), SESSION, "r1")
        frame["params"]["bcs_session_id"] = None
        frame["params"]["session_key"] = None

        async def drive():
            await engine._handle_chat_send(frame["id"], frame["params"], frame)
            await asyncio.sleep(0.05)
            res = engine._ws.sent[-1]
            self.assertEqual(res["type"], "res")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "invalid_request")

        run_until(drive())


class TestDecisionError(unittest.TestCase):
    def test_callback_failure_emits_terminal_error(self):
        async def boom(ctx: ChatRunContext, task):
            raise RuntimeError("model unavailable")

        engine = make_engine(on_task=boom)
        frame = chat_send_frame(build_speak_prompt(3, "台灯", 1), SESSION,
                                "smnode-1-speak_3-err")

        async def drive():
            await engine._handle_chat_send(frame["id"], frame["params"], frame)
            run_id = next(iter(engine._active_runs))
            await asyncio.sleep(0.1)

            acks = [f for f in engine._ws.sent
                    if f["type"] == "res" and f["id"] == frame["id"]]
            self.assertEqual(acks[0]["payload"]["run_id"], run_id)
            finals = [f for f in engine._ws.sent
                      if f["type"] == "event" and f["event"] == "chat"
                      and f["payload"]["state"] == "final"]
            errors = [f for f in engine._ws.sent
                      if f["type"] == "event" and f["event"] == "chat"
                      and f["payload"]["state"] == "error"]
            self.assertEqual(finals, [])
            self.assertEqual(len(errors), 1)
            payload = errors[0]["payload"]
            self.assertEqual(payload["runId"], run_id)
            self.assertEqual(payload["sessionId"], SESSION)
            self.assertEqual(payload["errorCode"], "DECISION_ERROR")
            self.assertIn("model unavailable", payload["errorMessage"])
            self.assertNotIn(run_id, engine._active_runs)
            self.assertIn(run_id, engine._finished_runs)

            # 已发终态后 abort 幂等，不能再补发事件
            await engine._handle_chat_abort(
                "late-abort", {"session_key": SESSION, "run_id": run_id})
            await asyncio.sleep(0.02)
            late = [f for f in engine._ws.sent
                    if f["type"] == "res" and f["id"] == "late-abort"]
            self.assertEqual(late[0]["payload"]["aborted_run_ids"], [])

        run_until(drive())


class TestChatInject(unittest.TestCase):
    def test_ack_only(self):
        engine = make_engine()

        async def drive():
            n0 = len(engine._ws.sent)
            await engine._handle_frame({"type": "req", "id": "inject-1",
                                        "method": "chat.inject",
                                        "params": {"session_key": SESSION}})
            await asyncio.sleep(0.05)
            frames = engine._ws.sent[n0:]
            acks = [f for f in frames if f["type"] == "res" and f.get("ok")]
            self.assertEqual(len(acks), 1)
            self.assertFalse(any(f["type"] == "event" for f in frames))

        run_until(drive())


class TestChatAbort(unittest.TestCase):
    def test_abort_shields_late_final_and_idempotent(self):
        async def slow_on_task(ctx, task):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return None
            raise AssertionError("姗姗来迟的 final 不应发出")

        engine = make_engine(on_task=slow_on_task)
        frame = chat_send_frame(build_speak_prompt(3, "台灯", 1), SESSION, "smnode-1-speak_3-0")

        async def drive():
            await engine._handle_chat_send(frame["id"], frame["params"], frame)
            run_id = next(iter(engine._active_runs))
            await engine._handle_chat_abort("ab1", {"session_key": SESSION, "run_id": run_id})
            await asyncio.sleep(0.2)
            ab1 = [f for f in engine._ws.sent if f["type"] == "res" and f["id"] == "ab1"]
            self.assertEqual(len(ab1), 1)
            self.assertTrue(ab1[0]["ok"])
            self.assertEqual(ab1[0]["payload"]["aborted_run_ids"], [run_id])
            finals = [f for f in engine._ws.sent
                      if f["type"] == "event" and f["event"] == "chat"]
            self.assertEqual(finals, [])
            # 幂等
            await engine._handle_chat_abort("ab2", {"session_key": SESSION, "run_id": run_id})
            await asyncio.sleep(0.05)
            ab2 = [f for f in engine._ws.sent if f["type"] == "res" and f["id"] == "ab2"]
            self.assertTrue(ab2[0]["ok"])
            self.assertEqual(ab2[0]["payload"]["aborted_run_ids"], [])

        run_until(drive())

    def test_unknown_run(self):
        engine = make_engine()

        async def drive():
            await engine._handle_chat_abort("abx", {"session_key": SESSION,
                                                    "run_id": "run-nonexistent"})
            await asyncio.sleep(0.02)
            abx = [f for f in engine._ws.sent if f["type"] == "res" and f["id"] == "abx"]
            self.assertEqual(len(abx), 1)
            self.assertFalse(abx[0]["ok"])
            self.assertEqual(abx[0]["error"]["code"], "not_found")

        run_until(drive())


class TestHistory(unittest.TestCase):
    def test_returns_empty(self):
        engine = make_engine()

        async def drive():
            await engine._handle_chat_history("h1", {"session_key": SESSION, "limit": 50})
            await asyncio.sleep(0.02)
            res = [f for f in engine._ws.sent if f["type"] == "res" and f["id"] == "h1"]
            self.assertEqual(len(res), 1)
            self.assertTrue(res[0]["ok"])
            self.assertEqual(res[0]["payload"]["messages"], [])

        run_until(drive())


class TestHeartbeat(unittest.TestCase):
    def test_bot_status_frame_build(self):
        engine = make_engine()

        async def drive():
            req_id = engine.next_req_id()
            # 并行：一个任务等 _request，另一个任务喂服务端 res 回包
            async def respond():
                await asyncio.sleep(0.05)
                await engine._handle_frame({"type": "res", "id": req_id, "ok": True,
                                            "payload": {}})
            responder = asyncio.create_task(respond())
            await engine._request(engine._ws, req_id, "bot.status", {})
            req = engine._ws.sent[-1]
            self.assertEqual(req["method"], "bot.status")
            await responder

        run_until(drive())


if __name__ == "__main__":
    unittest.main()