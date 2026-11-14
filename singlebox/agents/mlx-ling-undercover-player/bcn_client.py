"""BCN V3 WebSocket 协议客户端（「谁是卧底」MLX 玩家 Bot 的协议层）。

实现 inclusionAI/Avernet `docs/bot-integration.zh-CN.md` 定义的 Bot 侧协议：
- bot.connect 握手（protocol_version=3，token 重连）
- bot.status 心跳（60s 间隔）
- chat.send 接收节点任务 → ACK run_id（= 请求帧 id）→ 上行 chat final 事件
- chat.inject 静默观察 → 仅 ACK
- chat.abort 精确取消 run_id，屏蔽迟到事件
- chat.history 返回本地会话消息
- 断线指数退避重连（1s → 30s）

用法见 player.py（本层只管协议，不管游戏逻辑）。
"""

import asyncio
import json
import logging
import time
from uuid import uuid4

import websockets

log = logging.getLogger("bcn")


class BcnClient:
    def __init__(self, ws_url, on_task, on_inject=None, on_connected=None):
        """ws_url: ws://127.0.0.1:{BCS_PORT}/ws/bot
        on_task: async (session_key, text, params) -> str  处理一条需要回复的消息，
                 返回回复文本（空串 = 不产生群消息的静默回执）
        on_inject: async (session_key, text, params) -> None  静默观察回调（可选）
        on_connected: async (client) -> None  每次握手成功后回调（onboard 等）
        params 为 chat.send / chat.inject 的完整请求参数，唤醒分类用
        params["session_context"]（is_sender / from_bot_id / message 等）。
        """
        self.ws_url = ws_url
        self.on_task = on_task
        self.on_inject = on_inject
        self.on_connected = on_connected
        self.token = None
        self.bot_uuid = None
        self.session_history = {}  # session_key -> [{"role","content","timestamp"}]
        self.aborted_runs = set()  # 被 abort 的 run_id，屏蔽迟到事件
        self._seq = 0
        self._pending = {}  # 出站 RPC 的 id -> asyncio.Future（如 task.dispatch）
        self._ws = None

    # ---------- 连接生命周期 ----------

    async def run_forever(self):
        backoff = 1.0
        while True:
            try:
                # BCS 恒为本地回环地址，显式禁用环境代理（否则 SOCKS 代理会拦下 ws 连接）
                async with websockets.connect(self.ws_url, max_size=8 * 1024 * 1024,
                                              proxy=None) as ws:
                    self._ws = ws
                    await self._handshake(ws)
                    backoff = 1.0
                    log.info("connected as %s", self.bot_uuid)
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    try:
                        await self._recv_loop(ws)
                    finally:
                        self._ws = None
                        heartbeat.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("connection lost: %s; reconnect in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                # 断线时让未决 RPC 立刻失败，不挂死调用方
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("connection lost"))
                self._pending.clear()

    async def _handshake(self, ws):
        params = {"protocol_version": 3, "client_kind": "custom-engine"}
        if self.token:
            params["token"] = self.token
        await self._send(ws, {"type": "req", "id": "connect-1",
                              "method": "bot.connect", "params": params})
        res = json.loads(await ws.recv())
        if not res.get("ok"):
            raise RuntimeError(f"bot.connect failed: {res.get('error')}")
        payload = res["payload"]
        assert payload["protocol_version"] == 3, "需要 V3 协议"
        assert payload["capabilities"]["unified_run_events"] is True
        assert payload["capabilities"]["canonical_session_id"] is True
        self.token = payload["token"]
        self.bot_uuid = payload["bot_uuid"]
        for k, v in payload.get("env", {}).items():
            import os
            os.environ[k] = v
        if self.on_connected:
            await self.on_connected(self)

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(60)
            await self._send(ws, {"type": "req", "id": f"status-{uuid4().hex[:8]}",
                                  "method": "bot.status", "params": {}})

    # ---------- 收发主循环 ----------

    async def _recv_loop(self, ws):
        async for raw in ws:
            frame = json.loads(raw)
            if frame["type"] == "req":
                await self._handle_req(ws, frame)
            elif frame["type"] == "res":
                fut = self._pending.pop(frame.get("id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(frame)
            # event 帧为服务端下行通知，玩家当前用不到

    async def _handle_req(self, ws, frame):
        method, fid = frame["method"], frame["id"]
        if method == "chat.send":
            params = frame["params"]
            session_key = params["session_key"]  # V3: 必须等于 bcs_session_id
            if not params.get("bcs_session_id"):
                await self._res(ws, fid, ok=False, error={
                    "code": "invalid_request", "message": "V3 要求非空 bcs_session_id"})
                return
            # run_id 用 chat.send 请求帧的 id：服务端把 V3 run scope（bot/session
            # 身份）只注册在请求 id 上（state-machine 节点投递见 collaboration
            # runtime 的 send_claimed_node_dispatch）。自生成 run-uuid 时别名解析
            # 缺失，final 事件会以 "V3 event runId is unknown" 被静默丢弃，节点
            # 只能等 420s 超时——实测 4/4 全丢；对齐请求 id 后节点正常完结
            # （与三局全通的 PydanticAI 玩家 runtime.py 同一约定）。
            run_id = fid
            await self._res(ws, fid, payload={"run_id": run_id})
            asyncio.create_task(self._do_task(ws, run_id, params))
        elif method == "chat.inject":
            await self._res(ws, fid, payload={})
            if self.on_inject:
                text = self._text_of(frame["params"]["message"])
                asyncio.create_task(
                    self.on_inject(frame["params"]["session_key"], text, frame["params"]))
        elif method == "chat.abort":
            rid = frame["params"]["run_id"]
            if rid in self.aborted_runs:
                await self._res(ws, fid, payload={"aborted": True, "aborted_run_ids": []})
            else:
                self.aborted_runs.add(rid)
                await self._res(ws, fid, payload={"aborted": True, "aborted_run_ids": [rid]})
        elif method == "chat.history":
            sk = frame["params"]["session_key"]
            msgs = self.session_history.get(sk, [])[-frame["params"].get("limit", 50):]
            await self._res(ws, fid, payload={"session_key": sk, "messages": msgs})
        else:
            await self._res(ws, fid, ok=False,
                            error={"code": "unknown_method", "message": method})

    async def rpc(self, method, params, timeout=30.0):
        """出站 RPC（如 task.dispatch 派任务），等对应回包。"""
        ws = self._ws
        if ws is None:
            raise ConnectionError("not connected")
        rid = f"rpc-{uuid4().hex[:12]}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send(ws, {"type": "req", "id": rid, "method": method, "params": params})
        frame = await asyncio.wait_for(fut, timeout)
        if not frame.get("ok"):
            raise RuntimeError(f"{method} failed: {frame.get('error')}")
        return frame.get("payload") or {}

    async def _do_task(self, ws, run_id, params):
        session_key = params["session_key"]
        text = self._text_of(params["message"])
        log.info("task received run=%s text=%.200s", run_id, text.replace("\n", "⏎"))
        try:
            reply = await self.on_task(session_key, text, params)
        except Exception as e:
            log.exception("task failed")
            await self._chat_event(ws, run_id, params, "error",
                                   errorCode="MODEL_ERROR", errorMessage=str(e)[:200])
            return
        if run_id in self.aborted_runs:  # 迟到结果丢弃
            return
        self.session_history.setdefault(session_key, []).append(
            {"role": "user", "content": text, "timestamp": int(time.time() * 1000)})
        self.session_history[session_key].append(
            {"role": "assistant", "content": reply, "timestamp": int(time.time() * 1000)})
        try:
            await self._chat_event(ws, run_id, params, "final",
                                   content=reply, stopReason="complete")
        except Exception:
            # 发送失败必须出声：日志只写 "task reply" 会掩盖 final 根本没上线
            log.exception("chat final send FAILED run=%s", run_id)
            return
        log.info("task reply run=%s reply=%.120s", run_id, reply)

    # ---------- 帧构造 ----------

    @staticmethod
    def _text_of(message):
        content = message.get("content")
        if isinstance(content, list):
            return "".join(c.get("text", "") for c in content if c.get("type") == "text")
        return str(content or "")

    async def _send(self, ws, frame):
        await ws.send(json.dumps(frame, ensure_ascii=False))

    async def _res(self, ws, fid, payload=None, ok=True, error=None):
        frame = {"type": "res", "id": fid, "ok": ok}
        if ok:
            frame["payload"] = payload or {}
        else:
            frame["error"] = error
        await self._send(ws, frame)

    async def _chat_event(self, ws, run_id, params, state, **extra):
        self._seq += 1
        payload = {
            "runId": run_id,
            "sessionId": params["bcs_session_id"],
            "seq": self._seq,
            "ts": int(time.time() * 1000),
            "state": state,
            **extra,
        }
        await self._send(ws, {"type": "event", "event": "chat",
                              "payload": payload, "seq": self._seq})
