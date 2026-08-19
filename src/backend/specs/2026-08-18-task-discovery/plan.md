# 任务主动发现模块 — 技术方案 (HOW)

- **日期**: 2026-08-18
- **依赖**: `spec.md`（同目录）；执行框架 `src/backend/specs/2026-08-09-task-goal-driven-execution-framework/`
- **代码权威源**: Avernet `src/backend/src/agentclaw/community/core/task/task_discovery/`

---

## 0. 模块定位

task_discovery 是 `core/task/` 目录下的独立子模块，与执行框架（`task_center` / `task_graph` / `task_plan` / `task_dispatch` / `task_runner` / `task_harness`）并列但解耦。

```
core/task/
├── task_center/          # 执行框架：ExecutionEngine 编排核
├── task_graph/           # 执行框架：TaskGraphService 图谱 SSOT
├── task_plan/            # 执行框架：TaskPlanner 规划
├── task_dispatch/        # 执行框架：TaskDispatcher 搜推分发
├── task_runner/          # 执行框架：TaskRunner 三模态执行
├── task_harness/         # 执行框架：TaskHarness 旁路巡检
└── task_discovery/       # ← 本模块：任务主动发现（上游，独立）
    ├── models.py          # DiscoveredTask / DiscoverySession
    ├── discovery_service.py  # DiscoveryService 编排
    ├── task_reader.py     # TaskReader Protocol + SqliteTaskReader + MockTaskReader
    ├── session_creator.py # SessionCreator Protocol + EngineSessionCreator
    ├── lifecycle.py       # TaskDiscoveryLifecycle 定时调度
    └── __init__.py
```

**边界**：task_discovery 产出 `DiscoveredTask` → 创建 engine session 通知用户 → 用户确认后，外部调用 `TaskService.execute(task_info)` 进入执行框架。两个模块经 `TaskInfo` + engine session 衔接，无直接代码依赖。

---

## 1. 整体拓扑

```
┌─────────────────────────── backend (FastAPI 8888) ───────────────────────────┐
│                                                                              │
│  adapters/http/task_discovery/router.py  (thin, no-auth)                     │
│    POST /api/public/task-discovery/discover   ← 手动触发                     │
│    GET  /api/public/task-discovery/status      ← 查看状态                    │
│                                                                              │
│  di/modules/task_discovery_module.py                                         │
│    bind(TaskDiscoveryLifecycle, singleton)                                   │
│      ↓ @inject(BotService)                                                   │
│                                                                              │
│  core/task/task_discovery/                                                   │
│    ┌──────────────────────────────────────────────────────────┐              │
│    │ TaskDiscoveryLifecycle                                    │              │
│    │   startup() → asyncio.create_task(_run_daily_schedule)    │              │
│    │   _discover_once():                                       │              │
│    │     BotService.list_bots() → for each bot:                │              │
│    │       create_default_service(data_file, engine, frontend) │              │
│    │       DiscoveryService.discover(user_id, agent_id)        │              │
│    └─────────────────────────┬────────────────────────────────┘              │
│                              │                                               │
│    ┌─────────────────────────┴────────────────────────────────┐              │
│    │ DiscoveryService (编排核心)                                │              │
│    │   reader.read_pending_tasks() → list[DiscoveredTask]      │              │
│    │   for task:                                                │              │
│    │     session_creator.create_session(task, ...)              │              │
│    │     → DiscoveryResult(task, session, notification_message) │              │
│    └──────────┬──────────────────────────┬─────────────────────┘              │
│               │                          │                                    │
│    ┌──────────┴──────────┐    ┌──────────┴──────────────────┐                │
│    │ TaskReader (Proto)  │    │ SessionCreator (Proto)       │                │
│    │  SqliteTaskReader   │    │  EngineSessionCreator        │                │
│    │   ← discovered_tasks │    │   → POST {engine}/api/sessions│               │
│    │     .db (SQLite)     │    │   → session_url build        │                │
│    │  MockTaskReader      │    │                               │                │
│    │   ← tasks.json       │    │                               │                │
│    └─────────────────────┘    └───────────────────────────────┘                │
│                                                                              │
│         engine session creation ─────────────► engine (20003)                 │
│         session_url ────────────────────────► frontend (8000)                 │
└──────────────────────────────────────────────────────────────────────────────┘
         ▲                                      │
         │ HTTP (async httpx)                    │ session_url
┌────────┴──────── singlebox e2e test ──────────┴──────────────────────────────┐
│  test_task_discovery_e2e.py                                                   │
│    setup: init_discovered_tasks_db(mock data → SQLite)                       │
│    drive: POST /discover → GET /status → verify session reachable            │
│    gated: SINGLEBOX_TASK_E2E=1                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 领域模型 (`models.py`)

### 2.1 `DiscoveredTask`

```python
@dataclass(frozen=True)
class DiscoveredTask:
    task_id: str
    project_name: str           # 同时用作 engine session title
    description: str            # 项目简介
    business_scenario: str      # 业务场景描述
    discovery_basis: str        # 挖掘依据 — 行为节点演进链路
    work_item_url: str | None   # 关联需求/工作项 URL
    priority: str = "medium"    # high / medium / low
    discovered_at: str | None   # ISO timestamp
    status: str = "pending_confirmation"
```

派生方法：
- `needs_confirmation` → `status == "pending_confirmation"`
- `to_session_ext_info()` → 序列化为 engine session `extInfo` dict（含 `source: "task_discovery"`）
- `to_notification_message(session_url)` → 生成用户在 engine session 中看到的通知文本

### 2.2 `DiscoverySession`

```python
@dataclass(frozen=True)
class DiscoverySession:
    task_id: str
    session_id: str
    session_url: str
    created_at: str  # default: utcnow().isoformat()
```

---

## 3. 编排服务 (`discovery_service.py`)

### 3.1 `DiscoveryResult`

```python
@dataclass
class DiscoveryResult:
    task: DiscoveredTask
    session: DiscoverySession | None = None
    notification_message: str = ""
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.session is not None and self.error is None
```

### 3.2 `DiscoveryService`

```python
class DiscoveryService:
    def __init__(self, reader: TaskReader, session_creator: SessionCreator):
        self._reader = reader
        self._session_creator = session_creator
        self._discoveries: dict[str, DiscoveryResult] = {}

    async def discover(self, *, user_id: str, agent_id: str, model: str | None = None) -> list[DiscoveryResult]:
        tasks = self._reader.read_pending_tasks()
        results = []
        for task in tasks:
            result = await self._discover_single(task, user_id=user_id, agent_id=agent_id, model=model)
            results.append(result)
            self._discoveries[task.task_id] = result
        return results
```

- `_discover_single(task, ...)` 内部 `try/except`：成功返 `DiscoveryResult(task, session, message)`；失败返 `DiscoveryResult(task, error=str(exc))`
- `print_notifications(results)` → stdout 打印通知消息（供 CLI 输出）
- `create_default_service(data_file, engine_base_url, engine_frontend_url)` → 工厂方法，用 `SqliteTaskReader` + `EngineSessionCreator` 构建

---

## 4. 数据读取 (`task_reader.py`)

### 4.1 Protocol

```python
class TaskReader(Protocol):
    def read_discovered_tasks(self) -> list[DiscoveredTask]: ...
```

### 4.2 `SqliteTaskReader`

- db schema：`discovered_tasks` 表（9 字段，task_id PRIMARY KEY）
- `read_discovered_tasks()` → `SELECT * FROM discovered_tasks` → `_row_to_task` 映射
- `read_pending_tasks()` → 过滤 `needs_confirmation`
- `init_discovered_tasks_db(db_path, tasks)` → 建表 + 清空 + 批量插入（供 e2e 测试）

DDL:
```sql
CREATE TABLE IF NOT EXISTS discovered_tasks (
    task_id            TEXT PRIMARY KEY,
    project_name       TEXT NOT NULL,
    description        TEXT,
    business_scenario  TEXT,
    discovery_basis    TEXT,
    work_item_url      TEXT,
    priority           TEXT DEFAULT 'medium',
    discovered_at      TEXT,
    status             TEXT DEFAULT 'pending_confirmation'
);
```

### 4.3 `MockTaskReader`（JSON，向后兼容）

- JSON 格式：`{"tasks": [{...DiscoveredTask fields...}]}`
- 同样提供 `read_discovered_tasks()` / `read_pending_tasks()`

---

## 5. Session 创建 (`session_creator.py`)

### 5.1 Protocol

```python
class SessionCreator(Protocol):
    async def create_session(self, task: DiscoveredTask, *, user_id: str, agent_id: str, model: str | None = None) -> DiscoverySession: ...
```

### 5.2 `EngineSessionCreator`

- engine API：`POST {engine_base_url}/api/sessions`
- 请求体：`{title, user_id, agent_id, extInfo, model?}`
- 响应：`{success, data: {id | session_id}}`
- `session_url` 构建：`{frontend_url}/bcn/chat/session?bot_uuid={agent_id}&id={agent_id}&session={session_id}`
  - 对齐前端 SessionOnlyPage 路由的三个 query 参数
- 配置来源：构造器参数优先 → 环境变量 `ENGINE_BASE_URL` / `FRONTEND_URL` → 默认 localhost
- 超时：30s（`httpx.AsyncClient(timeout=30.0)`）

---

## 6. 生命周期调度 (`lifecycle.py`)

### 6.1 `TaskDiscoveryLifecycle(LifecycleBase)`

```python
class TaskDiscoveryLifecycle(LifecycleBase):
    @inject
    def __init__(self, bot_service: BotService): ...

    async def startup(self) -> None:
        # TASK_DISCOVERY_AUTO_START != true → skip
        # asyncio.create_task(_run_daily_schedule(hour, minute))

    async def shutdown(self) -> None:
        # cancel _task

    async def _run_daily_schedule(self, hour, minute):
        while True:
            delay = _seconds_until(hour, minute)
            await asyncio.sleep(delay)
            await _discover_once()
```

### 6.2 `_discover_once()` 流程

1. `BotService.list_bots(page=1, page_size=100)` → 所有用户 bot
2. 对每个 bot（有 `bot_id` + `owner_id`）：
   - `create_default_service(data_file, engine_url, frontend_url)`
   - `await service.discover(user_id=owner_id, agent_id=bot_id)`
   - 统计成功/失败
3. 日志输出汇总

### 6.3 自动发现机制

- 由 `discover_lifecycle_participants` 自动发现（与 `CronAutoSetupListener` 走相同机制）
- DI 容器 `TaskDiscoveryModule` 绑定 singleton
- `BotService` 已由 `BotManagementModule` 绑定，`@inject` 自动注入

---

## 7. HTTP 适配层 (`adapters/http/task_discovery/router.py`)

```python
router = APIRouter(prefix="/api/public/task-discovery", tags=["task-discovery"])

@router.post("/discover")
async def discover_tasks(user_id: str = Query("default"), agent_id: str = Query("bot_001")) -> dict:
    # _build_service() → service.discover(user_id, agent_id)
    # 返回 {success, discovered, tasks: [{task_id, project_name, success, session_id?, session_url?, error?}]}

@router.get("/status")
async def get_status() -> dict:
    # SqliteTaskReader(db_path).read_discovered_tasks()
    # 返回 {success, total, tasks: [{task_id, project_name, status, priority}]}
```

- 参考 `cron_noauth_router` 模式：无需认证
- `_build_service()` 从环境变量构建 `DiscoveryService`
- `_resolve_db_path()` 从 `TASK_DISCOVERY_DATA_FILE` 或默认路径解析

### App include

```python
# app.py
from ...task_discovery.router import router as task_discovery_router
app.include_router(task_discovery_router)
```

---

## 8. DI 接线 (`di/modules/task_discovery_module.py`)

```python
class TaskDiscoveryModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(TaskDiscoveryLifecycle, to=TaskDiscoveryLifecycle, scope=singleton)
```

DI 容器 (`container.py`) 注册：
```python
from ...task_discovery_module import TaskDiscoveryModule
# 在 modules 列表中加入 TaskDiscoveryModule
```

---

## 9. E2E 集成用例 (`test_task_discovery_e2e.py`)

### 9.1 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `SINGLEBOX_TASK_E2E` | — | gated 开关 |
| `SINGLEBOX_BACKEND_URL` | `http://localhost:8888` | backend 地址 |
| `SINGLEBOX_USER_ID` | `440718` | 测试用户 ID |
| `TASK_DISCOVERY_ENGINE_URL` | `http://localhost:20003` | engine 地址 |
| `TASK_DISCOVERY_FRONTEND_URL` | `http://localhost:8000` | 前端地址 |

### 9.2 测试数据

内联 `_MOCK_TASKS`（3 条），运行前 `init_discovered_tasks_db(_DATA_FILE, _MOCK_TASKS)` 写入 SQLite。

### 9.3 测试流程

1. **setup**：写入 mock 数据 → provision singlebox bot
2. **discover**：`POST /api/public/task-discovery/discover?user_id=...&agent_id=...` → 断言 `discovered == 3`，每个 task 有 `session_id` + `session_url`
3. **status**：`GET /api/public/task-discovery/status` → 断言 `total >= 3`，task_ids 匹配
4. **session 可达**：验证 `session_url` 格式正确（含 bot_uuid / id / session 参数）
5. **teardown**：清理

---

## 10. 微内核宪法遵守

- **transport-agnostic**：core 层 `models.py` / `discovery_service.py` / `task_reader.py` / `lifecycle.py` 无 transport import；`SessionCreator` 经 Protocol 隔离 HTTP 访问
- **thin adapter**：`router.py` 只转协议，不持领域策略
- **composition root**：`TaskDiscoveryModule` 是唯一接线点
- **Protocol seam**：`TaskReader` / `SessionCreator` 可插拔，corp 替换真实数据源/真实引擎只需实现 Protocol
- **lifecycle 自动发现**：对齐 `CronAutoSetupListener` 模式

---

## 11. 风险收敛（对应 spec §8）

- **R1 数据源**：`TaskReader` Protocol seam 已留，后续替换 `SqliteTaskReader` 为真实实现即可
- **R2 确认回调**：task_discovery → execution_framework 桥接为后续设计，当前模块止于"创建 session + 通知"
- **R3 状态持久化**：当前 status 存于 mock db；真实化时需引入独立状态服务（对齐 `TaskGraphService` 模式）
- **R4 并发**：定时调度串行遍历 bot，bot 规模大时需评估；当前 `page_size=100` 覆盖 singlebox 场景
- **R5 session_url**：对齐前端 `SessionOnlyPage` 路由约定，前端变更需同步更新 `_build_session_url`