# Engine 传输层协议替换：v3 WebSocket 帧 → A2A（架构设计）

日期：2026-10-13（v4，吸收三轮评审：R1 七项 + R2 五项 + R3 三项 + 两处文档收尾）
状态：**R3 阻塞项（安全重试判定机制）已修；总体方案收敛，按既有门禁推进 Spike → 1a → 1b；SDK 锁版/代理链路/真引擎行为以门禁实测为准，文档评审闭环**
范围：engine adapter 传输层协议替换详设 + 全客户端迁移蓝图。BaaS / backend / BCS /
前端 / 桌面各客户端线的详细改造各自后补 spec，本文只画依赖与阶段边界。
分支：`feat/engine-a2a-protocol-replacement`

## 评审吸收映射

| 轮次 | 评审项 | 级别 | 核验结果 | 本版落点 |
|---|---|---|---|---|
| R1 | A2A 1.0 方法名错误 | P1 | 成立（spec §5.3） | 附录 A |
| R1 | session 订阅与 task 恢复混淆 | P1 | 成立（`ws_server.py:830-877`） | §8.2 |
| R1 | 256 环形容量不兑现恢复 | P1 | 成立（`openclaw_resume.py:14-19`） | §8.1 |
| R1 | 新客户端×旧容器未定义 | P1 | 成立 | §10.3 |
| R1 | 原生 EventSource 不适配 | P1 | 成立 | §6.4 |
| R1 | A2A 壳持共享状态 | P1 | 成立（arch.rules 第 7 条） | §5 |
| R1 | HITL/reset/delta 不够具体 | P2 | 成立 | §8（delta 本轮再修） |
| R2 | JSON-RPC 被声明成 REST 绑定 | P1 | **成立**（spec §9.1：JSON-RPC 绑定 = `application/json`；`protocolBinding` 官方枚举 `JSONRPC`/`GRPC`/`HTTP+JSON` 三者并列；`application/a2a+json` 属 HTTP+JSON 绑定与 webhook 载荷） | §6.1/§6.4/§6.5/附录 A |
| R2 | 连续 Message delta 违反流生命周期 | P1 | **成立**（spec 词汇表：Streaming = "Real-time, incremental updates for tasks (status changes, artifact updates)"；artifactUpdate 自带 append/lastChunk） | §8.4（文本流改走 artifact，D10） |
| R2 | 幂等窗口失效后仍承诺同键安全重发 | P1 | 成立（v2 内部矛盾：进程内注册表 + 窗口外重执行 + 重发建议） | §8.5 窗口分离 + 禁止窗口外自动重发 |
| R2 | 活任务精确回放路由不明 | P1 | 成立（spec §3.1.6：SubscribeToTask = 快照+实时，含不了游标回放） | §8.1 三路由显式化 |
| R2 | "BCS 已传幂等键"依据不成立 | P2 | **成立**（`a2a_chat/mod.rs:902`：`idempotency_key: None`；run_id 只进帧 ID） | §6.2/§8.5 事实更正 + 迁移任务 |
| R3 | "窗口内才可重试"缺可执行判定机制 | P1 | 成立（客户端无从得知服务端进程内记录是否存活；先检查后重发与重启存在竞态） | §8.5.2 retry-only 服务端原子判定（D14）+ F5 改真实故障注入 |
| R3 | artifact 首块/lastChunk 语义歧义 | P2 | 成立（spec：lastChunk = 该 artifact 最后一块，非传输块结束） | §8.4 阶段表 |
| R3 | HITL 重复提交与全局幂等规则冲突 | P2 | 成立（网络重试 vs 新业务操作两类请求需区分） | §8.6 处理顺序 + 分类 |
| R3 | card 示例缺必填 skills / §13 引用方式 | 文档 | 成立（spec：skills 必填） | §6.5 最小 conversation skill；§13 直引 §3.1.2 |

## 1. 背景与问题

engine 模块当前的后端↔引擎信道是自定义 WebSocket JSON 帧协议（下称 v3）：

- 帧协议：`engine/adapter/src/engine/community/kernel/frames.py:34`（`PROTOCOL_VERSION = 3`
  ，req/res/event 三帧、512KB 上限、TICK 30s）
- 服务端：`engine/adapter/src/engine/community/api/transport/ws_server.py`
  （connect/hello-ok 握手；chat.send / chat.abort / chat.subscribe / chat.resume /
  chat.inject / sessions.reset / interaction.resolve）
- 会话订阅与断流恢复现有实现：`ws_server.py:830-877`（sessionKey 订阅 + live inject
  监听绑定）、`openclaw_resume.py:14-19`（MAX_RUNS=16、MAX_EVENTS_PER_RUN=20,000、
  MAX_BYTES_PER_RUN=16MiB、MAX_BYTES_TOTAL=64MiB、SUBSCRIBER_QUEUE_SIZE=256、
  TERMINAL_TTL=180s、CACHE_LIMIT）
- 客户端族：BaaS `_bot_websocket_client.py:85`、backend `grt_chat_service.py:131`、
  BCS `a2a_chat/mod.rs`（`build_chat_send_frame`，**payload 中 `idempotency_key`
  现为 `None`**，run_id 仅作帧 ID）、前端/桌面（直连 `127.0.0.1:20003`）
- 引擎选择 URL：BaaS SPI `_protocols.py:25-47`（`ws_path()`）

**动机与收益表述**：标准化通用通信部分（方法语义、版本协商、Task 生命周期、
消息/产物模型、错误模型），Avernet 继续 fork 维护 inject、session 订阅、精确回放
等扩展契约；私有协议税缩小但不归零。

## 2. 目标与非目标

### 目标

1. engine adapter `:20003` 以 **A2A 1.0.x JSON-RPC 绑定**（JSON-RPC 2.0 over HTTP，
   `application/json`；流式 = SSE `text/event-stream`）承载 v3 全部语义
2. 引擎插件上游传输零改动；SPI 遵循最小必要调整原则（§6.3）
3. 行为完全等价（D3 红线），以 §7-§8 契约 + 对拍测试为口径
4. 全程可回退；混合版本部署有显式兼容矩阵（§10.3）

### 非目标

- 管理 REST 89 条路由不动；对外公开门面（公网+OAuth2/OIDC）不在本期
- 不引入 HTTP+JSON（REST）绑定与 gRPC 绑定（`application/a2a+json` 媒体类型随之不用）
- 各客户端线详设各自后补 spec

## 3. 决策记录

| # | 决策 | 状态 |
|---|------|------|
| D1 | 范围 = adapter 详设 + 迁移蓝图 | 已拍板 |
| D2 | 落点 = Avernet community 传输层，ocb 经 submodule 继承 | 已拍板 |
| D3 | 行为完全等价（extension 补缺口） | 已拍板 |
| D4 | 官方 a2a-sdk + 防腐层，降级路径 = 手写 wire 层 | 已拍板 |
| D5 | 基线 = A2A 1.0.x | 已拍板 |
| D6 | **绑定 = 仅 JSON-RPC**（card `protocolBinding: "JSONRPC"`；`application/json`；不采用 HTTP+JSON / a2a+json） | v3 修正（R2-1） |
| D7 | 恢复容量承诺 = 对齐 `openclaw_resume.py` 常量 | v2 新增 |
| D8 | 新客户端按目标容器能力选协议；跨协议重试共享幂等键（**仅窗口内有效**，见 D11） | v2 新增，v3 收紧 |
| D9 | 协议层状态/事件归 kernel；A2A 与 v3 都是薄 adapter | v2 新增 |
| D10 | 文本流走 **artifact 分块更新**（append/lastChunk/同 artifactId 替换），不引入自定义 delta extension；标准流生命周期合规 | v3 新增（R2-2） |
| D11 | 幂等仅进程内承诺：窗口失效（重启/超窗）后契约 = 禁止自动重发，返回"执行结果未知"语义；不承诺跨重启重试安全 | v3 新增（R2-3） |
| D12 | 活任务/终态任务的**精确回放**统一走恢复 extension（含游标回放+锁内转 live）；标准 `SubscribeToTask` 仅提供快照+实时（spec §3.1.6） | v3 新增（R2-4） |
| D13 | BCS 需补齐幂等键传递（现在 `idempotency_key: None`）；逻辑请求 ID = A2aChatCommand.run_id，重试不得再生成 | v3 新增（R2-5） |
| D14 | **retry-only 安全重试语义**：FIRST（首提，登记新键开执行）/ RETRY-ONLY（不确定结果后的重试）两类请求；服务端 kernel **原子**判定——记录在则返回既有结果，记录不在则原子返回"执行结果未知"且**不得创建新执行**；A2A metadata 与 v3 params 映射同一 kernel 操作；不引入持久化 | v4 新增（R3-1） |

## 4. 方案选择

采用 **方案 A：绞杀者 + 兼容门面**（薄双壳共享 kernel）。方案 B（一次切换）被容器
版本 skew 否决；方案 C（v3↔A2A 转换层）仅作收割阶段长尾容器书面转换方案。

推进顺序：先抽共享内核验证 v3 行为不变（阶段 1a），再挂 A2A 壳、单客户端灰度
（阶段 1b），验收后扩展（2/3），最后按版本清单+流量双条件收割（4）。

## 5. 目标架构（D9）

```
A2A 客户端：BaaS / backend / 前端 / 桌面 / BCS / 未来外部
   │ A2A 1.0.x JSON-RPC 绑定：JSON-RPC 2.0 over HTTP（application/json）＋ SSE
   ▼
gateway → agentclawproxy /proxypass/{target}（HTTP+SSE 转发；SSE 缓冲 = G1 首验）
   ▼
engine adapter :20003
┌────────────────────────────────────────────────────────────────┐
│ delivery adapters（薄壳，arch.rules 第 7 条：不持领域策略）       │
│   api/transport/a2a/   A2A 壳：DTO/错误/流事件映射 + card        │
│   api/transport/ws_server + kernel/frames  v3 壳（阶段 4 删）    │
├────────────────────────────────────────────────────────────────┤
│ Service API（kernel protocol）                                   │
│   ├ Task 生命周期（start/cancel/get + 统一事件序号 seq 的流）     │
│   ├ Session 事件订阅 + 旁观 fanout（§8.2）                       │
│   └ inject / resolve_interaction / reset / supports(501)        │
├────────────────────────────────────────────────────────────────┤
│ kernel task/session core ＋ shared event store                  │
│   容量与恢复规则 = D7；幂等 registry（进程内，§8.5）             │
├────────────────────────────────────────────────────────────────┤
│ Engine Plugin API → 插件实现（SPI 最小调整）                     │
│   openclaw | teclaw (community)；claude_code | aicoding |        │
│   hermes | deepseek_harness (corp)                              │
└───────────────┬──────────────┬──────────────┬───────────────────┘
        ws://127.0.0.1:18789  ws://127.0.0.1:18900  JSON-RPC / stdio
```

分层依据：`docs/arch/arch.rules.md` 第 3/7/16/25 条。A2A 层只有 DTO 与流事件映射，
任务状态、回放日志、恢复/幂等规则全部在 kernel。**统一游标**：每个 run 一个单调
事件序号 `seq`（kernel 分配，publish 在单一互斥域内递增 + fanout）——§8.1 回放
游标与 §8.4 流事件排序共用同一序号空间，不存在两套游标。

## 6. 组件详设

### 6.1 协议绑定与端点（A2A 1.0 JSON-RPC 绑定，D6）

| 端点（`:20003`） | 说明 |
|------|------|
| `GET /.well-known/agent-card.json` | Agent Card（§6.5，`protocolBinding: "JSONRPC"`） |
| `POST /rpc`（单端点，spec §9.2 示例惯例，最终路径由 card.url 声明） | JSON-RPC envelope：`{"jsonrpc":"2.0","id":…,"method":"SendMessage","params":…}`；`Content-Type: application/json`；服务参数（A2A-Version / A2A-Extensions）走 HTTP 头 |
| `SendStreamingMessage` / `SubscribeToTask` / 恢复与 session extension | 同端点，响应 `text/event-stream`，data 为 JSON-RPC result（StreamResponse 或扩展事件） |
| `/ws`、`/api/{engine}/ws` | v3 壳（阶段 4 删除） |

A2A 壳结构（薄映射）：`api/transport/a2a/{router.py, card.py, sse.py, protocol.py}`；
`protocol.py` 是 `a2a_sdk` 唯一 import 边界（D4 防腐层）。`application/a2a+json`
媒体类型不使用（属 HTTP+JSON 绑定与 webhook 载荷，见附录 A.2 注记）。

### 6.2 kernel task/session core + shared event store

从 `ws_server.py` / `openclaw_resume.py` 抽入 kernel（行为零变，阶段 1a 先行+对拍）：
run 生命周期、事件缓冲与恢复容量（D7 常量，CACHE_LIMIT 语义保留）、会话订阅注册表
+ inject 监听绑定、幂等 registry（`(contextId, 幂等键)`，进程内，见 §8.5/D11）。

Service API（Protocol，示意，契约语义以 §8 为准）：

```python
class EngineChatService(Protocol):
    async def start_task(self, engine, context, message, *, idempotency_key,
                         retry_only: bool = False) -> TaskHandle
    async def cancel_task(self, engine, task_id) -> CancelResult
    async def inject_context(self, engine, context, message) -> None
    async def resolve_interaction(self, engine, interaction_id, resolution) -> None
    async def replay_task_events(self, task_id, since_seq) -> TaskEventStream   # 精确回放（D12，全态适用）
    async def subscribe_context(self, context) -> ContextEventStream           # Session 级（§8.2）
    def supports(self, capability) -> bool
```

### 6.3 Engine Plugin SPI：最小必要调整

不预设重排。默认现有 `chat.send / chat.inject / interaction` 分发接口承接；仅当
实证无法表达时才调契约（候选仅 session 订阅入口与统一 seq 游标两处），附 arch.rules
第 16 条传播分析。判断在阶段 1a 落地，产出"需要动契约的最小清单"。

### 6.4 客户端侧：兼容矩阵与协议选择（D8）

- 前端/桌面：支持 POST body、自定义 headers、ReadableStream 解析 SSE 的 **fetch
  流式客户端**（或经真实浏览器链路验证的 SDK 浏览器版）；EventSource 不可用
- BaaS 保留 v3 回退路径到收割完成；能力发现（**口径修正**：目标容器 card 可拉取且
  `supportedInterfaces[].protocolBinding == "JSONRPC"` 且 `protocolVersion` 为 1.0 →
  走 A2A，否则 v3）；缓存失效 = 容器 rebuild/publish 事件 + TTL 兜底；回滚 =
  组件级配置开关
- 跨协议超时换发：不确定结果后的自动重试必须携带 **retry-only 语义**（A2A 侧
  SendMessage metadata 扩展；v3 侧帧 params 字段；两协议映射同一 kernel 操作，
  §8.5/D14）；**禁止**以 FIRST 语义重发不确定结果——服务端无法探测记录是否
  已随重启丢失（竞态），这是契约边界而非技术防护；收到"结果未知"后显式再执行
  = 新用户操作（新幂等键）
- 旧容器的去重行为不受新 kernel registry 保护：registry 只存在于升级后容器的
  kernel 中；旧容器上的重发保护 = v3 现状（未实证，见 §8.5 末条）

### 6.5 Agent Card（1.0 结构，D6 修正）

```json
{
  "name": "<bot name>",
  "description": "<bot 描述>",
  "version": "<adapter 版本>",
  "supportedInterfaces": [
    { "url": "{deploy-base}/proxypass/{target}/rpc",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0" }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extensions": [ /* §8 extensions 声明 */ ]
  },
  "skills": [
    { "id": "conversation", "name": "Conversation",
      "description": "<bot 描述摘要>" }
  ],
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain", "application/json"]
}
```

`url` base 由部署侧注入（config 差异不进业务码道）；`skills` 为 spec 必填字段——card 示例已内置最小 conversation skill，G1a 可直接拿一份可校验 card；动态 skills 来源 = O3；extension URI
命名在 G2 与 SDK 版本一起锁定。

### 6.6 鉴权与版本协商

传输级鉴权语义不变（proxypass x-proxypass-token JWT；card securitySchemes 以 Bearer
声明）。版本协商：`A2A-Version: 1.0` 请求头（或请求参数）+ card
`supportedInterfaces[].protocolVersion`；不支持 → `VersionNotSupportedError`。
`x-moltis-mcp-token` 历史参数退役。

### 6.7 错误映射

`supports()` 保留 501 语义 → spec 错误码（逐码对表附录 A 定稿）；精确回放不可得
（CACHE_LIMIT / 终态窗口外 / 进程重启）→ 显式错误（带 reason 与 lastResumeSeq）；
Task 级失败进 `TASK_STATE_FAILED`；SSE 中途异常以流内事件 + 恢复路径表达。

### 6.8 观测

`a2a_task_duration_seconds`、`a2a_first_event_latency_seconds`、
`a2a_sse_reconnect_total`、`a2a_replay_refused_total`（CACHE_LIMIT/超窗拒绝数）、
`a2a_capability_fallback_total`（BaaS 回退 v3 比率）。

## 7. 语义映射契约表

| v3 | A2A 1.0（JSON-RPC 绑定） | 契约要点 | 对拍 |
|----|--------------------------|----------|------|
| connect/hello-ok | 无握手 + `A2A-Version` 头 | 版本不支持 → `VersionNotSupportedError` | TC-CONN |
| chat.send | `SendMessage`（contextId 续会话） | 幂等窗口语义 §8.5 | TC-SEND |
| chat 流式 EventFrame | `SendStreamingMessage`（SSE） | 首事件 Task；文本增量走 `artifactUpdate`（§8.4/D10）；流在终态终止 | TC-DELTA |
| chat.abort | `CancelTask`（幂等） | 终态后取消 → 标准错误 | TC-CANCEL |
| chat.resume（run 精确恢复） | 恢复 extension `ReplayEvents`（D12：活/终态适用） | 快照≠回放；`SubscribeToTask` 只做快照+实时 | TC-RESUME |
| 活任务重挂（无精确要求） | 标准 `SubscribeToTask` | 首事件 = 当前 Task 快照 | TC-RESUME |
| chat.subscribe（会话订阅） | extension `SubscribeToContext` | 与 Task 契约分离 | TC-SESSUB |
| chat.inject | extension inject | 会话级 fanout，非 Task | TC-INJECT |
| interaction.requested/resolved | `TASK_STATE_INPUT_REQUIRED` + 同 task 续消息 | §8.6 | TC-HITL |
| sessions.reset | extension `ResetContext` | §8.7 | TC-RESET |
| 每引擎 `/api/{engine}/ws` | 删除（card 单接口） | — | TC-CONN |
| 501 能力探测 | spec 错误码映射 | `supports()` 不变 | TC-501 |
| 管理 REST 89 路由 | 不动 | — | — |

## 8. 语义契约明细（可验收口径）

### 8.1 Task 恢复契约（D7/D12，三路由显式化）

| 路由 | 适用 | 行为 |
|---|---|---|
| 标准 `SubscribeToTask` | 活任务 | 首事件 = 当前 Task 快照，其后实时事件；流在终态终止（spec §3.1.6 原文）；**不承诺游标回放** |
| 恢复 extension `ReplayEvents` | 活 + 终态任务（180s 内） | 按 run 级 `seq` 游标逐事件回放；活任务回放完**锁内无缝转 live**（`openclaw_resume` attach/publish 同锁语义）；终态任务回放完即止 |
| 拒绝精确回放 | CACHE_LIMIT 触达后 / 终态 180s 窗口外 / 进程重启后 | **显式拒绝**（错误码 + reason + lastResumeSeq），客户端转 `GetTask` 快照 + 重挂实时；不存在"超容量回放"这种承诺 |

- 游标：每事件携带 `seq`（run 级单调，单一序号空间，§5）；客户端去重 = 丢弃
  `≤ 已见最大 seq`；回放与实时交界的无丢失由锁内交接保证
- 容量：per-run 20,000 事件 / 16MiB，全局 64MiB / 16 run；触界即停止缓存并置
  `reason=CACHE_LIMIT`（对客户端显现为上表第三路由）
- 多流共存（spec §3.5 直接对齐）：任务生命周期独立于任何单个流；同任务多流
  广播同序事件；关闭一条流不影响任务与其他流
- SSE 断开不影响执行（内核 run task 持有执行；订阅者队列满被踢仅影响投递）
- adapter 重启：进程内事件缓冲全失，不承诺跨重启恢复（与幂等 D11 一致）

### 8.2 Session/context 事件契约

extension `SubscribeToContext` / `UnsubscribeFromContext`（SSE 流，参数
`contextId`）。服务端推送：他人发起 run 的 task 事件摘要、旁观注入事件
（inject-* run 不进 task 状态机）、会话通知。等价判定用例 = 前端打开已存在会话
且自身无活跃 task 时，BCS 后台 inject 必须可见（F3）。live inject 监听绑定语义
随 `_ensure_openclaw_inject_listener` 移入 kernel。

### 8.3 inject 契约

extension 方法（A2A-Extensions 头协商）：message + contextId + 参与方/路由上下文
（最小集 = O2）；执行为非 Task、不占 run 状态机；fanout 至 §8.2 订阅者；幂等键
适用（防注入重放，键 = 逻辑注入 ID）。

### 8.4 delta / 文本流契约（D10：走 artifact，标准流生命周期合规）

依据：spec 将 Streaming 定义为 "Real-time, incremental updates for tasks (status
changes, artifact updates)"——流是 Task 更新流，不是连续 Message 流。

- **文本增量**：`TaskArtifactUpdateEvent`，run 级固定 `artifactId`（text artifact）。
  分块语义按阶段表定死——spec：`append` = 追加到**此前已发送的同 ID artifact**；
  `lastChunk` = **该 artifact 的最后一块**（不是当前传输块结束）：

  | 阶段 | append | lastChunk | 说明 |
  |---|---|---|---|
  | 首块（建立文本 artifact） | false/省略 | false | 建立 artifact |
  | 中间增量块 | true | false | |
  | 最后一块（无终稿替换） | true | true | 封口 |
  | 完整终稿替换（如需） | false | true | 全量替换，为最终更新 |

  终稿替换发出前**不得**提前宣布 artifact 结束；替换之后再进 Task 终态（对齐现
  前端对 final 覆盖快照的既有行为）。验收用例：三段增量 + 一次完整终稿替换 →
  最终文本正确、不中途结束、不重复追加（并入 G3）
- 排序/去重：`artifactUpdate.metadata.seq` = §5 统一游标；客户端按序拼接、按
  `seq` 去重
- `StreamResponse.message` 仅承载完整独立消息（如 inject 的回显），不作增量通道
- 产物（文件/结构化报告）同走 `artifactUpdate`（独立 artifactId）
- **不做**自定义连续-Message delta extension（若实测 artifact 方案无法承接某引擎
  的流形态，须回到本节重新评审并声明为显式 extension，不得默认其为标准行为）

### 8.5 幂等契约（D8/D11/D14：窗口分离、安全重试判定、删除越窗安全重发承诺）

两个窗口**分开定义**，不得互相推导：

| 窗口 | 定义 | 语义 |
|---|---|---|
| 回放窗口 | 终态 run 的事件缓冲保留期（D7：180s，进程内） | 决定 §8.1 精确回放可用性，**与是否可重发无关** |
| 幂等窗口 | kernel 幂等 registry 记录存活期（进程内；= run 存活期 + 终态保留期，随进程重启归零） | 决定同键重发的去重保护 |

契约正文：

1. 幂等键：A2A 侧 = `messageId`；v3 侧 = `ChatSendParams.idempotency_key`。
   逻辑请求 ID 由客户端生成（BCS = `A2aChatCommand.run_id`，D13：重试不得再生成），
   映射到两协议的键字段；帧级断言：发送键非空且跨重试一致（contract test）
2. **retry-only 判定机制（D14，服务端原子）**：请求分两类，判定与处理在 kernel
   内原子完成——客户端**无需**（也无法）预判服务端记录是否存活：

   | 请求 | 记录查找结果 | 处理 |
   |---|---|---|
   | FIRST（首次提交） | 不存在 | 登记新键并开始执行 |
   | FIRST | 存在 | 去重，返回既有 task/终态 |
   | RETRY-ONLY（不确定结果后的重试） | 存在 | 返回既有 task/终态结果 |
   | RETRY-ONLY | 不存在 | 原子返回"**执行结果未知**"（显式错误），**禁止创建新执行** |

   - 携带方式：A2A SendMessage metadata 扩展（`avernet.retryOnly: true`）；v3 帧
     params 字段；内部 extension，非 A2A 标准字段（附录 A.3 声明）
   - spec"Send MAY idempotent by messageId"的兜底经此标志显式激活
   - "世代/重试凭据"类替代方案不采用：首次响应丢失 + 记录消失时凭据同样不存在，
     相比 retry-only 无增益、多一段实现面
3. **窗口失效**（adapter 重启 / 幂等记录过期）：RETRY-ONLY 请求由上表原子返回
   "执行结果未知"（安全重试可执行，D14）；**禁止**以 FIRST 语义自动重发不确定
   结果；显式再执行 = 新的用户操作（客户端生成**新幂等键**），副作用风险显式
   交回调用方决策
4. F4 覆盖窗口内同键重发（含跨协议）；F5 覆盖**真实故障路径**下的 retry-only
   拦截（服务端拒绝第二次执行，执行次数监控验证恰好一次）
5. 事实边界：v3 引擎 adapter 当前对 `idempotency_key` 的实际去重行为**未实证**；
   D8 的保护只存在于新 kernel（升级后容器）。旧容器上的重发语义 = v3 现状，
   迁移期间 BCS/客户端不得对旧容器做跨协议安全重发假设（§10.3 矩阵备注）

### 8.6 HITL 契约

- 触发：Task 转 `TASK_STATE_INPUT_REQUIRED`，statusUpdate 携带 `interactionId`
 （metadata，对齐现 interaction.requested 参数）
- 决策：`SendMessage`，同 `taskId` + `contextId`（spec：不匹配 MUST 拒绝）；决策
  内容 = parts + metadata（对齐现 interaction.resolve）
- **处理顺序与请求分类**（裁掉与 §8.5 幂等规则的冲突）：先查同键幂等记录，
  再按 interaction 当前状态校验新操作——
  - **A. 同一审批的网络重试**（审批已处理、响应丢失、客户端携**同 messageId**
    重发）：查到记录 → 返回**先前处理结果**，不因状态已离开 input-required 而
    把成功审批渲染成失败（TC-HITL-RETRY）
  - **B. 对已解决 interaction 的新业务操作**（客户端用**新 messageId** 再次
    批准/拒绝/修改）：记录未中后再校验状态 → "已解决/状态冲突"错误
    （TC-HITL-DUP）
- cancel 与已处理结果的关系：cancel 只改写 run 状态（run → canceled，且仅当
  task 尚未到终态），**不回写已成功处理的 resolution 记录**——A 类网络重试仍可
  取回历史决策结果；迟到 cancel 在运行方向胜出，但不覆盖既有成功结果。未决
  超时 → 停留 input-required 至 run deadline

### 8.7 reset 契约

extension `ResetContext(contextId)`：保留 contextId，清空引擎会话历史；运行中
task 不受影响、订阅不撤销、事件缓冲按 run 独立。TC-RESET 对拍现 v3 行为固化；
对拍暴露额外副作用时以 v3 行为回填（阶段 1 显式产出）。

## 9. 测试策略

1. 对拍（双壳等价回归）：TC-* 全集
2. conformance：**官方 SDK client 读 Card 自动选绑定**后打真 adapter
   （openclaw + claude_code 实引引擎），全标准方法 + extension 矩阵——先于任何
   故障恢复场景（见 §10.1 门禁顺序）；禁手工指定端点 + 自研解析器先跑通 happy
   path（会掩盖绑定/生命周期类缺陷，R2 两轮教训）
3. flow coverage：singlebox a2a profile；changed-line ≥80% 走 `ci_test.sh` 同口径
4. Spike 失败用例 F1-F6（§10.1）
5. BCS（阶段 3）：Rust mock A2A server contract test + 帧级幂等键断言
6. arch 结构测试：kernel 无 transport import、A2A 壳无状态所有权

## 10. 迁移蓝图

前置硬约束同前（容器逐 bot 升级 → 双协议窗口下限 = publish-restart 节奏）。

### 10.1 阶段 0：Spike（验收门禁，顺序修正：标准路径先行）

| 门 | 内容 | 通过标准 |
|----|------|----------|
| G1a | **官方 SDK 按 Card 自动选择 JSONRPC 绑定**并消费完整流生命周期（Task 首事件 → artifact 分块流 → 终态终止），happy path | SDK 自动发现可用；流生命周期合规；禁手工指定端点 |
| G1b | SSE 过 agentclawproxy + gateway 全链路 + 真实浏览器 fetch 流式链路（跨域/鉴权头/AbortController/断流恢复） | 无代理缓冲；断链行为明确 |
| G2 | a2a-sdk 依赖通路 + 版本锁定（SDK 与 spec 精确版本回填附录 A；extension URI 定版 O8） | 可得且锁定 |
| G3 | artifact 流语义接真引擎（claude_code 文本流 → append/lastChunk/替换） | 打字机 UX 无可感降级 |
| G4 | inject + session 订阅 extension 原型 | BCS 旁观用例（含 F3） |
| G5 | 性能基准 | 建连 P50 相对 WS 增量 ≤50ms；吞吐 ≥WS 90%；内存持平 |

失败用例（**置于 G 门之后**，标准路径全绿才测）：

- F1：超出 D7 容量后断线重连 → 精确回放**显式拒绝**（CACHE_LIMIT 错误 + 快照降级）
- F2：task 终态后重连（180s 内：ReplayEvents 回放；窗外：拒绝 + GetTask 语义）
- F3：无前台活跃 task 时后台 inject 经 session 订阅可见
- F4：窗口内响应丢失，同幂等键重发（含跨协议）不二次执行
- F5：**真实故障路径**：服务端执行完成 → 响应被截断 → adapter 重启（记录清零）→
  客户端实际发起 retry-only 重发（测试**不**预告知"窗口已失效"）→ 服务端拒绝
  第二次执行并返回"执行结果未知"；执行次数监控验证恰好一次
- F6：活任务断线期间产生多条 delta / tool progress / interaction 事件；重连按
  `seq` 逐事件补齐（不只是终稿文本或当前快照）

任何 G 门不过 → 设计回炉（G2 过不了切 D4 降级）。

### 10.2 阶段 1-4

- **阶段 1a：内核抽取**（行为零变）：chat 分发/恢复/会话订阅/事件缓冲/幂等
  registry 移入 kernel；产出 SPI 最小调整清单（§6.3）；退出 = v3 对拍全绿
- **阶段 1b：A2A 壳 + BaaS 灰度**（按目标容器能力分流，§10.3）；对拍 TC 全绿 +
  conformance 全绿 + fallback 指标披露；**BCS 幂等键补齐**（D13：run_id →
  `idempotency_key` 映射 + 帧级断言，属 BCS 侧小改造，可在此阶段先行）
- **阶段 2**：backend + 前端/桌面 fetch 流式客户端
- **阶段 3**：BCS（Rust A2A client + inject/session 扩展 + 在线判定改健康轮询）；
  桌面滞后 → C 方案转换层兜底
- **阶段 4 收割（双条件）**：v3 流量 < 阈值（建议 1%）**且**存量 v3-only 容器版本
  清单清零或每目标有书面转换方案；删 `frames.py` + v3 壳 + v3 客户端路径；
  单独 PR；收割前保留 ≥1 可回退 release

### 10.3 客户端 × 容器兼容矩阵

| 客户端＼容器 | 旧（仅 v3） | 双壳 | 收割态（A2A-only） |
|---|---|---|---|
| 旧客户端（v3） | ✓ | ✓ | ✗（阶段 3 退出 = 旧客户端迁完） |
| 新客户端（A2A 优先 + v3 回退） | ✓（v3 回退；**旧容器无新幂等保护**，见 §8.5.5） | ✓ | ✓ |
| 能力发现 | card 拉取失败或非 `JSONRPC`/1.0 → v3 | — | — |

缓存失效 = rebuild/publish 事件 + TTL；组件级配置开关整体回退 v3。

## 11. 风险登记

| # | 风险 | 等级 | 缓解 |
|---|------|------|------|
| R1 | spec 演进（连续 breaking 史） | 高 | D5 + `protocol.py` 防腐层（O5） |
| R2 | SSE 过代理链路缓冲/超时 | 高 | G1b 首验 |
| R3 | artifact 流形态与既有前端管道不匹配 | 中 | G3 + TC-DELTA 对拍 | 
| R4 | extension 固化 | 中 | 最小面定义（O6） |
| R5 | 多团队排期 | 高 | 依赖顺序锁死；收割双条件 |
| R6 | 容器 skew | 高 | §10.3 + 回退；收割双条件 |
| R7 | a2a-sdk 通路缺失 | 中 | G2 + D4 降级 |
| R8 | 命名撞车（内部 `a2a_chat`） | 低 | 语境区分 |
| R9 | 内核抽取引入行为漂移 | 中 | 1a 独立对拍先于 A2A 代码 |
| R10 | 窗口外幂等承诺被客户端误读 | 中 | §8.5 契约 + F5 + 监控计数；SDK 侧错误码显式化 |

## 12. 开放决策

| # | 决策 | 推荐 | 何时定 |
|---|------|------|--------|
| O2 | inject 上下文最小字段集 | 按 `ChatSendParams` 参与方字段最小映射 | G4 |
| O3 | card skills 来源 | bot manifest 派生，回退单 conversation skill | 阶段 1b |
| O4 | 对外门面启动时机 | 阶段 1b 后评估 | 阶段 1b 后 |
| O5 | a2a-sdk 跟版策略 | minor 自动跟、major 评审制 | G2 |
| O6 | 去 extension 二期纯化评估 | 阶段 4 前一次 | 阶段 4 前 |
| O8 | extension URI 命名空间 | 内部 URI 方案 | G2 |

（O7 已关闭 → D10：文本流走 artifact，不引入 delta extension。）

## 附录 A：协议基线（A2A 1.0，spec 抓取 2026-10-13）

> 依据：https://a2a-protocol.org/latest/specification/ 。本附录引用均来自当日抓取
> 正文的关键词窗口。SDK 精确版本由 G2 锁定后回填（锁定前不进阶段 1b）。

### A.1 方法映射（spec §5.3/§9.1）

| 功能 | JSON-RPC 方法（PascalCase） | REST 端点（仅参考，不采用） |
|---|---|---|
| 发消息 | `SendMessage` | `POST /message:send` |
| 流式发消息 | `SendStreamingMessage`（SSE） | `POST /message:stream` |
| 查任务 | `GetTask`（幂等） | `GET /tasks/{id}` |
| 列任务 | `ListTasks` | `GET /tasks` |
| 取消 | `CancelTask`（幂等） | `POST /tasks/{id}:cancel` |
| 重挂任务流 | `SubscribeToTask`（快照+实时） | `POST /tasks/{id}:subscribe` |
| 推送配置 | `CreateTaskPushNotificationConfig` 等 | （本期不用） |

### A.2 关键对象与绑定语义

- **JSON-RPC 绑定（采用，spec §9.1）**：JSON-RPC 2.0 over HTTP(S)；请求/响应
  `Content-Type: application/json`；方法名 PascalCase；流式 = SSE
  （`text/event-stream`）；单端点 + envelope（§9.2 示例端点 `/rpc`）；服务参数
  （`A2A-Version`、`A2A-Extensions`）走 HTTP 头
- **card `protocolBinding`（官方枚举）**：`JSONRPC` / `GRPC` / `HTTP+JSON` 三者
  并列——`HTTP+JSON` 是 REST 风格的另一绑定，**与 JSON-RPC 绑定不是一回事**；
  `application/a2a+json` 是 HTTP+JSON 绑定与 webhook 载荷的媒体类型（IANA
  14.1.1），JSON-RPC 绑定不用
- `SendMessageRequest`：`tenant`（可选，须匹配所选 AgentInterface）、`message`
  （必填）、`configuration`、`metadata`；`Message`：`messageId`（必填）、
  `contextId` 可选、role、parts
- 流模型：词汇表定义 Streaming = "Real-time, incremental updates for tasks
  (status changes, artifact updates)"；`StreamResponse` 恰含其一
  （task/message/statusUpdate/artifactUpdate）；`SubscribeToTask` 首事件 = 当前
  Task 快照，流在终态终止（§3.1.6）；任务生命周期独立于单流生命周期、多流
  同序广播（§3.5）
- `TaskArtifactUpdateEvent`：`artifact` + `append`（true=追加到同 ID artifact）
  + `lastChunk`（true=最后一块）+ `taskId`/`contextId`/`metadata`
- 续交互：同 `taskId`+`contextId` 消息；不一致 MUST 拒绝；`input-required` 状态
  承载 HITL；幂等（§3.3.1）：Get 天然 / Send 经 `messageId` MAY / Cancel 幂等

### A.3 本仓 extension 清单（与标准方法分表，均以 `A2A-Extensions` 头协商）

| extension | 用途 | 替代的 v3 语义 |
|---|---|---|
| `ReplayEvents` | 精确回放：活+终态任务，`seq` 游标逐事件回放，活任务锁内转 live | chat.resume |
| `SubscribeToContext`/`UnsubscribeFromContext` | 会话级事件订阅 | chat.subscribe/unsubscribe |
| inject | 旁观注入（非 Task） | chat.inject |
| `ResetContext` | 会话历史重置 | sessions.reset |
| （SendMessage metadata：`interactionId`） | HITL 关联 | interaction.resolve 的承载 = 同 task 续消息 |
| （SendMessage metadata：`retryOnly`，D14） | 不确定结果后的安全重试（服务端原子判定） | v3 帧 params 的 retry 语义字段 |

wire 样例 golden 文件在 G2 锁 SDK 后由官方样例生成回填（conformance golden，不手拼）。

## 13. 参考与证据

- A2A spec v1.0.x：https://a2a-protocol.org/latest/specification/ （本附录为关键词
  窗口提取，非全文通读；spec §3.1.2 对「单 Message 流 / Task 生命周期流」的
  分类经 R3 评审核对原文确认，§8.4 的依据直接引用该条款；词汇表 Streaming
  定义与 §3.1.6/§3.5 为补充证据）
- 代码证据：`ws_server.py:830-877`（session 订阅）、`openclaw_resume.py:14-19/69-152`
  （恢复容量/CACHE_LIMIT/锁内交接）、`a2a_chat/mod.rs:902`（`idempotency_key:
  None`——v2 的"BCS 已传幂等键"失实，本轮更正；run_id 仅进帧 ID）、
  `docs/arch/arch.rules.md` 第 3/7/16/25 条
- 评审记录：R1（5 P1+2 P2）+ R2（4 P1+1 P2）+ R3（1 P1+2 P2+2 文档收尾），吸收映射见文首表
