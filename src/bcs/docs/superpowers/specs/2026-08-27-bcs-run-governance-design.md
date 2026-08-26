# BCS Direct Chat Run Governance — 设计规格

日期:2026-08-27
关联 issue:#1546(eat(bcs): govern process-local run and interaction state)
范围:issue 表中两个并列 in-scope run 项的**合并边界**——① Direct Chat 异步 run 的 `ChatRunStore`;② Provider 下行 run context 的 `MemoryBotRunContextStore`(`BotRunContextPort`)。

## 1. 问题

两条 run 状态目前纯进程序内:
- `ChatRunStore`(`services/bcs-message-flow/src/a2a_chat/run_store.rs`):`RwLock<HashMap<run_id, Arc<Slot>>>`,是 `POST /bots/{id}/chat-async` → `GET /chat/runs/{run_id}` 的唯一结果记录(状态机/累积正文/version/TTL/归属)。
- `MemoryBotRunContextStore`(`services/bcs-message-flow/src/run_context.rs`):`{runs, processing, provider_transports}` 三张进程序内表,是 Provider `/bot/events` 回调的路由/鉴权/终态认领/transport 绑定登记。

失效:重启即丢 run 结果(下游 Bot 可能仍在跑)、跨副本 `run_not_found`、终态/取消跨副本重复或丢失、回调认领失效。

## 2. 边界裁定:两独立治理 store,不合并

`BotRunContext` 并非直聊专用——它还服务 group 聊天(`group_flow.rs:203` `put_context`)、WS dispatcher 的 run alias(`dispatcher.rs:1297`)、HTTP provider SSE/callback。它是**横切直聊/群聊/WS 的 Provider 下行 run 通用登记簿**,而 `ChatRunStore` 仅直聊。合并会把 group/WS 耦合进直聊 store,并放弃"MySQL 审计 vs Redis 路由"的二分。**结论:两者各自独立治理,共享 `run_id`,由编排层对齐生命周期。**

| | ChatRunStore | BotRunContext |
|---|---|---|
| 服务范围 | 仅直聊 async | 直聊 + 群聊 + WS alias 通用 |
| system-of-record | **MySQL 权威** + Redis 热缓存(审计硬需求,偏离 issue Redis 处方) | **Redis**(issue 处方) |
| 含正文 | 是 | 否 |
| CAS 机制 | SQL `UPDATE ... WHERE version=? AND state NOT IN(...)` | Redis `SET NX` 认领 + Lua terminal 单调 |

## 3. SSE 三层与活读流边界

Provider 下行 SSE(`bcs-provider-http/src/lib.rs:377-474`)分三层:

| 层 | 是什么 | 归属 | 治理 |
|---|---|---|---|
| ① transport 绑定(走 SSE 非 callback) | `BotRunContext.provider_transports` | BotRun | ✅ Redis |
| ② 内容累积(SSE delta→正文) | `ChatRunStore.accumulated_content` | ChatRun | ✅ MySQL+Redis |
| ③ 活读流(reader 任务 + 打开的 HTTP 连接) | 节点本地 runtime | ❌ 不治理(issue 明令) |

边界处理(**用户裁定:TTL 兜底,选项 A,贴 issue 范围**):活读流不持久化;持有 reader 的节点重启则流断,靠 `ChatRun.expires_at` + 超时 scanner 把"reader 死了无终态"的 run 标 `failed("timeout")`。轮询者拿到"超时失败 + 已累积正文"(这正是治理买到的韧性;治理前整体丢)。跨节点接管/重建流为 issue 非本期验收,不做。

## 4. ChatRunStore 治理(MySQL 权威)

### 4.1 port
新 `bcs_service_api::port::repo::chat_run::ChatRunRepoPort`(在 `port/repo/mod.rs` 注册 pub use)。`ChatRunRecord`/`ChatRunState`/`ChatRunCompletionPolicy` 从 `bcs-message-flow::a2a_chat::run_store` 下移到此 crate,在原处 re-export 保持现存调用点不破。薄端口,不含状态机规则(规则集中在引擎,单一事实源):

```rust
async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunRepoError>;
async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError>;
async fn compare_and_set_state(&self, run_id, expected_version, new) -> Result<CasOutcome, _>;
async fn compare_and_set_terminal(&self, run_id, expected_version, new) -> Result<CasOutcome, _>;
async fn append_streaming_content(&self, run_id, expected_version, accumulated, truncated) -> Result<bool, _>;
async fn list_active(&self, now_ms) -> Result<Vec<ChatRunRecord>, _>;
async fn delete_expired_terminal(&self, now_ms, retention_ms) -> Result<usize, _>;
async fn metric_counts(&self) -> Result<Vec<ChatRunMetricCount>, _>;
```
`CasOutcome { Applied(rec), Conflict(Option<rec>), Terminal(Option<rec>) }`;`ChatRunRepoError { DuplicateRunId, NotFound, Conflict, Backend(String) }`(`Backend` 必须向上传播)。

### 4.2 字段级权威切分
- state/version/owner/session/timestamps/error/**终态正文** → MySQL 权威(审计)。
- 流式进行中 `accumulated_content` + 流式 version → Redis 热缓存(TTL=expires_at),per-token 高频写不打 MySQL。
- `Notify` → 节点本地。

写:state 转移→MySQL CAS + 刷 Redis;流式正文→只写 Redis(version++/accumulated)TTL 续期;终态→MySQL 一条事务写 state+正文+version,使 Redis 失效。读:终态→MySQL;非终态→Redis,miss/超时回源 MySQL。version 一致性:终态写取 `max(redis,mysql)+1` 续接 `since_version`。

### 4.3 引擎
`ChatRunStore` 重构为状态机引擎 over `Arc<dyn ChatRunRepoPort>` + 节点本地 Notify 注册表;对外 API 不变。`mutate` 拆 `mutate_state`(MySQL CAS)/`mutate_content`(Redis)。`wait_update` 正确性来自 `repo.get` 兜底 + Notify 加速(poll_interval≈1s)。`cleanup_expired`:`to_drop`→`delete_expired_terminal`;`to_fail`→`list_active(now)`→`mark_failed`。1 MiB `is_char_boundary` 截断保留。

### 4.4 store crate
新 `services/bcs-chat-run-store`:`SqlChatRunRepo`(吃 `Arc<dyn DbPlugin>`+flavor+`Arc<dyn CachePlugin>`+key_prefix+env)与 `MemoryChatRunRepo`(dev/test)。表 `bcs_chat_runs`(时间戳 BIGINT millis)。两处迁移:`SQLITE_DDL_STATEMENTS` + `migrations/mysql/011_chat_runs.sql`。

## 5. BotRunContext 治理(Redis)

新 `RedisBotRunContextStore` 实现 `BotRunContextPort`(在 store crate 或 `bcs-message-flow`),用 Redis:
- `runs`:`{prefix}botrun:{run_id}` → `BotRunContext`,TTL=deadline+retention。
- 终态认领:`try_begin_terminal` 用 `SET {prefix}botrun:claim:{run_id} NX`(TTL);`mark_terminal` 用 Lua 做"claim 态→terminal 单调"原子写回。
- `provider_transports`:`{prefix}botrun:transport:{run_id}`,TTL 短键。
- `MemoryBotRunContextStore` 保留 dev/test。
- 编排层对齐:`A2aChat` 终态化后同步 `mark_terminal`;分别落各自权威,无分布式事务,TTL 兜底。group_flow/WS alias 路径不变(API 相同,换实现)。

## 6. 可配置切换(用户裁定:缺省 memory,安全发布)

port + 两实现即为开关,符合既有 `MemoryGroupRepo`/`MySqlGroupStore`、`db_kind` 范式,零额外机制:

```toml
[direct_chat]
run_store = "persistent"   # | "memory";缺省 "memory"
run_context_store = "redis" # | "memory";缺省 "memory"
```

- 缺省 `memory` = #1546 前老行为,可回滚(不具重启/多副本一致性)。
- standalone/Default 恒 memory(不依赖 DB/Redis)。
- memory 模式 = 新引擎 + `MemoryChatRunRepo`/`MemoryBotRunContextStore`,由契约测试兜底等价。
- 灰度按实例/环境整批切;不跨模式迁移单 run(键空间/持久化语义不同)。
- bootstrap 生产路径(`server.rs:4254` `new_with_infrastructure`)读配置选实现;DB/Redis 取既有 `db_plugin`/`cache_plugin`。

## 7. 节点本地保留(不持久化,issue 明令)
`Notify`、`ChatRunEventPort`/`ChatRunCleanupPort` + `RunChannelManager`(mpsc + alias/session/trace)、SSE 活读流(reader 任务 + HTTP 连接)。跨节点事件交付用 owner-node 路由/发布订阅,本期不做。

## 8. 验证
- `cargo build -p bcs -p bcs-chat-run-store -p bcs-cache-redis`
- `cargo test -p bcs-chat-run-store`(SqlChatRunRepo(SQLite)+Memory CAS/状态机)
- `cargo test -p bcs-message-flow`(contract_a2a_chat / conformance 契约不破)
- `cargo test --workspace`
- 用例:重启可查、跨副本共享 repo、终态幂等 CAS、cancel 幂等、写失败传播(注入 DbPlugin 失败→5xx)、TTL 不误删、Notify 仅加速、审计 SQL 可查、SSE reader 死→超时标 failed(timeout)、memory/persistent 可配置切换且行为符合预期。

## 9. 与既有计划的关系
本 spec 取代前述 ChatRunStore 单点实现计划的 design-doc 交付物。§4(ChatRun)= 已批准的 ChatRunStore 持久化计划(实现照旧推进)。§5(BotRun Redis)= 本期新增。§3 SSE 边界 + §6 配置切换 = 新增约束。

## 10. 实现状态(2026-08-27)
已实现并验证(单测全绿):
- `ChatRunRepoPort` + `ChatRunRecord` 等类型下移至 `bcs_service_api::port::repo::chat_run`。
- `bcs-chat-run-store` crate:`MemoryChatRunRepo`(行为等价)与 `SqlChatRunRepo`(MySQL 权威 + Redis 热缓存,流式 overlay 合并读)。
- `ChatRunStore` 重构为引擎 over `Arc<dyn ChatRunRepoPort>` + 节点本地 `Notify`;`new()`/`with_capacity` 走 Memory,`with_repo` 供持久化。`bcs-message-flow` 全部 234 个既有测试保持通过。
- `RedisBotRunContextStore`(over `CachePlugin`),`BotRunContext` 加 serde derive。6 个单测通过。
- 配置:`async_chat_run_store`(memory/persistent,缺省 memory)、`bot_run_context_store`(memory/redis,缺省 memory)。生产组合路径 `new_with_infrastructure` 按配置选实现;standalone/Default/in-memory 路径恒 memory。
- 迁移:SQLite `SQLITE_DDL_STATEMENTS` + `migrations/mysql/016_chat_runs.sql`。
- `cargo check -p bcs` 通过;`cargo test -p bcs-chat-run-store`、`cargo test -p bcs-message-flow` 通过。

## 11. 待后续补齐(follow-ups)
- **A2aChat ↔ BotRun 终态协同**:直聊终态化(`mark_completed/failed/cancelled`)成功后,在 `A2aChat`(mod.rs)best-effort 调用 `bot_run_context.mark_terminal(run_id)`,使 BotRun 登记簿与 ChatRun 终态对齐(当前直聊路径未主动终态化 BotRun,沿用既有行为——靠 deadline/cleanup)。低风险增量改动,建议在独立小改动里落地。
- **Drain 路径写失败传播的显式化**:引擎 mutator 在 `Backend` 错误时已记 `error!` 并返回 `false`(不假装成功);`create` 路径错误已映射为 `ServiceError::InternalError`(不发 202)。可选:在 `record_run_event`/drain 把 `Backend` 显式映射为 `emit_run_lifecycle(Failed, InternalError)`。
- **bootstrap 集成测试(重启/跨副本/审计/配置切换)**:本机磁盘受限(20G,`-p bcs` 测试构建超限),未能运行 `bcs` 测试二进制;建议在 CI(无磁盘约束)新增 `crates/bootstrap/bcs/tests/run_governance_restart.rs` 覆盖验收用例:重启可查、两引擎共享 repo 的跨副本一致、终态幂等 CAS、cancel 幂等、注入 DbPlugin 失败→5xx、TTL 不误删、审计 SQL 可查、SSE reader 死→超时、memory/persistent 切换。
- **生产部署前**:确保 `[cache.redis]` 与 `bcs-chat-run-store` 持久化模式在真实 MySQL/Redis 上演练;`016_chat_runs.sql` 经 `bcs-admin migrate` 应用。

## 12. 不在范围
SSE 文本装配统一(`StreamTextAssembler`)、group `MessageTracker`/`ProviderBotEvents.visible_text`(decision pending)、admin invocation / interaction 持久化、`ChatRunEventPort`/`RunChannelManager` 跨节点路由、SSE 活读流跨节点接管(issue 非本期验收)。