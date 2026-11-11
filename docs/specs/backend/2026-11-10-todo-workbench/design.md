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

A 不创建 `ac_todo_interaction`。Human 互动和验收正文是 B 协作事件；A 的 `/interactions` 接口只是从 B 事件流中筛选 `human_interaction` 与 `human_acceptance`。

所有索引和 ORM tenant guard 包含 `avernet_tenant/env`。状态更新使用行锁和数据库状态条件，避免并发双写。

## 协作插件

`TodoCollaborationPlugin` 是唯一的 B 边界：

```text
create_context(...) -> collab_context_id
append_event(collab_context_id, request) -> event
list_events(collab_context_id) -> events
```

生产实现 `HttpTodoCollaborationPlugin` 通过配置的 B base URL 调用协作 HTTP API。`LocalTodoCollaborationPlugin` 提供离线开发与契约测试模拟。B 不可用或返回不可解析数据时抛出 `TodoCollaborationError`，映射为 503。

## 服务流程

### Draft -> in_progress

`POST /run` 校验 `draft` 后调用 B `context_collab`，保存返回的 `collab_context_id`，并原子转 `in_progress`。重复 Run 非 `draft` 返回 409。

### in_progress -> pending_acceptance

前端在详情打开且状态为 `in_progress` 时轮询事件。A 读取 B 完整事件流；若在最近的 `human_interaction` 事件序号之后出现新的完成类事件，则原子更新 `pending_acceptance`。旧完成事件不会跨越新的互动边界再次生效。

### pending_acceptance -> in_progress

`POST /interactions` 仅接受 message。A 读取 B 当前最大事件序号作为边界，追加 `human_interaction`，再原子转 `in_progress`。B 负责 Context 重建与 resume。

### pending_acceptance -> completed / in_progress

`POST /acceptance` 先向 B 追加 `human_acceptance`，再写入 A 验收历史并原子更新状态。通过置 `completed`，拒绝置 `in_progress`。

## 错误与兼容

新增接口和表没有旧 API 兼容负担。回滚时停止 A 路由流量后删除新增表；B 事件数据由 B 自行保留。配置缺少 B base URL 时 Run/互动/验收不可用，但草稿 CRUD 可继续使用。
