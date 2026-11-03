# Caller P0 契约冻结与数据映射

- 冻结日期：2026-10-09（P0 批次）。
- 依据：`src/bcs/specs/2026-09-16-caller-identity-session-container/spec.md`（下称 Spec）与同目录 `plan.md` P0。
- 定位：**可执行契约与数据映射清单**。本文完成 P0.2–P0.6 的契约定义与测试映射；它不表示任何 Caller 运行时能力已接通，P0 不开放 Caller 执行。
- 证据规则：实施状态与测试证据只由 `plan.md` 的执行记录维护；历史文档（caller-container-gateway.md、caller-identity-configuration.md）是目标契约/历史材料，已在各自文首标注（2026-10-03 source-mapping 审计文档已于 2026-10-23 合并删除，见 plan.md §15.104）。

## 1. 契约分类：Service API 与 Plugin API

按 Spec §10、plan P0.2 分类。原则：HTTP/WS/Core 不选择具体实现；Plugin API 是 BCS 调用外部身份/Gateway 能力的出站契约。

### 1.1 Service API（bcs-service-api：application / core / port）

| 契约 | 层 | 方向 | 状态（P0 后） |
|---|---|---|---|
| 受信消息准入（`AuthenticatedHumanCallerIngress` 与同次认证配对） | application | 入站 | 类型已冻结；用例编排属 P2-A |
| Bot caller 配置同步/读回（Core + Repo：`get/sync_caller_configuration` 语义） | core + port::repo | 入站（受信控制面） | **Core/Repo 契约已冻结（2026-10-09，P1.1 批）**：`BotControlPlaneCoreService`/`BotControlPlaneRepoPort` 新增方法 + 默认 fail-closed（4 项 seam 测试）；SQL 实现、受认证 application 与 HTTP 子资源属下一批 |
| 一次性身份 handoff（bounded 内存 slot，同任期消费） | application | 进程内 | 边界契约见 §7.5.2；实现属 P2-A |
| Speaking Lease 获取/抢占/释放/提交续期 | application + port::repo | 入站（仅 Caller 群） | 契约冻结于 §10.13.8；实现属 P3 |
| 控制回执（受理 ACK、status 查询恢复、取消意图与回执、终态） | application + core（Managed CAS） | 入站/内部 | 与 Managed Delivery 复用；映射见 §3 |
| `CallerExecutionService`（root/child run、parent 校验） | core + port::repo | 内部 | trait 草案见 §10.14；~~落地随 P1/P2~~（2026-10-20 勘误：该独立 run 表路线被 P5 reuse-first 决策取代，`bcs_execution_runs` 与本 trait 均未落地，lineage 为 runtime-only `RootLineageIndex`；本行仅存历史） |

### 1.2 Plugin API / outbound port

| 契约 | 层 | 说明 | 状态 |
|---|---|---|---|
| `CallerIdentityPlugin`（`establish_subject` / `issue_execution` / `verify_execution`） | port | 默认 JWT、internal AgentPass 共用；实现注由 composition root | **已落地（2026-10-09，P1.5）**：`bcs-service-api::port::caller_identity` trait + `crates/plugins/bcs-caller-identity-jwt` 默认实现（17 项 conformance 通过）；internal AgentPass 适配未联调 |
| `CallerContainerGatewayPort`（ensure / dispatch_send / dispatch_inject / status / cancel） | port | 传输无关出站契约（Spec §10.14）；wire 映射以 caller-container-gateway.md 为准 | **已落地，但方法清单修正（2026-10-20）**：实际 trait 仅 `ensure_container`（`port::caller_container`）；send/inject 分别为 `CallerExecutionSendPort`（`port::caller_send`）与 `ObserveInjectPort`，status/cancel 为进程内 `CallerExecutionControlService`（未挂 HTTP route）。本行原五方法单一端口清单未按此拆分落地 |
| Provider wire（`caller.container.ensure`、chat.send caller 扩展、inject observe-only、execution.status/cancel v1/v2） | bcs-protocol（wire） | 已在 caller-container-gateway.md 冻结目标 wire；`/bot/events/caller-terminal` 已移除（从未上线，无接入方）；DTO 落地随对应阶段 | P0 不新增 DTO |

## 2. 类型摆放映射（P0 已落地部分）

| 类型 | crate：模块 | 持久化 | 约束 |
|---|---|---|---|
| `CallerMinimumIsolation` / `CallerIdentityConfig` / `CallerIdentitySnapshot` | `bcs_domain::caller_identity` | 是（复用 `bcs_bots.bot_info.$.caller`，随 P1） | deny_unknown_fields；`enabled` 必填；Default 仅表示从未配置 |
| `CallerConfigRevisionDecision` / `CallerConfigRevisionConflict` | 同上 | 否（纯比较） | Apply/Unchanged/Stale 矩阵见 doc & 测试 |
| `ProviderCallerCapabilities` / `CallerTokenVisibility` | 同上 | 是（`bcs_providers.config.$.caller_capabilities`，随 P1） | 四字段必填，无默认支持值 |
| `BotCallerEffectiveStatus` / `BotCallerBlockedReason` | 同上 | 否（计算视图） | `merge()` 固化 §10.7.3 判定序：enabled → Provider supports_caller → BCS 执行链 ready |
| `ExecutionParentKind` / `ExecutionSourceActorKind` / `ExecutionRunMode` / `ExecutionRunStatus` / `ExecutionRunRecord` | 同上 | 是（`bcs_execution_runs`，DDL 目标见 §4） | 身份字段结构性缺席；`deny_unknown_fields` 有意不加（持久行跨版本读取） |
| `StandardDelegationPolicy`（depth=2、turn child=16） | 同上 | — | B10 冻结默认值，非上限 |
| `AuthenticatedHumanCallerIngress` / `HumanCallerAuthSource` / `HumanCallerIngressError` | `bcs_service_api::application::caller_identity` | **否** | 不派生 serde、无 Clone；Debug 脱敏 staff_no；有效期只可缩短 |
| `CallerSubjectRef` / `CallerSubjectKind` / `CallerSubjectRefType` / `CallerSubjectRefError` | 同上 | **否** | 值仅经 `expose_ref()` 暴露；Debug 脱敏 |
| `CallerExecutionPurpose` | 同上 | **否** | SubjectEstablishment / Execution / DelegationOnly 三值封闭；DelegationOnly 永不满足 Execution 检查 |

与既有类型的关系：`application::principal::CallerContext`（访问主体枚举，Public/Human/Bot/Integration/Admin）保持不变、不承载执行身份；`RequestAuthHeaders` 语义不变。下文 §7 的 wire 字段 `caller_context` 使用独立承载，不复用该枚举名。

## 3. Managed Delivery 字段复用与 CAS 边界（P0.4）

依据现源 `crates/contracts/bcs-domain/src/message_delivery.rs:77-118`（`PersistedMessageDelivery`）与 `crates/services/bcs-message-flow/src/managed_delivery.rs`（`StartSend` 分支 :326-336 生成 `attempt_no+1` 与 `request_id`）。重复一遍 P0 的结论：**不建第二套 Caller 状态真值**，Caller 派发复用 Managed 的 run/attempt/delivery 事实。

| Caller 概念（Spec §10.3.1） | Managed 复用 | 边界 |
|---|---|---|
| message_turn | `source_message_id`（canonical message id，不是 client_msg_id） | 多目标共享、child 沿用 |
| run / attempt / dispatch | `run_id` / `attempt_no` / `request_id`（StartSend 后才存在） | 队列阶段不得猜 attempt |
| 网络幂等重投 | 同 run/attempt/request 重投；新增 `request_digest`、`not_after_ms`（规范摘要见 caller-container-gateway.md） | 重投不新建 attempt、不续期 |
| 取消控制 | 既有 `cancel_*` / `abort_request_id` / `state_version` CAS + 新增 `caller_dispatch_control`（`{control_revision, cancel_request_id, reason, not_after_ms, receipt{cancel_state,execution_state,last_event_seq}}`） | 与生命周期/绑定上下文同事务 |
| 受理/终态 | `submitted_at_ms` / `accepted_at_ms` 新增分量 + `caller_terminal_observation`（outcome/event_seq/control_revision） | Accepted≠完成；终态独立 observation |
| Caller 必需标记 | `semantic_projection_json.caller_required=true`（含 caller_config_revision 可选） | 与 admission 同事务；普通路径遇异常标记一律拒绝（false/null 同样拒绝，不得删标记降级） |
| 禁止承载的材料 | `transport_context_json`（注释已声明 no tokens/bypass headers）、`semantic_projection_json`、`context_selection_json`、`original_request`（a2a_chat 持久化审计 frame 路径）、run_context 缓存 | 全部禁止 IAM/subject/proof/容器映射；看 §5 出口清单 |

CAS 事务边界（P0 固化，供 P2 实现复核）：

1. 准入（queued）→ `admit`：canonical message + queued delivery + `caller_required` 标记同事务；一次性内存 handoff 在普通 admission 前预留 slot。
2. StartSend：既有 CAS 获得 attempt+request；一次性领取 handoff 身份。
3. 网络前：`commit_caller_dispatch`（摘要/期限/revision）CAS；仅接受未取消 `Dispatching` 行；发送前重算摘要并再核 attempt/version。
4. 网络 after：ACK/取消/终态按读到的原版本 CAS 收敛；冲突传播，不重读覆盖。
5. 任意网络 I/O 期间不持有 DB 事务或 Managed 锁。

### 3.1 Workflow dispatch checkpoint 单独映射（P0.4）

Workflow 现有持久派发事实与 Group 路径不同源，P0 单独列出、不预设共用持久模型：

- 节点派发 request ID：`smnode-{run_id}-{node_id}-{attempt}`（`bcs-collaboration-runtime/src/runtime_dispatch.rs:20`、`runtime.rs:830`）；store 侧 `bcs-collaboration-store/src/dispatch.rs:12` 以同式比对 payload 一致性。
- 交付关联：runtime 以 `register_delivery_alias(delivery_request_id, bot_delivery_run_id)` 把编排节点派发与 Managed delivery run 关联（`runtime.rs:4090`），`bot_delivery_run_id` 进入节点状态（`runtime.rs:4087/2606`）。
- Caller 语义映射（P5 落地对照）：`parent_kind=orchestration` 的身份 parent 是**活动编排 attempt**；`smnode-*` request ID 与数据前驱（predecessor node run）都不是身份 parent。凭据不得写入 `StateMachineDispatchPayload.request`；持久恢复不得从已保存 frame 重放凭据或从已完成节点还原身份（Spec §8.5）。
- Workflow 恢复基线测试：`bcs-collaboration-runtime` 的 `runtime_progression` / `fixed_loop_snapshot`，`bcs-collaboration-store` 的 `memory_store` / `mysql_store`（见 §6）。

## 4. 新增存储目标（清单，不写迁移）

- `bcs_execution_runs`：~~字段见 Spec §10.3 DDL；索引 `(env,run_id)` 唯一、`(env,root_run_id,created_at_ms)`、`(env,parent_run_id)`、`(env,group_id,session_id,message_turn_id)`、`(env,target_bot_id,status)`、`(env,status,expires_at_ms)`。建表与迁移属于 P2+，P0 仅冻结目标。~~（2026-10-20 勘误：a2a_child/run 表族已整体下线（7ea603b8c），本目标作废；lineage 以 runtime-only `RootLineageIndex` 承载。）
- ~~不确定索引（`_uncertain` 状态页查询）按 caller-container-gateway.md。~~（2026-10-20 勘误：该索引与 `DeliveryWorkBatch::Uncertain` 均不存在，gateway.md 对应段落是已回退轮次的历史记录。）
- `bcs_message_deliveries`（或等价交付表）：随 P2 增列/JSON 子对象承载 §3 中 `request_digest`、`not_after_ms`、`caller_dispatch_control`、`caller_terminal_observation`；不确定索引（`_uncertain` 状态页查询）按 caller-container-gateway.md。
- `bcs_bots.bot_info.$.caller` / `bcs_providers.config.$.caller_capabilities`：随 P1。
- Speaking Lease 持久化（holder/lease_id/epoch/expires_at_ms，业务 Actor ID，非身份）：随 P3，复用现有表或新表由 P3 实施时决定。

## 5. 身份流持久化出口清单（必须保护）

每一出口在对应阶段实现时必须配 canary/负向测试（plan P6 收口）：

| 出口 | 保护要求 |
|---|---|
| `bcs_chat_runs.original_request`（a2a_chat 序列化 frame） | caller 材料在写入前剥离；`serde(skip)` 不够，需显式剥离测试 |
| `run_context_redis`（run context 缓存序列化） | 新字段不得无差别加入可写 Redis 结构 |
| `semantic_projection_json` / `transport_context_json` / `context_selection_json` | 禁 IAM、x-one-id、execution token、可兑换引用、observe_target、binding |
| Provider `extensions` 载体（普通 chat.send/inject wire） | 受保护信封只走独立 runtime-only 出口；普通正文日志路径不得记录 |
| 日志/metrics | 固定脱敏错误码；Debug 已在类型层脱敏（P0 已测） |
| 队列快照 / 回执投影 | 只含非敏感关联（digest、状态、seq） |

## 6. 兼容用例回归基线（P0.5）

| 兼容用例 | 既有测试 target（真实存在） | P0 结果 |
|---|---|---|
| 未开启 Caller 的 HTTP 入口行为不变 | `bcs-http`：`group_messages_contract`、`session_create_contract`、`bot_chat_contract`、`route_contract`、`route_boundary_contract` 等全量 | 随 `cargo test -p bcs-http` 回归（P2 接线前跑全量） |
| 未开启 Caller 的 WS 入口 | `bcs-ws`：`group_session_ws`、`interaction_end_to_end`、`web_frame_compat`、`frame_compat` | 同上 |
| 未开启 Caller 的 Provider 下行 | `bcs-provider-http::provider_transport_contract`；`bcs-message-flow`：`queued_provider_headers`、`delivery_runtime`、`managed_delivery` | 同上 |
| 普通群并发/路由不受 Lease 影响 | `bcs-message-flow`：`conformance_queued_group`、`conformance_delivery_scheduling` | 同上 |
| DM 固定目标（正文 @ 不扩目标） | `bcs-message-flow::contract_message_flow`（DM 路由）；`contract_human_notify_context` | 同上 |
| 附件/多内容完整投影 | `bcs-message-flow`：`bounded_context`、`conformance_queued_group` / `_task` / `_system`（含 attachment 路径） | 同上 |
| Direct A2A 队列/审计 | `bcs-message-flow`：`conformance_direct_a2a`、`contract_a2a_chat` | 同上 |
| 协作 start/end 无 name/toolName 兼容 | `bcs-message-flow`：`contract_coordination_tool_identity`、`contract_bot_event` | 同上 |
| Workflow 编排 dispatch checkpoint 恢复 | `bcs-collaboration-runtime`：`runtime_progression`、`fixed_loop_snapshot`；`bcs-collaboration-store`：`memory_store`/`mysql_store` | 同上 |

P0 批次实测基线：`cargo test -p bcs-domain -p bcs-protocol`（通过；新增 caller_identity_contract 9 项）、`cargo test -p bcs-service-api`（199 通过 0 失败）、`cargo check -p bcs-domain -p bcs-protocol -p bcs-service-api -p bcs-message-flow -p bcs-http -p bcs-provider-http -p bcs-bot -p bcs-bot-store`（通过、无新增告警）。全 workspace 由既有 `bcs-cli` 编译错误阻塞（plan §11 已记录），不由 Caller 批次处理。

## 7. Consumer conformance 测试映射（P0.6）

P0 阶段已存在并运行的 consumer 测试：

| 契约 | 测试 | 断言要点 |
|---|---|---|
| Bot caller 配置/能力/状态类型 | `bcs-domain/tests/caller_identity_contract.rs`（9 项，通过） | 严格 serde、revision 矩阵、§10.7.3 判定序、run wire 值与派生矩阵、持久 JSON 无身份字段 |
| Runtime-only 身份信封 | `bcs-service-api/tests/caller_runtime_identity_contract.rs`（6 项，通过） | Debug 不泄漏 staff_no/subject ref、有效期只缩不涨、非法构造拒绝、purpose 封闭 |

后续阶段必须补的 consumer 测试（P0 只列清单，未实现处不算完成）：

- P1 配置：真实 application→Core→repo consumer 断言配置写读、权限、Conflict/写失败不报 applied。
- P1.5 已补（2026-10-09）：`bcs-caller-identity-jwt` conformance 以真实 port/插件对象断言 establish/issue/verify 确实被调用并按 §10.13.7 分类错误（篡改、算法、密钥轮换、秒/毫秒、purpose/binding 隔离等 17 项）。
- P2 H2A：真实 ingress→admission→StartSend→protected sender 的全链 conformance；Gateway ensure 被调、摘要提交、Unknown/取消/终态 CAS；DB/日志无身份。
- P3 Lease：两 Human 并发提交/抢占/重启；普通群零 lease 查询。
- P4/P5：group observe/history_ready、child 预算/幂等与 Workflow parent 校验。

## 8. 配置 schema 草案（P1 落地对照）

```text
caller_identity:                      # 2026-10-20 勘误：以下为落地键,与旧草案不同
  plugin = "jwt" | "agentpass";        # agentpass 在公开装配 fail-closed 拒启
  [caller_identity.jwt]
  issuer = "bcs-caller";
  signing_kid = "<kid,须命中 public_keys>";
  signing_key_pem = "<Ed25519 PKCS#8>";
  public_keys = [{ kid, public_pem }];
  pseudonym_key_hex = "<>=16 bytes hex>";
  max_ttl_seconds = 300
```

~~allowed_algorithms / signing_key_ref~~ 未落地（`deny_unknown_fields` 会拒绝照抄旧草案的配置）。

约束：密钥引用不写死；unknown plugin、密钥缺失、消费方缺验证能力 → Caller 能力不就绪（不阻止普通路径）；不能读取 Bot 消息切换插件。

## 9. 本批未做（不得视为完成）

- bcs-protocol wire DTO、所有 trait/port 定义与实现（CallerIdentityPlugin、CallerContainerGatewayPort、Core/Repo 契约）——P1 起。
- 迁移 SQL、真实 MySQL/Redis、Singlebox、Gateway/AgentPass 联调。
- HTTP/WS 入口接线与 Caller 准入（P2 起）。