# Plan: Group Context 多值 Owners

- 日期：2026-10-19
- Spec：`spec-owner-field.md`
- 目标：为 Group Context 增加 JSON object 形式的多值 `owners`，默认 creator 为 owner；owner 拥有 `WRO` 权限；非 owner 的 `W` 只能修改 content 和有效期字段。

---

## Approach

在每个 Group Context 版本行中持久化 `owners`。`add` 和内部 `systemset` 的首次创建由服务端确定默认 owners；`update` 先基于当前 active row 判断 actor 是否 owner；owner 可更新管理字段且不要求出现在 `collect_from`，非 owner 再按现有 `collect_from` 获得普通 W 权限，且只能更新 `content`、`valid_from`、`valid_to`。`list/get` 基于同一 owner 判断，owner 输出 `WRO`，`get` 仅对 owner 额外返回策略字段。

本轮不接入 creator bot 的 owner/管理员运行时能力，也不为其新增 port 或占位实现。owner 授权只基于当前 active row 的 persisted `owners`；管理员不会写入存储。

## Affected Components

- `apps/bcs/crates/contracts/bcs-domain/src/group_context.rs:31-79` — 新增 `GroupContextOwners` 类型；`GroupContextPermission` 增加 `WRO`。
- `apps/bcs/crates/contracts/bcs-domain/src/group_context.rs:85-130` — Entry/Meta 增加版本化 `owners` 字段。
- `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs:50-79` — add 不暴露 owners；update 增加 owner 变更输入。
- `apps/bcs/crates/service-api/bcs-service-api/src/application/v1/group_context.rs:154-177` — list 输出 `WRO`；get 增加 owner-only 策略字段。
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:40-117` — create/supersede 写入版本化 owners。
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/service.rs:122-317` — add/update/list/get/systemset 接入 owner 语义。
- `apps/bcs/crates/application/v1/bcs-app-group-context/src/permissions.rs:3-15` — owner 隐含读写权限，并生成 `WRO`。
- `apps/bcs/crates/services/bcs-group-context-store/src/store.rs:32-47` — `NewGroupContextEntry` 增加 owners。
- `apps/bcs/crates/services/bcs-group-context-store/src/store.rs:134-158` — SQL select 增加 `owners_json`。
- `apps/bcs/crates/services/bcs-group-context-store/src/store.rs:194-255` — SQL row 映射 owners。
- `apps/bcs/crates/services/bcs-group-context-store/src/store.rs:260-530` — create/supersede/list/get 持久化并读取 owners。
- `apps/bcs/crates/services/bcs-group-context-store/src/memory.rs:28-173` — memory repo 对齐 SQL 语义。
- `apps/bcs/crates/adapters/http/bcs-http/src/routes/group_context.rs:44-149` — update 暴露 `owners`，add 不暴露。
- `apps/bcs/crates/tools/bcs-cli/src/main.rs:1532-1581` / `:4334-4433` — CLI update 支持 `--owners`；add 不支持。
- `apps/bcs/crates/bootstrap/bcs/src/migrations.rs:26-154` / `:479-488` — SQLite migration registry 和 runner 追加版本。
- `apps/bcs/migrations/sqlite/034_group_context.sql`、`apps/bcs/migrations/mysql/033_group_context.sql`、`apps/bcs/migrations/oceanbase/031_group_context.sql` — 已建表 migration 保持不改。

## Data Model Changes

### Domain 类型

新增独立类型，不复用 `GroupContextPolicy`：

```json
{
  "userIds": ["<owner-uuid>"]
}
```

- 字段名 wire 层为 `userIds`。
- `userIds` 必须非空；空列表表示非法请求/非法写入。
- 一个字段天然支持多个 owner，不做单值字段。
- `visible_to.userIds` 的空列表含义是“所有人”；owners 的空列表含义是非法，二者语义不同，因此必须使用不同类型。
- 应用层校验每个 id 非空，并沿用 actor id 的长度限制。

### 存储结构

`bcs_group_contexts` 新增：

```text
owners_json NULL
```

- 每个版本行复制写入自己的 `owners`；授权只读当前 active row。
- `update` 未传 `owners` 时，新版本沿用 old active row 的 owners；legacy rows 的 NULL/empty owners 也沿用，且不因空列表校验失败。
- `update` 传入新 owners 时，新 active row 使用新列表，历史版本保持旧列表；owner 变更是一次立即生效的所有权移交，请求传入列表必须非空。
- 公开 `add` 默认 `owners.userIds = [actor_id]`。
- `systemset` 是内部系统通道：不执行 owner 或 `collect_from` 授权判断；首次创建默认 `owners.userIds = ["system"]`，supersede 既有系统 context 时沿用 old owners（包括 legacy empty/null），避免内部系统更新剥离原 owner。
- 迁移不为存量数据提供 creator-derived owners。老 active/history rows 保持 `owners_json=NULL`；store 读取时映射为 empty owners，代表“没有 owner 权限”，而非“creator 是 owner”。

### Migrations

新增 additive migration，并接入 runner：

- `apps/bcs/migrations/sqlite/035_group_context_owners.sql`
- `apps/bcs/migrations/mysql/034_group_context_owners.sql`
- `apps/bcs/migrations/oceanbase/032_group_context_owners.sql`

三个 dialect 各自只执行 additive `ALTER TABLE ... ADD COLUMN ... NULL`，不包含 `UPDATE`，也不追加 `NOT NULL` 约束。SQLite 需要在 `migrations.rs` 注册 version 35 并添加执行分支；MySQL/OceanBase 按现有发布路径接入。

## API / Interface Changes

### add

- `AddGroupContext` 与 HTTP `AddRequest` 不增加 `owners` 字段。
- 服务端创建 `owners = [actor_id]`。
- 保持现有 serde 行为：未知字段被忽略。body 中即使出现 `owners` 也不会生效；本轮不引入 `deny_unknown_fields`。

### update

- `UpdateGroupContext` 与 HTTP `UpdateRequest` 增加可选：

```json
{
  "owners": {
    "userIds": ["<owner-uuid>"]
  }
}
```

- 授权顺序固定为先判断 owner，再判断普通 `collect_from` 写权限。
- 授权依据是请求读取到的 old active row；`command.owners` 只写新版本，不参与本次授权。
- owner 可修改：`content`、`valid_from`、`valid_to`、`visible_to`、`collect_from`、`owners`。
- owner 不要求出现在 `collect_from`。
- 未传 owners 时沿用 old owners，包括 legacy NULL/empty owners；传入 owners 时必须非空。
- 非 owner 必须满足现有 `collect_from` 写权限。
- 非 owner 的 `W` 只能修改：`content`、`valid_from`、`valid_to`。
- 非 owner 的 `W` 传入 `owners`、`visible_to` 或 `collect_from` 时返回 `permission_denied`。
- `change_reason` 继续作为审计元数据，不作为权限绕过条件。
- owners 变更是所有权移交，允许移除当前 owner，但列表不得为空。
- 新 owners 列表只作用于新 active row；移除的旧 owner 立即失去 owner 权限，之后只能依据普通策略获得读写能力。
- 无两阶段确认、撤销或旧 owner 保底机制。

### list

- `GroupContextPermission` 增加 `WRO`，序列化为 `"WRO"`。
- owner 即使不在 `visible_to` 或 `collect_from` 中也返回。
- owner 的 `permission` 输出 `WRO`。
- 非 owner 保持现有 `R/W/WR`。
- list 不返回 owners 本身。

### get

- owner 即使不在 `visible_to` 中也可读取。
- `GroupContextGetItem` 为 owner 额外输出：

```json
{
  "visible_to": {"userIds": []},
  "collect_from": {"userIds": []}
}
```

- 非 owner 输出中不包含这两个字段，使用 `skip_serializing_if = "Option::is_none"`。
- get 不返回 owners 本身。

### systemset

- `systemset` 保留内部 BCN 系统通道语义，不进入公开 update 的 owner/W 字段授权逻辑。
- 首次创建时写入 `owners=["system"]`。
- supersede 既有 context 时一律沿用 old owners。
- 不因为 old owners 里没有 `system` 而拒绝系统更新；也不因系统更新把 owner 改成 `system`。

### 后续扩展边界

- 本阶段不调用、抽象或模拟 creator bot 的 owner/管理员能力。
- 后续如需接入，另立需求和设计；本轮仅把 `owners` 建成可扩展的多值结构。

## Key Files & Functions

- `GroupContextOwners` — 新 domain 类型，提供构造、contains、empty 判断。
- `GroupContextPermission::WRO` — owner 专用标签，不新增 O-only / RO / WO。
- `NewGroupContextEntry.owners` / `GroupContextEntry.owners` / `GroupContextEntryMeta.owners` — store 与 domain 版本化模型。
- `UpdateGroupContext.owners` / `GroupContextGetItem.visible_to` / `GroupContextGetItem.collect_from` — transport-neutral contract。
- `actor_is_owner` / `actor_can_read` / `actor_can_write` / `permission_for` — 先做 owner 判定；`actor_can_write = owner || collect_from`，owner 隐含 R+W。
- `GroupContextApplicationServiceImpl::add` — 默认 creator owner。
- `GroupContextApplicationServiceImpl::update` — owner/W 字段级授权。
- `GroupContextApplicationServiceImpl::list/get` — owner 可见性与 `WRO`。
- `GroupContextApplicationServiceImpl::systemset` — 首建 system owner、后续沿用 owners，并保持内部通道不执行业务授权。
- `SqlGroupContextStore` / `MemoryGroupContextRepo` — owners 读写和版本历史。
- `GroupContextCommands::Update` — CLI `--owners <json>`；add 不新增参数。
- `bcs::GroupContext` — bootstrap 只在需要时调整构造调用，不改变 AddAccessGuard 包装顺序。

## Dependencies

- 不新增 crate。
- 不接入新的外部管理员服务。
- Migration 依赖现有 DB plugin 的 dialect 处理。

## Risks & Mitigations

- **Risk：当前 `creator` 是版本写入者，不是稳定原始 creator，容易被误用作授权 anchor。**
  现有 domain 注释和 `update/systemset` 逻辑 (`service.rs:108-110`、`:186-198`、`:270-317`) 都把写入者写入版本行。
  **Mitigation：** 本轮 owner 授权只读取 persisted owners，不依据 `creator` 判定权限。
- **Risk：把存量 creator 误推成 owner 会造成历史授权数据被迁移改写。**
  **Mitigation：** migration 只新增 nullable `owners_json`，不执行历史行 `UPDATE`；store 将 NULL 映射为 empty owners，empty owners 不授予 owner 权限。
- **Risk：owner 更新 owners 属于立即生效且不可自行撤销的所有权移交，移交者可能失去管理权。**
  **Mitigation：** 明确这是产品语义而非异常：列表非空即可成立，提交后以新 active row 为准；文档和测试显式说明旧 owner 会被移除，不提供两阶段确认或旧 owner 保底。
- **Risk：add body 传 `owners` 可能被调用方误解为已生效。**
  **Mitigation：** API 文档明确 add 不接收 owners；服务端总是覆盖为 actor id。不为了这个边界引入全局未知字段拒绝。

## Alternatives Considered

- **单值 owner：** 放弃。已确认需要多 owner 扩展性。
- **复用 `GroupContextPolicy`：** 放弃。其空列表语义与 owners 冲突。
- **持久化管理员：** 放弃。当前没有可靠管理员来源，持久化会制造不稳定快照。
- **现在新增 Noop authority port：** 放弃。当前没有可调用的真实能力，空接口会无意义地扩大实现面。

## Rollout

- 新增 column 然后发布应用，不使用 feature flag。
- 旧请求不含 `owners` 时：add 使用默认值；update 沿用 old owners（legacy NULL/empty 状态也被沿用）。
- 新列是 nullable additive column，不承载历史数据变更。
- 回滚应用时旧代码不会读新列；如需要数据库层完整回滚，按 dialect 显式删除新增列或恢复备份，不做自动 down migration。

## Test Strategy

- Domain/service API contract：JSON shape、`WRO`、add 不暴露 owner、get optional policy 字段。
- Store conformance：create/supersede 保留版本 owners；memory 与 SQLite/MySQL 语义一致。
- Migration：三种 dialect 建 nullable column、保持 active/history rows 为 NULL、checksum/runner。
- Application：
  - add 默认 creator owner。
  - owner 可改全部管理字段和 owners。
  - owner 不在 `collect_from` 时仍可改 content 和管理字段。
  - owner A 移交 owners 到 B 后，B 获得 owner 权限，A 立即失去 owner 权限。
  - W-only 只能改 content/validity。
  - W-only 改 `owners/visible_to/collect_from` 拒绝。
  - 授权只基于请求读取的 old active row，不能被请求中的新 owners 授权；非 owner 请求把自己加入 owners 被拒绝。
  - systemset 新建和 supersede 的 owners 行为。
  - systemset supersedes 既有 context 时不执行 owner/`collect_from` 授权。
- List/get：owner 输出 `WRO`、绕过可见性；get owner-only 字段；非 owner 不泄露策略。
- HTTP/CLI：update 参数透传；add 不支持；JSON 输出符合合同。
- Integration：跨 add/update/list/get 的 owner 生命周期。
- 收尾执行目标 package tests 和 `git diff --check`。
