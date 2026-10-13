# Engine 传输层协议替换：v3 WebSocket 帧 → A2A（架构设计）

日期：2026-10-13
状态：设计方向已与负责人对齐（决策记录见 §3）；文档待评审
范围：engine adapter 传输层协议替换详设 + 全客户端迁移蓝图。BaaS / backend / BCS /
前端 / 桌面各客户端线的详细改造各自后补 spec，本文只画依赖与阶段边界。
分支：`feat/engine-a2a-protocol-replacement`（基于 origin/dev a887e4dd5）

## 1. 背景与问题

engine 模块当前的后端↔引擎信道是自定义 WebSocket JSON 帧协议（下称 v3）：

- 帧协议定义：`engine/adapter/src/engine/community/kernel/frames.py:34`（`PROTOCOL_VERSION = 3`，
  req/res/event 三帧、512KB 上限、TICK 30s、握手超时 10s），协议注释明确
  "OpenClaw-native but adopted as the standard WS envelope for the whole engine module"
- 服务端：`engine/adapter/src/engine/community/api/transport/ws_server.py`
  （`connect`/`hello-ok` 握手；方法分发 `chat.send` / `chat.abort` / `chat.subscribe` /
  `chat.resume` / `chat.inject` / `sessions.reset` / `interaction.resolve`）
- 端点：`engine/adapter/src/engine/community/api/app.py`（`/ws` 引擎无关 + `/api/{engine}/ws` 钉路径，不匹配 close 4001）
- 客户端族：BaaS `apps/baas/src/secbaas/community/core/service/bot_run/_bot_websocket_client.py:85`
  （同样 PROTOCOL_VERSION=3）、backend `apps/backend/src/agentclaw/community/core/grt_chat/services/grt_chat_service.py:131`
  （经 `/proxypass/{target}/api/openclaw/ws`）、BCS `apps/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs:922`
  （`build_chat_send_frame` 构造 `chat.send` 帧）、前端/桌面（`connectionStore` 直连
  `127.0.0.1:20003`）
- 引擎选择 URL：BaaS SPI `apps/baas/src/secbaas/community/spi/bot/engine_adapter/_protocols.py:25-47`
  （`ws_path()`：openclaw→`/api/openclaw/ws`、teclaw→`/api/teclaw/ws`、aicoding→`/api/ws`、
  hermes→`/api/hermes/ws`、claude_code→`/api/claude_code/ws`）

**替换动机**（不是互操作加面，是统一协议栈）：平台四处客户端各自维护一份 v3 实现
与理解，私有协议税持续增长；A2A 已发布 v1.0（Linux Foundation 治理、官方 Python SDK
等 5 个 SDK、150+ 组织），把内部信道换成标准协议后，协议演进（版本协商、断流恢复、
鉴权声明）由标准与 SDK 承担，内外一套协议心智，对外互操作门面成为顺带产物。

## 2. 目标与非目标

### 目标

1. engine adapter 上 `:20003` 以 A2A v1.0.x（JSON-RPC 2.0 over HTTP + SSE 流式）承载
   现有 v3 信道全部**对话流语义**（Task 生命周期、消息流、HITL、旁观注入、断流恢复）
2. 引擎插件（openclaw / claude_code / hermes / aicoding / teclaw / deepseek_harness）
   的**上游传输零改动**，只做 SPI 签名适配
3. 迁移后客户端行为**完全等价**（D3 红线）：BCS 群聊旁观（chat.inject）、断流恢复、
   前端打字机粒度、在线态判定语义不变或可显式映射
4. 全程可回退：v3 壳在收割阶段（阶段 4）之前保持可用

### 非目标

- **管理面 89 条 HTTP REST 路由不动**（models / files / approvals / MCP / skills /
  sessions 查询等，见 `apps/backend/docs/openapi-v1/engine-surface.md`；与信道协议正交）
- 对外公开门面（公网网关路由、OAuth2/OIDC security schemes、card 对外发布）不在本期，
  只做能力声明
- BaaS / backend / BCS / 前端 / 桌面的改线详设（各自后补 spec）
- 不引入 gRPC / REST 绑定（所有客户端 HTTP 栈现成，JSON-RPC + SSE 单一绑定收敛）

## 3. 决策记录

| # | 决策 | 结论 | 状态 |
|---|------|------|------|
| D1 | 文档范围 | 聚焦 adapter 详设 + 迁移蓝图 | 已拍板 |
| D2 | A2A 核心落点 | Avernet community 传输层，ocb 经 submodule gitlink 继承 | 已拍板 |
| D3 | 行为等价红线 | **完全等价**：缺口用 A2A extension 补齐，客户端语义不动 | 已拍板 |
| D4 | 协议实现库 | 官方 a2a-sdk + 防腐层；spike 验证内网通路，不可得降级"手写 wire 层钉 v1.0.x + SDK 做 CI 一致性对端" | 已拍板 |
| D5 | 版本基线 | A2A **1.0.x**（spec 主线 latest = 1.0.0，途经 0.1.0/0.2.6/0.3.0） | 已拍板 |
| D6 | 传输绑定 | JSON-RPC 2.0 over HTTP + SSE（唯一绑定） | 已拍板 |

## 4. 方案选择

| 方案 | 描述 | 结论 |
|------|------|------|
| **A. 绞杀者 + 兼容门面** | adapter 内新建 A2A 传输模块与 v3 并列；把对话分发/流/HITL/inject 从 transport 抽成统一内核 facade；A2A 与 v3 是内核的两个壳；客户端分批迁移后收割 v3 壳 | **采用** |
| B. 原地重写一次切换 | 直接拆 v3 换 A2A，全客户端同版本切换 | 否决：engine adapter 部署在各 bot 容器内，镜像逐 bot 升级，老容器 v3 / 新容器 A2A 无法同步切换——**双协议窗口是部署模型的物理必然**，方案 B 不存在可行路径 |
| C. adapter 只留 A2A + 转换层 | 顽固客户端在 backend/proxy 侧做 v3↔A2A 帧翻译 | 不作主线；保留为桌面等长尾客户端迁滞时的补充手段（见 §10 阶段 3 备注） |

方案 A 的代价：过渡期双壳并存（一个 release 周期量级）、一次内核接口抽取投入。
收益：每一步可独立发布与验证、随时回退、A2A 壳与 v3 壳共享同一内核保证语义不双轨漂移。

## 5. 目标架构

```
A2A 客户端：BaaS / backend / 前端 / 桌面 / BCS（Rust）/ 未来外部生态
   │  A2A v1.0.x：JSON-RPC 2.0 over HTTP ＋ SSE 流式（无状态，A2A-Version 协商）
   ▼
gateway (bots-messages 路由) → agentclawproxy /proxypass/{target}（HTTP＋SSE 转发）
   ▼
engine adapter :20003（FastAPI；community 核心 + corp 引擎，PEP 420 并存）
┌─────────────────────────────────────────────────────────────┐
│ transport/a2a/        新 A2A server：Agent Card、             │
│   message/send|stream、tasks/get|cancel、extensions(inject)  │
│ transport/ws_server   v3 兼容壳（阶段 4 删除，frames.py 同收）│
├─────────────────────────────────────────────────────────────┤
│ kernel/engine_facade  新抽取的统一内核：Task 生命周期、        │
│   细粒度事件流(seq)、HITL、inject、session、能力探测(501 语义)│
├─────────────────────────────────────────────────────────────┤
│ engine plugin SPI（重排为 Task 语义，见 §6.3）                │
│   openclaw | teclaw (community)                              │
│   claude_code | aicoding | hermes | deepseek_harness (corp)  │
└──────────────┬──────────────┬──────────────┬────────────────┘
        ws://127.0.0.1:18789  ws://127.0.0.1:18900  JSON-RPC/stdio
        （各引擎上游传输全部不动）
```

模块布局（community 侧新增）：

```
engine/adapter/src/engine/community/
├── api/transport/a2a/
│   ├── router.py        # JSON-RPC 分发 + extension 方法
│   ├── card.py          # Agent Card 渲染
│   ├── task_store.py    # A2A Task 视图（映射 engine run）+ 事件日志(since_seq)
│   ├── sse.py           # SSE 编码、ping 保活、断链行为
│   └── protocol.py      # 防腐层：a2a-sdk 类型 ↔ 内部 DTO 的唯一边界
├── kernel/engine_facade.py   # 统一内核接口（v3 壳与 A2A 壳共用）
├── kernel/frames.py          # v3 帧（阶段 4 删除）
└── api/transport/ws_server.py  # v3 壳（阶段 4 删除）
```

端点布局（`:20003` 上新增，与既有管理 REST `/api/**` 无路径冲突）：

| 端点 | 说明 |
|------|------|
| `GET /.well-known/agent-card.json` | Agent Card（D5 声明 1.0.x） |
| `POST /a2a`（JSON-RPC 单端点，最终路径由 card.url 声明，阶段 0 定稿） | message/send、tasks/get、tasks/cancel、extension 方法 |
| `POST /a2a`（message/stream） | 响应体为 SSE 事件流 |
| `/ws`、`/api/{engine}/ws` | v3 壳，过渡期保留 |

## 6. 组件详设

### 6.1 A2A Server（transport/a2a/）

职责（只做壳，不含业务）：

- JSON-RPC 分发到 facade；A2A `Task` 状态机 = engine run 状态映射
  （pending→`submitted`、running→`working`、等审批→`input-required`、
  done→`completed`、error→`failed`、aborted→`canceled`）
- `message/stream`：订阅 facade 细粒度事件流，聚合成 A2A SSE 事件
  （`message` 增量 parts 带 `metadata.sequence`、`status-update`、`artifact-update`、
  `ping` 保活）；`contextId` ↔ 既有 engine session id
- 断流恢复：客户端重连后按 `since_seq` 补齐 task 事件日志
  （`task_store.py` 环形日志，默认 256 事件/task，容量可配）
- `protocol.py` 防腐层是 `a2a_sdk` 的唯一 import 边界——SDK 升级只动这一个文件
  （D4；降级路径下手写 wire 层也放在同一文件，业务层不感知）

### 6.2 Engine Facade（kernel/engine_facade.py）

从 `ws_server.py` 的方法分发中抽出统一内核接口（示意，签名实现期定稿）：

```python
class EngineTransportFacade(Protocol):
    async def start_task(self, engine, session_ref, message, mode) -> TaskHandle
    async def cancel_task(self, engine, task_id) -> CancelResult
    async def inject_context(self, engine, session_ref, context) -> None   # ← chat.inject 等价
    async def resolve_interaction(self, engine, interaction_id, resolution) -> None
    async def reset_session(self, engine, session_ref) -> None
    def supports(self, capability: EngineCapability) -> bool               # ← 501 语义保留
    def task_events(self, task_id, since_seq: int = 0) -> AsyncIterator[EngineEvent]
```

关键设计：**细粒度事件流在 kernel 层保留**。`EngineEvent(seq, kind, payload)`
携带 delta / final / status / interaction / tool_progress 等原语粒度——这是行为完全等价
（D3）的实现机制：打字机粒度、inject、断流恢复的语义都锚在内核，两个壳各自表达，
不产生第二套业务。

### 6.3 Engine Plugin SPI 重排

插件方法从 v3 帧语义重排为 Task 语义：`on_task / on_cancel / on_inject /
on_interaction`（参数取自 `ChatSendParams` 家族的既有字段，`群聊旁观`的
`GroupContext/mentions/delivery_type` 等字段进入 inject 上下文结构）。每个引擎实现
一次，A2A 壳与 v3 壳共用。

引擎适配矩阵（上游传输全部不动）：

| 引擎 | 所在 | 上游传输 | SPI 改造点 |
|------|------|----------|-----------|
| openclaw | community | `ws://127.0.0.1:18789`（openclaw gateway） | chat.send→on_task 等机械映射 |
| teclaw | community | 同 openclaw 族 | 同上 |
| claude_code | corp（ocb） | `ws://127.0.0.1:18900`（claude_code relay） | on_task + on_interaction（exec.approval） |
| aicoding | corp（ocb） | teamclaw-aicoding-relay | on_task |
| hermes | corp（ocb） | JSON-RPC 2.0 over WS | on_task |
| deepseek_harness | corp（ocb） | 子进程 stdio JSON-RPC | on_task |

corp 侧接线：`engine/adapter/src/engine/corp/transport/{claude_code,aicoding}/server/
websocket_server.py` 的 v3 方法处理改调 facade；过渡期 corp WS server 保留为壳，
阶段 4 与 community ws_server 一同收割。ocb 通过 submodule gitlink 升级继承
（当前钉 ca49b34aa，随阶段推进 bump）。

### 6.4 BaaS 客户端侧 SPI（改线预告，详设后补）

- `engine_adapter/_protocols.py`：`ws_path()` → `endpoint_url()`（A2A 单端点，引擎差异消失）
- `bot_run/_bot_websocket_client.py` → A2A client（SDK 或 httpx+SSE，
  `PROTOCOL_VERSION`、握手、tick 逻辑全退役）
- `_baas_service._resolve_ws_connection` → resolve A2A 连接（`DEFAULT_WS_PATH` /
  `DEFAULT_ADAPTER_PORT=20003` 常量随语境迁到 A2A endpoint 常量）
- BCS 的 `bcs-protocol` 帧（`RequestFrame::new("chat.send")`）在阶段 3 改为 Rust A2A
  client；`A2aChatRunService` 的 run 语义与 A2A Task 同构，操作面直译

### 6.5 Agent Card

一期静态生成：name（bot 名）、description、capabilities（`streaming: true`、
`pushNotifications: false`、`extensions: ["inject", …]`）、defaultInputModes/OutputModes
（text）、`securitySchemes` 声明 Bearer（内部 proxypass JWT，见 §6.6）、
supportedInterfaces 钉 1.0.x。skills 来源 = O3 开放决策（推荐：bot manifest 派生 +
回退单 skill）。card.url 的 base 由部署侧注入（singlebox 与集群同码道，仅 config 差异
——遵守"业务代码禁止按环境分叉"）。

### 6.6 鉴权与版本

- 本期鉴权语义**不变**：传输级沿用 agentclawproxy 的 x-proxypass-token JWT
  （HS256，target+exp），A2A card 里以 Bearer security scheme 声明。OAuth2/OIDC 属
  对外门面（O4），不在本期
- 版本协商：`A2A-Version` 请求头 + card supportedInterfaces；不支持版本返回标准
  版本协商错误（映射表实现期对 spec 逐条核对）
- `connect` 帧里的 `x-moltis-mcp-token` 历史鉴权参数退役，改标准 Authorization 头

### 6.7 错误处理

- facade `supports()` 保留 501 能力探测语义（现 `EngineRuntimeRelay._CAPABILITY_UNSUPPORTED_STATUS`
  行为不变），A2A 壳映射为 JSON-RPC 标准错误码（UnsupportedOperation / MethodNotFound）
- Task 级错误进 `failed` 状态 + 标准 error details；SSE 中途错误以事件流内 error 帧
  + 连接关闭表达，客户端按断流恢复路径补齐
- 传输超时语义变化显式化：v3 的 30s tick 死亡判定退役；A2A 无连接概念，
  adapter 内 run deadline（BCS 侧 `A2A_DOWNSTREAM_EXECUTION_TIMEOUT_MS` 同族语义）
  + SSE 空闲 ping 承担活性

### 6.8 观测

新增指标与 v3 指标并存到收割期，作为切换健康证据：
`a2a_task_duration_seconds`、`a2a_first_event_latency_seconds`（对比 ws 首 token）、
`a2a_sse_reconnect_total`（断流恢复触发率）、`a2a_extension_inject_total`。
日志结构对齐现有 bcs_observability 风格（body-free）。

## 7. 语义映射总表（v3 ↔ A2A ↔ facade）

| v3 | A2A 落法 | facade 锚点 | 备注 |
|----|----------|------------|------|
| connect/hello-ok + minProtocol/maxProtocol | 无握手（无状态 HTTP）+ `A2A-Version` 头 | — | 客户端连接管理简化 |
| 30s tick 心跳 | SSE `ping` 保活 + run deadline | `task_events` | 在线态语义见 §8 |
| chat.send | `message/send` | `start_task` | 直译 |
| chat 流式 EventFrame | `message/stream`（SSE：message/status-update/artifact-update） | `task_events` 细粒度→聚合 | delta 保粒度见 §8 |
| chat.abort | `tasks/cancel` | `cancel_task` | 直译 |
| chat.subscribe / chat.resume | 断流重连 + `since_seq` 补齐（见 O1） | `task_events(since_seq)` | BCS run 模型同构 |
| chat.inject | **extension** `message/inject` | `inject_context` | D3 红线核心 |
| interaction.requested / resolved | Task 状态 `input-required` + 后续 message | `resolve_interaction` | 语义同构 |
| sessions.reset | context 处置（`contextId` 续用或新开） | `reset_session` | 行为等价 |
| 每引擎 `/api/{engine}/ws` 钉路径 | 删除；A2A 单端点 | — | BaaS `ws_path()`→`endpoint_url()` |
| 501 能力探测 | JSON-RPC 标准错误映射 | `supports()` | 行为不变 |
| 管理 REST 89 路由 | **不动** | — | 与信道协议正交 |

## 8. 五个语义缺口的落地（行为完全等价红线）

| # | 缺口 | 落法 | 验证 |
|---|------|------|------|
| 1 | chat.inject（BCS 旁观注入） | extension 方法 `message/inject`，card extensions 声明；inject 上下文保留 `ChatSendParams` 的参与方/`delivery_type`/routing 字段语义 | G4：BCS 群聊旁观用例过 extension 原型 |
| 2 | 断流恢复（resume） | O1：优先标准重挂机制；能力不足则 `events/resubscribe`（自建 extension，`since_seq` 游标） | G1+spec 核对 |
| 3 | token delta 粒度 | kernel 事件流保 delta 原语；A2A 壳聚合成 message parts（`metadata.sequence`），前端聚合管线不降级 | G3：真引擎流式 UX 对比 |
| 4 | 在线态（连接=在线） | `/health` + card；BCS BotRegistry 的 WS 存活判定改健康轮询（BCS 侧改造，阶段 3） | BCS contract test |
| 5 | 每引擎 WS 钉路径 | 删除，A2A 单端点 | conformance 全引擎矩阵 |

## 9. 测试策略

1. **一致性（conformance）**：a2a-sdk 官方 client 作为对端打真 adapter（openclaw +
   claude_code 实引引擎），全方法 + extension 方法矩阵——D4 无论 SDK 主用还是降级
   手写，此对端都保留（手写时它就是契约防回归）
2. **flow coverage**：singlebox 框架新增 a2a profile（沿用
   `tests/community/framework/flow_coverage.py` 模式）；changed-line coverage ≥80% 走
   本地 `ci_test.sh` 同口径复现
3. **行为等价回归**：同一用例分别走 v3 壳与 A2A 壳，断言语义输出一致（双壳期特有的
   对拍测试，收割前是主要安全网）
4. **性能基准**：SSE vs 常驻 WS 的建连开销 / 首 token 延迟 / 吞吐 / 内存，
   门限见 G5
5. **BCS（阶段 3）**：Rust 侧 mock A2A server contract test + 真链路联调

## 10. 迁移蓝图

前置硬约束：engine adapter 部署在各 bot 容器内，镜像逐 bot 升级——过渡期老容器只有
v3、新容器双壳，**双协议窗口的下限 = default bot / 服务 bot 的 publish-restart 节奏**。

### 阶段 0：Spike（先于一切，验收门禁）

| 门 | 内容 | 通过标准 |
|----|------|----------|
| G1 | SSE 过 agentclawproxy + gateway 全链路 | 无代理缓冲、断线行为明确、自动重连 + since_seq 补齐可用 |
| G2 | a2a-sdk 内网依赖通路 | SDK 可得（D4 主路径）；否则降级路径即时书面决策 |
| G3 | delta 聚合接真引擎（claude_code 流式） | 打字机 UX 无可感降级 |
| G4 | `message/inject` extension 原型 | BCS 群聊旁观用例跑通 |
| G5 | 性能基准 | 建连开销 P50 相对 WS 常驻增量 ≤50ms；流吞吐 ≥WS 的 90%；内存持平 |

门禁任何一条不过 → 设计回炉（G2 过不了切 D4 降级分支，其余门失败按 §11 风险预案）。

### 阶段 1：community 落地 + BaaS 迁移

- 改动：A2A server + facade + SPI 重排落地（community 核心 + corp 接线）；BaaS 改 A2A
  client（§6.4）
- 进入：G1–G5 全过
- 退出：BaaS 全量走 A2A；双壳对拍回归绿；singlebox CI 绿；v3 壳 Shadow 保留
- 回退：BaaS 配置切回 v3 即回

### 阶段 2：backend + 前端连接层

- 改动：`grt_chat_service` 弃 WS 直连；前端 `connectionStore` / `engineService` /
  `requestConfig` 的 WS→EventSource 化
- 退出：预发全链路（含 proxypass SSE）稳定一个观察周期
- 回退：同上，v3 壳仍在

### 阶段 3：BCS + 桌面

- 改动：bcs-protocol→Rust A2A client（含 inject extension、在线判定改健康轮询）；
  桌面直连改 A2A
- 备注：桌面若滞后严重 → 启用方案 C 转换层兜底，不阻塞收割排期
- 退出：`contract_a2a_chat` / `conformance_direct_a2a` 等既有套件改指 A2A 通道全绿

### 阶段 4：收割

- 改动：新容器镜像不再内置 v3 处理路径；存量容器按 publish-restart 自然淘汰 +
  强制窗口（对齐服务 bot 轮转节奏）；最后删除 `kernel/frames.py` 与
  `ws_server.py`（Avernet）+ corp 两个 WS server 壳（ocb）、v3 客户端代码
- 进入：阶段 3 退出 + v3 通道流量占比 < 阈值（观测指标裁决，阈值上线前定，建议 1%）
- 回退：收割前保留 ≥1 个可回退 release；删除动作单独 PR

## 11. 风险登记

| # | 风险 | 等级 | 缓解 |
|---|------|------|------|
| R1 | spec 演进（一年内 0.2→0.3→1.0 连续 breaking） | 高 | 钉 D5 基线；`protocol.py` 防腐层收敛变更面（O5 跟版策略） |
| R2 | SSE 过代理链路缓冲/超时 | 高 | G1 首验；proxy 网关 buffering 配置进部署 checklist |
| R3 | 性能门限不达标 | 中 | G5 门禁；预案：高频小操作场景局部裁剪（回到"是否全量换轨"再评审） |
| R4 | extension 固化，二期纯化失败 | 中 | inject 语义按最小面定义；O6 二期评估 |
| R5 | 多团队排期协同（五条客户端线） | 高 | 本蓝图只锁依赖顺序与退出条件，各线 spec 独立排期；收割以观测指标而非日历驱动 |
| R6 | 容器版本 skew 拉长双壳期 | 中 | 双壳窗口按发布节奏给下限；阶段 4 强制窗口兜底 |
| R7 | a2a-sdk 内网通路缺失 | 中 | G2 早验 + D4 降级路径 |
| R8 | a2a 命名撞车（仓库已有 BCS `a2a_chat` 内部直聊） | 低 | 新代码模块统一 `a2a transport/`+`interop` 语境命名，文档全局以"标准 A2A vs 内部 A2A 直聊"区分 |

## 12. 开放决策（待拍板，均附推荐值）

| # | 决策 | 推荐 | 何时定 |
|---|------|------|--------|
| O1 | 断流恢复用标准机制还是自建 `events/resubscribe` extension | 优先标准，能力不足再 extension | 阶段 0（spec 核对） |
| O2 | `message/inject` 上下文字段面（旁观/转发/路由语义的最小集） | 按 BCS `ChatSendParams` 参与方字段最小映射 | 阶段 0 G4 |
| O3 | Agent Card skills 来源 | bot manifest 派生，回退单 `conversation` skill | 阶段 1 |
| O4 | 对外门面（公网路由 + OAuth2/OIDC）启动时机 | 阶段 1 完成后评估 | 阶段 1 后 |
| O5 | a2a-sdk 跟版策略 | minor 自动跟、major 评审制 | 阶段 1 前定版 |
| O6 | 去除 inject extension 的二期纯化评估 | 阶段 4 前做一次语义评估 | 阶段 4 前 |

## 13. 参考

- A2A 规范（v1.0.x）：https://a2a-protocol.org/latest/ ；仓库
  https://github.com/a2aproject/A2A （Linux Foundation 治理；官方 Python SDK
  `a2a-sdk`，兼容 1.0 与 0.3）
- 现状资产：`engine/adapter/src/engine/community/kernel/frames.py`（v3 帧）、
  `api/transport/ws_server.py`（v3 server）、
  `apps/backend/docs/openapi-v1/engine-surface.md`（管理面 HTTP 面）、
  `apps/bcs/crates/services/bcs-message-flow/src/a2a_chat/`（内部 A2A 直聊，
  命名撞车注意）
- 内部事实来源：本设计的前置调查（v3 现状 / A2A 官方文档核对）完成于 2026-10-13，
  A2A 协议细节以当日官方 spec 为准；`tasks/resubscribe` 等具体机制的精确语义在
  阶段 0 逐条核对后回填 O1
