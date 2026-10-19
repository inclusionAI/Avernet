# Engine A2A 阶段 1a 产品实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 engine adapter 的 FastAPI 应用上新增 A2A v1.0 JSON-RPC 绑定面（`/rpc` 端点 + Agent Card），与 v3 WS 壳并行运转；代码写完后在 singlebox 统一验证两路行为。

**Architecture:** **不动 `ws_server.py`**（v3 路径零改动——2067 行不动一行）。新建 `api/transport/a2a/` 独立传输模块，经 `EngineManager` 单例直接分发（与 ws_server 相同的插件面——`EngineManager.dispatch(...)` / `EngineManager.get_active_engine()`），内部实现统一 kernel 逻辑（Task 生命周期/事件流/D14 幂等/订阅）。注册为 FastAPI 子路由挂在 `app.py` 上。wire 转换严格沿用 spike `server.py::_wire()` 经验证的模式。

**Tech Stack:** FastAPI（既有实例追加路由）、A2A v1.0 JSON-RPC over HTTP + SSE（与 spike 同 wire 格式）、a2a-sdk 客户端（singlebox 验证时使用）。**无新依赖**（不用 a2a-sdk server 库——wire 转换在自家代码内完成，避免额外依赖）。

**上游文档：**
- 设计：`docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`（V5 通过版）
- Spike 取证：`spike/a2a/evidence/REPORT.md`（G1a G1b-lite pass）
- Spike 代码参考：`spike/a2a/.harness/src/`（server.py + kernel.py——全量测试通过的产品级参考实现）

**分支：** `feat/engine-a2a-1a`（从 `feat/engine-a2a-protocol-replacement` 切）

---

## 文件结构

```
engine/adapter/src/engine/community/api/transport/a2a/
├── __init__.py           # 导入 router 供 app.py include
├── card.py               # Agent Card 生成（参照 spike settings/make_card）
├── kernel.py             # A2A 传输内部 kernel：run 生命周期 + 订阅 + D14 + budget
├── wire.py               # kernel 事件 ↔ A2A v1.0 标准 wire 互转（含 metadata 序号）
└── router.py             # FastAPI 路由：/.well-known/agent-card.json + POST /rpc

代码总量预估 ~600 行（四个文件各 ≤200 行）
```

**分工：**
- `kernel.py` — 唯一持状态者（Task run/事件缓冲/订阅列表/幂等表）；不 import FastAPI/Starlette
- `wire.py` — 纯函数（无状态）：kernel 事件 dict → 标准 wire dict；反向 wire → kernel 命令
- `router.py` — 薄壳：请求解析 → 调 kernel → wire 转换 → StreamingResponse/JSONResponse
- `card.py` — 纯函数：生成符合 A2A v1.0 规范的 Agent Card JSON

**与 spike 代码的关系：** 逐文件参照 spike 的 `.harness/src/` 下同名物——产品版的主要差异是引擎分发改走 `EngineManager`（spike 是 mock 引擎流入 kernel），其余逻辑（wire/生命周期/D14/默认量）一一复用。**不 import spike 代码**（spike 永不合入）。

---

### Task 1: `wire.py`（纯函数——无依赖、最先写、其他模块全依赖它）

**Files:**
- Create: `engine/adapter/src/engine/community/api/transport/a2a/wire.py`

- [ ] **Step 1: 写 wire.py**

```python
"""A2A v1.0 wire conversion — kernel internal events ↔ standard StreamResponse.

Pure functions; no framework imports; no state.

Wire contract (verified in spike G1a with a2a-sdk 1.2.2):
  - StreamResponse.result has exactly one member: task | message |
    statusUpdate | artifactUpdate
  - seq/lastSeq/final live inside the member's metadata (NOT top-level —
    protobuf ParseDict rejects unknown fields)
  - final goes in metadata as string "true" (bool also works but "true" is
    safest for map<string,string> environments)
  - artifact chunks follow the stage table: (F,F)→首块, (T,F)→中间,
    (T,T)→末块, (F,T)→终稿替换
"""
from __future__ import annotations

from typing import Any

KINDS = ("task", "message", "statusUpdate", "artifactUpdate")
TERMINAL_STATES = (
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
)


def kernel_event_to_wire(event: dict[str, Any]) -> dict[str, Any]:
    """kernel event {"seq": int, "type": str, "ev": dict} → A2A wire event.

    The result is a standard StreamResponse member dict suitable for direct
    JSON serialization into SSE data lines or JSON-RPC result fields.
    """
    ev = event.get("ev") or {}
    seq = event.get("seq")
    out = dict(ev)

    for member in ("statusUpdate", "artifactUpdate"):
        if member in out and out[member] is not None:
            inner = dict(out[member])
            meta = dict(inner.get("metadata") or {})
            if seq is not None:
                meta["seq"] = seq
            inner["metadata"] = meta
            out[member] = inner

    return out


def wire_task_event(run_id: str, context_id: str, state: str) -> dict[str, Any]:
    return {"seq": None, "type": "task", "ev": {
        "task": {"id": run_id, "contextId": context_id,
                  "status": {"state": state}, "metadata": {}}}}


def wire_status_update(run_id: str, context_id: str, state: str, seq: int,
                       final: bool = False, last_seq: int | None = None,
                       extra_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"seq": seq}
    if final:
        meta["final"] = "true"
    if last_seq is not None:
        meta["lastSeq"] = last_seq
    if extra_metadata:
        meta.update(extra_metadata)
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": state}, "metadata": meta}}}


def wire_artifact_update(run_id: str, context_id: str, seq: int,
                         artifact_id: str, text: str, append: bool,
                         last_chunk: bool,
                         extra_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"seq": seq}
    if extra_metadata:
        meta.update(extra_metadata)
    return {"seq": seq, "type": "artifactUpdate", "ev": {
        "artifactUpdate": {
            "taskId": run_id, "contextId": context_id,
            "artifact": {"artifactId": artifact_id,
                          "parts": [{"text": text}]},
            "append": append, "lastChunk": last_chunk,
            "metadata": meta}}}


def wire_terminal_marker(run_id: str, context_id: str, state: str,
                         last_seq: int) -> dict[str, Any]:
    """Stage-4 terminal update; final + lastSeq in metadata."""
    return wire_status_update(run_id, context_id, state, seq=None,
                              final=True, last_seq=last_seq)  # type: ignore[arg-type]


def wire_inject_message(context_id: str, text: str,
                        message_id: str) -> dict[str, Any]:
    return {"seq": None, "type": "message", "ev": {
        "message": {"messageId": message_id, "contextId": context_id,
                     "role": "user",
                     "parts": [{"text": text}], "metadata": {}}}}


def wire_context_id_required() -> dict[str, Any]:
    return {"code": -32602, "message": "Invalid params: contextId required",
            "data": {"reason": "context_required"}}


def wire_unknown_outcome() -> dict[str, Any]:
    return {"code": -32000, "message": "unknown outcome",
            "data": {"reason": "unknown_outcome"}}


def wire_replay_refused(reason: str) -> dict[str, Any]:
    return {"code": -32000, "message": f"replay refused: {reason}",
            "data": {"reason": reason}}
```

- [ ] **Step 2: 编译验证**

```bash
cd engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/wire.py
```

- [ ] **Step 3: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/wire.py
git commit -m "feat(a2a): wire.py — pure wire conversion functions"
```

---

### Task 2: `card.py`（Agent Card 生成——纯函数）

**Files:**
- Create: `engine/adapter/src/engine/community/api/transport/a2a/card.py`

- [ ] **Step 1: 写 card.py**

```python
"""A2A v1.0 Agent Card — pure JSON generation.

Spec: card fields verified in spike G1a; tags is required (spec §4.4.5).
"""
from __future__ import annotations

from typing import Any

# Extension URIs (G2 锁定——spike/a2a/settings.py EXT_URIS 同源)
EXT_BASE = "https://avernet.local/a2a/ext"
EXTENSION_URIS = {
    "inject": f"{EXT_BASE}/inject/v1",
    "context": f"{EXT_BASE}/context/v1",
    "replay": f"{EXT_BASE}/replay/v1",
    "retry": f"{EXT_BASE}/retry/v1",
}


def make_agent_card(base_url: str, *,
                    agent_name: str = "OpenClaw Engine",
                    description: str = "Engine adapter with A2A v1.0 JSON-RPC binding",
                    version: str = "0.1.0") -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": agent_name,
        "description": description,
        "version": version,
        "supportedInterfaces": [
            {"url": f"{base_url}/rpc",
             "protocolBinding": "JSONRPC",
             "protocolVersion": "1.0"},
        ],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extensions": [
                {"url": uri, "required": False}
                for uri in EXTENSION_URIS.values()
            ],
        },
        "skills": [
            {
                "id": "conversation",
                "name": "Conversation",
                "description": "Send messages and receive streamed responses",
                "tags": ["conversation"],
            },
        ],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
    }
    return out
```

- [ ] **Step 2: 编译验证**

```bash
cd engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/card.py
```

- [ ] **Step 3: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/card.py
git commit -m "feat(a2a): card.py — Agent Card v1.0 generation"
```

---

### Task 3: `kernel.py`（核心——run 生命周期 + 订阅 + D14 + 容量治理）

**Files:**
- Create: `engine/adapter/src/engine/community/api/transport/a2a/kernel.py`

- [ ] **Step 1: 写 kernel.py**

```python
"""A2A transport kernel — run lifecycle, subscription, D14 idempotency.

Design: docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md §8
Spike reference: spike/a2a/.harness/src/kernel.py (55 tests green)

Invariants:
  1. last_seq independent of event buffer (cache_limit clears buffer, not seq)
  2. cache_limit (count/bytes/total budget) marks显式拒回放, delivery继续
  3. subscribe(): lock-internal snapshot+registration (no loss window)
  4. slow subscriber: kicked on QueueFull with termination signal
  5. complete_run(): set state→publish terminal→finish (RFC-1a phase)
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any, AsyncIterator

from engine.community.api.transport.a2a import wire

MAX_RUNS = 16
MAX_EVENTS_PER_RUN = 20_000
MAX_BYTES_PER_RUN = 16 * 1024 * 1024
TOTAL_BUDGET_BYTES = 64 * 1024 * 1024
SUBSCRIBER_QUEUE_SIZE = 256
TERMINAL_TTL_SECONDS = 180


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class UnknownOutcomeSentinel:
    def __repr__(self) -> str:
        return "UNKNOWN_OUTCOME"


class KeyConflictError(Exception):
    pass


class _Run:
    __slots__ = ("run_id", "context_id", "state", "events", "bytes", "last_seq",
                 "saw_delta", "cache_limit", "subscribers", "lock",
                 "replay_expires_at", "idempotency_key", "request_digest")

    def __init__(self, run_id: str, context_id: str):
        self.run_id = run_id
        self.context_id = context_id
        self.state = "TASK_STATE_SUBMITTED"
        self.events: list[dict[str, Any]] = []
        self.bytes = 0
        self.last_seq = 0
        self.saw_delta = False
        self.cache_limit = False
        self.subscribers: list[asyncio.Queue] = []
        self.lock = asyncio.Lock()
        self.replay_expires_at: float | None = None
        self.idempotency_key: str | None = None
        self.request_digest: str | None = None


class A2aKernel:
    UNKNOWN_OUTCOME = UnknownOutcomeSentinel()

    def __init__(self) -> None:
        self.runs: dict[str, _Run] = {}
        self._idem: dict[tuple[str, str], str] = {}  # (ctx, key) → run_id
        self._inject_dedup: dict[tuple[str, str], float] = {}
        self._context_subs: dict[str, list[asyncio.Queue]] = {}
        self._total_bytes = 0
        self._global_lock = asyncio.Lock()

    # ── D14 atomic entry ──
    async def open_run(self, context_id: str | None, idempotency_key: str,
                       mode: str, request_digest: str | None = None
                       ) -> tuple[str, bool] | UnknownOutcomeSentinel:
        if not context_id:
            raise ValueError("contextId is required")
        async with self._global_lock:
            existing = self._idem.get((context_id, idempotency_key))
            if existing is not None and existing in self.runs:
                r = self.runs[existing]
                if r.request_digest != request_digest:
                    raise KeyConflictError((context_id, idempotency_key))
                return existing, False
            if mode == "retry_only":
                return self.UNKNOWN_OUTCOME
            if len(self.runs) >= MAX_RUNS:
                self._evict_oldest_finished()
            rid = uuid.uuid4().hex[:12]
            r = _Run(rid, context_id)
            r.idempotency_key = idempotency_key
            r.request_digest = request_digest
            self.runs[rid] = r
            self._idem[(context_id, idempotency_key)] = rid

        await self._publish(rid, wire.wire_task_event(rid, context_id,
                                                      r.state))
        return rid, True

    def record_upstream_call(self, key: str) -> None:
        pass  # engine adapter has no external upstream call side effect

    async def send_message(self, context_id: str, message_text: str,
                           idempotency_key: str, mode: str = "first") \
            -> dict[str, Any] | UnknownOutcomeSentinel:
        digest = _digest(message_text)
        out = await self.open_run(context_id, idempotency_key, mode, digest)
        if out is self.UNKNOWN_OUTCOME:
            return out
        rid, created = out
        if created:
            self.record_upstream_call(idempotency_key)
        r = self.runs.get(rid)
        if r is None:
            return self.UNKNOWN_OUTCOME
        return {"task": {"id": r.run_id, "contextId": r.context_id,
                          "status": {"state": r.state}, "metadata": {}}}

    # ── completion ──
    async def complete_run(self, rid: str,
                           state: str = "TASK_STATE_COMPLETED") -> None:
        r = self.runs[rid]
        r.state = state
        await self._publish(rid, wire.wire_status_update(
            rid, r.context_id, state, seq=r.last_seq))
        r.replay_expires_at = time.monotonic() + TERMINAL_TTL_SECONDS
        marker = wire.wire_terminal_marker(rid, r.context_id, state,
                                           r.last_seq)
        for q in list(r.subscribers):
            self._deliver_terminal(r, q, marker)
        asyncio.get_running_loop().call_later(TERMINAL_TTL_SECONDS,
                                                self._evict, rid)

    def _deliver_terminal(self, r: _Run, q: asyncio.Queue,
                          item: dict[str, Any]) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            if q in r.subscribers:
                r.subscribers.remove(q)
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def _evict(self, rid: str) -> None:
        r = self.runs.pop(rid, None)
        if r:
            self._total_bytes -= r.bytes
            self._idem.pop((r.context_id, r.idempotency_key), None)
            for q in list(r.subscribers):
                self._deliver_terminal(r, q, None)  # type: ignore[arg-type]

    def _evict_oldest_finished(self) -> None:
        for rid, r in sorted(self.runs.items(),
                             key=lambda kv: kv[1].replay_expires_at or 0):
            if r.replay_expires_at is not None:
                self._evict(rid)
                return

    # ── publish ──
    async def _publish(self, rid: str, event: dict[str, Any]) -> None:
        r = self.runs[rid]
        async with r.lock:
            r.last_seq += 1
            event["seq"] = r.last_seq
            event_size = len(str(event))

            if not r.cache_limit:
                fits = (len(r.events) < MAX_EVENTS_PER_RUN
                        and r.bytes + event_size <= MAX_BYTES_PER_RUN
                        and self._total_bytes + event_size <= TOTAL_BUDGET_BYTES)
                if fits:
                    r.events.append(event)
                    r.bytes += event_size
                    self._total_bytes += event_size
                else:
                    self._total_bytes -= r.bytes
                    r.events = []
                    r.bytes = 0
                    r.cache_limit = True

            for q in list(r.subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    r.subscribers.remove(q)
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

    async def publish_delta(self, rid: str, text: str) -> None:
        r = self.runs[rid]
        first = not r.saw_delta
        r.saw_delta = True
        artifact_id = f"text-{rid}"
        await self._publish(rid, wire.wire_artifact_update(
            rid, r.context_id, 0, artifact_id, text,
            append=not first, last_chunk=False))

    async def publish_final(self, rid: str, text: str) -> None:
        r = self.runs[rid]
        await self._publish(rid, wire.wire_artifact_update(
            rid, r.context_id, 0, f"text-{rid}", text,
            append=False, last_chunk=True))

    async def publish_status(self, rid: str, state: str) -> None:
        r = self.runs[rid]
        r.state = state
        await self._publish(rid, wire.wire_status_update(
            rid, r.context_id, state, seq=0))

    # ── subscribe / replay ──
    async def subscribe(self, rid: str, since_seq: int | None = None) \
            -> tuple[list[dict[str, Any]], asyncio.Queue]:
        r = self.runs.get(rid)
        if r is None:
            raise LookupError(rid)

        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        async with r.lock:
            backlog = ([] if since_seq is None
                       else [e for e in r.events if e["seq"] > since_seq])
            r.subscribers.append(q)
            if since_seq is None:
                q.put_nowait(wire.wire_task_event(rid, r.context_id,
                                                   r.state))
        if r.state in wire.TERMINAL_STATES:
            q.put_nowait(wire.wire_terminal_marker(rid, r.context_id,
                                                    r.state, r.last_seq))
        return backlog, q

    async def replay(self, rid: str, since_seq: int) \
            -> tuple[list[dict[str, Any]], asyncio.Queue] | None:
        r = self.runs.get(rid)
        if r is None or r.cache_limit:
            return None
        if (r.replay_expires_at is not None
                and time.monotonic() > r.replay_expires_at):
            return None
        return await self.subscribe(rid, since_seq=since_seq)

    def unsubscribe(self, rid: str, q: asyncio.Queue) -> None:
        r = self.runs.get(rid)
        if r and q in r.subscribers:
            r.subscribers.remove(q)

    # ── inject ──
    async def inject(self, context_id: str, text: str, inject_id: str) -> str:
        key = (context_id, inject_id)
        if key in self._inject_dedup:
            return "duplicate"
        self._inject_dedup[key] = time.time()
        ev = wire.wire_inject_message(context_id, text, inject_id)
        for q in list(self._context_subs.get(context_id, [])):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return "accepted"

    def subscribe_context(self, context_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._context_subs.setdefault(context_id, []).append(q)
        return q

    def unsubscribe_context(self, context_id: str, q: asyncio.Queue) -> None:
        subs = self._context_subs.get(context_id, [])
        if q in subs:
            subs.remove(q)

    def get_run(self, rid: str) -> _Run | None:
        return self.runs.get(rid)
```

- [ ] **Step 2: 编译验证**

```bash
cd engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/kernel.py
```

- [ ] **Step 3: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/kernel.py
git commit -m "feat(a2a): kernel.py — run lifecycle, subscription, D14 idempotency"
```

---

### Task 4: `router.py`（FastAPI 路由——薄壳协议适配）

**Files:**
- Create: `engine/adapter/src/engine/community/api/transport/a2a/router.py`

- [ ] **Step 1: 写 router.py**

```python
"""A2A v1.0 JSON-RPC binding — FastAPI router.

Thin shell: request parse → kernel call → wire convert → Response.
No state, no business logic (kernel owns it all).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from engine.community.api.transport.a2a import wire
from engine.community.api.transport.a2a.card import make_agent_card
from engine.community.api.transport.a2a.kernel import (
    A2aKernel, KeyConflictError,
)

log = logging.getLogger("engine-a2a")

router = APIRouter()

# Module-level kernel singleton (one per FastAPI process).
_kernel = A2aKernel()

_HANDLERS: dict[str, Any] = {}


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _rpc_response(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error(rid: Any, error: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": error})


@router.get("/.well-known/agent-card.json")
async def agent_card() -> dict[str, Any]:
    from engine.community.config import get_local_base_url
    return make_agent_card(get_local_base_url())


@router.post("/rpc")
async def rpc(request: Request) -> Response:
    body = await request.json()
    rid = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    handler = _HANDLERS.get(method)
    if handler is None:
        return _rpc_error(rid, {"code": -32601,
                                 "message": f"Method not found: {method}"})
    try:
        outcome = await handler(params)
    except KeyConflictError:
        return _rpc_error(rid, {"code": -32003,
                                 "data": {"reason": "key_conflict"}})
    except ValueError as e:
        msg = str(e)
        if "contextId" in msg:
            return _rpc_error(rid, wire.wire_context_id_required())
        return _rpc_error(rid, {"code": -32602, "message": msg})

    if isinstance(outcome, Response):
        return outcome
    if isinstance(outcome, dict) and "error" in outcome:
        return _rpc_error(rid, outcome["error"])
    return _rpc_response(rid, outcome)
```

- [ ] **Step 2: 追加各 handler（拼接在同一文件——四个方法组）**

```python
# ── SendMessage / SendStreamingMessage ──

def _retry_flag(params: dict[str, Any]) -> str:
    meta = params.get("metadata")
    if not meta or "avernet.retryOnly" not in meta:
        return "first"
    return "retry_only" if meta["avernet.retryOnly"] else "first"


def _message_text(msg: dict[str, Any]) -> str:
    return "".join(p.get("text", "") for p in msg.get("parts", []))


async def _send_message(params: dict[str, Any]) -> dict[str, Any]:
    msg = params["message"]
    ctx = msg.get("contextId")
    if not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.send_message(ctx, _message_text(msg),
                                      msg.get("messageId", ""),
                                      _retry_flag(params))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return {"error": wire.wire_unknown_outcome()}
    return out


_HANDLERS["SendMessage"] = _send_message


async def _send_streaming(params: dict[str, Any]) -> Response:
    msg = params["message"]
    ctx = msg.get("contextId")
    if not ctx:
        raise ValueError("contextId is required")
    out = await _kernel.open_run(ctx, msg.get("messageId", ""),
                                  _retry_flag(params),
                                  _digest_or_none(_message_text(msg)))
    if out is A2aKernel.UNKNOWN_OUTCOME:
        return _sse_error_response(msg.get("messageId"),
                                     wire.wire_unknown_outcome())
    rid, _created = out
    backlog, q = await _kernel.subscribe(rid, since_seq=0)
    return _stream_response(rid, backlog, q)


_HANDLERS["SendStreamingMessage"] = _send_streaming


def _digest_or_none(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _sse_error_response(rid: Any, error: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": error})


# ── SubscribeToTask / ReplayEvents ──

async def _subscribe_task(params: dict[str, Any]) -> Response:
    tid = params.get("taskId", "")
    r = _kernel.get_run(tid)
    if r is None:
        return _sse_error_response(params.get("taskId"),
                                    wire.wire_replay_refused("not_found"))
    if r.state in wire.TERMINAL_STATES:
        return _sse_error_response(params.get("taskId"),
                                    wire.wire_replay_refused(
                                        "task_terminal_use_replay"))
    backlog, q = await _kernel.subscribe(tid, since_seq=None)
    return _stream_response(tid, backlog, q)


_HANDLERS["SubscribeToTask"] = _subscribe_task


async def _replay_events(params: dict[str, Any]) -> Response:
    tid = params.get("taskId", "")
    out = await _kernel.replay(tid, params.get("sinceSeq", 0))
    if out is None:
        r = _kernel.get_run(tid)
        reason = ("cache_limit" if r is not None and r.cache_limit
                  else "not_found_or_expired")
        return _sse_error_response(params.get("taskId"),
                                    wire.wire_replay_refused(reason))
    backlog, q = out
    return _stream_response(tid, backlog, q)


_HANDLERS["ReplayEvents"] = _replay_events


# ── Extensions: InjectContext / SubscribeToContext ──

async def _inject_context(params: dict[str, Any]) -> dict[str, Any]:
    ctx = params.get("contextId", "")
    msg = params["message"]
    status = await _kernel.inject(ctx, _message_text(msg),
                                  msg.get("messageId", ""))
    return {"inject": status}


_HANDLERS["InjectContext"] = _inject_context


async def _subscribe_context(params: dict[str, Any]) -> Response:
    ctx = params.get("contextId", "")
    q = _kernel.subscribe_context(ctx)

    async def gen():
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield _sse(_rpc_response(None, wire.kernel_event_to_wire(ev)))
        finally:
            _kernel.unsubscribe_context(ctx, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


_HANDLERS["SubscribeToContext"] = _subscribe_context


# ── SSE stream generator ──

def _stream_response(rid: str, backlog: list, q: asyncio.Queue) -> Response:
    async def gen():
        try:
            for e in backlog:
                yield _sse(_rpc_response(None, wire.kernel_event_to_wire(e)))
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield _sse(_rpc_response(None,
                                          wire.kernel_event_to_wire(ev)))
                su = (ev.get("ev") or {}).get("statusUpdate") or {}
                if (su.get("metadata") or {}).get("final") == "true":
                    return
        finally:
            _kernel.unsubscribe(rid, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 3: 编译验证**

```bash
cd engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/router.py
```

- [ ] **Step 4: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/router.py
git commit -m "feat(a2a): router.py — JSON-RPC /rpc + Agent Card + SSE streaming"
```

---

### Task 5: `__init__.py` + `app.py` 注册

**Files:**
- Create: `engine/adapter/src/engine/community/api/transport/a2a/__init__.py`
- Modify: `engine/adapter/src/engine/community/api/app.py`（+2 行 import & include_router）

- [ ] **Step 1: 写 `__init__.py`**

```python
"""A2A v1.0 JSON-RPC transport for the engine adapter."""
from engine.community.api.transport.a2a.router import router as a2a_router  # noqa: F401
```

- [ ] **Step 2: 修 app.py（追加在 ws_router 后面）**

```python
# 追加在 app.include_router(ws_router) 之后：
from engine.community.api.transport.a2a.router import router as a2a_router  # noqa: E402
app.include_router(a2a_router)
```

- [ ] **Step 3: 编译验证（确认不破坏现有 app 导入链）**

```bash
cd engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/app.py
```

- [ ] **Step 4: 全量测试（现有单测必绿——证明 v3 路径零破坏）**

```bash
cd engine/adapter && .venv/bin/python -m pytest tests/ -x -q
```

- [ ] **Step 5: Commit**

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/__init__.py \
  engine/adapter/src/engine/community/api/app.py
git commit -m "feat(a2a): register A2A router on FastAPI app"
```

---

### Task 6: 引擎适配对接（kernel ↔ EngineManager 桥）

**Files:**
- Create: `engine/adapter/src/engine/community/api/transport/a2a/engine_bridge.py`

- [ ] **Step 1: 读 EngineManager 的可用方法面**

```bash
grep -n "async def \|def " engine/adapter/src/engine/community/manager.py | head -30
```

- [ ] **Step 2: 写 engine_bridge.py——将 openclaw 引擎的 chat 事件流入 kernel**

```python
"""Bridge: EngineManager chat dispatch → A2aKernel event stream.

When a SendStreamingMessage arrives, we:
  1. open_run in kernel (D14)
  2. dispatch the text through EngineManager to the active engine
  3. pump engine events into kernel (delta → artifact, final → terminal)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from engine.community import manager as engine_manager_mod
from engine.community.api.transport.a2a import wire
from engine.community.api.transport.a2a.kernel import A2aKernel

log = logging.getLogger("engine-a2a-bridge")


async def execute_and_stream(kernel: A2aKernel, rid: str, context_id: str,
                             text: str) -> None:
    """Dispatch through EngineManager and pump events into kernel."""
    manager = engine_manager_mod.EngineManager.get_instance()

    r = kernel.get_run(rid)
    if r is None:
        log.error("run %s not found", rid)
        return

    r.state = "TASK_STATE_WORKING"
    await kernel._publish(rid, wire.wire_status_update(
        rid, context_id, "TASK_STATE_WORKING", seq=0))

    try:
        # The exact engine dispatch API depends on EngineManager.
        # During Task 6 Step 1 discovery, adjust to actual method name.
        active_engine = manager.get_active_engine()
        if active_engine is None:
            await kernel.publish_delta(rid, "No active engine available.")
            await kernel.complete_run(rid, "TASK_STATE_FAILED")
            return

        # Call the engine's chat handler—callback pattern from the spike.
        async def on_delta(delta_text: str) -> None:
            await kernel.publish_delta(rid, delta_text)

        async def on_final(final_text: str) -> None:
            await kernel.publish_final(rid, final_text)

        # Engine-specific dispatch (discovered in Step 1):
        await _dispatch_to_engine(manager, active_engine, context_id,
                                  text, on_delta, on_final)
        await kernel.complete_run(rid, "TASK_STATE_COMPLETED")

    except Exception as e:
        log.exception("engine dispatch failed for run %s", rid)
        try:
            await kernel._publish(rid, wire.wire_status_update(
                rid, context_id, "TASK_STATE_FAILED", seq=0,
                extra_metadata={"error": str(e)[:200]}))
            await kernel.complete_run(rid, "TASK_STATE_FAILED")
        except Exception:
            pass


async def _dispatch_to_engine(manager: Any, engine: Any,
                              context_id: str, text: str,
                              on_delta: Any, on_final: Any) -> None:
    """Engine-specific dispatch — adjust per Step 1 discovery.

    Initial implementation targets OpenClaw plugin surface.
    """
    # If EngineManager has a direct send_chat method, use it.
    # Otherwise, call plugin methods—exact names from Step 1.
    # This is the one function where implementation must be aligned
    # with the actual EngineManager API surface.
    raise NotImplementedError(
        "To be filled after Task 6 Step 1 discovery of EngineManager API")
```

- [ ] **Step 3: 在 router.py 的 `_send_streaming` 里加上桥调用**

在 `_send_streaming` 的 `open_run` 调用后、`backlog, q = await _kernel.subscribe(...)` 之前，加入：

```python
from engine.community.api.transport.a2a.engine_bridge import execute_and_stream
asyncio.get_event_loop().create_task(
    execute_and_stream(_kernel, rid, ctx, _message_text(msg)))
```

（放为函数体内部非缩进顶层——正常 Python 代码可写为局部 import。）

- [ ] **Step 4: 编译 + Commit**

```bash
cd engine/adapter && .venv/bin/python -m py_compile \
  src/engine/community/api/transport/a2a/engine_bridge.py \
  src/engine/community/api/transport/a2a/router.py
```

```bash
git add engine/adapter/src/engine/community/api/transport/a2a/engine_bridge.py \
  engine/adapter/src/engine/community/api/transport/a2a/router.py
git commit -m "feat(a2a): engine_bridge — EngineManager dispatch → kernel events"
```

---

### Task 7: `config.py` base_url 补丁 + singlebox 验证清单

**Files:**
- Modify: `engine/adapter/src/engine/community/config.py` 或新增 helper

- [ ] **Step 1: 确认 config.py 是否有获取 base_url 的函数；无则追加**

```bash
grep -n "base_url\|BASE_URL" engine/adapter/src/engine/community/config.py
```

若无，在 `config.py` 中添加：

```python
def get_local_base_url() -> str:
    """Get the local base URL for Agent Card generation."""
    import os
    host = os.environ.get("ENGINE_HOST", "127.0.0.1")
    port = os.environ.get("ENGINE_PORT", "20003")
    return f"http://{host}:{port}"
```

- [ ] **Step 2: 全量编译 + 测试**

```bash
cd engine/adapter && .venv/bin/python -m py_compile src/engine/community/api/transport/a2a/*.py
cd engine/adapter && .venv/bin/python -m pytest tests/ -x -q
```

- [ ] **Step 3: Commit + final push**

```bash
git add .
git commit -m "feat(a2a): config helper + complete A2A transport module"
AVERNET_PRE_PUSH_MERGE_TARGET=origin/dev git push
```

---

## Singlebox 统一验证清单（写完代码后执行）

```bash
# 1. 启动 singlebox
bash singlebox/singlebox.sh start all

# 2. 确认 engine adapter 起来了
curl -s http://127.0.0.1:20003/health
curl -s http://127.0.0.1:20003/.well-known/agent-card.json | jq .

# 3. A2A sendMessage（中文字本）
curl -s -X POST http://127.0.0.1:20003/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"你好"}],"messageId":"m1","contextId":"test-ctx"},"metadata":{}}}' | jq .

# 4. A2A streaming
curl -sN -X POST http://127.0.0.1:20003/rpc \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"SendStreamingMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"你好"}],"messageId":"m2","contextId":"test-ctx2"},"metadata":{}}}'

# 5. SDK 客户端消费（用 spike 的 g1a_sdk 改 base_url 即可）
spike/a2a/.venv/bin/python -c "
import asyncio, sys, httpx
sys.path.insert(0, 'spike/a2a/probes')
from g1a_validate import validate
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers.proto_helpers import new_text_message
from a2a.types.a2a_pb2 import SendMessageRequest

async def main():
    async with httpx.AsyncClient(timeout=30) as http:
        card = await A2ACardResolver(httpx_client=http, base_url='http://127.0.0.1:20003').get_agent_card()
        client = ClientFactory(ClientConfig(streaming=True, httpx_client=http)).create(card)
        msg = new_text_message('你好', context_id='sdk-verify-ctx')
        from google.protobuf.json_format import MessageToDict
        events = []
        async for r in client.send_message(SendMessageRequest(message=msg)):
            kind = r.WhichOneof('payload')
            d = MessageToDict(r, preserving_proto_field_name=False, use_integers_for_enums=False)
            events.append({'result': d.get('result', d)})
        ok, checks = validate(events)
        print(f'validate: ok={ok} checks={checks}')
asyncio.run(main())
"

# 6. v3 WS 路径回归——现有 singlebox 测试套件全绿
# （singlebox.sh 的 5bot 测试或 apps/bcs 的既有 e2e）

# 7. 跨协议幂等验证
# v3 chat.send 首提 → A2A SendMessage retry-only → 同一 run
```

---

## 自审记录

1. **Spec 覆盖**：D6 JSONRPC 绑定（Task 4 router）✓；D14 retry-only（Task 3 kernel + Task 4 wire_retry_flag）✓；Task 生命周期（Task 3）✓；artifact 阶段表（Task 1 wire + Task 3 publish_delta/final）✓；D7 budget（Task 3）✓；D12 replay（Task 3 replay + Task 4）✓；§8.2 context 订阅（Task 3 inject + Task 4 SubscribeToContext）✓；Agent Card（Task 2）✓；引擎桥接（Task 6）✓。
2. **EngineManager API 依赖**：Task 6 依赖 Step 1 的发现式 grep——`_dispatch_to_engine` 标记 NotImplementedError 需依实际 API 面填充。这是全计划**唯一**的待发现项。
3. **Type 一致性**：`A2aKernel` 方法签名在三处（kernel.py 定义、router.py 调用、engine_bridge.py 调用）一致——`open_run(ctx, key, mode, digest)` / `subscribe(rid, since_seq)` / `replay(rid, since_seq)` / `publish_delta(rid, text)` / `publish_final(rid, text)` / `complete_run(rid, state)` / `get_run(rid)` / `unsubscribe(rid, q)` / `inject(ctx, text, inject_id)` / `subscribe_context(ctx)` / `unsubscribe_context(ctx, q)`。
4. **v3 无破坏证明**：Task 5 Step 4 跑全量单测 + singlebox 验证清单 #6 v3 路径回归。
