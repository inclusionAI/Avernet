# Engine 传输层协议替换：v3 WebSocket 帧 → A2A（架构设计）

日期：2026-10-13（v2，吸收评审 7 项：5 P1 + 2 P2）
状态：**方向通过、详设随本轮修订；Spike 获准进入；全客户端迁移未开闸**
范围：engine adapter 传输层协议替换详设 + 全客户端迁移蓝图。BaaS / backend / BCS /
前端 / 桌面各客户端线的详细改造各自后补 spec，本文只画依赖与阶段边界。
分支：`feat/engine-a2a-protocol-replacement`（基于 origin/dev a887e4dd5）

## 评审吸收映射

| 评审项 | 级别 | 核验结果 | 本版落点 |
|---|---|---|---|
| 1. A2A 1.0 方法名错误（message/send → SendMessage 等） | P1 | **成立**（官方 spec §5.3 方法映射表提取确认；`message/send` 在 1.0 正文零命中） | 附录 A 协议基线；§6.1 |
| 2. session 订阅与 task 恢复混淆 | P1 | **成立**（`ws_server.py:830-877`：chat.subscribe 按 sessionKey 绑定 live inject 监听） | §6.2 / §8.2 双契约拆分 |
| 3. 256 环形日志不兑现恢复等价 | P1 | **成立**（`openclaw_resume.py:14-19`：20k 事件/16MiB/run、64MiB 总量、终态 180s；256 是订阅者队列） | §8.1 恢复契约 |
| 4. 新客户端×旧容器未定义 | P1 | 成立（文档自身模型可推导） | §6.4 / §10.3 兼容矩阵 |
| 5. EventSource 无法承载 POST JSON-RPC | P1 | 成立（Web 平台事实） | §6.4 / G1 措辞修正 |
| 6. 共享状态不得放 A2A 壳 | P2 | **成立**（`docs/arch/arch.rules.md` 第 7 条 Invariant："adapters must not own domain policy"） | §5 架构修正 |
| 7. HITL/reset/delta 不是可验收契约 | P2 | 成立 | §7 / §8 契约化 |

## 1. 背景与问题

engine 模块当前的后端↔引擎信道是自定义 WebSocket JSON 帧协议（下称 v3）：

- 帧协议：`engine/adapter/src/engine/community/kernel/frames.py:34`（`PROTOCOL_VERSION = 3`，
  req/res/event 三帧、512KB 上限、TICK 30s）
- 服务端：`engine/adapter/src/engine/community/api/transport/ws_server.py`
  （connect/hello-ok 握手；方法分发 chat.send / chat.abort / chat.subscribe /
  chat.resume / chat.inject / sessions.reset / interaction.resolve）
- 会话级订阅与断流恢复现有实现：`ws_server.py:830-877`（sessionKey 订阅 +
  live inject 监听绑定）、`openclaw_resume.py`（进程内回放通道：MAX_RUNS=16、
  MAX_EVENTS_PER_RUN=20,000、MAX_BYTES_PER_RUN=16MiB、MAX_BYTES_TOTAL=64MiB、
  SUBSCRIBER_QUEUE_SIZE=256、TERMINAL_TTL=180s、超限标记 CACHE_LIMIT）
- 客户端族：BaaS `_bot_websocket_client.py:85`、backend `grt_chat_service.py:131`、
  BCS `a2a_chat/mod.rs:922`、前端/桌面（直连 `127.0.0.1:20003`）
- 引擎选择 URL：BaaS SPI `_protocols.py:25-47`（`ws_path()` 每引擎钉路径）

**替换动机与收益的诚实表述**（v1 表述已修正）：不是"采用标准后私有协议维护消失"，
而是——**标准化通用通信部分（方法语义、版本协商、Task 生命周期、消息/产物模型、
错误模型、多绑定声明），Avernet 继续 fork 维护 inject、session 订阅、精确回放等
扩展契约**。私有协议税缩小但不归零；换来的是通用部分由标准与官方 SDK 承担演进。

## 2. 目标与非目标

### 目标

1. engine adapter 上 `:20003` 以 A2A v1.0.x（JSON-RPC 2.0 over HTTP，`application/a2a+json`
   + SSE 流式）承载 v3 信道的全部语义：Task 生命周期、消息流、HITL、旁观注入、
   **会话级事件订阅**、断流恢复
2. 引擎插件（openclaw / claude_code / hermes / aicoding / teclaw / deepseek_harness）
   上游传输零改动，SPI 遵循**最小必要调整**原则（§6.3）
3. 行为完全等价（D3 红线）：以 §7-§8 的映射契约 + 对拍测试为验收口径
4. 全程可回退；混合版本部署下的新旧客户端×新旧容器组合有显式矩阵（§10.3）

### 非目标

- 管理 REST 89 条路由不动（与信道协议正交）
- 对外公开门面（公网路由、OAuth2/OIDC、card 对外发布）不在本期
- gRPC / REST 绑定不引入（客户端统一走 JSON-RPC 绑定）
- 各客户端线详设后补各自 spec

## 3. 决策记录

| # | 决策 | 状态 |
|---|------|------|
| D1 | 范围 = adapter 详设 + 迁移蓝图 | 已拍板 |
| D2 | 落点 = Avernet community 传输层，ocb 经 submodule 继承 | 已拍板 |
| D3 | 行为完全等价（extension 补缺口，客户端语义不动） | 已拍板 |
| D4 | 官方 a2a-sdk + 防腐层；内网通路不可得则降级手写 wire 层 | 已拍板 |
| D5 | 基线 = A2A 1.0.x（spec 抓取 2026-10-13） | 已拍板 |
| D6 | 绑定 = 仅 JSON-RPC 2.0 over HTTP + SSE | 已拍板 |
| D7 | 恢复容量承诺 = 对齐现 `openclaw_resume.py` 常量（20k/16MiB/64MiB/180s/CACHE_LIMIT），A2A 面不得更差 | v2 新增（评审项 3） |
| D8 | 新客户端按目标容器能力选协议；跨协议重试必须共享幂等键（messageId ↔ idempotency_key） | v2 新增（评审项 4） |
| D9 | 协议层状态/事件归属 kernel task/session core；A2A 与 v3 都是薄 delivery adapter（arch.rules 第 7 条） | v2 新增（评审项 6） |

## 4. 方案选择

采用 **方案 A：绞杀者 + 兼容门面**（v3 壳与 A2A 壳同为薄 adapter，共享 kernel）。
方案 B（一次性切换）被容器版本 skew 否决：adapter 部署在各 bot 容器内，镜像逐 bot
升级，双协议窗口是部署模型的物理必然。方案 C（v3↔A2A 转换层）不作主线，保留为
收割阶段长尾容器的书面转换方案（§10 收割条件）。

**v2 推进顺序修正**（评审建议采纳）：先抽共享内核并验证 v3 行为不变，再挂 A2A 壳、
单客户端灰度，验收后扩展，最后按版本清单+流量共同收割——与 §10 一致。

## 5. 目标架构（D9 修正）

```
A2A 客户端：BaaS / backend / 前端 / 桌面 / BCS / 未来外部
   │ A2A 1.0.x：JSON-RPC over HTTP（application/a2a+json）+ SSE
   ▼
gateway → agentclawproxy /proxypass/{target}（HTTP+SSE 转发；SSE 缓冲 = G1 首验）
   ▼
engine adapter :20003
┌────────────────────────────────────────────────────────────────┐
│ delivery adapters（薄壳，arch.rules 第 7 条：不持领域策略）       │
│   api/transport/a2a/   A2A 壳：DTO/错误/流事件映射 + card        │
│   api/transport/ws_server + kernel/frames  v3 壳（阶段 4 删）    │
├────────────────────────────────────────────────────────────────┤
│ Service API（kernel protocol）：Facade 接口                      │
│   ├ Task 生命周期（start/cancel/get/since_seq 事件流）           │
│   ├ Session 事件订阅（subscribe/unsubscribe + 旁观 fanout）      │
│   └ inject / resolve_interaction / reset / supports(501)        │
├────────────────────────────────────────────────────────────────┤
│ kernel task/session core ＋ shared event store                  │
│   容量与恢复规则 = D7（对齐 openclaw_resume 常量与 CACHE_LIMIT）  │
│   幂等 registry（contextId+messageId → run，跨协议共享）         │
├────────────────────────────────────────────────────────────────┤
│ Engine Plugin API → 插件实现（SPI 最小调整，§6.3）               │
│   openclaw | teclaw (community)；claude_code | aicoding |        │
│   hermes | deepseek_harness (corp，经 ocb submodule)            │
└───────────────┬──────────────┬──────────────┬───────────────────┘
        ws://127.0.0.1:18789  ws://127.0.0.1:18900  JSON-RPC / stdio
        （各引擎上游传输全部不动）
```

分层裁决依据：`docs/arch/arch.rules.md` 第 7 条（core 传输无关；delivery adapter 只做
翻译）+ 第 3 条（Service API 以显式 Protocol 定义，独立于 delivery）+ 第 25 条
（契约必须有 conformance 测试）。v1 把 Task 视图+事件日志放在 `api/transport/a2a/
task_store.py`，与本图冲突，v2 已废除——A2A 层只有 DTO 与流事件映射，真实任务状态、
回放日志、恢复规则全部在 kernel。

## 6. 组件详设

### 6.1 协议绑定与端点（A2A 1.0）

JSON-RPC 方法名与绑定路径以附录 A 为准（`SendMessage` / `SendStreamingMessage` /
`GetTask` / `ListTasks` / `CancelTask` / `SubscribeToTask` + 本仓 extension）。
端点布局（`:20003`）：

| 端点 | 说明 |
|------|------|
| `GET /.well-known/agent-card.json` | Agent Card（§6.5） |
| `POST /a2a`（单 JSON-RPC 端点，`application/a2a+json`） | `SendMessage` 等单发方法 |
| `POST /a2a`（`SendStreamingMessage`） | 响应体 `text/event-stream` |
| `POST /a2a`（extension：`SubscribeToContext` 等） | 会话级事件流（SSE，§8.2） |
| `/ws`、`/api/{engine}/ws` | v3 壳（阶段 4 删除） |

A2A 壳结构（全部为薄映射，无状态所有权）：
`api/transport/a2a/{router.py, card.py, sse.py, protocol.py}`；`protocol.py` 是
`a2a_sdk` 的唯一 import 边界（D4 防腐层，降级路径下手写 wire 层也在此文件）。

### 6.2 kernel task/session core + shared event store（新）

从 `ws_server.py` / `openclaw_resume.py` 中把以下所有权抽进 kernel（这一步本身
不改任何行为，是阶段 1 的第一子步，完成后先做 v3 对拍回归）：

- run 生命周期与状态机（对应现 `OpenClawResumeRun.state`：starting/final/error/aborted）
- 事件缓冲与恢复容量治理（D7 常量；CACHE_LIMIT 触达即停止缓存并显式置因，不清空
  既有语义下的"静默部分丢失"——对客户端暴露为 §8.1 的显式错误）
- 会话订阅注册表（对应 `_session_subscribers` / `_conn_sessions`）与 inject 监听绑定
  （`_ensure_openclaw_inject_listener`）
- 幂等 registry：`(contextId, messageId)` → 已存在 run 的映射（供 SendMessage
  去重与跨协议重试共享；v3 侧等价键为 `ChatSendParams.idempotency_key`，BCS
  `build_chat_send_frame` 已在传此字段）

Service API 接口（Protocol 定义，示意，签名实现期定稿但契约语义以 §8 为准）：

```python
class EngineChatService(Protocol):
    async def start_task(self, engine, context, message, *, idempotency_key) -> TaskHandle
    async def cancel_task(self, engine, task_id) -> CancelResult
    async def inject_context(self, engine, context, message) -> None
    async def resolve_interaction(self, engine, interaction_id, resolution) -> None
    async def subscribe_task(self, task_id, since_seq) -> TaskEventStream      # Task 级
    async def subscribe_context(self, context) -> ContextEventStream          # Session 级（§8.2）
    def supports(self, capability) -> bool
```

### 6.3 Engine Plugin SPI：最小必要调整原则（评审项 6 后半）

**不预设"全部插件重排为 on_task"**。默认路径：现有 `chat.send / chat.inject /
interaction` 分发接口原样保留，kernel 以适配方式调用（契约不变、消费方不变）。
仅当某个语义缺口在现有接口上**实证**无法表达时（候选：session 订阅入口、
since_seq 游标），才做该处的契约调整，并按 arch.rules 第 16 条附传播分析。
判断动作放在阶段 1 内核抽取时，以"A2A 壳需要但现有 SPI 无法承载"清单为准；
全引擎零调整是理想结果，不作硬承诺。

### 6.4 客户端侧：兼容矩阵与协议选择（评审项 4/5）

**前端措辞修正（v2）**：不使用"EventSource 化"（浏览器原生 EventSource 仅支持 GET
、无自定义鉴权头，无法承载 `application/a2a+json` 的 POST JSON-RPC）。前端 /
桌面采用**支持 POST body、自定义 headers、ReadableStream 解析 SSE 的 fetch 流式
客户端**（或经真实浏览器链路验证过的 A2A SDK 浏览器版）；真实浏览器链路（跨域、
鉴权头、客户端取消 AbortController、断流恢复）列入 G1 验证面。

**BaaS 迁移与新旧容器并存（D8）**：见 §10.3 兼容矩阵。BaaS 保留 v3 回退路径到
收割完成；按目标容器能力选协议；能力声明与失效规则：
- 能力发现挂在 BaaS 既有的连接信息解析（`ws-info`/`http-info`/`_resolve_ws_connection`
  同族）：目标容器 card 可拉取且 `supportedInterfaces` 含 HTTP+JSON 1.0 → 走 A2A；
  否则 v3
- 能力缓存失效 = 容器 rebuild/publish 事件驱动（BaaS 已有 bot 发布事件面）+ TTL 兜底
- 回滚 = 组件级配置开关（DI 层 feature flag），不做业务分支
- **跨协议超时换发禁止**：超时重试（无论同协议还是换协议）必须携带同一幂等键，
  由 kernel 幂等 registry 去重（§8.5）；否则客户端只允许 GetTask 轮询确认后决策

backend / BCS / 桌面的具体改线各自 spec，本文只锁：协议选择规则与幂等键共享
为全局约束。

### 6.5 Agent Card（1.0 结构）

按 1.0 `supportedInterfaces` 定义（弃用旧式顶层 url 表述）：

```json
{
  "name": "<bot name>",
  "description": "<bot 描述>",
  "version": "<adapter 版本>",
  "supportedInterfaces": [
    { "url": "{deploy-base}/proxypass/{target}/a2a",
      "protocolBinding": "HTTP+JSON",
      "protocolVersion": "1.0" }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extensions": [ /* §8 extensions 的 AgentExtension 声明 */ ]
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain", "application/json"]
}
```

`url` 的 base 由部署侧注入（config 差异，不进业务码道）；skills 来源见 O3。
extension 命名遵循官方机制（`capabilities.extensions` 数组 + 请求侧可带
`A2A-Extensions` 头协商），本仓扩展的最终 URI 标识在 G2 与 SDK 版本一起锁定。

### 6.6 鉴权与版本协商

传输级鉴权语义不变（agentclawproxy x-proxypass-token JWT；card securitySchemes
以 Bearer 声明；OAuth2/OIDC 属对外门面 O4）。版本协商：请求头 `A2A-Version: 1.0`
+ card `supportedInterfaces[].protocolVersion`；不支持版本返回标准版本协商错误。
`connect` 帧的 `x-moltis-mcp-token` 历史参数退役，改 Authorization 头。

### 6.7 错误映射

- facade `supports()` 保留 501 语义；A2A 壳映射为 spec 错误（`UnsupportedOperationError`
  等，逐码对表在附录 A 定稿）
- 恢复能力触界（§8.1 CACHE_LIMIT、游标过期）→ 显式 JSON-RPC 错误，止静默截断
- Task 级失败进 `TASK_STATE_FAILED`；SSE 中途异常以流内事件 + 断流恢复路径表达

### 6.8 观测

`a2a_task_duration_seconds`、`a2a_first_event_latency_seconds`、
`a2a_sse_reconnect_total`、`a2a_extension_inject_total`、`a2a_capability_fallback_total`
（BaaS 回退 v3 比率——收割门槂数据源之一）。与 v3 指标并存至收割。

## 7. 语义映射契约表

对拍用例编号 TC-*：同用例走 v3 壳与 A2A 壳断言等价输出，语义裁决以 §8 契约为准。

| v3 | A2A 1.0 落法 | 契约要点 | 对拍 |
|----|--------------|----------|------|
| connect/hello-ok + min/maxProtocol | 无握手 + `A2A-Version` 头 | 版本不支持 → 标准协商错误 | TC-CONN |
| chat.send | `SendMessage`（contextId 续会话） | 幂等：messageId + §8.5 | TC-SEND |
| chat 流式 EventFrame | `SendStreamingMessage`（SSE） | StreamResponse 单选其一；delta 语义 §8.4 | TC-DELTA |
| chat.abort | `CancelTask` | 幂等；终态后取消 → spec 错误码 | TC-CANCEL |
| **chat.resume（run 恢复）** | extension 回放契约（§8.1） | 标准 `SubscribeToTask` 定位活任务流重挂（首事件为当前 Task 快照），**终态回放由 extension 承担** | TC-RESUME |
| **chat.subscribe（会话订阅）** | extension `SubscribeToContext`（§8.2） | 与 task 恢复**分契约**；旁观可见性 | TC-SESSUB |
| chat.inject | extension inject（§8.3） | 会话级 fanout，非 task 级 | TC-INJECT |
| interaction.requested/resolved | Task `input-required` + 同 task 续消息 | §8.6 | TC-HITL |
| sessions.reset | 行为契约 §8.7 | 单一语义，对拍固化 | TC-RESET |
| 每引擎 `/api/{engine}/ws` | 删除（单端点）+ card | — | TC-CONN |
| 501 能力探测 | spec 错误码映射 | `supports()` 不变 | TC-501 |
| 管理 REST 89 路由 | 不动 | — | — |

## 8. 语义契约明细（可验收口径）

### 8.1 Task 恢复契约（评审项 3，容量以 D7 为准）

- **游标**：每事件携带 `resumeSeq`（run 级单调递增，`publish()` 在单一互斥域内
  分配并 fanout——锁内完成，保证不缺号）；客户端去重以 `≤ 已见最大 seq` 丢弃
- **容量**：per-run 20,000 事件 / 16MiB，全局 64MiB / 16 个活跃 run；触达任一上限
  即停止缓存并置 `reason=CACHE_LIMIT`（不清空已缓冲事件以静默截断——对客户端显式
  返回 CACHE_LIMIT 错误，客户端转 `GetTask` 快照判断）
- **终态保留**：终态 run 保留 180s（attach 回放 + 终态标记；过后 evicted，恢复承诺
  终止，客户端语义 = 重新查询/重发，幂等键保护）
- **回放→live 切换**：attach 在锁内完成"回放快照 + 订阅注册"两端（现
  `openclaw_resume.py` 的 `_lock` 语义），不丢事件
- **SSE 断开不影响执行**：执行由内核 run task 持有；订阅者队列满被踢仅影响投递
- **adapter 重启边界**：进程内注册表全失（显式承诺：不跨重启恢复）；客户端按
  GetTask/重发 + 幂等键处理
- 标准能力衔接：活任务用 `SubscribeToTask`（含 GetTask→Subscribe 防丢失的首事件
  快照）；上述 D7 承诺与终态/超容量回放为 extension 责任

### 8.2 Session/context 事件契约（评审项 2，与 Task 恢复分契约）

- extension 方法 `SubscribeToContext` / `UnsubscribeFromContext`（SSE 流）；参数
  `contextId`
- 服务端推送三类会话级事件：他人发起的 run 的 task 状态事件摘要、
  旁观注入的消息事件（`inject-*` run 不进 task 状态机，见 `openclaw_resume.py:104-106`
  的既有裁决）、必要的会话通知
- **行为等价的判定用例**：前端打开已存在会话且自身无活跃 task，BCS 后台
  `chat.inject` —— 会话订阅者必须收到该事件（Spike F3；对拍 TC-SESSUB）
- live inject 监听绑定语义随 `_ensure_openclaw_inject_listener` 移入 kernel，
  openclaw 引擎之外返回订阅成功但无 inject 源（与现行为一致）

### 8.3 inject 契约

extension 方法（A2A-Extensions 协商）；参数 = message + `contextId` + 参与方/路由
上下文（承载现 `ChatSendParams` 的 `GroupContext/mentions/delivery_type` 字段语义，
最小集 = O2）；执行为非 task（不产生 A2A Task、不占 run 状态机），fanout 至
§8.2 订阅者。幂等键同样适用（防注入重放）。

### 8.4 delta / 产物语义（评审项 7）

- **会话文本流**：走 `StreamResponse` 的 `message` 事件；每个 delta 作为追加 part
  携带 `metadata.sequence`（run 级递增）；客户端按顺序拼接、按 `sequence` 去重。
  追加（append-only）是唯一写入语义；重写场景 = 新 message 事件（non-append 标记
  于 metadata，对齐现 v3 对 final 覆盖快照的行为）
- **产物（文件/最终交付物）**：走 `artifactUpdate`（标准自带
  append/lastChunk/artifactId 语义）；本仓哪些输出归 artifact = O 决策（推荐：clip
  文件、结构化最终报告；流式文本不归 artifact）

### 8.5 幂等契约（D8）

- 幂等键：A2A 侧 = `messageId`（spec §3.3.1：Send MAY be idempotent，代理方即我们；
  Get 自然幂等；Cancel 天然幂等）；v3 侧 = `ChatSendParams.idempotency_key`
- kernel registry 以 `(contextId, 幂等键)` 判重：已存在未终态 run → 返回该 task；
  终态保留窗口内 → 返回终态；过后 → 放行重执行（与恢复窗口对齐）
- **跨协议重试**（超时换协议）必须复用同一幂等键；无键的请求禁止换协议重发，
  只允许 GetTask/ListTasks 查证

### 8.6 HITL 契约（评审项 7）

- 触发：Task 转 `TASK_STATE_INPUT_REQUIRED`，statusUpdate 事件携带 interaction
  id（extension metadata：`interactionId`，对齐现 interaction.requested 参数）
- 继续/决策：客户端 `SendMessage`，**同 `taskId` + `contextId`**（spec 原文：续交互
  发同 taskId/contextId 消息；不匹配 MUST 拒绝）；决策内容 = parts + metadata
  （批准/拒绝/补充输入，载荷对齐现 interaction.resolve 参数）
- 冲突裁决：重复提交（task 已离开 input-required）→ spec 错误码（等价现 v3 交互
  重复提交拒绝）；审批期间 task 被 cancel → 取消裁决胜出，迟到的 resolve 返回
  任务已结束错误；超时未决 → task 停留 input-required 直到 run deadline
- 上述每条对应 TC-HITL 子用例（含"审批中取消"、"重复提交"两个负例）

### 8.7 reset 契约（评审项 7，单一语义）

`sessions.reset` → extension 方法 `ResetContext(contextId)`：**保留 contextId，
清空引擎会话历史**；运行中 task 不受影响、活跃订阅不撤销、事件缓冲按 run 独立
保留。以 TC-RESET 对拍现 v3 行为固化；若对拍暴露 v3 存在额外副作用，以 v3 行为
回填本节（该回填是阶段 1 的显式产出，不得默认省略）。

## 9. 测试策略

1. **对拍（双壳等价回归）**：TC-* 全集同用例打 v3 壳与 A2A 壳，断言语义等价；
   收割前是主要安全网
2. **conformance**：a2a-sdk 官方 client 作为对端打真 adapter（openclaw +
   claude_code 实引引擎），全标准方法 + extension 矩阵（D4 任一分支都保留）
3. **flow coverage**：singlebox 框架新增 a2a profile；changed-line ≥80% 走本地
   `ci_test.sh` 同口径
4. **Spike 失败用例 F1-F4**（§10.1）
5. **BCS（阶段 3）**：Rust mock A2A server contract test + 真链路联调
6. arch 结构测试：kernel 无 transport import、A2A 壳无状态所有权（第 7 条 CI 检查）

## 10. 迁移蓝图

前置硬约束：adapter 在各 bot 容器内，镜像逐 bot 升级——双协议窗口下限 = default
bot / 服务 bot 的 publish-restart 节奏。推进顺序 = 先内核后壳（§4 v2 修正）。

### 10.1 阶段 0：Spike（验收门禁）

| 门 | 内容 | 通过标准 |
|----|------|----------|
| G1 | SSE 过 agentclawproxy + gateway 全链路；**真实浏览器 fetch 流式链路**（跨域/鉴权头/AbortController 取消/断流恢复） | 无代理缓冲；断链行为明确；重连+since_seq 补齐可用 |
| G2 | a2a-sdk 依赖通路 + **版本锁定**（SDK 与 spec 精确版本回填附录 A） | 可得且锁定（D4 降级则书面决策） |
| G3 | delta 聚合接真引擎（claude_code 流式） | 打字机 UX 无可感降级 |
| G4 | inject + **session 订阅** extension 原型 | BCS 群聊旁观用例跑通，含 F3 场景 |
| G5 | 性能基准 | 建连开销 P50 相对 WS 常驻增量 ≤50ms；流吞吐 ≥WS 90%；内存持平 |

**失败用例（必须显式测）**：
- F1：超出 D7 容量上限后断线恢复（CACHE_LIMIT 显式错误，非静默丢失）
- F2：task 终态后重连（180s 窗口内：回放；窗口外：显式不承诺，GetTask 语义）
- F3：无前台活跃 task 时，后台 inject 经 session 订阅可见
- F4：请求已执行但响应丢失，同幂等键重试（跨协议）不产生第二次执行

任何门不过 → 设计回炉（G2 过不了切 D4 降级分支）。

### 10.2 阶段 1-4（顺序：内核先行）

- **阶段 1a：内核抽取**（行为零变）：chat 分发/恢复/会话订阅/事件缓冲移入 kernel；
  退出 = v3 对拍回归全绿（此时尚无 A2A 壳，对拍对象 = 抽取前后行为）
- **阶段 1b：A2A 壳落地 + BaaS 灰度**：退出 = BaaS 按目标能力分流跑通（A2A 优先 +
  v3 回退，§10.3 矩阵）；双壳对拍 TC 全绿；`a2a_capability_fallback_total` 披露
- **阶段 2**：backend + 前端/桌面 fetch 流式客户端迁移
- **阶段 3**：BCS（Rust A2A client + inject/session 扩展 + 在线判定改健康轮询）；
  桌面滞后 → C 方案转换层兜底
- **阶段 4 收割（双条件）**：① v3 通道流量占比 < 阈值（建议 1%，观测裁决）；②
  **存量 v3-only 容器版本清单清零，或对剩余每个目标存在书面转换方案**（长尾休眠
  bot 无流量不代表可收割——低频 bot 也依赖 v3）。删 `frames.py` + v3 壳 +
  v3 客户端路径；删除动作单独 PR；收割前保留 ≥1 个可回退 release

### 10.3 客户端 × 容器兼容矩阵（评审项 4）

| 客户端＼容器 | 旧（仅 v3） | 双壳 | 收割态（A2A-only） |
|---|---|---|---|
| 旧客户端（v3） | ✓ | ✓（v3 壳） | ✗ → 阶段 3 退出 = 旧客户端全部迁完 |
| 新客户端（A2A 优先 + v3 回退） | ✓（**v3 回退路径**） | ✓（A2A） | ✓ |
| 回退触发 | 能力发现：card 不可拉取 / `supportedInterfaces` 不含 HTTP+JSON 1.0 | — | — |

规则：能力缓存随容器 rebuild/publish 事件失效 + TTL 兜底；组件级配置开关可整体
切回 v3（显式回滚）；跨协议重发须幂等键（§8.5，F4 验证）。

## 11. 风险登记

| # | 风险 | 等级 | 缓解 |
|---|------|------|------|
| R1 | spec 演进（一年内 0.2→0.3→1.0 连续 breaking） | 高 | D5 + `protocol.py` 防腐层（O5 跟版策略） |
| R2 | SSE 过代理链路缓冲/超时 | 高 | G1 首验（含真浏览器） |
| R3 | 性能门限不达标 | 中 | G5 门禁；预案回到"是否全量换轨"再评审 |
| R4 | extension 固化，二期纯化失败 | 中 | inject/session 契约按最小面定义（O6） |
| R5 | 多团队排期协同 | 高 | 依赖顺序锁死；各线独立 spec；收割由版本清单+流量双驱动 |
| R6 | 容器版本 skew 拉长双壳期 | 高 | §10.3 矩阵 + 回退路径；收割双条件 |
| R7 | a2a-sdk 通路缺失 | 中 | G2 早验 + D4 降级 |
| R8 | 命名撞车（BCS 内部 `a2a_chat`） | 低 | 模块语境区分；文档统一称"标准 A2A / 内部 A2A 直聊" |
| R9 | 内核抽取期间引入行为漂移 | 中 | 阶段 1a 独立验证（抽取前后对拍）先于任何 A2A 代码合入 |

## 12. 开放决策（待拍板，均附推荐值）

| # | 决策 | 推荐 | 何时定 |
|---|------|------|--------|
| O1 | 从 1.0 绑定选型看，是否补一份 REST 绑定（`/message:send` 路径族）作为内部可选项 | 不补（D6 收敛单绑定）；REST 路径仅作附录参考 | 阶段 0 |
| O2 | inject 上下文最小字段集 | 按 `ChatSendParams` 参与方字段最小映射 | G4 |
| O3 | card skills 来源 | bot manifest 派生，回退单 conversation skill | 阶段 1b |
| O4 | 对外门面（公网+OAuth2/OIDC）启动时机 | 阶段 1b 后评估 | 阶段 1b 后 |
| O5 | a2a-sdk 跟版策略 | minor 自动跟、major 评审制 | G2 定版 |
| O6 | 去 extension 的二期纯化评估 | 阶段 4 前一次语义评估 | 阶段 4 前 |
| O7 | 哪些输出走 artifactUpdate vs message 流 | 文件与结构化最终报告走 artifact | 阶段 1b |
| O8 | extension URI 命名空间（内部 URI 方案） | `https://avernet.example/a2a/ext/<name>/v1` 风格，G2 与 SDK 锁定 | G2 |

## 附录 A：协议基线（A2A 1.0，spec 抓取 2026-10-13）

> 依据：https://a2a-protocol.org/latest/specification/ 本节字段/表均来自当日
> 抓取正文（关键词窗口提取）。SDK 精确版本由 G2 锁定后回填本附录（锁定前进不了阶段 1b）。

### A.1 方法映射（spec §5.3）

| 功能 | JSON-RPC 方法 | REST 端点（参考，不采用） |
|---|---|---|
| 发消息 | `SendMessage` | `POST /message:send` |
| 流式发消息 | `SendStreamingMessage`（SSE） | `POST /message:stream` |
| 查任务 | `GetTask`（幂等） | `GET /tasks/{id}` |
| 列任务 | `ListTasks` | `GET /tasks` |
| 取消 | `CancelTask`（幂等） | `POST /tasks/{id}:cancel` |
| 重挂任务流 | `SubscribeToTask`（SSE；首事件 = 当前 Task 快照） | `POST /tasks/{id}:subscribe` |
| 推送配置 | `CreateTaskPushNotificationConfig` 等 | （本期不用，pushNotifications=false） |

### A.2 关键对象与语义

- 请求：`Content-Type: application/a2a+json`；`SendMessageRequest` 字段：
  `tenant`（可选，须与所选 AgentInterface 的 tenant 匹配——多租户钩子，内部用
  proxypass target 承载、不启用）、`message`（必填）、`configuration`、`metadata`
- `Message`：`messageId` 必填（创建方生成）、`contextId` 可选、role、parts
- 续交互：同 `taskId` + `contextId` 的后续消息；两者不一致 MUST 拒绝
- 幂等（spec §3.3.1）：Get 天然；Send 经 `messageId`（MAY）；Cancel 幂等
- 流响应：`StreamResponse` **恰含其一**：`task` / `message` / `statusUpdate` /
  `artifactUpdate`
- Task 状态族：`TASK_STATE_*`（含 `input-required`、`canceled/rejected/failed/completed`）
- TaskStatusUpdateEvent / TaskArtifactUpdateEvent 均携带 `taskId` + `contextId`

### A.3 本仓 extension 清单（与标准方法分表）

| extension | 用途 | 替代的 v3 语义 |
|---|---|---|
| inject | 旁观注入（非 task） | chat.inject |
| SubscribeToContext / UnsubscribeFromContext | 会话级事件订阅 | chat.subscribe/unsubscribe + live inject 监听 |
| resume/replay（终态与超容量回放） | run 回放 | chat.resume（活任务重挂走标准 SubscribeToTask） |
| ResetContext | 会话历史重置 | sessions.reset |
| （SendMessage metadata 承载 interactionId、幂等键如需） | HITL 关联、去重 | interaction.resolve 的承载不是独立方法，而是同 task 续消息 |

样例 wire schema（请求/响应/SSE 事件/card 各一份可执行 JSON）在 G2 锁定 SDK
后由其官方样例生成回填，作为 conformance 测试的 golden 文件——不手写拼 JSON。

## 13. 参考与证据

- A2A spec v1.0.x：https://a2a-protocol.org/latest/specification/ （附录 A 来源；
  本文档对 spec 的引用为关键词窗口提取，非全文通读）
- 代码证据：`ws_server.py:830-877`（session 订阅）、`openclaw_resume.py:14-19/69-152`
  （恢复容量与 CACHE_LIMIT）、`a2a_chat/mod.rs:849-927`（BCS 帧构造，含
  idempotency_key 字段）、`docs/arch/arch.rules.md` 第 3/7/16/25 条
- 评审记录：2026-10-13 外部评审（5 P1 + 2 P2），吸收映射见文首表
