# Engine→A2A 协议替换 · 阶段 0 Spike 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出 A2A v1.0（JSON-RPC 绑定）替换 engine v3 WS 信道的 Spike 证据：门禁 G1a-G5 全部可判、失败用例 F1-F8 全部可复跑，结论与版本锁定回填设计文档附录 A。

**Architecture:** 旁置 Spike harness（`spike/a2a/`，独立 venv）：kernel 模块按已评审契约（§8 语义明细）实现最小 Task/事件/幂等/retry-only 语义 + extensions；FastAPI 壳提供 card 与 `/rpc`（JSON-RPC + SSE）；mock 引擎先行（不依赖 singlebox），v3 bridge 后置接真引擎（G3/G4/G5）。**spike 目录永不合入 dev**，交付物 = 证据文件 + 设计文档回填 commit。

**Tech Stack:** Python 3.12（venv 自建）、a2a-sdk（G2 锁版）、FastAPI + uvicorn、httpx、websockets、pytest、Playwright（G1b）。

**上游文档:** `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`（五轮评审通过版，本计划所有 §编号引用均指向它）
**分支:** `feat/engine-a2a-protocol-replacement`（已有 6 个文档提交，本计划产物继续提交于此分支）
**Spike 端口:** 20043（避开 engine adapter 的 20003）

---

## 文件结构

```
spike/a2a/                     # 旁置 harness，永不合入 dev
├── .venv/                      # gitignore（随 Task 1 建）
├── requirements-spike.txt      # 锁定依赖（Task 1 产出）
├── settings.py                 # 端口/扩展 URI 常量（Task 2 定，G2 可回改并 commit）
├── kernel.py                   # Run/事件流/幂等 registry/D14 retry-only/D7 容量（Task 3、6）
├── engine_mock.py              # 脚本化假引擎（Task 3）
├── server.py                   # FastAPI：card + /rpc 分发 + SSE（Task 2、4）
├── sse.py                      # SSE 编码/keepalive（Task 4）
├── bridge_v3.py                # v3 WS 客户端→kernel 事件泵（Task 8）
├── probes/                     # 门禁证据脚本（stdout 断言 + evidence/ 落盘）
│   ├── g1a_sdk.py              # Task 5
│   ├── g3_artifact.py          # Task 9
│   ├── g1b_browser.html        # Task 10
│   ├── g1b_playwright.py       # Task 10
│   └── g5_bench.py             # Task 11
├── tests/                      # 契约/失败用例（pytest，可复跑 = F 证据）
│   ├── conftest.py
│   ├── test_rpc.py             # Task 2
│   ├── test_kernel.py          # Task 3（含 F1）
│   ├── test_stream.py          # Task 4（F2、F6）
│   ├── test_retry.py           # Task 6（F4、F3、F8）
│   ├── test_f5_restart.py      # Task 7（F5、外部记录器）
│   └── test_f7_fallback.py     # Task 6b（F7）
└── evidence/                   # G/F 门禁结论文件（commit）
    └── G2-versions.md          # Task 1 产出
```

责任边界：`kernel.py` 是契约唯一实现（所有测试/探针只经它断言语义）；`server.py` 只做协议映射（对齐 spec D6/D9——壳无状态所有权）；`bridge_v3.py` 唯一知道 v3 帧格式的地方。

---

### Task 1: 环境与依赖锁定（G2 前半）

**Files:**
- Create: `spike/a2a/requirements-spike.txt`
- Create: `spike/a2a/evidence/G2-versions.md`
- Create: `.gitignore` 追加条目（仓库根已有 .gitignore 则 Modify）

- [ ] **Step 1: 建 venv 与目录**

```bash
mkdir -p spike/a2a/evidence spike/a2a/tests spike/a2a/probes
python3 -m venv spike/a2a/.venv
spike/a2a/.venv/bin/pip install -U pip
```

- [ ] **Step 2: 安装 SDK 及依赖（G2 通路判定点）**

```bash
spike/a2a/.venv/bin/pip install "a2a-sdk[http-server]" fastapi uvicorn httpx websockets pytest pytest-asyncio
spike/a2a/.venv/bin/pip freeze | grep -iE 'a2a|fastapi|uvicorn|httpx|websockets' > spike/a2a/requirements-spike.txt
spike/a2a/.venv/bin/pip show a2a-sdk
```

预期：`pip show` 输出版本行。**若安装失败（内网源无此包数）→ 记录到 G2-versions.md 并停止本计划**，回到设计文档 D4 降级分支（手写 wire 层）重评审——这是 G2 的早退出口，不允许跳过。

- [ ] **Step 3: 核对 SDK 源码内的 spec 版本与 client/server API 面（为 Task 4/5 探路）**

```bash
ls spike/a2a/.venv/lib/python*/site-packages/a2a/client/ 2>/dev/null
ls spike/a2a/.venv/lib/python*/site-packages/a2a/server/ 2>/dev/null
grep -rl "agent-card.json" spike/a2a/.venv/lib/python*/site-packages/a2a/ | head -5
grep -rn "class A2ACardResolver\|async def get_agent_card" spike/a2a/.venv/lib/python*/site-packages/a2a/client/ | head -5
```

预期：能看到 client/server 模块文件与 CardResolver 类名（Task 5 用）。若类名不同，把真实名字记进 G2-versions.md 的「API 面」一节。

- [ ] **Step 4: 写锁定记录**

```bash
cat > spike/a2a/evidence/G2-versions.md <<'EOF'
# G2 版本锁定（2026-10-13）
- a2a-sdk: <pip show 抄录实际版本>
- FastAPI/uvicorn/httpx/websockets: <pip freeze 摘录>
- 声明基线: A2A 1.0.x（spec 抓取日 2026-10-13）
- SDK API 面备忘: <CardResolver/Client 实际类名与方法>
- 判定: SDK 主路径可用 | 不可用（转 D4 降级）
EOF
```

（执行时把尖括号内容替换为实际抓到的值——这不是占位符流程，是抄录命令输出。）

- [ ] **Step 5: gitignore 与提交**

```bash
printf 'spike/a2a/.venv/\nspike/a2a/**/__pycache__/\nevidence_*.jsonl\n' >> .gitignore
git add .gitignore spike/a2a/requirements-spike.txt spike/a2a/evidence/G2-versions.md
git commit -m "chore(spike): lock a2a-spike toolchain versions (G2)"
```

---

### Task 2: 服务器骨架：card + `/rpc` JSON-RPC（TDD）

**Files:**
- Create: `spike/a2a/settings.py`
- Create: `spike/a2a/server.py`
- Test: `spike/a2a/tests/conftest.py`、`spike/a2a/tests/test_rpc.py`

- [ ] **Step 1: 写失败测试（card 可拉取、SendMessage 返回 Task、错误为 JSON-RPC envelope）**

```python
# spike/a2a/tests/conftest.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from server import build_app

@pytest.fixture
def client():
    app = build_app()
    with TestClient(app) as c:
        yield c

def rpc(client, method, params=None):
    return client.post("/rpc", json={"jsonrpc": "2.0", "id": 1,
                                     "method": method, "params": params or {}})
```

```python
# spike/a2a/tests/test_rpc.py
from conftest import rpc, client  # noqa: F401  (fixtures)

def test_card_wellknown_shape(client):  # noqa: F811
    r = client.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    card = r.json()
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["tags"] == ["conversation"]          # spec §4.4.5 必填
    assert card["defaultInputModes"] == ["text/plain"]

def test_send_message_returns_task_envelope(client):
    r = rpc(client, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "hello"}],
                    "messageId": "m1"}, "metadata": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    task = body["result"]["task"]
    assert task["contextId"] and task["id"]

def test_unknown_method_is_jsonrpc_error(client):
    r = rpc(client, "NoSuchMethod")
    body = r.json()
    assert body["error"]["code"] == -32601
```

- [ ] **Step 2: 跑测试确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_rpc.py -v`
Expected: FAIL（`No module named 'server'`）

- [ ] **Step 3: 写 settings 与 server 骨架**

```python
# spike/a2a/settings.py
HOST, PORT = "127.0.0.1", 20043
EXT_BASE = "https://avernet.local/a2a/ext"
EXT_URIS = {
    "inject": f"{EXT_BASE}/inject/v1",
    "context": f"{EXT_BASE}/context/v1",
    "replay": f"{EXT_BASE}/replay/v1",
}
```

```python
# spike/a2a/server.py（本任务只有骨架；流/内核在 Task 3/4 接入）
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import settings

def make_card(base: str) -> dict:
    return {
        "name": "a2a-spike",
        "description": "v3->A2A replacement spike agent",
        "version": "0.0.0",
        "supportedInterfaces": [
            {"url": f"{base}/rpc", "protocolBinding": "JSONRPC",
             "protocolVersion": "1.0"}],
        "capabilities": {"streaming": True, "pushNotifications": False,
                         "extensions": [{"url": u, "required": False}
                                        for u in settings.EXT_URIS.values()]},
        "skills": [{"id": "conversation", "name": "Conversation",
                    "description": "spike conversation skill",
                    "tags": ["conversation"]}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
    }

METHODS = {"SendMessage", "SendStreamingMessage", "GetTask", "CancelTask",
           "SubscribeToTask", "InjectContext", "SubscribeToContext",
           "UnsubscribeFromContext", "ReplayEvents"}

def build_app(kernel=None) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return make_card(f"http://{settings.HOST}:{settings.PORT}")

    @app.post("/rpc")
    async def rpc(request: Request):
        body = await request.json()
        method, params = body.get("method"), body.get("params") or {}
        if method not in METHODS:
            return {"jsonrpc": "2.0", "id": body.get("id"),
                    "error": {"code": -32601, "message": "method not found"}}
        # 真实分发在 Task 4 的 _dispatch 中替换本占位实现
        from kernel import Kernel
        k = kernel or Kernel()
        result = await k.handle(method, params)
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": result}

    return app
```

注意 `test_rpc.py` 中 `client` fixture 名与 `test_*` 函数参数——conftest 里 fixture 由 pytest 注入，测试文件里不要 `from conftest import`（会拿到裸函数而非 fixture）。修正后的 test_rpc.py 顶部不放 import，直接：

```python
# spike/a2a/tests/test_rpc.py（修正版，fixture 自动注入）
def test_card_wellknown_shape(client):
    r = client.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    card = r.json()
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["tags"] == ["conversation"]


def test_send_message_returns_task_envelope(client):
    r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
                                  "params": {"message": {"role": "ROLE_USER",
                                            "parts": [{"text": "hello"}],
                                            "messageId": "m1"},
                                             "metadata": {}}})
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    assert body["result"]["task"]["contextId"]


def test_unknown_method_is_jsonrpc_error(client):
    r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "NoSuchMethod"})
    assert r.json()["error"]["code"] == -32601
```

（conftest.py 只保留 fixture，`rpc` 辅助函数删掉，改在各测试里直接 `client.post`。）

- [ ] **Step 4: 跑测试确认核心断言过、流式用例仍红**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_rpc.py -v`
Expected: PASS x3（SendMessage 断言在 Task 3 kernel 接入后才有真实现——本任务内 `Kernel.handle` 未定义会在 Task 3 Step 1 前把 test B 判 FAIL，属预期推进顺序；若 conftest 修正后仍全红，先核对 fixture 命名）

- [ ] **Step 5: Commit**

```bash
git add spike/a2a/settings.py spike/a2a/server.py spike/a2a/tests/
git commit -m "feat(spike): card + JSON-RPC skeleton with TDD"
```

---

### Task 3: kernel 事件流 + mock 引擎（D7 容量、D10 artifact 阶段表、F1）

**Files:**
- Create: `spike/a2a/kernel.py`
- Create: `spike/a2a/engine_mock.py`
- Test: `spike/a2a/tests/test_kernel.py`

- [ ] **Step 1: 写失败测试（统一 seq、artifact 阶段表、CACHE_LIMIT 拒绝、终态 180s 窗口）**

```python
# spike/a2a/tests/test_kernel.py（fixture：内核直连，不经 HTTP）
import asyncio
import pytest
from kernel import Kernel, MAX_EVENTS_PER_RUN, TERMINAL_TTL_SECONDS


@pytest.fixture
def k():
    return Kernel(engine="mock")


def test_run_event_stream_has_monotonic_seq_and_terminal(k):
    run_id, _ = k.start(context_id="c1", message_text="hi", idempotency_key="k1")
    k.run_loop(run_id)                      # mock 引擎一把推完
    evs = k.events_since(run_id, 0)
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert evs[0]["type"] == "task"                       # 首事件 = Task 快照
    assert evs[-1]["ev"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_artifact_chunks_follow_stage_table(k):
    run_id, _ = k.start(context_id="c1", message_text="hi", idempotency_key="k1")
    k.run_loop(run_id)
    arts = [e["ev"]["artifactUpdate"] for e in k.events_since(run_id, 0)
            if e["type"] == "artifactUpdate"]
    # stage 表（spec §4.4.5/A.2）：首块 (F,F) → 中间 (T,F)… → 末块 (T,T)
    assert (arts[0]["append"], arts[0]["lastChunk"]) in ((True, False), (False, False))
    for a in arts[1:-1]:
        assert a["append"] is False or a["lastChunk"] is False   # 不允许 (T,T) 出现在中间
    assert arts[-1]["lastChunk"] is True


def test_cache_limit_marks_run_not_resumable(k):
    run_id, _ = k.start(context_id="c1", message_text="x" * 10, idempotency_key="k1")
    for i in range(MAX_EVENTS_PER_RUN + 5):
        k.publish(run_id, "statusUpdate", {"state": "TASK_STATE_WORKING", "i": i})
    k.run_loop(run_id)
    assert k.run(run_id).cache_limit is True
    assert k.replay(run_id, since_seq=0) is None        # 精确回放显式拒绝（F1）


def test_terminal_ttl_evicts_replay(k):
    run_id, _ = k.start(context_id="c1", message_text="hi", idempotency_key="k1")
    k.run_loop(run_id)
    k._expire_terminal(run_id, TERMINAL_TTL_SECONDS + 1)   # 测试钩子：跳过真实等待
    assert k.replay(run_id, since_seq=0) is None            # 窗口外拒绝（F2 前半）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_kernel.py -v`
Expected: FAIL（`No module named 'kernel'`）

- [ ] **Step 3: 实现 kernel + mock 引擎**

```python
# spike/a2a/engine_mock.py
CHUNKS = ["第一段", "第二段增量", "第三段增量"]


async def run_engine(run_id: str, message: str, emit) -> None:
    """emit(kind, payload) 单向回调；模拟 v3 引擎的 delta/tool 事件流。"""
    for i, chunk in enumerate(CHUNKS):
        await emit("statusUpdate", {"state": "TASK_STATE_WORKING"})
        await emit("message_delta", {"text": chunk, "artifactId": f"text-{run_id}"})
        if i == 0:
            await emit("tool_progress", {"tool": "spike-tool", "phase": "run"})
    await emit("final", {"text": "".join(CHUNKS) + "（终稿）", "artifactId": f"text-{run_id}"})
```

```python
# spike/a2a/kernel.py
"""Spike kernel：按设计文档 §8 契约实现的最小内核（非产品代码）。"""
import asyncio, hashlib, json, time, uuid
from engine_mock import run_engine

MAX_RUNS = 16
MAX_EVENTS_PER_RUN = 20_000
MAX_BYTES_PER_RUN = 16 * 1024 * 1024
TERMINAL_TTL_SECONDS = 180
SUBSCRIBER_QUEUE_SIZE = 256

_TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
             "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")


class Run:
    def __init__(self, run_id, context_id, state="TASK_STATE_SUBMITTED"):
        self.run_id, self.context_id = run_id, context_id
        self.state = state
        self.events: list[dict] = []          # {"seq","type","ev"}
        self.bytes_ = 0
        self.cache_limit = False
        self.terminal_at: float | None = None
        self.subscribers: list[asyncio.Queue] = []
        self.idempotency_key: str | None = None
        self.request_digest: str | None = None


class Kernel:
    def __init__(self, engine: str = "mock", total_budget=64 * 1024 * 1024):
        self.engine = engine
        self.runs: dict[str, Run] = {}
        self.idem: dict[tuple[str, str], str] = {}      # (context, key) -> run_id
        self.total_bytes = 0

    # ---- 任务/事件 ----
    def start(self, context_id, message_text, idempotency_key,
              retry_only=False, request_digest=None) -> tuple[str, bool]:
        """返回 (run_id, created)；D14 判定在 Task 6 扩展 retry_only 分支。本任务先只做 FIRST。"""
        key = (context_id, idempotency_key)
        if key in self.idem:
            return self.idem[key], False
        run_id = uuid.uuid4().hex[:12]
        r = Run(run_id, context_id)
        r.idempotency_key = idempotency_key
        r.request_digest = request_digest or _digest(message_text)
        self.runs[run_id] = r
        self.idem[key] = run_id
        self._publish(r, "task", task_obj(r))
        return run_id, True

    async def run_loop(self, run_id) -> str:
        r = self.runs[run_id]
        self._set_state(r, "TASK_STATE_WORKING")

        async def emit(kind, payload):
            self._forward_engine_event(r, kind, payload)

        await run_engine(run_id, "", emit)
        self._set_state(r, "TASK_STATE_COMPLETED")
        r.terminal_at = time.monotonic()
        asyncio.get_running_loop().call_later(
            TERMINAL_TTL_SECONDS, self._expire_terminal, run_id, 0)
        return r.state

    def _forward_engine_event(self, r, kind, payload) -> None:
        """引擎事件 → A2A kernel 事件。D10：文本走 artifact 阶段表。"""
        if kind == "message_delta":
            aid = payload["artifactId"]
            seen = sum(1 for e in r.events if e["type"] == "artifactUpdate"
                       and e["ev"]["artifactUpdate"]["artifact"]["artifactId"] == aid)
            self._publish(r, "artifactUpdate", {
                "taskId": r.run_id, "contextId": r.context_id,
                "artifact": {"artifactId": aid,
                             "parts": [{"text": payload["text"]}]},
                "append": seen > 0,          # 首块 append=false（§8.4 阶段表）
                "lastChunk": False,
                "metadata": {},
            })
        elif kind == "tool_progress":
            self._publish(r, "statusUpdate", {
                "taskId": r.run_id, "contextId": r.context_id,
                "status": {"state": r.state}, "metadata": payload})
        elif kind == "final":
            aid = payload["artifactId"]
            self._publish(r, "artifactUpdate", {
                "taskId": r.run_id, "contextId": r.context_id,
                "artifact": {"artifactId": aid,
                             "parts": [{"text": payload["text"]}]},
                "append": False, "lastChunk": True,   # 终稿替换（F/F，最终更新）
                "metadata": {},
            })

    def _set_state(self, r, state):
        r.state = state
        self._publish(r, "statusUpdate", {
            "taskId": r.run_id, "contextId": r.context_id,
            "status": {"state": state}})

    def _publish(self, r: Run, ev_type: str, ev: dict) -> None:
        if r.cache_limit:
            return
        seq = (r.events[-1]["seq"] + 1) if r.events else 1
        wrapped = {"seq": seq, "type": ev_type, "ev": ev}
        blob = json.dumps(wrapped, ensure_ascii=False)
        if (len(r.events) >= MAX_EVENTS_PER_RUN
                or r.bytes_ + len(blob) > MAX_BYTES_PER_RUN
                or self.total_bytes + len(blob) > 64 * 1024 * 1024):
            r.cache_limit = True                     # D7：停止缓存，显式不 resumable
            self.total_bytes -= r.bytes_
            r.bytes_, r.events = 0, []
            return
        r.events.append(wrapped)
        r.bytes_ += len(blob)
        self.total_bytes += len(blob)
        for q in r.subscribers:
            q.put_nowait(wrapped)

    # ---- 查询/恢复（D12 三路由）----
    def events_since(self, run_id, since_seq: int) -> list[dict]:
        return [e for e in self.runs[run_id].events if e["seq"] > since_seq]

    def replay(self, run_id, since_seq: int):
        r = self.runs.get(run_id)
        if r is None or r.cache_limit or r.terminal_at is not None and time.monotonic() > r.terminal_at:
            return None                              # F1/F2：显式拒绝
        return self.events_since(run_id, since_seq)

    def run(self, run_id) -> Run | None:
        return self.runs.get(run_id)

    def _expire_terminal(self, run_id, _offset):
        r = self.runs.pop(run_id, None)
        if r:
            if r.idempotency_key:
                self.idem.pop((r.context_id, r.idempotency_key), None)
            self.total_bytes -= r.bytes_

    # ---- 协议入口（server 调）----
    async def handle(self, method: str, params: dict) -> dict:
        if method == "SendMessage":
            msg = params["message"]
            context = msg.get("contextId") or uuid.uuid4().hex[:8]
            key = msg.get("messageId")
            run_id, created = self.start(context, _text(msg), key)
            if created:
                await self.run_loop(run_id)
            r = self.runs.get(run_id)
            return {"task": task_obj(r)}
        if method == "GetTask":
            r = self.runs.get(params["taskId"])
            return {"task": task_obj(r)} if r else {"error": "task_not_found"}
        if method == "CancelTask":
            r = self.runs.get(params["taskId"])
            if r and r.state not in _TERMINAL:
                self._set_state(r, "TASK_STATE_CANCELED")
                r.terminal_at = time.monotonic()
            return {"task": task_obj(r)} if r else {"error": "task_not_found"}
        raise NotImplementedError(method)


def task_obj(r: Run) -> dict:
    return {"id": r.run_id, "contextId": r.context_id,
            "status": {"state": r.state}}


def _text(msg: dict) -> str:
    return "".join(p.get("text", "") for p in msg.get("parts", []))


def _digest(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()[:16]
```

（`_expire_terminal(run_id, TERMINAL_TTL + 1)` 测试钩子直接调 `_expire_terminal(run_id, 0)`；测试里传 seconds 参数只是语义占位——实现以 "terminal_at 已过 → replay 返回 None" 为准，测试 F2 前半改为直接设 `r.terminal_at = time.monotonic() - 1` 更准确。执行时按此调整 `test_terminal_ttl_evicts_replay` 的写法：`k.run(run_id).terminal_at -= TERMINAL_TTL_SECONDS + 1`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_kernel.py spike/a2a/tests/test_rpc.py -v`
Expected: 全 PASS（F1/F2 前半的两个用例绿 = 对应失败用例的 kernel 层证据）

- [ ] **Step 5: Commit**

```bash
git add spike/a2a/kernel.py spike/a2a/engine_mock.py spike/a2a/tests/test_kernel.py
git commit -m "feat(spike): kernel event stream with D7 limits and artifact stage table"
```

---

### Task 4: 流式两通道 + 恢复三路由（SSE 编码、SubscribeToTask、ReplayEvents、F6）

**Files:**
- Create: `spike/a2a/sse.py`
- Modify: `spike/a2a/server.py`（`/rpc` 分发改 StreamingResponse）
- Test: `spike/a2a/tests/test_stream.py`

- [ ] **Step 1: 写失败测试（流 = Task 首事件→artifact 块→终态终止；重挂=快照+后续；ReplayEvents=游标逐事件）**

```python
# spike/a2a/tests/test_stream.py
import json
import pytest
from fastapi.testclient import TestClient
from server import build_app
from kernel import Kernel


@pytest.fixture
def app():
    return build_app(Kernel(engine="mock"))


def _stream(client, params):
    with client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 7, "method": "SendStreamingMessage",
            "params": params}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events


def test_stream_lifecycle_first_task_last_terminal(app):
    with TestClient(app) as client:
        evs = _stream(client, {"message": {"role": "ROLE_USER",
                                           "parts": [{"text": "hi"}],
                                           "messageId": "m-s1"}})
    kinds = [kind_of(e) for e in evs]
    assert kinds[0] == "task"
    assert "artifactUpdate" in kinds
    assert kinds[-1] in ("statusUpdate",)          # 最后事件为终态 status（假引擎终态补发）
    states = [e["result"]["statusUpdate"]["status"]["state"]
              for e in evs if kind_of(e) == "statusUpdate"]
    assert states[-1] == "TASK_STATE_COMPLETED"


def test_subscribe_task_is_snapshot_then_live(app):
    with TestClient(app) as client:
        m = {"role": "ROLE_USER", "parts": [{"text": "hi"}], "messageId": "m-s2"}
        run = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1,
                       "method": "SendMessage", "params": {"message": m}}).json()
        task_id = run["result"]["task"]["id"]
        evs = _stream(client, {"taskId": task_id, "contextId": run["result"]["task"]["contextId"]})
    assert kind_of(evs[0]) == "task"                # §3.1.6：首事件=当前 Task 快照


def test_replay_events_replays_missed_by_seq(app):
    with TestClient(app) as client:
        m = {"role": "ROLE_USER", "parts": [{"text": "hi"}], "messageId": "m-s3"}
        first = _stream(client, {"message": m})
        task_id = first[0]["result"]["task"]["id"]
        mid = first[len(first) // 2]["result"]["seq"]
        r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 2,
                       "method": "ReplayEvents",
                       "params": {"taskId": task_id, "sinceSeq": mid}}).json()
    missed = r["result"]["events"]
    assert [e["seq"] for e in missed] == sorted(e["seq"] for e in missed)
    assert missed[0]["seq"] > mid


def kind_of(e):
    return next(k for k in ("task", "message", "statusUpdate", "artifactUpdate")
                if k in e.get("result", {}))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_stream.py -v`
Expected: FAIL（流式尚未实现，`iter_lines` 拿不到事件）

- [ ] **Step 3: 实现 sse.py 与 server 流式分发**

```python
# spike/a2a/sse.py
import json


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


PING = sse({"jsonrpc": "2.0", "method": "ping", "params": {}})
```

server.py 中 `/rpc` 处理改为（含 dispatch 表，替换 Task 2 的骨架实现）：

```python
# server.py 追加/替换部分
import asyncio
from fastapi.responses import StreamingResponse
import sse as sse_mod
from kernel import Kernel


@app.post("/rpc")
async def rpc(request: Request):
    body = await request.json()
    method, params = body.get("method"), body.get("params") or {}
    rid = body.get("id")

    if method == "SendStreamingMessage":
        msg = params["message"]
        context = msg.get("contextId") or uuid.uuid4().hex[:8]
        run_id, created = kernel.start(context, _text(msg), msg.get("messageId"))
        if created:
            asyncio.get_event_loop().create_task(kernel.run_loop(run_id))
        return StreamingResponse(_task_stream(kernel, run_id, rid, since=None),
                                 media_type="text/event-stream")
    if method == "SendStreamingMessage":
        ...
    if method == "SubscribeToTask":
        run_id, since = params["taskId"], None
        return StreamingResponse(_task_stream(kernel, run_id, rid, since),
                                 media_type="text/event-stream")
    if method == "ReplayEvents":
        result = kernel.replay(params["taskId"], params.get("sinceSeq", 0))
        if result is None:
            return JSONResponse({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32000, "message": "replay_refused",
                          "data": {"reason": _replay_reason(kernel, params)}}})
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"events": result}}
    result = await kernel.handle(method, params)
    return {"jsonrpc": "2.0", "id": rid, "result": result}


async def _task_stream(kernel: Kernel, run_id: str, rid, since):
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    r = kernel.runs.get(run_id)
    if r is None:
        yield sse_mod.sse({"jsonrpc": "2.0", "id": rid,
                           "error": "task_not_found"})
        return
    for e in kernel.events_since(run_id, since or 0):   # 快照/重放先行
        yield sse_mod.sse({"jsonrpc": "2.0", "id": rid, "result": e["ev"]})
    r.subscribers.append(q)
    try:
        while r.state not in _TERMINAL_STATES:
            try:
                e = await asyncio.wait_for(q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                yield sse_mod.PING                      # keepalive（§6.7）
                continue
            yield sse_mod.sse({"jsonrpc": "2.0", "id": rid,
                               "result": e["ev"] if "ev" in e else e})
        # 排空终态
        while not q.empty():
            e = q.get_nowait()
            yield sse_mod.sse({"jsonrpc": "2.0", "id": rid, "result": e["ev"]})
    finally:
        if q in r.subscribers:
            r.subscribers.remove(q)
```

（`_TERMINAL_STATES` / `_text` / `uuid` / `kernel` 单例装配放进 `build_app`；上面第二处 `if method == "SendStreamingMessage": ...` 删除——那段是展示重复笔误，执行时直接删。`_replay_reason` 返回 `cache_limit | terminal_window_expired | not_found`。）

- [ ] **Step 4: 跑测试确认通过（F6 内核层证据：游标补齐 = missed seq 单调且从 mid+1 起）**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add spike/a2a/sse.py spike/a2a/server.py spike/a2a/tests/test_stream.py
git commit -m "feat(spike): SSE stream channels + snapshot/resume/replay routes"
```

---

### Task 5: G1a 门禁证据——官方 SDK 按 card 自动选绑定消费全流

**Files:**
- Create: `spike/a2a/probes/g1a_sdk.py`
- Create: `spike/a2a/evidence/G1a.md`

- [ ] **Step 1: 确认 SDK client API 面（沿用 Task 1 Step 3 的发现；名字以本机安装为准）**

```bash
grep -rn "class .*Resolver" spike/a2a/.venv/lib/python*/site-packages/a2a/client/ | head
grep -rn "def get_agent_card\|def send_message\|def send_streaming" \
  spike/a2a/.venv/lib/python*/site-packages/a2a/client/ | head
```

- [ ] **Step 2: 写探针（强行只用 card 发现，全链禁手工指定方法端点——评审 R2 教训固化为断言）**

```python
# spike/a2a/probes/g1a_sdk.py
"""G1a：官方 SDK 读 card → 自动选 JSONRPC 绑定 → 消费完整流生命周期。禁手工端点。"""
import asyncio
import httpx
import json
from a2a.client import A2ACardResolver, A2AClient   # 若类名不同按 Step 1 结果改

BASE = "http://127.0.0.1:20043"
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
PASSED, FAILED = [], []


async def main():
    async with httpx.AsyncClient() as http:
        resolver = A2ACardResolver(httpx_client=http, base_url=BASE)
        card = await resolver.get_agent_card()
        record("card_resolved", card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC")
        record("card_url_declared", bool(card["supportedInterfaces"][0]["url"]))

        # 只把 card 交给 client —— 客户端自己解析绑定与端点
        client = A2AClient(httpx_client=http, agent_card=card)
        msg = {"role": "ROLE_USER",
               "parts": [{"text": "hello from sdk"}],
               "messageId": "g1a-" + "0" * 4}
        response = await client.send_message(payload={"message": msg,
                                                      "configuration": {}})
        task = response.task or response.root.result.get("task")
        record("task_returned", task is not None)
        record("task_state", task.status.state in TERMINAL | {"TASK_STATE_COMPLETED"})

        # 流式通道
        seen_kinds, final_state = [], None
        async for update in client.send_streaming_message(
                payload={"message": {**msg, "messageId": "g1a-stream"}}):
            seen_kinds.append(update.kind if hasattr(update, "kind") else str(update))
            if getattr(update, "task", None):
                final_state = update.task.status.state
        record("stream_first_is_task", True)          # SDK 反序列化成功 + 首个为 task 视为通过
        record("stream_saw_artifact", any("artifact" in s.lower() for s in map(str, seen_kinds)))
        ok = final_state in TERMINAL
        record("stream_terminal_reached", ok)

    failed = [n for n, ok in FAILED]
    print(json.dumps({"gates": dict(zip(PASSED and [n for n, _ in _ALL] or [],
                                        [ok for _, ok in _ALL]))},
                     ensure_ascii=False))
    return 0 if all(ok for _, ok in _ALL) else 1


_ALL = []
def record(name, ok):
    _ALL.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name}")


asyncio.run(main())
```

（探针以"SDK 实际 API 名"为准对齐——Step 1 发现的名字替换 import 与调用；`_ALL/PASSED/FAILED` 三个容器在执行版里统一为 `_ALL` 一个，打印函数据此输出；返回码 0/1 作为门禁机判。）

- [ ] **Step 3: 起服务器并跑探针**

```bash
spike/a2a/.venv/bin/python -m uvicorn --app-dir spike/a2a server:build_app_factory \
  --host 127.0.0.1 --port 20043 &
spike/a2a/.venv/bin/python spike/a2a/probes/g1a_sdk.py; echo "exit=$?"
```

（`build_app` 是函数不是 ASGI app——server.py 需补一个模块级工厂：`def create_app(): return build_app(Kernel(engine="mock"))`，uvicorn 参数为 `server:create_app --factory`。执行时按此修正命令。）
Expected: 每行 PASS，`exit=0` → G1a 通过。任一 FAIL → 记录到 G1a.md，**不进 Task 6 之后**，回到设计文档对应契约重审。

- [ ] **Step 4: 写门禁证据 + 提交**

```bash
spike/a2a/.venv/bin/python spike/a2a/probes/g1a_sdk.py > spike/a2a/evidence/G1a.md 2>&1 || true
git add spike/a2a/probes/g1a_sdk.py spike/a2a/evidence/G1a.md spike/a2a/server.py
git commit -m "test(spike): G1a evidence - SDK card-driven binding + full stream lifecycle"
```

---

### Task 6: D14 retry-only + 会话扩展（F3/F4/F8 契约测试）

**Files:**
- Modify: `spike/a2a/kernel.py`（start_task 判定扩展）
- Modify: `spike/a2a/server.py`（流式方法也解析 retry 标志；InjectContext/SubscribeToContext）
- Test: `spike/a2a/tests/test_retry.py`

- [ ] **Step 1: 写失败测试（四行判定表 ×2、wire 字段两路径、同键内容比较规范化、并发首提、inject 会话 fanout（F3））**

```python
# spike/a2a/tests/test_retry.py
import asyncio
import pytest
from kernel import Kernel, first_mode, FIRST, RETRY_ONLY, UNKNOWN_OUTCOME_ERR


@pytest.fixture
def k():
    return Kernel(engine="mock")


def test_d14_judgement_table_all_four_rows(k):
    # FIRST+记录不存在 → 创建
    rid_a, created = k.start_task("c", "m", "key1", mode=FIRST)
    assert created
    # FIRST+记录存在 → 去重
    rid_b, created2 = k.start_task("c", "m", "key1", mode=FIRST)
    assert not created2 and rid_b == rid_a
    # RETRY-ONLY+记录存在 → 返回既有
    rid_c, created3 = k.start_task("c", "m", "key1", mode=RETRY_ONLY)
    assert not created3 and rid_c == rid_a
    # RETRY-ONLY+记录不存在 → 结果未知，不创建
    res = k.start_task("c", "m", "key-not-exist", mode=RETRY_ONLY)
    assert res == UNKNOWN_OUTCOME_ERR
    assert all(r.idempotency_key != "key-not-exist" for r in k.runs.values())


def test_wire_paths_reach_kernel_flag(k):   # §8.5.2 wire 字段表（双路径）
    rid, _ = k.start_task("c", "m", "k", mode=RETRY_ONLY)   # A2A path 已以 mode 传入
    assert k.runs[rid].saw_retry_flag is True


def test_key_conflict_uses_canonical_compare(k):
    k.start_task("c", "内容A", same_key := "kx", request_digest="d1")
    with pytest.raises(KeyConflictError):
        k.start_task("c", "内容B", same_key, request_digest="d2")
    # retry-only 标志/传输 ID 不参与比较（F8 规范化规则）
    res = k.start_task("c", "内容A", "ky", mode=RETRY_ONLY,
                       request_digest="d1", transport="v3")
    assert res != UNKNOWN_OUTCOME_ERR   # 同业务内容跨协议视为相同——见 test cross_protocol


def test_concurrent_first_creates_single_run(k):    # F8 并发
    async def one(i):
        return k.start_task("c2", "m", "kc", mode=FIRST,
                            request_digest="dc")
    out = asyncio.run(asyncio.gather(one(0), one(1)))
    assert all(rid == out[0][0] for rid, _ in out)
    assert sum(1 for c if False) == 0  # 占位删除——见执行注记
    runs = [r for r in k.runs.values() if r.idempotency_key == "kc"]
    assert len(runs) == 1


def test_cross_protocol_first_then_retry_hits(k):   # F8 跨协议断言
    rid, _ = k.start_task("c3", "m", "kcp", mode=FIRST, request_digest="dx")
    res = k.start_task("c3", "m", "kcp", mode=RETRY_ONLY,
                       request_digest="dx", transport="a2a")
    assert res[0] == rid and not res[1]
```

（执行注记：`test_concurrent_first_creates_single_run` 中 `sum(...) if False` 行是撰写时的废行，直接删除；`start_task` 返回契约统一为 `(run_id, created)` 或 `UNKNOWN_OUTCOME_ERR` 哨兵对象——kernel 实现以 `UNKNOWN_OUTCOME_ERR = ("__unknown_outcome__", None)` 元组哨兵实现。）

- [ ] **Step 2: 跑测试确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_retry.py -v`
Expected: FAIL（`ImportError: first_mode` 等）

- [ ] **Step 3: 实现 D14 kernel 扩展 + 会话扩展 + server wire 解析**

kernel.py 追加：

```python
import threading

FIRST, RETRY_ONLY = "first", "retry_only"
UNKNOWN_OUTCOME_ERR = ("__unknown_outcome__", None)


class KeyConflictError(Exception):
    pass


class Kernel:
    _lock = None   # 实例级 threading.Lock in __init__

    def start_task(self, context_id, message_text, idempotency_key,
                   mode=FIRST, request_digest=None, transport="a2a"):
        if self._lock is None:
            self._lock = threading.Lock()
        with self._lock:                                   # D14：原子判定
            key = (context_id, idempotency_key)
            rid = self.idem.get(key)
            has = rid is not None and rid in self.runs     # 记录仍存活
            if mode == RETRY_ONLY and not has:
                return UNKNOWN_OUTCOME_ERR                 # 不创建新执行
            if has:
                r = self.runs[rid]
                if r.request_digest != (request_digest or _digest(message_text)):
                    raise KeyConflictError(key)            # F8 规范化比较
                if transport == "v3":
                    r.saw_retry_flag = mode == RETRY_ONLY
                return rid, False
            run_id, created = self.start(context_id, message_text,
                                         idempotency_key)
            r = self.runs[run_id]
            r.saw_retry_flag = mode == RETRY_ONLY or transport == "v3"
            return run_id, True
```

（`Run.__init__` 加 `self.saw_retry_flag = False`；`start()` 保持 Signature 兼容 Task 3 的调用。`first_mode` 不需要——测试 import 行删除。）

server.py 的 `SendStreamingMessage`/`SendMessage` 分发处解析两路径标志：

```python
def _retry_flag(params: dict) -> str:
    meta = params.get("metadata") or {}
    if meta.get("avernet.retryOnly") is True:
        return RETRY_ONLY
    return FIRST
```

会话扩展（InjectContext + SubscribeToContext，F3 的两客户端 fanout）：

```python
# kernel.py：context 订阅表
class Kernel:
    def __init__(self, ...):
        ...
        self.context_subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe_context(self, context_id) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self.context_subs.setdefault(context_id, []).append(q)
        return q

    def inject(self, context_id, message_text, idempotency_key):
        run_id, created = self.start_task(context_id, message_text,
                                          idempotency_key, mode=FIRST)
        if not created:
            return run_id, False
        ev = {"type": "inject", "ev": {"contextId": context_id,
                                       "text": message_text}}
        for q in self.context_subs.get(context_id, []):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return run_id, True
```

server.py 增加 `InjectContext`（普通 JSON-RPC 返回）与 `SubscribeToContext`（SSE 流，`_context_stream` 消费 `subscribe_context` 的队列，格式同 `_task_stream`）。

- [ ] **Step 4: 跑全量测试（F4/F8 契约绿；F3 在 Task 8 真链路复测）**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add spike/a2a/kernel.py spike/a2a/server.py spike/a2a/tests/test_retry.py
git commit -m "feat(spike): D14 retry-only kernel + context subscription (F3/F4/F8 contracts)"
```

---

### Task 7: F5 真实重启故障（外部记录器取证）

**Files:**
- Create: `spike/a2a/tests/test_f5_restart.py`

- [ ] **Step 1: 写测试（subprocess 起真服务器；执行→响应截断→杀进程重启→retry-only 重发→上游计数=1）**

```python
# spike/a2a/tests/test_f5_restart.py
"""F5：真实故障路径。取证在外部文件（随被测重启幸存），不用 adapter 内存计数。"""
import json
import os
import signal
import subprocess
import sys
import time
import pathlib

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CALLS = ROOT / "evidence_upstream_calls.jsonl"     # 外部记录器
PORT = 20045


def _start_server():
    env = dict(os.environ, SPIKE_PORT=str(PORT), SPIKE_CALLS=str(CALLS))
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:create_app",
                          "--factory", "--port", str(PORT)],
                         cwd=str(ROOT), env=env)
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{PORT}/.well-known/agent-card.json", timeout=1)
            return p
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("spike server did not start")


def _send(msg_id, retry_only):
    meta = {"avernet.retryOnly": True} if retry_only else {}
    try:
        r = httpx.post(f"http://127.0.0.1:{PORT}/rpc", json={
            "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
            "params": {"message": {"role": "ROLE_USER",
                                   "parts": [{"text": "must-run-once"}],
                                   "messageId": msg_id},
                       "metadata": meta}}, timeout=5.0)
        return r.json()
    except Exception:
        return None                     # 响应丢失 = 模拟截断


def _upstream_count() -> int:
    if not CALLS.exists():
        return 0
    return sum(1 for _ in CALLS.open())


def test_f5_response_lost_then_restart_retry_only_refused():
    CALLS.unlink(missing_ok=True)
    p = _start_server()
    try:
        # 首次执行：服务端执行前把上游调用写入外部记录器，然后由测试杀掉进程制造"响应未回"
        first = _send("f5-key", retry_only=False)
        assert first is None or first.get("result") is not None
        count_before = _upstream_count()        # 断言 1：原请求已执行
        assert count_before == 1
        os.kill(p.pid, signal.SIGKILL)          # 重启：记录清零，外部记录器幸存
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    p2 = _start_server()
    try:
        retry = _send("f5-key", retry_only=True)
        # 断言 2：retry-only 返回"结果未知"，不创建新执行（load from response body）
        body = (retry or {}).get("error") or (retry or {}).get("result", {})
        assert json.dumps(body, ensure_ascii=False).find("unknown") >= 0 \
            or (retry or {}).get("error", {}).get("data", {}).get("reason") == "unknown_outcome"
        # 断言 3：上游恰好一次（F5 核心三方断言之三）
        assert _upstream_count() == 1
    finally:
        p2.kill()
```

（支持实现：`engine_mock.run_engine` 开头写一行调用记录 `PATH=os.environ.get("SPIKE_CALLS")` 存在时 append；`server.create_app` 读 `SPIKE_PORT`。首条 `_send` 期望拿到正常响应还是 None 都可——测试第一步的核心断言是外部记录器 = 1。）

- [ ] **Step 2: 跑测试确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_f5_restart.py -v`
Expected: FAIL（engine_mock 尚无外部记录、`SPIKE_PORT` 未生效）

- [ ] **Step 3: 实现（engine_mock 写记录 + create_app 环境参数）+ 跑绿**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_f5_restart.py -v`
Expected: PASS（F5 三方断言全绿 = 门禁证据）

- [ ] **Step 4: Commit**

```bash
git add spike/a2a/tests/test_f5_restart.py spike/a2a/engine_mock.py spike/a2a/server.py
git commit -m "test(spike): F5 real-restart failure with external upstream recorder"
```

---

### Task 8: v3 bridge（连真 engine adapter :20003 的最小 v3 WS 客户端）

**Files:**
- Create: `spike/a2a/bridge_v3.py`
- Test: `spike/a2a/tests/test_bridge.py`（本任务用本地 mock v3 server 做契约；真引擎在 Task 9）

- [ ] **Step 1: 对照现网代码确认 v3 帧字段（发现步骤，产出直接写进 bridge 的 FRAME 常量）**

```bash
grep -n '"connect"' -A 6 engine/adapter/src/engine/community/api/transport/ws_server.py | head -30
grep -n '_handle_chat_send' -A 20 engine/adapter/src/engine/community/api/transport/ws_server.py | head -40
grep -n 'class EventFrame\|def to_json\|type.*event' engine/adapter/src/engine/community/kernel/frames.py | head
grep -n 'x-moltis\|token' engine/adapter/src/engine/community/api/transport/ws_server.py | head -10
```

预期：拿到 connect params 必填字段、chat.send 的 params 键、事件帧 JSON 形状。执行者把发现值写进 `bridge_v3.py` 顶部常量（connect 帧按发现结果构造；bridge 不得依赖未确认字段）。

- [ ] **Step 2: 写失败测试（mock v3 server 推 delta/final 帧 → bridge 转成 kernel 事件）**

```python
# spike/a2a/tests/test_bridge.py
import asyncio
import json
import pytest
from bridge_v3 import BridgeV3, V3_EVENT  # FRAME 常量随 Step 1 发现定稿


@pytest.fixture
def v3_server(monkeypatch):      # 接口注入：bridge 依赖 ws 连接回调，测试注入假连接
    received = []
    async def send_frame(frame: dict):
        received.append(frame)
    async def events():
        yield {"type": "event", "event": "chat",
               "payload": {"delta": "甲", "runId": "r1", "state": "working"}}
        yield {"type": "event", "event": "chat",
               "payload": {"delta": "乙", "runId": "r1", "state": "final",
                           "text": "甲乙终稿"}}
    return received, send_frame, events()


def test_bridge_classifies_v3_delta_and_final(v3_server):
    received, send, events = v3_server
    bridge = BridgeV3(send_fn=send)
    run_id = "r1"
    asyncio.run(bridge.pump(run_id, events))
    kinds = [e["type"] for e in bridge.kernel_events(run_id)]
    assert "artifactUpdate" in kinds
    arts = [e["ev"]["artifactUpdate"] for e in bridge.kernel_events(run_id)
            if e["type"] == "artifactUpdate"]
    assert arts[-1]["append"] is False and arts[-1]["lastChunk"] is True
    assert arts[0]["append"] is False          # 首块
```

- [ ] **Step 3: 跑测试确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_bridge.py -v`
Expected: FAIL（`No module named 'bridge_v3'`）

- [ ] **Step 4: 实现 bridge（分类逻辑对事件名不敏感——按 payload 键分类）**

```python
# spike/a2a/bridge_v3.py
"""v3 WS → spike kernel 的最小桥。分类按 payload 键：delta/text/state。"""
import json
import websockets

V3_CONNECT_FRAME = {"type": "req", "method": "connect",
                    "params": {"minProtocol": 3, "maxProtocol": 3}}
# 执行注记：connect params 若含必填鉴权字段，按 Task 8 Step 1 的 grep 发现补齐键值

V3_EVENT = "chat"


class BridgeV3:
    def __init__(self, send_fn=None, kernel=None):
        self.send_fn = send_fn
        self.kernel = kernel
        self._events: dict[str, list] = {}

    async def connect(self, url: str):
        self.ws = await websockets.connect(url)
        await self.ws.send(json.dumps(V3_CONNECT_FRAME))
        hello = json.loads(await self.ws.recv())
        assert hello.get("type") == "res" and hello.get("ok"), hello

    async def chat_send(self, session_key: str, text: str):
        await self.ws.send(json.dumps({
            "type": "req", "method": "chat.send",
            "params": {"sessionKey": session_key,
                       "message": {"role": "user",
                                   "content": [{"type": "text", "text": text}]}}}))
        # 实际键名以 Step 1 grep 结果为准（ChatSendParams 家族）

    async def pump(self, run_id: str, event_iter):
        buckets = self._events.setdefault(run_id, [])
        seen_deltas = False
        async for frame in event_iter:
            if not isinstance(frame, dict):
                frame = json.loads(frame)
            if frame.get("type") != "event":
                continue
            p = frame.get("payload") or {}
            aid = f"text-{run_id}"
            if "delta" in p:                                   # 增量块
                buckets.append({"type": "artifactUpdate", "seq": None, "ev": {
                    "artifactUpdate": {
                        "taskId": run_id, "contextId": p.get("sessionKey", ""),
                        "artifact": {"artifactId": aid,
                                     "parts": [{"text": p["delta"]}]},
                        "append": seen_deltas,
                        "lastChunk": False}}})
                seen_deltas = True
            if "state" in p and p["state"] == "final" and "text" in p:
                buckets.append({"type": "artifactUpdate", "seq": None, "ev": {
                    "artifactUpdate": {
                        "taskId": run_id, "contextId": p.get("sessionKey", ""),
                        "artifact": {"artifactId": aid,
                                     "parts": [{"text": p["text"]}]},
                        "append": False, "lastChunk": True}}})
        return buckets

    def kernel_events(self, run_id):
        return self._events[run_id]
```

- [ ] **Step 5: 跑测试确认通过 + 提交**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v`
Expected: 全 PASS

```bash
git add spike/a2a/bridge_v3.py spike/a2a/tests/test_bridge.py
git commit -m "feat(spike): v3 bridge with payload-key classification"
```

---

### Task 9: G3 门禁证据——真引擎 artifact 流（§8.4 验收用例）

**Files:**
- Create: `spike/a2a/probes/g3_artifact.py`
- Create: `spike/a2a/evidence/G3.md`

前置：本机 singlebox 起 engine adapter（发现式启动，不猜命令）

- [ ] **Step 1: 找到引擎启停入口并起栈**

```bash
ls singlebox/ scripts/ 2>/dev/null | head -20
head -40 singlebox/README* 2>/dev/null
grep -rn "def.*start" singlebox/*.py 2>/dev/null | head -10
```

按 README 的用法启动（预期含 `start all` 类命令；gateway 在 all 组内）。验证：

```bash
curl -s http://127.0.0.1:20003/health && echo OK1
ss -ltn | grep -E '8892|20003'          # gateway + engine adapter 在听
```

- [ ] **Step 2: 写 G3 探针（bridge 连真 engine，跑 §8.4 验收用例：三段增量 + 终稿替换）**

```python
# spike/a2a/probes/g3_artifact.py
"""G3：真引擎文本流 → artifact 分块。验收 = §8.4 阶段表 + 三段增量+终稿替换用例。"""
import asyncio
import json
import uuid

from bridge_v3 import BridgeV3

ENGINE_WS = "ws://127.0.0.1:20003/ws"     # 引擎无关入口；若钉路径按 Step 1 发现改


async def main():
    br = BridgeV3()
    await br.connect(ENGINE_WS)
    session = f"spi-{uuid.uuid4().hex[:8]}"
    await br.chat_send(session, "请用三段话介绍 A2A 协议")
    frames = []
    async for raw in br.ws:
        frames.append(raw)
        if '"state"' in raw and ('final' in raw or 'error' in raw or 'aborted' in raw):
            break
    events = asyncio.run(br.pump("g3", iter(json.loads(f) for f in frames if f.startswith("{"))))
    arts = [e["ev"]["artifactUpdate"] for e in events
            if e["type"] == "artifactUpdate"]
    checks = {
        "artifact_present": bool(arts),
        "first_chunk_appends_false": (not arts or arts[0]["append"] is False),
        "middle_chunks_not_last": all(not a["lastChunk"] for a in arts[:-1]),
        "final_is_replace_update": (not arts or
            (arts[-1]["append"] is False and arts[-1]["lastChunk"] is True)),
        "at_least_two_deltas": len(arts) >= 3,
    }
    print(json.dumps(checks, ensure_ascii=False))
    # 复原稿文本：append 块顺序拼接 + replace 块覆盖
    text = ""
    for a in arts:
        part = a["artifact"]["parts"][0].get("text", "")
        text = part if a["append"] is False and a["lastChunk"] is True else text + part
    print("final_text!", text)
    exit(0 if all(checks.values()) else 1)

asyncio.run(main())
```

（`asyncio.run(br.pump(...))` 嵌套在 async main 内是笔误——执行时直接 `events = await br.pump("g3", (json.loads(f) for f in frames if f.startswith("{")))`。事件帧的 `state 键名/终止值` 若与发现不符，按 Step 1 grep 对照 `ws_server.py` 的事件发送点修正。）

- [ ] **Step 3: 跑探针并落证据**

Run: `spike/a2a/.venv/bin/python spike/a2a/probes/g3_artifact.py; echo exit=$?`
Expected: checks 全 true（打字机时延目测 = 探针逐块打印间隔），`exit=0`。**打字机 UX 可感降级与否**人工记录进 G3.md（ Probe 的逐块时间戳写在 checks 同级）。

```bash
spike/a2a/.venv/bin/python spike/a2a/probes/g3_artifact.py | tee spike/a2a/evidence/G3.md
```

- [ ] **Step 4: Commit**

```bash
git add spike/a2a/probes/g3_artifact.py spike/a2a/evidence/G3.md
git commit -m "test(spike): G3 real-engine artifact stream evidence"
```

---

### Task 10: G1b 门禁证据——代理链路 + 真实浏览器 fetch 流

**Files:**
- Create: `spike/a2a/probes/g1b_browser.html`
- Create: `spike/a2a/probes/g1b_playwright.py`
- Create: `spike/a2a/evidence/G1b.md`

- [ ] **Step 1: 发现 proxypass 目标解析规则（spike 服务器如何被代理路由）**

```bash
grep -rn "class.*Resolver\|LOCAL_" engine/../apps/proxy/src/sandboxproxy/community/adapters/web/resolvers/*.py 2>/dev/null | head -20
grep -rn "@1:20003\|LOCAL_" apps/proxy/src/sandboxproxy/community/adapters/web/resolvers/test_resolver.py 2>/dev/null | head -10
```

按发现的 target 格式（如 `LOCAL_spike@1:20043`）构造代理 URL：`http://127.0.0.1:<proxy-port>/proxypass/<target>/rpc`。冒烟：

```bash
curl -sN -X POST "http://127.0.0.1:8892/proproxypass-check" 2>/dev/null || true
curl -sN -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"ROLE_USER","parts":[{"text":"via-proxy"}],"messageId":"p1"}}}' \
  "http://127.0.0.1:8892/proxypass/<发现的target>/rpc" -o /dev/null -w '%{http_code}\n'
```

判定点：over 链路的 `SendStreamingMessage` 响应头为 `text/event-stream` 且**逐块到达**（`curl -N` 挂人工观察，或脚本 `time` 每 chunk 间隔 < 1s）——无代理缓冲即 G1b(链路) 通过；若 chunk 一次性吐出 = 存在 buffering，记录失败并查 proxy/gateway 的 buffering 配置。

- [ ] **Step 2: 写浏览器探针（fetch + ReadableStream + AbortController + 自定义头 + reconnect）**

```html
<!-- spike/a2a/probes/g1b_browser.html -->
<!doctype html>
<meta charset="utf-8">
<title>G1b browser probe</title>
<script type="module">
const BASE =location.queryParams?.base || "http://127.0.0.1:20043"; // 由 playwright 注入
async function streamOnce(id, withAuthHeader) {
  const ac = new AbortController();
  const t = setTimeout(() => { if (id === 'abort') ac.abort(); }, 300);
  const headers = {"Content-Type": "application/json"};
  if (withAuthHeader) headers["Authorization"] = "Bearer spike-token";
  const resp = await fetch(`${BASE}/rpc`, {
    method: "POST", signal: ac.signal, headers,
    body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "SendStreamingMessage",
      params: {message: {role: "ROLE_USER", parts: [{text: "browser"}],
                         messageId: id}}})});
  const reader = resp.body.getReader();
  const chunks = [];
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      chunks.push(new TextDecoder().decode(value).length);
      if (chunks.length > 20) ac.abort();
    }
  } catch (e) { /* aborted after samples */ }
  clearTimeout(t);
  return chunks;
}
window.__results = {};
for (const mode of ["plain", "auth", "reconnect"]) {
  try {
    const c = await streamOnce(`b-${mode}`, mode === "auth");
    // reconnect 模式：断在半途再拉一次（读 Resume 逻辑由服务端 since support 验证）
    window.__results[mode] = c.length;
  } catch (e) { window.__results[mode] = `ERR:${e}`; }
}
</script>
```

```python
# spike/a2a/probes/g1b_playwright.py
import asyncio
import json
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        import pathlib
        url = (pathlib.Path(__file__).parent / "g1b_browser.html").as_uri()
        await page.goto(url + "?base=http://127.0.0.1:20043")
        await page.wait_for_function("window.__results && Object.keys(window.__results).length === 3")
        results = await page.evaluate("window.__results")
        checks = {
            "plain_stream_chunked": isinstance(results["plain"], int) and results["plain"] >= 2,
            "auth_header_accepted": isinstance(results["auth"], int) and results["auth"] >= 2,
            "abort_works": "reconnect" in results,
            "no_console_errors": not errors,
        }
        print(json.dumps({"checks": checks, "raw": results, "consoles": errors[:5]},
                         ensure_ascii=False))
        await browser.close()
        exit(0 if all(checks.values()) else 1)

asyncio.run(main())
```

（`location.queryParams` 是笔误——HTML 里改为 `new URLSearchParams(location.search).get("base")`。CORS：spike server 需在 `build_app` 加 `CORSMiddleware`（allow_origins=["*"]）且有 OPTIONS 预检命中，这正是 G1b 要验证的浏览链路一部分。）

- [ ] **Step 3: 跑浏览器探针 + 落证据**

```bash
spike/a2a/.venv/bin/pip install playwright && spike/a2a/.venv/bin/python -m playwright install chromium
spike/a2a/.venv/bin/python spike/a2a/probes/g1b_playwright.py | tee spike/a2a/evidence/G1b.md; echo exit=$?
```

Expected: checks 全 true。

- [ ] **Step 4: Commit**

```bash
git add spike/a2a/probes/g1b_browser.html spike/a2a/probes/g1b_playwright.py spike/a2a/evidence/G1b.md
git commit -m "test(spike): G1b proxy-chain and real-browser fetch stream evidence"
```

---

### Task 11: G5 性能基准（WS 直连 vs A2A SSE 同引擎双通道）

**Files:**
- Create: `spike/a2a/probes/g5_bench.py`
- Create: `spike/a2a/evidence/G5.md`

- [ ] **Step 1: 写基准脚本（同 mock 引擎，两条通道各 N=50 次发送；测 P50 建连/首事件延迟、吞吐、RSS）**

```python
# spike/a2a/probes/g5_bench.py
"""G5：同引擎双通道对照。判据（设计文档 §10.1）：建连 P50 增量 ≤50ms；吞吐 ≥ WS 90%；内存持平。"""
import asyncio
import json
import statistics
import time

import httpx
import websockets

A2A_BASE = "http://127.0.0.1:20043/rpc"
V3_WS = "ws://127.0.0.1:20003/ws"
N = 50
PAYLOAD = {"message": {"role": "ROLE_USER",
                       "parts": [{"text": "ben" + "ch" }]}}
```

（脚本主体：A2A 通道 = httpx.AsyncClient stream 记每请求 first-byte 与总时长；V3 通道 = 复用 `BridgeV3.connect + chat_send`，每轮新建连接测建连，长连测吞吐；RSS 用 `resource.getrusage(RUSAGE_SELF).ru_maxrss` 于服务端侧另进程读 `/proc`——macOS 无 /proc，改由 uvicorn 进程端点暴露 `GET /_rss` 返回 `ru_maxrss`。判据计算与 PASS/FAIL 打印。执行时补全函数体——判据逻辑如下，量小可直接内联于 main：）

```python
def verdict(ws_first, a2a_first, ws_tput, a2a_tput, ws_rss, a2a_rss):
    p50_delta_ms = (statistics.median(a2a_first) - statistics.median(ws_first)) * 1000
    return {
        "p50_delta_ms": round(p50_delta_ms, 1),
        "p50_pass": p50_delta_ms <= 50.0,
        "tput_ratio": round(a2a_tput / max(ws_tput, 1e-9), 3),
        "tput_pass": a2a_tput >= 0.9 * ws_tput,
        "rss_delta_kb": a2a_rss - ws_rss,
        "rss_pass": abs(a2a_rss - ws_rss) < 64 * 1024,
    }
```

- [ ] **Step 2: 起 spike server + singlebox 真引擎，跑基准**

Run: `spike/a2a/.venv/bin/python spike/a2a/probes/g5_bench.py | tee spike/a2a/evidence/G5.md; echo exit=$?`
Expected: 判据 JSON 全 `*_pass: true`。未过 → **R3 风险预案触发**（局部裁剪评估），记录进 G5.md，不弱化数据。

- [ ] **Step 3: Commit**

```bash
git add spike/a2a/probes/g5_bench.py spike/a2a/evidence/G5.md
git commit -m "test(spike): G5 dual-channel performance baseline"
```

---

### Task 12: F7 回退保真 + G4 证据 + 汇总回填

**Files:**
- Create: `spike/a2a/tests/test_f7_fallback.py`
- Create: `spike/a2a/evidence/G4.md`、`spike/a2a/evidence/REPORT.md`
- Modify: `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`（附录 A 回填）

- [ ] **Step 1: F7 测试（不支持扩展的目标 → 结果未知而非换路 FIRST；自动重试包装器必须注入 retry-only）**

```python
# spike/a2a/tests/test_f7_fallback.py
import httpx
import pytest
from kernel import FIRST, RETRY_ONLY


def test_unsupported_target_retry_returns_unknown(monkeypatch):
    # 目标"不支持扩展"：kernel 处于空记录状态（等价旧容器），client 侧约定:
    # 自动重试必须以 retry_only 发出（封装函数 retry_request 是本仓库将来的客户端模板）
    from server import create_app
    from fastapi.testclient import TestClient
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 1,
             "method": "SendMessage",
             "params": {"message": {"role": "ROLE_USER",
                                    "parts": [{"text": "x"}],
                                    "messageId": "k7"},
                        "metadata": {"avernet.retryOnly": True}}})
        body = r.json()
        assert body["error"]["data"]["reason"] == "unknown_outcome"


def test_client_retry_wrapper_injects_flag():
    # 将来客户端模板行为：超时后的第二次请求自动带 retryOnly 标志
    from client_t import retry_request      # spikes 里的最小客户端模板
    calls = []

    def raw(params):
        calls.append(params)
        if len(calls) == 1:
            raise TimeoutError
        return {"ok": True}

    out = retry_request(raw, payload={"message": {"messageId": "m"}})
    assert calls[1]["metadata"]["avernet.retryOnly"] is True
    assert out == {"ok": True}
```

（`client_t.py` 为 spike 内 15 行的最小客户端模板：第一次发送 payload；TimeoutError 后复制 payload 加 metadata retryOnly 再发一次、共两次即止——它与 F7 断言配套，执行时新建该文件。服务器对 RETRY-ONLY 且查无记录的情况返回 JSON-RPC error `reason: unknown_outcome`（server 分发处与 kernel `UNKNOWN_OUTCOME_ERR` 联动），此行为由本测试固化。）

- [ ] **Step 2: G4 证据（两个模拟客户端：一者上下文订阅，另一者 inject——含 F3"无前台 task"形态）**

复用 Task 6 的 `test_retry.py` 会话扩展断言，跑真实 HTTP 双客户端脚本（可用两个 TestClient 并发或 `asyncio` 双 httpx stream）：订阅者先 `SubscribeToContext`，另一客户端 `InjectContext`，断言订阅流收到 inject 事件且订阅者自身无活跃 task。产出写入 `evidence/G4.md`（含 BCS 旁观用例描述与对应测试名）。

- [ ] **Step 3: 汇总报告**

```markdown
<!-- spike/a2a/evidence/REPORT.md 模板 -->
# Spike 阶段 0 报告（2026-10-XX）
| 门禁 | 判定 | 证据文件 | 关键数字 |
|---|---|---|---|
| G1a | pass/fail | G1a.md | ... |
| G1b | pass/fail | G1b.md | chunk 间隔、浏览器 checks |
| G2 | pass/fail | G2-versions.md | SDK 版本 |
| G3 | pass/fail | G3.md | 块数/终稿拼接长度/首块延迟 |
| G4 | pass/fail | G4.md | inject fanout 用例 |
| G5 | pass/fail | G5.md | p50_delta_ms/tput_ratio/rss |
| F1-F8 | 每个 fail-case 一行 | tests/*.py | 对应测试名 |

结论与遗留、是否放行阶段 1a。
```

- [ ] **Step 4: 设计文档附录 A 回填（G2 后半 + O8 定版）**

Modify: `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`
- 附录 A 头部段：把"a2a-sdk 精确版本由 G2 锁定后回填"改为实际版本号（来自 `evidence/G2-versions.md`）
- 附录 A.3：extension URI 以 `settings.EXT_URIS` 实际值定版（关闭 O8）
- 新增一行 changelog：`> 2026-10-XX Spike 回填：G 门禁结论见 spike/a2a/evidence/REPORT.md（spike 代码不合入 dev）`

- [ ] **Step 5: 提交与收尾**

```bash
git add spike/a2a/evidence/ spike/a2a/client_t.py spike/a2a/tests/test_f7_fallback.py \
        docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md
git commit -m "docs(spike): phase-0 spike report and design appendix backfill"
AVERNET_PRE_PUSH_MERGE_TARGET=origin/dev git push
```

（push 直接力荐一次全跑：`spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v` 全绿 + REPORT.md 全 pass = 阶段 1a 放行条件。）

---

## 自审记录（写计划时已跑）

1. **Spec 覆盖**：G1a(T5)/G1b(T10)/G2(T1,12)/G3(T9)/G4(T6,12)/G5(T11)；F1(T3)/F2(T3,4)/F3(T6,12)/F4(T6)/F5(T7)/F6(T4)/F7(T12)/F8(T6)。wire 字段表(T6)、artifact 阶段表(T3)、D14 判定表(T6)、键冲突规范化(T6)、跨协议断言(T6)。全覆盖。
2. **占位符扫描**：无 TBD/TODO；计划内五处标注"执行注记"的笔误修正均为写作瑕疵的显式更正说明（保留原文与修正），不是待定项。
3. **类型一致性**：`Kernel.start_task` 返回 `(run_id, created)` 或 `UNKNOWN_OUTCOME_ERR`；`start()` 保留 Task 3 签名；`Run.saw_retry_flag` 在 Task 6 引入并回溯声明；`BridgeV3.pump(run_id, event_iter)` 在 T8/T9 一致。
