# 任务主动发现模块 (task_discovery)

- **日期**: 2026-08-18
- **状态**: 已落地（mock 数据驱动；真实数据源待接入）
- **代码权威源**: Avernet 仓 `src/backend/src/agentclaw/community/core/task/task_discovery/`
- **关联框架**: `src/backend/specs/2026-08-09-task-goal-driven-execution-framework/`（任务目标驱动执行框架；task_discovery 是其上游——发现待执行任务后交执行框架处理）

---

## 1. 背景 (WHY)

任务目标驱动执行框架（`core/task/`）已具备完整的"理解→规划→派发→执行→验收→重规划"闭环编排能力，但缺少**任务来源**：框架的入口是 `TaskService.execute(task_info)` ——调用方需手动构造 `TaskInfo` 传入。

实际业务中，大量待执行任务并非用户主动发起，而是从用户行为历程、消息管线、业务系统等数据中**挖掘**得出的。需要一个独立模块负责：

1. **读取** 已发现的待确认任务数据（当前 mock，未来对接真实数据源）
2. 为每个任务**创建 engine session**（不触发执行，等用户确认）
3. **通知** 用户（session_url），用户在 engine 前端查看任务详情后决定是否执行
4. 用户确认后，走执行框架的 `TaskService.execute` 完成任务

task_discovery 与执行框架**解耦**：本模块只负责"发现→通知"，不负责"确认→执行"。两者经 `TaskInfo` + engine session 衔接。

---

## 2. 目标 (WHAT)

### G1. 核心领域模型（transport-agnostic）

| 模型 | 职责 |
|---|---|
| `DiscoveredTask` | 已发现但尚未执行的待确认任务投影（frozen dataclass）。字段：task_id / project_name / description / business_scenario / discovery_basis / work_item_url / priority / discovered_at / status |
| `DiscoverySession` | 发现流程中创建的 engine session 信息（task_id / session_id / session_url / created_at） |
| `DiscoveryResult` | 单次发现流程的结果（task + session + notification_message + error） |

`DiscoveredTask.status` 状态枚举：

| status | 语义 |
|---|---|
| `pending_confirmation` | 待用户确认（默认） |
| `confirmed` | 用户已确认，待执行 |
| `executing` | 已提交执行框架 |
| `ignored` | 用户忽略 |

### G2. 编排服务 `DiscoveryService`

编排完整流程：
1. `TaskReader.read_pending_tasks()` 读取待确认任务
2. 对每个任务，`SessionCreator.create_session(task, user_id, agent_id)` 创建 engine session
3. 生成通知消息（任务详情 + session_url）
4. 返回 `list[DiscoveryResult]`

- 不负责任务执行（由执行框架 `TaskService.execute` 负责）
- `create_default_service(data_file, engine_base_url, engine_frontend_url)` 工厂方法构建默认配置

### G3. 数据读取 `TaskReader`（Protocol seam，可插拔）

| 实现 | 数据源 | 用途 |
|---|---|---|
| `SqliteTaskReader` | 本地 SQLite db（`discovered_tasks` 表） | 默认实现；e2e 测试用 `init_discovered_tasks_db` 写入确定性 mock |
| `MockTaskReader` | 本地 JSON 文件 | 向后兼容 |

未来替换为真实数据源（消息管线、行为分析平台等），只需实现同一 `TaskReader` Protocol。

### G4. Session 创建 `SessionCreator`（Protocol seam，可插拔）

| 实现 | 数据源 | 用途 |
|---|---|---|
| `EngineSessionCreator` | engine HTTP API `POST /api/sessions` | 默认实现 |

- 调用 engine 创建 session（title=项目名称，extInfo=完整任务详情）
- 创建后构建 `session_url` 供用户在浏览器中打开确认
- 与 cron `run-single` 的区别：只创建 session 不触发执行

### G5. 生命周期调度 `TaskDiscoveryLifecycle`

- 继承 `LifecycleBase`，由 DI 容器自动发现
- `startup()` 调度每日定时任务（默认 11:00）
- `shutdown()` 取消调度
- 定时触发时：遍历 `BotService.list_bots()` 所有用户 bot → 为每个 bot 执行发现流程
- `@inject` 注入 `BotService`

### G6. HTTP 适配层（thin，无需认证）

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/public/task-discovery/discover` | POST | 手动触发任务发现（query: user_id, agent_id） |
| `/api/public/task-discovery/status` | GET | 查看任务发现状态（从 SQLite db 读取） |

- 参考 `cron_noauth_router` 模式：无需认证的公开端点
- Router 只翻译协议，不持领域策略

### G7. DI 接线 `TaskDiscoveryModule`

- 绑定 `TaskDiscoveryLifecycle` 为 singleton
- Lifecycle 参与者由 `discover_lifecycle_participants` 自动发现
- `BotService` 由 `BotManagementModule` 绑定，`@inject` 自动注入

### G8. 端到端集成用例

- `tests/.../task/singlebox_e2e/test_task_discovery_e2e.py`
- gated（`SINGLEBOX_TASK_E2E=1` 启用真实 singlebox e2e）
- 验证：discover → status → session 可达全链路

---

## 3. 非目标 (Non-Goals)

- **不**实现任务执行：确认后执行由执行框架 `TaskService.execute` 负责，不在本模块内
- **不**对接真实数据源：当前 mock（SQLite/JSON），真实消息管线/行为分析平台属后续
- **不**做用户确认回调处理：确认态翻转/执行触发由前端+执行框架衔接
- **不**做前端页面：session_url 指向已有 engine 前端 SessionOnlyPage 路由

---

## 4. 范围与形态 (Scope)

### 已落地代码

| 层 | 路径 | 内容 |
|---|---|---|
| core | `core/task/task_discovery/models.py` | `DiscoveredTask` / `DiscoverySession` |
| core | `core/task/task_discovery/discovery_service.py` | `DiscoveryService` / `DiscoveryResult` / `create_default_service` |
| core | `core/task/task_discovery/task_reader.py` | `TaskReader` Protocol / `SqliteTaskReader` / `MockTaskReader` / `init_discovered_tasks_db` |
| core | `core/task/task_discovery/session_creator.py` | `SessionCreator` Protocol / `EngineSessionCreator` |
| core | `core/task/task_discovery/lifecycle.py` | `TaskDiscoveryLifecycle` |
| adapters/http | `adapters/http/task_discovery/router.py` | POST /discover + GET /status |
| di/modules | `di/modules/task_discovery_module.py` | `TaskDiscoveryModule` (singleton bind) |
| tests | `tests/.../task/singlebox_e2e/test_task_discovery_e2e.py` | gated e2e |

### 触发方式

| 方式 | 入口 | 说明 |
|---|---|---|
| A. 自动 | `TaskDiscoveryLifecycle` startup 定时调度 | 每日 11:00 遍历所有 bot |
| B. 手动 | HTTP `POST /api/public/task-discovery/discover` | CLI / 外部调度器触发 |
| C. CLI | `scripts/task_discovery.sh discover` → curl backend API | 本地脚本 |

---

## 5. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TASK_DISCOVERY_AUTO_START` | `true` | 是否启用自动调度 |
| `TASK_DISCOVERY_SCHEDULE_HOUR` | `11` | 调度小时 |
| `TASK_DISCOVERY_SCHEDULE_MINUTE` | `0` | 调度分钟 |
| `TASK_DISCOVERY_ENGINE_URL` | `http://localhost:20003` | Engine API 地址 |
| `TASK_DISCOVERY_FRONTEND_URL` | `http://localhost:8000` | 前端 workbench 地址 |
| `TASK_DISCOVERY_DATA_FILE` | `scripts/.dependencies/data/discovered_tasks.db` | 任务数据文件路径 |

---

## 6. 利益相关者与约束

| 角色 | 关注点 |
|---|---|
| 框架维护者 | Protocol seam 可插拔；transport-agnostic；与执行框架解耦 |
| corp 接入者 | `TaskReader` / `SessionCreator` Protocol 替换真实数据源即可 |
| singlebox/CI | e2e gated，不拖慢默认 CI |
| 微内核宪法 | DI 接线、thin router、lifecycle 参与者自动发现 |

### 硬约束
- core 层 transport-agnostic（禁 transport import）
- HTTP adapter thin（只转协议，不持领域策略）
- 任务数据源经 `TaskReader` Protocol 隔离，不硬编码到 core
- engine session 创建经 `SessionCreator` Protocol 隔离

---

## 7. 成功标准（验收条件）

- **AC-1**：`DiscoveryService.discover()` 能读取待确认任务 → 为每个任务创建 engine session → 返回含 session_url 的 `DiscoveryResult` 列表。
- **AC-2**：`TaskDiscoveryLifecycle` 在 backend startup 后自动调度，每日定时遍历所有 bot 执行发现；`shutdown()` 正确取消。
- **AC-3**：HTTP `POST /api/public/task-discovery/discover` 手动触发返回结构化结果（discovered count + tasks[]）；`GET /status` 返回任务列表。
- **AC-4**：`SqliteTaskReader` + `init_discovered_tasks_db` 能写入/读取确定性 mock 数据；`MockTaskReader` 向后兼容 JSON。
- **AC-5**：`EngineSessionCreator` 调 engine `POST /api/sessions` 创建 session，构建 session_url 格式正确。
- **AC-6**：e2e 用例 gated（`SINGLEBOX_TASK_E2E=1`），默认不跑；开启后 discover → status → session 可达全链路跑通。
- **AC-7**：DI 接线正确——`TaskDiscoveryModule` 绑定 singleton，`TaskDiscoveryLifecycle` 自动发现并启动。

---

## 8. 风险与开放问题

- **R1 数据源**：当前 mock 数据无真实行为分析。后续需替换 `TaskReader` 为真实数据源（消息管线/行为分析平台），Protocol seam 已留。
- **R2 确认回调**：用户在 engine session 中确认后如何触发执行框架 `TaskService.execute`，本模块未实现该衔接。需后续设计 task_discovery → execution_framework 的桥接。
- **R3 状态持久化**：`DiscoveredTask.status` 当前仅存在 mock db 中，无独立状态管理服务。真实化时需引入状态存储。
- **R4 并发**：定时调度遍历所有 bot，当前串行。bot 数量增长时需评估并发上限。
- **R5 session_url 路由**：依赖前端 `SessionOnlyPage` 路由的 query 参数约定（`bot_uuid`/`id`/`session`），前端路由变更需同步。