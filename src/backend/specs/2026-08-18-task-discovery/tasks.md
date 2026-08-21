# Tasks — 任务主动发现模块 (task_discovery)

> 状态：已落地。本清单用于归档已实现内容 + 标注后续待办。
> 落点：全在 Avernet `src/backend`；core 层 transport-agnostic；Protocol seam 可插拔。

## 实现原则（对齐 AGENTS.md + 微内核宪法）

- **契约先行 / transport-agnostic**：core 层禁 transport import；`TaskReader` / `SessionCreator` 经 Protocol 隔离外部 IO。
- **thin adapter**：HTTP router 只转协议（`discover` / `status`），不持领域策略。
- **composition root**：`TaskDiscoveryModule` 是唯一接线点；lifecycle 自动发现。
- **Protocol seam**：数据源和 session 创建可插拔，corp 替换真实实现只需实现 Protocol。
- **与执行框架解耦**：task_discovery 产出 `DiscoveredTask` + engine session，用户确认后外部调用 `TaskService.execute`。

---

## G1 — 领域模型（已完成）

- [x] **T1.1** `models.py` — `DiscoveredTask` frozen dataclass（9 字段 + `needs_confirmation` / `to_session_ext_info` / `to_notification_message` 派生方法）
- [x] **T1.2** `models.py` — `DiscoverySession` frozen dataclass（task_id / session_id / session_url / created_at）
- ✅ **G1 验收**：模型纯数据、零 transport 依赖；`to_session_ext_info` 含 `source: "task_discovery"` 标识

---

## G2 — 编排服务 DiscoveryService（已完成）

- [x] **T2.1** `discovery_service.py` — `DiscoveryResult` dataclass（task + session + notification_message + error + `success` property）
- [x] **T2.2** `discovery_service.py` — `DiscoveryService.__init__(reader, session_creator)`；`async discover(user_id, agent_id, model?)` → `list[DiscoveryResult]`
  - `read_pending_tasks()` → 逐 task `_discover_single`（try/except 包裹，失败填 error 不中断）
  - `_discoveries` dict 缓存最近结果供外部查询
- [x] **T2.3** `discovery_service.py` — `print_notifications(results)` → stdout 打印（CLI 用）
- [x] **T2.4** `discovery_service.py` — `create_default_service(data_file, engine_base_url?, engine_frontend_url?)` 工厂方法
- ✅ **G2 验收**：编排逻辑完整；不涉及任务执行；async 签名对齐 backend lifecycle 模式

---

## G3 — 数据读取 TaskReader（已完成）

- [x] **T3.1** `task_reader.py` — `TaskReader` Protocol（`read_discovered_tasks() -> list[DiscoveredTask]`）
- [x] **T3.2** `task_reader.py` — `SqliteTaskReader(db_path)` 实现
  - `_CREATE_TABLE_SQL` DDL（9 字段 + task_id PK）
  - `read_discovered_tasks()` → SELECT + `_row_to_task` 映射
  - `read_pending_tasks()` → 过滤 `needs_confirmation`
- [x] **T3.3** `task_reader.py` — `init_discovered_tasks_db(db_path, tasks)` 工具函数（建表 + 清空 + 批量插入，供 e2e）
- [x] **T3.4** `task_reader.py` — `MockTaskReader(data_file)` JSON 实现（向后兼容）
- ✅ **G3 验收**：Protocol seam 可插拔；两种实现覆盖 SQLite + JSON；`init_discovered_tasks_db` 支持确定性 mock

---

## G4 — Session 创建 SessionCreator（已完成）

- [x] **T4.1** `session_creator.py` — `SessionCreator` Protocol（`async create_session(task, *, user_id, agent_id, model?) -> DiscoverySession`）
- [x] **T4.2** `session_creator.py` — `EngineSessionCreator(engine_base_url?, engine_frontend_url?)` 实现
  - 配置来源：构造器参数 → env `ENGINE_BASE_URL` / `FRONTEND_URL` → 默认 localhost
  - `create_session()` → `POST {engine_url}/api/sessions`（body: title/user_id/agent_id/extInfo/model?）
  - 响应解析：`data.id` 或 `data.session_id` → 构建 `DiscoverySession`
  - `_build_session_url(session_id, agent_id)` → `{frontend}/bcn/chat/session?bot_uuid={agent_id}&id={agent_id}&session={session_id}`
  - 超时 30s（httpx.AsyncClient）；success=False 时 raise RuntimeError
- ✅ **G4 验收**：Protocol seam 可插拔；engine API 调用正确；session_url 格式对齐前端 SessionOnlyPage 路由

---

## G5 — 生命周期调度 TaskDiscoveryLifecycle（已完成）

- [x] **T5.1** `lifecycle.py` — `TaskDiscoveryLifecycle(LifecycleBase)` + `@inject.__init__(bot_service)`
- [x] **T5.2** `startup()` → 读 `TASK_DISCOVERY_AUTO_START`（默认 true）→ `asyncio.create_task(_run_daily_schedule(hour, minute))`
  - 调度时间：`TASK_DISCOVERY_SCHEDULE_HOUR` (11) / `TASK_DISCOVERY_SCHEDULE_MINUTE` (0)
- [x] **T5.3** `shutdown()` → cancel `_task`（CancelledError 兜底）
- [x] **T5.4** `_run_daily_schedule(hour, minute)` → `while True: sleep(_seconds_until) → _discover_once()`
- [x] **T5.5** `_seconds_until(hour, minute)` → 计算到下一个目标时间的秒数（跨日处理）
- [x] **T5.6** `_discover_once()` → `BotService.list_bots(page=1, page_size=100)` → 逐 bot `create_default_service + discover(user_id=owner_id, agent_id=bot_id)` → 日志汇总
- [x] **T5.7** `_list_all_bots()` → BotService 查询（异常兜底返 []）
- [x] **T5.8** `_resolve_data_file()` → env 或默认路径（9 级上溯到项目根）
- ✅ **G5 验收**：lifecycle 自动发现 + 定时调度 + 遍历所有 bot；env 控制开关/时间

---

## G6 — HTTP 适配层（已完成）

- [x] **T6.1** `adapters/http/task_discovery/router.py` — `APIRouter(prefix="/api/public/task-discovery", tags=["task-discovery"])`
- [x] **T6.2** `POST /discover` — query params `user_id` / `agent_id` → `_build_service()` → `service.discover()` → 结构化响应
- [x] **T6.3** `GET /status` — `SqliteTaskReader(db_path).read_discovered_tasks()` → `{success, total, tasks: [...]}`
- [x] **T6.4** `_build_service()` — 从 env `TASK_DISCOVERY_ENGINE_URL` / `TASK_DISCOVERY_FRONTEND_URL` 构建
- [x] **T6.5** `_resolve_db_path()` — 从 env `TASK_DISCOVERY_DATA_FILE` 或默认路径解析
- [x] **T6.6** `app.py` include `task_discovery_router`
- ✅ **G6 验收**：thin router（无领域策略）；无需认证（对齐 cron_noauth 模式）；discover + status 端点可用

---

## G7 — DI 接线（已完成）

- [x] **T7.1** `di/modules/task_discovery_module.py` — `TaskDiscoveryModule(Module)` → `binder.bind(TaskDiscoveryLifecycle, to=..., scope=singleton)`
- [x] **T7.2** `di/container.py` — import + 注册 `TaskDiscoveryModule`
- ✅ **G7 验收**：DI 接线正确；lifecycle 参与者自动发现并启动

---

## G8 — E2E 集成用例（已完成）

- [x] **T8.1** `test_task_discovery_e2e.py` — gated（`@unittest.skipUnless(_LIVE, ...)`）
- [x] **T8.2** 内联 `_MOCK_TASKS`（3 条确定性数据）+ `_write_mock_data()` → `init_discovered_tasks_db`
- [x] **T8.3** `_DATA_FILE` 路径推算（8 级上溯到项目根 + scripts/.dependencies/data/）
- [x] **T8.4** `TestTaskDiscoveryE2E.test_discover_creates_sessions_and_returns_tasks` — discover → status → session 可达
- [x] **T8.5** 断言常量派生（`_EXPECTED_TASK_COUNT` / `_EXPECTED_TASK_IDS` / `_EXPECTED_PROJECT_NAMES`）
- ✅ **G8 验收**：gated 用例可 `SINGLEBOX_TASK_E2E=1` 开启；discover 发现 3 任务 + session_url 可达

---

## 后续待办（spec §8 风险项）

- [ ] **T9.1** 真实数据源接入：替换 `SqliteTaskReader` 为消息管线/行为分析平台实现（Protocol seam 已留）
- [ ] **T9.2** 确认→执行桥接：用户在 engine session 确认后，如何触发 `TaskService.execute(task_info)` 的衔接设计
- [ ] **T9.3** 状态持久化：引入独立状态管理服务，替代 mock db 存储 `DiscoveredTask.status`
- [ ] **T9.4** 并发优化：bot 规模增长时，定时调度从串行改为并发（`asyncio.gather` + Semaphore）
- [ ] **T9.5** CLI 脚本 `scripts/task_discovery.sh`（spec 中提及但仓内未找到）
- [ ] **T9.6** 契约测试：HTTP adapter + `TaskReader` / `SessionCreator` Protocol 契约测（Rule 25）

---

## 验收总览（对齐 spec §7 AC）

- AC-1 ✅ T2.2/T2.4 ｜ AC-2 ✅ T5.2-T5.8 ｜ AC-3 ✅ T6.2/T6.3 ｜ AC-4 ✅ T3.2/T3.3/T3.4
- AC-5 ✅ T4.2 ｜ AC-6 ✅ T8.1-T8.5 ｜ AC-7 ✅ T7.1/T7.2

## 配置项速查

| 环境变量 | 默认值 |
|---|---|
| `TASK_DISCOVERY_AUTO_START` | `true` |
| `TASK_DISCOVERY_SCHEDULE_HOUR` | `11` |
| `TASK_DISCOVERY_SCHEDULE_MINUTE` | `0` |
| `TASK_DISCOVERY_ENGINE_URL` | `http://localhost:20003` |
| `TASK_DISCOVERY_FRONTEND_URL` | `http://localhost:8000` |
| `TASK_DISCOVERY_DATA_FILE` | `scripts/.dependencies/data/discovered_tasks.db` |
| `SINGLEBOX_TASK_E2E` | — (gated) |
| `SINGLEBOX_BACKEND_URL` | `http://localhost:8888` |
| `SINGLEBOX_USER_ID` | `440718` |