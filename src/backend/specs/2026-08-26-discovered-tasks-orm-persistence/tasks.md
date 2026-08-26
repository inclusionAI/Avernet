# 任务清单 — discovered_tasks 从 SQLite 文件迁移到 ORM 持久化

## 已完成

### ORM 模型 + DDL

- [x] T1: 新增 `core/task/task_discovery/discovered_task_models.py`
  - 实现 `DiscoveredTaskModel`（`ac_discovered_tasks` 表），字段与 `DiscoveredTask` 对齐
  - `to_domain()`：`acceptances` JSON 解析失败 / 非 list 防御性回退 `[]`
  - `Index("idx_ac_discovered_tasks_bot_owner_dt", "bot_id", "owner_id", "dt")` 与 SQL DDL 对齐
- [x] T2: 新增 `core/task/sql/2026_08_26_discovered_tasks.sql`
  - OceanBase 8 兼容语法（`bigint(20)` / `varchar` / `text` / `timestamp`）
  - `PRIMARY KEY(id)` + `UNIQUE KEY uk_ac_discovered_tasks_task_id(task_id) LOCAL` + `KEY idx_ac_discovered_tasks_bot_owner_dt`
  - `DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='任务发现-已发现任务数据'`

### 任务读路径 — OrmTaskReader

- [x] T3: `core/task/task_discovery/task_reader.py` 新增 `OrmTaskReader`
  - `__init__(db: DatabasePlugin)`（`@inject`）—— 读 ORM session 解放自直接 sqlite3.connect
  - 实现 `read_discovered_tasks()` / `read_pending_tasks()` / `read_pending_tasks_for_bot(bot_id, owner_id, dt)`
  - 加入 `__all__`
- [x] T4: `core/task/task_discovery/task_reader.py` 标 `SqliteTaskReader` 为 deprecated
  - docstring 增加 `.. deprecated::` 注释，说明生产环境用 `OrmTaskReader`
  - 保留旧测试构造路径（不删除，避免破坏其他历史测试）

### 任务写路径 — Upset/Clear/Seed helper

- [x] T5: `task_reader.py` 新增 `upsert_discovered_tasks(db, tasks: list[dict]) -> int`
  - 按 `task_id` 自然键 insert / update
  - `acceptances` 字段用 `json.dumps(..., ensure_ascii=False)` 落库
  - 使用 `db.transactional_orm_session()` —— 事务边界在 helper
- [x] T6: `task_reader.py` 新增 `clear_discovered_tasks(db) -> int` —— 整表 delete
- [x] T7: `task_reader.py` 新增 `seed_discovered_tasks(db, tasks)` —— `clear + upsert` 幂等播种

### DI 绑定调整

- [x] T8: `di/modules/task_discovery_module.py` 把 `_provide_task_reader` 改成 `OrmTaskReader(db)`
  - 形参：`db: DatabasePlugin` 用 `@inject` 注入
  - 删除 `import Path` / `_resolve_db_path` / `_DEFAULT_DB` / 项目根 8 级上溯逻辑
  - docstring 强调"corp 走 ZDAS/OceanBase，local 走 SQLite 内存库"

### 本地 SQLite 数据库插件注册

- [x] T9: `plugins/local/database.py` 注册 `discovered_task_models` 模块
  - `import agentclaw.community.core.task.task_discovery.discovered_task_models  # noqa: F401  ac_discovered_tasks`
  - 与既有 `bot_dormant.sqlite_models`、`task.repository.models` 等同一行风格

### HTTP 端点

- [x] T10: router.py `GET /discovery/status` 改为注入 `TaskReader`
  - 形参增加 `reader: TaskReader = Injected(TaskReader)`
  - 删除函数体 `db_path = _resolve_db_path(); reader = SqliteTaskReader(db_path)`
  - 删除顶部 `_PROJECT_ROOT` / `_DEFAULT_DB` / `_resolve_db_path`
  - 异常路径保持 `InternalError("status read failed") → 500`
- [x] T11: router.py 新增 `POST /discovery/tasks`（upsert）
  - Body：`{"tasks": [...]}`
  - 用 `Injected(DatabasePlugin)` + `upsert_discovered_tasks`
  - 成功响应：`{"code": 200000, "data": {"written": <int>}}`
  - 异常：`InternalError("write discovered tasks failed") → 500`
- [x] T12: router.py 新增 `DELETE /discovery/tasks`（清空）
  - 用 `Injected(DatabasePlugin)` + `clear_discovered_tasks`
  - 成功响应：`{"code": 200000, "data": {"deleted": <int>}}`
  - 异常：`InternalError("clear discovered tasks failed") → 500`

### 测试套件迁移

- [x] T13: `tests/.../test_task_discovery_unit.py` 由 SqliteTaskReader → OrmTaskReader
  - `_setup_db` 改为 in-memory SQLAlchemy + `Base.metadata.create_all` + `DatabasePlugin` stub
- [x] T14: `tests/.../test_task_discovery_coverage.py` `test_row_to_task_invalid_acceptances_json` → `test_orm_reader_invalid_acceptances_json`
  - 直接 insert 一行 `DiscoveredTaskModel(acceptances="not-valid-json{{")`，断言 `to_domain().acceptances == []`
- [x] T15: `tests/.../test_task_discovery_router.py`
  - 删除 `_seed_status_error_dir` / `_ERROR_DB_DIR`（与 SQLite 强相关的"目录当 db 路径"错误路径）
  - 新正向 coverage 用例：ORM 播种 → 进程内 `DiscoveryService` 聚合持久化 tasks 与 process-local discovery results
  - error 例保留 POST /discover 422（缺 bot_id）+ dingtalk-config 422（缺 body）
- [x] T16: `tests/.../singlebox_e2e/test_task_discovery_e2e.py`
  - mock 数据播种由 `init_discovered_tasks_db(file_path)` 改为 HTTP `POST /discovery/tasks` upsert
  - 删除文件路径相关清理逻辑
- [x] T17: `tests/.../singlebox_e2e/test_cron_scheduler_e2e.py` / `test_cron_timed_fire_e2e.py` / `test_cron_timed_fire_workorder_e2e.py`
  - setUpClass 的 mock 数据播种统一走 `POST /discovery/tasks` upsert

### Lint / 单测验证

- [x] T18: `ruff check` 通过本 PR 涉及变更文件
- [x] T19: backend 单测套件 `pytest tests/community/core/task/test_task_discovery_*.py + tests/community/repository/task/test_task_discovery_lock_repository.py + tests/community/endpoints/test_task_discovery_router.py` 全部 PASSED
- [x] T20: 本地 in-memory SQLite `Base.metadata.create_all` 能为 `ac_discovered_tasks` 正确建表
