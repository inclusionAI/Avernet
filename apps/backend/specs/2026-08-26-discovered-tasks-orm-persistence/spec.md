# discovered_tasks 从 SQLite 文件迁移到 ORM 持久化（ac_discovered_tasks 表）

## 概述

本 spec 把 task_discovery 模块的"已发现任务"数据存储从本地 SQLite 文件 (`scripts/.dependencies/data/discovered_tasks.db`)，迁移到 SQLAlchemy ORM 模式：

- **生产环境**：通过 `DatabasePlugin.orm_session()` 走 ZDAS / OceanBase，新表 `ac_discovered_tasks` 由 DDL 同步；
- **本地 / singlebox / 测试环境**：通过 SqliteDB（在 `plugins/local/database.py`）走 SQLite 内存库，由 SQLAlchemy `Base.metadata.create_all` 自动建表；
- **统一抽象**：`OrmTaskReader` 实现 `TaskReader` Protocol，替换原 `SqliteTaskReader`（保留为 deprecated 仅供旧测试构造）。

同时新增两个 HTTP 端点供 e2e 测试或外部数据源写入：

- `POST /api/v1/collaboration/tasks/discovery/tasks` —— upsert 语义，按 `task_id` 自然键 insert/update；
- `DELETE /api/v1/collaboration/tasks/discovery/tasks` —— 清空全部已发现任务（运维重置 / 测试清理）。

**关联文档**：
- `2026-08-25-task-discovery-taskspec-align-and-notify-channels/spec.md` — 字段对齐后的 DiscoveredTask 数据结构（`title` / `instruction` / `background` / `objective` / `acceptances`），本 spec 沿用其字段集，不改 DiscoveredTask 领域模型
- `2026-08-20-task-discovery-endpoints-and-dingtalk-notify/spec.md` — 已有的 `/discovery/*` HTTP 端点；本 spec 只新增写入语义的两条端点，不动现有的 `/discovery/discover`、`/discovery/status`、`/discovery/reschedule`、`/discovery/dingtalk-config`、`/discovery/scheduled-trigger`
- `2026-08-18-task-discovery/spec.md` — task_discovery 模块总览

**领域定位**：
- 把"已发现任务"从"在 backend 进程附近的临时 SQLite 文件"提升为"通过 DI 注入的 DatabasePlugin 的正式持久化数据"；让 cron-driven 的 discover 流程与单机多实例环境（多 pod 共用同一 ZDAS / OceanBase）兼容
- `DiscoveredTask` 领域模型 / `to_discovery_prompt` / `to_discovery_prompt` 等领域行为不变；只有读写实现被换掉

---

## 需求列表

### REQ-1: 引入 `DiscoveredTaskModel` ORM model

- **描述**：在 `core/task/task_discovery/discovered_task_models.py` 中新增 SQLAlchemy `DiscoveredTaskModel`，对应新表 `ac_discovered_tasks`，列与 `DiscoveredTask` 领域模型字段一一映射；`acceptances` 以 JSON 文本存储，读取时反序列化。
- **验收标准**：
  - `DiscoveredTaskModel` 表名 `ac_discovered_tasks`，与 lock_models.py 同一 base
  - 字段：`id`（pk 自增）、`task_id`（UNIQUE）、`bot_id` / `owner_id` / `dt` / `title` / `instruction` / `background` / `discovery_basis` / `priority` / `discovered_at` / `status` / `objective`、`acceptances`（JSON 文本）、`gmt_create` / `gmt_modified`
  - `__table_args__` 上加 `Index("idx_ac_discovered_tasks_bot_owner_dt", "bot_id", "owner_id", "dt")`（与 SQL DDL `KEY idx_ac_discovered_tasks_bot_owner_dt` 对齐）
  - `to_domain()` 把 ORM row 还原为 `DiscoveredTask`；`acceptances` JSON 解析失败或非 list 时防御性回退 `[]`
- **改动文件**：
  - `core/task/task_discovery/discovered_task_models.py`（新增）
- **状态**：已完成

### REQ-2: 新增 OceanBase DDL 文件

- **描述**：在 `core/task/sql/2026_08_26_discovered_tasks.sql` 中给出 `ac_discovered_tasks` 的 OceanBase DDL；线上 DBA 通过此 DDL 建表，单实例 ZDAS / OceanBase 与本地 SQLite 共享同一逻辑 schema（差异在 SQLite 端由 `create_all` 自动建表）。
- **验收标准**：
  - MySQL/OceanBase 8 兼容语法（`bigint(20)` / `varchar(N)` / `text` / `timestamp ... DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`）
  - `PRIMARY KEY(id)` + `UNIQUE KEY uk_ac_discovered_tasks_task_id(task_id) LOCAL` + `KEY idx_ac_discovered_tasks_bot_owner_dt(bot_id, owner_id, dt)`
  - 列顺序与 `DiscoveredTaskModel` 对齐
  - 默认 `charset=utf8mb4 collate=utf8mb4_bin`
  - `comment='任务发现-已发现任务数据'`
- **改动文件**：
  - `core/task/sql/2026_08_26_discovered_tasks.sql`（新增）
- **状态**：已完成

### REQ-3: `OrmTaskReader` 替代 `SqliteTaskReader` 作为 DI 中默认 `TaskReader`

- **描述**：DI 的 `task_discovery_module._provide_task_reader` 由 `SqliteTaskReader(path)` 改为 `OrmTaskReader(db)`；删除 `_resolve_db_path` 与 `_DEFAULT_DB` / 项目根上溯逻辑（这些是 file-system 直读的痕迹，与 ORM 故事不兼容）；`SqliteTaskReader` 保留为 deprecated，仅供旧测试构造。
- **验收标准**：
  - `OrmTaskReader` 实现 `TaskReader` Protocol 的全部方法（`read_discovered_tasks` / `read_pending_tasks` / `read_pending_tasks_for_bot`）
  - `__init__` 用 `@inject` 注入 `DatabasePlugin`
  - `task_discovery_module.py` 删除 `Path` / `_resolve_db_path` / `_DEFAULT_DB` + `/sql/.dependencies/...` 等痕迹
  - `OrmTaskReader` 在 `DatabasePlugin.orm_session()` 中只读 `session.query` —— 不持有写权；写入端走专用 helper（REQ-4）
- **改动文件**：
  - `core/task/task_discovery/task_reader.py`（新增 `OrmTaskReader`，保留旧 `SqliteTaskReader`）
  - `di/modules/task_discovery_module.py`（改绑 `OrmTaskReader`）
- **状态**：已完成

### REQ-4: 模块级写入 helper（`upsert_discovered_tasks` / `clear_discovered_tasks` / `seed_discovered_tasks`）

- **描述**：把"写入"作为独立 helper 暴露在 `task_reader.py` 模块层（不在 `TaskReader` Protocol 上）—— 读 / 写分离：`TaskReader` 只是读抽象，写入端通过 DatabasePlugin 的事务 ORM session 完成。helper 跨 SQLite / OceanBase 兼容（纯 `session.query` + `add` / `merge`，无方言专有语法）。
- **验收标准**：
  - `upsert_discovered_tasks(db, tasks: list[dict]) -> int` —— 按 `task_id` 自然键 insert or update；`acceptances` 字段用 `json.dumps(..., ensure_ascii=False)` 落库；返回写入条数
  - `clear_discovered_tasks(db) -> int` —— `session.query(DiscoveredTaskModel).delete()` 整表清空；返回删除行数
  - `seed_discovered_tasks(db, tasks)` —— `clear_discovered_tasks(db) + upsert_discovered_tasks(db, tasks)` 幂等播种
  - 均使用 `db.transactional_orm_session()`（事务边界在 helper）
  - 在 `__all__` 中导出
- **改动文件**：
  - `core/task/task_discovery/task_reader.py`
- **状态**：已完成

### REQ-5: 本地 SQLite 数据库插件注册新 ORM model

- **描述**：在 `plugins/local/database.py` 的 `SqliteDB._setup_metadata()` 中 import `discovered_task_models`（side-effect，把 `DiscoveredTaskModel` 注册到 `Base.metadata`），让 `Base.metadata.create_all()` 在 local 内存库上能自动建表。
- **验收标准**：
  - `import agentclaw.community.core.task.task_discovery.discovered_task_models  # noqa: F401  ac_discovered_tasks` 一行 `import-only-for-side-effect`
  - 与现有 model 注册（`bot_dormant.sqlite_models`、`task_queue.repository.models`、`task.repository.models` 等）格式一致
- **改动文件**：
  - `plugins/local/database.py`
- **状态**：已完成

### REQ-6: HTTP 端点 GET /discovery/status 改用注入的 `TaskReader`

- **描述**：原 handler 在函数体里 `SqliteTaskReader(_resolve_db_path())` 直接 `new` 一个 reader，与 ORM 迁移后注入的 single instance 不一致，会绕开 DI 的 DatabasePlugin；改为通过 `Injected(TaskReader)` 取 DI 提供的 `OrmTaskReader`，并删除 handler 内的 `_resolve_db_path` / `_DEFAULT_DB` 痕迹。
- **验收标准**：
  - handler 形参增加 `reader: TaskReader = Injected(TaskReader)`，删除函数体里的 `db_path = _resolve_db_path(); reader = SqliteTaskReader(db_path)`
  - 删除 router.py 顶部 `_PROJECT_ROOT` / `_DEFAULT_DB` / `_resolve_db_path` 痕迹
  - 错误路径仍走 `InternalError("status read failed") → 500`
- **改动文件**：
  - `adapters/http/task/router.py`
- **状态**：已完成

### REQ-7: 新增 HTTP 端点 POST /discovery/tasks（upsert 已发现任务）

- **描述**：让 e2e 测试与外部数据源能通过 HTTP 播种 / 写入已发现任务，无需直接操作底层 ZDAS / SQLite 数据库 —— 等价于 `upsert_discovered_tasks(db, tasks)` 的 HTTP 包装。
- **验收标准**：
  - `@router.post("/discovery/tasks", response_model=Envelope[dict[str, Any]])`
  - Body：`{"tasks": [{"task_id": "...", "bot_id": "...", ...}, ...]}`
  - 成功响应：`{"code": 200000, "data": {"written": <int>}}`
  - 实现层用 `Injected(DatabasePlugin)` + `upsert_discovered_tasks`；异常 `InternalError("write discovered tasks failed") → 500`
- **改动文件**：
  - `adapters/http/task/router.py`
- **状态**：已完成

### REQ-8: 新增 HTTP 端点 DELETE /discovery/tasks（清空已发现任务）

- **描述**：对应运维重置 / 测试清理场景 —— 通过 HTTP 把 `ac_discovered_tasks` 表清空。
- **验收标准**：
  - `@router.delete("/discovery/tasks", response_model=Envelope[dict[str, Any]])`
  - 成功响应：`{"code": 200000, "data": {"deleted": <int>}}`
  - 实现层用 `Injected(DatabasePlugin)` + `clear_discovered_tasks`；异常 `InternalError("clear discovered tasks failed") → 500`
- **改动文件**：
  - `adapters/http/task/router.py`
- **状态**：已完成

### REQ-9: 测试套件迁移到 ORM

- **描述**：在所有受影响的测试文件中，用 `OrmTaskReader` + `seed_discovered_tasks` / `upsert_discovered_tasks` 取代原 `SqliteTaskReader` + `init_discovered_tasks_db`（路径 + sqlite3 直读）。测试新写 helper（in-memory SQLite + sessionmaker + 简易 `DatabasePlugin` stub）以覆盖 ORM 路径（含 `to_domain` JSON 异常分支、字段缺失 fallback）。
- **验收标准**：
  - `test_task_discovery_unit.py`：原本基于 SQLite 文件的 `_setup_db` 改为 in-memory SQLAlchemy + `Base.metadata.create_all` + `DatabasePlugin` stub；`TestTaskReader` 通过 `OrmTaskReader` 跑
  - `test_task_discovery_coverage.py`：`test_row_to_task_invalid_acceptances_json` 改为 `test_orm_reader_invalid_acceptances_json`，验证 `DiscoveredTaskModel.to_domain()` 在 `acceptances="not-valid-json{{"` 时回退 `[]`
  - `test_task_discovery_router.py`：`get_discovery_status` 由"指向不存在的目录"的错误路径（与 SQLite 强相关），改为"通过 ORM 播种的真实任务" + 进程内 `DiscoveryService` 的正向覆盖 + happy path；error 案例只剩 POST /discover 422（缺 bot_id）
  - `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`：mock 数据播种由 `init_discovered_tasks_db(file_path)` → 走 HTTP `POST /discovery/tasks` upsert
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py` / `test_cron_timed_fire_e2e.py` / `test_cron_timed_fire_workorder_e2e.py`：setUpClass 的 mock 数据播种统一走 `POST /discovery/tasks`
- **改动文件**：
  - `tests/community/core/task/test_task_discovery_unit.py`
  - `tests/community/core/task/test_task_discovery_coverage.py`
  - `tests/community/endpoints/test_task_discovery_router.py`
  - `tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_cron_timed_fire_e2e.py`
  - `tests/community/core/task/singlebox_e2e/test_cron_timed_fire_workorder_e2e.py`
- **状态**：已完成

### REQ-10: DiscoveredTaskModel 不引入到 corp 的 prod-only import 路径

- **描述**：避免 DiscoveredTaskModel 在 import 链上意外把 corp 端 ZDAS / 钉钉 / corp-only sparrow 等模块拖进 community / local / tests 上下文 —— 与 `lock_models.py` 同构（locally-imported only via side-effect import）。
- **验收标准**：
  - `core/task/task_discovery/discovered_task_models.py` 只 import：`sqlalchemy`、`agentclaw.community.core.base`、`agentclaw.community.core.task.task_discovery.models`（领域 record）
  - 不引入 `agentclaw.community.plugins.community.notify_sender` / `agents_orm` / `corp` any 其他 corp-only type
  - corp / local 均能在 DI 启动时正确注册并 `create_all`
- **改动文件**：
  - `core/task/task_discovery/discovered_task_models.py`
- **状态**：已完成
