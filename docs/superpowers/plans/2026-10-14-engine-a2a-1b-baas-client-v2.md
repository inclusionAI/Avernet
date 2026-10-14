# Engine A2A 阶段 1b v2：BaaS 客户端灰度（R15 修订版）

日期：2026-10-14（v2，吸收 1b 计划评审 R15 八项——上一版核心前提"客户端保形+编排零改动"与真实调用代码不符，全面重写）
状态：**任务级代码分段撰写中**——本文件的 §1-§4（裁决/契约清单/架构决策/映射规则）为已定稿部分；§5+ 任务序列按分段工序逐步补齐，每段补齐后机器校验
分支：`feat/engine-a2a-protocol-replacement`（base=7d817ee6a；E1/E2 首发件 `04176aa6f` 按 §5.E1' 返工）

---

## §0 R15 八条裁决（全部实证成立）

| # | 裁决 | 修订落点 |
|---|------|---------|
| 1 签名臆造 | 成立（真实面见 §1 契约清单） | §5.B1 |
| 2 chat_send 阻塞至 SSE 完 | 成立（ack 后台分发的流式退化） | §3.L1 生命周期 |
| 3 终态归一把失败变成功/清空终稿 | 成立（`state.content=text` 覆盖） | §3.T1 终态单次规则 |
| 4 JSON-RPC 错误/断流被吞 | 成立 | §3.E1 错误面 |
| 5 HITL 无决策回传 + 载荷截断 | 成立（且 engine 侧缺同 task 续消息面） | §3.H1 + 新任务 E4 |
| 6 agent 映射丢 stream/data | 成立（engine_frame 逃生舱存在） | §3.M1 无损映射 |
| 7 能力探测≠全量切换，无回退开关 | 成立 | §3.G1 灰度门 |
| 8 测试缺陷与 T2 残缺 | 成立 | §5.T1'/T2' |

---

## §1 BaaS 客户端契约清单（从真实代码逐条提取——实现者的对齐基准）

### 1.1 方法面（`_bot_websocket_client.py` 完整签名）

```python
# :240-248 —— 编排调用方 _send_chat_request(:743) 的真实传参
async def chat_send(
    self, session_key: str, message: str, timeout_ms: int | None = None,
    auth_token: str | None = None, app_id: str | None = None,
    chat_metadata: dict[str, str] | None = None,
    attachments: list[Any] | None = None,
) -> dict[str, Any]:
    """纯数据边界：返回服务端原始 result dict；ok 判断由 _send_chat_request
    统一完成（result.ok=False → ChatRequestError(code/message/retryable)）。
    params 组装含 permissionMode/timeoutMs/appId/chat_metadata 并置/
    attachments(dataclass asdict)/x-iam-token(auth or "OPEN_API:NOT_PROVIDED")/traceId。"""

# :335-346
async def chat_abort(self, session_key: str, run_id: str | None = None) -> dict[str, Any]: ...

# :316-334 —— 返回"精确的已验证 RPC 交换"（EngineInteractionResolveExchange）
async def interaction_resolve(self, *, interaction_id: str,
                              resolution: InteractionResolution,
) -> EngineInteractionResolveExchange: ...

# :281 起同名方法（A2A 版须等义）
async def chat_inject(self, session_key: str, message: Any, label: Any = None) -> ...: ...
```

### 1.2 生命周期与事件面（`_async_chat_client.py`）

- **发送即返回**：`_send_chat_request`（:725-775）sent 后立即返回 ack dict，
  `_recv_loop` 后台分派事件 → `on_event(name, payload)` 同步回调。
  `send_message_stream`（:483-566）从 `state.stream_queue` 实时产 chunk——
  **首个 delta 必须在引擎 final 之前到达下游**（v2.T1' 有此专项时序断言）。
- **事件七类**（:282-289）：chat / agent / interaction.requested /
  interaction.resolved / mode_transition.resolved / error / `*`（通配日志）。
  回调签名 `(event_name, payload)`——通配在精确 handler 缺席时兜底日志。
- **`_on_chat` 三分支**（final/error/else）：
  - final：`contents = message.content[]`；`text=None 跳过`；
    `stopReason=="inject"` 跳过 chat_complete（不置完成）；否则
    `state.content=text; state.chat_complete.set()` + StreamChunk(type="final")。
  - error（state=="error"）→ `_handle_terminal_error(errorMessage, errorCode)`。
  - 其余忽略。
- **`_on_agent`**：`payload["stream"]`（"tool"/"assistant"/"thinking"…）+
  `payload["data"]`（dict，含 phase）；agent final（stream=="assistant"，state=="final"）
  也设 chat_complete。**event 派发同时发 StreamChunk(type="agent",
  metadata={"engine_frame": payload})——完整原始载荷**（转义通道：BCN 等
  converter 直接消费）。`stream=="tool" && data.phase=="result"` → 收集
  `state.agent_payloads`。
- **`_on_disconnect`**：断连时全部在途会话包装为终态 error（`_handle_terminal_error`），
  除非已完成。
- **交互决策回传**（:1180-1215）：`interaction_service` 维度内 `client.interaction_resolve(
 interaction_id, resolution)` → `record_engine_exchange(exchange)`；异常 →
  `mark_failed(session_key, interaction_id, error)`。

### 1.3 `ok` 契约

`chat_send` 返回 dict 的 `ok=False` 分支由 `_send_chat_request` 抛
`ChatRequestError(message, error_code, error_message, retryable)`——A2A 客户端
必须返回同构 `{ok, error?{code, message, retryable?}}` 形状，不改变 ok 的判断点。

---

## §2 灰度范围决策（R15-7）

| 项 | 决策 |
|---|---|
| 组件级开关 | `A2A_CLIENT_MODE`（DI/config 读取：**off** \| grey \| on）。1b 出厂默认 **off**——生产零风险；grey = 能力探测通过后启用；on = 强制（探测失败才回退）。off 是组件级整体回退 v3（设计 §10.3 要求） |
| attachments | 带附件请求**发送前可判定** → 工厂层强制 v3（不静默丢参——A2A v1 面无附件通道） |
| chat_metadata | A2A `SendMessageRequest.metadata` 承载（key 透传；引擎侧 bridge 的 ChatRequest.extraParams 对齐——E3' 一并接线） |
| auth_token | HTTP 头 `X-IAM-Token`（1a gate 已消费）；缺失与 WS 同义填 `OPEN_API:NOT_PROVIDED` |
| HITL | **全闭环进 1b**（E4）——半闭环等于把可观测的等待请求变成不可答的悬挂 |
| app_id / timeout_ms | A2A message.metadata 承载 / SSE 空闲 ping 保活（engine 侧 1a 已有；客户端 idle-超时断连走 error 终态） |

---

## §3 架构决策（修正版核心）

### L1 生命周期：ack 与流消费分离

```
chat_send(...) ──▶ 发 SendStreamingMessage（httpx.stream 打开）
                   ├─ HTTP ≠ 200 / JSON-RPC error（error 字段在首个 JSON body）
                   │    → 返回 {"ok": False, "error": {…}}（对齐 ok 契约，不抛裸异常）
                   ├─ 打开成功 = 引擎受理（发送方日志 ack）
                   │    → 返回 {"ok": True, "runId": A2A task id, "a2a": True}
                   │       并持有 body task：client._requests[request_id] = {task, session_key}
                   └─ SSE 逐行消费（后台）：wire → v3 payload 归一 → on_event 分发
close() / chat_abort() ──▶ 取消 _requests 的真实 body task（可取消性收口）
```

`_recv_loop` 对位物：一个共享后台 task 轮询多队列或直接 per-request task
（选 per-request task——取消语义天然正确）。

### T1 终态单次规则（R15-3）

每 run 恰一次有效终态，按来源语义分流：

| wire 到达 | 处理 |
|---|---|
| artifactUpdate(final=True, append=False) | **只更新内容快照**（internally cached final text）；不发 chat final 事件，不置 chat_complete |
| statusUpdate(state=COMPLETED, metadata.final=true) | 以**缓存终稿**（或空）为内容发 chat final（`{state:"final", message.content}`），chat_complete 置位一次 |
| statusUpdate(state=FAILED/CANCELED/REJECTED, final) | 走 error 终态：发 error 事件形 `{state:"error", errorMessage}`——服务端 FAILED 时 bridge 的 final 工况 metadata 已携带 errorMessage 则透传 |
| 重复 final / 无终态 EOF | 重复：去重计数不重复置完成；EOF 无终态：**不得视为成功**——发 error 终态（"stream ended without terminal"） |

### E1 错误区（R15-4）

- **响应类型二分**：打开后立即读首块——JSON-RPC 200 响应若 `Content-Type` 非
  `text/event-stream` → 读完整 body → `{"ok": False, "error": {code? map 32000→zero_check, …}}`
- **流中断**：取消（CancelledError）静默；其他异常 → 按会话发 error 终态事件
  + `{"ok": True but terminal error}`——不把 ok ack 改写，错误进事件面（与 WS
  断连的 `_on_disconnect` 语义对齐）
- 归一 payload 一律带 `sessionKey`（wire 成员的 contextId 回填）

### M1 无损映射（R15-6）

wire 侧（engine）：所有 engine 帧（agent/interaction/approval/mode_transition）的原
始 payload dict **完整**携带：`statusUpdate.metadata.engine_frame = payload`（不截断、
不挑字段——BaaS `StreamChunk.metadata.engine_frame` 逃生舱直接复用）。
BaaS 侧归一：事件形 = engine_frame 重建 v3 payload（含 stream/state/data/message），
留少量顶层易读字段（kind/state）供日志。

### H1 HITL 闭环（R15-5，新任务 E4）

engine 侧（对齐设计 §8.6）：
- wire 构造：requested（完整 engine_frame）→ INPUT_REQUIRED；resolved → WORKING
- **`ResolveInteraction` 新 extension 方法**：入参 `interactionId + resolution
  {decision, message?}`；
  kernel 语义：定位 interaction 所属 run（`_interactions: iid → rid` 注册表），
  同 run 续写一条 assistant 用户决策消息后恢复工作流——实现为 engine Bridge 的
  `resolve` 转发（openclaw `sessions.send`? 具体引擎转发目标在 E4 发现式
  Step 0 决定：ws_server 的 interaction.resolve 转发路径是现成的唯一事实源）。
- 冲突两分支：重复提交（run 已离开 INPUT_REQUIRED）→ 标准 conflict 错；run 不
  存在 → not_found。（与 R3 评审 TC-HITL-DUP 同口径）

BaaS 客户端：`interaction_resolve(interaction_id, resolution)` 实现该方法面——
返回 `EngineInteractionResolveExchange` 对齐的 exchange dict（response/ok/error）。

---

## §5 任务序列

### E1' wire/kernel 无损构造返工（替代首发 `04176aa6f` 的降采样版）

**Files:** Modify `engine/adapter/src/engine/community/api/transport/a2a/wire.py`、
`kernel.py`（返工 04176aa6f 已落部分）

已发布的三个构造函数把 payload 挑字段+截断（R15-5/6）——返工为 **engine_frame
完整载荷**（M1 无损映射），签名与调用方保持不变：

```python
# wire.py —— 三个构造函数整体替换（seed 保留 interaction_id 提取与状态语义）

def wire_interaction_requested(run_id: str, context_id: str, seq: int,
                               interaction_id: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
    """HITL 请求（§8.6/h1）：INPUT_REQUIRED + 完整 engine_frame 载荷
    （BaaS StreamChunk.metadata.engine_frame 逃生舱直接消费——不挑字段不截断）。"""
    meta: dict[str, Any] = {"seq": seq, "interactionId": interaction_id,
                            "engine_frame": dict(payload)}
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                          "metadata": meta}}}


def wire_interaction_resolved(run_id: str, context_id: str, seq: int,
                              payload: dict[str, Any]) -> dict[str, Any]:
    """HITL 决议：回 WORKING + 完整 engine_frame；E4 s 的 resolve 响应
    帧也复用本构造。"""
    meta: dict[str, Any] = {"seq": seq, "engine_frame": dict(payload)}
    iid = payload.get("interactionId") or payload.get("id")
    if isinstance(iid, (str, int)):
        meta["interactionId"] = iid
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}


def wire_agent_progress(run_id: str, context_id: str, seq: int,
                        payload: dict[str, Any]) -> dict[str, Any]:
    """agent 事件（stream/data 结构体保真）→ statusUpdate(WORKING) +
    metadata.engine_frame 完整载荷（BaaS 侧由 engine_frame 重建 v3 payload）。"""
    meta: dict[str, Any] = {"seq": seq, "kind": "agent",
                            "engine_frame": dict(payload)}
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id, "contextId": context_id,
                          "status": {"state": "TASK_STATE_WORKING"},
                          "metadata": meta}}}
```

kernel.py 已落的三个 publish_* 方法**签名不变**（内部调 wire 构造——替换后的
wire 无损版自动生效）。另追加 E4 依赖的注册表与状态查询：

类内追加（两处编辑点注明）：
- `A2aKernel.__init__` 末尾加成员；`publish_interaction_requested` 内、
  置状态之后、`_publish` 之前加登记行；类内追加两个查询方法：

```python
# 编辑点 1（__init__ 末尾）：成员——interactionId → rid 注册表
        self._interactions: dict[str, str] = {}

# 编辑点 2（publish_interaction_requested 内）：登记
        self._interactions[interaction_id] = rid

# 编辑点 3（类内方法）：
    def interaction_run(self, interaction_id: str) -> str | None:
        """HITL resolve 的 run 定位（E4）。"""
        return self._interactions.get(interaction_id)

    def interaction_state(self, interaction_id: str) -> str | None:
        rid = self._interactions.get(interaction_id)
        if rid is None:
            return None
        r = self.runs.get(rid)
        return r.state if r else None
```

**验证**：既有 20 项回归必须全绿（R12/R13 测试不依赖被删的截断字段——若有
引用按新构造修正断言目标，不改语义）；冒烟补 interaction frame 完整性断言。

**Commit**: `fix(a2a): wire constructors carry full engine_frame payload (R15 rework of 04176aa6f)`

### E3' engine_bridge 帧路由（R15 前版 E3 的修正）

**前置事实**（已核）：bridge 循环内 `event ∈ {"chat"}` 走原分支；`event=="agent"`
（openclaw `_derive_event_name` 默认值）居多；`interaction.requested`{状态或事件
双形态}（claude_code 列表 :29 证实）；`approval_requested/resolved` 状态型；
`mode_transition.resolved` 事件型（BaaS 订阅对位物）。文件顶部已有 `import uuid`。

```python
# _dispatch_to_engine 循环——inject 分支之后、chat 分支之前插入：

            # ── v2-E3: non-chat engine frames — lossless SSE routing ──
            if event != "chat" and state not in _TERMINAL_CHAT_STATES:
                if state == "interaction.requested" or \
                        event == "interaction.requested":
                    iid = payload.get("interactionId") or payload.get("id")
                    if not isinstance(iid, str) or not iid:
                        iid = f"int-{uuid.uuid4().hex[:8]}"
                    await kernel.publish_interaction_requested(
                        rid, iid, payload)
                    continue
                if state == "interaction.resolved" or \
                        event == "interaction.resolved":
                    await kernel.publish_interaction_resolved(rid, payload)
                    continue
                if state in ("approval_requested", "approval_resolved"):
                    iid = payload.get("approvalId") or payload.get("id") \
                        or f"apr-{uuid.uuid4().hex[:8]}"
                    if state == "approval_requested":
                        await kernel.publish_interaction_requested(
                            rid, str(iid), payload)
                    else:
                        await kernel.publish_interaction_resolved(rid, payload)
                    continue
                if event == "agent" and payload:
                    await kernel.publish_agent_progress(rid, payload)
                    continue
                if event == "mode_transition.resolved":
                    # mode_transition 按同一 lossless 通道广播——v3 消费面对位
                    # 事件名经 engine_frame 重建。
                    await kernel.publish_agent_progress(rid, payload)
                    continue
                log.debug("bridge: dropping unhandled frame event=%s state=%s",
                          event, state)
                continue
```

**验证**：既有回归 + R14 bridge 帧序测试扩展（见 T1'-engine 侧单测矩阵）。

**Commit**: `feat(a2a): bridge routes agent/interaction/approval/mode_transition frames losslessly`

### E4 HITL resolve 闭环（engine 侧新 extension）

**Step 0 发现（实现者必执行）**：`chat = _chat_plugin()` 后 MRO 检查
`resolve_interaction`（我们对 claude_code 已核：`core/adapters/claude_code/chat.py:297
resolve_interaction(session_key, run_id, response, auth)`）；openclaw 侧无直接
resolve 方法——其 resolve 转发路径 **在 Step 0 里 grep 确认**
（入口提示：openclaw adapter 的 stream 段 :194-214 有 run_id 相关响应处理；
`sessions.send` 是候选 relay 方法）。发现结果写进实现 commit message 与
`SECOND_STAGE_EVIDENCE` 侧注，不静默选择。

**wire/kernel/router 实现**：

```python
# wire.py 追加
def wire_resolve_conflict(run_id: str | None, context_id: str, seq: int,
                          reason: str) -> dict[str, Any]:
    """E4/H1：重复提交或 run 已不在 INPUT_REQUIRED 的标准 conflict 错误。"""
    return {"seq": seq, "type": "statusUpdate", "ev": {
        "statusUpdate": {"taskId": run_id or "", "contextId": context_id,
                          "status": {"state": "TASK_STATE_FAILED"},
                          "metadata": {"kind": "resolve_conflict",
                                       "reason": reason}}}}
```

```python
# router.py —— HANDLERS 追加（gated method 也要进 _GATED_METHODS）
async def _resolve_interaction(params: dict[str, Any], req_id: Any,
                               gated_key: str | None) -> dict[str, Any]:
    """H1/H4：同 run 决策提交——§8.6 契约（重复提交 conflict / run 不存在
    not_found / 成功恢复 WORKING）。"""
    interaction_id = params.get("interactionId")
    if not isinstance(interaction_id, str) or not interaction_id:
        raise _ParamError("interactionId is required (string)")
    response = params.get("response")
    if response is not None and not isinstance(response, str):
        raise _ParamError("response must be a string when present")
    rid = _kernel.interaction_run(interaction_id)
    if rid is None or rid not in _kernel.runs:
        return {"error": {"code": -32000, "message": "interaction not found",
                          "data": {"reason": "not_found"}}}
    state = _kernel.interaction_state(interaction_id)
    if state != "TASK_STATE_INPUT_REQUIRED":
        return {"error": {"code": -32003,
                          "message": f"run not awaiting input (state={state})",
                          "data": {"reason": "resolve_conflict"}}}
    run = _kernel.runs[rid]
    # 引擎转发（Step 0 发现的路径）——claude_code:
    #   await chat.resolve_interaction(session_key=run.context_id,
    #                                run_id=rid, response=response, auth=None)
    # openclaw: 见 Step 0。失败 → not_found_or_engine 错误返回。
    ok, err = await _forward_resolve(run.context_id, rid, response)
    if not ok:
        return {"error": {"code": -32000,
                          "message": err or "resolve forward failed",
                          "data": {"reason": "resolve_forward_failed"}}}
    await _kernel.publish_interaction_resolved(
        rid, {"interactionId": interaction_id, "resolution": response})
    return {"interaction": {"resolved": True, "runId": rid}}


_HANDLERS["ResolveInteraction"] = _resolve_interaction
# _GATED_METHODS 追加 "ResolveInteraction"（gate 同强度——决策提交可触发执行恢复）
```

`_forward_resolve` 的模式与 R13 `_forward_engine_inject` 同构（`_chat_plugin`
解析/await 调用/INTERNAL_ERROR 结构化上抛）——claude_code 走上面的 resolve_interaction
调用；openclaw 的具体调用由 Step 0 落定后按同构骨架填入。

kernel 侧 requires：E1' 的 `_interactions` 注册表（已含）。注册表随 run 驱逐
自然失效（rid 不在 runs → resolve 返回 not_found 即可——不必主动清理）。

**交互事件 payload 双形态提示**（对 B1 归一层同样适用）：
`event=="interaction.requested"` 或 `state=="interaction.requested"` 任一即中。

**验证（T1'-engine 矩阵里落测）**：requested → resolve 成功（WORKING 恢复）/
重复 resolve → conflict / 未知 id → not_found / 双（T1'-engine 矩阵）。

**Commit**: `feat(a2a): ResolveInteraction extension — HITL same-run resolve closed loop`

### B1 `BotA2aClient` v2（契约对齐完整客户端）

**Files:** Create `apps/baas/src/secbaas/community/core/service/bot_run/_bot_a2a_client.py`

实现者须知：**先通读 §1 契约清单再动笔**。签名从 `_bot_websocket_client.py`
逐字对齐（:240-248/:316/:335/:281——复制其 docstring 语义），**不发明参数**。
以下给关键骨架与全部自研逻辑的全码；与 WS 语义镜像的样板组装按引用处对齐。

```python
"""BotA2aClient — BotWebSocketClient 的 A2A v1.0 传输兄弟（1b 灰度）。

契约对齐基准：_bot_websocket_client.py（方法签名/docstring 语义逐条对齐）+
本计划 §1（生命周期/事件面/ok 契约）+ §3（L1 ack 生命周期 / T1 终态单次 /
E1 错误区 / M1 无损归一）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Generator, Optional

import httpx

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Any]
# 每 run 的归一状态：内容快照 + 终态去重（T1 单次终态规则）
_TERMINAL_WIRE_STATES = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
                         "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")


class A2aUnavailableError(RuntimeError):
    """目标不可用/非 JSONRPC——工厂回退 v3 的信号（ G1 灰度门）。"""


class _RunCtx:
    __slots__ = ("rid", "session_key", "final_text", "terminal_emitted",
                 "task")

    def __init__(self, rid: str, session_key: str):
        self.rid = rid
        self.session_key = session_key
        self.final_text: str | None = None   # artifact 终稿快照（T1）
        self.terminal_emitted = False         # 单次终态去重（T1）
        self.task: asyncio.Task | None = None


def _message_to_text(message: Any) -> str:
    """WS chat_send 的 message 是 str（v3 客户端协议）——A2A parts 的 text 块。"""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "".join(p.get("text", "") for p in content
                           if isinstance(p, dict))
    return str(message)


class BotA2aClient:
    """方法面签名逐条见 _bot_websocket_client.py 对位行注释。"""

    def __init__(self, *, base_url: str, headers: Optional[dict] = None,
                 connect_timeout: float = 10.0,
                 http_transport_factory: Callable[
                     [], httpx.AsyncClient] | None = None):
        self.base_url = base_url.rstrip("/")
        self.rpc_url = f"{self.base_url}/rpc"
        self.card_url = f"{self.base_url}/.well-known/agent-card.json"
        self.headers = dict(headers or {})
        self._connect_timeout = connect_timeout
        self._http_factory = http_transport_factory   # 可测性通道（全方法生效）
        self._event_handlers: dict[str, EventHandler] = {}
        self._disconnect_callback: EventHandler | None = None
        self._connected = False
        self._server_info: dict[str, Any] | None = None
        self._runs: dict[str, _RunCtx] = {}            # rid → ctx（L1 持有）
        self._session_runs: dict[str, str] = {}        # sessionKey → rid

    # ── 可测性：所有 httpx.AsyncClient 现场化唯一出口 ──
    def _http(self, *, timeout: float | None) -> httpx.AsyncClient:
        if self._http_factory is not None:
            return self._http_factory()
        return httpx.AsyncClient(timeout=timeout, proxy=None)

    # ── 生命周期（连接=能力探测；§3.G1） ──
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_info(self) -> dict[str, Any] | None:
        return self._server_info

    @property
    def features(self) -> dict[str, Any] | None:
        return (self._server_info or {}).get("features")

    async def connect(self) -> dict[str, Any]:
        """能力探测（G1）：JSONRPC + 1.x 才可用；返回 WS hello-ok 同构 info。"""
        if self._connected:
            raise RuntimeError("Already connected")
        try:
            async with self._http(timeout=self._connect_timeout) as http:
                resp = await http.get(self.card_url, headers=self.headers)
                resp.raise_for_status()
                card = resp.json()
        except Exception as e:
            raise A2aUnavailableError(
                f"A2A capability probe failed: {e}") from e
        matched = any(
            (i.get("protocolBinding") == "JSONRPC"
             and str(i.get("protocolVersion", "")).startswith("1."))
            for i in card.get("supportedInterfaces", []))
        if not matched:
            raise A2aUnavailableError(
                f"no JSONRPC 1.x interface on card")
        self._server_info = {"type": "hello-ok", "protocol": 1,
                             "server": {"version": card.get("version", ""),
                                        "card": card}, "a2a": True}
        self._connected = True
        return dict(self._server_info)

    def on_event(self, event_name: str, handler: EventHandler) -> None:
        self._event_handlers[event_name] = handler

    def on_disconnect(self, handler: EventHandler) -> None:
        self._disconnect_callback = handler

    async def _notify_disconnect(self) -> None:
        if self._disconnect_callback:
            try:
                self._disconnect_callback("disconnect", {})
            except Exception:
                logger.exception("BotA2aClient disconnect handler error")

    async def close(self) -> None:
        """L1：取消全部在途 body task 后回调 disconnect（含 error 终态语义见
        _consume_stream 的 finally 路径——清理顺序：先取消后通知）。"""
        self._connected = False
        for ctx in list(self._runs.values()):
            if ctx.task and not ctx.task.done():
                ctx.task.cancel()
        self._runs.clear()
        self._session_runs.clear()
        await self._notify_disconnect()

    # ── L1 生命周期核心：chat_send 两段式 ──
    async def chat_send(
        self, session_key: str, message: str, timeout_ms: int | None = None,
        auth_token: str | None = None, app_id: str | None = None,
        chat_metadata: dict[str, str] | None = None,
        attachments: list[Any] | None = None,
    ) -> dict[str, Any]:
        """对位 _bot_websocket_client.py:240-248（签名与 ok 语义不增改）。

        attachments 非空：本客户端**拒绝**（工厂层的特性门在连接级已拦截——
        此兜底防御仍然返回显式错误，绝不静默丢参）。
        返回 ack：{"ok": True, "runId": rid, "a2a": True} 或
        {"ok": False, "error": {"code", "message", "retryable"?}}——ok 判断
        权仍在 _send_chat_request。
        """
        if not self._connected:
            return {"ok": False, "error": {"code": "NOT_CONNECTED",
                                            "message": "not connected"}}
        if attachments:
            return {"ok": False,
                    "error": {"code": "ATTACHMENTS_UNSUPPORTED",
                              "message": "A2A path cannot carry attachments "
                                         "(factory should route to v3)"}}
        rid = f"{session_key}:{time.time_ns()}"   # 稳定 run 追踪键
        payload_message = {
            "messageId": rid, "contextId": session_key,
            "role": "ROLE_USER", "parts": [{"text": _message_to_text(message)}],
        }
        metadata: dict[str, Any] = {"traceId": rid}
        if chat_metadata:
            metadata["chatMetadata"] = chat_metadata
        if app_id:
            metadata["appId"] = app_id
        headers = {**self.headers,
                   "X-IAM-Token": auth_token or "OPEN_API:NOT_PROVIDED",
                   "Accept": "text/event-stream"}
        body = {"jsonrpc": "2.0", "id": rid,
                "method": "SendStreamingMessage",
                "params": {"message": payload_message,
                           "metadata": metadata}}

        client = self._http(timeout=None)
        # httpx stream 打开自身即"请求受理"——错误体的读法按 §3.E1 二分：
        try:
            resp = await client.send(
                client.build_request("POST", self.rpc_url, json=body,
                                     headers=headers), stream=True)
        except Exception as e:
            await client.aclose()
            return {"ok": False,
                    "error": {"code": "TRANSPORT_ERROR",
                              "message": str(e)[:200]}}
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or "text/event-stream" not in ctype:
            # HTTP 200 + JSON-RPC error 也会走这里（1a 错误面是 JSONResponse）
            data: dict[str, Any] = {}
            try:
                data = json.loads((await resp.aread()).decode(
                    errors="replace")) or {}
            except Exception:
                data = {}
            err = ((data.get("error") or {}).get("data") or {})
            await resp.aclose()
            await client.aclose()
            return {"ok": False,
                    "error": {"code": err.get("reason",
                                              f"HTTP_{resp.status_code}"),
                              "message": (data.get("error") or {})
                              .get("message", "")[:200],
                              "retryable": False}}
        ctx = _RunCtx(rid, session_key)
        self._runs[rid] = ctx
        self._session_runs[session_key] = rid
        ctx.task = asyncio.create_task(self._consume_stream(ctx, resp, client))
        return {"ok": True, "runId": rid, "a2a": True}

    # ── T1 终态单次规则 + E1 错误区 + M1 无损归一 ──
    async def _consume_stream(self, ctx: _RunCtx,
                              resp: httpx.Response,
                              client: httpx.AsyncClient) -> None:
        """后台逐行消费：wire → 事件 → handlers。异常按 E1 处理。"""
        try:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = json.loads(line[6:])
                if "error" in body:
                    err = (body["error"].get("data") or {})
                    self._emit_terminal_error(
                        ctx, err.get("reason", "a2a_error"),
                        body["error"].get("message", ""))
                    return
                self._handle_wire_result(ctx, body.get("result") or {})
            else:
                # E1：EOF 无终态不得视为成功
                if not ctx.terminal_emitted:
                    self._emit_terminal_error(
                        ctx, "STREAM_CLOSED_WITHOUT_TERMINAL",
                        "stream ended before terminal status")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("BotA2aClient SSE consume failed rid=%s",
                            ctx.rid)
            if not ctx.terminal_emitted:
                self._emit_terminal_error(ctx, "STREAM_ERROR", str(e)[:200])
        finally:
            await resp.aclose()
            await client.aclose()
            self._runs.pop(ctx.rid, None)

    def _handle_wire_result(self, ctx: _RunCtx, result: dict[str, Any]) -> None:
        for member in ("statusUpdate", "artifactUpdate", "task", "message"):
            if member in result:
                handler = getattr(self, f"_on_{member}")
                handler(ctx, result[member])
                return

    def _on_task(self, ctx: _RunCtx, task: dict[str, Any]) -> None:
        logger.debug("[a2a] task opened rid=%s state=%s", ctx.rid,
                     (task.get("status") or {}).get("state"))

    def _on_artifactUpdate(self, ctx: _RunCtx, au: dict[str, Any]) -> None:
        # T1：artifact 终稿只更新快照——绝不发 chat final / 不置完成
        if au.get("lastChunk") and not au.get("append"):
            parts = (au.get("artifact") or {}).get("parts") or []
            ctx.final_text = "".join(
                p.get("text", "") for p in parts if isinstance(p, dict))
            return
        text = ""
        parts = (au.get("artifact") or {}).get("parts") or []
        if parts:
            text = parts[0].get("text", "")
        self._dispatch("chat", {
            "state": "delta", "deltaText": text,
            "runId": au.get("taskId"), "sessionKey": ctx.session_key,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]},
            "seq": (au.get("metadata") or {}).get("seq"),
        })

    def _on_statusUpdate(self, ctx: _RunCtx, su: dict[str, Any]) -> None:
        meta = su.get("metadata") or {}
        state = (su.get("status") or {}).get("state", "")
        base = {"runId": su.get("taskId"), "sessionKey": ctx.session_key,
                "seq": meta.get("seq")}

        # M1 无损重建：engine_frame 优先生效
        frame = meta.get("engine_frame")
        if state == "TASK_STATE_INPUT_REQUIRED":
            payload = dict(frame) if isinstance(frame, dict) else {}
            payload.setdefault("interactionId", meta.get("interactionId"))
            payload.setdefault("state", "interaction.requested")
            payload["sessionKey"] = ctx.session_key
            self._dispatch("interaction.requested", payload)
            return
        if meta.get("interactionId") and state == "TASK_STATE_WORKING":
            payload = dict(frame) if isinstance(frame, dict) else {}
            payload.setdefault("interactionId", meta.get("interactionId"))
            payload["state"] = "interaction.resolved"
            payload["sessionKey"] = ctx.session_key
            self._dispatch("interaction.resolved", payload)
            return
        if meta.get("kind") == "agent":
            payload = dict(frame) if isinstance(frame, dict) else {}
            payload.setdefault("stream", payload.get("stream", ""))
            payload.setdefault("state", payload.get("state", ""))
            payload["sessionKey"] = ctx.session_key
            self._dispatch("agent", payload)   # stream/data 保真重建
            return

        # 终态分流（T1）
        if meta.get("final") in ("true", True):
            if ctx.terminal_emitted:
                return                       # 单次终态去重
            ctx.terminal_emitted = True
            if state == "TASK_STATE_COMPLETED":
                text = ctx.final_text or ""
                self._dispatch("chat", {
                    **base, "state": "final",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": text}]},
                })
            else:
                self._dispatch("chat", {
                    **base, "state": "error",
                    "errorMessage": meta.get("errorMessage",
                                             f"engine terminal {state}"),
                    "errorCode": state,
                })
            return
        self._dispatch("chat", {**base, "state": state})

    def _on_message(self, ctx: _RunCtx, msg: dict[str, Any]) -> None:
        # inject 回显等 message 成员 → v3 "chat" 事件（BaaS 编排通配日志吸收）
        text_parts = (msg.get("parts") or [])
        self._dispatch("chat", {
            "state": "message",
            "sessionKey": ctx.session_key,
            "runId": ctx.rid,
            "message": {"role": "user",
                        "content": [{"type": "text",
                                     "text": "".join(
                                         p.get("text", "")
                                         for p in text_parts
                                         if isinstance(p, dict))}]},
        })

    def _emit_terminal_error(self, ctx: _RunCtx, code: str,
                             message: str) -> None:
        ctx.terminal_emitted = True
        self._dispatch("chat", {
            "state": "error", "sessionKey": ctx.session_key,
            "runId": ctx.rid, "errorMessage": message, "errorCode": code,
        })

    def _dispatch(self, event_name: str, payload: dict[str, Any]) -> None:
        handler = self._event_handlers.get(event_name) \
            or self._event_handlers.get("*")
        if handler:
            try:
                handler(event_name, payload)
            except Exception:
                logger.exception("[a2a] handler %s failed", event_name)

    # ── abort / inject / interaction_resolve（§1.1 契约面） ──
    async def chat_abort(self, session_key: str,
                        run_id: str | None = None) -> dict[str, Any]:
        """对位 _bot_websocket_client.py:335-346。A2A 面 CancelTask 后取消
        本地 body task（L1）。"""
        rid = run_id or self._session_runs.get(session_key)
        out: dict[str, Any] = {"ok": False, "a2a": True}
        if rid:
            async with self._http(timeout=30.0) as http:
                resp = await http.post(self.rpc_url, json={
                    "jsonrpc": "2.0", "id": rid,
                    "method": "CancelTask",
                    "params": {"taskId": rid, "contextId": session_key}},
                    headers=self.headers)
                out = {"ok": resp.status_code == 200, "a2a": True}
                if resp.status_code == 200:
                    body = resp.json()
                    out["ok"] = "error" not in body
        ctx = self._runs.get(rid or "")
        if ctx and ctx.task and not ctx.task.done():
            ctx.task.cancel()
        return out

    async def chat_inject(self, session_key: str, message: Any,
                          label: Any = None) -> dict[str, Any]:
        """对位 _bot_websocket_client.py:281（v3 是 relay 转发返回 ok/壳）。"""
        mid = f"inj-{uuid.uuid4().hex[:12]}"
        payload = {
            "jsonrpc": "2.0",
            "id": mid,
            "method": "InjectContext",
            "params": {
                "contextId": session_key,
                "message": {
                    "role": "ROLE_USER",
                    "messageId": mid,
                    "parts": [{"text": _message_to_text(message)}],
                },
            },
        }
        async with self._http(timeout=30.0) as http:
            resp = await http.post(self.rpc_url, json=payload,
                                   headers=self.headers)
            if resp.status_code != 200:
                return {"ok": False, "a2a": True,
                        "error": {"message": f"HTTP {resp.status_code}"}}
            body = resp.json()
        result = body.get("result") or {}
        status = result.get("inject", "unknown")
        return {"ok": status in ("accepted", "duplicate_accepted"),
                "status": status, "a2a": True}

    async def interaction_resolve(self, *, interaction_id: str,
                                  resolution: Any) -> dict[str, Any]:
        """对位 _bot_websocket_client.py:316-334 的 exchange 契约。

        resolution（InteractionResolution 现结构——实现前 cat 它的定义对齐
        decision/message 字段名）；A2A 面是 ResolveInteraction extension。"""
        response = getattr(resolution, "response", None) or \
            getattr(resolution, "message", None) or \
            (resolution.get("response") if isinstance(resolution, dict)
             else None)
        payload = {
            "jsonrpc": "2.0",
            "id": f"res-{interaction_id}",
            "method": "ResolveInteraction",
            "params": {
                "interactionId": interaction_id,
                "response": response,
            },
        }
        async with self._http(timeout=30.0) as http:
            resp = await http.post(self.rpc_url, json=payload,
                                   headers=self.headers)
            body = (resp.json() if resp.status_code == 200 else
                    {"error": {"message": f"HTTP {resp.status_code}"}})
        if "error" in body:
            return {"ok": False, "exchange": body,
                    "error": body["error"]}
        return {"ok": True, "exchange": body,
                "result": body.get("result") or {}}
```

**实现校验点**（执行者逐条过）：
- `InteractionResolution` 的真实字段名 cat 定义后修正 `response` 提取——允许
  对源结构对齐，不猜。
- `_dispatch` 事件名集合 = §1.2 七类的子集（"message"态 chat 事件核对
  `_on_chat` else 分支可否被吸收——`state="message"` 会进 else 忽略分支，
  无害；通配 handler 会记录）。
- 1a router 无 CancelTask handler 时（现状）——`chat_abort` 的 `ok=False`
  是诚实面；进 §5.T2' 验收注记（或作为缺口的显式清单进 E3' 补充——决定：
  本计划已在 §3.H1 边界注记 "CancelTask 面 1a 未实现"——v2 明确为**缺口
  清单项**，不静默）。

**Commit**: `feat(a2a): BotA2aClient v2 — contract-aligned ack/stream lifecycle`

### B2 `AsyncChatClient` 双客户端工厂 + 灰度门（G1）

**Files:** Modify `apps/baas/src/secbaas/community/core/service/bot_run/_async_chat_client.py`

```python
# 顶部 import 增补
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError, BotA2aClient,
)
import os as _os

# 模块级 helper
def _a2a_client_mode() -> str:
    """G1 组件级灰度开关。出处顺序：env A2A_CLIENT_MODE > "off"。
    off=整组件回退 v3（§10.3）；grey=能力探测通过即用；on=探测失败仍试建。"""
    return (_os.environ.get("A2A_CLIENT_MODE", "off")
            or "off").strip().lower()

def _a2a_base_url_from_ws(uri: str) -> str | None:
    """ws(s)://host:port/... → http(s)://host:port；非法输入返回 None。"""
    if not isinstance(uri, str) or "://" not in uri:
        return None
    scheme, _, rest = uri.partition("://")
    if scheme not in ("ws", "wss"):
        return None
    host_port = rest.split("/", 1)[0]
    return ("https://" if scheme == "wss" else "http://") + host_port
```

`connect()` 的唯一构造点（本文件 ：264 起原 BotWebSocketClient(...) 块）整体替换：

```text
        # ── v2 §3.G1 灰度门：配置→能力→特性，逐级判定 ──
        mode = _a2a_client_mode()
        a2a_base = _a2a_base_url_from_ws(self.uri)
        _client = None
        transport = "v3-ws"
        if mode != "off" and a2a_base and not self._has_attachments_bound():
            # 特性门（连接级）：attachments 由上层会话决定进 v3——本类目前
            # 不持会话附件，故以工厂统一："带附件连接" 划入 v3 的判据放在
            # _bot_service_selector（调用侧），此处提供 opt-out 属性。
            try:
                _client = BotA2aClient(base_url=a2a_base,
                                        headers=self.headers)
                await _client.connect()
                transport = "a2a"
            except A2aUnavailableError as e:
                logger.info("[a2a-gate] probing failed (%s) — v3 path", e)
                _client = None
                if mode == "on":
                    raise RuntimeError(
                        f"A2A_CLIENT_MODE=on but capability unavailable: {e}")
        if _client is None:
            from secbaas.community.core.service.bot_run._bot_websocket_client import (
                BotWebSocketClient,
            )
            _client = BotWebSocketClient(
                uri=self.uri, client_id=self.client_id,
                client_version=self.client_version, headers=self.headers)
            await _client.connect()
        self._client = _client
        self._transport = transport
        logger.info("AsyncChatClient connected via %s", transport)
```

`__init__` 增补成员：`self._transport: str = "v3-ws"`。
特性门 `_has_attachments_bound()` 是简化门——真实附件按附件请求存在时由
`_send_chat_request` 走 v3 客户端的单连判断不可行（一连接一条 transport）。
**本计划的取舍**：文件级 `chat_send` attachments 参数在 A2A 客户端返回
`ATTACHMENTS_UNSUPPORTED` 硬错（B1 已落），不做静默；**调用层**（`_claude_service`
之类的会话级）的连接选择按服务类型在 B3（后补任务）决定——1b 灰度范围的
连接全部为纯对话（无附件）服务，已有的附件服务连接则继续 v3。
`_GATED_METHODS` 语义对应不变。

**实现校验点**：رده核对 `_async_chat_client` 里 `self._client.server_info`、
`is_connected` 的消费点（BaaS 侧其他文件的 pool 健康检查逻辑）不受传输差异
影响——`connected`/`server_info` 面已对齐。

**Commit**: `feat(a2a): BaaS dual-transport factory with gated grey-release (G1)`

### T1' 消费者级测试（R15-8：从真实入口验证，失败必须阻断）

**Files:** Create `apps/baas/tests/community/service/bot_run/test_bot_a2a_client.py`

测试对象：`BotA2aClient` 直连假 A2A app（httpx ASGITransport 工厂——**全方法生效**，
chat_send/chat_inject/interaction_resolve 也走注入通道，R15-8 指出的旧缺陷）。
假 app 模拟 1a router 语义（含错误体即 JSONResponse、SSE 流式）。

```python
"""BotA2aClient v2 契约测试——R15 修正版（从真实消费者口径驱动）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError, BotA2aClient,
)


def _sse(result: dict) -> str:
    return f"data: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result})}\n\n"


def _make_app(stream_script: list[Any] | None = None,
              *, inject_ok: bool = True,
              resolve_conflict: bool = False,
              gate_error: bool = False) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
                "version": "0.1.0"}

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        if gate_error:
            return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"),
                                "error": {"code": -32000,
                                          "message": "auth_required",
                                          "data": {"reason": "auth_required"}}})
        if body["method"] == "SendStreamingMessage":
            session = body["params"]["message"]["contextId"]

            async def gen():
                # 时序保证：先产 delta，等一个 tick 再产 final——
                # 消费者级断言用此窗口证明首个 delta 先于 final 到达。
                yield _sse({"statusUpdate": {
                    "taskId": "t1", "contextId": session,
                    "status": {"state": "TASK_STATE_WORKING"},
                    "metadata": {"kind": "agent", "seq": 1,
                                 "engine_frame": {"stream": "tool",
                                                   "data": {"phase": "start"},
                                                   "state": ""}}}})
                yield _sse({"artifactUpdate": {
                    "taskId": "t1", "contextId": session,
                    "artifact": {"parts": [{"text": "你好"}]},
                    "append": False, "lastChunk": False,
                    "metadata": {"seq": 2}}})
                await asyncio.sleep(0.15)     # delta→final 窗口
                yield _sse({"artifactUpdate": {
                    "taskId": "t1", "contextId": session,
                    "artifact": {"parts": [{"text": "你好，完整终稿"}]},
                    "append": False, "lastChunk": True,
                    "metadata": {"seq": 3}}})
                yield _sse({"statusUpdate": {
                    "taskId": "t1", "contextId": session,
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "metadata": {"final": "true", "lastSeq": 4}}})
                if stream_script:               # 附加异常场景注入
                    for extra in stream_script:
                        yield extra
            return StreamingResponse(gen(), media_type="text/event-stream")
        if body["method"] == "InjectContext":
            status = "accepted" if inject_ok else "unknown"
            return {"jsonrpc": "2.0", "id": body["id"],
                    "result": {"inject": status}}
        if body["method"] == "ResolveInteraction":
            if resolve_conflict:
                return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                                    "error": {"code": -32003,
                                              "data": {"reason":
                                                        "resolve_conflict"}}})
            return {"jsonrpc": "2.0", "id": body["id"],
                    "result": {"interaction": {"resolved": True}}}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                            "error": {"code": -32601}})

    return app


def _client(app: FastAPI) -> tuple[BotA2aClient, httpx.AsyncClient]:
    holder = {}

    def factory() -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver")
        holder["client"] = c
        return c

    return BotA2aClient(base_url="http://testserver",
                        http_transport_factory=factory), holder


# ── L2 ack 语义：发送立即返回，不吞 SSE 完成 ──

def test_chat_send_returns_before_final():
    """R15-2 消费者级时序：final 所需窗口内 ack 已返回。"""
    app = _make_app()

    async def scenario():
        client, _ = _client(app)
        await client.connect()
        import time
        t0 = time.monotonic()
        ack = await client.chat_send(session_key="s", message="hi",
                                     auth_token="tok")
        elapsed = time.monotonic() - t0
        assert ack["ok"] is True and ack["a2a"] is True
        assert elapsed < 0.14, f"ack blocked until near-final ({elapsed:.2f}s)"
        await asyncio.sleep(0.4)   # 让 body task 消费完（避免悬挂）
        await client.close()
        return "ok"

    assert asyncio.run(scenario()) == "ok"


def test_first_delta_reaches_consumer_before_final():
    """R15-2 硬断言：引擎 final 前下游已收到首个 delta（编排流式保真）。"""
    app = _make_app()
    received: list[tuple[str, dict]] = []

    async def scenario():
        client, _ = _client(app)
        await client.connect()
        client.on_event("chat", lambda n, p: received.append((n, p)))
        client.on_event("agent", lambda n, p: received.append((n, p)))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.08)   # final 窗口开启前
        delta_seen = any(p.get("state") == "delta" for _, p in received)
        return delta_seen

    assert asyncio.run(scenario()) is True


# ── T1 终态单次规则 ──

def test_double_final_dedup_and_content_preserved():
    """重复 final 只完成一次；最终 content 为终稿非空文本。"""
    extra = [_sse({"statusUpdate": {
        "taskId": "t1", "contextId": "s",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "metadata": {"final": "true", "lastSeq": 5}}})]
    app = _make_app(stream_script=extra)
    finals: list[dict] = []

    async def scenario():
        client, _ = _client(app)
        await client.connect()
        client.on_event("chat", lambda n, p: finals.append(p)
                        if p.get("state") == "final" else None)
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.5)
        await client.close()

    asyncio.run(scenario())
    assert len(finals) == 1, f"terminal emitted {len(finals)} times"
    assert finals[0]["message"]["content"][0]["text"] == "你好，完整终稿"


def test_failed_task_yields_error_terminal_not_success():
    app = _make_app(stream_script=[_sse({"statusUpdate": {
        "taskId": "t1", "contextId": "s",
        "status": {"state": "TASK_STATE_FAILED"},
        "metadata": {"final": "true", "errorMessage": "engine broke",
                      "lastSeq": 5}}})])
    seen: list[tuple[str, dict]] = []

    async def scenario():
        client, _ = _client(app)
        await client.connect()
        client.on_event("chat", lambda n, p: seen.append((n, p)))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.5)
        await client.close()

    asyncio.run(scenario())
    errors = [p for n, p in seen if p.get("state") == "error"]
    finals = [p for n, p in seen if p.get("state") == "final"]
    assert errors and not finals, "FAILED must surface as error, never final"


# ── E1 错误区：gate 拒绝不入流、形不成 ok=True ──

def test_gate_rejection_returns_not_ok():
    app = _make_app(gate_error=True)

    async def scenario():
        client, _ = _client(app)
        await client.connect()
        return await client.chat_send(session_key="s", message="hi",
                                     auth_token=None)

    out = asyncio.run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "auth_required"


# ── M1 无损归一：engine_frame 全量透过 ──

def test_agent_engine_frame_roundtrip_lossless():
    app = _make_app()
    agent_payloads: list[dict] = []

    async def scenario():
        client, _ = _client(app)
        await client.connect()
        client.on_event("agent", lambda n, p: agent_payloads.append(p))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.5)
        await client.close()

    asyncio.run(scenario())
    assert agent_payloads, "agent event missing"
    frame_payload = agent_payloads[0]
    assert frame_payload["stream"] == "tool"
    assert frame_payload["data"] == {"phase": "start"}


# ── H1 interaction_resolve 闭环 ──

def test_resolve_conflict_and_ok_paths():
    ok_app = _make_app()
    conflict_app = _make_app(resolve_conflict=True)

    async def resolve_ok():
        client, _ = _client(ok_app)
        await client.connect()
        return await client.interaction_resolve(
            interaction_id="i-1", resolution={"response": "approve"})

    async def resolve_conflict():
        client, _ = _client(conflict_app)
        await client.connect()
        return await client.interaction_resolve(
            interaction_id="i-1", resolution={"response": "deny"})

    out_ok = asyncio.run(resolve_ok())
    out_conflict = asyncio.run(resolve_conflict())
    assert out_ok["ok"] is True
    assert out_conflict["ok"] is False


# ── G1 能力探测矩阵 ──

def _bare_app(binding: str, version: str) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {"supportedInterfaces": [
            {"protocolBinding": binding, "protocolVersion": version}]}

    return app


def test_capability_gate_matrix():
    for binding, version, should_fail in [
        ("JSONRPC", "1.0", False),
        ("HTTP+JSON", "1.0", True),
        ("JSONRPC", "0.3.0", True),
    ]:
        app = _bare_app(binding, version)

        async def probe():
            client, _ = _client(app)
            return await client.connect()

        if should_fail:
            try:
                asyncio.run(probe())
                raise AssertionError(f"{binding}/{version} must be rejected")
            except A2aUnavailableError:
                pass
        else:
            assert asyncio.run(probe())["protocol"] == 1
```

**跑测**（不许伪绿——`||` 兜底被禁止）：

```text
cd apps/baas && .venv/bin/python -m pytest \
  tests/community/service/bot_run/test_bot_a2a_client.py -q --no-header
目标：全部 PASS。任何 FAIL 为阻断项（不存在“没有测试”出口）。
```

**编排层流式时序专项**（R15-2 要求的 Send_stream 入口;文件同测试文件追加）：
以 `BotA2aClient` 为 transport 构造 `AsyncChatClient`（monkeypatch connect 的
工厂产物），走 `send_stream()` 消费 `stream_queue`，断言 final chunk 到达前
stream 消费者拿到 `type=delta` 的 chunk。**实施者依据实际 `AsyncChatClient`
构造参数（session/uri/client_id…）组装**——本测试走真实消费路径，
不许简化为直调 client。

**Commit**: `test(a2a): consumer-contract tests — ack timing, single-terminal, lossless frames, gate matrix`

### T2' singlebox 探针（正文落齐——R15-8 缺失项）

**Files:** Create `spike/a2a/probes/baas_e2e_smoke.py`

```python
"""1b singlebox E2E 冒烟（正文版，接 1a 的起链配方：mock-model:18080 +
openclaw gateway:30001 + adapter:20003）。

验收断言（评审加强版——非 generic"通过"）：
  1. 真引擎 ack 时序：chat_send 抖手后立即返回（< 1s，无 15s 等待）
  2. 首个 delta 在 final 之前进入消费者（真实引擎流窗口）
  3. interaction/agent 帧无损到达（ख़至少 agent engine_frame roundtrip）
  4. inject 返回 accepted 且真实 transcript 逐字含原文
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "baas_src"))

from _bot_a2a_client import BotA2aClient  # noqa: E402

BASE = "http://127.0.0.1:20003"
SESSION = f"baas-e2e-1b-{time.time_ns()}"


async def main() -> int:
    received: list[tuple[str, dict]] = []

    c = BotA2aClient(base_url=BASE)
    t0 = time.monotonic()
    hello = await c.connect()
    connect_ms = (time.monotonic() - t0) * 1000
    assert hello["protocol"] == 1 and hello["a2a"] is True
    print(f"[connect] card probe 独立探针通过（{connect_ms:.0f}ms）")

    c.on_event("chat", lambda n, p: received.append((n, p)))
    c.on_event("agent", lambda n, p: received.append((n, p)))
    c.on_event("interaction.requested",
               lambda n, p: received.append((n, p)))

    t0 = time.monotonic()
    ack = await c.chat_send(
        session_key=SESSION,
        message="请用一句话介绍 A2A 协议",
        auth_token="OPEN_API:NOT_PROVIDED")
    ack_ms = (time.monotonic() - t0) * 1000
    assert ack["ok"] is True, ack
    assert ack_ms < 1000, f"SSE-fullblocking regression: {ack_ms:.0f}ms"
    print(f"[send] ack @ {ack_ms:.0f}ms（流后台消费）")

    # 流中段采样：final 到达前 delta 是否已进 handler
    await asyncio.sleep(0.3)
    early_states = [p.get("state") for _, p in received]
    if "final" not in early_states:
        assert "delta" in early_states or len(early_states) > 0, \
            "首 delta 未在 final 前到达"

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(p.get("state") == "final" for _, p in received):
            break
        await asyncio.sleep(0.1)
    species = [p.get("state") for _, p in received]
    assert "final" in species, f"60s 无终态：{species}"
    assert "error" not in [s for s in species if s == "error"]
    final_payload = next(p for _, p in received if p.get("state") == "final")
    print(f"[stream] states={species}")
    print(f"[stream] final text: {final_payload['message']['content'][0]['text'][:60]!r}")
    assert len(final_payload["message"]["content"][0]["text"]) > 0

    inj_text = f"1b-e2e-sentinel-{time.time_ns()}"
    inj = await c.chat_inject(SESSION, inj_text)
    assert inj["ok"] is True and inj["status"] == "accepted", inj

    async def _get(path: str) -> str:
        reader, writer = await asyncio.open_connection("127.0.0.1", 20003)
        req = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:20003\r\n"
               "Connection: close\r\n\r\n").encode()
        writer.write(req)
        await writer.drain()
        data = await reader.read()
        writer.close()
        return data.decode(errors="replace")

    transcript_url = "/api/sessions/" + SESSION.replace(":", "%3A") + "/messages"
    transcript = await _get(transcript_url)
    assert inj_text in transcript, f"inject 原文不在 transcript：{transcript[-200:]}"
    await c.close()
    print("== 1b BaaS→A2A singlebox E2E PASS ==")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

**Commit**: `test(a2a): 1b singlebox E2E smoke — ack timing, stream, inject transcript roundtrip`

---

## §6 阶段退出门禁（1b 完成判据——评审约定的回执）

1. **仓库测试**：T1'/bridge/engine 侧全绿；`uv run ... pytest src -q` 全量。
2. **singlebox E2E**：T2' 探针一次性跑通 + 1a 链路（adapter F2/F3/F4/F6
   证据已达，不重复）。
3. **双壳对拍**：`A2A_CLIENT_MODE=off` 走 v3 与 `=grey` 走 A2A，同一
   BaaS send_stream 输入的最终内容一致（真引擎一轮即可）。
4. **fallback 指标**：gate 拒/双壳断/非法 card 三场景 v3 指令链回退生效——
   以 `A2A_CLIENT_MODE=off / grey(card 拔掉) / grey(card 篡 binding)` 三轮跑。
5. **CancelTask 缺口显式清单**：1a router 未实现 CancelTask（engine 侧能力
   声明在 card 有 streaming/pushNotifications，无 cancel）；1b 期间 BaaS
   `chat_abort` 在 A2A 路径返回 `{"ok": False}`——这是本阶段**显式缺口**，
   非 silent；进 1c 补（任务 `CancelTask + 能力卡锁定`）。