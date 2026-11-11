# 待办工作台（当前空间、事件与验收）

Status: **approved**

## 范围

实验室 `/lab/tasks` 提供产品化待办工作台。待办绑定当前空间和单个 Bot；创建为草稿，用户显式启动后，页面展示 B 事件时序，待验收时进行 Human 互动并完成验收。

## 已确认行为

- 页面只读取全局当前空间，不提供空间创建、邀请成员、加入空间或成员管理。
- Bot 清单直接读取当前空间 Bot 工坊列表；本期不做多 Bot、并行分支、团队知识库和标签。
- 产品状态为 `draft`、`in_progress`、`pending_acceptance`、`completed`。
- 创建只生成 `draft`；详情中的 Run 显式启动执行。
- 只有 `pending_acceptance` 允许提交互动和验收；其他状态只读。
- Human 互动和验收由 A 调 B 写为协作事件；页面不再组装 `snapshotId`、`lastEventSeq` 或 `executionId`。
- 拒绝验收后待办回到 `in_progress`；后续执行由 B 基于事件推进，前端不再调用 Run 接口。
- 待办执行中且详情打开时轮询事件；进入待验收或完成后停止轮询。
- 事件时序由 A 投影 B 数据，展示事件类型、参与者、内容、状态、Context 版本和产物名称/地址。
- 互动历史是 B `human_interaction` / `human_acceptance` 事件投影，不是 A 独立互动表。

## A 对前端 API

- `GET /openapi/v1/todos`：当前空间、关键词、状态分页查询。
- `POST /openapi/v1/todos`：创建草稿。
- `GET /openapi/v1/todos/{todoId}`：详情。
- `PATCH /openapi/v1/todos/{todoId}`：修改草稿。
- `POST /openapi/v1/todos/{todoId}/run`：无 body，A 调 B 创建协作 Context。
- `GET /openapi/v1/todos/{todoId}/events`：完整事件时序。
- `GET /openapi/v1/todos/{todoId}/interactions`：Human 互动/验收事件投影。
- `POST /openapi/v1/todos/{todoId}/interactions`：`{ message }`。
- `POST /openapi/v1/todos/{todoId}/acceptance`：`{ verdict, message? }`。

## 验收标准

1. 当前空间切换后，列表重新查询当前空间待办。
2. Bot 选择器只展示当前空间 Bot 工坊返回的清单。
3. 创建只调用 create，返回 `draft`，不自动 Run。
4. `draft` 支持编辑和显式 Run；非草稿不显示编辑/Run。
5. `in_progress` 详情打开时轮询 Todo 与事件，状态变化同步列表。
6. 页面展示事件时序和产物名称/地址，事件随 `event_seq` 排序。
7. `pending_acceptance` 可互动和验收；其他状态前端拒绝提交且后端再次校验。
8. 互动请求只带 message；拒绝验收只调 acceptance，不再触发 Run。
9. 加载失败或无数据时展示统一错误/空状态和重试。
