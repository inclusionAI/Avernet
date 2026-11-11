# 待办工作台 A 侧设计

## 架构事实

- Backend 遵循 `Core Service -> Repository Protocol -> HTTP Adapter -> DI composition root`。
- Public OpenAPI 路由使用 `PublicAPIRoute`、`require_principal`、`refuse_app_only_caller`，并维护 Authorization/Admission 库存。
- 空间权限复用 `SpaceAccessServiceProtocol.require_space_member`；Bot 归属复用 `BotRepository.list_live_by_bot_id`。
- Context、事件排序、Bot 执行和 Human 互动持久化均属于 B，不进入 A 的 Core。

## 领域归属

`core/task/todos` 拥有：

- Todo 产品状态机：`draft -> in_progress -> pending_acceptance -> completed`
- Space/Bot eligibility
- Todo 与 `collab_context_id` 的关联
- 验收决策和 A 侧验收历史
- B 事件到公开 Todo API 的投影

它不拥有 Context 组装、Context 版本、事件写入顺序或 Bot Run resume 逻辑。

## 持久化模型

- `ac_todo`
  - Todo 基础信息、Bot 名称快照、产品状态、`collab_context_id`、创建者和时间。
- `ac_todo_acceptance`
  - 不可覆盖的验收结论、意见、作者和时间。

A 不创建 `ac_todo_interaction`。Human 互动是 B 协作事件，A 的 `/interactions` 接口从 B 事件流中筛选 `user_interaction.posted`；验收是 Todo 产品状态，只在 A 持久化。

所有索引和 ORM tenant guard 包含 `avernet_tenant/env`。状态更新使用行锁和数据库状态条件，避免并发双写。

## 协作插件

`TodoCollaborationPlugin` 是 Todo Core 消费 ContextCollab 的唯一边界：

```text
create_context(request) -> context
get_context(collab_context_id, actor_id) -> context
run_context(collab_context_id, actor_id) -> context
append_event(collab_context_id, request) -> event
```

A、B 当前部署在同一 Backend 进程。生产实现
`InProcessTodoCollaborationPlugin` 直接依赖 transport-agnostic 的
`ContextCollabServiceProtocol`，负责 Todo DTO、`ActorContext` 与 B 命令/投影之间的
适配，不经过内部 HTTP 自调用。`LocalTodoCollaborationPlugin` 仅提供离线开发与契约测试模拟。

身份字段必须分离：`principal_id` 是创建或操作 Todo 的 Human，`executor_id` 是
Bot 产品 ID，`executor_owner_id` 来自已解析 Bot 记录的 `owner_id`，用于组合真实运行时
地址 `bot_id:owner_id`。不得用 Todo 创建人的 `principal_id` 代替 Bot owner。

## 服务流程

### 创建 Todo 与 Context

创建 Todo 时先校验 Space member 与 Bot scope，从 Bot 记录读取并校验 owner，然后创建
B Context 并把 `collab_context_id` 绑定回 Todo。Context 创建时不自动 Run。本阶段不提供
跨 Todo/Context 两次写入的事务一致性，失败会显式返回错误，不伪装成功。

### Draft -> in_progress

`POST /run` 仅允许 B Context 投影为 `created` 的 Todo。A 通过同进程插件调用 B
`run_context`；B 调用 Bot runtime 并写入 `task.started` 后，A 将 B 的 `in_progress`
状态投影为公开 Todo 状态。重复 Run 返回 409。

### in_progress -> pending_acceptance

除 A 已持久化的 `completed` 外，Todo 状态在读取时直接由 B Context 状态投影：
`created -> draft`，`in_progress/reviewing/blocked -> in_progress`，
`done/cancelled -> pending_acceptance`。其中 `reviewing` 是 B 内部运行时评审，不替代 A 的
Human 验收；B 进入成功或取消等终态后，都交由 A 的验收流程决定 Todo 是否关闭。A 不另存一份
运行时状态。

### pending_acceptance 互动

`POST /interactions` 仅在投影为 `pending_acceptance` 时追加
`user_interaction.posted` 事件。A 不创建互动表，也不在本阶段支持 Todo 编辑。

### pending_acceptance -> completed / pending_acceptance

`POST /acceptance` 只在 A 持久化 Todo 验收结论，不修改 B Context 状态。通过后 A 状态置为
`completed`；拒绝后 Todo 保持 `pending_acceptance`。

## 错误与兼容

新增 Todo 接口和表没有旧 API 兼容负担。回滚时停止 A 路由流量后删除新增表；B 事件数据由 ContextCollab 自行保留。生产装配不再依赖 B base URL；若 Context runtime 缺少可用的 Bot delivery port，Run 会 fail closed，Todo 读与草稿创建仍保持可诊断。

同进程适配器不得把 ContextCollab 的领域异常直接泄漏到 Todo HTTP 语义。已知的 B
校验、版本冲突、父事件错误、Context 缺失以及 runtime 启动失败统一转换为
`TodoCollaborationError`，由 Todo 路由返回稳定的 503；Todo 自身的参数校验和状态冲突仍由
Todo Core 决定，例如非草稿状态执行 `/run` 返回 409。Bot delivery port 通过
transport-neutral 的 `OpenApiBotError` 暴露已知投递失败，Context runtime 再转换为
`ContextCollabRuntimeError`；未声明的编程错误不被宽泛捕获。
