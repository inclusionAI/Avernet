# 实验室待办第一批对齐调研

> 状态：Confirmed
> 日期：2026-11-26
> 分支：`todo-product-protocol-separation-1125`
> 本批范围：Dima 单向批量导入、Bot 小队、Todo 六状态与对应页面

## 1. 结论

本批不重建 Todo 的流程存储，也不因产品名称从“模板”改为“Bot 小队”而调整数据库。

现有 Template/Version/Stage 已具备小队 V1 所需的主要物理能力：空间归属、草稿与发布版本、发布后不可变、有序阶段、参与角色绑定、Todo 创建时展开阶段实例。因此本批采用：

- 产品、API、Frontend 使用 `BotTeam` / “Bot 小队”命名；
- Backend 增加 BotTeam facade，将产品协议映射到现有 Template 能力；
- 数据库继续使用 `template_id`、`template_version_id`、`ac_todo_stage_instance` 等现有结构；
- 不新增同义字段、不物理重命名、不回填历史数据、不删除旧 Template API；
- 新 Frontend 不再暴露 Template 产品入口。

`template_id` 名称与“小队”不一致只是存储实现细节，不构成本批修改表的理由。未来若要物理重命名或清理，需单独评审调用方、线上数据和发布顺序。

## 2. 产品基线

### 2.1 Todo

- 正文 V1 继续使用现有单一 `description` 文本。
- 执行方可选择当前空间的单 Bot 或 Bot 小队。
- Todo 创建后不支持修改标题、正文、执行方和负责人。
- 所有空间成员可查看 Todo；互动协议不在本批重写。
- 审批只允许 Todo 负责人执行。

### 2.2 Bot 小队

- 属于一个空间。
- V1 只支持有序线性节点。
- 支持创建、查看、编辑生成新 revision、归档。
- 已发布 revision 不可变；Todo 创建时固定 revision 和阶段快照。
- 节点字段至少包括：`code`、`name`、`bot`、`approval_required`、`completion_gate`。

### 2.3 Dima 导入

- 使用当前登录 Human 身份访问 Dima MCP。
- 支持容器筛选、搜索、分页、多选；单批最多 20 条。
- 导入时为 Todo 选择单 Bot 或 Bot 小队，并指定负责人。
- 每条独立返回 `imported | duplicate | failed`；单条失败不回滚其他成功项。
- 重复项返回已有 Todo ID。
- 只做单向导入，不回写、不持续同步。

### 2.4 Frontend

- 对齐 Backend 六状态：`not_started | in_progress | interrupted | pending_action | completed | cancelled`。
- 未知状态必须显式报协议错误，不能静默降级为旧 `pending`。
- 本批按原型调整 Todo 列表、创建、Dima 导入和 Bot 小队页面。
- Progress、Interaction 只保留真实组件边界和空态；没有正式协议前不构造假数据或临时字段。

## 3. 当前实现证据

### 3.1 可直接复用

| 能力 | 代码证据 | 本批结论 |
| --- | --- | --- |
| 模板及不可变版本 | `core/task/todos/template_management_*`、`services/todo_template_service.py` | 作为 BotTeam 物理存储和管理能力 |
| 有序阶段定义 | `template_models.py`、`template_validation.py` | 一个 BotTeam node 映射为一个 Bot stage |
| Todo 阶段快照 | `stage_models.py`、`services/todo_stage_service.py` | 创建 Todo 时继续展开 `ac_todo_stage_instance` |
| 参与者绑定 | `participant_bindings_json` | 保存 `todo_owner`，不新增 owner 列 |
| Dima MCP 访问 | `services/dima_source_client.py`、`services/todo_source_service.py` | 保持当前登录 Human 身份透传 |
| 来源防重 | `ac_todo_source_binding`、`TodoSourceAlreadyImportedError` | duplicate 返回已有 Todo ID |
| Backend 六状态 | `core/task/todos/models.py` | Frontend 直接对齐 |

### 3.2 产品字段到现有存储的映射

| 产品协议 | 现有物理字段/模型 |
| --- | --- |
| `bot_team_id` | `template_id` |
| `bot_team_revision_id` | `template_version_id` |
| `bot_team_name_snapshot` | `template_name_snapshot` |
| BotTeam node | `TodoStageDefinition` |
| Todo node snapshot | `ac_todo_stage_instance` |
| Todo owner | `participant_bindings_json` 中 `todo_owner` Human binding |
| Todo body | `ac_todo.description` |

历史 Todo 没有 `todo_owner` binding 时，读取层可回退展示 `created_by`；不回填历史数据。

### 3.3 `approval_required` 映射

不新增数据库字段。BotTeam facade 将一个需要审批的产品节点展开为：

1. Bot stage：执行节点工作；
2. Human stage：code 为 `<node_code>__approval`，执行角色为 `todo_owner`。

读取 BotTeam revision 时，facade 将这个自动生成的 Human stage 折叠回前一个产品节点的 `approval_required=true`。这样审批继续复用现有 Stage lifecycle，且产品协议不暴露内部展开细节。

## 4. 本批范围

1. BotTeam 产品 facade 与 HTTP API，底层复用 Template/Version/Stage。
2. 单 Bot/BotTeam 创建 Todo，固定 revision、阶段快照和负责人 binding。
3. Dima 单向批量导入，最多 20 条，逐条隔离和防重。
4. Frontend 六状态和未知状态 fail-visible。
5. Todo、Dima、Bot 小队相关页面按原型重构。
6. Progress/Interaction 仅建立真实组件边界，不接入未定义协议。

## 5. 明确不做

- 不新增 BotTeam、Node、owner 的同义表或列；
- 不重命名或删除 `template_id`、Template/Stage 表；
- 不回填历史 Todo；
- 不删除旧 Template API；
- 不实现 DAG、并行、分支、循环；
- 不实现 Dima 回写或持续同步；
- 不实现新的 Progress/Interaction Runtime 协议；
- 不实现自动化、通知、动作卡或 Todo 创建后编辑。

## 6. 绑定需求

- `2026111900120790386`：手动选择 Dima
- `2026111900120789898`：AI coding 场景产品 profile
- `2026112400120935398`：20 个真实 Dima 导入及预发 Bot 配置验证

公共仓库只记录工作项 ID 和标题，不记录私有链接、凭据或运行时状态。
