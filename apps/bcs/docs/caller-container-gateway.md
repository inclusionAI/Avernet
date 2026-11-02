# Caller Container Gateway 内部契约（实施子集）

日期：2026-10-04。对应 spec §10.8、P1 契约 / P3 H2A 准备。

> **状态注记（2026-10-20 更新）：** 2026-10-09 的注记曾声明 caller 实现已"整体回退"；
> 此后 caller 已随 `d72f4a49c` 及后续提交**重新落地**：ensure/protected send/observe
> inject/身份插件装配均为当前生产代码（接线声明见 §"受保护 root-H2A chat.send v1"与
> `bootstrap/bcs/src/caller_identity_wiring.rs`）。但本文中段约 §317-805 区域仍保留
> 2026-10-08 那一轮**已回退实现**的历史记录：其中引用的符号
> （`CallerContainerHttpGateway`、`status_router`/`cancel_router`、
> `CallerExecutionStatusPort/CancelPort`、`CallerDeliveryReconciliationService`、
> `DeliveryWorkBatch::Uncertain`、`(env,status,delivery_id)` 索引、
> `conformance_caller_reconciliation`/`caller_status_contract` 测试等）**不在当前代码中**，
> 请勿作为实现证据或对接依据；当前实现以 `CallerDispatchPipeline` +
> `CallerExecutionControlService` 为准。本文的 **wire 契约部分**（ensure/send/inject/
> ACK/重试/兼容性与墓碑段落）仍是权威。当前实施进度与测试证据统一以
> `Avernet/src/bcs/specs/2026-09-16-caller-identity-session-container/plan.md` 为准。

## Caller 路由兼容：ensure 与 subject 模式

Provider Caller 能力增加 `container_routing`：缺省 `ensure` 保留原路径；显式
`caller_subject` 才跳过 ensure RPC，由 Provider 懒创建/复用 Human×Bot 容器并保证 session 隔离。
`supports_caller=true` 仍必需；群同步还需 `supports_observe_inject=true`。
BCS 准备时读取既有 Provider 能力源，读失败或 ensure 失败均不自动降级。

两种模式的 protected `chat.send` 和普通 observe-only `chat.inject` 都发送
`extensions.caller_context.subject`，其 ref 始终为工号而非插件伪名。send 保持现有 proof purpose 语义，caller_task 仍只是任务描述，
Owner 仅派生仍走 Owner 路由，不能仅凭 caller_context 存在切换执行身份。
inject 的 execution.mode 为 observe_only，caller_context 仅含接收 Human 的 subject，
不是消息 sender；不携带执行 token、IAM 材料或执行身份有效期，不调用身份兑换。
ensure 模式 inject 额外携带配对 `caller_ref/target_epoch`，subject 模式省略整个 runtime_target。
ensure 准备接口保留现有 caller_subject 请求对象；history_complete 控制变体使用同样的
路由专用 caller_context.subject，不携带执行 token。BCS 先按冻结清单发送正文，再发 complete；
完成响应必须为 ok=true 且 history_ready.history_snapshot_id/manifest_digest 与请求匹配。
普通 ok=true 不算历史 ready。Bot/session 复用现有 envelope，Gateway 在当前 Caller/session
隔离环境应用正文并验证快照，容器重建必须废弃旧 ready/去重状态。具体字段见 API 报告 §6.4。
Provider 必须拒绝 subject 与引用范围/代次冲突，不能自动回退默认容器。

subject 模式准备描述符的 ready 仅表示所声明的路由可用，不证明物理容器或历史快照就绪；
Provider 必须在接受任务前完成隔离和历史屏障。此兼容不新增生命周期或持久化身份。

## 状态与边界

新增 `bcs_service_api::port::CallerContainerGatewayPort`，是传输无关的
outbound Service API port；调用方为应用层 `CallerContainerPreparationService`，实现方为
`bcs-provider-http::CallerContainerHttpGateway`，composition root 负责装配。**本次没有接通线上调用链，
已有 H2A Provider HTTP adapter 子集，但不是完整 Provider wire contract，也没有实现下游容器创建。**

## 请求与响应

| 请求字段 | 语义 |
| --- | --- |
| caller_subject_ref | 可信 Human 工号（不加 human_ 前缀），不接受从消息 sender 或 DB holder 伪造 |
| target_bot_id | canonical Bot ID |
| session_id / environment | 上下文隔离及环境范围；长期容器仍按 Human×Bot 复用 |
| minimum_isolation | V1 `CallerContainer`，最低隔离要求 |
| idempotency_key | 稳定重试键，同键不同输入必须失败，不含凭据 |
| run | run_id/root_run_id/message_turn_id 必填，parent_run_id 可选；仅关联，不作为 parent 授权证明 |
| policy | lazy/warmup/must_exist 与正数 ttl_ms；容器 TTL 不延长身份期限 |

| 响应字段 | 语义 |
| --- | --- |
| target_bot_id / caller_subject_ref | 返回关联目标，不赋予新的执行权限 |
| ready | 仅容器可用；不等于 history_ready 或 MCP 已授权 |
| isolation | `ContainerBound` 实际保证，与最低要求分开建模 |
| binding_ref | 可选 opaque 引用；缺失不等于降级隔离，不进入 CallerContext |
| sandbox_container_id | 顶层可选真实沙箱 ID；AgentPass 签发必需，JWT 允许缺省；不得用 binding_ref 替代 |
| runtime_generation | 实际隔离环境替换时改变的代次 |
| expires_at_ms | 返回引用/隔离描述的有效期限，不延长 Human 身份有效期 |

响应不实现 Serialize/Deserialize；Debug 隐藏 subject 和 binding。请求不实现
Debug/Serialize/Deserialize，避免无意暴露 opaque subject。临时引用不写入 DB、
消息、队列快照或模型上下文。Gateway 自行保存物理容器映射，BCS 不连接容器。

## 应用准备流程

```text
可信入站编排（尚未接线）
  → PrepareCallerContainerCommand：ensure 请求 + 原始身份截止时间
  → 校验非空 subject/Bot/session/env/幂等键及身份期限
  → Gateway.ensure（至多一次）
  → 再读时间，检查身份期限、返回关联、binding、ready、容器期限
  → PreparedCallerContainer，有效期 = min(身份期限, 容器期限)
```

请求构造前须完成身份建立及 Bot/session/environment 授权。本服务不认证 Human，
不替代 admission、撤销检查、history_ready 或 MCP 授权，也不签发 token。
Gateway 错误统一转为 `GatewayFailed`，不泄露响应正文；该错误包括结果未知，
不能推断容器未创建。返回成功后派发方仍须重新检查有效期。无 DB 写入或日志输出。

## 后续接入约束

- ensure 使用 BCS 服务认证，不依赖尚未签发的执行 token；不自动续签身份。
- 应用 consumer 已核对 Bot/subject、就绪状态和期限；当前隔离枚举仅接受 `ContainerBound`。代次原样返回，跨派发代次比较仍属于后续编排。
- 不支持/不可用返回错误，不回退 Owner；超时不证明未创建，不能盲目生成新键重试。
- 已补 run/policy 和 Provider H2A 报文映射；仍需群 scope、观察引用、生产目标解析及版本协商。
- 群聊必须补 `observe_target`、目标代次和 history_ready；本子集不可作为群聊 ready。
- 真实 `CallerContainerPreparation` + recording Gateway 已有 happy/failure conformance；
  覆盖关联错配、空作用域、调用前及调用后身份过期、未就绪/过期容器、可选 binding、
  有效期限取最小值、Debug 脱敏和 Gateway 失败不重试。另有 9 项本地 HTTP adapter 测试，不是外部 Provider 或端到端验收。

## 兼容与成本

这是新增、未装配的 port；原 BotDeliveryPort、Provider 报文和派发保护不变。
不新增数据库字段、查询或后台任务。尚无 Gateway/AgentPass/Runtime 联调结果。

## Provider HTTP adapter 实施边界（2026-10-05）

适配器固定在可信编排提供的 BotDeliveryTarget/environment，不自行发现或授权 Bot。
复用现有 Provider webhook 地址、服务 Bearer 和 OutboundUrlGuard；仅支持协议 2.0。
ensure 使用独立的无正文日志路径，不经过普通 Provider 请求正文记录器；不把身份放进
持久化 BcsFrame。请求 params 对应 §10.8；H2A 无 group_id，外层 bcn_group_id 为空。
响应按 ok/isolation 解析，返回 Bot/subject 关联来自本次已绑定请求，**不是 Gateway echo**。

请求时重新校验 URL/DNS 并绑定解析地址，禁用代理、重定向及客户端重试；总超时覆盖
DNS、请求和响应读取，响应上限 64 KiB。所有错误不携带 URL/正文/opaque 引用。
本地测试覆盖正常请求、目标/环境错配、无效配置、超大响应、Gateway 拒绝/非法隔离
及重定向不转发凭据；新增响应体停滞超时、无 Content-Length 的合法 JSON 分块超限
（含小响应成功对照）、成功/失败路径日志捕获与 Debug 脱敏回归。日志测试使用
独立 subscriber 和前后正向标记，检查 service token、subject、binding、错误正文未泄漏。
2026-10-05：Provider HTTP 全套 77 项、Service API 全套含 doc-test 193 项通过。
这些是本地契约回归，不覆盖进程外日志采集系统或真实下游实现。

生产入口/装配、Provider 能力准入及撤销复核尚未接通，Caller 派发保护保持关闭执行。


## 受保护 root-H2A `chat.send` v1（2026-10-09，未生产接线）

这是独立 outbound Service API `CallerExecutionSendPort`，以及复用 Provider
webhook 的 **BCS→Gateway Plugin wire 子契约**，不是新 HTTP 路由。通过
`CallerContainerHttpGateway::execution_sender` 显式选择，不改变普通
`BotDeliveryCommand`，不放开现有 Caller gate。

```text
Managed StartSend（预留真实 run/attempt/dispatch，尚未提交网络）
  → 真实 JWT / HumanCallerExecutionPreparation
  → HTTP caller.container.ensure
  → 验证后得到 subject、proof、最早有效期
  → SendCallerExecutionCommand（仅内存、一次所有权）+ canonical_request_digest
  → Managed commit_caller_dispatch（摘要/期限 CAS，不代表已发送）
  → [待接线：真实 dispatcher 策略/目标/取消复核]
  → Provider HTTP：chat.send + 独立 runtime-only extensions
  ← 严格 DispatchAck（accepted / rejected / unknown）
  → Managed apply_caller_dispatch_receipt（已有内部 CAS，待 dispatcher 接线）
  → [待接线：认证事件、结果发布]
```

| 输入 | 定义 |
| --- | --- |
| dispatch | 复用 status 的八字段原始关联，dispatch_id 对应已提交 request_id |
| group_id / message_turn_id | canonical DM Group 与原始 Human 消息关联；不是身份来源 |
| text | 本子集只支持根 H2A 文本；普通 message 中不放身份 |
| request_digest | `sha256:` + 64 位小写十六进制；由可信 producer 对规范化非秘密任务/控制投影计算并在发送前提交；适配器发送前重算并精确比较，不替代可信准入/持久化 |
| control_revision | 已提交控制版本，允许初始 0；不能由请求体自报 |
| not_after_ms | identity、container、task 的原始最早期限；不能重试延期 |
| caller_subject_ref / execution_token | SecretString；仅用于运行时信封，不可进 DB/队列/日志/模型 |
| proof_format | 可信装配明确选择 jwt 或 opaque；不是从 token 猜测信任源 |
| proof_type | 签发插件标签，wire `caller_execution_token.type`：BCS AgentPass 签发为 `agentpass`，默认 JWT profile 为 `jwt`；同为编码标签，不是信任源 |

请求沿用 Provider webhook envelope，外层 `id=run_id`、`session_id`、
`bcn_group_id`、`to_bot`、`timeout_ms`；正文为根级 `message: {text}`。
运行期扩展位于根级 `extensions`，不是 `params.extensions`。
`extensions.execution` 包含 environment、target_bot_id、caller_task、
run（run_id/root_run_id/attempt/dispatch_id）、control_revision、message_turn_id、
not_after_ms、request_digest；caller_context 和 caller_execution_token 保持原语义。
此子集没有物理 route 或上游登录态，不代表完整群历史与 child lineage 已接线。

群任务的 protected send 从既有派发事实映射 `bcn_group_id`，并在有历史头时发送
`extensions.execution.history`：`history_snapshot_id/history_cutoff_seq/projection_version/manifest_digest`。
H2A 无历史头时省略，不伪造空快照。此映射不等于 Gateway 已完成历史补齐或快照隔离。

Caller observe-only `chat.inject` 也沿用根级 `message: {text}`；只在 HTTP 2xx 且
标准 Provider JSON ACK `ok=true` 时成功。`ok=false` 使用顶层 `retryable` 判断重试；
4xx、空/畸形/超过 64 KiB 的响应不重试，5xx/传输失败/超时可有界重试同一 dedup key。
HTTP 发送和 ACK 分块读取共用 10 秒 deadline，读取时限制 64 KiB。它不携带执行 token。

### Provider HTTP 派发字段的唯一来源

Caller 复用既有任务、attempt 和派发关联，不建立独立 parent/child 生命周期或表。
内部 `CallerDispatchAssociation` 只是 StartSend 事实的类型化载体，不要求 Provider 回传。

| 信息 | 唯一来源 |
|---|---|
| Provider / Provider Bot | envelope 的 `to_bot.provider_id/provider_bot_ref` |
| 会话 | envelope 的 `session_id` |
| 回调 run | envelope 的 `id`，即既有 `run_id` |
| Human 主体 | `extensions.caller_context.subject` |
| 执行 proof | `extensions.caller_execution_token`，runtime-only |
| 执行期限 / 历史边界 | `extensions.execution.not_after_ms/history` |
| attempt、dispatch、控制版本、业务摘要、parent/root/turn | BCS 既有执行记录及可信运行时关联；不重复放入 send wire |

BCS 在发送前核对目标 Bot/Provider 与既有派发关联，网络 ACK 由在途调用关联回原命令，
不从 Provider 回显取得 CAS 版本或摘要。身份 proof 内的目标、用途和期限约束不因此取消。
`isolation.binding_ref` 是可选隔离约束，不是另一份执行生命周期。

**兼容性：** 这是 Caller 接线契约的协调升级：新版 send 不再输出旧 execution 关联字段，
旧 Provider 若读取它们，须先迁移为 envelope/run 关联及 proof 消费；不能仅凭 ACK 向后兼容
宣称整个 wire 无破坏。普通 Owner 请求、数据库结构和 BCN envelope 版本不变。
旧 ACK 的 echo 字段可以存在，但不作为权威。HTTP `caller-terminal` 入口已移除
（wire 从未推送上线，无任何 Provider 按此接入，不存在需兼容的旧接入方）；
结果/终态走既有 `/bot/events`。

**重试边界：** `run_id` 不天然区分 attempt。结果未知不能自动换 attempt 重发；
同一 run 只允许已证明未受理的安全重投。BCS 内部 attempt/CAS/取消控制不删除。
普通事件不能凭 `run_id` 证明事件属于哪个旧 attempt，也不等价于带序号的远端取消证据；
需要此类强证据的 status/cancel 控制契约仍保留，不能用普通 final 伪造其水位。


### 最小接收 ACK

```json
{"ok": true}
```

HTTP 2xx 且显式 `ok=true` 表示接收，不表示执行完成或 MCP 已授权。不要求返回
`dispatch_id`、`dispatch`、`control_revision`、`request_digest` 或 token。
可选 `state` 支持 `"accepted"` / `"unknown"` 或对应 `{"state":"accepted"}` 对象；
省略时为 accepted，其他成功状态（包括 null、rejected）拒绝。
拒绝使用 `{"ok":false,"error":{"code":"HISTORY_NOT_READY","retryable":true}}`。
`retryable` 不自动授权重发或续签。超时、断线及畸形 ACK 不能证明任务未启动。
BCS 使用原命令的控制版本/摘要收敛 ACK，不信任 Provider 的旧 echo 字段。

错误分界：请求开始前返回 NotSent，仅说明本次未发出；请求开始后异常统一
OutcomeUnknown。明确 accepted 只是受理；Provider 执行结果仍须认证事件/状态链路。
总预算包含 DNS/TLS client 准备与网络；发送前再读身份时钟；禁重定向/重试，
响应最多 64 KiB。没有 DB 访问、后台任务或发送取消的原子保证。

`caller_send_contract` 本地测试覆盖真实 JWT→ensure→send、错误 ACK、调用前与准备中
过期、ACK 丢失、重定向不传凭据、日志脱敏；非真实 Gateway 去重/取消账本验收。
本实现不代表第 1 步 H2A 端到端完成，仍缺生产入口/dispatcher/结果发布及 readiness。

### 规范化摘要与准备后提交（内部应用接口，尚未挂载 dispatcher）

`SendCallerExecutionCommand::canonical_request_digest()` 是无 I/O 的纯计算，
使用 SHA-256，输出 `sha256:` 加 64 位小写十六进制。v1 原文编码固定为：

1. 域分隔字节 `b"bcs.caller.root-h2a.dispatch.v1\0"`。
2. 顺序编码 environment、provider_id、provider_bot_ref、target_bot_id、session_id、
   run_id、dispatch_id、group_id、message_turn_id、text；每个字段为 UTF-8 字节长度
   （u64 大端）后接原始字节，不 trim、不做 Unicode normalization 或 JSON 编码。
3. attempt（u32 大端）、control_revision（u64 大端）、not_after_ms（u64 大端）。

不读取 subject、proof、proof_format、binding、request_digest 本身或网络 timeout；
摘要不是身份凭据或权限证明。HTTP adapter 在任何发送前重新计算，正文或控制字段修改但
沿用旧摘要返回 NotSent。协议双方必须同步升级规范化规则，不能私自改变 v1 投影。
独立向量：依次字符串为 test/provider-a/runtime-a/bot-a/session-a/run-a/dispatch-a/
 dm-a/message-a/处理这条消息（此处分隔符和换行不属于字段），数值为 1、0、1800000030000，
结果为 `sha256:4f09da419e71169e7364f4e4dbd94946f26e1750fd60e86aadcdc054be723ede`。

真实 dispatch ID 在 StartSend 后产生，有效期限在身份/容器准备后确定，因此新增
`CommitCallerDispatchCommand`：delivery_id、expected_state_version、now_ms、完整 dispatch、
request_digest、control_revision、not_after_ms。`commit_caller_dispatch` 使用既有 Bot guard
和仓储 CAS，只接受尚未 submitted/accepted/cancelled/terminal 的 Caller HTTP Send Dispatching
行；原八字段须完全匹配、revision=0、deadline 晚于 now 且不超过 run_deadline。
保存 `{version:1,request_digest,not_after_ms}`，不保存正文、身份或 binding。
同版本下完全相同的重复提交无写；摘要/期限改动、旧版本或取消已提交均 Conflict。
提交只增加状态版本，保留 Dispatching，不设置 submitted/accepted 时间；普通未携带类型化
commit 命令的 CommitDispatch 事件拒绝。提交后网络前仍需策略、期限、目标与取消检查，
这不是跨系统原子发送许可，也不替代 Gateway 取消水位。

### 准备结果 → 发送 → 回执的应用编排（内部，未挂载生产 dispatcher）

`HumanCallerDeliveryDispatchService::dispatch(DispatchPreparedHumanCallerCommand)`
消费一次性的 `PreparedHumanCallerDelivery` 和可信应用投影后的 text；输入无 Debug/Clone/serde，
不是 public request、恢复入口或新的身份签发接口。实现为 message-flow 的
`HumanCallerDeliveryDispatch`，通过 composition 注入同一 Managed、preparation、受保护 sender
和 proof_format，不在应用层选择 HTTP/JWT 实现。

```text
已完成可信身份准备的 owning dispatcher
  → dispatch(prepared, canonical text)
    → preparation.validate（当前 attempt / DM 策略 / 期限）
    → 读取原 delivery，导出已提交 Provider 关联，核对全部 attempt 关联
    → 计算实际摘要 → commit_caller_dispatch（原 state_version CAS）
    → 仅用该次 CAS 返回版本更新 transient prepared.attempt
    → preparation.validate（再次校验当前 attempt / DM 策略 / 期限）
    → 将 subject/proof 所有权转交 sender，最多发送一次
    ← ACK / NotSent / OutcomeUnknown
    → 原提交版本 CAS 落回执或本次传输结果
  ← 持久化状态，或脱敏 Conflict / Unavailable / Validation 错误
```

- 更新 transient 版本只来自本次成功的 metadata CAS；不是重新读取后覆盖并发取消。
  最终校验冲突不写入；其他最终校验失败在确定尚未调用 sender 时，尝试针对原提交版本
  `DefinitelyNotSent(retry=false)` 收敛，再返回 Validation；写失败或 CAS 冲突优先返回。
  首次校验失败保持只读，不借机改变已经由其他路径处理的任务。
- sender 的 NotSent 收敛为 Failed，不重试；OutcomeUnknown 收敛为 Unknown，保留 lane。
  ACK 由在途请求关联到原命令，内部 CAS 保持严格匹配；HTTP 期间取消导致旧版本 ACK 被拒绝，不重读后强行落库。
  HTTP 已发出但落库失败时返回错误，维持原可能已发送状态，交给现有恢复机制查询。
- Drop/abort 不是未发送证据，不能据此释放 lane 或重新签发身份。无数据库锁跨网络 I/O。
- text 必须来自本条已准入消息的 canonical 投影。本组件不读取消息正文或完成入口鉴权，
  不补齐群历史/child，也不替代实时目标、能力、代次、owner lifetime 及远端取消 fence。
  当前测试装配不代表真实 ingress/queue dispatcher 已接线，生产 gate 仍关闭。

契约测试使用真实 Managed、Human preparation、JWT、HTTP 与 Memory/SQLite；包括正常版本衔接、
执行前过期/取消、网络前过期、未知/非法 ACK、HTTP 中取消，以及 DB 边界故障下摘要提交失败、
提交后期限失效、ACK 写入失败。共享迁移测试不计作独立 Caller 场景。

### ACK → Managed 状态（内部应用接口，尚未挂载 dispatcher）

`StartSend.transport_context_json.caller_dispatch` 可提交
`{version:1, request_digest:"sha256:..."}`；只保存非身份摘要。Managed 校验格式、
Caller Send/HTTP 范围，拒绝预置 receipt；不代替可信 producer 计算规范化摘要。
`ApplyCallerDispatchReceiptCommand` 包含 delivery_id、expected_state_version、
now_ms 与完整 HTTP receipt。事务内校验原八字段关联、摘要和初始 control_revision=0：

| 回执 | Managed 行为 |
| --- | --- |
| accepted | Running；取消已提交则保持取消状态，不代表工具成功 |
| unknown | Unknown/CancelUnknown，保留 lane，不重发 |
| 确定拒绝 | Failed；取消已提交则 Cancelled，不创建 retry/attempt |
| retryable HISTORY_NOT_READY | 保留 Unknown 与原 dispatch，不能缓存永久失败；历史屏障重查/重发尚未接线 |
| 完全重复且版本匹配 | 无写入，不改变已有终态 |
| 错关联/摘要/控制版本、旧 CAS 版本、与已受理矛盾 | Conflict，不自动重读后覆盖 |

新的可选 JSON 字段需要严格 reader 协调升级，不新增表或 migration。
无 token、subject、binding、正文由上述提交入库。规范化摘要与准备后 CAS 已实现，真实 dispatcher 接线仍待完成；
这不是原子网络取消 fence。SQLite 写失败不成功返回，receipt 和生命周期一起回滚。
组合测试已覆盖真实 Managed StartSend→JWT/ensure→规范化摘要→Managed commit→HTTP send→Managed ACK；
摘要来自实际命令，提交前只发生 ensure、未发生执行发送；仍不是生产入口端到端验收。
早期 StartSend digest-only 元数据兼容保留；新准备链路使用包含最终期限的 commit。

## execution.status v1：无凭据受理查询子集

这是新增、未装配的 **BCS→Gateway Plugin wire 子契约**，实现 outbound business port
`CallerExecutionStatusPort`，不是新的 BCS HTTP 路由。复用已解析的 Provider webhook，
`POST`，BCN transport protocol `2.0`；状态方法自身以 `contract_version: 1` 独立版本化。
它不表示 Gateway 已支持完整执行/取消协议，不能仅凭 status 子集开放 Caller 派发。

请求示意（`id` 每次查询新建，与原 dispatch ID 分开）：

```json
{
  "type": "req",
  "id": "query-request-id",
  "method": "execution.status",
  "session_id": "session-a",
  "bcn_group_id": "",
  "to_bot": {"provider_id": "provider-a", "provider_bot_ref": "runtime-a"},
  "timeout_ms": 2000,
  "params": {
    "contract_version": 1,
    "dispatch": {
      "environment": "test",
      "target_bot_id": "bot-a",
      "provider_id": "provider-a",
      "provider_bot_ref": "runtime-a",
      "session_id": "session-a",
      "run_id": "run-a",
      "attempt": 1,
      "dispatch_id": "original-managed-request-id"
    }
  }
}
```

`dispatch` 的 8 个字段全部必填；字符串非空、attempt 正数。
目标 Bot/environment/Provider/runtime ref 必须匹配可信 composition 提供的原目标，
错配在网络调用前拒绝。`dispatch_id` 复用 managed `request_id`，不生成新 attempt。
`bcn_group_id` 为空是此查询子契约的外层兼容字段，不参与身份推断；实际范围由 dispatch 指定。

HTTP 2xx 响应必须为下列严格结构：

```json
{
  "type": "res",
  "id": "query-request-id",
  "ok": true,
  "contract_version": 1,
  "dispatch": {
    "environment": "test",
    "target_bot_id": "bot-a",
    "provider_id": "provider-a",
    "provider_bot_ref": "runtime-a",
    "session_id": "session-a",
    "run_id": "run-a",
    "attempt": 1,
    "dispatch_id": "original-managed-request-id"
  },
  "observation": "accepted"
}
```

- `id` 必须匹配本次查询；`dispatch` 来自 Gateway 实际回执并逐字段比对，**不以请求值补齐响应**。
- `observation` 仅接受 `accepted / not_found / unknown`。accepted 是持久受理事实，
  不代表运行或成功；not_found 不代表在途请求以后不能启动。即使 not_found 也必须回显完整查询范围。
- `queued/running/terminal`、event_seq、control_revision、cancel_state/结果引用尚不在本子集。
  顶层及 dispatch 的缺字段、未知字段、错误类型或版本均拒绝；后续完整回执需显式演进，不能静默降级。
- HTTP 错误（包括 HTTP 404）、ok=false、非法 JSON、错关联、超大/停滞响应均为固定脱敏错误，
  **不能映射成 not_found**，不自动重试、不重发任务、不刷新 Human 身份。
- 仅发送 BCS→Provider 服务 Bearer；不传 Human 登录态、caller ref、binding 或 execution proof。
  身份过期仍可查询。复用 ensure 的 URL/DNS guard、解析地址绑定、禁止重定向/代理/重试、
  64 KiB 响应限制和覆盖整个请求的超时，不经过普通正文日志路径。

影响范围：本地适配器及声明支持本方法的 Gateway；既有 ensure/普通下行报文不变。
没有数据库访问、新依赖、配置项或生产工厂。生产能力协商、原目标重新解析/授权、
owning dispatcher 装配、Gateway 持久账本以及完整状态/取消事件仍待实现和联调。
本地 `caller_status_contract` 是消费方 HTTP 契约测试，不是下游生产实现符合性的证明。


### 取消控制 HTTP v1（内部 Port，未生产挂载）

`CallerExecutionCancelPort` 是 BCS application 调用的出站业务 Port，不是 Human
公开接口。`CallerContainerHttpGateway::cancel_router` 由可信 composition 注入 Runtime
resolver、environment、URL guard、timeout，每次只解析一次原目标。不提供捕获固定目标
的取消入口；绑定停用或替换后不回退旧凭据/旧 endpoint。

请求沿用 BCN 2.0 外层，method=`execution.cancel`，每次网络调用有独立 frame id。
`params.contract_version=1`，`params.command` 必须包含：

| 字段 | 类型与约束 | 语义 |
|---|---|---|
| dispatch | 与 status 相同的八字段完整对象 | 原 env/Bot/Provider/runtime/session/run/attempt/dispatch；attempt > 0 |
| cancel_request_id | 非空字符串 | 已登记取消操作的稳定幂等键，不由 transport 生成 |
| control_revision | u64 > 0 | 已提交的取消控制版本；不能用当前 Lease epoch 代替 |
| reason | user_requested / scope_revoked | 封闭原因码，不接受任意文本或身份材料 |
| not_after_ms | u64 > 0 | 原执行截止时间；已过期仍允许发送取消，不延长身份期限 |

HTTP 2xx 回执必须包含 `type=res/id/ok=true/contract_version=1/command/cancel_state/
execution_state/last_event_seq`。实际 command 逐字段回显且完全匹配；不能从请求补值。
顶层、command、dispatch 缺字段/未知字段、错误类型、非法枚举/版本全部拒绝。

- cancel_state：requested / fenced / stopped / unconfirmed。
- execution_state：accepted / queued / running / terminal / not_found / unknown。
- last_event_seq：u64，允许 0（尚无执行事件）；消费者另行检查 attempt 内的单调性。
- requested/fenced 可以伴随 running，不能推断工具已停止。stopped 必须伴随
  terminal 或 not_found；not_found 本身不表示 fenced/stopped，不允许据此重试执行。
- `stopped + not_found` 是 Gateway 对从未启动且已封锁执行的明确声明；单靠本地
  解析器不能证明持久 tombstone。终态可能早于取消，不代表撤销副作用或取消成功。
- 错误/超时返回固定脱敏错误，含义是取消结果可能未知；不自动重试、不重签、不 ensure。
  重复显式调用保留 cancel_request_id/command，但使用新的网络 frame id。
- 只用 BCS 服务认证，不含 Human token/ref/proof。沿用总 deadline、64 KiB 上限、
  禁止重定向/代理/重试以及 URL/DNS guard。身份过期不阻止取消。

### 取消应用消费者与原子回执（内部已实现，未生产挂载）

```text
可信取消入口 → Managed：事务提交稳定 cancel control + Cancelling
内部消费者 → Managed：按 delivery_id/current state_version 读取已提交意图
内部消费者 → Gateway：execution.cancel（原 command，一次限时调用）
Gateway → 内部消费者：完整 command echo + 状态 + last_event_seq
内部消费者 → Managed：apply_caller_cancel_receipt(expected_state_version)
  → 锁内重读，校验原 run/attempt/dispatch/control 及序列
  → 同一事务：receipt + 生命周期 + bound context；提交后沿用既有通知
```

`CallerDeliveryCancellationService` 只接受已提交取消意图的 Caller Group Send，且状态为
Cancelling/CancelUnknown。网络期间不持有 DB 事务或 managed 锁。不恢复身份、不 ensure、
不重发执行、不创建新 attempt。超时/错误保留原状态、意图和占用 lane；上层显式重试需读取
新 state_version，但重用同一 cancel command。消费者不自带扫描、调度或自动重试。

Managed 边界再次完整校验 command 与当前版本。`caller_cancel_control.receipt` 只保存
封闭 cancel/execution 状态和 last_event_seq，不含身份凭据。相同回执不写入；不同回执必须
严格递增。已持久化的 fenced 是该 dispatch 的确认事实，不能被更大 seq 的
requested/unconfirmed 覆盖：Managed 返回 Conflict，原行、控制意图和 lane 均保持不变。
允许 fenced 更新实际 execution_state（包括 unknown），或推进至 stopped；未曾确认 fence
时，unconfirmed 后仍可观察到 requested。Gateway 丢失账本不得将已确认 fence 报成未确认，
应按错误/不可用处理；本地拒绝回退不等于远端 fence 已恢复。此校验复用锁内重读，无新增 SQL。
requested/fenced/unconfirmed 即使伴随 terminal/not_found 也只进入 CancelUnknown；
只有 stopped + terminal/not_found 通过原终态路径进入 Cancelled。回执、终态和绑定上下文
消费共用一个事务，任一写入失败整体回滚。先提交的 Completed 不被迟到取消覆盖。
内部类型化终态入口也使用取消观察的 seq 水位（见下文）；status v1 和完整入站事件流尚未
统一。stopped 不代表副作用回滚。

兼容性：新增 Service API 和默认拒绝的 Managed 方法；transport JSON 新增可选 receipt，
无需 schema migration。旧行可读，但旧严格解码二进制不能读取新字段，启用前必须协调升级。
Port 的闭合状态枚举支持持久化 serde；HTTP wire DTO 仍由 adapter 所有。

成本（SQL store，无 task reply，另计既有目标解析）：变更非终态回执 4 次点读 + 1 个事务
更新 1 行；重复回执 3 次读不写；Gateway 错误 1 次读不写。stopped 有 C 个绑定上下文时为
5+C 次读、一个事务更新 1+C 行；task reply 沿用既有 admission 开销。消费者一次只发一个
外部请求；既有上下文 fanout 未在本步新增上限，部署并发/退避和大规模验证仍待完成。

**内部终态收敛（受信应用 API）：**
`ManagedMessageDeliveryService.handle_caller_terminal` 接收完整原 dispatch 关联、
`outcome=completed|failed|aborted`、`event_seq`、`control_revision` 和当前 state_version。
这是受信应用 API，不是 `/bot/events` endpoint；调用方必须先认证原 Provider。
不接收结果正文/引用、身份或新任务，也不负责回复发布。

- 原 Bot/Provider/runtime/session/run/attempt/dispatch 全部匹配；revision 不能超前于已提交
  的取消 control。允许取消前生成的迟到 final 携带 revision=0，不把它当成取消失效。
- seq 必须大于已保存取消回执的 seq；相同终态回执在新 CAS 版本下无写幂等。
  终态后不同回执（即使序号更大）拒绝，不允许覆盖终态。
- 取消先提交：逻辑状态保持 Cancelled，实际执行结果枚举单独保存为
  `caller_terminal_observation`。完成先提交则保留原终态。仅记录结果事实，不启动 child。
- Caller 已可能发出或已请求取消时，旧的无序号 Completed/Failed/Aborted 返回 Conflict，
  包含重复终态，不必等到取消提交。锁内重读后、上下文查询前拒绝，两次点查零写零通知。
  不能用旧入口绕过回执仲裁或发布 reply；尚未派发的本地失败、显式审计人工 resolution
  保留独立契约；普通非 Caller 路径保持不变。
- 终态观察、生命周期及绑定上下文同事务。无结果 admission 时，SQL 成本为 4+C 次读
  （其中一次读取 C 个 bound context）、一个事务更新 1+C 行；完全重复为三次读、零写。
  沿用既有上下文扇出，未新增上限、网络调用、后台循环或 schema。
- metadata 为可选新字段，旧数据兼容；旧严格 JSON reader 需协调升级。默认 trait 实现拒绝。
  status v1 不增加终态；原认证 HTTP 控制入口已移除（wire 从未上线），序号缺口补齐、结果发布和生产 Caller 派发接线仍待实施。

**内部 status 终态查询恢复（生产关闭）：**
`CallerExecutionStatusPort` 可返回 `Terminal { outcome, event_seq, control_revision }`，
可信实现必须提供远端实际快照和完整原 dispatch 回显，不能从 ACK、超时或本地取消推断。
恢复服务使用网络查询前读取的 state_version，调用与回调相同的
`handle_caller_terminal`；错关联、超前 control、旧 seq、查询期间取消或回调提交
均按既有 CAS 拒绝，不覆盖新状态。取消先提交仍保持逻辑 Cancelled，实际 outcome 独立记录。
存储失败返回封闭错误，无成功通知或 lane 释放；不内部重试、不重建身份或新 attempt。
仅恢复已有 Unknown/CancelUnknown 候选，不扫描 Running；配置了取消消费者时仍优先重放
已提交的取消 control，不新增 status fallback。终态移出后续候选页；`reconciled` 仍是
观察成功计数，不等于结果已发布。快照收敛不是 delta/event 缺口补齐。
HTTP status v1 wire 保持不变且拒绝 terminal；显式选择的 v2 adapter 已支持该快照（见下节），
生产能力选择、Provider 验收与装配尚未完成。
共享 `CallerTerminalOutcome` 移至 Port，旧 application 路径保留 re-export；枚举新增变体
要求穷尽匹配的 Rust 消费方同步编译。无 schema 变更、结果正文/引用或 child admission。
查询每次额外一次候选点查、一次有界远端调用，随后复用 Managed 终态事务；正常路径
合计 5+C 次读、一个事务更新 1+C 行（C 为绑定上下文数，不含目标解析开销）。
错回显/远端失败仅候选点查、零写。页上限仍为 32、外部查询串行，不跨网络持锁；
未增加上下文扇出上限，不宣称负载或真实 MySQL 验收。

**生产前置仍未完成：**Gateway 持久 tombstone/fence、Runtime 实际停止、能力协商、
完整 event_seq/终态事件仲裁、认证调度与 owning dispatcher 装配。不能仅因配置 URL 就启用。
既有 resolver 缓存与 lookup-to-send 撤销竞态仍存在，不声称权威实时撤销。
resolver 每调用一次既有目标解析，缓存命中通常无 SQL，冷路径最多四类单行读取
（并发失效可能重复）。本地 Memory/SQLite/HTTP fixture 不代替远端停止、MySQL 或负载验收。

### execution.status v2：类型化终态快照（未生产挂载）

由 composition 显式调用 `CallerContainerHttpGateway::status_router_v2` 选择；既有
`status_router`、固定目标构造和 `with_status_target_resolver` 仍使用 v1。
不根据 Bot 消息推断能力，不自动升级，也不在失败后降级重试。实际 Provider 必须先通过
v2 契约验收；该构造函数不是新的公共 HTTP endpoint，也不启用 Caller 派发。

请求沿用上文 v1 envelope 和八字段 dispatch，仅 `params.contract_version` 改为 `2`。
响应的 `contract_version` 必须为 `2`，`id` 必须回显本次查询 ID，`dispatch` 必须完整回显
原查询关联。终态响应示意：

```json
{
  "type": "res",
  "id": "query-request-id",
  "ok": true,
  "contract_version": 2,
  "dispatch": {
    "environment": "test",
    "target_bot_id": "bot-a",
    "provider_id": "provider-a",
    "provider_bot_ref": "runtime-a",
    "session_id": "session-a",
    "run_id": "run-a",
    "attempt": 1,
    "dispatch_id": "original-managed-request-id"
  },
  "observation": {
    "state": "terminal",
    "outcome": "completed",
    "event_seq": 7,
    "control_revision": 0
  }
}
```

`observation` 为严格封闭对象：

| state | 额外必填字段 | 含义 |
| --- | --- | --- |
| `accepted` | 无 | 仅受理观察，不能证明执行完成 |
| `not_found` | 无 | 未找到，不证明未执行，不允许重放 |
| `unknown` | 无 | 保持不确定状态 |
| `terminal` | `outcome`, `event_seq`, `control_revision` | 原执行的远端终态快照 |

`outcome` 仅为 `completed / failed / aborted`；seq/revision 是无符号 64 位整数，
不补默认值。缺失、null、错误类型、未知字段（包括非终态对象的多余字段）、错版本或
错回显均拒绝；远端解析失败保持原状态和 lane，不重试、不重新签发身份。
Managed 层继续校验已提交 control revision、取消观察 seq 水位和查询前 CAS；取消先提交
时保留逻辑 Cancelled 并单独记录实际 outcome。HTTP 解析成功不等于允许提交终态。

沿用 BCS→Provider 服务认证、URL guard、响应大小限制和脱敏错误；不携带 Human 登录态、
Caller token/proof、结果正文或结果引用。每次一次目标解析及一次 HTTP 请求，解析和发送共享
原超时；无新增 DB/schema/cache，终态持久化复用现有 Managed 事务。此快照仅收敛执行状态，
不补齐事件缺口、不发布结果、不派生 child，也不替代 Runtime 实际停止验收。

### 状态查询的目标重新解析（内部接线，未挂载 dispatcher）

可信 composition 可通过 `with_status_target_resolver(Arc<dyn BotRuntimeConnectionService>)`
注入既有 Runtime 应用服务。每次查询在原 query 范围校验后重新解析一次：

```text
原 managed dispatch 关联
  → target-scoped Gateway 校验环境/Bot/Provider/ref
  → BotRuntimeConnectionService.resolve_delivery_target
  → BotCore 检查绑定、Provider、downlink、服务凭据的可用状态
  → 返回目标必须仍为原 Bot × Provider × runtime ref 的 HTTP Provider
  → 使用此次解析的 endpoint/服务凭据发 execution.status
  → 严格核对真实响应关联
```

解析失败、缺失、返回 WS、Provider/ref 被替换均拒绝；不使用旧快照兜底。
同 Provider/ref 的 endpoint 更新可使用新 URL，但仍经过同一 URL/DNS guard。
状态查询不新增 Human 授权、不重新签发身份，不要求原 Human 登录态仍然有效。
该选项仅影响 status，ensure 的目标和报文不变。未注入时保留已有固定目标适配器契约；
后续生产恢复装配必须注入解析器，不能把固定目标实例当作当前授权证据。

**边界：** 复用现有解析逻辑，不新增分散的授权判断。SQL 的 Provider/binding/credential
当前有 30 秒进程内缓存及本进程写入失效；本实现不是跨实例实时撤销，也不解决解析后到
发送前的竞态。endpoint 更新还需要 Gateway 的持久账本连续性保证。权威复核、撤销
fencing、账本迁移和 owning dispatcher 生产接线仍未完成，生产 Caller gate 保持关闭。

**访问成本：** 每次查询一次单目标解析，缓存命中通常无 SQL；无并发失效的冷路径至多
读取 Bot、binding、Provider、credential 各一行，无数据库写入、roster 扫描或新缓存。
既有缓存的并发失效可重复加载，不能据此宣称严格至多 4 次查询。未新增周期轮询；
生产 dispatcher 仍需限制恢复并发、批次及失败退避。

### Unknown 候选查询（内部仓储，尚未调度）

> **⚠ 历史段落（2026-10-08 已回退轮次的记录，见文首状态注记）：**本节及随后几节
> 描述的 `DeliveryWorkBatch::Uncertain`、`(env,status,delivery_id)` 索引
> （当前 SQLite 032/MySQL 031 为 session_registry，MySQL 034 为 speaking leases，
> 无此索引）、`CallerDeliveryReconciliationService::scan_page`、
> `conformance_caller_reconciliation`/`conformance_delivery_repo` 计数，
> 均在**当前代码中不存在**。保留仅为 wire 目标设计的历史说明。

恢复服务的下一层入口已有 `DeliveryWorkBatch::Uncertain`：按环境读取
Unknown/CancelUnknown，使用独占 delivery ID 游标，每页最多 200 条。查询保留原关联
元数据，不加载 canonical 消息正文，不签发身份、不重发；过期/普通 delivery 也可能
成为候选，必须由 reconciliation 再检查 Caller 标记、原 run/attempt/version。

SQLite 032 / MySQL 031 新增 `(env,status,delivery_id)` 索引。每页两个有序范围查询，
每个最多 200 行、合计最多 400 行，合并后返回最多 200 行；limit=0 不访问 SQL。
失败直接传播，没有逐行补查、写入或缓存。仅新增索引维护成本，未挂载扫描器，
没有新增生产轮询负载；MySQL 实际执行计划和迁移链仍待验证。

本子集仓储回归 `conformance_delivery_repo` 49 项通过，覆盖 Memory/SQLite、11 万条记录
下的分页/索引/环境隔离和错误传播。SQLite 032 使用幂等 DDL，测试覆盖建索引后、
迁移版本记录前中断的重启恢复，以及重复运行保留历史迁移记录。此证据不替代真实
MySQL 执行计划与迁移、大表建索引影响及生产联调验证。
恢复回归 `conformance_caller_reconciliation` 50 项通过。架构门禁仍未通过：
1 pass / 12 skip / 2 fail（DEP 脚本异常、CFG 缺少 rg）；底层配置测试 132 项通过。
未运行完整 workspace、真实 HTTPS/MySQL、Singlebox 或 Gateway/AgentPass 联调。

候选页不是认领或快照。dispatcher 仍需完成有界轮次/并发、退避、游标轮转及 epoch/
shutdown 管理；本接口不能被当作“读取后直接重发”的依据。

### 单页恢复消费（内部 application，生产未挂载）

`CallerDeliveryReconciliationService.scan_page` 输入独占游标与 limit（正数、上限 32），
返回下一游标、短页标识及 scanned/reconciled/skipped/unavailable。普通/畸形候选跳过；
默认有效候选逐条复用原 reconcile，保留原版本重新读取/CAS。`reconciled` 只是成功获取
状态或控制回执，包括 NotFound/Unknown/Fenced，不表示任务完成或实际停止。Gateway 失败计数后继续，不重试；存储失败
停止并返回闭合错误，先前已提交的状态不回滚。游标不持久化、无自动绕回。

每页 2 次索引查询、各 <=32 行；有效 NotFound/Unknown 每条另有 2 次单行读取，
Gateway 失败 1 次；Accepted 沿用原 managed 点读/单行 CAS。页内外部并发 1、最多
32 次，单请求受 status timeout 约束；这不是 DB/锁等待的整页硬时限。
下述内部轮询组件提供频率/退避；调用者仍须挂载 epoch 唯一所有权及 shutdown 取消。
drop 不能撤销已开始的 DB 提交。未新增 wire、身份持久化、配置或生产挂载，Caller gate 不变。

本轮 `conformance_caller_reconciliation` 55 项通过（新增 5 项）。架构门禁重跑仍为
1 pass / 12 skip / 2 fail：DEP 脚本异常变量、CFG 缺少 rg；底层配置测试 132 项通过。
未运行完整 workspace、真实 MySQL/HTTPS、Singlebox 或 Gateway/AgentPass 联调。

#### 已提交取消意图的恢复分支

组合根可通过 `with_cancellation` 显式注入既有取消消费者；默认不注入，保留状态查询行为。
注入后，扫描到 `Cancelling/CancelUnknown` 时只消费已提交的 control，沿用原 dispatch/attempt、
cancel_request_id/revision 和原 deadline。缺少或非法 control 只计 skipped，不回落到状态
查询，不临时生成取消意图。显式装配后通过 `UncertainWithCancellation` 纳入初始
`Cancelling`，不必等待 abort timeout；未装配时仍只扫描 Unknown/CancelUnknown。
身份/执行期限过期不禁止控制查询，但不能因此恢复凭据、续签、ensure 或重发执行。

Requested/Fenced/Unconfirmed 保持占用 lane；仅 Stopped + Terminal/NotFound 经原子 CAS
进入 Cancelled。错 echo/并发版本变更计 skipped；Gateway 不可用计 unavailable，复用既有
轮询退避；DB 错误立即返回，之后可按原控制标识再次消费，不回滚先前已提交的其他行。

SQL status-only 为两次索引查询；装配取消后为三次（增加 cancelling），各最多 32 行，
最多读取 96 行并合并处理 32 条，外部并发为 1。复用原索引，不新增 migration。
每条取消的点读/CAS 和 bound-context 写入成本沿用下文取消消费者契约；Stopped 的上下文
扇出并未新增上限，不能把每页 32 条等同于最多 32 条 SQL。无跨网络事务/锁，无新 schema。
Memory store 的候选实现仍在内存中全量过滤；本地测试不证明 MySQL 或大扇出容量。

验证（2026-10-08）：取消契约 51 项、状态恢复契约 63 项通过（含共享 migration 测试），
新增 3 项覆盖重复扫描 Fenced→Stopped、Gateway 不可用/错 attempt、SQLite 回执写失败后
按相同控制恢复。bootstrap 可选装配见下节；生产依赖选择和远端停止验收
仍待完成；此组件能力不代表生产 Caller 已启用。

#### Bootstrap 显式取消装配（2026-10-08）

`CallerHandoffDependencies.cancellation` 可选提供 resolver、URL guard 和 timeout；
必须同时配置 status recovery，否则装配返回 InvalidConfig。零/非法 timeout 同样拒绝。
只开启 status 不隐式获得取消能力；生产入口仍传入 None，不新增面向用户的配置或 API。

```text
既有 dispatcher master epoch
  → 同一个有界恢复 future
    → Cancelling/CancelUnknown + 已提交 control
      → CallerDeliveryCancellation（共享 Managed、environment、clock）
        → HTTP cancel_router（每次解析原 Bot/Provider/ref）
          → Provider execution.cancel（服务认证，不携带 Human 凭据）
        ← 严格 echo 校验的取消回执
      → 原子 receipt/lifecycle CAS
```

取消使用独立显式 timeout，包含 resolver/HTTP 总截止时间；不创建新 worker/扫描/选举。
follower 不消费，epoch 降级或 shutdown drop 同一个恢复 future；drop 不代表远端停止。
每页仍最多处理 32 条，单并发和退避不变；取消模式增加一个有界索引范围查询。单行
使用原取消消费者的既有点读/CAS，目标解析
沿用现有 Provider 缓存/查询，不能据此宣称即时撤销。首次取消失败保留 Cancelling 和原
control，后续扫描按原标识重试；与普通 timeout/终态提交竞争由现有 state_version CAS 裁决。

真实 SQLite + dispatcher + HTTP peer 测试覆盖 master 消费、follower 静默、原控制/attempt
回显及装配后 binding disabled 拒绝调用；装配测试覆盖无 status 和非法 timeout 拒绝。
现有 status-only 测试保留。远端 durable fence、真实停止、跨副本一致性仍需单独验收。
首次取消接线测试在 startup recovery 后提交意图，确保从初始 Cancelling 消费，而非
只验证启动时自动归并到 CancelUnknown 的恢复。2026-10-08 回归：Bootstrap 18 项、
取消契约 54 项、状态恢复 63 项、仓储契约 52 项，managed/runtime 三组共 32 项通过。
仓储测试包含 110000 行 SQLite 数据的查询次数、索引、环境隔离与分页上限验证；
未运行真实 MySQL、Singlebox 或外部 Runtime 停止验收。


### 恢复轮询生命周期（内部组件，尚未生产挂载）

`caller_reconciliation::run` 以现有 application trait 为依赖；每个 master epoch
由 owner 持有一个 future，降级时 drop，不创建游离 task。shutdown watch 为 true
或 channel 关闭即停止；false 通知不取消当前页。初次和正常页后均等待 5 秒，
每页最多 32 条、逐条查询；短页只重置下一轮游标，不立即绕回、不补发积压 tick。
Gateway 不可用/存储错误按 10/20/40/60 秒退避，成功非空页恢复 5 秒，空页不清除
退避。存储失败保留该页起始游标；已完成的 CAS 不回滚，也不触发 execution 重发。
顶层 Conflict 直接返回 owner。取消本地 future 不撤回已开始的 DB 提交或远端操作。

每实例最多一页/一个状态 IO 在途，无额外 SQL；status-only 每页两次范围读（最多64行），取消模式三次（最多96行），加
原有目标点读/CAS。故障饱和后每分钟最多一页，另加实际处理时间。当前未生产挂载，
不新增配置、持久化凭据或公开协议；原目标授权、Gateway durable receipts 与
取消 fence 仍须补齐，不能据此开启生产 Caller。

验证（2026-10-08）：完整 `conformance_caller_reconciliation` **61 项通过**，
本轮新增 6 项覆盖轮询节奏/绕回/无积压突发、失败退避、空页与恢复、取消和 DB 故障频率。
`git diff --check` 及修改源文件 <=1000 行检查通过。架构门禁仍为 1 pass / 12 skip /
2 fail（DEP 脚本异常变量、CFG 缺 rg；底层配置测试 132 项通过），未绕过检查。
未运行完整 workspace、真实 MySQL/HTTPS、Singlebox 或 Gateway/AgentPass 联调。


**Dispatcher epoch 内部接线（2026-10-08，非生产启用）：** trusted composition 的
`CallerHandoffDependencies.status` 选择 HTTP resolver/URL guard/timeout，由 bootstrap
构造 v1 `status_router`，或显式选择 `HttpV2` 构造 `status_router_v2`
（任意 port 仅限测试）；使用同一个
managed delivery service、environment 和时钟构造真实 reconciliation。既有主节点
完成 takeover policy refresh 并再次确认 leader 后，同时持有 delivery runtime、
policy reconciliation 与 Caller status future；不再生成独立 worker 或新增选举查询。
未配置 status 时无扫描/计时器。降级、选举错误或 shutdown 丢弃当前 future；重新当选
重置游标和退避，首次仍等待 5 秒。单次 Gateway 查询上限 30 秒，页大小与退避沿用上文。
轮询意外退出使 owning scheduler 停止并关闭 admission，不自动重启或降级 Owner。
本地取消不撤回已开始的 DB 提交/远端操作，不构成跨副本 fence。

生产 `wire_with_leader` 仍传入 `None`，未选择 HTTP status adapter 或 Caller 身份依赖。
此接线没有新增 wire/config/schema；唯一新增依赖是内部可选 status port。SQL 成本沿用
status-only 每页两次有界范围读，取消模式三次，另加候选点读/CAS；不增加每次选举的 DB 请求。Gateway 持久回执、
取消 fence、原目标权威授权/撤销及生产接线仍未完成。


验证：`cargo test -p bcs --lib message_delivery_wiring` **13 项通过**，其中新增 2 项
使用真实 SQLite managed lifecycle/dispatcher，仅替换外部 Gateway 和选举，覆盖 follower
无查询、master 查询、降级/停机取消等待、再当选初始延迟与旧查询释放，以及 accepted
提交同一 run/attempt、非终态且不重发。恢复契约测试
`cargo test -p bcs-message-flow --test conformance_caller_reconciliation` **61 项通过**。
`git diff --check` 与本轮源文件行数检查通过（138/378/117/517 行）。架构门禁为
1 pass / 12 skip / 2 fail：DEP 脚本异常变量、CFG 静态检查缺 rg；底层配置测试
132 项通过。未修改或绕过门禁；未运行完整 workspace、Singlebox、真实 MySQL/HTTPS
或外部 Gateway/AgentPass 联调。

### 多目标恢复消费方接线（内部组合，尚未生产挂载）

一个恢复服务需要处理多个 Bot 的原始派发。使用
`CallerContainerHttpGateway::status_router(resolver, environment, url_guard, timeout)`
获得既有 `Arc<dyn CallerExecutionStatusPort>`，注入 `CallerDeliveryReconciliation`。
无须预先捕获某个 Bot 的 endpoint/服务凭据，也不维护无界的每 Bot adapter 字典。
固定目标 `new` 与 `with_status_target_resolver` 继续兼容；ensure 语义不变。
内部 dispatcher 装配允许 `CallerStatusDependencies::Http` 或显式 `HttpV2`
（均为 resolver、URL guard、timeout），由 bootstrap 分别构造 `status_router` / `status_router_v2`。
既有 Http 保持 v1；HttpV2 只用于已确认支持 v2 的恢复目标集合，不自动探测、协商或降级。
混合 Provider 版本的能力选择仍须生产接线解决，不能通过尝试失败后改版本实现。
任意 status port 注入仅限测试。非法配置在 scheduler
启动前拒绝，不延迟到首轮扫描。每次查询仍依赖现有 resolver 的缓存/失效策略，不能声称
跨进程即时撤销。生产 `wire_with_leader` 仍传 `None`，此修改不会开放 Caller。

```text
内部调用方提交 delivery_id + expected_state_version
  → CallerDeliveryReconciliation 读取原 Unknown/CancelUnknown 派发
  → 从已提交元数据取得原 Bot/Provider/ref/session/run/attempt/request
  → 共享 status_router：校验环境/关联 → 一次现有 Runtime 目标解析
  → target-scoped validator：必须仍为原 Bot/Provider/runtime ref
  → 原 deadline 下 POST execution.status（只用服务认证）
  → 校验实际 HTTP 回执（不从请求填补关联字段）
  → accepted：原 state_version/request CAS；不代表完成，不清除取消
  → not_found/unknown：复核快照，不写入、不释放 lane、不重发
  → v2 terminal：复用 Managed 终态 CAS/取消水位 → 同事务提交 → 释放 lane
```

v2 终态仍使用同一 elected dispatcher 生命周期，不新增调度器。status v2 与 cancel v1
独立选择；配置取消消费者时继续优先处理已提交 cancel control，不把终态查询当取消回退。
真实 SQLite/dispatcher/HTTP consumer 覆盖三类终态、绑定禁用、v2 受理后 v1 取消及非法装配。
终态快照不发布正文/结果引用，不创建 child，也不意味着完整事件序列恢复已完成。

总 deadline 在解析前建立，传给 HTTP 层时不重置；适配器不做第二次解析。
每请求的解析访问成本与上一节相同，另加既有 reconciliation 点读/CAS；不增加扫描、
轮询、身份签发、数据库字段或生产依赖。测试新增的 message-flow/store 为 dev 依赖。
内部组合验证不等于生产 owning dispatcher 已挂载：有界扫描/退避及可选主节点 epoch
接线已见上文；权威撤销、Gateway 持久账本、终态回执和取消 fencing 仍需完成，
生产 adapter 尚未选择，Caller gate 仍关闭。

HTTP 装配及初始化修复验证（2026-10-08）：此前 status 契约 5 pass / 13 fail，
已定位到 HTTP client 不必要的原生证书加载约 15 秒（URL 校验约 120µs、配额等待约 4µs）。
仅跳过已校验 HTTP 目标的 TLS 根加载后，无证书环境覆盖的 status **18/18**、ensure
**11/11**、初始化故障 **2/2** 通过。命令：

```sh
cargo test -p bcs-provider-http --test caller_status_contract --test caller_container_contract --test caller_client_initialization_contract
cargo test -p bcs --lib message_delivery_wiring
```

后者 **15/15** 通过；HTTP fixture 恢复 2 秒 adapter 预算/9 秒 scheduler 等待（含首次
5 秒延迟），不再依赖临时 30 秒预算。accepted 保持原 run/attempt 且非终态；装配后
binding 停用不发 HTTP。生产 gate 不打开；真实 HTTPS、完整 workspace、Singlebox、
MySQL 或外部 Gateway/AgentPass 联调未运行。架构门禁结果见实施计划。

### Client 初始化与请求截止时间

ensure/status 的 `reqwest::ClientBuilder::build` 已迁入 Tokio blocking pool，避免同步
系统证书加载阻塞异步请求线程。进程级共享 semaphore 限制最多 4 个排队/运行中的
Caller client 初始化任务；其余请求异步等待并受各自总截止时间约束。

```text
原请求 deadline
  → 目标解析 / URL 与 DNS guard
  → 等待初始化配额 → blocking pool 构建 client
  → 再核对原 deadline → HTTP 请求与响应体读取
超时 / 请求被 drop
  → 中止尚未开始的初始化任务
  → 已开始的 native 初始化可继续，但不持有请求体或服务/Human 凭据，不能发送请求
```

同一 deadline 覆盖解析、配额等待、初始化及 HTTP I/O；初始化完成后再次检查 deadline，
不因为超时与完成同时就绪而补发请求。配额由阻塞任务持有直到结束，避免超时重入累积
无限个初始化任务。经 guard 校验的 HTTP 单请求 client 不加载无用的 TLS 信任根，
使用结构化 URL 的 scheme 判断，不放宽 URL 策略，不将 HTTPS 降级。HTTPS 的原生
信任根、证书/主机名校验保持不变；请求级 DNS pinning、禁止重定向/重试亦保持不变。
不增加客户端缓存、配置项、DB 访问或生产依赖。

**限制：** 已开始的 native 调用不能强行终止。若 4 个初始化全部卡住，新请求会等待
到自己的 deadline 后失败；这不是 native 操作或 Tokio shutdown 的硬时限保证。
生产前仍需真实 HTTPS/证书链、负载及关闭行为验收，不能把阻塞任务隔离当作这些验收完成。

早期本地 HTTP fixture 曾在测试进程设置 `SSL_CERT_FILE=/dev/null` 绕过本机
证书加载延迟；这只属于历史测试证据，不是生产配置。2026-10-08 定位到 URL 校验
约 120µs、配额等待约 4µs，而 client 构建约 15 秒；HTTP-only 跳过无用信任根加载
后，当前 Caller 专项回归不再需要该环境变量。HTTPS 冷初始化的时延仍待真实验收。
新增回归在真实 current-thread runtime 中占满 blocking pool，验证 ensure/status 按期
失败、释放资源后不延迟发送；status 同时覆盖并发等待及资源恢复后新的显式查询成功。

历史验证命令（设置了证书环境覆盖，仅本地 HTTP fixture）：

```sh
SSL_CERT_FILE=/dev/null cargo test -p bcs-provider-http
```

2026-10-08 早期带环境覆盖回归 99 passed、0 failed、0 ignored：单元 38、初始化故障契约 2、
ensure 契约 11、status 契约 18、普通下行契约 30。初始化故障在独立测试进程运行，
不影响普通短超时用例。status 覆盖服务认证、输入范围、真实关联回显、目标重新解析、
版本/响应结构、错误脱敏与响应上限、超时不重试。此命令不验证真实 TLS 或下游 Gateway 实现。

status 中新增 6 项真实恢复组合测试，覆盖同一 consumer 多 Bot 恢复、not_found/unknown
保持原记录与 lane、CancelUnknown 不被 accepted 覆盖、错误回执不推进状态，以及目标替换/
禁用拒绝。另运行既有 `conformance_caller_reconciliation`，49 项通过（业务 13、migration 36）。
架构门禁结果为 1 pass / 12 skip / 2 fail：DEP 检查脚本第 36 行异常变量触发
unbound variable，CFG 静态检查缺少 rg；132 项配置测试通过。未修改或绕过门禁。
本轮未运行完整 workspace、MySQL、Singlebox、真实 HTTPS 或 Gateway/AgentPass 联调。
多目标 adapter 尚未接入 owning dispatcher；已有内部 unknown 单页扫描及退避轮询组件，
可选 status port 已接入 dispatcher epoch 生命周期，但生产仍未选择该 adapter，
Caller 生产开关仍关闭。


### Provider 认证终态入口（已移除）

`POST /bot/events/caller-terminal` HTTP v1 入口及其配套的 Provider 认证受理应用服务
（`CallerTerminalIngestService`）已经移除。判据与 a2a_child 表族下线相同：该 wire
契约随全部分支提交停留在本地、从未推送上线，全部调用方为零（含 Provider demo、
openclaw-channel-bcn 与 submodules/OpenClawEnterprise），"保留旧契约兼容"的保留对象
是空集。终态收敛的外部通道回归既有 `/bot/events`；上节"内部终态收敛"的受信应用
API 语义保持原样可用。

保留面（内部控制面设计词汇，不是对外契约）：`CallerTerminalObservation` /
`CallerTerminalReceipt` / `CallerTerminalOutcome` 类型，
`ManagedMessageDeliveryService.handle_caller_terminal` 的 Managed CAS 终态仲裁
（语义投影 `caller_terminal_observation`；取消先提交时实际 outcome 独立保存）。
将来若确实需要强证据终态回执的对外入口，应在此语义之上重新设计并重新评审认证面，
而不是复活 v1 wire。

## 本次修复的兼容范围

- 内部 outbound Service API `SendCallerExecutionCommand` 新增 `group_id: Option<String>`；
  root/delegate 生产者及测试构造点同步更新。外部 Rust 消费者重新编译时须补该字段。
  它来自既有派发事实，不是新的 Caller 授权或生命周期关联。
- Provider 继续使用既有 webhook 和 envelope；群请求补齐 `bcn_group_id`，
  有历史时补齐 `extensions.execution.history`，H2A 无历史时省略。
- observe-only inject 继续使用既有 `message.text` 和普通 `ProviderAckResponse`。
  仅 HTTP 2xx 不再代表成功：Provider 必须返回显式 `ok: true`；
  空正文或不合规 ACK 将停止本次同步，不能误记为成功。
- 不增加配置项、表或迁移，不改变普通 send 的派发协议。真实 Provider/Runtime
  必须验证 ACK、消息去重和执行快照隔离；本地契约测试不能替代这些验收。
