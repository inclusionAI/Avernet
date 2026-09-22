# Group 级 at-human 外部通知策略设计

- 日期：2026-09-22
- 状态：设计与 implementation plan 已按审阅修订，待实现
- 范围：`src/bcs` Group 配置、Group Patch、Group Store、消息流和 `human_notify` 调用边界
- 不包含：`human_notify` Plugin API、Provider 实现、Session schema、群内消息路由语义

## 1. 摘要

当前 BCS 中，群消息里的 `at human` 会在消息持久化和现有 outbound policy 检查之后，通过
`[[human_notify.providers]]` 选择的通知 Provider 投递给被 @ 的人类角色。该行为目前没有 Group 级开关，
因此所有符合现有通知条件的消息都会触发外部通知。

本设计在 Group 上增加持久化配置 `human_mention_notify_mode`，支持三个值：

- `driver_bot_only`：只有当前 Group 的 `driver_bot` 发送的 `at human` 消息触发外部通知；
  `manager_worker` Group 中的 `driver_bot` 即 manager bot。
- `all`：任意 Group 成员发送的 `at human` 消息都可以触发外部通知。
- `none`：任何成员发送的 `at human` 消息都不触发外部通知。

默认值为 `all`，以保持现有行为。

配置只保存于 Group，不复制到 Session。消息流在通知判断时通过专用 Service API 从权威存储读取当前策略和
`driver_bot`，不复用消息处理早期的 Group snapshot 或普通 Group cache；因此配置 Patch 成功后对已有
Session 和新建 Session 都生效。并发判断边界见 §5.4。该配置只抑制通过
`[[human_notify.providers]]` 发出的外部通知，不改变群内消息的持久化、Bot 路由、Session transcript
或 Human participant 的可见性。

## 2. 当前上下文与约束

### 2.1 现有通知链路

当前人类外部通知由 `bcs-message-flow` 的共享 helper 组装并通过
`HumanMentionNotifyPort` 投递。主要入口有三处：

```text
群内 Bot/Human 消息
  -> services/bcs-message-flow/src/group_flow/group_send.rs

Workbench/Web 消息
  -> services/bcs-message-flow/src/group_flow/web_send.rs

Bot 回调消息
  -> services/bcs-message-flow/src/bot_event/relay.rs
```

三条路径都会在消息已经持久化，并通过控制同一消息的 outbound policy 之后，调用：

```text
human_notify_hook::spawn_human_mention_notify
  -> build_mention_trigger
  -> HumanMentionNotifyPort::notify_mentioned_humans
```

`HumanMentionNotifyPort` 由 bootstrap 组合根适配 `bcs-human-notify-api` 的 Plugin API。Provider 只负责
具体通知后端选择和投递，不拥有 Group 业务策略。

### 2.2 Group Patch 链路

两套 Group 更新接口最终都进入 V1 Group application service：

```text
PATCH /openapi/v1/collaboration/groups/{id}
PATCH /groups/{id}
  -> HTTP DTO
  -> application::v1::GroupPatch
  -> GroupMutableFieldsPatch
  -> GroupMutationKind::PatchMutableFields
  -> GroupRepoPort / Group Store
```

现有的 `PATCH /groups/{id}/settings` 是 `service_spec` 专属接口，不扩展它承载本配置。

### 2.3 关键架构约束

- Group 是未来 Session 的配置模板；本需求要求配置修改后影响所有 Session，因此配置不能只写入 Session。
- 消息流只依赖 `bcs_service_api` 的 Service API 和 outbound port，不能依赖具体 Store 或 Plugin 实现。
- Group Store 负责 SQL/Memory 映射、迁移和缓存；Group application/core 负责合同和业务编排。
- `human_notify` Plugin API 与 `HumanMentionNotifyPort` 不因本需求变化。
- 配置和 API 合同变化必须同步更新文档、迁移、实现和测试。

## 3. 目标与非目标

### 3.1 目标

1. 在 Group 上增加独立的 `human_mention_notify_mode` 配置。
2. 通过两套现有 Group Patch 接口更新该配置。
3. 配置支持 `driver_bot_only`、`all`、`none` 三个值。
4. 缺省配置为 `all`，兼容现有行为。
5. 配置只持久化到 Group，并对已有和新建 Session 生效。
6. 将通知策略判断集中在共享消息流入口，避免三个消息入口分别复制规则。
7. 只抑制外部 Human Notify Provider，不改变群消息业务语义。
8. 为 HTTP 合同、Group Store、消息流和迁移提供完整验证。

### 3.2 非目标

- 不修改 `[[human_notify.providers]]` 的配置格式或 Provider 选择逻辑。
- 不修改 `bcs-human-notify-api`、`bcs-human-notify-dummy`、`bcs-human-notify-work-order`。
- 不把配置复制到 Session，也不新增 Session 字段或 Session migration。
- 不改变 `at human` 的群内路由、消息持久化、Human 可见性或 Bot `chat.send`/
  `chat.inject` 语义。
- 不为通知策略增加新的独立 Plugin API、缓存广播服务或后台 worker。
- 不改变 Dm Group 目前不触发人类外部通知的行为。

## 4. 对外合同

### 4.1 Group 字段和枚举

新增领域枚举：

```rust
HumanMentionNotifyMode
```

序列化值：

```text
driver_bot_only
all
none
```

`HumanMentionNotifyMode::default()` 为 `All`。`Group` 新增非可选字段：

```rust
#[serde(default)]
pub human_mention_notify_mode: HumanMentionNotifyMode
```

`Group::new` 和所有显式 `Group` struct literal 都必须初始化为 `HumanMentionNotifyMode::default()`；运行时不使用
`Option` 表示缺失。历史 JSON 缺字段只在 serde 反序列化边界通过 default 兼容。

旧的 Group JSON 或旧数据库行缺少该字段时按 `all` 读取；对外投影始终返回一个明确的枚举值，不返回
`null`。

### 4.2 通知决策

策略判断只依赖当前消息发送者和 Group 的 `driver_bot`：

| Group 策略 | sender 是 driver | sender 是其他 Bot | sender 是 Human |
| --- | ---: | ---: | ---: |
| `all` | 通知 | 通知 | 通知 |
| `driver_bot_only` | 通知 | 不通知 | 不通知 |
| `none` | 不通知 | 不通知 | 不通知 |

`manager_worker` Group 不需要新增特殊分支。其 manager bot 已由现有 Group 语义写入
`group.driver_bot`，因此 `driver_bot_only` 自动等价于“仅 manager bot 发送时通知”。策略判断始终比较
通知判断时读取到的当前 `group.driver_bot`；如果 Group 的 driver/manager 后续发生变更，变更提交后开始
判断的消息按新的 driver 生效，不使用建群时、消息入口或 Session 创建时的 driver snapshot。与 Patch
重叠的判断按 §5.4 的单次权威读取快照决定。

`Dm` Group 允许保存和返回该字段，但仍由现有消息流条件阻止外部人类通知。字段值对 Dm 没有运行时效果，
这保持了现有 Dm 行为并让两套 Group Patch 合同保持一致。

### 4.3 Patch 接口

以下两个接口都支持新字段：

```text
PATCH /openapi/v1/collaboration/groups/{id}
PATCH /groups/{id}
```

请求示例：

```json
{
  "human_mention_notify_mode": "driver_bot_only"
}
```

规则：

- 字段省略表示保持原值不变；
- 字段只接受三个枚举值；
- `null` 不表示恢复默认值，显式传 `null` 按非法 Patch 字段处理；
- 其他 Group Patch 字段的行为保持不变；
- 现有 Group 管理权限保持不变；
- `GroupPatch::is_empty()` 必须将该字段纳入判断，使只更新该字段的请求合法；
- Legacy 和 V1 HTTP DTO 都使用非空枚举字段解析，非法值和 `null` 返回现有的
  `400 invalid_request` 错误形态；
- OpenAPI 的 Patch schema 保持 `additionalProperties: false`，新增字段加入 enum 定义。

### 4.4 查询返回

为了让调用方确认当前配置，新增字段进入所有 Group 公开投影：

- V1 普通 Group detail；
- V1 Dm Group detail；
- V1 普通和 Dm Group summary/list；
- Legacy Group detail；
- Legacy Group list entry；
- `bcs-service-api` 内部的 Group detail/list DTO。

示例：

```json
{
  "group_id": "group-123",
  "human_mention_notify_mode": "none"
}
```

响应字段是 additive change，旧客户端可以忽略。

## 5. 架构与数据流

### 5.1 领域和应用层传播

新增字段在各层按下列方向传播：

```text
bcs-domain::Group
  -> bcs-service-api::GroupMutableFieldsPatch
  -> bcs-service-api::application::v1::GroupPatch
  -> HTTP request DTO / OpenAPI response DTO
  -> Group Store
```

`GroupMutableFieldsPatch` 使用 `Option<HumanMentionNotifyMode>` 表示字段级 Patch：

```text
None       -> 不修改现有值
Some(All)  -> 设置为 all
Some(mode)  -> 设置为对应策略
```

V1 Group application service 在管理权限和其他字段校验完成后，把该字段写入
`GroupMutableFieldsPatch`，并通过现有 `GroupMutationKind::PatchMutableFields` 提交。普通字段 Patch 和
新的通知字段继续共享已有的存储错误传播、Group version/eventful mutation 和返回投影路径。

`bcs-group/src/core/group_core.rs::prepare_group_mutation` 必须将该字段纳入 `changed_fields` 比较。
只改变通知策略时必须进入 repository mutation；重复设置同一值保持既有 no-op/version 语义。不新增
mutable Patch 公共 Event：沿用现有 `event: None` 的行为。只修改 application/Store 而漏掉 Core 的
变更检测，会让单字段请求成功返回旧 Group，不能视为实现完成。

### 5.2 持久化

`bcs_groups` 增加 Group 级字符串字段：

```text
human_mention_notify_mode
```

数据库规则：

- MySQL 使用与现有 Group 枚举字段一致的字符串列，默认值为 `all`；
- SQLite 使用文本列，默认值为 `all`，新 migration 不增加 CHECK 约束；合法值校验由 HTTP/domain/Store
  parser 共同完成；
- 字段只由每个 dialect 的新 additive migration 增加，不修改已提交历史 migration 或 SQLite bootstrap
  `migrations/baseline.rs`；
- fresh install 使用不变的 baseline 后运行完整版本链，upgrade 运行相同的新 migration；不让 baseline
  和增量 migration 各加一次字段，也不以“列已存在则跳过”掩盖双重归属；
- 实现前检查当前最大 migration 编号，预计为 MySQL `030`、SQLite `031`，编号以仓库实际状态为准。

必须更新的 Store 路径：

- MySQL/SQLite 单 Group 查询；
- 按 participant 查询和分页查询的所有 Group projection；
- Group list/count 相关查询中使用的 Group row projection；
- Group `upsert` / normal create；
- Dm race-safe insert；
- eventful `PatchMutableFields`；
- 普通 `patch_mutable_fields`；
- Memory Group Store；
- Group Repo conformance fixtures。

读取规则：

- SQL `NULL` 或历史反序列化/测试 row 缺失字段映射为 `All`；数据库物理列仍必须先通过 migration 创建，
  不承诺新 SQL 能在未升级 schema 上运行；
- 空字符串以及其他任何非三枚举字符串都属于非法持久化值，返回存储/内部错误，不静默变成某个策略；
- 只更新新字段时保留 Group 其他字段、参与者、routing policy、Session 关系和消息数据。

Patch 成功后复用现有 Group Store cache invalidation，供普通 Group detail/list 读取使用。通知判断使用
下节的独立、无缓存权威读取，不把普通 cache invalidation 当作严格新鲜度证明。不新增 Session 批量写入、
配置广播或通知策略缓存。

### 5.3 消息流通知判断

扩展现有共享入口 `services/bcs-message-flow/src/human_notify_hook.rs`，将
`spawn_human_mention_notify` 改为 async；三个入口均等待它完成资格判断，再继续原有 Bot/Workbench
投递。实际 Session display metadata 查询和 Provider 调用仍由既有异步任务完成。

新增内部 Service API 类型与读取方法，不修改 HTTP 或 Human Notify Plugin API：

```rust
// bcs_service_api::types
pub struct GroupHumanNotifyPolicy {
    pub mode: HumanMentionNotifyMode,
    pub driver_bot_id: String,
}

// GroupCoreService 和 GroupRepoPort 各声明相同的读取合同
async fn read_human_notify_policy(
    &self,
    group_id: &str,
) -> ServiceResult<Option<GroupHumanNotifyPolicy>>;
```

`None` 表示 Group 不存在；存储读取/枚举解析错误返回 `Err`，不能降级为默认 `all` 或普通 `get`。
Core 只委托 repo port；Memory 在同一 `groups` 读锁内获取 mode/driver；SQL Store 在写入所用的权威数据库
上执行一次 autocommit SELECT，同一行同时读取这两个字段，并按 `env`、`group_id` 和现有可见记录条件
限定范围。不使用 Group cache、只读副本或旧事务 snapshot，不加载 participants/Session，不回填普通缓存。

通知判断顺序：

```text
1. 判断 HumanMentionNotifyPort 是否可用；无 mention ids / 无可通知 Human 时直接返回
2. 调用 GroupCoreService::read_human_notify_policy（一次通知策略判断开始）
3. 根据同一 policy snapshot 的 mode、driver 和 sender 同步判定，不在读取结果和比较之间再 await
4. 不允许时早退；允许时才创建原有通知任务
5. 任务中解析可选 Session display metadata，然后调用 HumanMentionNotifyPort
```

三条路径仍在消息持久化及既有 outbound policy 检查之后调用 helper：

```text
services/bcs-message-flow/src/group_flow/group_send.rs
services/bcs-message-flow/src/group_flow/web_send.rs
services/bcs-message-flow/src/bot_event/relay.rs
```

入口向 helper 传递 Group Core trait、mention/overlay 和现有 `MentionNotifyContext`，不传入口处 Group
snapshot 的 mode/driver。Dm 沿用既有调用条件，不进入外部通知分支。

策略抑制时用 `tracing::debug!` 记录 `group_id`、`sender_actor_id` 和 mode；policy 读取失败或 Group
已删除时，使用结构化 warning 并 fail-closed：不创建通知任务、不查询 Session title、不调用 Provider。
此处是 best-effort 通知读取失败，不是持久化写入失败；不回滚或阻断已经成功持久化消息的 Bot/Workbench
投递。所有写入错误仍必须按既有主消息/Patch 合同向上游传播。日志不包含正文、人类列表或未经清理的原始 DB 错误。

### 5.4 Session 生效与并发语义

配置只读 Group，Session 不保存该字段。`apply_session_participant_scope` 仍只替换 Group participants；
已有和新建 Session 的每次通知资格判断都调用同一 current-policy read，不使用 Session driver snapshot。

一次判断从 helper 调用 `read_human_notify_policy` 开始，到拿到 snapshot 后同步比较结束。线性化点为
Memory 读锁内取值或 SQL 单条 SELECT 建立的读取快照：

- Patch 在本次判断开始前已提交：必须使用新 mode/driver，即使消息入口已取得旧 Group 副本；
- Patch 与判断区间重叠：允许读取提交前或提交后的值，但 mode/driver 必须来自同一个已提交行版本；
- 已完成的允许判断和已创建的通知任务不因后续 Patch 被撤回，不承诺取消已在途的 Provider 调用。

这将“判断时读取当前配置”落实为明确的线性化边界，而不是声称复用 cache-first `get` 就能即时生效。
无需跨网络持锁或把 Group Patch 与 Provider 调用包在同一事务中。

### 5.4.1 访问成本与失败边界

- Port 不可用、Dm、无 mention 或无有效 Human：新增 0 次 DB 查询。
- 其他符合既有通知条件的消息：新增 1 次 scoped SELECT，最多 1 行、只读 mode/driver；即使模式为 `none`，
  也必须进行此次读取才能保证之后 `none -> all` 的 Patch 可见。
- 该路径没有按人类数量产生的 N+1，无重试、无额外写入。并发候选消息 N 条最多新增 N 次读取，连接占用
  随查询结束释放；故障时每条候选至多尝试一次，不启动重试 worker。
- 使用 recording DB/Repo 验证 0/1 次调用边界、最大支持 mention 数量和并发候选；验证连续读取失败仍无
  Provider/Session-title 调用。真实 MySQL 运行记录查询延迟及 pool 限制，不从小规模单测推断吞吐能力。

### 5.5 Human Notify Plugin 边界

本需求不修改：

- `bcs_service_api::port::HumanMentionNotifyPort` 方法签名；
- `bcs-human-notify-api` 的 `MentionNotification`；
- `bcs-human-notify-dummy`；
- `bcs-human-notify-work-order`；
- bootstrap 的 Provider factory 和 Provider fan-out。

Plugin 只会收到已经通过 Group 策略判断的 `MentionNotification`。策略属于 BCS Group/message-flow 业务层，
Provider 不需要理解 `driver_bot_only`、`all` 或 `none`。

## 6. 持久化迁移与兼容性

### 6.1 Migration

实现阶段新增：

- MySQL additive migration：在当前最大编号之后新增 `human_mention_notify_mode`，默认 `all`；
- SQLite additive migration：在当前最大编号之后新增同名字段，默认 `all`；
- SQLite baseline/历史 bootstrap DDL 保持不变；新库和旧库均通过新 versioned migration 增加字段；
- migration 说明记录历史数据缺失时的兼容值为 `all`。

遵守 BCS migration 约束：

- 不修改已提交 migration 的内容、编号或 checksum；
- 不把新字段回填到旧 `001_init_schema.sql` 或 SQLite `migrations/baseline.rs`；
- 每个 dialect 的字段 addition 使用新的唯一 migration；
- migration 重复执行保持幂等；
- MySQL migration 在真实 MySQL 链上验证，不能只依赖静态 SQL 检查；使用现有
  `full_mysql_migration_chain_applies_and_preserves_history` ignored 测试，并通过
  `BCS_TEST_MYSQL_URL` 指向一次性 MySQL 8.4 测试库；本地无 MySQL 时由
  `.github/workflows/unit-tests.yml` 的同一 job 提供验收证据，不能把静态检查当作已验证；
- 同步更新 `migrate_mysql_chain_tests.rs` 固定终点断言：全链 30、版本集合 `1..=30`、v20 前缀升级 10；
  新增 Group 列/默认值检查；以上数字仅在新迁移实际编号仍为 030 时适用；
- 在同一真实 MySQL 测试数据库生命周期内单独执行 Group Store 共享行为测试并记录结果，迁移通过不能替代
  upsert、冷读、普通/eventful Patch 和无缓存 policy read 的行为验证。

### 6.2 API 兼容性

这是 additive API change：

- 旧数据缺字段时行为为 `all`；
- 旧请求不发送新字段时行为不变；
- 旧客户端忽略响应新增字段即可；
- 非法新值只影响发送非法请求的客户端，不改变旧字段行为；
- 部署必须先完成 additive schema migration，再启动使用新列的二进制；SQL 默认值处理历史行，
  不需要额外业务级逐行回填。Serde/parser 默认值不是跳过 schema migration 的依据。

### 6.3 写入失败语义

数据库或 Group Store 写入失败必须向上游传播：

```text
Group Patch persistence failure
  -> application error
  -> HTTP error response
  -> no successful “configuration updated” response
```

不能在写入失败后只更新进程内 Group、只刷新 response projection 或返回成功。

## 7. 错误处理

### 7.1 HTTP 输入

以下情况返回现有 `400 invalid_request`：

- `human_mention_notify_mode` 不是三个合法值；
- 字段显式传 `null`；
- 传入 OpenAPI 禁止的未知字段；
- 只传未知/非法新字段导致 Patch 无法构造。

现有鉴权、Group not found、Group management forbidden、数据库错误映射保持原有合同。

### 7.2 Store 读取

- `NULL`/历史缺失：`All`；
- 合法枚举：对应领域值；
- 未知字符串：返回 `ServiceError::InternalError` 或等价存储错误；
- 不允许未知值静默映射到 `none` 或 `all`，避免数据损坏导致不可见的通知策略漂移。

### 7.3 外部 Provider 错误

继续遵循现有通知语义：外部通知错误由通知端口/adapter 记录，不影响主消息路径。新策略只决定是否进入
该端口，不改变 Provider error handling。

## 8. 测试与验证

### 8.1 Domain / Service API

覆盖：

- 枚举三种值的 serde round-trip；
- 默认值为 `all`；
- 缺失 Group 字段反序列化为 `all`；
- `GroupMutableFieldsPatch` 能区分 omitted 和三个设置值；
- 只 Patch 新字段不改变其他 Group 字段；
- 新增 policy read 的未知实现 fail-closed，不允许默认退回 cache-first `get`。

### 8.2 HTTP 合同

V1 OpenAPI 和 Legacy HTTP 都覆盖：

- Patch `driver_bot_only`、`all`、`none`；
- 省略字段保持原值；
- 非法字符串返回 `400 invalid_request`；
- `null` 返回 `400 invalid_request`；
- 未知字段继续被拒绝；
- 成功响应包含持久化后的策略值；
- detail/list 返回当前策略值（包括 Dm Group projection）；
- Dm Group 可以保存和返回字段。

同步更新并验证：

```text
src/bcs/api-contracts/v1/openapi/groups.yaml
src/bcs/api-contracts/v1/domain-models.yaml
src/bcs/tests/openapi/test_group_v1_contract.py
```

### 8.3 Group application

覆盖：

- 现有 Group 管理权限仍然生效；
- 新字段单独 Patch 通过真实 application -> Core -> Store 成功，version 仅在值变化时增加；
- 同值重复 Patch 保持 no-op，不新增公共 Event；
- 新字段不会覆盖 `name`、`context`、`visibility`、`delivery_policy` 等其他 Patch 字段；
- Patch 失败不会返回成功；
- Group detail/list projection 返回默认值和已配置值；
- Dm Group 的字段持久化不改变 Dm 通知禁用语义。

### 8.4 Group Store conformance

Memory、SQLite、MySQL 共享同一行为要求：

- 新建 Group 默认持久化为 `all`；
- 三种值都能完整 round-trip；
- NULL/历史缺失值读为 `all`；
- 普通 mutable patch 和 eventful patch 都能更新字段；
- 只更新该字段时保留其他字段；
- 所有 Group SELECT projection 都能读到该字段；
- migration 能在旧 schema 上执行且重复执行幂等；
- 非法持久化值返回错误；
- SQL 写入失败向上游传播；
- SQL round-trip 必须以同 DB 的新 Store 实例冷读，不能只读取 upsert 填充的 cache；
- read_human_notify_policy 在暖旧缓存、另一个 Store 提交新值后仍返回新值，且不同 env 的同名 Group 隔离；
- SQL 查询每个候选至多 1 次、至多 1 行，不加载 participants；Memory 与真实 MySQL/SQLite 运行同一共享行为套件。

### 8.5 Message Flow

共享 helper 策略矩阵：

| 模式 | driver Bot | 其他 Bot | Human |
| --- | ---: | ---: | ---: |
| `all` | 通知 | 通知 | 通知 |
| `driver_bot_only` | 通知 | 不通知 | 不通知 |
| `none` | 不通知 | 不通知 | 不通知 |

消息流至少覆盖：

- `group_send`；
- `web_send`；
- `bot_event/relay`；
- `manager_worker` 中 manager 通知、worker 不通知；
- Dm Group 始终不通知；
- `none` 下消息仍被持久化；
- `none` 下 Bot/Workbench 路由结果不变；
- Patch Group 后已有 Session 使用新策略；
- 新建 Session 使用同一 Group 策略；
- 被抑制时不创建通知任务、不查询 Session title、不调用 Provider；
- 屏障测试暂停在入口旧 Group 已读取、通知判断尚未开始的位置，提交 `all -> none` 或 driver 变更后继续，
  必须按新值判断；另测与判断重叠的 Patch 只使用一个一致 snapshot；
- policy read 的 missing/error/非法值都 fail-closed，主消息仍持久化并路由；
- Port 不可用/无有效 Human 时 0 次新读取，有有效目标时 1 次读取；连续失败没有自动重试。

### 8.6 静态与架构验证

实现完成后从 `src/bcs` 运行最邻近 Rust 测试，至少包括：

```bash
cargo test -p bcs-domain
cargo test -p bcs-service-api
cargo test -p bcs-group-store
cargo test -p bcs-group
cargo test -p bcs-app-group
cargo test -p bcs-message-flow
cargo test -p bcs-http
cargo test -p bcs-api-http
cargo check --workspace --all-targets
```

另外验证：

- 从仓库根运行 `uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py --root src/bcs/api-contracts/v1`；
- 从仓库根运行 `uv run --with pyyaml --with pytest python -m pytest src/bcs/tests/openapi/test_group_v1_contract.py`；
- SQLite fresh/upgrade/repeat migration chain，历史数据和 migration records 保留；
- 真实 MySQL migration chain 和 Store 共享行为测试，分别记录是否执行；
- 从 `src/bcs` 运行 architecture/import checks；Rust protocol presence/public-api 检查不等于 OpenAPI 校验，
  旧路径/扫描失败必须修复并补脚本回归，不能将 SKIP 或未扫描的 PASS 当验收；
- 对实施起点到 HEAD 的完整已提交差异，以及 staged/unstaged 差异分别运行 hygiene 检查；
- 检查每个新增/修改源文件的行数；超 1,000 行的历史文件也不自动豁免，优先按职责拆分，例外必须走实际
  CI allowlist 和显式原因/清理计划，不能在本计划里自建免检列表；
- 不运行全局 `cargo fmt`，遵守 `src/bcs/AGENTS.md`。

## 9. 传播范围与架构审查

### 9.1 受影响的 Service/API 合同

- `bcs-domain::Group`：新增领域字段和枚举，并更新 domain root re-export；
- `GroupHumanNotifyPolicy`、`GroupCoreService::read_human_notify_policy` 和 `GroupRepoPort`：新增 Service/Repo read contract；
- `GroupMutableFieldsPatch`：新增字段级 Patch；
- V1 `GroupPatch`、Group detail/summary/list DTO；
- V1 OpenAPI Group schema；
- Legacy HTTP Group request/response projection；
- `GroupHumanNotifyPolicy`、`GroupCoreService::read_human_notify_policy` 和同名 Group Repo port 合同。

这是 Service API 和配置 schema 的 additive change，需要同步消费者、实现和合同测试。

### 9.2 受影响的实现

- `bcs-domain`；
- `bcs-service-api`；
- `bcs-app-group`；
- `bcs-group`；
- `bcs-group-store`；
- `bcs-http`；
- `bcs-api-http`；
- `bcs-message-flow`；
- bootstrap 新 migration 注册/测试（不修改 baseline）；
- `bcs-test-support` 共享 conformance、`bcs-admin` 真实 MySQL 合同和必要的 CI 校验脚本修复；
- 相关测试和 OpenAPI 合同。

### 9.3 不受影响的实现

- `bcs-human-notify-api`；
- `bcs-human-notify-dummy`；
- `bcs-human-notify-work-order`；
- `[[human_notify.providers]]` 配置解析和 Provider factory；
- Session Store schema；
- Bot delivery protocol；
- Frontend UI（前端展示或管理入口不在本需求范围内）。

### 9.4 架构规则符合性

- Group 策略放在 Group domain/application，而不是具体 Provider；
- message-flow 只调用 `HumanMentionNotifyPort`，不依赖 concrete Plugin；
- Store 负责数据库映射，core/application 不直接访问 SQL；
- Patch 仍通过现有 Service API 和 repository port；
- 未新增 transport/framework 依赖到 core；
- 未新增环境变量、硬编码 URL、token 或私有 endpoint；
- 未把配置复制到 Session，避免 Group/Session 配置漂移；
- 该变更的 propagation scope、兼容性、migration 和 conformance coverage 已明确。

## 10. 方案选择记录

### 10.1 采用独立 Group 字段

选择 `Group.human_mention_notify_mode` 独立持久化字段，而不是写入已有 `routing_policy_json` 或新增通用
`group_settings_json`，原因：

- 人类外部通知策略不是 Bot 消息 routing policy；
- 独立字段的合同、查询、Patch 和 migration 语义清晰；
- 可以复用已有 Group mutable patch，不引入额外 JSON merge/CAS 复杂度；
- 当前只有一个新配置，不为未来未知配置提前增加通用抽象。

### 10.2 不写入 Session

Session 是参与者和运行时上下文的 snapshot，但本配置明确要求修改后对所有 Session 生效。将其写入 Session
会使旧 Session 继续使用旧策略，违反需求。因此只保存 Group，通知时使用当前 Group。

### 10.3 不修改 Plugin

Provider 不应该理解 Group 级业务策略。BCS 在调用 Plugin 前完成策略裁剪，Plugin 继续只处理已经确认要发送的
`MentionNotification`，保持 Plugin API 稳定和可替换性。

## 11. 验收标准

实现被视为满足本设计时，必须同时满足：

1. Group 新字段默认值为 `all`，旧数据行为不变；
2. 两套 Group Patch API 都能单独设置三种值；Core 识别真实变更，同值 Patch 不增加 version，响应返回持久化值；
3. 非法值和 `null` 被拒绝；
4. Dm Group 可以保存字段但仍不触发外部人类通知；
5. `all`、`driver_bot_only`、`none` 的 sender 策略矩阵全部通过测试；
6. `manager_worker` 的 manager/worker 行为符合 `driver_bot` 语义；
7. Group Patch 修改后已有 Session 和新 Session 都使用新值，屏障测试证明不依赖消息入口旧 snapshot/cache；
8. 被抑制的消息仍完成原有持久化、群内路由和可见性处理；
9. `human_notify` Plugin API 和 Provider 实现无代码修改；
10. MySQL、SQLite、Memory Store 运行共享行为验证，migration chain 单独验证；SQLite baseline 保持不变；
11. 受影响的 OpenAPI、Service API、Group Store、message-flow 合同测试通过；
12. 架构检查、workspace check 和覆盖所有实现提交的 diff hygiene 验证通过；
13. 专用 policy read 满足 0/1 次访问边界，读取失败 fail-closed，且不改变主消息持久化/路由结果。

## 12. 后续工作边界

本次修订仅更新设计与配套 implementation plan，不代表功能已实现或验收通过。实现阶段不应额外加入：

- 前端配置面板；
- Provider 级 override；
- Session 级 override；
- 人类角色分组通知；
- 通知重试/Outbox；
- 新的通用 Group settings framework；
- 与本需求无关的 Group 或 message-flow 重构。
