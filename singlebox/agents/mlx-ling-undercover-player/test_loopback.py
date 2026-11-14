"""BCN 协议层回环自测：mock 服务器 ↔ BcnClient ↔ GamePlayer（stub 模型）。

覆盖 #2611 关键链路（不需要起 Avernet）：
1. bot.connect 握手（V3 capabilities 校验、token 回存）
2. chat.send → ACK run_id（= 请求帧 id）→ chat final 事件（发言任务端到端）
3. chat.inject → 仅 ACK，不上行事件
4. chat.history → 返回本地会话消息
5. chat.abort → 屏蔽迟到的 final 事件

运行：.venv/bin/python test_loopback.py
"""

import asyncio
import json

import websockets

from bcn_client import BcnClient
from player import GamePlayer, PRESTART_LINE

SPEAK_TASK = """[State Machine Task]
node_id: speak_3
display_name: 3号发言

[Input]
{"game": "谁是卧底", "round": 2, "history": [{"round": 1, "speeches": [{"seat": 1, "player": "小明", "text": "甜的，我很喜欢"}]}]}

[Upstream Outputs]
[speak_1]
超市里常见

[Instruction]
你是 3 号，你的词是【苹果】。第 2 轮发言。
输出一句话，不超过 25 字，描述你的词。
不得出现「苹果」；也不得把它的字拆到一句里说全。"""

ACK_TASK = """[State Machine Task]
node_id: vote_start
display_name: 投票预备确认

[Input]
{"game": "谁是卧底", "round": 2}

[Instruction]
【投票预备确认】请原样输出下面这一句开场白：
投票开始，请大家投出一票，等待主持人公布结果。"""


class MockBcnServer:
    """服务端视角：握手应答 + 主动下发 chat.send / inject / abort / history。"""

    def __init__(self):
        self.ws = None
        self.received_events = []
        self.connected = asyncio.Event()

    async def handler(self, ws):
        self.ws = ws
        try:
            await self._serve(ws)
        except websockets.exceptions.ConnectionClosed:
            pass  # 测试收尾时客户端正常断开

    async def _serve(self, ws):
        async for raw in ws:
            frame = json.loads(raw)
            if frame["type"] == "res":
                self.received_events.append(frame)
            elif frame.get("event") == "chat":
                self.received_events.append(frame)
            elif frame["type"] == "req" and frame["method"] == "bot.connect":
                assert frame["params"]["protocol_version"] == 3
                await ws.send(json.dumps({
                    "type": "res", "id": frame["id"], "ok": True,
                    "payload": {
                        "protocol_version": 3,
                        "capabilities": {"unified_run_events": True,
                                         "canonical_session_id": True},
                        "token": "tok-loopback", "bot_uuid": "bot-loopback",
                        "env": {"BCS_TEST": "1"},
                    }}))
                self.connected.set()
            elif frame["type"] == "req" and frame["method"] == "bot.status":
                await ws.send(json.dumps(
                    {"type": "res", "id": frame["id"], "ok": True, "payload": {}}))

    async def send_chat(self, method, params):
        fid = f"{method}-x"
        await self.ws.send(json.dumps(
            {"type": "req", "id": fid, "method": method, "params": params}))
        return fid

    async def wait_event(self, pred, timeout=5.0):
        async def poll():
            while True:
                for fr in self.received_events:
                    if pred(fr):
                        return fr
                await asyncio.sleep(0.02)
        return await asyncio.wait_for(poll(), timeout)


def stub_infer(prompt):
    if "请只输出「我投N号」" in prompt:
        return "我投1号"
    return "甜脆多汁，一年四季都有"


async def main():
    server = MockBcnServer()
    async with websockets.serve(server.handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]

        player = GamePlayer(stub_infer, undercover_words={"梨"})
        client = BcnClient(f"ws://127.0.0.1:{port}/ws/bot", on_task=player.on_task)
        runner = asyncio.create_task(client.run_forever())
        await asyncio.wait_for(server.connected.wait(), 5)
        # 等客户端处理完握手响应（server 发出响应 ≠ client 已置 token）
        while client.token is None:
            await asyncio.sleep(0.01)
        assert client.token == "tok-loopback" and client.bot_uuid == "bot-loopback"
        assert client.session_history == {}
        print("1. 握手 OK（V3 capabilities 校验通过，token 回存）")

        # 2. 发言任务：ACK run_id → final 事件
        params = {"session_key": "bcs-sess-1", "bcs_session_id": "bcs-sess-1",
                  "message": {"role": "user", "content": SPEAK_TASK}}
        await server.send_chat("chat.send", params)
        ack = await server.wait_event(lambda f: f.get("type") == "res" and f["id"] == "chat.send-x")
        # run_id 必须等于 chat.send 请求帧 id（服务端按请求 id 注册 V3 run scope）
        assert ack["ok"] and ack["payload"]["run_id"] == "chat.send-x", ack
        ev = await server.wait_event(lambda f: f.get("event") == "chat" and f["payload"].get("state") == "final")
        reply = ev["payload"]["content"]
        assert ev["payload"]["runId"] == ack["payload"]["run_id"]
        assert reply == "甜脆多汁，一年四季都有", reply
        assert ev["payload"]["sessionId"] == "bcs-sess-1"
        print("2. chat.send 链路 OK（ACK run_id → final 事件，发言原样回传）")

        # ack 任务：原样念开场白
        params2 = {"session_key": "bcs-sess-1", "bcs_session_id": "bcs-sess-1",
                   "message": {"role": "user", "content": ACK_TASK}}
        await server.send_chat("chat.send", params2)
        await server.wait_event(lambda f: f.get("event") == "chat"
                                and f["payload"].get("content") == PRESTART_LINE)
        print("3. 投票预备确认 OK（原样输出开场白）")

        # 3. inject：仅 ACK，无事件
        n_events = len([f for f in server.received_events if f.get("event") == "chat"])
        await server.send_chat("chat.inject", {
            "session_key": "bcs-sess-1", "bcs_session_id": "bcs-sess-1",
            "message": {"role": "user", "content": "（旁白）天黑请闭眼"}})
        await server.wait_event(lambda f: f.get("type") == "res" and f["id"] == "chat.inject-x")
        await asyncio.sleep(0.1)
        n_events2 = len([f for f in server.received_events if f.get("event") == "chat"])
        assert n_events2 == n_events, "inject 不应产生上行事件"
        print("4. chat.inject OK（仅 ACK，静默）")

        # 4. history：返回两轮会话
        await server.send_chat("chat.history",
                               {"session_key": "bcs-sess-1", "limit": 10})
        hist = await server.wait_event(lambda f: f.get("type") == "res" and f["id"] == "chat.history-x")
        msgs = hist["payload"]["messages"]
        assert len(msgs) == 4 and msgs[-1]["role"] == "assistant", len(msgs)
        print(f"5. chat.history OK（返回 {len(msgs)} 条本地消息）")

        # 5. abort：慢推理任务被取消，迟到 final 被屏蔽
        def slow_infer(prompt):
            import time
            time.sleep(0.4)
            return "迟到的发言"

        client.on_task = GamePlayer(slow_infer, undercover_words=set()).on_task
        server.received_events.clear()
        await server.send_chat("chat.send", {
            "session_key": "bcs-sess-2", "bcs_session_id": "bcs-sess-2",
            "message": {"role": "user", "content": SPEAK_TASK}})
        await server.wait_event(lambda f: f.get("type") == "res" and f["id"] == "chat.send-x")
        new_run = [f for f in server.received_events if f.get("payload", {}).get("run_id")][0]["payload"]["run_id"]
        await server.send_chat("chat.abort", {"run_id": new_run})
        await server.wait_event(lambda f: f.get("type") == "res" and f["id"] == "chat.abort-x")
        await asyncio.sleep(0.8)  # 慢任务 0.4s 后返回，确认 final 被吞
        finals = [f for f in server.received_events
                  if f.get("event") == "chat" and f["payload"].get("runId") == new_run]
        assert not finals, f"abort 后不应有 final 事件: {finals}"
        print("6. chat.abort OK（run_id 精确取消，迟到 final 被屏蔽）")

        runner.cancel()
    print("ALL LOOPBACK TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
