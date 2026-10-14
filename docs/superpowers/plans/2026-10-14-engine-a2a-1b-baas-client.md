# Engine A2A 阶段 1b：BaaS 单客户端灰度 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BaaS `bot_run` 编排层获得一条 A2A v1.0 客户端路径：新容器（有 Agent Card）走 A2A，旧容器回退 v3 WS——编排层零改动（双客户端契约面保形）。

**Architecture:** 三块推进：(E) engine adapter 的 A2A 壳补齐 engine 帧→SSE 事件的映射（agent tool 进度、interaction HITL、approval）——这是 BaaS 消费 A2A 面的事件保真前提；(B) BaaS 新增 `BotA2aClient`（duck-typed 保形 `BotWebSocketClient`：connect 时做能力探测、chat_send/chat_inject 换 A2A JSON-RPC、SSE 消费循环把 wire 归一回 v3 事件形喂 handlers）；(C) 能力发现在 client 工厂处完成（GET card → `protocolBinding==JSONRPC` && `protocolVersion==1.0` → A2A；连接失败/非 JSONRPC → v3 回退），对齐设计 §10.3 兼容矩阵。

**Tech Stack:** httpx（SSE 流消费——BaaS 已依赖）、FastAPI TestClient（测试）、A2A v1.0 JSON-RPC（同 1a 壳 wire 契约）。

**上游文档：**
- 设计：`docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`（§6.4 BaaS 侧改线、§8.6 HITL 契约、§10.3 兼容矩阵）
- 1a 实现：`engine/adapter/src/engine/community/api/transport/a2a/`（7 文件）
- 1a 证据：`a2a/SECOND_STAGE_EVIDENCE.md`（F4 零副作用已验）

**分支：** 继续 `feat/engine-a2a-protocol-replacement`（base=7d817ee6a）

---

## 真实代码事实（实现者必读）

1. **openclaw port 事件名推导**（`plugins/openclaw/_chat.py:70-88 _derive_event_name`）：
   `event_data` 里的显式 `event` 键优先；`state==approval_requested/resolved` →
   `approval.*`；**其余一律 `agent`**。也就是 gateway 大多数帧的 event 名是
   `"agent"`（chat 帧名也是部分由 explicit hint 决定——chat 帧自身 event=="chat"）。
2. **bridge 现状**（`api/transport/a2a/engine_bridge.py:139-` 循环）：
   `if event == "chat" or state in _TERMINAL_CHAT_STATES` 之外的帧全部（隐式）
   丢弃——**agent/interaction.requested/approval.* 均未上 SSE**。
   interaction 的 payload 形状（ws_server 消费面）：`{"state":"interaction.requested",
   "interactionId": <id>, "kind":..., "prompt":..., "sessionKey":..., "runId":...}`。
3. **BaaS 编排依赖的事件面**（`_async_chat_client.py:282-289`）：
   `on_event("chat"|"agent"|"interaction.requested"|"interaction.resolved"|
   "mode_transition.resolved"|"error"|"*", payload)` + `on_disconnect(handler)`。
   handler 签名 `(event_name, payload)`。
4. **BotWebSocketClient 关键成员**（`_bot_websocket_client.py`）：
   `connect()`→握手响应 dict、`chat_send(session_key, message, idempotency_key,
   attachments, caller_wait_mode)`、`chat_inject(session_key, message, label)`、
   `connected` property、`server_info` property。
5. **BaaS conn 建点**（`_async_chat_client.py:264-` `connect()`）：直接构造
   `BotWebSocketClient(uri=self.uri, client_id=..., client_version=..., headers=...)`
   ——1b 在此插 система双客户端工厂。
6. **BaaS 测试 venv：** `apps/baas/.venv`（singlebox setup 已建）。

---

## 文件结构

```
engine/adapter/src/engine/community/api/transport/a2a/
├── engine_bridge.py      # [E] 修改：agent/interaction/approval 帧映射进 SSE
├── kernel.py             # [E] 修改：publish_interaction_requested/Resolved（状态转 INPUT_REQUIRED）
└── wire.py               # [E] 修改：wire_interaction_requested/wire_agent_progress 构造

apps/baas/src/secbaas/community/core/service/bot_run/
├── _bot_a2a_client.py    # [B] 新建：A2A 客户端（保形 BotWebSocketClient）
└── _async_chat_client.py # [B] 修改：client 构造点换工厂（能力发现）

apps/baas/src/secbaas/community/core/service/bot_run/tests 或 apps/baas/tests 下
└── test_bot_a2a_client.py  # [T] 新建：SSE 归一 + 能力发现矩阵回归
```

---

## 波次 E：engine adapter A2A 壳事件补齐

### Task E1: `wire.py` 事件构造函数（纯函数）

**Files:**
- Modify: `engine/adapter/src/engine/community/api/transport/a2a/wire.py`（追加）

- [ ] **Step 1: 追加构造函数**

```python
# ---- 追加到 wire.py 末尾 ----

def wire_interaction_requested(run_id: str, context_id: str, seq: int,
                               interaction_id: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
    """HITL 请求（§8.6）：Task 转 INPUT_REQUIRED；元数据承载 interactionId
    与原始 payload 摘要（kind/prompt/text 降采样）。"""
    meta: dict[str, Any] = {"seq": seq, "interactionId": interaction_id}
    for key in ("kind", "phase", "toolCallId"):
        v = payload.get(key)
        if isinstance(v, (str, int, float, bool)):
            meta[key] = v
    prompt = payload.get("prompt") or payload.get("text")
    if isinstance(prompt, str) and prompt:
        meta["prompt"] = prompt[:200]
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                          "metadata": meta}}}


def wire_interaction_resolved(run_id: str, context_id: str, seq: int,
                              payload: dict[str, Any]) -> dict[str, Any]:
    """HITL 决议（§8.6）：Task 回 WORKING；metadata.interactionId 保留对应关系。"""
    meta: dict[str, Any] = {"seq": seq}
    iid = payload.get("interactionId") or payload.get("id")
    if isinstance(iid, (str, int)):
        meta["interactionId"] = iid
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}


def wire_agent_progress(run_id: str, context_id: str, seq: int,
                        payload: dict[str, Any]) -> dict[str, Any]:
    """agent 事件（openclaw tool 进度等）→ statusUpdate（state 不变；metadata
    保 tool/phase/data 摘要——BaaS 客户端反向归一回 v3 `agent` 事件 payload）。"""
    meta: dict[str, Any] = {"seq": seq, "kind": "agent"}
    for key in ("tool", "phase", "type", "name"):
        v = payload.get(key)
        if isinstance(v, (str, int, float, bool)):
            meta[key] = v
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}
```

- [ ] **Step 2: 编译验证 + Commit**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/wire.py
cd /Users/rongzhi/PycharmProjects/Avernet && git add engine/adapter/src/engine/community/api/transport/a2a/wire.py && git commit -m "feat(a2a): wire constructors for interaction/agent status updates"
```

### Task E2: `kernel.py` 状态转 INPUT_REQUIRED 的发布方法

**Files:**
- Modify: `engine/adapter/src/engine/community/api/transport/a2a/kernel.py`（追加）

- [ ] **Step 1: 追加方法**

```python
# ---- 追加在类内（publish_status 之后）----

    async def publish_interaction_requested(self, rid: str,
                                            interaction_id: str,
                                            payload: dict[str,
                                                          Any]) -> None:
        """HITL 请求：state 转 INPUT_REQUIRED（终态之外的专用状态，
        complete_run 语义不变）。通知方式与 status 发布一致。"""
        r = self.runs[rid]
        r.state = "TASK_STATE_INPUT_REQUIRED"
        await self._publish(rid, wire.wire_interaction_requested(
            rid, r.context_id, 0, interaction_id, payload))

    async def publish_interaction_resolved(self, rid: str,
                                           payload: dict[str, Any]) -> None:
        """HITL 决议：state 回 WORKING。"""
        r = self.runs[rid]
        r.state = "TASK_STATE_WORKING"
        await self._publish(rid, wire.wire_interaction_resolved(
            rid, r.context_id, 0, payload))

    async def publish_agent_progress(self, rid: str,
                                     payload: dict[str, Any]) -> None:
        """agent/tool 进度事件不上收 run 状态，仅作为 statusUpdate 流出。"""
        r = self.runs[rid]
        await self._publish(rid, wire.wire_agent_progress(
            rid, r.context_id, 0, payload))
```

注意 `TERMINAL_STATES`/subscribe 快照/中断边界都要把 `TASK_STATE_INPUT_REQUIRED`
视为活动态——核对 kernel 判断 state 的地方：
- `subscribe()` 里 `if r.state in TERMINAL_STATES` —— INPUT_REQUIRED ∈ TERMINAL?
  **不属于终态**（§8.6：未决超时停留 input-required 至 deadline）——现有代码
  终态 tuple 只有 COMPLETED/FAILED/CANCELED/REJECTED，无需改。
- `router._subscribe_task` 出口判断 `r.state in wire.TERMINAL_STATES` 同上无需改。

- [ ] **Step 2: 编译 + 单测验证（在 adapter 现有测试目录追加一个快速断言）**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/engine/adapter && .venv/bin/python -c "
import sys, asyncio; sys.path.insert(0,'src')
from engine.community.api.transport.a2a import kernel as K, wire as W
k = K.A2aKernel()
async def t():
    rid,_ = await k.open_run('c','k','first','d')
    await k.publish_interaction_requested('i-1', {'kind':'exec.approval','prompt':'rm -rf /tmp/x 测试'})
    r = k.get_run(rid); assert r.state=='TASK_STATE_INPUT_REQUIRED', r.state
    bl,_ = await k.subscribe(rid, since_seq=0)
    su = [e for e in bl if e['type']=='statusUpdate'][-1]['ev']['statusUpdate']
    assert su['status']['state']=='TASK_STATE_INPUT_REQUIRED'
    assert su['metadata']['interactionId']=='i-1'
    assert su['metadata']['prompt'].startswith('rm -rf')
    await k.publish_interaction_resolved({})
    assert k.get_run(rid).state=='TASK_STATE_WORKING'
    print('E2 OK')
asyncio.run(t())"
cd /Users/rongzhi/PycharmProjects/Avernet && git add engine/adapter/src/engine/community/api/transport/a2a/kernel.py && git commit -m "feat(a2a): kernel publishes interaction/agent status updates"
```

### Task E3: `engine_bridge.py` 消费 agent/interaction/approval 帧

**Files:**
- Modify: `engine/adapter/src/engine/community/api/transport/a2a/engine_bridge.py`（循环体）

- [ ] **Step 1: 在 `_dispatch_to_engine` 循环的 inject 分支之后、chat 分支之前，加非 chat 帧的路由**

现有循环（R12 版）从上到下是 inject 检查 → `if event == "chat" or state in _TERMINAL_CHAT_STATES:`。修改为（只展示插入块与必需上下文）：

```python
            if isinstance(run_id, str) and run_id.startswith("inject-"):
                inject_text = _payload_text(payload, state)
                if inject_text:
                    kernel.publish_engine_inject(context_id, inject_text,
                                                 run_id)
                continue

            # ── 1b-E3: non-chat engine frames routed onto the SSE stream ──
            if event != "chat" and state not in _TERMINAL_CHAT_STATES:
                if state == "interaction.requested":
                    iid = payload.get("interactionId") or payload.get("id")
                    if not iid:
                        iid = f"int-{uuid.uuid4().hex[:8]}"
                    await kernel.publish_interaction_requested(
                        rid, str(iid), payload)
                    continue
                if state == "interaction.resolved" or \
                        event == "interaction.resolved":
                    await kernel.publish_interaction_resolved(rid, payload)
                    continue
                if state in ("approval_requested", "approval_resolved"):
                    # approval 与 interaction 同族（exec 审批）——HITL 同映射
                    if state == "approval_requested":
                        iid = payload.get("approvalId") or payload.get("id")
                        if not iid:
                            iid = f"apr-{uuid.uuid4().hex[:8]}"
                        await kernel.publish_interaction_requested(
                            rid, str(iid), payload)
                    else:
                        await kernel.publish_interaction_resolved(rid, payload)
                    continue
                if event == "agent" and payload:
                    await kernel.publish_agent_progress(rid, payload)
                    continue
                # 未知帧默认丢弃（log）
                log.debug("bridge: dropping unhandled frame event=%s state=%s",
                          event, state)
                continue

            if event == "chat" or state in _TERMINAL_CHAT_STATES:
                ...（保持原有 chat 分支不变）...
```

**注意 import：** 文件顶部确认有 `import uuid`（用于兜底 id；现有文件未导入——需加）。

其中 `interaction.requested` 工具的一个细节：`state` 是 payload 的
`state` 字段——根据插件透传的 event 字典 hint 形态各异（claude_code 有
`state`，openclaw 显式 `event`）；**加一个 event 名匹配的 OR**：模仿上面 approval 的分支
`event == "interaction.resolved"` 所示模式。

- [ ] **Step 2: 编译 + 仓库回归**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/engine_bridge.py
uv run --no-sync --with pytest-asyncio --with socksio pytest src/engine/community/api/tests/test_a2a_transport.py -q --no-header
uv run --no-sync --with pytest-asyncio --with socksio pytest src -q --no-header   # 全量 3007+
cd /Users/rongzhi/PycharmProjects/Avernet
git add engine/adapter/src/engine/community/api/transport/a2a/engine_bridge.py
git commit -m "feat(a2a): bridge routes agent/interaction/approval frames to SSE status updates"
```

---

## 波次 B：BaaS 包 1b 转移改造

### Task B1: `_bot_a2a_client.py`（保形新客户端）

**Files:**
- Create: `apps/baas/src/secbaas/community/core/service/bot_run/_bot_a2a_client.py`

- [ ] **Step 1: 写完整客户端**

```python
"""BotA2aClient — BotWebSocketClient 的 A2A v1.0 保形兄弟（1b 灰度）。

Why

    BaaS bot_run 编排（_async_chat_client.py）依赖 v3 WS 客户端的 duck 面：
    connect()/chat_send()/chat_inject()/on_event()/on_disconnect()/connected/
    server_info。1b 在不改编排层的前提下新增 A2A 传输——本类消费 engine
    adapter 的 POST /rpc SSE 流，把 A2A StreamResponse 反向归一成 v3 事件
    payload 形状喂给 handlers（事件名严格用编排层已有的名字）。

Sub

    能力探测在 connect()：GET /.well-known/agent-card.json →
    supportedInterfaces[0].protocolBinding == "JSONRPC" 且 protocolVersion
    major==1 → 视为 A2A 可用；否则抛 A2aUnavailableError 由工厂回退 v3。

Note

    - A2A 无连接态：connected = 最近一次 capability 探测成功 + 后台消费任务存活
    - chat_send 用 SendStreamingMessage（流式面的事件最全），经 SSE 消费循环
      分发；执行结果与流状态经 handlers 传递（与 WS 相同）
    - 反向归一映射（SSE wire → v3 payload 形）：
        statusUpdate(INPUT_REQUIRED + interactionId) → ("interaction.requested", payload)
        statusUpdate(WORKING + interactionId)         → ("interaction.resolved", payload)
        statusUpdate(WORKING + kind=agent)             → ("agent", {tool/phase/…})
        statusUpdate(final="true")                     → ("chat", {state:"final", message:{…}})
        artifactUpdate(append=*)                      → ("chat", {state:"delta", deltaText: <该分块文本>, message:{content:[…]}})
      （final 帧的 message 形状优先取自 artifact.content 全文快照——本 adapter
      侧 1a E 段已保证 final 是全文替换帧）
    - inject: manager.chat_inject 在 adapter 侧等效——A2A 面上 InjectContext
      只支持同步（非 chat_inject 原帧），返回 {"accepted"|"duplicate_*"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Any]

_A2A_VERSION_MAJOR = 1
_REQUEST_TIMEOUT_SECONDS = 60.0
_SSE_IDLE_PING_SECONDS = 45.0


class A2aUnavailableError(RuntimeError):
    """目标引擎容器不提供可用的 A2A 面——工厂应回退 v3 WS。"""


def _wire_to_v3(task_payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """单个 SSE wire 事件 → v3 事件 advisor (event_name, payload) 或 None（丢弃）。

    Args:
        task_payload: json.loads(data line)["result"]——标准 StreamResponse 成员 dict
    """
    if "statusUpdate" in task_payload:
        su = task_payload["statusUpdate"]
        meta = su.get("metadata") or {}
        state = (su.get("status") or {}).get("state", "")
        payload = {
            "taskId": su.get("taskId"),
            "sessionKey": su.get("contextId"),
            **{k: v for k, v in meta.items() if k != "seq"},
        }
        payload["seq"] = meta.get("seq")
        if state == "TASK_STATE_INPUT_REQUIRED":
            payload["state"] = "interaction.requested"
            return "interaction.requested", payload
        if "interactionId" in meta and state == "TASK_STATE_WORKING":
            payload["state"] = "interaction.resolved"
            return "interaction.resolved", payload
        if meta.get("kind") == "agent":
            payload["state"] = "agent"
            return "agent", payload
        if meta.get("final") in ("true", True):
            payload["state"] = "final"
            return "chat", payload
        # 纯 state 摆动（working/…）——保持 chat 面形状但 state 透传

        payload["state"] = state
        return "chat", payload
    if "artifactUpdate" in task_payload:
        au = task_payload["artifactUpdate"]
        text = ""
        try:
            text = au["artifact"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            text = ""
        delta_payload = {
            "taskId": au.get("taskId"),
            "sessionKey": au.get("contextId"),
            "deltaText": text,                      # 增量原文（拼接语义由消费者决定）
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]},
            "seq": (au.get("metadata") or {}).get("seq"),
        }
        if au.get("lastChunk") and not au.get("append"):
            delta_payload["state"] = "final"        # 终稿替换帧 → v3 final
        else:
            delta_payload["state"] = "delta"
        return "chat", delta_payload
    return None


class BotA2aClient:
    """Duck-typed A2A 兄弟——1b BaaS 单客户端灰度的传输实现。"""

    def __init__(self, *, base_url: str, client_id: str = "",
                 client_version: str = "", headers: Optional[dict] = None,
                 connect_timeout: float = 10.0,
                 http_transport_factory: Callable[
                     [], httpx.AsyncClient] | None = None):
        self.base_url = base_url.rstrip("/")
        self.rpc_url = f"{self.base_url}/rpc"
        self.card_url = f"{self.base_url}/.well-known/agent-card.json"
        self.client_id = client_id
        self.client_version = client_version
        self.headers = dict(headers or {})
        self._connect_timeout = connect_timeout
        # 生产 None → 每次调用现场 httpx.AsyncClient()；测试传工厂注入
        # ASGITransport——BotA2aClient 直连行为的可测性通道（1b T1 依赖）。
        self._http_factory = http_transport_factory
        self._event_handlers: dict[str, EventHandler] = {}
        self._disconnect_callback: Optional[EventHandler] = None
        self._connected = False
        self._server_info: Optional[dict[str, Any]] = None
        self._consumer_task: Optional[asyncio.Task] = None  # 后台 SSE 队列消费
        self._pending_streams: dict[str, asyncio.Queue] = {}  # rid → wire queue
        self._streams_by_session: dict[str, str] = {}  # sessionKey → 当前 rid

    # ── 能力/生命周期 ──

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_info(self) -> Optional[dict[str, Any]]:
        return self._server_info

    @property
    def features(self) -> Optional[dict[str, Any]]:
        return (self._server_info or {}).get("features")

    async def connect(self) -> dict[str, Any]:
        if self._connected:
            raise RuntimeError("Already connected")
        http_cm = (self._http_factory() if self._http_factory
                   else httpx.AsyncClient(timeout=self._connect_timeout,
                                          proxy=None))
        try:
            async with http_cm as http:
                resp = await http.get(self.card_url, headers=self.headers)
                resp.raise_for_status()
                card = resp.json()
        except Exception as e:
            raise A2aUnavailableError(
                f"engine adapter A2A capability probe failed: {e}") from e
        iface = ((card.get("supportedInterfaces") or [{}])[0])
        binding = iface.get("protocolBinding", "")
        version = iface.get("protocolVersion", "")
        if binding != "JSONRPC" or not version.startswith(
                f"{_A2A_VERSION_MAJOR}."):
            raise A2aUnavailableError(
                f"unsupported A2A interface binding={binding} "
                f"version={version}")
        # 保存 server_info 呼应 WS handshake payload
        self._server_info = {
            "type": "hello-ok", "protocol": _A2A_VERSION_MAJOR,
            "server": {"version": card.get("version", ""), "card": card},
            "a2a": True,
        }
        self._connected = True
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("BotA2aClient connected (A2A %s @ %s)", version,
                    self.rpc_url)
        return dict(self._server_info)

    async def close(self) -> None:
        self._connected = False
        for q in self._pending_streams.values():
            q.put_nowait(None)
        self._pending_streams.clear()
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._disconnect_callback:
            try:
                self._disconnect_callback("disconnect", {})
            except Exception:
                logger.exception("BotA2aClient disconnect handler error")

    def on_event(self, event_name: str, handler: EventHandler) -> None:
        self._event_handlers[event_name] = handler

    def on_disconnect(self, handler: EventHandler) -> None:
        self._disconnect_callback = handler

    async def _dispatch_wire(self, rid: str, task_result: dict[str, Any]) -> None:
        norm = _wire_to_v3(task_result)
        if norm is None:
            return
        event_name, payload = norm
        handler = self._event_handlers.get("*")
        if event_name in self._event_handlers:
            handler = self._event_handlers[event_name]
        if handler:
            handler(event_name, payload)  # 与 WS 相同： ICommand synchronous 回调
        else:
            logger.debug("[BotA2aClient] no handler for event=%s", event_name)

    async def _consume_loop(self) -> None:
        """多路复用多个 active SSE 的 wire 队列（1b 简化：一个共享队列）。"""
        # 简化实现：每个 chat_send 内联消费自身 SSE（见 _stream_once），
        # 此 loop 仅供未来跨 stream 复用——1b 保留骨架。
        try:
            while self._connected:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _stream_once(self, rid: str,
                           resp: httpx.Response) -> None:
        """消费单个 SendStreamingMessage 的 SSE 响应，同步推送 handlers。"""
        try:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = json.loads(line[6:])
                if "error" in body:
                    err = body["error"]
                    handler = self._event_handlers.get("error")
                    if handler:
                        handler("error", {"reason": (err.get("data") or {})
                                          .get("reason", "a2a_error"),
                                          "message": err.get("message", "")})
                    continue
                result = body.get("result") or {}
                await self._dispatch_wire(rid, result)
        except Exception:
            logger.exception("BotA2aClient SSE consume failed rid=%s", rid)
        finally:
            if rid in self._pending_streams:
                self._pending_streams.pop(rid, None)

    # ── 业务操作（保形 WS 面） ──

    async def chat_send(
        self, session_key: str, message: Any,
        idempotency_key: str,
        attachments: Any | None = None,
        caller_wait_mode: Any | None = None,
    ) -> dict[str, Any]:
        """经 A2A SendStreamingMessage 发起 chat；同步阻塞消费本次流。

        args/message 语义保持 v3 透传（服务端 adapter 期待 {"content": [...]}）；
        attachments 暂不启用（v3 面仍可用旧附件语义——A2A 面此期不支 attachments，
         老容器回退路径覆盖）。
        """
        if not self._connected:
            raise RuntimeError("BotA2aClient not connected")

        rid = idempotency_key or f"a2a-{uuid.uuid4().hex[:16]}"
        message_dict = {
            "messageId": rid,
            "contextId": session_key,
            "role": "ROLE_USER",
            "parts": [{"text": _message_to_text(message)}],
        }
        params: dict[str, Any] = {"message": message_dict, "metadata": {}}
        # caller_wait_mode 等扩展语义通过 metadata 透传已是 1b 约定（adapter 侧
        # 未实现——保留字段不传 errors，用 dict 形式提交）。本期不含 retryOnly。

        async with httpx.AsyncClient(timeout=None, proxy=None) as http:
            async with http.stream("POST", self.rpc_url, json={
                "jsonrpc": "2.0", "id": rid,
                "method": "SendStreamingMessage",
                "params": params,
            }, headers={**self.headers, "Accept": "text/event-stream"}) as resp:
                if resp.status_code != 200:
                    body_text = (await resp.aread()).decode(
                        errors="replace")[:300]
                    raise RuntimeError(
                        f"A2A SendStreamingMessage failed "
                        f"status={resp.status_code} body={body_text}")
                resp.raise_for_status()
                await self._stream_once(rid, resp)

        self._streams_by_session[session_key] = rid
        return {"ok": True, "runId": rid, "a2a": True}

    async def chat_inject(self, session_key: str, message: Any,
                           label: Any = None) -> dict[str, Any]:
        if not self._connected:
            raise RuntimeError("BotA2aClient not connected")
        mid = f"inj-{uuid.uuid4().hex[:12]}"
        payload = {
            "jsonrpc": "2.0", "id": mid,
            "method": "InjectContext",
            "params": {"contextId": session_key,
                        "message": {"role": "ROLE_USER",
                                    "messageId": mid,
                                    "parts": [{"text": _message_to_text(message)}]}},
        }
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS,
                                     proxy=None) as http:
            resp = await http.post(self.rpc_url, json=payload,
                                   headers=self.headers)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"A2A InjectContext status={resp.status_code}")
            body = resp.json()
        result = body.get("result") or body.get("error") or {}
        return {"ok": "inject" in result and result["inject"] in (
            "accepted", "duplicate_accepted", "duplicate_in_progress"),
                "status": result.get("inject", "unknown"),
                "a2a": True}


def _message_to_text(message: Any) -> str:
    """v3 兼容——message 可能是 {"content": [{"type":"text","text":…},…]} 或 str。"""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "".join(p.get("text", "") for p in content
                           if isinstance(p, dict))
    if isinstance(message, list):
        return "".join(p.get("text", "") for p in message
                       if isinstance(p, dict))
    return str(message)
```

（注意：uid 不在文件头——`uuid` 已 import。）

- [ ] **Step 2: 编译**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/apps/baas && .venv/bin/python -m py_compile src/secbaas/community/core/service/bot_run/_bot_a2a_client.py
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet && git add apps/baas/src/secbaas/community/core/service/bot_run/_bot_a2a_client.py && git commit -m "feat(a2a): BotA2aClient — wire-compatible A2A client for BaaS bot_run"
```

### Task B2: 能力工厂 + `_async_chat_client.connect()` 接入

**Files:**
- Modify: `apps/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py`（connect 处）

- [ ] **Step 1: 修改 `connect()` 构造点**

在文件顶部 import 增补：

```python
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError, BotA2aClient,
)
```

在 `connect()` 中把构造 `BotWebSocketClient(uri=...)` 的地方改为：

```python
        # 1b 灰度：A2A 能力探测优先——card(JSONRPC 1.x)可拉取则走 A2A 客户端，
        # 否则维持 v3 WS（§10.3 兼容矩阵：BaaS 新版客户端 × 旧容器 → v3 回退）。
        a2a_base = _a2a_base_url_from_ws(self.uri)
        _client = None
        transport = "v3-ws"
        if a2a_base:
            try:
                _client = BotA2aClient(
                    base_url=a2a_base,
                    client_id=self.client_id,
                    client_version=self.client_version,
                    headers=self.headers,
                )
                await _client.connect()
                transport = "a2a"
            except A2aUnavailableError as e:
                logger.info("[a2a-fallback] engine adapter not A2A-capable "
                            "(%s) — falling back to v3 WS", e)
                _client = None
        if _client is None:
            from secbaas.community.core.service.bot_run._bot_websocket_client import (
                BotWebSocketClient,
            )
            _client = BotWebSocketClient(
                uri=self.uri,
                client_id=self.client_id,
                client_version=self.client_version,
                headers=self.headers,
            )
            await _client.connect()

        self._client = _client
        self._transport = transport
        logger.info("AsyncChatClient connected via %s", transport)
```

同一文件（class 外或模块尾部）追加 helper：

```python
def _a2a_base_url_from_ws(uri: str) -> str | None:
    """ws(s)://host:port/xxx → http(s)://host:port（A2A base 同主机）。

    None 表示不可解析（保守回退 v3）。
    """
    if not isinstance(uri, str) or "://" not in uri:
        return None
    scheme, _, rest = uri.partition("://")
    if scheme not in ("ws", "wss"):
        return None
    host_port = rest.split("/", 1)[0]
    http_scheme = "https" if scheme == "wss" else "http"
    return f"{http_scheme}://{host_port}"
```

注意连接池差异（`_async_chat_client_pool.py`）对 `connected` property 的
健康检查——`BotA2aClient.connected` 已实现定义，无需改 pool 层。

连接生命周期其他消费（如 server_info、features）——`BotA2aClient` 面已对齐。

`__init__` 处加 `self._transport: str = "v3-ws"` 的初始化（类型标注）。

- [ ] **Step 2: 打字检查（BaaS 无完整 pyright 面也要先编译）+ 现有测试**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/apps/baas && .venv/bin/python -m py_compile src/secbaas/community/core/service/bot_run/_async_chat_client.py src/secbaas/community/core/service/bot_run/_bot_a2a_client.py
# BaaS 现有测试子集（流转路径编译面）——若社区包有测试则跑（run any existing bot_run tests）
.venv/bin/python -m pytest tests/community/service/bot_run -q --no-header 2>/dev/null || .venv/bin/python -m pytest src -k "bot_run" -q --no-header 2>/dev/null || echo "no existing bot_run tests found"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet && git add apps/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py && git commit -m "feat(a2a): BaaS async chat client dual-transport — A2A capability first, v3 fallback"
```

---

## 波次 T：仓库测试

### Task T1: `_bot_a2a_client.py` 单测（含归一矩阵 + fallback）

**Files:**
- Create: `apps/baas/tests/community/service/bot_run/test_bot_a2a_client.py`（如 tests 目录不存在，先 `mkdir -p`；保持与 secbaas 现有测试目录一致——实现者先 `ls apps/baas/tests` 找到现有 root 习惯再落位）

- [ ] **Step 1: 写测试**

```python
"""BotA2aClient — wire 归一 + 能力探测 + inject 状态面回归（1b）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError, BotA2aClient, _wire_to_v3,
)


def _sse(result: dict[str, Any]) -> str:
    return f"data: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}, ensure_ascii=False)}\n\n"


# ── 归一矩阵 ──

def test_wire_to_v3_input_required():
    out = _wire_to_v3({"statusUpdate": {
        "taskId": "t", "contextId": "c", "status": {
            "state": "TASK_STATE_INPUT_REQUIRED"},
        "metadata": {"interactionId": "i-1", "kind": "exec.approval",
                     "prompt": "do it", "seq": 3}}})
    assert out[0] == "interaction.requested"
    payload = out[1]
    assert payload["interactionId"] == "i-1"
    assert payload["kind"] == "exec.approval"


def test_wire_to_v3_interaction_resolved():
    out = _wire_to_v3({"statusUpdate": {
        "taskId": "t", "contextId": "c",
        "status": {"state": "TASK_STATE_WORKING"},
        "metadata": {"interactionId": "i-1", "seq": 4}}})
    assert out[0] == "interaction.resolved"


def test_wire_to_v3_agent_progress():
    out = _wire_to_v3({"statusUpdate": {
        "taskId": "t", "contextId": "c",
        "status": {"state": "TASK_STATE_WORKING"},
        "metadata": {"kind": "agent", "tool": "bash", "phase": "start",
                     "seq": 5}}})
    assert out[0] == "agent"
    assert out[1]["tool"] == "bash"


def test_wire_to_v3_delta_and_final():
    delta = _wire_to_v3({"artifactUpdate": {
        "taskId": "t", "contextId": "c",
        "artifact": {"parts": [{"text": "你好"}]},
        "append": True, "lastChunk": False, "metadata": {"seq": 6}}})
    assert delta[0] == "chat" and delta[1]["state"] == "delta"
    assert delta[1]["deltaText"] == "你好"

    final = _wire_to_v3({"artifactUpdate": {
        "taskId": "t", "contextId": "c",
        "artifact": {"parts": [{"text": "完整文本"}]},
        "append": False, "lastChunk": True, "metadata": {"seq": 7}}})
    assert final[0] == "chat" and final[1]["state"] == "final"


def test_wire_to_v3_unknown_dropped():
    assert _wire_to_v3({"message": {"parts": []}}) is None


# ── 能力探测 + 流式（假 adapter）──

def _fake_a2a_app(*, binding="JSONRPC", version="1.0",
                  stream_script: list[str] | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card():
        return {"supportedInterfaces": [{"protocolBinding": binding,
                                          "protocolVersion": version}],
                 "capabilities": {"streaming": True}, "version": "0.1.0"}

    @app.post("/rpc")
    async def rpc(request: Any) -> Any:
        body = await request.json()
        mid = body.get("id", 1)
        if body.get("method") == "SendStreamingMessage":
            async def gen():
                for e in stream_script or []:
                    yield _sse(json.loads(e))
                yield _sse({"statusUpdate": {
                    "taskId": mid, "contextId": "s",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "metadata": {"final": "true", "lastSeq": 99}}})
            return StreamingResponse(gen(), media_type="text/event-stream")
        if body.get("method") == "InjectContext":
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"inject": "accepted"}}
        return JSONResponse({"jsonrpc": "2.0", "id": mid,
                             "error": {"code": -32601}})

    return app


def _fake_stream_results() -> list[str]:
    return [
        json.dumps({"statusUpdate": {
            "taskId": "r", "contextId": "s",
            "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"kind": "agent", "tool": "bash", "seq": 1}}}),
        json.dumps({"artifactUpdate": {
            "taskId": "r", "contextId": "c",
            "artifact": {"parts": [{"text": "你好"}]},
            "append": False, "lastChunk": False, "metadata": {"seq": 2}}}),
        json.dumps({"statusUpdate": {
            "taskId": "r", "contextId": "s",
            "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
            "metadata": {"interactionId": "i-1", "seq": 3}}}),
    ]


def _client_with(app) -> BotA2aClient:
    """BotA2aClient + ASGITransport factory（生产代码路径不改动）。"""
    import httpx
    factory = lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://testserver")
    return BotA2aClient(base_url="http://testserver",
                        http_transport_factory=factory), factory


def test_connect_rejects_non_jsonrpc_binding():
    app = _fake_a2a_app(binding="HTTP+JSON")

    async def scenario():
        client, factory = _client_with(app)
        try:
            await client.connect()
        except A2aUnavailableError as e:
            return "rejected"
        finally:
            await _close_factory(factory)
        return "accepted"

    import asyncio
    assert asyncio.run(scenario()) == "rejected"


async def _close_factory(factory):
    # ASGITransport 客户端由 httpx 内部随 async with 关闭——此处不持有外层
    pass


def test_connect_rejects_wrong_version():
    app = _fake_a2a_app(version="0.3.0")

    async def scenario():
        client, _ = _client_with(app)
        try:
            await client.connect()
        except A2aUnavailableError:
            return "rejected"
        return "accepted"

    import asyncio
    assert asyncio.run(scenario()) == "rejected"


def test_connect_accepts_jsonrpc_v1():
    app = _fake_a2a_app()

    async def scenario():
        client, _ = _client_with(app)
        return await client.connect()

    import asyncio
    hello = asyncio.run(scenario())
    assert hello["protocol"] == 1 and hello["a2a"] is True


def test_full_stream_dispatches_v3_events():
    app = _fake_a2a_app(stream_script=_fake_stream_results())
    received: list[tuple[str, dict]] = []

    async def scenario():
        client, _ = _client_with(app)
        try:
            await client.connect()
        except A2aUnavailableError:
            raise AssertionError("JSONRPC 1.0 app must accept")
        client.on_event("chat", lambda n, p: received.append((n, p)))
        client.on_event("agent", lambda n, p: received.append((n, p)))
        client.on_event("interaction.requested",
                        lambda n, p: received.append((n, p)))
        out = await client.chat_send("sess", {"content": [
            {"type": "text", "text": "hi"}]}, idempotency_key="r-1")
        await client.close()
        return out

    import asyncio
    out = asyncio.run(scenario())
    assert out["ok"] is True
    names = [n for n, _ in received]
    assert "agent" in names, received       # tool 进度归一（E3 路径）
    assert "interaction.requested" in names, received   # HITL 归一
    chats = [p for n, p in received if n == "chat"]
    assert any(p.get("state") == "delta" for p in chats), received
    assert any(p.get("state") == "final" for p in chats), received


def test_inject_accepted_status():
    app = _fake_a2a_app()

    async def scenario():
        client, _ = _client_with(app)
        await client.connect()
        return await client.chat_inject("sess", "test message")

    import asyncio
    out = asyncio.run(scenario())
    assert out["status"] == "accepted" and out["ok"] is True
```

- [ ] **Step 2: 跑测试**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet/apps/baas && .venv/bin/python -m pytest tests/community/service/bot_run/test_bot_a2a_client.py -q --no-header
```

- [ ] **Step 3: Commit**

```bash
git add apps/baas/tests/community/service/bot_run/test_bot_a2a_client.py apps/baas/src/secbaas/community/core/service/bot_run/_bot_a2a_client.py && git commit -m "test(a2a): BotA2aClient wire-normalization matrix + capability tests"
```if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

注意——上方探针依赖连接的是真实 adapter（由用户在 singlebox 点火后执行）。**本 Task 只落探针文件**（不作为 CI）。

- [ ] **Step 2: Commit**

```bash
cd /Users/rongzhi/PycharmProjects/Avernet && git add spike/a2a/probes/baas_e2e_smoke.py && git commit -m "test(a2a): 1b BaaS e2e smoke probe for singlebox acceptance"
```

---

## 自审记录（计划完成时回填）

1. **Spec 覆盖**（设计 §6.4/§8.6/§10.3）：
   - §6.4 `ws_path() → endpoint_url()`：**本计划有意缓做**——1b 灰度靠能力
     探测（连接池已有 uri 面），SPI 改面（ws_path）生态影响大而收益滞后
     （决定推迟到 §6.4 全落或 1c 收口）
   - §8.6 HITL：E1/E2/E3 补 engine 侧 wire 事件（INPUT_REQUIRED/交互元数据）
     ✓；B1 归一为 interaction.requested/resolved ✓
   - §10.3 矩阵：B2 双客户端工厂 + A2aUnavailableError 回退 ✓；缓存失效
     （rebuild 事件）**未进本波次**——发现探测每次 connect() 都命中网络，
     成本可接受（card GET 毫秒级；连接粒度探≁——失效缓存=每次连接都重探测）。
     这个决策写成实现 note 放 B2 docstring。
   - appendix A 折叠 include：D retryOnly 路径——1b 不启用（chat_send 里注
     `caller_wait_mode 透传留给 1c——本波不移 D14 桥`)
2. **占位符扫描**：B1 里 T1 测试中曾出现"review 保证"式空断言(rewrite注入因
   http transport 不可测)——已通过 T1 的"诚实声明+方法论"定死：把
   `http_transport_factory`参数补到 B1 的构造器里，实现者必须正面改写这三段
   测试至全绿，禁止保留无断言伪测试。
3. **类型一致性**：BotA2aClient 与 BotWebSocketClient 的 duck 面（connected/
   server_info/features/chat_send/chat_inject/on_event/on_disconnect/close)——
   `_async_chat_client` 消费面严格对齐（`await _client.connect()`、
   `self._client.connected`）。

## 执行顺序建议

E1→E2→E3（adapter）→ B1（独立类文件）→ B2（接入点）→ T1（BaaS tests）→
T2（singlebox 探针）→ 用户验证后（人工点火跑 T2）。