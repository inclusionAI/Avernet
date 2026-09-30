"""singlebox e2e — 创建会话、发送消息并验证大模型回复。

链路:
  1. backend (8888) 查 bot → 解析 engine target
  2. engine adapter (20010) POST /api/sessions 创建 session
  3. WebSocket ws://{target}/api/openclaw/ws 发送消息并等待回复
  4. 回查 session 列表 + 消息历史确认

环境变量:
    SINGLEBOX_SESSION_E2E=1   启用本测试(默认 skip)
    SINGLEBOX_BACKEND_URL     backend 地址,默认 http://localhost:8888
    SINGLEBOX_USER_ID         用户工号,默认 440718
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest

import httpx

_LIVE = os.environ.get("SINGLEBOX_SESSION_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_SESSION_E2E=1 启用")
class TestCreateSessionE2E(unittest.TestCase):
    """在 singlebox 下创建一个引擎会话,并验证它出现在 engine session 列表中。"""

    def test_create_session_and_verify(self) -> None:
        with httpx.Client(timeout=30.0, headers=_HDRS) as cli:
            # 1) 查已有 bot
            bots: list[dict] = []
            for endpoint in [
                f"{_BACKEND}/api/bots/by-owner-or-collaborator",
                f"{_BACKEND}/api/bots",
            ]:
                try:
                    r = cli.get(endpoint, params={"user_id": _USER_ID})
                    if r.status_code == 200:
                        bots = (r.json().get("data") or {}).get("items") or []
                        if bots:
                            break
                except Exception:
                    continue
            if not bots:
                self.skipTest("singlebox 未 provision 任何 bot,请先 start all")

            bot = bots[0]
            bot_id = bot["bot_id"]
            owner_id = bot.get("owner_id", _USER_ID)
            print(f"[bot] bot_id={bot_id} owner_id={owner_id}")

            # 2) 从 bot 信息取 binding_id → 查 device connection 拿 engine target
            #    链路同 singlebox_engine_adapter._resolve_target():
            #    GET /api/bots/{bot_id} → binding_id → GET /api/v1/devices/{binding_id}/connection
            bot_resp = cli.get(f"{_BACKEND}/api/bots/{bot_id}")
            bot_resp.raise_for_status()
            binding_id = (bot_resp.json().get("data") or {}).get("binding_id")
            self.assertIsNotNone(binding_id, f"bot {bot_id} 无 binding_id")
            conn_resp = cli.get(f"{_BACKEND}/api/v1/devices/{binding_id}/connection")
            conn_resp.raise_for_status()
            target = (conn_resp.json().get("data") or {}).get("target") or ""
            self.assertTrue(target, f"未取到 engine target: {conn_resp.json()}")
            print(f"[engine] target={target} (binding_id={binding_id})")

        # 3) 直连 engine adapter 创建 session
        engine_url = f"http://{target}"
        with httpx.Client(timeout=30.0, headers=_HDRS) as cli:
            create_resp = cli.post(
                f"{engine_url}/api/sessions",
                json={
                    "title": "e2e-test-session",
                    "user_id": _USER_ID,
                    "agent_id": bot_id,
                },
            )
            create_resp.raise_for_status()
            session = create_resp.json().get("data") or {}
            session_id = session.get("id") or ""
            self.assertTrue(session_id, f"创建 session 返回 id 为空: {session}")
            print(f"[created] session_id={session_id}")

            # 4) 回查 engine session 列表确认存在
            #    create 返回 "session:xxx:user:440718",list 返回 "agent:main:session:xxx:user:440718"
            #    前缀差异由引擎内部 agent 路由产生,用 endswith 匹配避免硬编码前缀。
            list_resp = cli.get(
                f"{engine_url}/api/sessions",
                params={"limit": 100, "offset": 0, "user_id": _USER_ID},
            )
            list_resp.raise_for_status()
            sessions = list_resp.json().get("data") or []
            found = any(
                s.get("id") == session_id or s.get("id", "").endswith(session_id)
                for s in sessions
            )
            self.assertTrue(
                found,
                f"创建的 session_id={session_id} 未在 engine 列表中找到",
            )
            print(f"[verified] session 已在 engine 列表中确认存在")

            # 5) 通过 WebSocket 向 session 发送消息并等待大模型回复
            #    协议同 singlebox_engine_adapter._ws_chat_roundtrip():
            #    connect(proto3 握手) → chat.send → 收到 state=final 事件
            reply = asyncio.run(self._ws_chat(target, session_id, "你好呀"))
            print(f"[chat] 大模型回复: {reply}")
            self.assertTrue(reply, "大模型回复为空")

            # 6) 回查消息历史确认 user + assistant 两条记录
            import base64
            encoded_id = base64.urlsafe_b64encode(session_id.encode()).decode()
            msg_resp = cli.get(
                f"{engine_url}/api/sessions/{encoded_id}/messages",
                params={"limit": 10, "offset": 0},
            )
            msg_resp.raise_for_status()
            messages = msg_resp.json().get("data") or []
            roles = [m.get("role") for m in messages]
            self.assertIn("user", roles, "消息历史中缺少 user 消息")
            self.assertIn("assistant", roles, "消息历史中缺少 assistant 消息")
            print(f"[messages] 共 {len(messages)} 条, roles={roles}")

    async def _ws_chat(self, target: str, session_key: str, message: str) -> str:
        """开 WebSocket:connect 握手 → chat.send → 读到 final → 返回回复文本。"""
        import websockets

        ws_path = "/api/openclaw/ws"
        uri = f"ws://{target}{ws_path}"
        connect_params = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {"id": "e2e-test", "version": "1.0.0", "platform": "linux", "mode": "operator"},
            "role": "operator",
        }

        async with websockets.connect(uri, open_timeout=10) as ws:
            # 1) 握手
            await ws.send(json.dumps({
                "type": "req", "id": "1", "method": "connect", "params": connect_params,
            }))
            hs = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if not hs.get("ok"):
                return f"[握手失败] {json.dumps(hs)[:200]}"

            # 2) 发消息
            await ws.send(json.dumps({
                "type": "req", "id": "2", "method": "chat.send",
                "params": {"sessionKey": session_key, "message": message},
            }))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if not ack.get("ok"):
                return f"[发送被拒绝] {json.dumps(ack)[:200]}"

            # 3) 读事件到 final
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    return "[超时] 60秒内未收到回复"
                data = json.loads(raw)
                if data.get("type") != "event" or data.get("event") != "chat":
                    continue
                payload = data.get("payload") or {}
                state = payload.get("state")
                if state == "final":
                    message_obj = payload.get("message") or {}
                    contents = message_obj.get("content") or []
                    texts = [
                        c.get("text", "") for c in contents
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    return "\n".join(texts) if texts else json.dumps(payload, ensure_ascii=False)[:500]
                if state == "error":
                    return f"[错误] {payload.get('errorMessage', 'unknown')}"


if __name__ == "__main__":
    unittest.main()