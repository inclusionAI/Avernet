"""BCN WebSocket 引擎：协议级接入，玩家引擎核心。

实现 docs/bot-integration.zh-CN.md 规定的 V3 协议：
  - bot.connect 握手（含重连 token），bot.status 心跳（60s）
  - 处理 chat.send（需回复，ACK 回 run_id + 回传 canonical chat final 事件）
  - 处理 chat.inject（仅 ACK，静默观察）
  - 处理 chat.abort（精确取消匹配 session_key+run_id，屏蔽迟到事件，幂等）
  - 处理 chat.history
  - 指数退避重连（1s→2s→...→最大 30s），同 Bot 活跃连接拒绝重试

engine.py 本文件需要一个由 agent 实现的「决策回调」，把 NodeTask 转成一句话。
本引擎不夹带任何 LLM；把「谁说话」交给逻辑回调，协议与格式分开验证。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Optional

from .message_model import NodeTask

log = logging.getLogger("undercover_player.engine")

# BCN 心跳建议间隔 60s，TTL 5 分钟
HEARTBEAT_INTERVAL_S = 60
# 重连退避：初始 1s，最大 30s
RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 30
PROTOCOL_VERSION = 3

# 决策回调：输入 NodeTask，返回最终话术（None 表示弃权/不回复）
DecisionHandler = Callable[["ChatRunContext", NodeTask], Awaitable[Optional[str]]]


class ChatRunContext:
    """一次 chat.send 处理的运行上下文：给决策回调看当前轮次、座位、词。"""

    def __init__(self, run_id: str, session_key: str, task: NodeTask):
        self.run_id = run_id
        self.session_key = session_key
        self.task = task


class LateMessageError(RuntimeError):
    """该 run 已终态/被 abort，禁止提交迟到事件。"""


class PeterPlayerEngine:
    """一个玩家 Bot 的 BCN 客户端。

    on_task: 决策回调（由 agent/逻辑决定怎么说、投谁），签名为
             async (ctx, task) -> str | None
    """

    def __init__(
        self,
        ws_url: str,
        bot_name: str = "玩家逻辑控林工",
        client_kind: str = "undercover-python-player",
        token: str | None = None,
        on_task: Optional[DecisionHandler] = None,
        heartbeat_s: int = HEARTBEAT_INTERVAL_S,
    ):
        self.ws_url = ws_url
        self.bot_name = bot_name
        self.client_kind = client_kind
        self._token = token
        self.on_task = on_task
        self.heartbeat_s = heartbeat_s
        self.bot_uuid: str | None = None

        # 活跃 run：run_id -> asyncio.Task（正在跑/待决策）
        self._active_runs: dict[str, asyncio.Task] = {}
        # 已终态 run：run_id -> True（屏蔽迟到事件 / abort 幂等）
        self._finished_runs: set[str] = set()
        # 每个 session_key 下已见过的 run_id（chat.history 也能用到）
        self._session_runs: dict[str, set[str]] = defaultdict(set)

        self._ws = None
        self._seq = 0
        self._request_seq = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._connect_fut: Optional[asyncio.Future] = None
        self._closed = asyncio.Event()
        self._stop = False

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def next_req_id(self) -> str:
        self._request_seq += 1
        return f"req-{self._request_seq}"

    async def start(self) -> None:
        self._closed.clear()
        self._stop = False
        await self._connect_loop()

    async def stop(self) -> None:
        self._stop = True
        self._closed.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        # 取消所有活跃 run
        for t in list(self._active_runs.values()):
            t.cancel()
        self._active_runs.clear()

    async def _connect_loop(self) -> None:
        delay = RECONNECT_MIN_DELAY_S
        first = True
        while not self._stop:
            try:
                if not first:
                    log.info("reconnecting in %ss (backoff)", delay)
                    await asyncio.sleep(delay)
                else:
                    first = False
                self._ws = await self._open_ws()
                delay = RECONNECT_MIN_DELAY_S
                # 事件处理循环会循环执行，直到断开
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("connection error: %s", exc)
                delay = min(delay * 2, RECONNECT_MAX_DELAY_S)
            finally:
                try:
                    if self._ws is not None:
                        await self._ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ws = None
                ws_ev = getattr(self, "_ws_ready", None)
                if ws_ev is not None:
                    ws_ev.set()  # 让心跳/读循环退出

    async def _open_ws(self) -> Any:
        import websockets

        ws = await websockets.connect(self.ws_url)
        try:
            await self._handshake(ws)
        except BaseException:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        return ws

    async def _handshake(self, ws) -> None:
        """发送 bot.connect 并直接读取它的响应。

        此刻 ``_read_loop`` 还没启动，不能走 ``_request()``：那个 future 只有
        ``_read_loop`` 里的 ``_handle_frame`` 才能完成，先等 ACK 会死锁到超时。
        """
        params: dict[str, Any] = {"protocol_version": PROTOCOL_VERSION,
                                  "client_kind": self.client_kind}
        if self._token:
            params["token"] = self._token
        req_id = self.next_req_id()
        await ws.send(json.dumps({"type": "req", "id": req_id,
                                  "method": "bot.connect", "params": params}))
        resp = await self._await_response(ws, req_id)
        if not resp.get("ok"):
            code = (resp.get("error") or {}).get("code")
            msg = (resp.get("error") or {}).get("message", "")
            raise ConnectionRefusedError(f"bot.connect failed: {code}: {msg}")
        payload = resp["payload"]
        self._token = payload.get("token", self._token)
        self.bot_uuid = payload.get("bot_uuid", self.bot_uuid)
        cap = payload.get("capabilities", {})
        if cap.get("unified_run_events") is not True:
            raise RuntimeError("BCN 未启用 unified_run_events，拒绝非 V3 兼容连接")
        if cap.get("canonical_session_id") is not True:
            raise RuntimeError("BCN 未启用 canonical_session_id，拒绝非 V3 兼容连接")
        # 设置 env
        for k, v in payload.get("env", {}).items():
            import os
            os.environ[k] = str(v)
        log.info("bot connected: uuid=%s name=%s", self.bot_uuid, self.bot_name)

    async def _await_response(self, ws, req_id: str, timeout: float = 30) -> dict:
        """在启动读循环前，直接读取与 ``req_id`` 匹配的响应帧。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"no response for {req_id}")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                log.warning("skip non-JSON handshake frame: %.120s", raw)
                continue
            if frame.get("type") == "res" and frame.get("id") == req_id:
                return frame
            # 握手阶段服务端不应先派请求；记录后继续等握手回包。
            log.debug("ignore pre-handshake frame: %.120s", raw)

    # ------------------------------------------------------------------ #
    # 基于 ws 的读写环
    # ------------------------------------------------------------------ #
    async def _read_loop(self) -> None:
        assert self._ws is not None
        ws = self._ws
        heartbeat = asyncio.create_task(self._heartbeat(ws))
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("skip non-JSON frame: %.120s", raw)
                    continue
                await self._handle_frame(frame)
        finally:
            heartbeat.cancel()

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_s)
                try:
                    req_id = self.next_req_id()
                    resp = await self._request(ws, req_id, "bot.status", {})
                    if not resp.get("ok"):
                        log.warning("bot.status error: %s", resp.get("error"))
                except Exception as exc:  # noqa: BLE001
                    log.debug("heartbeat failed: %s", exc)
                    return
        except asyncio.CancelledError:
            pass

    async def _request(self, ws, req_id: str, method: str, params: dict) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await ws.send(json.dumps({"type": "req", "id": req_id,
                                      "method": method, "params": params}))
            return await asyncio.wait_for(fut, timeout=30)
        finally:
            self._pending.pop(req_id, None)

    async def _handle_frame(self, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype == "res":
            rid = frame.get("id")
            fut = self._pending.get(rid)
            if fut is not None and not fut.done():
                fut.set_result(frame)
            return

        if ftype == "event":
            # 单向推送（非本引擎关注的 run 事件），忽略
            return

        # 否则是 req —— 服务端下行请求
        method = frame.get("method")
        rid = frame.get("id")
        params = frame.get("params") or {}
        if method == "chat.send":
            await self._handle_chat_send(rid, params, frame)
        elif method == "chat.inject":
            await self._ack(rid, {})
        elif method == "chat.abort":
            await self._handle_chat_abort(rid, params)
        elif method == "chat.history":
            await self._handle_chat_history(rid, params)
        else:
            log.warning("unknown server request: %s", method)
            await self._ack_err(rid, "unknown_method", f"unknown method: {method}")

    async def _ack(self, rid: str, payload: dict) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "res", "id": rid,
                                        "ok": True, "payload": payload}))

    async def _ack_err(self, rid: str, code: str, message: str) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "res", "id": rid, "ok": False,
                                        "error": {"code": code, "message": message,
                                                  "retryable": False, "retry_after_ms": None}}))

    # ------------------------------------------------------------------ #
    # chat.send：节点任务
    # ------------------------------------------------------------------ #
    async def _handle_chat_send(self, rid: str, params: dict, frame: dict) -> None:
        run_id = f"run-{rid}"  # 引擎自生成 run_id（也可用 frame id）
        message = params.get("message") or {}
        content = _extract_text(message.get("content"))
        session_key = params.get("bcs_session_id") or params.get("session_key")
        if not session_key:
            await self._ack_err(rid, "invalid_request", "V3 chat.send 必须携带 bcs_session_id")
            return

        # 立即 ACK，返回 run_id
        await self._ack(rid, {"run_id": run_id})
        # ACK 完成，解析节点任务并后台处理
        self._session_runs[session_key].add(run_id)

        from .message_model import parse_node_task
        node_task = parse_node_task(content)

        # 启动后台处理，稍后回传
        processor = asyncio.get_running_loop().create_task(
            self._process_node(run_id, session_key, node_task)
        )
        self._active_runs[run_id] = processor

    async def _process_node(self, run_id: str, session_key: str, task: NodeTask) -> None:
        """决策 → 回传 final 事件。若期间被 abort，屏蔽迟到事件。"""
        try:
            if self.on_task is None:
                text = None
            else:
                ctx = ChatRunContext(run_id, session_key, task)
                text = await self.on_task(ctx, task)
            text = (text or "").strip()
            if run_id in self._finished_runs:
                # 已被 abort 或已终态：屏蔽迟到内容
                log.info("run %s finished/aborted, dropping late content", run_id)
                return
            await self._emit_chat_final(run_id, session_key, text)
        except asyncio.CancelledError:
            # abort 取消
            log.info("run %s cancelled by abort", run_id)
        except Exception as exc:  # noqa: BLE001
            # 决策回调失败必须回传终态，否则 BCS 会一直等到节点超时。
            log.exception("run %s decision callback failed", run_id)
            if run_id not in self._finished_runs:
                try:
                    await self._emit_chat_error(run_id, session_key, exc)
                except Exception:  # noqa: BLE001
                    log.exception("run %s failed to emit terminal error", run_id)
        finally:
            self._active_runs.pop(run_id, None)
            self._finished_runs.add(run_id)

    async def _emit_chat_final(self, run_id: str, session_key: str, text: str) -> None:
        """发送 canonical chat state=final 事件。"""
        if self._ws is None:
            return
        seq = self.next_seq()
        payload = {
            "runId": run_id,
            "sessionId": session_key,
            "seq": seq,
            "ts": int(time.time() * 1000),
            "state": "final",
            "content": text,
            "stopReason": "complete",
        }
        event = {
            "type": "event",
            "event": "chat",
            "payload": payload,
            "seq": seq,
        }
        await self._ws.send(json.dumps(event, ensure_ascii=False))

    async def _emit_chat_error(self, run_id: str, session_key: str,
                               exc: Exception) -> None:
        """发送 canonical chat state=error 终态事件（见 bot-integration §6.2）。"""
        if self._ws is None:
            return
        seq = self.next_seq()
        message = str(exc).strip() or type(exc).__name__
        payload = {
            "runId": run_id,
            "sessionId": session_key,
            "seq": seq,
            "ts": int(time.time() * 1000),
            "state": "error",
            "errorCode": "DECISION_ERROR",
            "errorMessage": message[:500],
        }
        event = {
            "type": "event",
            "event": "chat",
            "payload": payload,
            "seq": seq,
        }
        await self._ws.send(json.dumps(event, ensure_ascii=False))

    # ------------------------------------------------------------------ #
    # chat.abort：精确取消
    # ------------------------------------------------------------------ #
    async def _handle_chat_abort(self, rid: str, params: dict) -> None:
        session_key = params.get("session_key") or params.get("bcs_session_id")
        run_id = params.get("run_id")
        aborted: list[str] = []

        if run_id is None:
            await self._ack_err(rid, "invalid_request", "chat.abort 需要 run_id")
            return
        # 已终态 run：幂等成功，空 aborted_run_ids（即使后台任务还没完全退出）
        if run_id in self._finished_runs:
            await self._ack(rid, {"aborted": True, "aborted_run_ids": []})
            return
        run = self._active_runs.get(run_id)
        if run_id in self._active_runs and run is not None and run.done():
            # 刚完成但还挂在 active 里：视作已终态
            self._finished_runs.add(run_id)
            await self._ack(rid, {"aborted": True, "aborted_run_ids": []})
            return
        if run is None:
            await self._ack_err(rid, "not_found",
                                f"unknown run {run_id} or not under session")
            return
        # 校验 session 归属
        belongs = session_key is None or (self._session_runs.get(session_key, set())
                                          .__contains__(run_id))
        if not belongs:
            await self._ack_err(rid, "invalid_request",
                                f"run {run_id} 不属于 session {session_key}")
            return

        # 取消该 run，并屏蔽其迟到事件
        run.cancel()
        self._finished_runs.add(run_id)
        aborted.append(run_id)
        await self._ack(rid, {"aborted": True, "aborted_run_ids": aborted})

    # ------------------------------------------------------------------ #
    # chat.history
    # ------------------------------------------------------------------ #
    async def _handle_chat_history(self, rid: str, params: dict) -> None:
        session_key = params.get("session_key")
        payload = {"session_key": session_key, "session_id": session_key,
                   "messages": []}  # 本引擎不持久化历史，返回空
        await self._ack(rid, payload)


def _extract_text(content) -> str:
    """params.message.content 可能是 str 或 [{type:text,text}]。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""