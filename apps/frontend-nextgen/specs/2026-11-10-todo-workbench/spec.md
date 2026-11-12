# 待办工作台（当前空间、事件与验收）

Status: **approved**

## 范围

实验室 `/lab/todos` 提供产品化待办工作台。既有 `/lab/tasks` 任务页保持不变，继续承载跨 Bot、跨群协同任务。待办绑定当前空间和单个 Bot；创建后直接进入待处理并由系统自动调度。列表使用按状态分组的卡片，点击卡片进入独立详情页，详情集中展示事件、互动和产物。

## 已确认行为

- 待办是独立能力，不替换、不复用或改变既有任务页面、任务路由和任务数据契约。
- 页面只读取全局当前空间，不提供空间创建、邀请成员、加入空间或成员管理。
- Bot 清单直接读取当前空间 Bot 工坊列表；本期不做多 Bot、并行分支和团队知识库。
- 产品状态为 `pending`、`in_progress`、`pending_acceptance`、`completed`，不存在草稿态。
- 创建直接生成 `pending`；前端不显式调用 Run，由 Scheduler 自动调度。
- 列表按“待处理、进行中、已完成”分组展示小卡片；`pending` 与 `pending_acceptance` 都属于需要用户关注的“待处理”分组，卡片仍保留真实状态标签。
- 点击待办卡片进入 `/lab/todos/:todoId` 独立详情页，不使用表格“查看”操作或右侧详情抽屉。
- 详情页以“事件、互动、产物”三个视图为主；基本信息收进可折叠的“详情”。
- 只有 `pending_acceptance` 允许提交互动和验收；其他状态只读。
- Human 互动和验收由 A 调 B 写为协作事件；页面不组装 `snapshotId`、`lastEventSeq` 或 `executionId`。
- 拒绝验收后由 B 基于事件推进后续执行，前端不调用 Run 接口。
- 待办执行中且详情打开时轮询事件；进入待验收或完成后停止轮询。
- 事件按 `event_seq` 展示时间线；事件内可直接展示关联产物，产物视图汇总全部产物名称和地址。
- 互动历史是 B Human 事件投影，不是 A 独立互动表。

## A 对前端 API

- `GET /openapi/v1/todos`：按当前空间分页查询，支持关键词、状态和负责人（Bot）筛选。
- `POST /openapi/v1/todos`：创建待处理待办。
- `GET /openapi/v1/todos/{todoId}`：详情。
- `GET /openapi/v1/todos/{todoId}/events`：完整事件时序。
- `GET /openapi/v1/todos/{todoId}/interactions`：Human 互动/验收事件投影。
- `POST /openapi/v1/todos/{todoId}/interactions`：`{ message }`。
- `POST /openapi/v1/todos/{todoId}/acceptance`：`{ verdict, message? }`。
- `POST /openapi/v1/todos/{todoId}/run`：仅作为自动调度补偿接口，不在普通前端流程中暴露。

## 验收标准

1. 当前空间切换后，列表重新查询当前空间待办。
2. Bot 选择器只展示当前空间 Bot 工坊返回的清单。
3. 创建只调用 create，返回 `pending`，前端不自动 Run。
4. 列表以小卡片按状态分组展示，不出现表格“查看”列。
5. 点击卡片进入独立详情路由，详情包含事件、互动、产物三个视图。
6. `in_progress` 详情打开时轮询 Todo 与事件，状态变化同步详情。
7. 页面按 `event_seq` 展示事件，并在事件与产物视图中展示产物名称/地址。
8. `pending_acceptance` 可互动和验收；其他状态前端拒绝提交且后端再次校验。
9. 互动请求只带 message；拒绝验收只调 acceptance，不触发 Run。
10. 加载失败或无数据时展示统一错误/空状态和重试。
