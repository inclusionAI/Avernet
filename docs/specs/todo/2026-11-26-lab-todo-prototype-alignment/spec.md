# 实验室待办第一批对齐 Spec

> 状态：Confirmed
> 日期：2026-11-26
> Research：[`research.md`](research.md)
> Design：[`design.md`](design.md)

## 1. 目标

第一批交付 Dima 单向批量导入和 Bot 小队，并让 Todo 页面使用统一执行方与六状态协议。实现优先复用既有 Template/Version/Stage 存储，不因产品命名变化调整数据库。

## 2. 统一术语

| 术语 | 定义 |
| --- | --- |
| Todo | 一次具体工作实例，创建后核心字段不可编辑 |
| 执行方 | 单 Bot 或 Bot 小队 |
| Bot 小队 | 当前空间内按固定顺序执行的 Bot 节点集合 |
| revision | Bot 小队一次已发布且不可变的定义版本 |
| 负责人 | Todo 的 Human 负责人，也是审批节点允许执行者 |
| Dima 导入 | 将当前用户可见的 Dima 工作项单向复制为 Todo |

## 3. 产品协议

### 3.1 执行方

```json
{
  "type": "bot | bot_team",
  "id": "string",
  "revision_id": "string | null"
}
```

- `bot` 不需要 revision；
- `bot_team` 必须固定已发布 revision；
- 执行方与 Todo 必须属于同一空间或在该空间可用。

### 3.2 Bot 小队节点

```json
{
  "code": "requirement_analysis",
  "name": "需求分析",
  "bot_id": "bot-id",
  "approval_required": false,
  "completion_gate": null
}
```

V1 数组顺序即执行顺序，不接受 DAG、并行、分支或循环字段。

### 3.3 Dima 批量导入结果

```json
{
  "source_work_item_id": "2026...",
  "status": "imported | duplicate | failed",
  "todo_id": "optional",
  "error_code": "optional",
  "message": "optional"
}
```

## 4. 验收点

### AC-001：BotTeam facade 复用现有物理结构

Backend 对外提供 BotTeam 产品模型和 API，但持久化继续映射到 `ac_todo_template`、`ac_todo_template_version`、`ac_todo_stage_instance` 及 Todo 现有 template 字段。

### AC-002：无命名驱动的 DDL 或历史回填

本批不新增 BotTeam/Node/owner 同义表列，不重命名或删除现有 Template/Stage 表列，不回填历史 Todo；旧 Template API 保持兼容，新 Frontend 不暴露 Template 产品入口。

### AC-003：Bot 小队生命周期和线性节点

空间用户可列表、创建、查看、编辑和归档 Bot 小队；创建自动发布首 revision，编辑生成并发布新 revision，已发布 revision 不可变。节点顺序稳定、code 唯一、Bot 可用性受校验。`approval_required=true` 通过 Bot stage + `todo_owner` Human approval stage 表达。

### AC-004：Bot/BotTeam 创建 Todo 并固定快照

创建 Todo 时可选单 Bot 或 BotTeam，并指定负责人。BotTeam 创建固定 `template_id/template_version_id/template_name_snapshot` 并展开阶段实例；负责人保存为 `todo_owner` participant binding。后续编辑 BotTeam 不影响已创建 Todo。

### AC-005：Dima 最多 20 条单向批量导入

当前登录用户可搜索、按容器筛选并分页查看 Dima 工作项，一次选择不超过 20 条，按单 Bot/BotTeam 和负责人导入。每条独立执行并返回 `imported | duplicate | failed`；重复项返回已有 Todo ID；任一失败不回滚其他成功项；不回写 Dima。

### AC-006：Frontend 精确支持六状态

Frontend 只接受 `not_started | in_progress | interrupted | pending_action | completed | cancelled`；状态标签、筛选和详情判断一致。Backend 返回未知状态时抛出可见协议错误，不得回退为 `pending`。

### AC-007：原型页面和统一执行方选择器

Todo 列表、创建、Dima 导入和 Bot 小队页面按原型信息架构实现。执行方选择器分组展示“当前空间 Bot”和“当前空间 Bot 小队”，创建及导入复用同一产品协议。

### AC-008：Progress/Interaction 不接假协议

本批详情页可保留 Progress、Interaction 的真实现有数据或明确空态，但不得发明 Node Attempt Step、Dialog Turn 等未确认接口，不得用前端假数据固化未来协议。

## 5. 状态协议

Frontend 与 Backend 使用同一枚举：

| 值 | 展示 |
| --- | --- |
| `not_started` | 未开始 |
| `in_progress` | 进行中 |
| `interrupted` | 已中断 |
| `pending_action` | 待处理 |
| `completed` | 已完成 |
| `cancelled` | 已取消 |

## 6. 权限

- Todo 和 Bot 小队均按 tenant/env/space 隔离。
- 所有空间成员可查看 Todo。
- Bot 小队管理沿用现有 Template 管理权限和空间鉴权。
- Todo 审批只允许 `todo_owner`。
- Dima MCP 调用使用当前登录 Human 身份。
- HTTP adapter 继续显式传递 `user_id`，Backend 必须再次鉴权。

## 7. 非目标

- Template/Stage 物理迁移或清理；
- Todo 创建后编辑；
- Dima 双向同步；
- Bot 小队 DAG/并行/分支/循环；
- Progress、Interaction、通知、动作卡、自动化的新协议和持久化。
