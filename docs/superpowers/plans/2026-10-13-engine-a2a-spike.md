# Engine→A2A 协议替换 · 阶段 0 Spike 实施计划（v3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出 A2A v1.0（JSON-RPC 绑定）替换 engine v3 WS 信道的 Spike 证据：门禁 G1a-G5 全部可判、F1-F8 全部可复跑；**每个 pass 必须真实证明它声称的契约**。

**Architecture:** 旁置 Spike harness（`spike/a2a/`，独立 venv，永不合入 dev）。kernel 按设计文档 §8 契约实现（统一订阅、锁内交接、D14 原子判定、慢订阅者踢除、统一收口）；A2A 壳把 kernel 私有事件**转换为标准 StreamResponse wire**（`{seq,type,ev}` 不出 kernel）；v3 迷你服务器与 A2A 壳共享 kernel 且同为异步执行路径（G5 对照口径一致）；故障代理带 HTTP 控制端点（跨进程可控，屏障 = state.forwarded）；mock 引擎带可配置慢速节奏（G1b 无缓冲证明）。

**Tech Stack:** Python 3.12、a2a-sdk（G2 锁版）、fastapi + uvicorn、httpx、websockets、pytest + pytest-asyncio、Playwright。

**上游文档:** `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`（V5 通过版；本计划 v3.1 吸收计划评审 R6 全 8 项 + R7 全 6 P1 + 2 实现项 + 3 环境项 + R8 全 6 P1 + 2 P2——R8 修订方式：**事件 DTO/digest 统一、F5/F7 片段运行错误、G1a 验收器洞、G1b/G5 判据收紧全部以 harness 真实跑绿为准（47 passed）**，非文档层声明）
**分支:** `feat/engine-a2a-protocol-replacement`
**端口:** 20043=A2A 壳 / 20044=v3 迷你 / 20045=F5 被测 / 20046=故障代理 / 20047=echo 引擎 / 20048=壳(桥测试) / 20049=legacy A2A / 20050=F7 代理 / 20003=singlebox 真引擎

---

## 文件结构

```
spike/a2a/
├── .venv/  .gitignore
├── requirements-spike.txt      # 含 pytest/pytest-asyncio 全部复跑依赖
├── settings.py  kernel.py  engine_mock.py  server.py  sse.py
├── v3_server.py                # 迷你 v3（共享 kernel；异步执行；legacy 模式）
├── v3_echo_engine.py           # 纯帧成型对端（bridge 契约测试，无 kernel）
├── bridge_v3.py  client_t.py  fault_proxy.py
├── probes/
│   ├── g1a_validate.py  g1a_sdk.py  g3_realtime.py
│   ├── g1b_browser.html  g1b_playwright.py  g5_protocol.py  report.py
├── tests/
│   ├── conftest.py  test_rpc.py  test_kernel.py  test_stream.py
│   ├── test_g1a_validator.py  test_retry.py  test_f5_restart.py
│   └── test_v3_f7_f8.py  test_bridge.py
└── evidence/                   # 机器生成（G2.json、G1a.json、G3.json、G1b.json、G4.json、G5.json、REPORT.md）
```

**不变量（跨任务一致，违反即返工）：**
1. **wire 边界（R7-1）**：kernel 事件 `{seq,type,ev}` 私有封装不出 kernel；A2A 壳经 `_wire()` 输出标准 StreamResponse——`result` 恰含其一 `task`/`message`/`statusUpdate`/`artifactUpdate`；seq 位于成员 `metadata.seq`；终态 statusUpdate 带 `final: true`
2. 订阅生命周期（R7-7）：流结束/断开 finally 移除订阅队列；QueueFull 踢除慢订阅者，不影响执行与其他订阅者
3. 幂等键 = `(contextId, messageId)`（R7-2）；**客户端契约：调用方必须提供稳定 contextId**（丢失响应后的关联 = 同 context+messageId）；server 对缺失 contextId 显式 `-32602 context_required`
4. kernel `last_seq` 独立于事件缓冲；计数/字节/全局任一预算越界 → 显式 `cache_limit` 拒回放，投递不中断（R7-7）
5. 判据预声明（R7-6）：G5 阈值与口径、G1b 时序断言先写死在探针内；rss 样本缺失 → verdict=not_run
6. 故障代理 HTTP 控制端点跨进程控制（R7-3）；同步屏障 = `state.forwarded`（上游响应已在代理手中）
7. 统一任务收口（R7-8）：`complete_run()` 置状态→发布终态→finish；bridge 复用同一方法

---

### Task 1: 环境与依赖锁定（G2）

**Files:** Create `requirements-spike.txt`、`evidence/G2-versions.md`、`evidence/G2.json`；Modify `.gitignore`

- [ ] **Step 1: venv 与目录**

```bash
mkdir -p spike/a2a/{evidence,tests,probes}
python3 -m venv spike/a2a/.venv
spike/a2a/.venv/bin/pip install -U pip
```

- [ ] **Step 2: 安装并整锁依赖（asyncio 测试依赖一并锁入——R7 环境项）**

```bash
spike/a2a/.venv/bin/pip install "a2a-sdk[http-server]" fastapi uvicorn httpx \
  websockets pytest pytest-asyncio
spike/a2a/.venv/bin/pip freeze > spike/a2a/requirements-spike.txt
grep -cE '^(pytest=|pytest-asyncio|a2a-sdk|fastapi==|uvicorn==|httpx==|websockets==)' \
  spike/a2a/requirements-spike.txt
```

预期 ≥7 行；<7 = 锁失败。**a2a-sdk 安装失败 = G2 早退**（G2.json 记 fail，回设计文档 D4 降级分支重评审），不允许跳过。

- [ ] **Step 3: SDK API 面备忘（发现什么记什么；T5 的 normalize 契约据此实现）**

```bash
grep -rn "class .*Resolver\|def get_agent_card\|def send_message\|def send_streaming" \
  spike/a2a/.venv/lib/python*/site-packages/a2a/ 2>/dev/null | head -8
```

输出原样存 `evidence/G2-versions.md`。

- [ ] **Step 4: 机器生成 G2.json（report.py 聚合 G*.json——R7 环境项）**

```bash
SDKV=$(spike/a2a/.venv/bin/pip show a2a-sdk | awk '/^Version/{print $2}')
printf '{"gate": "G2", "verdict": "pass", "evidence_level": "toolchain", "a2a_sdk_version": "%s"}\n' \
  "$SDKV" > spike/a2a/evidence/G2.json
cat spike/a2a/evidence/G2.json
```

- [ ] **Step 5: gitignore + 提交**

```bash
printf 'spike/a2a/.venv/\nspike/a2a/**/__pycache__/\nspike/a2a/evidence_upstream_calls.jsonl\n' >> .gitignore
git add .gitignore spike/a2a/requirements-spike.txt spike/a2a/evidence/
git commit -m "chore(spike): lock toolchain incl test deps, machine G2.json"
```

---

### Task 2+3: card`/rpc` 骨架 + kernel（同批提交）

**Files:** Create `settings.py`、`kernel.py`、`engine_mock.py`、`server.py`、`tests/conftest.py`、Test `test_rpc.py`、`test_kernel.py`

- [ ] **Step 1: 失败测试**

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
CTX = "fixed-ctx-rpc"


def _params(mid, text, metadata=None, ctx=CTX):
    return {"message": {"role": "ROLE_USER", "parts": [{"text": text}],
                        "messageId": mid, "contextId": ctx},
            "metadata": metadata or {}}


def test_card_wellknown_shape(client):
    card = client.get("/.well-known/agent-card.json").json()
    iface = card["supportedInterfaces"][0]
    assert iface["protocolBinding"] == "JSONRPC" and iface["protocolVersion"] == "1.0"
    assert iface["url"].endswith("/rpc")
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["tags"] == ["conversation"]


def test_send_message_jsonrpc_envelope_with_fixed_context(client):
    r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 11,
                                  "method": "SendMessage",
                                  "params": _params("rpc-m1", "hi")})
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 11
    task = body["result"]["task"]
    assert task["contextId"] == CTX                     # 幂等作用域固定（R7-2）
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"


def test_missing_context_rejected(client):             # 不变量 3 的服务端面
    r = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 13, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                               "messageId": "noctx"}, "metadata": {}}})
    assert r.json()["error"]["data"]["reason"] == "context_required"


def test_unknown_method_jsonrpc_error(client):
    r = client.post("/rpc", json={"jsonrpc": "2.0", "id": 12,
                                  "method": "NoSuch"})
    assert r.json()["error"]["code"] == -32601
```

```python
# spike/a2a/tests/test_kernel.py
import asyncio
import pathlib
import tempfile

from kernel import (Kernel, SUBSCRIBER_QUEUE_SIZE, MAX_BYTES_PER_RUN,
                    MAX_EVENTS_PER_RUN, TERMINAL_TTL_SECONDS, TOTAL_BUDGET)


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
    assert seqs == sorted(set(seqs))
    assert evs[0]["type"] == "task"
    states = [e["ev"]["statusUpdate"]["status"]["state"] for e in evs
              if e["type"] == "statusUpdate"]
    assert states[-1] == "TASK_STATE_COMPLETED"


def test_artifact_stage_table():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kk2", "first", "d")
        await k.execute_message(rid)
        backlog, _q = await k.subscribe(rid, since_seq=0)
        return [e["ev"]["artifactUpdate"] for e in backlog
                if e["type"] == "artifactUpdate"]
    arts = run(main())
    assert (arts[0]["append"], arts[0]["lastChunk"]) == (False, False)
    assert all((a["append"], a["lastChunk"]) != (True, True) for a in arts[1:-1])
    assert (arts[-1]["append"], arts[-1]["lastChunk"]) == (False, True)


def test_byte_budget_overflow_sets_cache_limit():      # R7-7：字节越界也置位
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kb", "first", "d")
        k.runs[rid].bytes_ = MAX_BYTES_PER_RUN - 1      # 构造预算边缘
        await k.publish_status(rid, f"pad-{'x' * 4096}")
        return k, rid
    k, rid = run(main())
    assert k.runs[rid].cache_limit is True
    assert run(k.replay(rid, since_seq=0)) is None      # 显式拒绝，不出半截历史


def test_total_budget_overflow_sets_cache_limit():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kt", "first", "d")
        k.total_bytes = TOTAL_BUDGET                    # 全局预算已尽
        await k.publish_status(rid, "TASK_STATE_WORKING")
        return k, rid
    k, rid = run(main())
    assert k.runs[rid].cache_limit is True


def test_cache_limit_stops_caching_not_delivery():     # F1
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kc", "first", "d")
        r = k.runs[rid]
        for _ in range(MAX_EVENTS_PER_RUN + 3):
            await k.publish_status(rid, "TASK_STATE_WORKING")
        assert r.cache_limit is True
        assert await k.replay(rid, since_seq=0) is None
        backlog, q = await k.subscribe(rid, since_seq=0)
        assert backlog == []
        await k.publish_status(rid, "TASK_STATE_COMPLETED")
        got = await asyncio.wait_for(q.get(), 1)
        return got, r
    got, r = run(main())
    assert got["ev"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert r.last_seq > MAX_EVENTS_PER_RUN             # seq 不随缓存清零


def test_slow_subscriber_kicked_with_termination_signal():  # R7-7/R8-7 后半
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "ks", "first", "d")
        _backlog, slow = await k.subscribe(rid, since_seq=0)
        junk = {"seq": None, "type": "junk", "ev": {}}
        for _ in range(SUBSCRIBER_QUEUE_SIZE):          # 恰好灌满（不越界自爆）
            slow.put_nowait(junk)
        await k.execute_message(rid)                    # 执行照常；踢除+灭活的信号
        return k, rid, slow

    async def drain(slow):                              # 消费方有限时间内结束
        while True:
            ev = await asyncio.wait_for(slow.get(), 5)
            if ev is None:
                return True

    k, rid, slow = run(main())
    assert k.runs[rid].state == "TASK_STATE_COMPLETED"  # 被踢不影响执行
    assert slow not in k.runs[rid].subscribers
    assert run(drain(slow)) is True                     # 流有限时间内结束


def test_completion_marker_survives_queue_boundary():  # R9-2/R10-1 复现
    """真边界：q 由 subscribe 新建（backlog 承载历史，q 从 0 起）→
    预填 SIZE-1 → 终态 status 占满 → marker 撞 QueueFull：终止必达。"""
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kq2", "first", "d")
        _backlog, q = await k.subscribe(rid, since_seq=0)   # backlog 持历史，q 空
        junk = {"seq": None, "type": "junk", "ev": {}}
        for _ in range(SUBSCRIBER_QUEUE_SIZE - 1):     # 预填至差一位（R10-1）
            q.put_nowait(junk)
        assert q.qsize() == SUBSCRIBER_QUEUE_SIZE - 1  # 前置边界断言
        await k.complete_run(rid)                      # 终态 status 添满 → marker 撞满
        return rid, q

    async def consume(q):
        """消费边界队列直到有限终止：final 标记或 None 关流信号。
        若实现退回"满队列吞掉 marker"（评审变异验证），final 永不出现 →
        q.get() 超时 → 测试失败。"""
        while True:
            ev = await asyncio.wait_for(q.get(), 5)
            if ev is None:
                return False
            su = ev.get("ev", {}).get("statusUpdate", {})
            meta = (su.get("metadata") or {})
            if meta.get("final"):
                assert meta["lastSeq"] >= 1             # R9-1：标记承载数
                return True
    rid, q = run(main())
    assert run(consume(q)) is True                     # 有限时间耗尽且见终态


def test_standard_subscribe_on_live_task():            # R8 P2-8：活任务标准订阅
    async def main():
        import engine_mock
        gate = asyncio.Event()
        engine_mock.PAUSE_GATE = gate
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kl", "first", "d")
        executing = asyncio.create_task(k.execute_message(rid))
        await asyncio.sleep(0.05)
        backlog, q = await k.subscribe(rid)             # 快照订阅（活任务）
        gate.set()
        await executing
        first = await asyncio.wait_for(q.get(), 5)
        evs = [first]
        while True:
            ev = await asyncio.wait_for(q.get(), 5)
            evs.append(ev)
            su = ev["ev"].get("statusUpdate", {})
            if (su.get("metadata") or {}).get("final"):
                break
        engine_mock.PAUSE_GATE = None
        return first, evs, k.runs[rid].state
    first, evs, state = run(main())
    assert first["type"] == "task"                      # 快照=当时状态
    assert first["ev"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert (evs[-1]["ev"]["statusUpdate"].get("metadata") or {}).get("final") is True
    assert state == "TASK_STATE_COMPLETED"


def test_terminal_replay_window_is_180s_not_immediate():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kw", "first", "d")
        await k.execute_message(rid)
        out = await k.replay(rid, since_seq=0)
        return k, rid, out is not None
    k, rid, immediate_ok = run(main())
    assert immediate_ok
    k.runs[rid].replay_expires_at -= TERMINAL_TTL_SECONDS + 1
    assert run(k.replay(rid, since_seq=0)) is None


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


def test_f6_live_handoff_no_gap():                     # R7 F6：交接期发布的事件
    async def main():
        import engine_mock
        gate = asyncio.Event()
        engine_mock.PAUSE_GATE = gate
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kh", "first", "d")
        executing = asyncio.create_task(k.execute_message(rid))
        await asyncio.sleep(0.05)                       # 引擎停在第二块前
        backlog, q = await k.subscribe(rid, since_seq=0)   # 锁内交接
        gate.set()                                      # set() 非 awaitable
        await executing
        live = []
        while True:
            ev = await asyncio.wait_for(q.get(), 5)
            su = ev["ev"].get("statusUpdate", {}) if ev else {}
            if ev is None or (su.get("metadata") or {}).get("final"):
                break
            live.append(ev)
        engine_mock.PAUSE_GATE = None
        return backlog, live, k.runs[rid].state
    backlog, live, state = run(main())
    seqs = [e["seq"] for e in backlog] + [e["seq"] for e in live]
    assert seqs == sorted(set(seqs))                    # 无重复
    assert seqs == list(range(seqs[0], seqs[-1] + 1))   # 无缺号
    assert live and state == "TASK_STATE_COMPLETED"     # 到达真实终态（终态 marker 见 metadata.final）


def test_standard_subscribe_snapshot_then_terminal_marker():
    async def main():
        k = Kernel(engine="mock")
        rid, _ = await k.open_run("c", "kss", "first", "d")
        await k.execute_message(rid)
        backlog, q = await k.subscribe(rid)             # since=None
        first = await asyncio.wait_for(q.get(), 1)
        second = await asyncio.wait_for(q.get(), 1)
        return backlog, first, second
    backlog, first, second = run(main())
    assert backlog == []
    assert first["type"] == "task"                      # §3.1.6 快照
    assert first["ev"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    su2 = second["ev"]["statusUpdate"]
    assert (su2.get("metadata") or {}).get("final") is True
    marker_seq = (su2.get("metadata") or {}).get("lastSeq")
    assert marker_seq is not None and marker_seq >= 1   # 标记承接最终序号


def test_f8_concurrent_first_single_execution():       # R7 F8：并发 + 执行级断言
    tmp = pathlib.Path(tempfile.mkdtemp()) / "calls.jsonl"

    async def main():
        k = Kernel(engine="mock", calls_path=str(tmp))
        outs = await asyncio.gather(
            k.send_message("cctx", "m", "kc", "first"),
            k.send_message("cctx", "m", "kc", "first"))
        return outs
    outs = run(main())
    assert outs[0]["task"]["id"] == outs[1]["task"]["id"]
    assert sum(1 for _ in tmp.open()) == 1              # 上游恰好执行一次


def test_d14_rejects_retry_only_without_record():
    async def main():
        k = Kernel(engine="mock")
        return await k.send_message("c8", "m", "absent", "retry_only")
    assert run(main()) is Kernel.UNKNOWN_OUTCOME
```

- [ ] **Step 2: 确认失败**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_rpc.py spike/a2a/tests/test_kernel.py -v`
Expected: FAIL（`No module named`）

- [ ] **Step 3: 实现 settings / kernel / engine_mock / server**

```python
# spike/a2a/settings.py
HOST, PORT = "127.0.0.1", 20043
EXT_BASE = "https://avernet.local/a2a/ext"
EXT_URIS = {"inject": f"{EXT_BASE}/inject/v1",
            "context": f"{EXT_BASE}/context/v1",
            "replay": f"{EXT_BASE}/replay/v1",
            "retry": f"{EXT_BASE}/retry/v1"}    # retry 惟能力发现声明用
```

```python
# spike/a2a/kernel.py
"""Spike kernel：设计文档 §8 契约的最小实现（全 async、单循环、单事件循环边界）。"""
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
            "status": {"state": r.state}, "metadata": {}}


def _terminal_marker(r) -> dict:
    return {"seq": None, "type": "statusUpdate",
            "ev": {"statusUpdate": {
                "taskId": r.run_id, "contextId": r.context_id,
                "status": {"state": r.state},
                "metadata": {"final": True,
                             "lastSeq": r.last_seq}}}}  # R11-G1a：final 入 metadata


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
        self.last_seq = 0                          # 不变量 4：独立于缓冲
        self.saw_delta = False                     # artifact 阶段表首块判定
        self.cache_limit = False
        self.subscribers: list[asyncio.Queue] = []
        self.lock = asyncio.Lock()
        self.replay_expires_at: float | None = None
        self.idempotency_key = None
        self.request_digest = None


class Kernel:
    UNKNOWN_OUTCOME = UnknownOutcome()

    def __init__(self, engine="mock", calls_path=None, legacy=False):
        self.engine = engine
        self.calls_path = calls_path
        self.legacy = legacy                        # legacy：忽略 retry 标志
        self.runs: dict[str, Run] = {}
        self.idem: dict[tuple[str, str], str] = {}
        self.inject_dedup: dict[tuple[str, str], float] = {}
        self.context_subs: dict[str, list[asyncio.Queue]] = {}
        self.total_bytes = 0
        self._gl = asyncio.Lock()

    # ---- D14 原子入口（不变量 3） ----
    async def open_run(self, context_id, idempotency_key, mode,
                       request_digest=None):
        if context_id is None:
            raise ValueError("contextId required")
        async with self._gl:
            rid = self.idem.get((context_id, idempotency_key))
            if rid is not None and rid in self.runs:
                r = self.runs[rid]
                if r.request_digest != request_digest:
                    raise KeyConflictError((context_id, idempotency_key))
                return rid, False
            if mode == "retry_only" and self.legacy:
                mode = "first"                      # 旧容器语义：照常执行
            if mode == "retry_only":
                return self.UNKNOWN_OUTCOME         # 记录不在：不创建执行
            if len(self.runs) >= MAX_RUNS:
                self._evict_oldest_finished()
            rid = uuid.uuid4().hex[:12]
            r = Run(rid, context_id)
            r.idempotency_key = idempotency_key
            r.request_digest = request_digest
            self.runs[rid] = r
            self.idem[(context_id, idempotency_key)] = rid
        await self._publish(rid, "task", {"task": _task_obj(r)})
        return rid, True

    async def execute_message(self, rid):
        self._record_upstream(self.runs[rid].idempotency_key)
        from engine_mock import run_engine
        r = self.runs[rid]
        r.state = "TASK_STATE_WORKING"
        await self._publish(rid, "statusUpdate", {"statusUpdate": {
            "taskId": rid, "contextId": r.context_id,
            "status": {"state": r.state}}})

        async def emit(kind, payload):
            await self._forward(rid, kind, payload)

        await run_engine(rid, emit)
        await self.complete_run(rid)

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

    # ---- 统一收口（不变量 7，R7-8） ----
    async def complete_run(self, rid, state="TASK_STATE_COMPLETED"):
        r = self.runs[rid]
        r.state = state
        await self._publish(rid, "statusUpdate", {"statusUpdate": {
            "taskId": rid, "contextId": r.context_id,
            "status": {"state": state}}})
        r.replay_expires_at = time.monotonic() + TERMINAL_TTL_SECONDS
        for q in list(r.subscribers):
            self._deliver_terminal(r, q, _terminal_marker(r))
        asyncio.get_running_loop().call_later(
            TERMINAL_TTL_SECONDS, self._evict, rid)

    def _deliver_terminal(self, r, q, item):
        """终止信号必达（R9-2）：满队列时踢除该订阅并腾位重投。
        即便踢除失败（消费端死等），信号也不会静默丢失。"""
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

    def _record_upstream(self, key):
        if self.calls_path:
            with open(self.calls_path, "a") as f:
                f.write(json.dumps({"key": key, "ts": time.time()}) + "\n")

    def _evict(self, rid):
        r = self.runs.pop(rid, None)
        if r:
            self.total_bytes -= r.bytes_
            self.idem.pop((r.context_id, r.idempotency_key), None)
            for q in list(r.subscribers):
                self._deliver_terminal(r, q, None)  # 流终止信号（必达）

    def _try_put(self, q, item):
        """普通投递满队列不抛错（R7-7）；终止信号走 _deliver_terminal。"""
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def _evict_oldest_finished(self):
        for rid, r in sorted(self.runs.items(),
                             key=lambda kv: kv[1].replay_expires_at or 0):
            if r.replay_expires_at is not None:
                self._evict(rid)
                return

    # ---- 发布（锁内；预算越界显式置位；慢订阅者踢除） ----
    async def _publish(self, rid, ev_type, ev):
        r = self.runs[rid]
        async with r.lock:
            r.last_seq += 1
            wrapped = {"seq": r.last_seq, "type": ev_type, "ev": ev}
            blob = len(json.dumps(wrapped, ensure_ascii=False))
            if not r.cache_limit:
                fits = (len(r.events) < MAX_EVENTS_PER_RUN
                        and r.bytes_ + blob <= MAX_BYTES_PER_RUN
                        and self.total_bytes + blob <= TOTAL_BUDGET)
                if fits:
                    r.events.append(wrapped)
                    r.bytes_ += blob
                    self.total_bytes += blob
                else:                               # 计数/字节/全局任一越界
                    self.total_bytes -= r.bytes_
                    r.events = []
                    r.bytes_ = 0
                    r.cache_limit = True
            for q in list(r.subscribers):
                try:
                    q.put_nowait(wrapped)
                except asyncio.QueueFull:           # 不变量 2：踢除慢订阅者
                    self._deliver_terminal(r, q, None)   # 有限结束（R8-7/R9-2）

    async def _forward(self, rid, kind, payload):
        r = self.runs[rid]
        if kind == "message_delta":
            first = not r.saw_delta
            r.saw_delta = True
            await self._publish(rid, "artifactUpdate", {"artifactUpdate": {
                "taskId": rid, "contextId": r.context_id,
                "artifact": {"artifactId": f"text-{rid}",
                             "parts": [{"text": payload["text"]}]},
                "append": not first,                # §8.4 首块 (F,F)
                "lastChunk": False}})
        elif kind == "final":
            await self._publish(rid, "artifactUpdate", {"artifactUpdate": {
                "taskId": rid, "contextId": r.context_id,
                "artifact": {"artifactId": f"text-{rid}",
                             "parts": [{"text": payload["text"]}]},
                "append": False, "lastChunk": True}})
        elif kind == "tool_progress":
            await self._publish(rid, "statusUpdate", {"statusUpdate": {
                "taskId": rid, "contextId": r.context_id,
                "status": {"state": r.state}, "metadata": payload}})

    async def publish_status(self, rid, state):
        self.runs[rid].state = state
        await self._publish(rid, "statusUpdate", {"statusUpdate": {
            "taskId": rid, "contextId": self.runs[rid].context_id,
            "status": {"state": state}}})

    # ---- 统一订阅（锁内交接） ----
    async def subscribe(self, rid, since_seq=None):
        r = self.runs.get(rid)
        if r is None:
            raise LookupError(rid)
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        async with r.lock:                          # 与 _publish 共持 → 无窗口
            backlog = ([] if since_seq is None else
                       [e for e in r.events if e["seq"] > since_seq])
            r.subscribers.append(q)
            if since_seq is None:
                q.put_nowait({"seq": None, "type": "task",
                              "ev": {"task": _task_obj(r)}})
        if r.state in TERMINAL_STATES:
            q.put_nowait(_terminal_marker(r))
        return backlog, q

    async def replay(self, rid, since_seq):
        r = self.runs.get(rid)
        if r is None or r.cache_limit:
            return None
        if (r.replay_expires_at is not None
                and time.monotonic() > r.replay_expires_at):
            return None
        return await self.subscribe(rid, since_seq=since_seq)

    async def cancel(self, rid):
        r = self.runs.get(rid)
        if r and r.state not in TERMINAL_STATES:
            await self.complete_run(rid, "TASK_STATE_CANCELED")
        return {"task": _task_obj(r)} if r else None

    # ---- inject：非 Task + 操作级去重（§8.3） ----
    async def inject(self, context_id, text, inject_id):
        key = (context_id, inject_id)
        if key in self.inject_dedup:
            return "duplicate"
        self.inject_dedup[key] = time.time()
        for q in list(self.context_subs.get(context_id, [])):
            try:
                q.put_nowait({"seq": None, "type": "inject",
                              "ev": {"message": {
                                  "messageId": inject_id,
                                  "contextId": context_id,
                                  "parts": [{"text": text}]}}})
            except asyncio.QueueFull:
                pass
        return "accepted"

    def unsubscribe_context(self, context_id, q):
        subs = self.context_subs.get(context_id, [])
        if q in subs:
            subs.remove(q)

    def subscribe_context(self, context_id):
        q = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self.context_subs.setdefault(context_id, []).append(q)
        return q
```

```python
# spike/a2a/engine_mock.py
import asyncio
import os

CHUNKS = ["第一段", "第二段增量", "第三段增量"]
PAUSE_GATE = None                      # 测试注入 asyncio.Event：第二块前暂停
INTER_CHUNK_DELAY = float(os.environ.get("SPIKE_SLOW_MS", "0"))


async def run_engine(run_id: str, emit) -> None:
    for i, chunk in enumerate(CHUNKS):
        if PAUSE_GATE is not None and i == 1:
            await PAUSE_GATE.wait()
        if INTER_CHUNK_DELAY:
            await asyncio.sleep(INTER_CHUNK_DELAY / 1000)
        await emit("statusUpdate", {"state": "TASK_STATE_WORKING"})
        await emit("message_delta", {"text": chunk})
        if i == 0:
            await emit("tool_progress", {"tool": "spike-tool", "phase": "run"})
    await emit("final", {"text": "".join(CHUNKS) + "（终稿）"})
```

```python
# spike/a2a/server.py（T2 骨架；wire/流式 handler 在 T4 追加到同一 HANDLED）
import os
import uuid

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

import settings
from kernel import KeyConflictError


def make_card(base: str, legacy: bool = False) -> dict:
    exts = [u for name, u in settings.EXT_URIS.items()
            if not (legacy and name == "retry")]    # legacy 不声明 retry 扩展
    return {"name": "a2a-spike", "description": "v3->A2A spike",
            "version": "0.0.0",
            "supportedInterfaces": [{"url": f"{base}/rpc",
                                     "protocolBinding": "JSONRPC",
                                     "protocolVersion": "1.0"}],
            "capabilities": {"streaming": True, "pushNotifications": False,
                             "extensions": [{"url": u, "required": False}
                                            for u in exts]},
            "skills": [{"id": "conversation", "name": "Conversation",
                        "description": "spike conversation",
                        "tags": ["conversation"]}],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain", "application/json"]}


def build_app(kernel, legacy: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.kernel = kernel
    app.state.legacy = legacy

    @app.get("/.well-known/agent-card.json")
    def card():
        return make_card(f"http://{settings.HOST}:{settings.PORT}", legacy)

    @app.post("/rpc")
    async def rpc(request: Request):
        body = await request.json()
        rid, method = body.get("id"), body.get("method")
        params = body.get("params") or {}
        handler = HANDLED.get(method)
        if handler is None:
            return JSONResponse({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601,
                          "message": f"Method not found: {method}"}})
        try:
            outcome = await handler(kernel, rid, params)
        except BadRetryFlag:
            return JSONResponse({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32602,
                          "message": "Invalid params: avernet.retryOnly",
                          "data": {"reason": "malformed_retry_flag"}}})
        except KeyConflictError:
            return JSONResponse({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32003,
                          "data": {"reason": "key_conflict"}}})
        if isinstance(outcome, Response):            # 流式/错误响应直接透传
            return outcome
        return {"jsonrpc": "2.0", "id": rid, "result": outcome}

    return app


class BadRetryFlag(Exception):
    pass


HANDLED = {}


def _retry_flag(params: dict) -> str:
    meta = params.get("metadata")
    if not meta or "avernet.retryOnly" not in meta:
        return "first"
    flag = meta["avernet.retryOnly"]
    if flag is not True and flag is not False:
        raise BadRetryFlag()
    return "retry_only" if flag else "first"


def _text(msg: dict) -> str:
    return "".join(p.get("text", "") for p in msg.get("parts", []))


def _context_error(rid):
    return JSONResponse({"jsonrpc": "2.0", "id": rid,
        "error": {"code": -32602, "message": "Invalid params: contextId",
                  "data": {"reason": "context_required"}}})


async def send_message(kernel, rid, params):
    msg = params["message"]
    if not msg.get("contextId"):                     # 不变量 3：必须显式提供
        return _context_error(rid)
    out = await kernel.send_message(msg["contextId"], _text(msg),
                                    msg.get("messageId"), _retry_flag(params))
    if out is kernel.UNKNOWN_OUTCOME:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000, "message": "unknown outcome",
                      "data": {"reason": "unknown_outcome"}}})
    return out


HANDLED["SendMessage"] = send_message


def create_app():
    from kernel import Kernel
    kernel = Kernel(engine="mock",
                     legacy=os.environ.get("SPIKE_LEGACY") == "1",
                     calls_path=os.environ.get("SPIKE_CALLS"))
    return build_app(kernel, legacy=kernel.legacy)
```

- [ ] **Step 4: 确认通过 + 提交**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_rpc.py spike/a2a/tests/test_kernel.py -v`
Expected: 全 PASS

```bash
git add spike/a2a/settings.py spike/a2a/kernel.py spike/a2a/engine_mock.py \
  spike/a2a/server.py spike/a2a/tests/
git commit -m "feat(spike): kernel budgets/kick/clean-complete + context-scoped idempotency"
```

---

### Task 4: 流式三通道（标准 wire DTO + 路由 Response 透传——R7-1）

**Files:** Create `sse.py`、Test `test_stream.py`、Modify `server.py`

- [ ] **Step 1: 失败测试（断言标准 StreamResponse 成员，不再断言私有 {type,ev}；验证路由透传与订阅清理）**

```python
# spike/a2a/tests/test_stream.py
import json

CTX = "fixed-ctx-stream"
KINDS = ("task", "message", "statusUpdate", "artifactUpdate")


def _message(mid, ctx=CTX):
    return {"role": "ROLE_USER", "parts": [{"text": "hi"}],
            "messageId": mid, "contextId": ctx}


def _kind(e):
    return next(k for k in KINDS if k in e["result"])


def _collect(resp):
    events = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_send_streaming_standard_wire_lifecycle(client):
    with client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 21, "method": "SendStreamingMessage",
            "params": {"message": _message("s1")}}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        evs = _collect(resp)
    kinds = [_kind(e) for e in evs]                    # 标准 wire 成员
    assert kinds[0] == "task"                          # 首事件 Task
    for e in evs:                                      # 每条恰含一个合法成员
        members = [k for k in KINDS if k in e["result"]]
        assert len(members) == 1, e["result"]
    assert kinds[-1] == "statusUpdate"                 # 流终于终态 status
    last = evs[-1]["result"]["statusUpdate"]
    assert last["status"]["state"] == "TASK_STATE_COMPLETED"
    assert (last.get("metadata") or {}).get("final") is True
    arts = [e["result"]["artifactUpdate"] for e in evs
            if "artifactUpdate" in e["result"]]
    assert arts and arts[-1]["append"] is False        # 终稿替换 (F,T)
    assert arts[-1]["lastChunk"] is True
    seqs = []                                          # 按到达序单遍提取（R8-4）
    for e in evs:
        for k in ("statusUpdate", "artifactUpdate"):
            if k in e["result"]:
                seq = (e["result"][k].get("metadata") or {}).get("seq")
                if seq is not None:
                    seqs.append(seq)
    assert seqs == sorted(set(seqs))                   # 交错流亦单调无重号


def test_subscribe_task_terminal_rejected_use_replay(client):
    """R8 P2-8：标准 SubscribeToTask 面向活任务；终态走 ReplayEvents。"""
    send = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 24, "method": "SendMessage",
        "params": {"message": _message("s3")}}).json()
    tid = send["result"]["task"]["id"]
    r = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 25, "method": "SubscribeToTask",
        "params": {"taskId": tid, "contextId": CTX}})
    assert r.json()["error"]["data"]["reason"] == "task_terminal_use_replay"


def test_replay_events_terminal_window_returns_stream(client):
    """终态任务窗口内恢复走 ReplayEvents（设计 §8.1 路由三）。"""
    send = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 27, "method": "SendMessage",
        "params": {"message": _message("s5")}}).json()
    tid = send["result"]["task"]["id"]
    with client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 28, "method": "ReplayEvents",
            "params": {"taskId": tid, "sinceSeq": 0, "contextId": CTX}}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        evs = _collect(r)
    kinds = [_kind(e) for e in evs]
    assert kinds[0] == "task"
    assert kinds[-1] == "statusUpdate"
    assert (evs[-1]["result"]["statusUpdate"].get("metadata")
            or {}).get("final") is True


def test_stream_cleanup_after_end(client):            # R7-7：订阅队列清理
    with client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 26, "method": "SendStreamingMessage",
            "params": {"message": _message("s4")}}) as resp:
        _ = _collect(resp)
    kernel = client.app.state.kernel
    assert all(not r.subscribers for r in kernel.runs.values())


def test_streaming_retry_only_rejected(client):
    r = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 35, "method": "SendStreamingMessage",
        "params": {"message": _message("rt-s"),
                   "metadata": {"avernet.retryOnly": True}}})
    assert r.json()["error"]["data"]["reason"] == "unknown_outcome"
```

- [ ] **Step 2: 确认失败 → Step 3: 实现**

```python
# spike/a2a/sse.py
import json


def sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
```

server.py 追加（与 T2 同一文件的后续段落）：

```python
# server.py 追加段（import 区新增）：
import asyncio

from kernel import TERMINAL_STATES, _digest
from starlette.responses import StreamingResponse

import sse as sse_mod


def _wire(e: dict) -> dict:
    """kernel 私有事件 → 标准 StreamResponse 成员（不变量 1）。"""
    out = dict(e.get("ev") or {})
    for kind in ("statusUpdate", "artifactUpdate"):
        if kind in out and out[kind] is not None:
            inner = dict(out[kind])
            meta = dict(inner.get("metadata") or {})
            if e.get("seq") is not None:
                meta["seq"] = e["seq"]
            inner["metadata"] = meta
            out[kind] = inner
    return out


def _sse_of(kernel, rid, task_id, backlog, q):
    async def gen():
        try:
            for e in backlog:
                yield sse_mod.sse({"jsonrpc": "2.0", "id": rid,
                                   "result": _wire(e)})
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield sse_mod.sse({"jsonrpc": "2.0", "id": rid,
                                   "result": _wire(ev)})
                su = ev["ev"].get("statusUpdate", {})
                if (su.get("metadata") or {}).get("final"):
                    return
        finally:                                       # 不变量 2：订阅清理
            r = kernel.runs.get(task_id)
            if r is not None and q in r.subscribers:
                r.subscribers.remove(q)
    return StreamingResponse(gen(), media_type="text/event-stream")


async def send_streaming(kernel, rid, params):
    msg = params["message"]
    if not msg.get("contextId"):
        return _context_error(rid)
    out = await kernel.open_run(msg["contextId"], msg.get("messageId"),
                                _retry_flag(params), _digest(_text(msg)))
    if out is kernel.UNKNOWN_OUTCOME:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000, "data": {"reason": "unknown_outcome"}}})
    task_id, created = out
    if created:
        asyncio.get_event_loop().create_task(kernel.execute_message(task_id))
    backlog, q = await kernel.subscribe(task_id, since_seq=0)
    return _sse_of(kernel, rid, task_id, backlog, q)


async def subscribe_task(kernel, rid, params):
    r = kernel.runs.get(params.get("taskId"))
    if r is None:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000, "data": {"reason": "not_found"}}})
    if r.state in TERMINAL_STATES:                      # R8 P2-8：终态走 ReplayEvents
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000,
                      "data": {"reason": "task_terminal_use_replay"}}})
    backlog, q = await kernel.subscribe(params["taskId"], since_seq=None)
    return _sse_of(kernel, rid, params["taskId"], backlog, q)


async def replay_events(kernel, rid, params):
    out = await kernel.replay(params["taskId"], params.get("sinceSeq", 0))
    if out is None:
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000,
                      "data": {"reason": _replay_reason(kernel,
                                                        params["taskId"])}}})
    backlog, q = out                                   # 活任务：回放后接 live
    return _sse_of(kernel, rid, params["taskId"], backlog, q)


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

    async def gen():
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield sse_mod.sse({"jsonrpc": "2.0", "id": rid,
                                   "result": _wire(ev)})
        finally:
            kernel.unsubscribe_context(params["contextId"], q)
    return StreamingResponse(gen(), media_type="text/event-stream")


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
git commit -m "feat(spike): standard StreamResponse wire + response passthrough + sub cleanup"
```

---

### Task 5: G1a 验收器 + SDK 探针 + 负向自测

**Files:** Create `probes/g1a_validate.py`、`probes/g1a_sdk.py`、Test `test_g1a_validator.py`

- [ ] **Step 1: 验收器（消费标准 wire）与负测（先证明验收器能发现错误）**

```python
# spike/a2a/probes/g1a_validate.py
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
KINDS = ("task", "message", "statusUpdate", "artifactUpdate")


def event_kind(wire_event: dict) -> str | None:
    result = wire_event.get("result", wire_event)
    return next((k for k in KINDS if k in result), None)


def member_count(wire_event: dict) -> int:
    result = wire_event.get("result", wire_event)
    return sum(1 for k in KINDS if k in result)


def validate(events: list[dict]) -> tuple[bool, dict]:
    kinds = [event_kind(e) for e in events]
    seqs, states = [], []
    for e in events:
        result = e.get("result", e)
        for k in ("statusUpdate", "artifactUpdate"):
            if k in result and result[k]:
                seq = (result[k].get("metadata") or {}).get("seq")
                if seq is not None:
                    seqs.append(seq)
        if event_kind(e) == "statusUpdate" and result.get("statusUpdate"):
            states.append(result["statusUpdate"]["status"]["state"])
    last = events[-1].get("result", events[-1]) if events else None
    last_kind = event_kind(events[-1]) if events else None
    checks = {
        "first_event_is_task": bool(events) and kinds[0] == "task",
        "artifact_update_present": "artifactUpdate" in kinds,
        "every_event_exactly_one_member": all(member_count(e) == 1
                                              for e in events),
        "last_event_is_final_status": last_kind == "statusUpdate"
        and (last["statusUpdate"].get("metadata") or {}).get("final") is True,
        "last_final_status_is_terminal": last_kind == "statusUpdate"
        and (last["statusUpdate"].get("metadata") or {}).get("final") is True
        and last["statusUpdate"]["status"]["state"] in TERMINAL,
        "seq_strictly_increasing": seqs == sorted(set(seqs)),
    }
    return all(checks.values()), checks
```

```python
# spike/a2a/tests/test_g1a_validator.py
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "probes"))
from g1a_validate import validate


def _task(state="TASK_STATE_SUBMITTED"):
    return {"result": {"task": {"id": "t", "status": {"state": state}}}}


def _status(state, seq=None, final=None):
    su = {"taskId": "t", "contextId": "c", "status": {"state": state}}
    meta = {}
    if seq is not None:
        meta["seq"] = seq
    if final is not None:
        meta["final"] = final            # R11-G1a：final 属性 wire 位置在 metadata
    if meta:
        su["metadata"] = meta
    return {"result": {"statusUpdate": su}}


def _art(seq=None):
    au = {"taskId": "t", "contextId": "c", "artifact": {},
          "append": True, "lastChunk": False}
    if seq is not None:
        au["metadata"] = {"seq": seq}
    return {"result": {"artifactUpdate": au}}


GOOD = [_task(), _art(seq=2), _art(seq=3),
        _status("TASK_STATE_COMPLETED", seq=4, final=True)]


def test_validator_accepts_correct_stream():
    ok, checks = validate(GOOD)
    assert ok and all(checks.values()), checks


def test_validator_rejects_wrong_first_event():
    ok, checks = validate(GOOD[1:])
    assert not ok and not checks["first_event_is_task"]


def test_validator_rejects_missing_terminal_final():
    ok, _ = validate([_task(), _art(seq=1),
                      _status("TASK_STATE_COMPLETED", seq=2, final=False)])
    assert not ok


def test_validator_rejects_duplicate_seq():
    ok, _ = validate([_task(), _art(seq=1), _art(seq=1),
                      _status("TASK_STATE_COMPLETED", seq=2, final=True)])
    assert not ok


def test_validator_rejects_nonstandard_member():
    bad = [_task(), {"result": {"weirdMember": {}}},
           _status("TASK_STATE_COMPLETED", seq=4, final=True)]
    ok, checks = validate(bad)
    assert not ok and not checks["every_event_exactly_one_member"]


def test_validator_rejects_trailing_unknown_after_valid_stream():  # R8-3 复现
    bad = GOOD + [{"result": {"weirdMember": {}}}]
    ok, checks = validate(bad)
    assert not ok                                    # 末条非 final 终态即拒
    assert not checks["last_event_is_final_status"]


def test_validator_rejects_two_members_in_one_result():           # R8-3 负测
    two = {"result": {"task": {"id": "t"},
                      "statusUpdate": {"status": {"state": "x"}}}}
    ok, checks = validate([_task(), _art(seq=1), two])
    assert not ok and not checks["every_event_exactly_one_member"]
```

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_g1a_validator.py -v`
Expected: 5 PASS

- [ ] **Step 2: SDK 探针（验收器直接消费标准 wire；normalize 为探索适配层；退出码贯通；写 G1a.json）**

```python
# spike/a2a/probes/g1a_sdk.py
"""G1a：官方 SDK 读 card → 自动选 JSONRPC 绑定 → 消费完整流。事件经
normalize 转为标准 wire 事件（result 恰含一个标准成员）后由验收器判定。"""
import asyncio
import json
import sys

import httpx

from g1a_validate import validate

BASE = "http://127.0.0.1:20043"


async def normalize(update) -> dict:
    """探索适配层（本计划唯一依 G2-versions.md 备忘实现的函数）。

    契约：SDK 流式 update → {"result": {恰一标准成员}}；成员内 metadata.seq
    有值则保留；终态 statusUpdate 携带 final。验收 = validate(全事件) 通过。
    """
    raise NotImplementedError("implement per G2-versions.md API memo")


async def main() -> int:
    async with httpx.AsyncClient(timeout=30) as http:
        from a2a.client import A2ACardResolver, A2AClient   # 名字以 G2 备忘为准
        card = await A2ACardResolver(httpx_client=http,
                                      base_url=BASE).get_agent_card()
        client = A2AClient(httpx_client=http, agent_card=card)
        events = []
        async for update in client.send_streaming_message(payload={
                "message": {"role": "ROLE_USER",
                            "parts": [{"text": "g1a"}],
                            "messageId": "g1a-1",
                            "contextId": "g1a-ctx"}}):
            events.append(await normalize(update))
    ok, checks = validate(events)
    out = {"gate": "G1a", "verdict": "pass" if ok else "fail",
           "checks": checks, "event_count": len(events),
           "evidence_level": "protocol-contract"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("spike/a2a/evidence/G1a.json", "w") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 3: 起服务跑探针**

```bash
spike/a2a/.venv/bin/python -m uvicorn --app-dir spike/a2a server:create_app \
  --factory --port 20043 > /tmp/spike-a2a.log 2>&1 &
sleep 2
spike/a2a/.venv/bin/python spike/a2a/probes/g1a_sdk.py; rc=$?; echo rc=$rc
```

Expected: `verdict: "pass"`、`rc=0`。任一 FAIL → 修到过或 G1a.json 记 fail，停在 T6 之前。

- [ ] **Step 4: Commit**

```bash
git add spike/a2a/probes/g1a_validate.py spike/a2a/probes/g1a_sdk.py \
  spike/a2a/tests/test_g1a_validator.py spike/a2a/evidence/G1a.json
git commit -m "test(spike): G1a standard-wire verdict with negative self-test"
```

---

### Task 6: retry-only 契约测试（context 固定作用域 + wire 显式拒绝）

**Files:** Test `tests/test_retry.py`（kernel/server 已实现）

```python
# spike/a2a/tests/test_retry.py
CTX = "fixed-ctx-retry"


def _send(client, mid, text, metadata=None, ctx=CTX):
    return client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER",
                               "parts": [{"text": text}],
                               "messageId": mid, "contextId": ctx},
                   "metadata": metadata or {}}})


def test_d14_four_rows_via_protocol(client):          # (ctx, key) 作用域命中
    first = _send(client, "rt1", "m").json()
    tid = first["result"]["task"]["id"]
    dup = _send(client, "rt1", "m").json()            # FIRST+记录在 → 去重
    assert dup["result"]["task"]["id"] == tid
    retry_hit = _send(client, "rt1", "m",
                      {"avernet.retryOnly": True}).json()   # RETRY-ONLY+在
    assert retry_hit["result"]["task"]["id"] == tid
    miss = _send(client, "rt-none", "m",
                 {"avernet.retryOnly": True}).json()   # RETRY-ONLY+不在
    assert miss["error"]["data"]["reason"] == "unknown_outcome"


def test_malformed_retry_flag_rejected(client):
    r = _send(client, "rt-bad", "m", {"avernet.retryOnly": "yes"})
    assert r.json()["error"]["code"] == -32602


def test_key_conflict_same_key_diff_content(client):
    _send(client, "rt-kc", "原始内容A")
    r = _send(client, "rt-kc", "不同的内容B").json()
    assert r["error"]["data"]["reason"] == "key_conflict"


def test_key_conflict_excludes_non_business_fields(client):
    _send(client, "rt-nb", "内容X")
    r = _send(client, "rt-nb", "内容X",
              {"avernet.retryOnly": True}).json()
    assert "error" not in r and r["result"]["task"]["id"]


def test_inject_non_task_with_operation_dedup(client):
    r1 = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 39, "method": "InjectContext",
        "params": {"contextId": "ctx-inj",
                   "message": {"role": "ROLE_USER",
                               "parts": [{"text": "旁观文本"}],
                               "messageId": "inj-1"}}}).json()
    assert r1["result"]["inject"] == "accepted"
    r2 = client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 40, "method": "InjectContext",
        "params": {"contextId": "ctx-inj",
                   "message": {"role": "ROLE_USER",
                               "parts": [{"text": "旁观文本"}],
                               "messageId": "inj-1"}}}).json()
    assert r2["result"]["inject"] == "duplicate"
    kernel = client.app.state.kernel
    assert all(r.idempotency_key != "inj-1" for r in kernel.runs.values())
```

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v` → 全 PASS；

```bash
git add spike/a2a/tests/test_retry.py
git commit -m "test(spike): context-scoped D14 matrix at protocol level"
```

---

### Task 7: F5 真实响应丢失（HTTP 控制的跨进程故障代理——R7-3）+ F7 真路径

**Files:** Create `client_t.py`、`fault_proxy.py`、Test `test_f5_restart.py`

- [ ] **Step 1: 客户端模板（context 必填 + retry-only 注入 + 5xx/超时语义 + 能力发现）**

```python
# spike/a2a/client_t.py
"""Spike 客户端模板（将来各客户端迁移参考）。
客户端契约（不变量 3）：调用方必须提供稳定 contextId；响应丢失后的关联 =
同 context+messageId；重试自动注入 avernet.retryOnly（仅当目标支持该扩展）。"""
import httpx

RETRYEXT = "https://avernet.local/a2a/ext/retry/v1"


class UnknownOutcome(Exception):
    """目标不支持安全重试扩展（或窗口外）——不得自动重发。"""


class _Retryable(Exception):
    pass


async def _post(base, context_id, message_id, text, metadata=None, timeout=1.0):
    async with httpx.AsyncClient(timeout=timeout) as http:
        return await http.post(f"{base}/rpc", json={
            "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
            "params": {"message": {"role": "ROLE_USER",
                                   "parts": [{"text": text}],
                                   "messageId": message_id,
                                   "contextId": context_id},
                       "metadata": metadata or {}}})


async def send_once(base, context_id, message_id, text,
                    metadata=None, timeout=1.0):
    """超时/断链/5xx = 响应丢失语义。"""
    try:
        r = await _post(base, context_id, message_id, text, metadata, timeout)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise _Retryable() from e
    if r.status_code >= 500:
        raise _Retryable()
    return r


async def retry_request(base, context_id, message_id, text, *,
                        supports_retry_ext, timeout=1.0):
    try:
        return await send_once(base, context_id, message_id, text,
                               timeout=timeout)
    except _Retryable:
        if not supports_retry_ext:
            raise UnknownOutcome(message_id)
        return await send_once(base, context_id, message_id, text,
                               metadata={"avernet.retryOnly": True},
                               timeout=timeout * 3)


async def detect_retry_support(base) -> bool:
    """F7 能力发现：card 是否声明 retry 扩展。"""
    async with httpx.AsyncClient(timeout=5) as http:
        card = (await http.get(f"{base}/.well-known/agent-card.json")).json()
    uris = [e.get("url") for e in card["capabilities"]["extensions"]]
    return any(u == RETRYEXT for u in uris)
```

- [ ] **Step 2: 故障代理（独立进程；HTTP 控制端点；屏障 = forwarded）**

```python
# spike/a2a/fault_proxy.py
"""F5/F7 fault proxy（独立进程）。armed：上游响应到手后被扣；release：向客户端
返 503（response lost）。控制端点：POST /_test/arm、GET /_test/state、
POST /_test/release。跨进程同步屏障 = state.forwarded（上游响应已在扣留中）。"""
import asyncio
import os

import httpx
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

app = FastAPI()
TARGET = os.environ.get("FAULT_TARGET", "http://127.0.0.1:20045")

_state = {"armed": False, "forwarded": False, "released": False,
          "relays": 0}


@app.post("/_test/arm")
async def arm():
    _state.update(armed=True, forwarded=False, released=False)
    return _state


@app.get("/_test/state")
async def state():
    return _state


@app.post("/_test/release")
async def release():
    _state["released"] = True
    return _state


@app.post("/rpc")
async def relay(request: Request):
    body = await request.json()
    _state["relays"] += 1
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            upstream = await http.post(f"{TARGET}/rpc", json=body)
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"),
                             "error": {"code": -32000,
                                       "message": "upstream unreachable",
                                       "data": {"reason": "fault_upstream_gone"}}},
                            status_code=503)
    if _state["armed"] and not _state["released"]:
        _state["forwarded"] = True                    # 上游响应已被扣（屏障）
        while not _state["released"]:
            await asyncio.sleep(0.02)
        _state["armed"] = False
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

- [ ] **Step 3: 测试（F5 全时序屏障同步；F7 走真实 legacy 目标 + 能力发现）**

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
LEG_PORT, LEG_PROXY_PORT = 20049, 20050


def _start(kind, port, extra_env=None) -> subprocess.Popen:
    if kind == "server":
        cmd = [sys.executable, "-m", "uvicorn", "server:create_app", "--factory",
               "--port", str(port)]
        env = {"SPIKE_CALLS": str(CALLS)}
    else:
        cmd = [sys.executable, "-m", "uvicorn", "fault_proxy:app",
               "--port", str(port)]
        env = {"FAULT_TARGET": f"http://127.0.0.1:{S_PORT}"}
    env.update(extra_env or {})
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=dict(os.environ, **env))
    for _ in range(60):
        try:
            httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json",
                      timeout=0.5)
            return p
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"{kind} on {port} failed to start")


def _upstream_count() -> int:
    if not CALLS.exists():
        return 0
    return sum(1 for _ in CALLS.open())


async def _wait_health(port):
    while True:
        try:
            httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json",
                      timeout=0.5)
            return True
        except Exception:
            await asyncio.sleep(0.1)


async def test_f5_real_response_loss_real_retry_no_double_execution():
    CALLS.unlink(missing_ok=True)
    srv1 = _start("server", S_PORT)
    proxy = None
    srv2 = None
    try:
        from client_t import retry_request
        proxy = _start("proxy", P_PORT)
        assert (httpx.post(
            f"http://127.0.0.1:{P_PORT}/_test/arm")).json()["armed"] is True

        task = asyncio.ensure_future(retry_request(
            f"http://127.0.0.1:{P_PORT}", "f5-ctx", "f5-key", "must-run-once",
            supports_retry_ext=True, timeout=20.0))

        deadline = time.time() + 10                   # 屏障：上游响应已扣留
        st = {}
        while time.time() < deadline:
            st = (httpx.get(
                f"http://127.0.0.1:{P_PORT}/_test/state")).json()
            if st["forwarded"]:
                break
            await asyncio.sleep(0.05)
        assert st["forwarded"] and _upstream_count() == 1   # 断言1：已执行一次

        srv1.send_signal(signal.SIGKILL)              # registry 清零
        srv1.wait()
        srv2 = _start("server", S_PORT)               # 新进程空 registry
        await asyncio.wait_for(_wait_health(S_PORT), 10)

        httpx.post(f"http://127.0.0.1:{P_PORT}/_test/release")
        resp = await asyncio.wait_for(task, 10)       # 客户端真实重试
        body = resp.json()
        assert body["error"]["data"]["reason"] == "unknown_outcome"  # 断言2
        assert _upstream_count() == 1                 # 断言3：上游恰好一次
    finally:
        for p in (srv1, srv2, proxy):
            if p is not None and p.poll() is None:
                p.kill()


async def test_f7_legacy_target_no_auto_retry_real_path():
    """F7（真实路径）：legacy A2A 目标（card 无 retry 扩展 → 能力发现 False）
    + 故障代理扣首响应 → 客户端不自动重发；屏障时序与 F5 相同（先证实
    首请求已达 legacy 且响应被扣，再释放），外部记录器证明确实只有一次。"""
    CALLS.unlink(missing_ok=True)
    legacy = _start("server", LEG_PORT, extra_env={"SPIKE_LEGACY": "1"})
    proxy = None
    try:
        from client_t import retry_request, detect_retry_support
        proxy = _start("proxy", LEG_PROXY_PORT,
                       extra_env={"FAULT_TARGET": f"http://127.0.0.1:{LEG_PORT}"})
        await asyncio.wait_for(_wait_health(LEG_PROXY_PORT), 10)

        supports = await detect_retry_support(
            f"http://127.0.0.1:{LEG_PROXY_PORT}")
        assert supports is False                      # 能力发现走真实 card

        assert (httpx.post(
            f"http://127.0.0.1:{LEG_PROXY_PORT}/_test/arm")).json()["armed"]
        task = asyncio.ensure_future(retry_request(
            f"http://127.0.0.1:{LEG_PROXY_PORT}", "f7-ctx", "f7-key", "m",
            supports_retry_ext=supports, timeout=20.0))

        deadline = time.time() + 10                   # 屏障：首请求已执行且被扣
        st = {}
        while time.time() < deadline:
            st = (httpx.get(
                f"http://127.0.0.1:{LEG_PROXY_PORT}/_test/state")).json()
            if st["forwarded"]:
                break
            await asyncio.sleep(0.05)
        assert st["forwarded"] and _upstream_count() == 1   # 首请求已达 legacy

        httpx.post(f"http://127.0.0.1:{LEG_PROXY_PORT}/_test/release")
        try:
            await asyncio.wait_for(task, 10)
            raise AssertionError("expected UnknownOutcome")
        except Exception as e:
            assert type(e).__name__ == "UnknownOutcome"  # 客户端不自动重发
        await asyncio.sleep(0.2)
        assert _upstream_count() == 1 and st["relays"] == 1  # 无第二次业务请求
    finally:
        for p in (proxy, legacy):
            if p is not None and p.poll() is None:
                p.kill()
```

- [ ] **Step 4: 跑绿 + Commit**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/test_f5_restart.py -v`
Expected: 2 PASS

```bash
git add spike/a2a/client_t.py spike/a2a/fault_proxy.py spike/a2a/tests/test_f5_restart.py
git commit -m "test(spike): F5 cross-process fault control + F7 real legacy path"
```

---

### Task 8: v3 迷你服务器（共享 registry；异步执行路径与 A2A 一致——R7-6）+ F8 跨协议

**Files:** Create `v3_server.py`、Test `test_v3_f7_f8.py`

- [ ] **Step 1: 失败测试**

```python
# spike/a2a/tests/test_v3_f7_f8.py
import asyncio

import pytest

from v3_server import make_v3_app

CTX = "xproto-ctx"


@pytest.fixture
def v3pair(client):
    return client, make_v3_app(client.app.state.kernel)


def test_f8_cross_protocol_same_context_hits_same_run(v3pair):
    a2a, v3app = v3pair
    rid = _v3_send_and_collect(v3app, CTX, "xproto-1", "跨协议内容")
    r = a2a.post("/rpc", json={
        "jsonrpc": "2.0", "id": 41, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER",
                               "parts": [{"text": "跨协议内容"}],
                               "messageId": "xproto-1", "contextId": CTX},
                   "metadata": {"avernet.retryOnly": True}}}).json()
    assert r["result"]["task"]["id"] == rid       # 同 (ctx,key,digest) 命中同一 run


def test_f8_cross_protocol_same_key_diff_content_conflicts(v3pair):
    a2a, v3app = v3pair
    _v3_send_and_collect(v3app, CTX, "xproto-2", "原始内容甲")
    r = a2a.post("/rpc", json={
        "jsonrpc": "2.0", "id": 42, "method": "SendMessage",
        "params": {"message": {"role": "ROLE_USER",
                               "parts": [{"text": "不同内容乙"}],
                               "messageId": "xproto-2", "contextId": CTX},
                   "metadata": {}}}).json()
    assert r["error"]["data"]["reason"] == "key_conflict"   # 跨口 digest 一致（R8-2）


def test_g3_g4_observer_sees_inject_and_no_new_task(v3pair):
    a2a, _v3app = v3pair
    kernel = a2a.app.state.kernel

    async def scenario():
        q = kernel.subscribe_context("ctx-obs")
        before = len(kernel.runs)
        status = await kernel.inject("ctx-obs", "旁观样例文本", "inj-v3-1")
        ev = await asyncio.wait_for(q.get(), 1)
        return status, ev, before, len(kernel.runs)
    status, ev, before, after = asyncio.run(scenario())
    assert status == "accepted"
    assert ev["type"] == "inject"
    assert ev["ev"]["message"]["parts"][0]["text"] == "旁观样例文本"
    assert after == before                        # inject 非 Task（§8.3）


def _v3_send_and_collect(app, ctx, key, text):
    """同步外壳：TestClient 完成 ws 首提并收全事件。
    greeting 是不带 task 的 res 帧——先跳过，取携 task 的业务 res。"""
    from fastapi.testclient import TestClient
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"method": "chat.send", "id": "r1", "params": {
                "sessionKey": ctx, "idempotency_key": key,
                "message": {"role": "user",
                            "content": [{"type": "text", "text": text}]}}})
            rid = None
            while rid is None:
                frame = ws.receive_json()
                if frame.get("type") == "res" and "task" in frame:
                    rid = frame["task"]["id"]
            while True:
                payload = ws.receive_json().get("payload")
                su = (payload or {}).get("ev", {}).get("statusUpdate", {})
                if payload is None or (su.get("metadata") or {}).get("final"):
                    return rid
```

- [ ] **Step 2: 确认失败 → Step 3: v3 迷你服务器（异步执行与 A2A 同轨迹）**

```python
# spike/a2a/v3_server.py
"""v3 mini-server on the SAME kernel as the A2A shell。chat.send 与
SendStreamingMessage 同为后台执行 + 订阅流式（G5 对照口径一致，R7-6）。
legacy=True：忽略 extensions.retry_only 照常 FIRST（旧容器语义）。"""
import asyncio

from fastapi import FastAPI, WebSocket

from kernel import _digest

TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")


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
                await _pump_context(kernel, sock, frame["params"]["sessionKey"])
                return
            if frame.get("method") != "chat.send":
                continue
            params = frame.get("params", {})
            retry = bool(params.get("extensions", {}).get("retry_only"))
            mode = "first" if (app.state.legacy or not retry) else "retry_only"
            ctx = params.get("sessionKey") or "v3-ctx"
            key = params.get("idempotency_key") or "v3-k"
            out = await kernel.open_run(ctx, key, mode, _digest(_text(params)))
            if out is kernel.UNKNOWN_OUTCOME:
                await sock.send_json({"type": "res", "id": frame.get("id"),
                                      "ok": False,
                                      "error": {"reason": "unknown_outcome"}})
                continue
            rid, created = out
            if created:
                asyncio.get_event_loop().create_task(
                    kernel.execute_message(rid))    # 后台执行（与 A2A 一致）
            backlog, q = await kernel.subscribe(rid, since_seq=0)
            await sock.send_json({"type": "res", "id": frame.get("id"),
                                  "ok": True, "task": {"id": rid}})
            await _drain_run(kernel, sock, rid, backlog, q)
            return

    return app


async def _pump_context(kernel, sock, context_id):
    q = kernel.subscribe_context(context_id)
    try:
        while True:
            ev = await q.get()
            if ev is None:
                return
            await sock.send_json({"type": "event", "payload": ev})
    finally:
        kernel.unsubscribe_context(context_id, q)


async def _drain_run(kernel, sock, rid, backlog, q):
    try:
        for e in backlog:
            await sock.send_json({"type": "event", "payload": e})
        while True:
            ev = await q.get()
            if ev is None:
                return
            await sock.send_json({"type": "event", "payload": ev})
            su = ev["ev"].get("statusUpdate", {})
            if (su.get("metadata") or {}).get("final"):
                return
    finally:                                          # 订阅清理（不变量 2）
        r = kernel.runs.get(rid)
        if r is not None and q in r.subscribers:
            r.subscribers.remove(q)


def _text(params):
    msg = params.get("message", {})
    return "".join(p.get("text", "") for p in msg.get("content", []))
```

- [ ] **Step 4: 全绿 + Commit**

Run: `spike/a2a/.venv/bin/python -m pytest spike/a2a/tests/ -v` → 全 PASS

```bash
git add spike/a2a/v3_server.py spike/a2a/tests/test_v3_f7_f8.py
git commit -m "feat(spike): shared-kernel async-exec v3 mini server (F8 real protocols)"
```

---

### Task 9: bridge→kernel 活管线（G3 真引擎实时证据）

**Files:** Create `v3_echo_engine.py`、`bridge_v3.py`、Test `test_bridge.py`、Probe `g3_realtime.py`

- [ ] **Step 0: 发现真 v3 帧字段（产出固化为 bridge 常量）**

```bash
grep -n '"connect"' -A 6 engine/adapter/src/engine/community/api/transport/ws_server.py | head -30
grep -n '_handle_chat_send' -A 15 engine/adapter/src/engine/community/api/transport/ws_server.py | head -30
grep -n 'send_event(' engine/adapter/src/engine/community/api/transport/ws_server.py | head -15
```

（字段名 delta/state/text 若与发现不符，修正 `_pump` 分类键；connect 帧若需鉴权字段，按发现补 `CONNECT_FRAME`。）

- [ ] **Step 1: 回声引擎（纯帧成型对端，无 kernel——避免"自己测自己"）+ 失败测试**

```python
# spike/a2a/v3_echo_engine.py
"""独立 v3 帧回声引擎（无 kernel）——bridge 契约测试对端。"""
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
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", port)
                writer.close()
                break
            except OSError:
                await asyncio.sleep(0.1)
    try:
        br = BridgeV3(kernel=kernel)
        await br.connect("ws://127.0.0.1:20047/ws")
        rid = await br.chat_send("br-ctx", "测试桥接")   # echo 引擎为执行源
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
                        if "statusUpdate" in ev and ev["statusUpdate"].get(
                                "final"):
                            break
        assert "task" in arrivals[0]                     # 标准 wire 首事件
        arts = [e["artifactUpdate"] for e in arrivals if "artifactUpdate" in e]
        assert len(arts) >= 3                            # 实时泵入生效
        assert arts[0]["append"] is False                # §8.4 首块
        assert arts[-1]["append"] is False and arts[-1]["lastChunk"] is True
        for e in arrivals:                               # R9-3：全流 context 对齐
            for k in ("task", "statusUpdate", "artifactUpdate"):
                if k in e:
                    assert e[k]["contextId"] == "br-ctx", (k, e[k])
                    assert e[k].get("taskId") in (None, rid), (k, e[k])
        final = [e for e in arrivals if "statusUpdate" in e][-1]["statusUpdate"]
        assert (final.get("metadata") or {}).get("final") is True
        assert final["status"]["state"] == "TASK_STATE_COMPLETED"
    finally:
        a2a_srv.should_exit = True
        echo_srv.should_exit = True
        await asyncio.gather(t1, t2)
```

- [ ] **Step 2: bridge 实现（统一收口 complete_run——R7-8）**

```python
# spike/a2a/bridge_v3.py
"""真 engine v3 WS → spike kernel 活泵。分类按 payload 键（delta/state/text）。"""
import asyncio
import json

import websockets

from kernel import _digest

CONNECT_FRAME = {"type": "req", "method": "connect",
                 "params": {"minProtocol": 3, "maxProtocol": 3}}
# Step 0 发现如需鉴权字段在此补齐；禁止依赖未确认字段


class BridgeV3:
    def __init__(self, kernel=None):
        self.kernel = kernel
        self.ws = None

    async def connect(self, url: str):
        self.ws = await websockets.connect(url)
        await self.ws.send(json.dumps(CONNECT_FRAME))
        hello = json.loads(await self.ws.recv())
        assert hello.get("type") == "res" and hello.get("ok"), hello

    async def chat_send(self, context_key, text, key=None):
        """kernel 建 run（唯一 registry 入口）→ 经 v3 协议要求执行 → 泵事件。"""
        key = key or f"v3-{context_key}"
        out = await self.kernel.open_run(context_key, key, "first",
                                          _digest(text))
        rid, _created = out
        self._reader = asyncio.create_task(self._pump(rid))
        await self.ws.send(json.dumps({
            "type": "req", "method": "chat.send", "id": rid,
            "params": {"sessionKey": context_key, "idempotency_key": key,
                       "message": {"role": "user",
                                   "content": [{"type": "text",
                                                "text": text}]}}}))
        return rid

    async def _pump(self, rid):
        seen_delta = False
        async for raw in self.ws:
            frame = json.loads(raw) if isinstance(raw, str) else raw
            if frame.get("type") != "event":
                continue                                # 跳过 res/握手帧
            inner = frame.get("payload", {})
            aid = f"text-{rid}"
            ctx = self.kernel.runs[rid].context_id      # R9-3：事件流 context 对齐
            if "delta" in inner:
                await self.kernel._publish(rid, "artifactUpdate", {
                    "artifactUpdate": {
                        "taskId": rid, "contextId": ctx,
                        "artifact": {"artifactId": aid,
                                     "parts": [{"text": inner["delta"]}]},
                        "append": seen_delta, "lastChunk": False}})
                seen_delta = True
            elif inner.get("state") == "final" and "text" in inner:
                await self.kernel._publish(rid, "artifactUpdate", {
                    "artifactUpdate": {
                        "taskId": rid, "contextId": ctx,
                        "artifact": {"artifactId": aid,
                                     "parts": [{"text": inner["text"]}]},
                        "append": False, "lastChunk": True}})
                await self.kernel.complete_run(rid)     # R7-8：统一收口
                return
            elif "state" in inner:
                await self.kernel.publish_status(rid, inner["state"])
```

（echo 引擎与 v3 迷你服务器分工：前者是纯帧成型对端（bridge 不允许把入 kernel 的责任转嫁给共享 kernel 的对端）；后者（T8）是双协议共享 registry 目标。两者不混用。）

- [ ] **Step 3: G3 探针（singlebox 真引擎 → bridge → kernel → SSE → 消费者；逐事件时间戳 + 终态校验）**

```python
# spike/a2a/probes/g3_realtime.py
"""G3：真引擎→bridge→kernel→SSE 活管线，消费者逐事件记时戳。"""
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
        br = BridgeV3(kernel=kernel)
        await br.connect(V3_REAL)
        t0 = time.monotonic()
        rid = await br.chat_send("g3-ctx", "请用三句话介绍 A2A 协议")
        arrivals = await consume(http, rid)
    srv.should_exit = True
    await t
    arts = [a for a in arrivals if "artifactUpdate" in a]
    finals = [a for a in arrivals if "statusUpdate" in a
              and (a["statusUpdate"].get("metadata") or {}).get("final")]
    checks = {
        "first_event_is_task": bool(arrivals) and "task" in arrivals[0],
        "artifact_stream_live": len(arts) >= 2,
        "first_chunk_latency_ms": round((arts[0]["t"] - t0) * 1000, 1),
        "final_replace_update": bool(arts) and arts[-1]["artifactUpdate"]
        ["append"] is False and arts[-1]["artifactUpdate"]["lastChunk"] is True,
        "three_stage_case": _three_stage(arts),
        "task_terminal_completed": bool(finals) and finals[-1]["statusUpdate"]
        ["status"]["state"] == "TASK_STATE_COMPLETED",    # R7-8
    }
    verdict = all(bool(v) for v in checks.values()) and \
        checks["first_chunk_latency_ms"] < 3000
    out = {"gate": "G3", "verdict": "pass" if verdict else "fail",
           "checks": checks,
           "arrival_timestamps": [round(a["t"] - t0, 3) for a in arts],
           "evidence_level": "real-engine"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("spike/a2a/evidence/G3.json", "w") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if verdict else 1


async def consume(http, rid) -> list[dict]:
    arrivals = []
    async with http.stream("POST", f"http://127.0.0.1:20043/rpc", json={
            "jsonrpc": "2.0", "id": 9, "method": "ReplayEvents",
            "params": {"taskId": rid, "sinceSeq": 0,
                       "contextId": "g3-ctx"}}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:]).get("result", {})
                arrivals.append({"t": time.monotonic(), **ev})
                if "statusUpdate" in ev and (ev["statusUpdate"].get(
                        "metadata") or {}).get("final"):
                    break
    return arrivals


def _three_stage(arts) -> bool:
    if len(arts) < 3:
        return False
    vals = [(a["artifactUpdate"]["append"],
             a["artifactUpdate"]["lastChunk"]) for a in arts]
    return vals[0] == (False, False) and all(v[1] is False for v in vals[1:-1]) \
        and vals[-1] == (False, True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

singlebox 启动（发现式，先看再跑）：

```bash
ls singlebox/ && head -40 singlebox/README* 2>/dev/null
grep -rn "def start\|'start'" singlebox/*.py 2>/dev/null | head -5
curl -s http://127.0.0.1:20003/health && echo ENGINE-OK
```

- [ ] **Step 4: 跑探针 + Commit**

Run: `spike/a2a/.venv/bin/python spike/a2a/probes/g3_realtime.py; rc=$?; echo rc=$rc`
Expected: verdict pass、rc=0

```bash
git add spike/a2a/v3_echo_engine.py spike/a2a/bridge_v3.py \
  spike/a2a/tests/test_bridge.py spike/a2a/probes/g3_realtime.py \
  spike/a2a/evidence/G3.json
git commit -m "test(spike): G3 live pipeline with terminal-state validation"
```

---

### Task 10: G1b 真链路浏览器（收紧验收——R7-5）

**Files:** Create `probes/g1b_checks.py`、`probes/g1b_browser.html`、`probes/g1b_playwright.py`、Test `tests/test_g1b_checks.py`

**判据预声明（写死于探针内）**：慢引擎 `SPIKE_SLOW_MS=250`；浏览器侧
inter-arrival ≥ 100ms 的事件对 ≥ 2（证明非整体缓冲）；abort 必须精确为
`AbortError`；恢复后两段 seq 并集 = 连续无缺号且含终态；负向 401。

- [ ] **Step 1: 发现代理入口与凭据（发现式）**

```bash
grep -rn "class.*Resolver" apps/proxy/src/sandboxproxy/community/adapters/web/resolvers/*.py | head
grep -rn "x-proxypass-token\|HS256" apps/proxy/src/sandboxproxy/community/adapters/web/relay_jwt.py | head
grep -rn "LOCAL_\|@1:20003" apps/proxy/src/sandboxproxy/community/adapters/web/resolvers/test_resolver.py | head
```

产出代理 target 写法与凭据签发办法；**签不出 → G1b.json 记 BLOCKED 与缺失条件，不降级直连**。

- [ ] **Step 2: 判定纯函数 + 负测（先证拒绝——R8-3 同法）**

```python
# spike/a2a/probes/g1b_checks.py
"""G1b 判定的纯函数（探针与负测共用；毫秒时基）。"""
SLOW_GAP_MS = 100          # 判据预声明：慢引擎 250ms，≥SLOW_GAP_MS 的间隔计 1
REQUIRED_SLOW_GAPS = 2
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}


def inter_gaps(arrivals: list[dict]) -> list[float]:
    return [b["t"] - a["t"] for a, b in zip(arrivals, arrivals[1:])]


def g1b_checks(first: dict, resumed: dict, noauth, console_errors) \
        -> tuple[bool, dict]:
    gaps = inter_gaps((first or {}).get("arrivals", []))
    seqs = ([a["seq"] for a in (first or {}).get("arrivals", [])
             if a.get("seq") is not None]
            + [a["seq"] for a in (resumed or {}).get("arrivals", [])
               if a.get("seq") is not None])
    last_event = (resumed or {}).get("lastEvent") or {}
    final_last_seq = last_event.get("lastSeq")            # 终止标记承载数（R9-1）
    checks = {
        "unauth_rejected_401": noauth in (401, 403),
        "chunked_not_buffered": sum(1 for g in gaps
                                    if g >= SLOW_GAP_MS) >= REQUIRED_SLOW_GAPS,
        "abort_is_aborterror": (first or {}).get("aborted") == "AbortError",
        "resume_seq_contiguous": bool(seqs)
        and seqs == sorted(set(seqs))
        and seqs == list(range(seqs[0], seqs[-1] + 1)),
        "resume_ends_terminal": last_event.get("kind") == "statusUpdate"
        and last_event.get("final") is True
        and last_event.get("state") in TERMINAL,
        "resume_tail_complete": final_last_seq is not None
        and bool(seqs) and max(seqs) == final_last_seq,   # 尾部无截断
        "no_console_errors": not console_errors,
    }
    return all(checks.values()), checks
```

```python
# spike/a2a/tests/test_g1b_checks.py
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "probes"))
from g1b_checks import g1b_checks


def _arr(seq_gaps):
    out, t = [], 0.0
    for seq, gap in seq_gaps:                 # gap = 距上一事件毫秒
        t += gap
        out.append({"t": t, "seq": seq})
    return out


GOOD_FIRST = {"arrivals": _arr([(1, 0.0), (2, 250.0), (3, 250.0)]),
              "aborted": "AbortError"}
GOOD_RESUMED = {"arrivals": _arr([(4, 30.0), (5, 30.0)]),
                "lastEvent": {"kind": "statusUpdate",
                              "state": "TASK_STATE_COMPLETED",
                              "final": True, "lastSeq": 5}}


def _run(first=GOOD_FIRST, resumed=GOOD_RESUMED, noauth=401, errors=()):
    return g1b_checks(first, resumed, noauth, errors)


def test_good_case_all_pass():
    ok, checks = _run()
    assert ok and all(checks.values()), checks


def test_tail_truncation_with_marker_rejected():       # R9-1 复现
    """首段 2..5、恢复段 6、尾部 7/8 丢失：seq 连续但 lastSeq=8 揭穿截断。"""
    first = {"arrivals": _arr([(2, 250.0), (3, 250.0), (4, 250.0), (5, 250.0)]),
             "aborted": "AbortError"}
    resumed = {"arrivals": _arr([(6, 30.0)]),
               "lastEvent": {"kind": "statusUpdate",
                             "state": "TASK_STATE_COMPLETED",
                             "final": True, "lastSeq": 8}}
    ok, checks = _run(first=first, resumed=resumed)
    assert not ok and not checks["resume_tail_complete"]


def test_terminal_without_completed_state_rejected():  # R9-1 state 不核放行
    working = {**GOOD_RESUMED,
               "lastEvent": {"kind": "statusUpdate",
                             "state": "TASK_STATE_WORKING",
                             "final": True, "lastSeq": 5}}
    ok, checks = _run(resumed=working)
    assert not ok and not checks["resume_ends_terminal"]


def test_buffered_burst_rejected():           # 整体缓冲：间隔≈0
    buffered = {"arrivals": _arr([(1, 0.0), (2, 5.0), (3, 5.0)]),
                "aborted": "AbortError"}
    ok, checks = _run(first=buffered)
    assert not ok and not checks["chunked_not_buffered"]


def test_wrong_abort_rejected():
    wrong = {**GOOD_FIRST, "aborted": "TypeError"}
    ok, checks = _run(first=wrong)
    assert not ok and not checks["abort_is_aborterror"]


def test_resume_without_terminal_rejected():  # 无终态即放行是审查要拦的
    dead = {**GOOD_RESUMED, "lastEvent": {"kind": "statusUpdate",
                                          "state": "TASK_STATE_WORKING",
                                          "final": None}}
    ok, checks = _run(resumed=dead)
    assert not ok and not checks["resume_ends_terminal"]


def test_tail_loss_rejected():                # 尾部丢事件的续拉
    holed = {**GOOD_RESUMED,
             "arrivals": _arr([(4, 30.0), (7, 30.0)]),   # seq 5/6 丢失
             "lastEvent": {"kind": "statusUpdate",
                           "state": "TASK_STATE_COMPLETED", "final": True}}
    ok, checks = _run(resumed=holed)
    assert not ok and not checks["resume_seq_contiguous"]


def test_noauth_success_rejected():
    ok, checks = _run(noauth=200)
    assert not ok and not checks["unauth_rejected_401"]


def test_console_error_rejected():
    ok, checks = _run(errors=["boom"])
    assert not ok and not checks["no_console_errors"]
```

- [ ] **Step 3: 浏览器页面（经真实代理；按 artifact 计数触发 abort 覆盖多个慢间隔；记录 lastEvent）**

```html
<!-- spike/a2a/probes/g1b_browser.html -->
<!doctype html><meta charset="utf-8"><title>G1b</title><script type="module">
const q = new URLSearchParams(location.search);
const PROXY = q.get("proxy");
const TARGET = q.get("target");
const TOKEN = q.get("token");
const streamURL = `${PROXY}/proxypass/${TARGET}/rpc`;
const kindOf = ev => ev.task ? "task" : ev.message ? "message"
  : ev.statusUpdate ? "statusUpdate" : ev.artifactUpdate ? "artifactUpdate" : null;

async function streamOnce(body, headers, abortAfterArtifacts) {
  const ac = new AbortController();
  const r = {n: 0, artifacts: 0, lastSeq: 0, taskId: null, aborted: null,
             httpStatus: null, arrivals: [], lastEvent: null};
  try {
    const resp = await fetch(streamURL, {method: "POST", signal: ac.signal,
      headers: {"Content-Type": "application/json", ...(headers || {})},
      body: JSON.stringify(body)});
    if (resp.status !== 200) { r.httpStatus = resp.status; return r; }
    const reader = resp.body.getReader(); const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6)).result;
        const su = ev.statusUpdate, au = ev.artifactUpdate;
        const seq = (su?.metadata?.seq) ?? (au?.metadata?.seq) ?? null;
        r.n += 1;
        if (ev.task && !r.taskId) r.taskId = ev.task.id;
        if (seq != null) r.lastSeq = seq;
        r.arrivals.push({t: performance.now(), seq});
        if (au) r.artifacts += 1;
        r.lastEvent = {kind: kindOf(ev), state: su?.status?.state ?? null,
                       final: su?.metadata?.final ?? null,
                       lastSeq: su?.metadata?.lastSeq ?? su?.metadata?.seq ?? null};
        if (abortAfterArtifacts && r.artifacts >= abortAfterArtifacts) ac.abort();
      }
    }
  } catch (e) { r.aborted = e.name; }
  return r;
}

const message = id => ({message: {role: "ROLE_USER",
  parts: [{text: "g1b-browser"}], messageId: id, contextId: "g1b-ctx"}});
window.__results = {};

(async () => {
  const neg = await streamOnce(
      {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
       "params": message("neg-1")}, {}, 0);
  window.__results.noauth = neg.httpStatus;
  const auth = TOKEN ? {"Authorization": `Bearer ${TOKEN}`} : {};
  const first = await streamOnce(
      {"jsonrpc": "2.0", "id": 2, "method": "SendStreamingMessage",
       "params": message("g1b-1")}, auth, 2);   // 2 个 artifact 后 abort（≥2 慢间隔）
  window.__results.first = {n: first.n, artifacts: first.artifacts,
                            lastSeq: first.lastSeq, taskId: first.taskId,
                            aborted: first.aborted, arrivals: first.arrivals,
                            lastEvent: first.lastEvent};
  if (first.taskId) {
    const resumed = await streamOnce(
        {"jsonrpc": "2.0", "id": 3, "method": "ReplayEvents",
         "params": {taskId: first.taskId, sinceSeq: first.lastSeq,
                    contextId: "g1b-ctx"}}, auth, 0);
    window.__results.resumed = {n: resumed.n, lastSeq: resumed.lastSeq,
                                arrivals: resumed.arrivals,
                                lastEvent: resumed.lastEvent};
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # g1b_checks
from g1b_checks import g1b_checks, inter_gaps

from playwright.async_api import async_playwright

BASE = {"proxy": "http://127.0.0.1:8892",        # Step 1 发现后替换
        "target": "LOCAL_spike@1:20043",         # Step 1 发现后替换
        "token": ""}                              # Step 1 发现后替换（可空=负向）


async def run() -> int:
    page_url = (pathlib.Path(__file__).parent / "g1b_browser.html").as_uri()
    url = f"{page_url}?proxy={BASE['proxy']}&target={BASE['target']}" \
          f"&token={BASE['token']}"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" else None)
        await page.goto(url)
        await page.wait_for_function(
            "window.__results && window.__results.done")
        res = await page.evaluate("window.__results")
        await browser.close()
    ok, checks = g1b_checks(res.get("first"), res.get("resumed"),
                            res.get("noauth"), errors)
    out = {"gate": "G1b", "verdict": "pass" if ok else "fail",
           "checks": checks,
           "gaps_ms": [round(g) for g in
                       inter_gaps((res.get("first") or {})
                                  .get("arrivals", []))],
           "first_last_event": (res.get("first") or {}).get("lastEvent"),
           "resumed_last_event": (res.get("resumed") or {})
                                  .get("lastEvent"),
           "consoles": errors[:5], "evidence_level": "proxy-e2e"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("spike/a2a/evidence/G1b.json", "w") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
```

- [ ] **Step 4: Playwright 安装、慢引擎起服务、跑探针、锁版、提交**

```bash
spike/a2a/.venv/bin/pip install playwright && \
  spike/a2a/.venv/bin/python -m playwright install chromium
spike/a2a/.venv/bin/pip freeze > spike/a2a/requirements-spike.txt
SPIKE_SLOW_MS=250 spike/a2a/.venv/bin/python -m uvicorn \
  --app-dir spike/a2a server:create_app --factory --port 20043 \
  > /tmp/spike-slow.log 2>&1 &
sleep 2
spike/a2a/.venv/bin/python spike/a2a/probes/g1b_playwright.py; rc=$?; echo rc=$rc
git add spike/a2a/probes/g1b_browser.html spike/a2a/probes/g1b_playwright.py \
  spike/a2a/evidence/G1b.json spike/a2a/requirements-spike.txt
git commit -m "test(spike): G1b tightened assertions via real proxy chain"
```

---

### Task 11: G5 基准（同源双通道；判据预声明；rss 缺失判 not_run——R7-6）

**Files:** Create `probes/g5_failure_paths.py`、`probes/g5_protocol.py`、Test `tests/test_g5_failure_paths.py`

- [ ] **Step 0: 失败路径子模块（纯函数；供探针与负测共用——R10-2）**

```python
# spike/a2a/probes/g5_failure_paths.py
"""G5 失败路径的纯函数子集：空样本不计算 median（直接结构化失败）、
WS 断开/超时统一归入不完整轮。g5_protocol 导入使用；负测直接测此处。"""
import json
import statistics
import time


def finalize_stats(first: list, whole: list, incomplete: int, rss_kb: int,
                   n_total: int) -> dict:
    """聚合单通道轮次。空/部分样本：不计算 median，保留 incomplete 计数。"""
    out = {"incomplete_rounds": incomplete, "samples": len(first),
           "n_total": n_total, "rss_kb": rss_kb}
    if first and whole:
        out["p50_first_ms"] = statistics.median(first) * 1000
        out["p50_whole_ms"] = statistics.median(whole) * 1000
    else:
        out["p50_first_ms"] = None
        out["p50_whole_ms"] = None
        out["all_failed"] = True                      # 全失败时不许 median 崩
    return out


async def ws_round(ws, i: int, t0: float):
    """单轮 WS：返回 (first_s | None, whole_s | None, seen_terminal)。
    连接关闭/超时/解析异常一律归入不完整轮（except Exception 不放行崩出）。"""
    first = None
    try:
        while True:                                   # 业务 res（id 匹配）
            msg = json.loads(await ws.recv())
            if msg.get("type") == "res" and "task" in msg \
                    and msg.get("id") == i:
                first = time.monotonic() - t0
                break
        while True:                                   # 至终态
            frame = json.loads(await ws.recv())
            payload = frame.get("payload", {})
            su = payload.get("ev", {}).get("statusUpdate", {})
            if (su.get("metadata") or {}).get("final"):
                return first, time.monotonic() - t0, True
    except Exception:                                 # R10-2：断开=不完整轮
        return first, None, False
    return first, None, False
```

```python
# spike/a2a/tests/test_g5_failure_paths.py（R10-2 两负测 + 正路径）
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "probes"))
from g5_failure_paths import finalize_stats, ws_round


def test_finalize_all_failed_returns_structured_failure():
    """全部提前 EOF：不计算 median、不崩、all_failed=True。"""
    s = finalize_stats([], [], incomplete=30, rss_kb=100_000, n_total=30)
    assert s["all_failed"] is True
    assert s["p50_first_ms"] is None and s["incomplete_rounds"] == 30


def test_finalize_partial_still_reports_median():
    s = finalize_stats([0.1, 0.2, 0.3], [1.0, 1.2], incomplete=1,
                       rss_kb=100_000, n_total=3)
    assert s["p50_first_ms"] == 200.0
    assert s["incomplete_rounds"] == 1


class _WS:
    """recv 按 script 顺序返回文本或抛异常。"""

    def __init__(self, script):
        self._items = list(script)
        self._i = 0

    async def recv(self):
        if self._i >= len(self._items):
            raise RuntimeError("connection closed")
        item = self._items[self._i]
        self._i += 1
        if isinstance(item, Exception):
            raise item
        return item


RES = '{"type":"res","id":0,"ok":true,"task":{"id":"t"}}'
EV_FINAL = ('{"type":"event","payload":{"ev":{"statusUpdate":'
            '{"metadata":{"final":true}}}}}')


def test_ws_round_normal_path():
    first, whole, term = asyncio.run(ws_round(_WS([RES, EV_FINAL]), 0, 0.0))
    assert term is True and first is not None and whole is not None


def test_ws_round_disconnect_after_first_response():  # R10-2 负测
    """首响应后断开 → seen_terminal=False、不崩、first 保序丢弃。"""
    first, whole, term = asyncio.run(
        ws_round(_WS([RES, RuntimeError("conn closed")]), 0, 0.0))
    assert term is False and whole is None


def test_ws_round_disconnect_before_first_response():
    first, whole, term = asyncio.run(
        ws_round(_WS([RuntimeError("conn closed")]), 0, 0.0))
    assert term is False and first is None and whole is None
```

- [ ] **Step 1: 探针（双方同口径：每轮新建连接、后台执行、同一语义首事件；rss 必备）**

```python
# spike/a2a/probes/g5_protocol.py
"""实验A（协议性能，G5 判定实验）。判据（预声明，跑后不得改阈值）：
- 同一 kernel + 同一 mock 引擎事件脚本；两前门均为"新 run + 新连接 + 后台执行 + 流式输出"
- N=30 轮冷连接。指标：P50 首业务事件延迟（SSE=首个 data 行；WS=res 帧）；P50 整轮时长
- P50(SSE) − P50(WS) ≤ 50ms；吞吐比 median(WS whole)/median(SSE whole) ≥ 0.9
- rss：--single sse|ws 两个独立进程采样 ru_maxrss 写 G5-rss.jsonl；样本不足 2 条
  → verdict=not_run（不允许缺证据判 pass）
真引擎差异不进 G5 判定（真引擎侧属 G3 兼容性观察）。"""
import argparse
import asyncio
import json
import resource
import sys
import time

import httpx
import uvicorn
import websockets

from g5_failure_paths import finalize_stats, ws_round
from kernel import Kernel
from server import build_app
from v3_server import make_v3_app

N = 30
P50_LIMIT_MS = 50.0
TPUT_MIN = 0.9
RSS_TOLERANCE_KB = 16 * 1024


async def bench_sse() -> dict:
    kernel = Kernel(engine="mock")
    srv = uvicorn.Server(uvicorn.Config(build_app(kernel), port=20043,
                                        log_level="error"))
    task = asyncio.create_task(srv.serve())
    try:                                              # R10-2：异常路径也关服务器
        for _ in range(50):
            try:
                async with httpx.AsyncClient(timeout=2) as http:
                    await http.get("http://127.0.0.1:20043"
                                   "/.well-known/agent-card.json")
                break
            except Exception:
                await asyncio.sleep(0.1)
        first, whole, incomplete = [], [], 0
        for i in range(N):
            t0 = time.monotonic()
            first_t = None
            seen_terminal = False
            async with httpx.AsyncClient(timeout=10) as http:  # 每轮新连接
                async with http.stream("POST",
                        "http://127.0.0.1:20043/rpc", json={
                        "jsonrpc": "2.0", "id": i,
                        "method": "SendStreamingMessage",
                        "params": {"message": {"role": "ROLE_USER",
                                               "parts": [{"text": "bench"}],
                                               "messageId": f"b-sse-{i}",
                                               "contextId": f"g5-sse-{i}"}}}) as resp:
                    if resp.status_code != 200:
                        incomplete += 1                  # 非成功轮（R9-4）
                        continue
                    async for line in resp.aiter_lines():  # 首事件+至终态单循环
                        if not line.startswith("data: "):
                            continue
                        if first_t is None:
                            first_t = time.monotonic() - t0
                        ev = json.loads(line[6:]).get("result", {})
                        if "statusUpdate" in ev and ev["statusUpdate"].get(
                                "final"):
                            seen_terminal = True
                            break
            if not seen_terminal:
                incomplete += 1
                continue
            first.append(first_t)
            whole.append(time.monotonic() - t0)
        return finalize_stats(first, whole, incomplete,
                              resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              n_total=N)               # 空样本不 median（R10-2）
    finally:
        srv.should_exit = True
        await task


async def bench_ws() -> dict:
    kernel = Kernel(engine="mock")
    v3app = make_v3_app(kernel)
    srv = uvicorn.Server(uvicorn.Config(v3app, port=20044, log_level="error"))
    task = asyncio.create_task(srv.serve())
    try:
        for _ in range(50):
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", 20044)
                writer.close()
                break
            except OSError:
                await asyncio.sleep(0.1)
        first, whole, incomplete = [], [], 0
        for i in range(N):
            t0 = time.monotonic()
            async with websockets.connect("ws://127.0.0.1:20044/ws") as ws:
                await ws.recv()                  # greeting（连接即发）
                await ws.send(json.dumps({
                    "method": "chat.send", "id": i,
                    "params": {"sessionKey": f"g5-ws-{i}",
                               "idempotency_key": f"g5-ws-{i}",
                               "message": {"role": "user",
                                           "content": [{"type": "text",
                                                        "text": "bench"}]}}}))
                f, w, term = await ws_round(ws, i, t0)   # 断开=不完整轮（R10-2）
            if not term:
                incomplete += 1
                continue
            first.append(f)
            whole.append(w)
        return finalize_stats(first, whole, incomplete,
                              resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                              n_total=N)
    finally:                                          # R10-2：异常也关服务器
        srv.should_exit = True
        await task


def aggregate(sse: dict, ws: dict) -> dict:
    if sse["incomplete_rounds"] > 0 or ws["incomplete_rounds"] > 0:
        return {"gate": "G5", "verdict": "fail",    # R9-4/R10-2：失败也出证据
                "results": {"incomplete_sse": sse["incomplete_rounds"],
                            "samples_sse": sse.get("samples"),
                            "incomplete_ws": ws["incomplete_rounds"],
                            "samples_ws": ws.get("samples"),
                            "all_failed": sse.get("all_failed", False)
                            or ws.get("all_failed", False)},
                "evidence_level": "protocol-perf-same-source"}
    p50_delta = sse["p50_first_ms"] - ws["p50_first_ms"]
    tput_ratio = ws["p50_whole_ms"] / max(sse["p50_whole_ms"], 1e-9)
    checks = {"p50_delta_ms": round(p50_delta, 1),
              "p50_pass": p50_delta <= P50_LIMIT_MS,
              "tput_ratio": round(tput_ratio, 3),
              "tput_pass": tput_ratio >= TPUT_MIN}
    verdict = "not_run"                            # rss 样本不足 → not_run
    try:
        samples = [json.loads(line) for line
                   in open("spike/a2a/evidence/G5-rss.jsonl")]
    except FileNotFoundError:
        samples = []
    if len(samples) == 2:
        delta_kb = abs(samples[0]["rss_kb"] - samples[1]["rss_kb"])
        checks["rss_delta_kb"] = round(delta_kb, 1)
        checks["rss_pass"] = delta_kb <= max(
            0.1 * max(s["rss_kb"] for s in samples), RSS_TOLERANCE_KB)
        verdict = "pass" if all(v for k, v in checks.items()
                                if k.endswith("_pass")) else "fail"
    return {"gate": "G5", "verdict": verdict,
            "predefined_criteria":
                f"P50Δ≤{P50_LIMIT_MS}ms; tput≥{TPUT_MIN}; "
                f"|Δrss|≤max(10%, {RSS_TOLERANCE_KB}KB)",
            "results": checks, "evidence_level": "protocol-perf-same-source"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--single", choices=("sse", "ws"))
    args = parser.parse_args()
    if args.single:
        stats = asyncio.run(bench_sse() if args.single == "sse" else bench_ws())
        line = json.dumps({"channel": args.single, **stats})
        print(line)
        with open("spike/a2a/evidence/G5-rss.jsonl", "a") as f:
            f.write(line + "\n")
        return 0
    out = None
    try:
        out = aggregate(asyncio.run(bench_sse()), asyncio.run(bench_ws()))
    except Exception as exc:                  # R10-2：任何异常也覆盖写 fail 证据
        out = {"gate": "G5", "verdict": "fail", "error": repr(exc)[:400],
               "evidence_level": "protocol-perf-same-source"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("spike/a2a/evidence/G5.json", "w") as f:   # 总是覆盖，不保旧 pass
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑基准（双通道 + 两进程 rss + 复跑聚合）+ Commit**

```bash
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py              # P50+吞吐
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py --single sse
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py --single ws
spike/a2a/.venv/bin/python spike/a2a/probes/g5_protocol.py              # 聚合 rss
```

Expected: 最终 `verdict: "pass"`（rss 样本不足 → not_run 且退出码 1；未过判据 → fail；**任何情况不允许改探针内阈值**）

```bash
git add spike/a2a/probes/g5_failure_paths.py spike/a2a/probes/g5_protocol.py \
  spike/a2a/tests/test_g5_failure_paths.py spike/a2a/evidence/G5.json
git commit -m "test(spike): G5 failure-path evidence (empty samples, ws disconnects)"
```

---

### Task 12: 机器化汇总（证据分级 + F 用例分项）+ 附录回填
**Files:** Create `probes/report.py`、`evidence/G4.json`、`evidence/REPORT.md`

- [ ] **Step 1: report.py（聚合 G*.json；F 用例按分项映射多测试；NOT RUN 不放行）**

```python
# spike/a2a/probes/report.py
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
F_MAP = {
    "F1": ["tests/test_kernel.py::test_cache_limit_stops_caching_not_delivery",
           "tests/test_kernel.py::test_byte_budget_overflow_sets_cache_limit"],
    "F2": ["tests/test_kernel.py::test_terminal_replay_window_is_180s_not_immediate"],
    "F3": ["tests/test_v3_f7_f8.py::test_g3_g4_observer_sees_inject_and_no_new_task"],
    "F4": ["tests/test_retry.py::test_d14_four_rows_via_protocol"],
    "F5": ["tests/test_f5_restart.py::test_f5_real_response_loss_real_retry_no_double_execution"],
    "F6": ["tests/test_kernel.py::test_f6_missed_events_replay_by_seq",
           "tests/test_kernel.py::test_f6_live_handoff_no_gap"],
    "F7": ["tests/test_f5_restart.py::test_f7_legacy_target_no_auto_retry_real_path"],
    "F8": ["tests/test_v3_f7_f8.py::test_f8_cross_protocol_same_context_hits_same_run",
           "tests/test_v3_f7_f8.py::test_f8_cross_protocol_same_key_diff_content_conflicts",
           "tests/test_kernel.py::test_f8_concurrent_first_single_execution"],
}
LEVELS = {"kernel-unit": "内核单测", "protocol-contract": "协议契约",
          "proxy-e2e": "代理端到端", "real-engine": "真引擎实测",
          "toolchain": "工具链", "protocol-perf-same-source": "同源协议性能"}


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
    lines += ["", f"- pytest 全量: {'PASS' if rc == 0 else 'FAIL'} (rc={rc})",
              "", "| F 用例 | 分项测试 |", "|---|---|"]
    for k, items in F_MAP.items():
        lines.append(f"| {k} | " + "<br>".join(f"`{i}`" for i in items) + " |")
    lines += ["", "NOT RUN/BLOCKED 的门 = 未知数：kernel 单测绿 ≠ 代理端到端通过。",
              "放行条件：全部 verdict=pass 且 pytest rc=0。"]
    (EV / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    gates_ok = all(v == "pass" for _, v, _ in rows)
    return 0 if (gates_ok and rc == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: G4 证据（双客户端真链路进程级）**

uvicorn 起 A2A 壳 + v3 迷你（同一 kernel）；客户端甲 `SubscribeToContext`（SSE），客户端乙经 v3/`InjectContext` 注入；断言观察者收到 inject 事件且 kernel.runs 数量不变。结果机写 `evidence/G4.json`：

```json
{"gate": "G4", "verdict": "pass", "evidence_level": "protocol-contract",
 "checks": {"observer_witnessed": true, "no_new_task": true}}
```

实测脚本 = `tests/test_v3_f7_f8.py::test_g3_g4_observer_sees_inject_and_no_new_task` 的进程级包装（同一断言），BLOCKED 不降级。

- [ ] **Step 3: 汇总 + 设计文档附录 A 回填**

```bash
spike/a2a/.venv/bin/python spike/a2a/probes/report.py; rc=$?; echo rc=$rc
```

Modify `docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md`：附录 A 头部抄录 a2a-sdk 实际版本（G2.json）；附录 A.3 以 `settings.EXT_URIS` 定版 extension URI（关闭 O8）；状态行追加 Spike 结论指引（REPORT.md，spike 代码不合入 dev）。

- [ ] **Step 4: 提交推送**

```bash
git add spike/a2a/probes/report.py spike/a2a/evidence/ \
  docs/superpowers/specs/2026-10-13-engine-a2a-protocol-replacement-design.md
git commit -m "docs(spike): machine report with evidence levels and per-case F map"
AVERNET_PRE_PUSH_MERGE_TARGET=origin/dev git push
```

**放行规则**：阶段 1a 放行 = REPORT.md 全门 `pass` + pytest rc=0；NOT RUN/BLOCKED 任一存在即不放行；证据等级决定该结论支撑的决策层。

---

## 自审记录（v3）

1. **评审覆盖**：R6 8 项 + R7 6 P1 + 2 实现项 + 3 环境项 → R7-1 wire（T4 `_wire`/路由透传/标准断言）；R7-2 context（T2/T6/T7/T8 全链固定 + `context_required` + 客户端契约入不变量 3）；R7-3 跨进程控制（T7 `/_test/*` 端点 + forwarded 屏障 + 全时序）；R7-4 F6/F7/F8 实路径（T3 live-handoff、T7 legacy 真访问+能力发现、T3/T8 并发+执行级断言、T12 分项 F_MAP）；R7-5 G1b（T10 inter-arrival/AbortError/seq 并集连续性）；R7-6 G5（T11 同口径冷连接/同语义首事件/rss 必备）；R7-7 预算/慢订阅者/清理（T3 kernel + 测试）；R7-8 complete_run 统一收口（T3/T9 bridge/G3 终态断言）；环境项（T1 pytest-asyncio/G2.json/锁 grep）。
2. **成稿方法修订**：v2 单次成稿三连出现同类缺陷后改为分段拼装；R8 后升格"组装 harness 实际运行 mock 层测试"——两层关卡：块级 py_compile（28 块）+ 装配运行 pytest（R8 版 47 passed；R9 修复后总数随新增用例增长，以实际运行输出为准；真引擎/浏览器/性能仍 out of scope）。残留标记扫描零命中才提交；R9 期间自查又捕一次 `if False else` 残迹，编辑即修。
3. **类型一致性**：`kernel`（open_run/execute_message/send_message/complete_run/subscribe/replay/publish_status/inject/unsubscribe_context）；`server.HANDLED` handler 签名 `(kernel, rid, params)`；`client_t.send_once/retry_request/detect_retry_support`；`v3_server.make_v3_app(kernel, legacy)`；`fault_proxy.非代码块控制端点`（HTTP，无进程内 import）。
4. **已知留白**：`g1a_sdk.py::normalize` 为显式探索适配层（NotImplementedError + 契约注释，依 G2-versions.md 实现——R6/R7 指示的处理方式）；singlebox 启动命令与 proxy target 格式为发现式步骤。

## 机器校验（执行本计划前，评审者可复跑）

两层：

**第一层：语法编译。（v3.4 实测 30 块全绿；失败必须非零退出——R11-1）**

```bash
python3 - <<'EOF'
import re, pathlib, subprocess, sys, tempfile
md = pathlib.Path("docs/superpowers/plans/2026-10-13-engine-a2a-spike.md").read_text()
blocks = re.findall(r"```python\n(.*?)```", md, re.S)
failed = 0
for i, b in enumerate(blocks):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(b)
    rc = subprocess.run([sys.executable, "-m", "py_compile", f.name]).returncode
    if rc != 0:
        failed += 1
        print(f"BLOCK {i} FAILED")
print(f"blocks={len(blocks)} failed={failed}")
sys.exit(1 if failed else 0)     # R11-1：仅打印不退出会漏过 pytest 绿的假象
EOF
```

**第二层：mock 实运行。（R8 升格；R9 后 fresh checkout 可复现）**

```bash
# 一次性（fresh checkout）：
python3 -m venv spike/a2a/.harness/venv
spike/a2a/.harness/venv/bin/pip install \
  fastapi uvicorn httpx websockets pytest pytest-asyncio
# 复跑关卡：
python3 spike/a2a/.harness/assemble.py           # 计划 → src/ 装配 + py_compile
spike/a2a/.harness/venv/bin/python -m pytest \
  spike/a2a/.harness/src/tests -q --no-header    # 期望全 PASS（数量以实际输出为准）
```

装配器 `spike/a2a/.harness/assemble.py` **随仓库跟踪**（ignore 只作用于
`src/`、`venv/`、`__pycache__`）——R9-5 修正：上一轮"提交于（gitignored）"
为矛盾陈述，实际未提交。

要求：mock 层任何非零 → 修复后重跑并更新全书；真引擎/浏览器/性能实验不在
此关卡（评审 R8 的边界划定）。缺口上报：`_start(kind,..)` 端口占用时换端口。

两层任一不过 → 本计划不得作为执行底本（不得以扩写或说明替代重跑）。

