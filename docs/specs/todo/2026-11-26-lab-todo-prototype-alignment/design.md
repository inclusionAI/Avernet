# 实验室待办第一批对齐技术设计

> 状态：Confirmed
> 日期：2026-11-26
> Spec：[`spec.md`](spec.md)

## 1. 核心决策

1. BotTeam 是产品 facade，不是本批新增的物理聚合。
2. 继续使用 Template/Version/Stage 表和 Todo 现有 template 字段。
3. 不做 DDL、不回填、不物理重命名、不删除旧接口。
4. BotTeam API/Domain/Frontend 使用产品命名，Service 内部负责映射。
5. Dima 批量导入为同步批处理；单批最多 20 条，无需新增 import job 表。
6. Progress/Interaction 不接临时 Runtime 协议。

## 2. 分层

```text
Frontend BotTeam/Dima/Todo
  -> HTTP schemas + routers
    -> Service API
      -> TodoBotTeamService / TodoDimaImportService
        -> existing TodoTemplate services / Todo creation / source services
          -> existing repositories and tables
```

- Core 不依赖 HTTP schema。
- HTTP adapter 显式传递 actor、tenant、env、space。
- Repository 保持 Template/Stage 术语，因为它描述当前物理存储。
- 产品命名转换仅位于 BotTeam facade、Service API 和 HTTP/Frontend 边界。

## 3. BotTeam 映射

### 3.1 标识映射

| BotTeam 产品字段 | Template 物理字段 |
| --- | --- |
| `bot_team_id` | `template_id` |
| `bot_team_revision_id` | `template_version_id` |
| `bot_team_name` | template name |
| `bot_team_name_snapshot` | `template_name_snapshot` |
| ordered node | `TodoStageDefinition` |
| Todo node snapshot | `ac_todo_stage_instance` |

BotTeam response 不直接返回 `template_*` 字段；旧 Template API 仍返回旧协议。

### 3.2 节点展开

普通节点展开为一个 Bot stage：

```text
stage_code       = node.code
stage_name       = node.name
stage_kind       = bot
executor_role    = generated role for node.bot_id
completion_gate  = node.completion_gate
```

`approval_required=true` 时，紧跟一个内部 Human stage：

```text
stage_code       = <node.code>__approval
stage_name       = <node.name>审批
stage_kind       = human
executor_role    = todo_owner
```

反向读取时，仅当 Human stage 满足上述受控约定且紧跟对应 Bot stage，才折叠为 `approval_required=true`；不吞掉用户自定义的其他 Human stage。

### 3.3 创建、编辑和归档

创建：

1. 校验空间、名称、节点顺序、唯一 code 和 Bot 可用性；
2. 调用现有 Template management service 创建 template；
3. 写入并校验首个 draft；
4. 发布首个 revision；
5. 返回 BotTeam 产品 DTO。

编辑：

1. 读取当前 active BotTeam；
2. 创建下一 draft；
3. 以新节点定义更新 draft；
4. 校验并发布；
5. 历史 revision 保持不可变。

归档直接复用 template archive；归档后不可用于新 Todo，历史 Todo 仍可读。

## 4. Todo 创建

### 4.1 单 Bot

继续使用现有单 Bot 创建链路；统一执行方 DTO 在 adapter/service 层转换为现有创建命令。

### 4.2 BotTeam

1. 将 `bot_team_id/revision_id` 映射为 `template_id/template_version_id`；
2. 负责人转换为 required participant binding：

```json
{
  "key": "todo_owner",
  "subject_type": "human",
  "subject_id": "...",
  "subject_name": "..."
}
```

3. 调用现有 `create_todo_from_template`；
4. 原子保存 Todo template 快照、participant bindings 和 stage instances；
5. 产品响应再映射为 BotTeam 命名。

老 Todo 没有 `todo_owner` 时，读取模型回退展示 `created_by`，但不修改数据库历史行。

## 5. Dima 批量导入

新增应用服务：

```text
TodoDimaImportServiceProtocol
TodoDimaImportService
```

请求包含：

```text
space_id
items[1..20]: source_work_item_id + executor + owner
actor context
```

逐条流程：

1. 使用当前 actor 身份加载 Dima work item draft；
2. 根据 executor 走单 Bot 或 BotTeam Todo 创建；
3. 保存来源白名单快照和 `ac_todo_source_binding`；
4. `TodoSourceAlreadyImportedError` 转换为 `duplicate` 并返回 existing Todo ID；
5. 其他正式领域异常转换为当前条 `failed`；
6. 已成功条目不因其他条失败而回滚。

服务不解析数据库错误字符串。请求超过 20 条、重复 request item、缺失 executor/owner 应在执行前返回结构化校验错误。

本批使用同步批处理，不新增 job 表。20 条之外的异步、重试和导入进度属于后续需求。

## 6. HTTP API

### 6.1 BotTeam

```text
GET  /openapi/v1/collaboration/tasks/bot-teams
POST /openapi/v1/collaboration/tasks/bot-teams
GET  /openapi/v1/collaboration/tasks/bot-teams/{bot_team_id}
PUT  /openapi/v1/collaboration/tasks/bot-teams/{bot_team_id}
POST /openapi/v1/collaboration/tasks/bot-teams/{bot_team_id}/archive
```

新 contract 必须提供 schema description、显式 `user_id`、权限 inventory 和 admission inventory 测试。旧 `/todo-templates` route 保留。

### 6.2 Dima 批量导入

在现有 Todo source route 下增加批量导入 endpoint，复用既有项目、列表和 draft 查询能力。具体路径以当前 router 命名约定为准，避免引入第二套 source 根路径。

## 7. Frontend

### 7.1 六状态

- `TodoStatus` 改为 Backend 六状态的精确 union。
- mapper 对未知状态抛出协议错误并保留原始值用于诊断。
- status label/color/filter、看板/列表和详情判断共用一个映射模块。
- 删除 `pending_acceptance` 和旧 `pending` 的产品判断。

### 7.2 页面

- Todo 列表：标题、状态、执行方、负责人、来源/创建方式、创建时间、完成时间。
- 创建页：标题、单一正文、执行方、负责人。
- 执行方选择器：分组加载当前空间 Bot 与 BotTeam。
- BotTeam：列表、创建/编辑线性节点、归档、revision 只读信息。
- Dima：容器、搜索、分页、多选、批量设置或逐条设置 executor/owner、结果汇总。
- Progress/Interaction：仅使用当前真实接口；能力不存在时展示明确空态，不生成假 node/dialog 数据。

## 8. 兼容与数据库策略

本批数据库变更为 **无**。

必须保持：

```text
ac_todo_template
ac_todo_template_version
ac_todo_stage_instance
ac_todo.template_id
ac_todo.template_version_id
ac_todo.template_name_snapshot
ac_todo.current_stage_instance_id
ac_todo.participant_bindings_json
```

兼容规则：

- 旧 Template API 和现有调用方继续工作；
- BotTeam facade 与旧 API 共享同一数据，不双写两套表；
- 不因字段名称不符合产品词汇做迁移；
- 不回填历史 owner 或 BotTeam 字段；
- 未来清理必须作为独立变更，包含数据盘点、调用方盘点和回滚方案。

## 9. 设计覆盖矩阵

| AC | 设计覆盖 | 主要验证 |
| --- | --- | --- |
| AC-001 | §2、§3 | facade/domain/service/API tests |
| AC-002 | §8 | schema diff + compatibility tests |
| AC-003 | §3 | revision immutability、node validation、approval folding tests |
| AC-004 | §4 | create integration、snapshot、owner binding tests |
| AC-005 | §5 | 20-item mixed result、duplicate、failure isolation tests |
| AC-006 | §7.1 | mapper/status focused frontend tests |
| AC-007 | §7.2 | component/service/typecheck tests |
| AC-008 | §7.2 | contract review + no fake fixture checks |
