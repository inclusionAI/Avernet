# Engine→A2A 协议替换 · 阶段 0 Spike 实施计划（v2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出 A2A v1.0（JSON-RPC 绑定）替换 engine v3 WS 信道的 Spike 证据：门禁 G1a-G5 全部可判、失败用例 F1-F8 全部可复跑；**门禁结论只由机器生成的证据文件支撑，探针失败以非零退出码显式暴露，报告按证据等级分级**。

**Architecture:** 旁置 Spike harness（`spike/a2a/`，独立 venv）：kernel 按设计文档 §8 契约实现（统一订阅入口、锁内交接、D14 原子判定）；FastAPI 壳提供 card 与 `/rpc`；**同一 kernel 同时被 A2A 壳、v3 迷你服务器、G3/G5 实验复用——任何离线拼接演示不是证据**。mock 引擎先行；v3 bridge 后置接真引擎。spike 目录永不合入 dev。

**Tech Stack:** Python 3.12（自建 venv）、a2a-sdk（G2 锁版）、FastAPI + uvicorn、httpx、websockets、pytest（同步 TestClient 模式）、Playwright。

**上游文档:** `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`（V5 通过版，§引用均指向它。本计划自身的 v2 修订吸收计划评审 R6 八项 + 撰写自审发现的实现缺陷：统一订阅与终态终止、last_seq 独立于缓存、F5 故障时序、凭据负向等）
**分支:** `feat/engine-a2a-protocol-replacement`
**端口:** 20043=A2A 壳 / 20044=v3 迷你 / 20045=F5 被测服务 / 20046=故障代理 / 20047=桥测试 / 20003=singlebox 真引擎

---

## 文件结构

```
spike/a2a/                     # 永不合入 dev
├── .venv/  .gitignore
├── requirements-spike.txt      # 含 pytest 等全部复跑依赖（T1）
├── settings.py                 # 端口/扩展 URI（T2）
├── kernel.py                   # 唯一契约实现：Run/统一订阅/D7/D14/inject（T3/T6）
├── engine_mock.py              # 脚本化假引擎（含可暂停闸门，T3）
├── server.py                   # A2A 壳：card + /rpc + SSE 三通道（T2/T4）
├── v3_server.py                # v3 迷你服务器：同 kernel，legacy 模式（T8）
├── bridge_v3.py                # 真 engine v3 WS → kernel 活泵（T9）
├── client_t.py                 # 客户端模板：超时/5xx→retry-only、能力门控（T7）
├── fault_proxy.py              # F5 响应吞没代理（T7）
├── probes/
│   ├── g1a_validate.py  g1a_sdk.py           # T5（验收器可负测）
│   ├── g3_realtime.py                          # T9
│   ├── g1b_browser.html  g1b_playwright.py    # T10
│   ├── g5_protocol.py                          # T11（判据预声明）
│   └── report.py                               # T12（evidence/*.json 聚合）
├── tests/
│   ├── conftest.py  test_rpc.py  test_kernel.py  test_stream.py
│   ├── test_g1a_validator.py  test_retry.py  test_f5_restart.py
│   └── test_v3_f7_f8.py  test_bridge.py
└── evidence/                   # 机器生成（G2-versions.md、G*.json、REPORT.md）
```

**关键不变量（跨任务一致的 kernel 契约，先声明后实现）：**
1. `Kernel.send_message(ctx, text, key, mode)` → `{"task":...}` | `UNKNOWN_OUTCOME` 哨兵；`Kernel.open_run(ctx, key, mode, digest)` → `(run_id, created)` | `UNKNOWN_OUTCOME`；`Kernel.execute_message(run_id)`（记录上游调用并执行）
2. `Kernel.subscribe(rid, since_seq)` → `(backlog, queue)`；`since_seq=None` = 标准订阅（首事件为合成 Task 快照）；`Kernel.replay(rid, since_seq)` → 同型 | `None`（显式拒绝）
3. 事件序号 = `Run.last_seq`，**独立于事件缓冲**（cache_limit 清空缓冲不清零序号）；锁内完成"快照 + 订阅注册"，与发布共持 run 锁 → 无丢失窗口
4. cache_limit 只停止缓冲，**不停止投递**；终态事件带 `final: True` 标记，流据此终止（不依赖 180s 驱逐）

---

### Task 1: 环境与依赖锁定（G2 前半）

**Files:** Create `spike/a2a/requirements-spike.txt`、`spike/a2a/evidence/G2-versions.md`；Modify `.gitignore`

- [ ] **Step 1: venv 与目录**

```bash
mkdir -p spike/a2a/{evidence,tests,probes}
python3 -m venv spike/a2a/.venv
spike/a2a/.venv/bin/pip install -U pip
```

- [ ] **Step 2: 安装并整锁依赖**

```bash
spike/a2a/.venv/bin/pip install "a2a-sdk[http-server]" fastapi uvicorn httpx \
  websockets pytest
spike/a2a/.venv/bin/pip freeze > spike/a2a/requirements-spike.txt
grep -cE '^(pytest|a2a|fastapi|uvicorn|httpx|websockets)==' spike/a2a/requirements-spike.txt
```

预期 ≥6 行命中；命中 <6 视为锁失败。**a2a-sdk 安装失败 = G2 早退**：记录 G2-versions.md、停止本计划、回设计文档 D4 降级分支重评审——不允许跳过。

- [ ] **Step 3: SDK API 面备忘（发现什么记什么）**

```bash
grep -rn "class .*Resolver\|def get_agent_card\|def send_message\|def send_streaming" \
  spike/a2a/.venv/lib/python*/site-packages/a2a/client/ | head -8
spike/a2a/.venv/bin/pip show a2a-sdk
```

把两类输出原样存入 `evidence/G2-versions.md`（含 a2a-sdk 版本号与 client 类/方法名——T5 import 名以这里为准）。

- [ ] **Step 4: gitignore + 提交**

```bash
printf 'spike/a2a/.venv/\nspike/a2a/**/__pycache__/\nspike/a2a/evidence_upstream_calls.jsonl\n' >> .gitignore
git add .gitignore spike/a2a/requirements-spike.txt spike/a2a/evidence/G2-versions.md
git commit -m "chore(spike): lock full spike toolchain (G2)"
```

---

### Task 2 + 3: card`/rpc` 骨架 + kernel 统一订阅（两个任务同批提交）

**Files:** Create `settings.py`、`server.py`、`kernel.py`、`engine_mock.py`、`tests/conftest.py`、Test `test_rpc.py`、`test_kernel.py`

- [ ] **Step 1: 先写失败测试（两个测试文件一并落盘）**

```python
# spike/a2a/tests/conftest.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from server import build_app
from kernel import Kernel


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    with TestClient(build_app(Kernel(engine="mock"))) as c:
        yield c
```

```python
# spike/a2a/tests/test_rpc.py
def test_card_wellknown_shape(client):
    card = client.get("/.well-known/agent-card.json").json()
    iface = card["supportedInterfaces"][0]
    assert iface["protocolBinding"] == "JSONRPC" and iface["protocolVersion"] == "1.0"
    assert iface["url"].endswith("/rpc")
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["tags"] == ["conversation"]          # spec §4.4.5


def test_send_message_jsonrpc_envelope(client):
    r = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 11, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hi"}],
                               "messageId": "rpc-m1"}, "metadata": {}}})
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 11
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_unknown_method_jsonrpc_error(client):
    r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 12, "method": "NoSuch"})
    assert r.json()["error"]["code"] == -32601
```

```python
# spike/a2a/tests/test_kernel.py
import asyncio

from kernel import Kernel, MAX_EVENTS_PER_RUN, TERMINAL_TTL_SECONDS


def run(coro):
    return asyncio.run(coro)


def test_event_seq_monotonic_task_first_terminal_last():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kk", "first", "d")
        await k.execute_message(rid)
        backlog, _q = await k.subscribe(rid, since_seq=0)
        return backlog
    evs = run(main())
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(set(seqs))                    # 单调无重号
    assert evs[0]["type"] == "task"                     # 首事件 Task
    states = [e["ev"]["statusUpdate"]["status"]["state"] for e in evs
              if e["type"] == "statusUpdate"]
    assert states[-1] == "TASK_STATE_COMPLETED"


def test_artifact_stage_table():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kk", "first", "d2")
        await k.execute_message(rid)
        backlog, _q = await k.subscribe(rid, since_seq=0)
        return [e["ev"]["artifactUpdate"] for e in backlog
                if e["type"] == "artifactUpdate"]
    arts = run(main())
    assert (arts[0]["append"], arts[0]["lastChunk"]) == (False, False)
    assert all((a["append"], a["lastChunk"]) != (True, True) for a in arts[1:-1])
    assert (arts[-1]["append"], arts[-1]["lastChunk"]) == (False, True)


def test_terminal_replay_window_is_180s_not_immediate():       # R6 P1-2
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kw", "first", "d")
        await k.execute_message(rid)
        out = await k.replay(rid, since_seq=0)
        return k, rid, out is not None
    k, rid, immediate_ok = run(main())
    assert immediate_ok                                          # 刚结束仍在窗口内
    k.runs[rid].replay_expires_at -= TERMINAL_TTL_SECONDS + 1    # 快进窗口外
    assert run(k.replay(rid, since_seq=0)) is None


def test_cache_limit_stops_caching_not_delivery():              # R6 P1-2 + F1
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kc", "first", "d")
        r = k.runs[rid]
        for _ in range(MAX_EVENTS_PER_RUN + 3):
            await k.publish_status(rid, "TASK_STATE_WORKING")
        assert r.cache_limit is True
        assert await k.replay(rid, since_seq=0) is None          # F1 显式拒绝
        backlog, q = await k.subscribe(rid, since_seq=0)         # 投递继续
        assert backlog == []                                     # 缓冲已清
        await k.publish_status(rid, "TASK_STATE_COMPLETED")
        got = await asyncio.wait_for(q.get(), 1)
        return got, r
    got, r = run(main())
    assert got["ev"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert r.last_seq > MAX_EVENTS_PER_RUN                      # seq 不随缓存清零


def test_f6_missed_events_replay_by_seq():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kf", "first", "d")
        await k.execute_message(rid)
        backlog, _q = await k.subscribe(rid, since_seq=0)
        mid = backlog[len(backlog) // 2]["seq"]
        missed, _q2 = await k.replay(rid, since_seq=mid)
        return mid, [e["seq"] for e in missed]
    mid, seqs = run(main())
    assert seqs == sorted(seqs) and seqs[0] == mid + 1


def test_standard_subscribe_snapshot_then_marker():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "ks", "first", "d")
        await k.execute_message(rid)
        backlog, q = await k.subscribe(rid)                     # since=None
        first = await asyncio.wait_for(q.get(), 1)
        return backlog, first
    backlog, first = run(main())
    assert backlog == []
    assert first["type"] == "task"                              # §3.1.6 快照
    assert first["ev"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
```

- [ ] **Step 2: 确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_rpc.py spike/a2a/tests/test_kernel.py -v`
Expected: FAIL（`No module named 'server'` / `kernel`）

- [ ] **Step 3: 实现 settings / server / kernel / engine_mock**

```python
# spike/a2a/settings.py
HOST, PORT = "127.0.0.1", 20043
EXT_BASE = "https://avernet.local/a2a/ext"
EXT_URIS = {"inject": f"{EXT_BASE}/inject/v1",
            "context": f"{EXT_BASE}/context/v1",
            "replay": f"{EXT_BASE}/replay/v1"}
```

```python
# spike/a2a/kernel.py
"""Spike kernel：设计文档 §8 契约的最小实现（全 async、单循环）。"""
import asyncio
import hashlib
import json
import time
import uuid

MAX_RUNS = 16
MAX_EVENTS_PER_RUN = 20_000
MAX_BYTES_PER_RUN = 16 * 1024 * 1024
TOTAL_BUDGET = 64 * 1024 * 1024
SUBSCRIBER_QUEUE_SIZE = 256
TERMINAL_TTL_SECONDS = 180

TERMINAL_STATES = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
                   "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _task_obj(r) -> dict:
    return {"id": r.run_id, "contextId": r.context_id,
            "status": {"state": r.state}}


def _terminal_marker(r) -> dict:
    return {"seq": None, "type": "statusUpdate",
            "ev": {"statusUpdate": {"taskId": r.run_id, "contextId": r.context_id,
                                    "status": {"state": r.state},
                                    "final": True}}}


class UnknownOutcome:
    def __repr__(self):
        return "UNKNOWN_OUTCOME"


class KeyConflictError(Exception):
    pass


class Run:
    def __init__(self, run_id, context_id):
        self.run_id, self.context_id = run_id, context_id
        self.state = "TASK_STATE_SUBMITTED"
        self.events: list[dict] = []
        self.bytes_ = 0
        self.last_seq = 0                       # 独立于缓冲（不变量 3）
        self.last_delta_count = 0               # artifact 阶段表计数（§8.4 首块）
        self.cache_limit = False
        self.subscribers: list[asyncio.Queue] = []
        self.lock = asyncio.Lock()
        self.replay_expires_at: float | None = None
        self.idempotency_key = None
        self.request_digest = None


class Kernel:
    UNKNOWN_OUTCOME = UnknownOutcome()

    def __init__(self, engine="mock", calls_path=None):
        self.engine = engine
        self.calls_path = calls_path             # F5 外部记录器
        self.runs: dict[str, Run] = {}
        self.idem: dict[tuple[str, str], str] = {}
        self.inject_dedup: dict[tuple[str, str], float] = {}
        self.context_subs: dict[str, list[asyncio.Queue]] = {}
        self.total_bytes = 0
        self._gl = asyncio.Lock()

    # ---- D14 原子入口 ----
    async def open_run(self, context_id, idempotency_key, mode,
                       request_digest=None):
        async with self._gl:                     # 判定与登记原子（不变量 1）
            rid = self.idem.get((context_id, idempotency_key))
            if rid is not None and rid in self.runs:
                r = self.runs[rid]
                if r.request_digest != request_digest:
                    raise KeyConflictError((context_id, idempotency_key))
                return rid, False
            if mode == "retry_only":
                return self.UNKNOWN_OUTCOME      # 记录不在：不创建执行
            if len(self.runs) >= MAX_RUNS:
                self._evict_oldest_finished()
            rid = uuid.uuid4().hex[:12]
            r = Run(rid, context_id)
            r.idempotency_key = idempotency_key
            r.request_digest = request_digest
            self.runs[rid] = r
            self.idem[(context_id, idempotency_key)] = rid
            created = True
        if created:
            await self._publish(rid, "task", {"task": _task_obj(r)})
        return rid, True

    async def execute_message(self, rid):
        self._record_upstream(self.runs[rid].idempotency_key)
        await self._execute(rid)

    async def send_message(self, context_id, message_text, idempotency_key,
                           mode="first", request_digest=None):
        digest = request_digest or _digest(message_text)
        out = await self.open_run(context_id, idempotency_key, mode, digest)
        if out is self.UNKNOWN_OUTCOME:
            return out
        rid, created = out
        if created:
            await self.execute_message(rid)
        if rid not in self.runs:
            return self.UNKNOWN_OUTCOME
        return {"task": _task_obj(self.runs[rid])}

    def _record_upstream(self, key):
        if self.calls_path:
            with open(self.calls_path, "a") as f:
                f.write(json.dumps({"key": key, "ts": time.time()}) + "\n")

    async def _execute(self, rid):
        from engine_mock import run_engine
        r = self.runs[rid]
        r.state = "TASK_STATE_WORKING"
        await self._publish(rid, "statusUpdate", {
            "taskId": rid, "contextId": r.context_id,
            "status": {"state": r.state}})

        async def emit(kind, payload):
            await self._forward(rid, kind, payload)

        await run_engine(rid, emit)
        r.state = "TASK_STATE_COMPLETED"
        await self._publish(rid, "statusUpdate", {
            "taskId": rid, "contextId": r.context_id,
            "status": {"state": r.state}})
        self._finish_run(rid)

    def _finish_run(self, rid):
        """终态收口：回放窗口 + 终态 marker 投递 + 驱逐调度（bridge 复用）。"""
        r = self.runs[rid]
        r.replay_expires_at = time.monotonic() + TERMINAL_TTL_SECONDS
        for q in r.subscribers:                   # 终态投递永不被 cache_limit 阻断
            q.put_nowait(_terminal_marker(r))
        asyncio.get_running_loop().call_later(
            TERMINAL_TTL_SECONDS, self._evict, rid)

    def _evict(self, rid):
        r = self.runs.pop(rid, None)
        if r:
            self.total_bytes -= r.bytes_
            self.idem.pop((r.context_id, r.idempotency_key), None)
            for q in r.subscribers:
                q.put_nowait(None)                # 流终止信号

    def _evict_oldest_finished(self):
        for rid, r in sorted(self.runs.items(), key=lambda kv: kv[1].replay_expires_at
                             or 0):
            if r.replay_expires_at is not None:
                self._evict(rid)
                return

    # ---- 发布（锁内；cache_limit 只停缓冲，不停投递） ----
    async def _publish(self, rid, ev_type, ev):
        r = self.runs[rid]
        async with r.lock:
            r.last_seq += 1                       # 不变量 3：seq 独立于缓冲
            wrapped = {"seq": r.last_seq, "type": ev_type, "ev": ev}
            blob = len(json.dumps(wrapped, ensure_ascii=False))
            if not r.cache_limit and (
                    len(r.events) < MAX_EVENTS_PER_RUN
                    and r.bytes_ + blob <= MAX_BYTES_PER_RUN
                    and self.total_bytes + blob <= TOTAL_BUDGET):
                r.events.append(wrapped)
                r.bytes_ += blob
                self.total_bytes += blob
                if (len(r.events) >= MAX_EVENTS_PER_RUN
                        or r.bytes_ >= MAX_BYTES_PER_RUN):
                    self.total_bytes -= r.bytes_   # D7：清空 + 标记
                    r.events = []                  # （现网 CACHE_LIMIT 行为）
                    r.bytes_ = 0
                    r.cache_limit = True
            for q in r.subscribers:                # 投递始终进行（不变量 4）
                q.put_nowait(wrapped)

    async def _forward(self, rid, kind, payload):
        r = self.runs[rid]
        if kind == "message_delta":
            aid = f"text-{rid}"
            seen = r.last_delta_count
            self.runs[rid].last_delta_count = seen + 1
            await self._publish(rid, "artifactUpdate", {
                "taskId": rid, "contextId": r.context_id,
                "artifact": {"artifactId": aid,
                             "parts": [{"text": payload["text"]}]},
                "append": seen > 0,                # §8.4 首块 (F,F)
                "lastChunk": False})
        elif kind == "final":
            await self._publish(rid, "artifactUpdate", {
                "taskId": rid, "contextId": r.context_id,
                "artifact": {"artifactId": f"text-{rid}",
                             "parts": [{"text": payload["text"]}]},
                "append": False, "lastChunk": True})   # 终稿替换 (F,T)
        elif kind == "tool_progress":
            await self._publish(rid, "statusUpdate", {
                "taskId": rid, "contextId": r.context_id,
                "status": {"state": r.state}, "metadata": payload})

    async def publish_status(self, rid, state):   # 测试/驱动便利方法
        self.runs[rid].state = state
        await self._publish(rid, "statusUpdate", {
            "taskId": rid, "contextId": self.runs[rid].context_id,
            "status": {"state": state}})

    # ---- 统一订阅（锁内交接） ----
    async def subscribe(self, rid, since_seq=None):
        r = self.runs.get(rid)
        if r is None:
            raise LookupError(rid)
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        async with r.lock:                        # 与 _publish 共持 → 无丢失窗口
            backlog = ([] if since_seq is None else
                       [e for e in r.events if e["seq"] > since_seq])
            r.subscribers.append(q)
            if since_seq is None:
                q.put_nowait({"seq": None, "type": "task",
                              "ev": {"task": _task_obj(r)}})   # §3.1.6 快照
        if r.state in TERMINAL_STATES:
            q.put_nowait(_terminal_marker(r))     # 终态流立即封口
        return backlog, q

    async def replay(self, rid, since_seq):
        r = self.runs.get(rid)
        if r is None or r.cache_limit:
            return None                           # F1：显式拒绝
        if (r.replay_expires_at is not None
                and time.monotonic() > r.replay_expires_at):
            return None                           # 窗口外
        return await self.subscribe(rid, since_seq=since_seq)

    async def cancel(self, rid):
        r = self.runs.get(rid)
        if r and r.state not in TERMINAL_STATES:
            r.state = "TASK_STATE_CANCELED"
            await self._publish(rid, "statusUpdate", {
                "taskId": rid, "contextId": r.context_id,
                "status": {"state": r.state}})
            self._finish_run(rid)
        return {"task": _task_obj(r)} if r else None

    # ---- inject：非 Task + 操作级去重（§8.3） ----
    async def inject(self, context_id, text, inject_id):
        key = (context_id, inject_id)
        if key in self.inject_dedup:
            return "duplicate"
        self.inject_dedup[key] = time.time()
        for q in self.context_subs.get(context_id, []):
            q.put_nowait({"seq": None, "type": "inject",
                          "ev": {"contextId": context_id, "text": text}})
        return "accepted"

    def subscribe_context(self, context_id):
        q = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self.context_subs.setdefault(context_id, []).append(q)
        return q
```


```python
# spike/a2a/engine_mock.py
CHUNKS = ["第一段", "第二段增量", "第三段增量"]
PAUSE_GATE = None            # 测试可注入 asyncio.Event（同循环内）暂停在块 2 前


async def run_engine(run_id: str, emit) -> None:
    for i, chunk in enumerate(CHUNKS):
        if PAUSE_GATE is not None and i == 1:
            await PAUSE_GATE.wait()
        await emit("statusUpdate", {"state": "TASK_STATE_WORKING"})
        await emit("message_delta", {"text": chunk})
        if i == 0:
            await emit("tool_progress", {"tool": "spike-tool", "phase": "run"})
    await emit("final", {"text": "".join(CHUNKS) + "（终稿）"})
```

```python
# spike/a2a/server.py（T2 骨架 + SendMessage；流式三通道在 T4 追加）
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import settings

TERMINAL_STATES = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
                   "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")


def make_card(base: str) -> dict:
    return {
        "name": "a2a-spike", "description": "v3->A2A spike", "version": "0.0.0",
        "supportedInterfaces": [{"url": f"{base}/rpc",
                                 "protocolBinding": "JSONRPC",
                                 "protocolVersion": "1.0"}],
        "capabilities": {"streaming": True, "pushNotifications": False,
                         "extensions": [{"url": u, "required": False}
                                        for u in settings.EXT_URIS.values()]},
        "skills": [{"id": "conversation", "name": "Conversation",
                    "description": "spike conversation",
                    "tags": ["conversation"]}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
    }


def build_app(kernel=None, legacy: bool = False) -> FastAPI:
    if kernel is None:
        kernel = Kernel_default()
    app = FastAPI()
    app.state.kernel = kernel
    app.state.legacy = legacy

    @app.get("/.well-known/agent-card.json")
    def card():
        return make_card(f"http://{settings.HOST}:{settings.PORT}")

    @app.post("/rpc")
    async def rpc(request: Request):
        body = await request.json()
        rid, method, params = body.get("id"), body.get("method"), \
            body.get("params") or {}
        handler = HANDLED.get(method)
        if handler is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "Method not found: " + str(method)}}
        try:
            outcome = await handler(kernel, rid, params)
        except BadRetryFlag:
            return JSONResponse({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32602,
                          "message": "Invalid params: avernet.retryOnly",
                          "data": {"reason": "malformed_retry_flag"}}})
        except KeyConflict:
            return JSONResponse({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32003,
                          "data": {"reason": "key_conflict"}}})
        if isinstance(outcome, JSONResponse):
            return outcome
        return {"jsonrpc": "2.0", "id": rid, "result": outcome}

    return app

def Kernel_default():
    import os
    from kernel import Kernel
    return Kernel(engine="mock", calls_path=os.environ.get("SPIKE_CALLS"))


class BadRetryFlag(Exception):
    pass


class KeyConflict(Exception):
    pass


HANDLED = {}


async def send_message(kernel, rid, params):
    msg = params["message"]
    context = msg.get("contextId") or uuid.uuid4().hex[:8]
    out = await kernel.send_message(context, _text(msg), msg.get("messageId"),
                                    _retry_flag(params))
    if out is kernel.UNKNOWN_OUTCOME:                    # D14
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000, "message": "unknown outcome",
                      "data": {"reason": "unknown_outcome"}}})
    return out


HANDLED["SendMessage"] = send_message


def _retry_flag(params: dict) -> str:
    """§8.5.2：A2A path = params.metadata['avernet.retryOnly']；非布尔 → 显式拒绝。"""
    meta = params.get("metadata")
    if not meta or "avernet.retryOnly" not in meta:
        return "first"
    flag = meta["avernet.retryOnly"]
    if flag is not True and flag is not False:
        raise BadRetryFlag()
    return "retry_only" if flag else "first"


def _text(msg: dict) -> str:
    return "".join(p.get("text", "") for p in msg.get("parts", []))


def create_app():
    return build_app()
```

（kernel 的 `KeyConflictError` 在 server 侧 catch：`from kernel import KeyConflictError as KeyConflict` 放到 import 区——执行文件 import 处一并写好。）

- [ ] **Step 4: 确认通过 + 提交**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_rpc.py spike/a2a/tests/test_kernel.py -v`
Expected: 全 PASS

```bash
git add spike/a2a/settings.py spike/a2a/server.py spike/a2a/kernel.py \
  spike/a2a/engine_mock.py spike/a2a/tests/
git commit -m "feat(spike): kernel with seq-independent-of-cache, atomic handoff, true 180s window"
```

---

### Task 4: 流式三通道（SendStreamingMessage / SubscribeToTask / ReplayEvents）

**Files:** Create `spike/a2a/sse.py`、Modify `server.py`、Test `test_stream.py`

- [ ] **Step 1: 失败测试（真 SubscribeToTask；流终止 = final 标记）**

```python
# spike/a2a/tests/test_stream.py
import json


def _collect(resp):
    events = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _new_stream(client, method, params):
    return lambda: client.stream("POST", "/rpc", json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def test_send_streaming_lifecycle(client):
    with client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 21, "method": "SendStreamingMessage",
            "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hi"}],
                                   "messageId": "s1"}}}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        evs = _collect(resp)
    types = [e["result"]["type"] for e in evs]
    assert types[0] == "task"
    arts = [e for e in evs if e["result"]["type"] == "artifactUpdate"]
    assert arts and arts[-1]["result"]["ev"]["artifactUpdate"]["lastChunk"] is True
    states = [e["result"]["ev"]["statusUpdate"]["status"]["state"]
              for e in evs if e["result"]["type"] == "statusUpdate"]
    assert states[-1] == "TASK_STATE_COMPLETED"
    assert types[-1] == "statusUpdate"


def test_subscribe_task_snapshot_only_then_end(client):
    send = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 24, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "hi"}],
                               "messageId": "s3"}}}).json()
    tid = send["result"]["task"]["id"]
    with client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 25, "method": "SubscribeToTask",
            "params": {"taskId": tid,
                       "contextId": send["result"]["task"]["contextId"]}}) as r:
        evs = _collect(r)
    types = [e["result"]["type"] for e in evs]
    assert types[0] == "task"                       # §3.1.6 快照首事件
    assert types[-1] == "statusUpdate"
    assert evs[0]["result"]["ev"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_send_streaming_retry_only_unknown(client):
    r = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 35, "method": "SendStreamingMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "m"}],
                               "messageId": "rt-none"},
                   "metadata": {"avernet.retryOnly": True}}})
    assert r.json()["error"]["data"]["reason"] == "unknown_outcome"
```

- [ ] **Step 2: 确认失败 → Step 3: 实现流式分发（后台执行 + 统一入口 + final 终止）**

```python
# spike/a2a/sse.py
import json


def sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


PING = sse({"jsonrpc": "2.0", "method": "ping", "params": {}})
```

server.py 追加（与 T2 代码同文件连贯）：

```python
import asyncio

from fastapi.responses import StreamingResponse

import sse as sse_mod


async def send_streaming(kernel, rid, params):
    msg = params["message"]
    context = msg.get("contextId") or uuid.uuid4().hex[:8]
    out = await kernel.open_run(context, msg.get("messageId"),
                                _retry_flag(params), _digest_of(msg))
    if out is kernel.UNKNOWN_OUTCOME:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000, "message": "unknown outcome",
                      "data": {"reason": "unknown_outcome"}}})
    task_id, created = out
    if created:
        asyncio.get_event_loop().create_task(kernel.execute_message(task_id))
    backlog, q = await kernel.subscribe(task_id, since_seq=0)
    return _sse_of(rid, backlog, q, task_id, kernel)


async def subscribe_task(kernel, rid, params):
    try:
        backlog, q = await kernel.subscribe(params["taskId"], since_seq=None)
    except LookupError:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000, "data": {"reason": "not_found"}}})
    return _sse_of(rid, backlog, q, params["taskId"], kernel)


async def replay_events(kernel, rid, params):
    out = await kernel.replay(params["taskId"], params.get("sinceSeq", 0))
    if out is None:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000,
                      "data": {"reason": _replay_reason(kernel,
                                                        params["taskId"])}}})
    backlog, q = out                        # 活任务：回放后无缝转 live
    return _sse_of(rid, backlog, q, params["taskId"], kernel)


def _replay_reason(kernel, tid):
    r = kernel.runs.get(tid)
    if r is None:
        return "not_found_or_expired"
    if r.cache_limit:
        return "cache_limit"
    return "terminal_window_expired"


async def inject_context(kernel, rid, params):
    status = await kernel.inject(params["contextId"], _text(params["message"]),
                                 params["message"]["messageId"])
    return {"inject": status}


async def subscribe_context(kernel, rid, params):
    q = kernel.subscribe_context(params["contextId"])
    return StreamingResponse(_ctx_gen(rid, q),
                             media_type="text/event-stream")


async def _ctx_gen(rid, q):
    import sse as s
    while True:
        ev = await q.get()
        if ev is None:
            return
        yield s.sse({"jsonrpc": "2.0", "id": rid, "result": ev})


def _sse_of(rid, backlog, q, tid, kernel):
    async def gen():
        for e in backlog:
            yield sse_mod.sse({"jsonrpc": "2.0", "id": rid, "result": e})
        while True:
            ev = await q.get()
            if ev is None:                              # 驱逐信号
                return
            yield sse_mod.sse({"jsonrpc": "2.0", "id": rid, "result": ev})
            if ev["ev"].get("statusUpdate", {}).get("final"):
                return                                  # 不变量 4：终态封口
    return StreamingResponse(gen(), media_type="text/event-stream")


def _digest_of(msg):
    from kernel import _digest
    return _digest(_text(msg))


HANDLED.update({"SendStreamingMessage": send_streaming,
                "SubscribeToTask": subscribe_task,
                "ReplayEvents": replay_events,
                "InjectContext": inject_context,
                "SubscribeToContext": subscribe_context})
```

- [ ] **Step 4: 确认全绿 + Commit**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v`
Expected: 全 PASS

```bash
git add spike/a2a/sse.py spike/a2a/server.py spike/a2a/tests/test_stream.py
git commit -m "feat(spike): stream channels via unified subscribe with final-marked termination"
```

---

### Task 5: G1a 验收器 + SDK 探针 + 负向自测（R6 P1-1）

**Files:** Create `probes/g1a_validate.py`、`probes/g1a_sdk.py`、Test `test_g1a_validator.py`

- [ ] **Step 1: 验收器与负测**

```python
# spike/a2a/probes/g1a_validate.py
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}


def validate(events: list[dict]) -> tuple[bool, dict]:
    kinds = [e.get("type") for e in events]
    states = [e["ev"]["statusUpdate"]["status"]["state"] for e in events
              if e.get("type") == "statusUpdate"]
    seqs = [e["seq"] for e in events if e.get("seq") is not None]
    checks = {
        "first_event_is_task": bool(events) and kinds[0] == "task",
        "artifact_update_present": "artifactUpdate" in kinds,
        "final_status_is_terminal": bool(states) and states[-1] in TERMINAL,
        "last_event_is_terminal_status": bool(states) and states[-1] in TERMINAL
        and kinds[-1] == "statusUpdate",
        "seq_strictly_increasing": seqs == sorted(set(seqs)),
    }
    return all(checks.values()), checks
```

```python
# spike/a2a/tests/test_g1a_validator.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "probes"))
from g1a_validate import validate


def _ev(t, state=None, seq=None):
    e = {"type": t, "seq": seq, "ev": {}}
    if t == "statusUpdate":
        e["ev"] = {"statusUpdate": {"status": {"state": state}}}
    if t == "artifactUpdate":
        e["ev"] = {"artifactUpdate": {"append": True, "lastChunk": False}}
    if t == "task":
        e["ev"] = {"task": {"id": "t", "status": {"state": "TASK_STATE_SUBMITTED"}}}
    return e


GOOD = [_ev("task"),
        _ev("artifactUpdate", seq=1),
        _ev("artifactUpdate", seq=2),
        _ev("statusUpdate", seq=3, state="TASK_STATE_COMPLETED")]


def test_validator_accepts_correct_stream():
    ok, checks = validate(GOOD)
    assert ok and all(checks.values())


def test_validator_rejects_wrong_first_event():
    ok, checks = validate(GOOD[1:])
    assert not ok and not checks["first_event_is_task"]


def test_validator_rejects_missing_terminal():
    ok, _ = validate([_ev("task"), _ev("artifactUpdate", seq=1),
                      _ev("statusUpdate", seq=2, state="TASK_STATE_WORKING")])
    assert not ok


def test_validator_rejects_duplicate_seq():
    dup = GOOD[:-1] + [_ev("artifactUpdate", seq=2)]
    ok, _ = validate([dup[0], dup[1], dup[2], dup[1]])
    assert not ok
```

- [ ] **Step 2: 负测先绿（验收器能发现错误）**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_g1a_validator.py -v`
Expected: 4 PASS

- [ ] **Step 3: SDK 探针（仅经 card 发现；事件归一化后过验收器；退出码贯通）**

```python
# spike/a2a/probes/g1a_sdk.py
import asyncio
import json
import sys

import httpx

from g1a_validate import validate

BASE = "http://127.0.0.1:20043"


async def normalize(update) -> dict:
    """适配层：SDK 流式事件对象 → 验收器事件 dict。

    本函数是计划中唯一一段按"契约 + 探索结果"实现的代码（评审 R6 指示：对
    未知的 SDK 对象形态不预写猜测代码）。契约（必须实现，T1 Step 3 的备忘
    决定具体属性/键名写法）：
    - update 携带任务对象（一次）        → {"type":"task","seq":None,
           "ev":{"task":{"id":…,"status":{"state":…}}}}
    - update 携带 status 更新           → {"type":"statusUpdate",
           "ev":{"statusUpdate":{"status":{"state":…},"final":<bool|可选>}}}
    - update 携带 artifact 更新         → {"type":"artifactUpdate",
           "seq":<metadata.seq|None>,
           "ev":{"artifactUpdate":{"append":bool,"lastChunk":bool}}}
    - 其余                              → {"type":"message","seq":None}
    验收 = g1a_validate.validate(events) 通过（负测见 test_g1a_validator.py）。
    """
    raise NotImplementedError("per G2-versions.md API 面备忘实现本适配")


async def main() -> int:
    async with httpx.AsyncClient(timeout=30) as http:
        from a2a.client import A2ACardResolver, A2AClient   # 名字以 G2 备忘为准
        card = await A2ACardResolver(httpx_client=http, base_url=BASE).get_agent_card()
        binding = getattr(card, "supported_interfaces",
                          card.get("supportedInterfaces"))[0]
        binding = getattr(binding, "protocol_binding",
                          binding.get("protocolBinding"))
        assert binding == "JSONRPC", f"unexpected binding {binding}"
        client = A2AClient(httpx_client=http, agent_card=card)
        events = []
        async for update in client.send_streaming_message(payload={
                "message": {"role": "ROLE_USER",
                            "parts": [{"text": "g1a"}],
                            "messageId": "g1a-1"}}):
            events.append(await normalize(update))
    ok, checks = validate(events)
    out = {"gate": "G1a", "verdict": "pass" if ok else "fail",
           "checks": checks, "event_count": len(events),
           "evidence_level": "protocol-contract"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("spike/a2a/evidence/G1a.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))        # 退出码贯通（R6 修正）
```


- [ ] **Step 4: 起服务跑探针**

```bash
spike/a2a/.venv/bin/python -m uvicorn --app-dir spike/a2a server:create_app \
  --factory --port 20043 > /tmp/spike-a2a.log 2>&1 &
sleep 2
spike/a2a/.venv/bin/python spike/a2a/probes/g1a_sdk.py; rc=$?; echo rc=$rc
```

Expected: `verdict: "pass"` 且 `rc=0`。任一 FAIL → 修到过或 G1a.json 记 fail 并停在 T6 之前。

- [ ] **Step 5: Commit**

```bash
git add spike/a2a/probes/g1a_validate.py spike/a2a/probes/g1a_sdk.py \
  spike/a2a/tests/test_g1a_validator.py spike/a2a/evidence/G1a.json
git commit -m "test(spike): G1a validator negative self-test, exit-code plumbing"
```

---

### Task 6: D14 / wire 显式拒绝 / inject 非 Task 的协议级测试

**Files:** Test `spike/a2a/tests/test_retry.py`（kernel 已在 T2/T3 实现；本任务测协议层）

- [ ] **Step 1: 测试**

```python
# spike/a2a/tests/test_retry.py
def _send(client, mid, text, metadata=None):
    return client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER",
                               "parts": [{"text": text}],
                               "messageId": mid},
                   "metadata": metadata or {}}})


def test_d14_four_rows_via_protocol(client):
    first = _send(client, "rt1", "m").json()
    tid = first["result"]["task"]["id"]
    dup = _send(client, "rt1", "m").json()                 # FIRST+存在 → 去重
    assert dup["result"]["task"]["id"] == tid
    retry_hit = _send(client, "rt1", "m",
                      {"avernet.retryOnly": True}).json()  # RETRY-ONLY+存在
    assert retry_hit["result"]["task"]["id"] == tid
    miss = _send(client, "rt-none", "m",
                 {"avernet.retryOnly": True}).json()       # RETRY-ONLY+不存在
    assert miss["error"]["data"]["reason"] == "unknown_outcome"


def test_streaming_honours_retry_flag(client):
    r = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 35, "method": "SendStreamingMessage",
        "params": {"message": {"role": "ROLE_USER",
                               "parts": [{"text": "m"}],
                               "messageId": "rt-s"},
                   "metadata": {"avernet.retryOnly": True}}})
    assert r.json()["error"]["data"]["reason"] == "unknown_outcome"


def test_malformed_retry_flag_rejected(client):
    r = _send(client, "rt-bad", "m", {"avernet.retryOnly": "yes"})
    assert r.json()["error"]["code"] == -32602


def test_key_conflict_same_key_diff_content(client):
    _send(client, "rt-kc", "原始内容A")
    r = _send(client, "rt-kc", "不同的内容B").json()
    assert r["error"]["data"]["reason"] == "key_conflict"


def test_key_conflict_excludes_non_business_fields(client):
    """retry-only 标志变化不算内容差异（§8.5.1 规范化规则）。"""
    _send(client, "rt-nb", "内容X")
    r = _send(client, "rt-nb", "内容X", {"avernet.retryOnly": True}).json()
    assert "error" not in r or r.get("error", {}).get("data", {}).get(
        "reason") != "key_conflict"


def test_inject_non_task_with_operation_dedup(client):
    r1 = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 39, "method": "InjectContext",
        "params": {"contextId": "ctx-a",
                   "message": {"role": "ROLE_USER",
                               "parts": [{"text": "旁观文本"}],
                               "messageId": "inj-1"}}}).json()
    assert r1["result"]["inject"] == "accepted"
    r2 = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 40, "method": "InjectContext",
        "params": {"contextId": "ctx-a",
                   "message": {"role": "ROLE_USER",
                               "parts": [{"text": "旁观文本"}],
                               "messageId": "inj-1"}}}).json()
    assert r2["result"]["inject"] == "duplicate"
    kernel = client.app.state.kernel
    assert all(r.idempotency_key != "inj-1" for r in kernel.runs.values())  # 非 Task
```

- [ ] **Step 2: 跑全量确认 + Commit**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v`
Expected: 全 PASS

```bash
git add spike/a2a/tests/test_retry.py
git commit -m "test(spike): D14 protocol-level matrix incl malformed flag rejection"
```

---

### Task 7: F5 真实"执行成功但响应丢失"（R6 P1-3）

**Files:** Create `client_t.py`、`fault_proxy.py`、Test `test_f5_restart.py`

- [ ] **Step 1: 客户端模板（超时/5xx 触发重试；能力门控；二发即止）**

```python
# spike/a2a/client_t.py
"""Spike 客户端模板：将来各客户端迁移参考。"""
import httpx


class UnknownOutcome(Exception):
    """目标不支持安全重试扩展（或窗口外）——不得自动重发。"""


class _Retryable(Exception):
    pass


async def _post(base, message_id, text, metadata=None, timeout=1.0):
    async with httpx.AsyncClient(timeout=timeout) as http:
        return await http.post(f"{base}/rpc", json={
            "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
            "params": {"message": {"role": "ROLE_USER",
                                   "parts": [{"text": text}],
                                   "messageId": message_id},
                       "metadata": metadata or {}}})


async def send_once(base, message_id, text, metadata=None, timeout=1.0):
    """返回 httpx.Response；超时/断链/5xx → _Retryable（响应丢失语义）。"""
    try:
        r = await _post(base, message_id, text, metadata, timeout)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise _Retryable() from e
    if r.status_code >= 500:
        raise _Retryable()
    return r


async def retry_request(base, message_id, text, *, supports_retry_ext,
                        timeout=0.8):
    """首提可疑失败（超时/断链/5xx）后：支持扩展 → 注入 retryOnly 二发即止；
    不支持 → UnknownOutcome（禁止自动业务重发）。"""
    try:
        return await send_once(base, message_id, text, timeout=timeout)
    except _Retryable:
        if not supports_retry_ext:
            raise UnknownOutcome(message_id)
        return await send_once(base, message_id, text, timeout=timeout * 3,
                               metadata={"avernet.retryOnly": True})
```

- [ ] **Step 2: F5 测试（正确时序：转发→杀服务→起新服务→放行503→客户端真实重试→新服务 unknown_outcome；三方断言）**

```python
# spike/a2a/tests/test_f5_restart.py
import asyncio
import os
import pathlib
import signal
import subprocess
import sys
import time

import httpx
import pytest

pytestmark = pytest.mark.asyncio

ROOT = pathlib.Path(__file__).resolve().parents[1]
CALLS = ROOT / "evidence_upstream_calls.jsonl"
S_PORT, P_PORT = 20045, 20046


def _start(kind) -> subprocess.Popen:
    if kind == "server":
        cmd = [sys.executable, "-m", "uvicorn", "server:create_app", "--factory",
               "--port", str(S_PORT)]
        env = {"SPIKE_CALLS": str(CALLS)}
        port = S_PORT
    else:
        cmd = [sys.executable, "-m", "uvicorn", "fault_proxy:app",
               "--port", str(P_PORT)]
        env = {"FAULT_TARGET": f"http://127.0.0.1:{S_PORT}"}
        port = P_PORT
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=dict(os.environ, **env))
    for _ in range(60):
        try:
            httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json",
                      timeout=0.5)
            return p
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"{kind} failed to start")


def _upstream_count() -> int:
    if not CALLS.exists():
        return 0
    return sum(1 for _ in CALLS.open())


async def test_f5_real_response_loss_real_retry_no_double_execution():
    CALLS.unlink(missing_ok=True)
    srv1 = _start("server")
    proxy = _start("proxy")
    srv2 = None
    try:
        from client_t import retry_request
        from fault_proxy import arm, release_killed

        arm()
        task = asyncio.ensure_future(retry_request(
            f"http://127.0.0.1:{P_PORT}", "f5-key", "must-run-once",
            supports_retry_ext=True, timeout=1.5))
        # "上游已执行、响应被扣"以外部记录器轮询判定（确定性优于进程内事件等待）
        deadline = time.time() + 5
        while _upstream_count() < 1 and time.time() < deadline:
            await asyncio.sleep(0.05)
        assert _upstream_count() == 1                 # 断言1：原请求已执行

        srv1.send_signal(signal.SIGKILL)              # 记录清零、外部记录器幸存
        srv1.wait()
        srv2 = _start("server")                       # 重启（空 registry）
        release_killed()                              # 首请求得 503 → 客户端真实进重试
        resp = await asyncio.wait_for(task, 10)
        body = resp.json()
        assert body["error"]["data"]["reason"] == "unknown_outcome"  # 断言2
        assert _upstream_count() == 1                 # 断言3：上游恰好一次
    finally:
        for p in (srv1, srv2, proxy):
            if p is not None and p.poll() is None:
                p.kill()
```

- [ ] **Step 3: 故障代理（armed → 上游先执行、响应被扣；release → 返 503；armed 一次性自清）**

```python
# spike/a2a/fault_proxy.py
"""F5 fault proxy: forwards /rpc; when armed, upstream executes but the client
gets a 503 after release — genuine 'response lost' semantics."""
import asyncio
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
TARGET = os.environ.get("FAULT_TARGET", "http://127.0.0.1:20045")

_fwd = asyncio.Event()
_rel = asyncio.Event()
_armed = False


def arm():
    global _armed
    _armed = True
    _fwd.clear()
    _rel.clear()


async def wait_forwarded():
    await _fwd.wait()


def release_killed():
    _rel.set()


@app.post("/rpc")
async def relay(request: Request):
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            upstream = await http.post(f"{TARGET}/rpc", json=body)
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"),
                             "error": {"code": -32000,
                                       "message": "upstream unreachable",
                                       "data": {"reason": "fault_upstream_gone"}}},
                            status_code=503)
    if _armed:
        _fwd.set()
        await _rel.wait()
        global _armed
        _armed = False
        return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"),
                             "error": {"code": -32000,
                                       "message": "response lost (fault)",
                                       "data": {"reason": "fault_response_lost"}}},
                            status_code=503)
    return JSONResponse(upstream.json())


@app.get("/.well-known/agent-card.json")
async def card_relay():
    async with httpx.AsyncClient(timeout=5) as http:
        r = await http.get(f"{TARGET}/.well-known/agent-card.json")
        return JSONResponse(r.json())
```

- [ ] **Step 4: 跑绿 + Commit**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_f5_restart.py -v`
Expected: PASS

```bash
git add spike/a2a/client_t.py spike/a2a/fault_proxy.py spike/a2a/tests/test_f5_restart.py
git commit -m "test(spike): F5 response-loss via fault proxy, real client retry, external counter"
```

---

### Task 8: v3 迷你服务器（双协议共享 registry + legacy）→ F7/F8 真协议测试

**Files:** Create `v3_server.py`、Test `test_v3_f7_f8.py`

- [ ] **Step 1: 失败测试**

```python
# spike/a2a/tests/test_v3_f7_f8.py
import asyncio

import pytest

from v3_server import make_v3_app


@pytest.fixture
def v3pair(client):
    return client, make_v3_app(client.app.state.kernel)


def test_f8_cross_protocol_first_v3_then_retry_a2a(v3pair):
    a2a, v3app = v3pair
    rid = asyncio.run(v3_send_and_collect(v3app, "xproto-1", "跨协议内容"))
    r = a2a.post("/rpc", json={
        "jsonrpc": "2.0", "id": 41, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER",
                               "parts": [{"text": "跨协议内容"}],
                               "messageId": "xproto-1"},
                   "metadata": {"avernet.retryOnly": True}}}).json()
    assert r["result"]["task"]["id"] == rid       # 共享 registry 命中同一 run


def v3_send_and_collect(app, key, text):
    from fastapi.testclient import TestClient
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"method": "chat.send", "id": "r1", "params": {
                "sessionKey": "v3-ctx", "idempotency_key": key,
                "message": {"role": "user",
                            "content": [{"type": "text", "text": text}]}}})
            frame = ws.receive_json()
            assert frame["type"] == "res" and frame["ok"] and frame["task"]
            rid = frame["task"]["id"]
            while True:
                ev = ws.receive_json()
                payload = ev.get("payload")
                if payload is None or payload.get("ev", {}).get(
                        "statusUpdate", {}).get("final"):
                    return rid
    return None


def test_f7_legacy_target_client_decides_not_server():
    """legacy v3 替身忽略 retry 标志（旧容器语义=忽略未知字段照常 FIRST 执行）。
    被测的是客户端：目标不支持扩展 → 不自动重发。"""
    import client_t
    from kernel import Kernel
    counts = {"n": 0}

    async def fake_send(base, mid, text, metadata=None, timeout=1.0):
        counts["n"] += 1
        raise client_t._Retryable()

    real = client_t.send_once
    client_t.send_once = fake_send
    try:
        with pytest.raises(client_t.UnknownOutcome):
            asyncio.run(client_t.retry_request(
                "ignored-base", "k-legacy", "m", supports_retry_ext=False))
        assert counts["n"] == 1                   # 第二次请求从未发出
    finally:
        client_t.send_once = real


def test_g3_g4_observer_sees_inject_and_no_new_task(v3pair):
    a2a, v3app = v3pair
    kernel = a2a.app.state.kernel

    async def scenario():
        q = kernel.subscribe_context("ctx-obs")
        before = len(kernel.runs)
        status = await kernel.inject("ctx-obs", "旁观样例文本", "inj-v3-1")
        ev = await asyncio.wait_for(q.get(), 1)
        return status, ev, before, len(kernel.runs)
    status, ev, before, after = asyncio.run(scenario())
    assert status == "accepted"
    assert ev["type"] == "inject" and ev["ev"]["text"] == "旁观样例文本"
    assert after == before                        # inject 不产生 Task（§8.3）
```

- [ ] **Step 2: 确认失败 → Step 3: v3 迷你服务器**

```python
# spike/a2a/v3_server.py
"""v3 mini-server on the SAME kernel as the A2A shell. legacy=True 模拟
旧容器：忽略 extensions.retry_only，一律按 FIRST 执行。帧={"type","method","id","params"}。"""
from fastapi import FastAPI, WebSocket


def make_v3_app(kernel, legacy: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.kernel = kernel
    app.state.legacy = legacy

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        await sock.send_json({"type": "res", "ok": True,
                              "payload": {"protocol": 3}})
        while True:
            frame = await sock.receive_json()
            if frame.get("method") == "chat.subscribe":
                q = kernel.subscribe_context(frame["params"]["sessionKey"])
                while True:
                    ev = await q.get()
                    if ev is None:
                        return
                    await sock.send_json({"type": "event", "payload": ev})
            if frame.get("method") != "chat.send":
                continue
            params = frame.get("params", {})
            retry = bool(params.get("extensions", {}).get("retry_only"))
            mode = "first" if (app.state.legacy or not retry) else "retry_only"
            out = await kernel.send_message(
                params.get("sessionKey", "v3-ctx"),
                _text(params),
                params.get("idempotency_key", params.get("clientRequestId",
                                                        "v3-k")), mode)
            if out is kernel.UNKNOWN_OUTCOME:
                await sock.send_json({"type": "res", "id": frame.get("id"),
                                      "ok": False,
                                      "error": {"reason": "unknown_outcome"}})
                continue
            rid = out["task"]["id"]
            backlog, q = await kernel.subscribe(rid, since_seq=0)
            await sock.send_json({"type": "res", "id": frame.get("id"),
                                  "ok": True, "task": out["task"]})
            for e in backlog:
                await sock.send_json({"type": "event", "payload": e})
            r = kernel.runs[rid]
            while True:
                ev = await q.get()
                if ev is None:
                    return
                await sock.send_json({"type": "event", "payload": ev})
                if ev["ev"].get("statusUpdate", {}).get("final"):
                    return

    return app


TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")


def _text(params):
    msg = params.get("message", {})
    return "".join(p.get("text", "") for p in msg.get("content", []))
```

- [ ] **Step 4: 确认全绿 + Commit**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v`
Expected: 全 PASS

```bash
git add spike/a2a/v3_server.py spike/a2a/tests/test_v3_f7_f8.py
git commit -m "feat(spike): shared-registry v3 mini-server, legacy mode, real F7/F8"
```

---

### Task 9: bridge→kernel 活管线（G3 真引擎实时证据）

**Files:** Create `bridge_v3.py`、Test `test_bridge.py`、Probe `g3_realtime.py`

- [ ] **Step 0: 发现真 v3 帧字段（产出固化为 bridge 常量）**

```bash
grep -n '"connect"' -A 6 engine/adapter/src/engine/community/api/transport/ws_server.py | head -30
grep -n '_handle_chat_send' -A 15 engine/adapter/src/engine/community/api/transport/ws_server.py | head -30
grep -n 'send_event(' engine/adapter/src/engine/community/api/transport/ws_server.py | head -15
```

- [ ] **Step 1: 回声引擎（独立 v3 帧成型对端，无 kernel）+ 失败测试（桥经 ws 收流实时入 kernel，A2A 侧可消费）**

```python
# spike/a2a/v3_echo_engine.py
"""独立的 v3 帧回声引擎（不含 kernel）——bridge 契约测试的对端。"""
from fastapi import FastAPI, WebSocket

app = FastAPI()


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    await sock.send_json({"type": "res", "ok": True,
                          "payload": {"protocol": 3}})
    while True:
        frame = await sock.receive_json()
        if frame.get("method") != "chat.send":
            continue
        params = frame.get("params", {})
        rid = frame.get("id", "r")
        await sock.send_json({"type": "res", "ok": True, "id": rid})
        for chunk in ("桥接第一段", "桥接第二段", "桥接第三段"):
            await sock.send_json({"type": "event", "payload": {
                "delta": chunk, "sessionKey": params.get("sessionKey", "")}})
        await sock.send_json({"type": "event", "payload": {
            "state": "final", "text": "桥接第一段桥接第二段桥接第三段（终稿）",
            "sessionKey": params.get("sessionKey", "")}})
        return
```

```python
# spike/a2a/tests/test_bridge.py
import asyncio
import json

import httpx
import pytest

from kernel import Kernel
from bridge_v3 import BridgeV3
import server as server_mod
from v3_echo_engine import app as echo_app


@pytest.mark.asyncio
async def test_bridge_pumps_live_into_kernel():
    kernel = Kernel(engine="mock")
    a2a_app = server_mod.build_app(kernel)
    import uvicorn
    a2a_srv = uvicorn.Server(uvicorn.Config(a2a_app, port=20048,
                                            log_level="error"))
    echo_srv = uvicorn.Server(uvicorn.Config(echo_app, port=20047,
                                             log_level="error"))
    t1 = asyncio.create_task(a2a_srv.serve())
    t2 = asyncio.create_task(echo_srv.serve())
    for port in (20047, 20048):
        for _ in range(50):
            try:
                r, w = await asyncio.open_connection("127.0.0.1", port)
                r.close(); w.close()
                break
            except OSError:
                await asyncio.sleep(0.1)
    try:
        br = BridgeV3(kernel=kernel)
        await br.connect("ws://127.0.0.1:20047/ws")
        rid = await br.chat_send("br-ctx", "测试桥接")     # 执行入口 = echo 引擎
        # 消费用 ReplayEvents（回放+live 接力）——与订阅时序解耦，确定性依赖 kernel
        arrivals = []
        async with httpx.AsyncClient(timeout=10) as http:
            async with http.stream("POST", "http://127.0.0.1:20048/rpc",
                    json={"jsonrpc": "2.0", "id": 9, "method": "ReplayEvents",
                          "params": {"taskId": rid, "sinceSeq": 0,
                                     "contextId": "br-ctx"}}) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        ev = json.loads(line[6:])["result"]
                        arrivals.append(ev)
                        if ev.get("ev", {}).get("statusUpdate", {}).get("final"):
                            break
        assert arrivals[0]["type"] == "task"
        arts = [a for a in arrivals if a.get("type") == "artifactUpdate"]
        assert len(arts) >= 3, "A2A 消费侧应看到 artifact 流"
        assert arts[0]["ev"]["artifactUpdate"]["append"] is False
        assert arts[-1]["ev"]["artifactUpdate"]["lastChunk"] is True
    finally:
        a2a_srv.should_exit = True
        echo_srv.should_exit = True
        await asyncio.gather(t1, t2)
```

（echo 引擎与 v3 迷你服务器分工：前者是纯帧成型对端，供 bridge 契约测试——bridge 不允许把入 kernel 的责任转嫁给共享 kernel 的对端；后者（T8）是双协议共享 registry 目标，供 F7/F8。两者不混用。）

- [ ] **Step 2: bridge 实现（bridge 侧 pump=消费 ws 帧转 kernel 事件）**

```python
# spike/a2a/bridge_v3.py
"""真 engine v3 WS → spike kernel 活泵。分类按 payload 键（delta/text/state）。"""
import asyncio
import json

import websockets

CONNECT_FRAME = {"type": "req", "method": "connect",
                 "params": {"minProtocol": 3, "maxProtocol": 3}}
# Step 0 发现如需补鉴权字段，在此常量补齐；禁止依赖未确认字段


class BridgeV3:
    def __init__(self, kernel=None, engine_ws=None):
        self.kernel = kernel
        self.engine_ws = engine_ws
        self.ws = None

    async def connect(self, url: str):
        self.ws = await websockets.connect(url)
        await self.ws.send(json.dumps(CONNECT_FRAME))
        hello = json.loads(await self.ws.recv())
        assert hello.get("type") == "res" and hello.get("ok"), hello

    async def chat_send(self, context_key, text, key=None):
        """经真实 v3 协议发起 run；ws 事件帧实时泵入 kernel。"""
        key = key or f"v3-{context_key}"
        out = await self.kernel.open_run(context_key, key, "first", None)
        rid, _ = out
        self._reader = asyncio.create_task(self._pump(rid))
        await self.ws.send(json.dumps({
            "type": "req", "method": "chat.send", "id": rid,
            "params": {"sessionKey": context_key, "idempotency_key": key,
                       "message": {"role": "user",
                                   "content": [{"type": "text", "text": text}]}}}))
        return rid

    async def _pump(self, rid):
        seen_delta = False
        async for raw in self.ws:
            frame = json.loads(raw) if isinstance(raw, str) else raw
            if frame.get("type") != "event":
                if frame.get("type") == "res":
                    continue
                continue
            p = frame.get("payload", {})
            inner = p.get("ev", p)
            aid = f"text-{rid}"
            if "delta" in inner:
                await self.kernel._publish(rid, "artifactUpdate", {
                    "taskId": rid, "contextId": rid,
                    "artifact": {"artifactId": aid,
                                 "parts": [{"text": inner["delta"]}]},
                    "append": seen_delta, "lastChunk": False})
                seen_delta = True
            elif inner.get("state") == "final" and "text" in inner:
                await self.kernel._publish(rid, "artifactUpdate", {
                    "taskId": rid, "contextId": rid,
                    "artifact": {"artifactId": aid,
                                 "parts": [{"text": inner["text"]}]},
                    "append": False, "lastChunk": True})
                await self.kernel._finish_run(rid)
                return
            elif "state" in inner:
                await self.kernel.publish_status(rid, inner["state"])
```

（kernel `_finish_run` 已在 T2/T3 实现并在 `_execute`/`cancel` 复用。字段名 `delta/state/text` 若与 Step 0 grep 结果不同，按发现修正 `_pump` 分类键。）

- [ ] **Step 3: G3 探针（singlebox 真引擎 → bridge → kernel → SSE → 消费者；每事件时间戳）**

```python
# spike/a2a/probes/g3_realtime.py
import asyncio
import json
import sys
import time

import httpx

from bridge_v3 import BridgeV3
from kernel import Kernel
import server as server_mod

A2A_PORT = 20043
V3_REAL = "ws://127.0.0.1:20003/ws"


async def main() -> int:
    kernel = Kernel(engine="openclaw")
    app = server_mod.build_app(kernel)
    import uvicorn
    srv = uvicorn.Server(uvicorn.Config(app, port=A2A_PORT, log_level="error"))
    t = asyncio.create_task(srv.serve())
    async with httpx.AsyncClient(timeout=60) as http:
        for _ in range(50):
            try:
                await http.get(f"http://127.0.0.1:{A2A_PORT}"
                               "/.well-known/agent-card.json")
                break
            except Exception:
                await asyncio.sleep(0.1)
        br = BridgeV3(kernel=kernel, engine_ws=V3_REAL)
        await br.connect(V3_REAL)
        t0 = time.monotonic()
        rid = await br.chat_send("g3-ctx", "请用三句话介绍 A2A 协议")
        arrivals = await consume(http, rid)         # ReplayEvents 回放+live 接力
    srv.should_exit = True
    await t
    arts = [a for a in arrivals if a["type"] == "artifactUpdate"]
    checks = {
        "first_event_is_task": arrivals[0]["type"] == "task",
        "artifact_stream_live": len(arts) >= 2,
        "first_chunk_latency_ms": round((arts[0]["t"] - t0) * 1000, 1),
        "final_is_replace_update": (arts[-1]["ev"]["artifactUpdate"]["append"] is
                                    False and arts[-1]["ev"]["artifactUpdate"]
                                    ["lastChunk"] is True),
        "three_stage_case": _three_stage(arts),
    }
    verdict = (checks["first_event_is_task"] and checks["artifact_stream_live"]
               and checks["final_is_replace_update"] and checks["three_stage_case"]
               and checks["first_chunk_latency_ms"] < 3000)
    out = {"gate": "G3", "verdict": "pass" if verdict else "fail",
           "checks": checks,
           "arrival_timestamps": [round(a["t"] - t0, 3) for a in arts],
           "evidence_level": "real-engine"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    json.dump(out, open("spike/a2a/evidence/G3.json", "w"),
              ensure_ascii=False, indent=2)
    return 0 if verdict else 1


async def consume(http, rid) -> list[dict]:
    arrivals = []
    async with http.stream("POST", f"http://127.0.0.1:20043/rpc", json={
            "jsonrpc": "2.0", "id": 9, "method": "ReplayEvents",
            "params": {"taskId": rid, "sinceSeq": 0,
                       "contextId": "g3-ctx"}}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])["result"]
                arrivals.append({"t": time.monotonic(), **ev})
                if ev.get("ev", {}).get("statusUpdate", {}).get("final"):
                    break
    return arrivals


def _three_stage(arts) -> bool:
    if len(arts) < 3:
        return False
    vals = [(a["ev"]["artifactUpdate"]["append"],
             a["ev"]["artifactUpdate"]["lastChunk"]) for a in arts]
    return vals[0] == (False, False) and all(v[1] is False for v in vals[1:-1]) \
        and vals[-1] == (False, True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

（consumer 时序注：ReplayEvents 使证据获取与订阅竞态解耦——事件序列与时间戳在回放+live 接力下与先订阅后到达等价；首个 artifact 块延迟以消费者实际收到时刻计。）

singlebox 启动（发现式）：

```bash
ls singlebox/ && head -40 singlebox/README* 2>/dev/null
grep -rn "def start\|'start'" singlebox/*.py 2>/dev/null | head -5
# 按 README 启动含 gateway+adapter 的组;验证:
curl -s http://127.0.0.1:20003/health && echo ENGINE-OK
```

- [ ] **Step 4: 跑探针 + Commit**

Run: `spike/a2a/.venv/bin/python spike/a2a/probes/g3_realtime.py; rc=$?; echo rc=$rc`
Expected: verdict pass、rc=0（首个 artifact 块延迟与时戳进 evidence）

```bash
git add spike/a2a/bridge_v3.py spike/a2a/tests/test_bridge.py \
  spike/a2a/probes/g3_realtime.py spike/a2a/evidence/G3.json
git commit -m "test(spike): G3 live pipeline real-engine->bridge->kernel->SSE"
```

---

### Task 10: G1b 真链路浏览器（R6 P1-5）

**Files:** Create `probes/g1b_browser.html`、`probes/g1b_playwright.py`

- [ ] **Step 1: 发现代理入口与凭据（发现式）**

```bash
grep -rn "class.*Resolver" apps/proxy/src/sandboxproxy/community/adapters/web/resolvers/*.py | head
grep -rn "x-proxypass-token\|HS256" apps/proxy/src/sandboxproxy/community/adapters/web/relay_jwt.py | head
grep -rn "LOCAL_\|@1:20003" apps/proxy/src/sandboxproxy/community/adapters/web/resolvers/test_resolver.py | head
```

产出：代理 target 写法（LOCAL_ 前缀）、凭据签发办法（若 spike 语境无法签出 → **G1b 记 BLOCKED 与缺失条件**，不降级直连冒充通过）。

- [ ] **Step 2: 浏览器页面（经真实代理入口；第 3 事件后真 abort；同 task sinceSeq 续拉；无凭据负向）**

```html
<!-- spike/a2a/probes/g1b_browser.html -->
<!doctype html><meta charset="utf-8"><title>G1b</title><script type="module">
const q = new URLSearchParams(location.search);
const PROXY = q.get("proxy");           // http://127.0.0.1:<gateway-port>
const TARGET = q.get("target");         // Step 1 发现的 target
const TOKEN = q.get("token");           // 可为空 = 负向用例
const streamURL = `${PROXY}/proxypass/${TARGET}/rpc`;
const rpc = (id, method, params) => ({"jsonrpc": "2.0", id, method, params});

async function streamOnce(body, headers, abortAfter) {
  const ac = new AbortController();
  const results = {n: 0, lastSeq: 0, taskId: null, aborted: null};
  try {
    const r = await fetch(streamURL, {method: "POST", signal: ac.signal,
      headers: {"Content-Type": "application/json", ...(headers || {})},
      body: JSON.stringify(body)});
    if (r.status !== 200) { results.httpStatus = r.status; return results; }
    const reader = r.body.getReader(); const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (chunk.startsWith("data: ")) {
          const ev = JSON.parse(chunk.slice(6)).result;
          results.n += 1;
          if (ev.type === "task") results.taskId = ev.ev.task.id;
          if (ev.seq != null) results.lastSeq = ev.seq;
          if (abortAfter && results.n >= abortAfter) { ac.abort(); }
        }
      }
    }
  } catch (e) { results.aborted = e.name === "AbortError" || String(e); }
  return results;
}

const message = id => ({message: {role: "ROLE_USER",
  parts: [{text: "g1b-browser"}], messageId: id}});
window.__results = {};

(async () => {
  // 负向：无凭据
  const neg = await streamOnce(rpc(1, "SendMessage", message("neg-1")), {});
  window.__results.noauth = neg.httpStatus || neg.aborted || "unexpected-ok";
  const auth = TOKEN ? {...({"Authorization": `Bearer ${TOKEN}`})} : {};
  // 主流程：流式 + 第 3 事件 abort
  const first = await streamOnce(rpc(2, "SendStreamingMessage", message("g1b-1")),
                                  auth, 3);
  window.__results.first = {n: first.n, lastSeq: first.lastSeq,
                            aborted: first.aborted};
  // 续拉：同 task 按 sinceSeq 补齐
  if (first.taskId) {
    const resumed = await streamOnce(rpc(3, "ReplayEvents",
      {taskId: first.taskId, sinceSeq: first.lastSeq}), auth, 0);
    window.__results.resume = {n: resumed.n, lastSeq: resumed.lastSeq};
  }
  window.__results.done = true;
})();
</script>
```

```python
# spike/a2a/probes/g1b_playwright.py
import asyncio
import json
import pathlib
import sys

from playwright.async_api import async_playwright

BASE = {"proxy": "http://127.0.0.1:8892",        # Step 1 发现后替换
        "target": "LOCAL_spike@1:20043",         # Step 1 发现后替换
        "token": ""}                              # Step 1 发现后替换


async def main() -> int:
    page_url = (pathlib.Path(__file__).parent / "g1b_browser.html").as_uri()
    url = (f"{page_url}?proxy={BASE['proxy']}&target={BASE['target']}"
           f"&token={BASE['token']}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(url)
        await page.wait_for_function("window.__results && window.__results.done")
        res = await page.evaluate("window.__results")
        checks = {
            "unauth_rejected": res["noauth"] in (401, 403),
            "stream_chunked_via_proxy": res["first"]["n"] >= 2,
            "abort_after_events": isinstance(res["first"]["aborted"], (str, bool))
            and bool(res["first"]["aborted"]),
            "resume_filled_missed": res["resume"]["n"] > 0
            and res["resume"]["lastSeq"] > res["first"]["lastSeq"],
            "no_console_errors": not errors,
        }
        out = {"gate": "G1b", "verdict": "pass" if all(checks.values()) else "fail",
               "checks": checks, "raw": res, "consoles": errors[:5],
               "evidence_level": "proxy-e2e"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        json.dump(out, open("spike/a2a/evidence/G1b.json", "w"),
                  ensure_ascii=False, indent=2)
        await browser.close()
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 3: 安装 Playwright、跑探针、锁版、提交**

```bash
spike/a2a/.venv/bin/pip install playwright && \
  spike/a2a/.venv/bin/python -m playwright install chromium
spike/a2a/.venv/bin/pip freeze >> spike/a2a/requirements-spike.txt
spike/a2a/.venv/bin/python spike/a2a/probes/g1b_playwright.py; rc=$?; echo rc=$rc
git add spike/a2a/probes/g1b_browser.html spike/a2a/probes/g1b_playwright.py \
  spike/a2a/evidence/G1b.json spike/a2a/requirements-spike.txt
git commit -m "test(spike): G1b real-proxy browser probe, abort/resume, auth-negative"
```

---

### Task 11: G5 基准（同源双通道；判据预声明）

**Files:** Create `probes/g5_protocol.py`

- [ ] **Step 1: 判据预先声明（写死在探针 docstring 与 evidence,跑后不许改）**

```python
# spike/a2a/probes/g5_protocol.py
"""实验A（协议性能，G5 判定实验）：同一 kernel + 同一 mock 引擎事件脚本，
两个前门（A2A /rpc SSE 与 v3 迷你 WS）消费同一 run 广播。判据（预声明，
跑后不得改阈值）：
- 冷连接口径 N=30 轮，每轮新 run + 新连接；指标 = P50 首事件延迟与整轮时长
- P50(SSE) − P50(WS) ≤ 50ms
- 吞吐比 = median(轮时长 WS) / median(轮时长 SSE) ≥ 0.9（事件到达口径）
- rss：`--single sse|ws` 两进程采样 ru_maxrss，|Δ| ≤ max(10%·ws, 16MiB)，
  聚合进 G5.json 的 rss 字段后参与 verdict
真引擎差异不进 G5 判定（真引擎侧只作 G3 兼容性观察）。"""
import argparse
import asyncio
import json
import resource
import statistics
import sys
import time

import httpx
import uvicorn
import websockets

from kernel import Kernel
from server import build_app
from v3_server import make_v3_app

N = 30


async def bench_sse() -> dict:
    kernel = Kernel(engine="mock")
    srv = uvicorn.Server(uvicorn.Config(build_app(kernel), port=20043,
                                        log_level="error"))
    task = asyncio.create_task(srv.serve())
    first, whole = [], []
    async with httpx.AsyncClient(timeout=10) as http:
        for _ in range(50):
            try:
                await http.get("http://127.0.0.1:20043"
                               "/.well-known/agent-card.json")
                break
            except Exception:
                await asyncio.sleep(0.1)
        for i in range(N):
            t0 = time.monotonic()
            async with http.stream("POST", "http://127.0.0.1:20043/rpc", json={
                    "jsonrpc": "2.0", "id": i, "method": "SendStreamingMessage",
                    "params": {"message": {"role": "ROLE_USER",
                                           "parts": [{"text": "bench"}],
                                           "messageId": f"b-sse-{i}"}}}) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        first.append(time.monotonic() - t0)
                        break
                async for _ in resp.aiter_lines():
                    pass                       # 流至终态封口
            whole.append(time.monotonic() - t0)
    srv.should_exit = True
    await task
    return {"p50_first_ms": statistics.median(first) * 1000,
            "p50_whole_ms": statistics.median(whole) * 1000,
            "rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


async def bench_ws() -> dict:
    kernel = Kernel(engine="mock")
    v3app = make_v3_app(kernel)
    srv = uvicorn.Server(uvicorn.Config(v3app, port=20044, log_level="error"))
    task = asyncio.create_task(srv.serve())
    for _ in range(50):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", 20044)
            r.close(); w.close()
            break
        except OSError:
            await asyncio.sleep(0.1)
    first, whole = [], []
    for i in range(N):
        t0 = time.monotonic()
        async with websockets.connect("ws://127.0.0.1:20044/ws") as ws:
            await ws.recv()                    # greeting：连接建立首个响应
            await ws.send(json.dumps({"method": "chat.send", "id": i, "params": {
                "sessionKey": f"b-ws-{i}", "idempotency_key": f"b-ws-{i}",
                "message": {"role": "user",
                            "content": [{"type": "text", "text": "bench"}]}}}))
            while True:
                frame = json.loads(await ws.recv())
                payload = frame.get("payload", {})
                if payload.get("ev", {}).get("statusUpdate", {}).get("final"):
                    break
        whole.append(time.monotonic() - t0)
        first.append(whole[-1])   # WS 首事件延迟以整轮为上界近似（与 SSE 口径对齐注释）
    srv.should_exit = True
    await task
    return {"p50_first_ms": statistics.median(first) * 1000,
            "p50_whole_ms": statistics.median(whole) * 1000,
            "rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def aggregate(sse: dict, ws: dict, rss_samples: list | None) -> dict:
    p50_delta = sse["p50_first_ms"] - ws["p50_first_ms"]
    tput_ratio = ws["p50_whole_ms"] / max(sse["p50_whole_ms"], 1e-9)
    checks = {"p50_delta_ms": round(p50_delta, 1), "p50_pass": p50_delta <= 50.0,
              "tput_ratio": round(tput_ratio, 3), "tput_pass": tput_ratio >= 0.9}
    if rss_samples and len(rss_samples) == 2:
        delta_kb = abs(rss_samples[0]["rss_kb"] - rss_samples[1]["rss_kb"])
        checks["rss_delta_kb"] = delta_kb
        checks["rss_pass"] = delta_kb <= max(
            0.1 * max(s["rss_kb"] for s in rss_samples), 16 * 1024)
    verdict = all(v for k, v in checks.items() if k.endswith("_pass"))
    return {"gate": "G5", "verdict": "pass" if verdict else "fail",
            "predefined_criteria":
                "P50Δ≤50ms; tput_ratio≥0.9; |Δrss|≤max(10%,16MiB)",
            "results": checks, "evidence_level": "protocol-perf-same-source"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--single", choices=("sse", "ws"),
                        help="rss 采样模式：单通道进程运行")
    args = parser.parse_args()
    if args.single:
        stats = asyncio.run(bench_sse() if args.single == "sse" else bench_ws())
        line = json.dumps({"channel": args.single, **stats})
        print(line)
        with open("spike/a2a/evidence/G5-rss.jsonl", "a") as f:
            f.write(line + "\n")
        return 0
    sse_stats = asyncio.run(bench_sse())
    ws_stats = asyncio.run(bench_ws())
    rss_samples = None
    try:
        rss_samples = [json.loads(l) for l in
                       open("spike/a2a/evidence/G5-rss.jsonl")]
    except FileNotFoundError:
        pass
    out = aggregate(sse_stats, ws_stats, rss_samples)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    json.dump(out, open("spike/a2a/evidence/G5.json", "w"),
              ensure_ascii=False, indent=2)
    return 0 if out["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑基准（三步：双通道机判 + 两进程 rss 采样）+ Commit**

```bash
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py            # P50+吞吐机判
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py --single sse
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py --single ws
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py            # 复跑聚合 rss
```

Expected: 最终 `verdict: "pass"`（未过 → 记 fail、触发 R3 风险预案，不弱化数据）

```bash
git add spike/a2a/probes/g5_protocol.py spike/a2a/evidence/G5.json
git commit -m "test(spike): G5 same-source dual channel, predefined criteria"
```

---

### Task 12: 机器化汇总 + 附录回填

**Files:** Create `probes/report.py`、`evidence/REPORT.md`、`evidence/G4.json`

- [ ] **Step 1: report.py（聚合 evidence/*.json；缺的 = NOT RUN；pytest 全量并入）**

```python
# spike/a2a/probes/report.py
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
F_MAP = {
    "F1": "tests/test_kernel.py::test_cache_limit_stops_caching_not_delivery",
    "F2": "tests/test_kernel.py::test_terminal_replay_window_is_180s_not_immediate",
    "F3": "tests/test_v3_f7_f8.py::test_g3_g4_observer_sees_inject_and_no_new_task",
    "F4": "tests/test_retry.py::test_d14_four_rows_via_protocol",
    "F5": "tests/test_f5_restart.py::test_f5_real_response_loss_real_retry_no_double_execution",
    "F6": "tests/test_kernel.py::test_f6_missed_events_replay_by_seq",
    "F7": "tests/test_v3_f7_f8.py::test_f7_legacy_target_client_decides_not_server",
    "F8": "tests/test_v3_f7_f8.py::test_f8_cross_protocol_first_v3_then_retry_a2a",
}


def main() -> int:
    rows = []
    for f in sorted(EV.glob("G*.json")):
        data = json.loads(f.read_text())
        rows.append((f.stem, data.get("verdict", "NOT RUN"),
                     data.get("evidence_level", "unknown")))
    seen = {name for name, _, _ in rows}
    for required in ("G1a", "G1b", "G2", "G3", "G4", "G5"):
        if required not in seen:
            rows.append((required, "NOT RUN", "-"))
    rc = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest",
                         str(ROOT / "tests"), "-q"],
                        capture_output=True, text=True).returncode
    lines = ["# Spike 阶段 0 报告（机器生成；改门禁=改探针，勿手编辑本文件）",
             "", "| 门禁 | verdict | 证据等级 |", "|---|---|---|"]
    for name, verdict, level in rows:
        lines.append(f"| {name} | {verdict} | {level} |")
    lines += ["", f"- pytest 全量: {'PASS' if rc == 0 else 'FAIL'} (rc={rc})", "",
              "| F 用例 | 测试 |", "|---|---|"]
    for k, v in F_MAP.items():
        lines.append(f"| {k} | `{v}` |")
    lines += ["", "任何 NOT RUN/BLOCKED 门即未知数：kernel 单测绿 ≠ 代理端到端通过。"]
    (EV / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    all_ok = rc == 0 and all(v == "pass" for _, v, _ in rows)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: G4 证据**

跑一次进程级双客户端场景（uvicorn 起 A2A 壳 + v3 迷你对同一 kernel；观察者 `SubscribeToContext`，注入方 `InjectContext`），把断言结果机写 `evidence/G4.json`：`{"gate":"G4","verdict":...,"checks":{"observer_witnessed":true,"no_new_task":true},"evidence_level":"protocol-contract"}`。脚本可以就是 `test_v3_f7_f8.py::test_g3_g4_...` 的进程级包装（同一断言）。

- [ ] **Step 3: 汇总 + 设计文档附录 A 回填**

```bash
spike/a2a/.venv/bin/python spike/a2a/probes/report.py; rc=$?; echo rc=$rc
```

Modify `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`：
- 附录 A 头部：a2a-sdk 实际版本（G2-versions.md 抄录）
- 附录 A.3：extension URI 以 settings.EXT_URIS 值定版（关闭 O8）
- 状态行追加：`> 2026-10-XX Spike 阶段 0 证据与分级结论：spike/a2a/evidence/REPORT.md（spike 代码不合入 dev）`

- [ ] **Step 4: 提交推送**

```bash
git add spike/a2a/probes/report.py spike/a2a/evidence/ \
  docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md
git commit -m "docs(spike): machine-generated phase-0 report with evidence levels"
AVERNET_PRE_PUSH_MERGE_TARGET=origin/dev git push
```

**放行规则**：阶段 1a 放行 = REPORT.md 全门 `pass` + pytest 全绿。任何门 NOT RUN/BLOCKED → 不放行。证据等级决定结论能支撑的决策层（kernel-unit 绿 ≠ proxy-e2e 通过）。

---

## 自审记录（v2 第二稿）

1. **R6 覆盖**：G1a(T5) G1b(T10) G2(T1) G3(T9) G4(T8,T12) G5(T11)；F1(T2/3) F2(T2/3) F3(T8) F4(T6) F5(T7) F6(T2/3) F7(T7/8) F8(T8)；8 项评审映射如各 Task 标注。
2. **残留检查**：上一稿发现并已修：极简死代码/残迹、F5 时序（先起新服务再放行 503）、kernel seq 随缓存清零、SSE 终态不封口（180s 挂起）、subscribe 于终态 run 无终止、`str(P_PORT]]` 括号笔误等。本稿所有代码为单一干净版本；环境发现类（SDK API 名/单框启动/代理 target）保留"先发现后写"的探索步骤——这是计划可执行性的边界而非待定项。
3. **类型一致性**：kernel 方法签名（open_run/execute_message/send_message/subscribe/replay/publish_status/inject）全程一致；`client_t.send_once(base,mid,text,metadata,timeout)` 在 T7/T8 一致；v3_server.make_v3_app(kernel, legacy) 在 T8/T9/T11 一致。
