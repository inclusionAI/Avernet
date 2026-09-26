# 技术设计 — discovered_tasks 从 SQLite 文件迁移到 ORM 持久化

## 1. 改动范围

```
源码：
├── core/task/task_discovery/
│   ├── discovered_task_models.py   (新增)  DiscoveredTaskModel ORM (ac_discovered_tasks)
│   ├── task_reader.py              (修改)  新增 OrmTaskReader + upsert/clear/seed helper；标 SqliteTaskReader deprecated
├── core/task/sql/
│   └── 2026_08_26_discovered_tasks.sql     (新增)  OceanBase DDL
├── adapters/http/task/
│   └── router.py                   (修改)  GET /status 注入 TaskReader；新增 POST /discovery/tasks + DELETE /discovery/tasks
├── di/modules/
│   └── task_discovery_module.py     (修改)  _provide_task_reader 由 SqliteTaskReader(path) 改为 OrmTaskReader(db)
└── plugins/local/
    └── database.py                 (修改)  注册 discovered_task_models 让 create_all 生效

测试：
├── tests/community/core/task/
│   ├── test_task_discovery_unit.py     (修改) in-memory SQLAlchemy + DatabasePlugin stub 驱动
│   ├── test_task_discovery_coverage.py  (修改) to_domain JSON 异常分支用 ORM 行直接构造
├── tests/community/endpoints/
│   └── test_task_discovery_router.py  (修改) status coverage 改用 ORM 播种；删除 _error_dir 旧 SQLite 错误路径
└── tests/community/core/task/singlebox_e2e/
    ├── test_task_discovery_e2e.py                       (修改) mock 播种走 POST /discovery/tasks
    ├── test_cron_scheduler_e2e.py                      (修改) 同上
    ├── test_cron_timed_fire_e2e.py                     (修改) 同上
    └── test_cron_timed_fire_workorder_e2e.py           (修改) 同上
```

## 2. 为什么选址 ORM 而非保留 SQLite 文件

### Before

```python
# task_discovery_module.py
def _provide_task_reader(self) -> TaskReader:
    return SqliteTaskReader(_resolve_db_path())   # → scripts/.dependencies/data/discovered_tasks.db

# router.py 内联：
async def get_discovery_status(...):
    db_path = _resolve_db_path()
    reader = SqliteTaskReader(db_path)
    ...
```

存在 4 个本质问题：

1. **多实例不共享**：cron-driven 的 discover 流程在多 pod 部署上每个 pod 写自己的 SQLite 文件，看到的 discovered_tasks 是进程隔离的；service 层无法跨 pod 汇查"今天哪些任务发现了"
2. **闯入 DI**：handler 直接 `new SqliteTaskReader(path)`，每次绕开 DI 的 singleton；上下文里 registry 的 `DiscoverService` 看的是 DI 的 reader，与 handler 看的不是同一份数据
3. **环境变量层泄漏**：`_resolve_db_path` 直接读 `TASK_DISCOVERY_DATA_FILE` env，违反 "raw env access belongs in configuration loading / composition root" 规则
4. **测试播种靠 OS 文件系统**：所有 e2e 都要先 `init_discovered_tasks_db(file_path)` 写真实 SQLite 文件，CI 容器依赖项目根目录可写；测出来的"持久化"是 OS 副作用，而不是 ORM 层的真实覆盖

### After

```python
# task_discovery_module.py
@singleton
@provider
@inject
def _provide_task_reader(self, db: DatabasePlugin) -> TaskReader:
    return OrmTaskReader(db)   # corp: ZDAS / OceanBase；local: SQLite 内存库

# router.py 通过 Injected 拿到同一个 reader 实例
async def get_discovery_status(
    request: Request,
    reader: TaskReader = Injected(TaskReader),
    service: DiscoveryService = Injected(DiscoveryService),
):
    tasks = reader.read_discovered_tasks()
```

新写 helper（HTTP 端点也对齐同一组 helper）：

```python
@router.post("/discovery/tasks")
async def write_discovered_tasks(
    request: Request,
    tasks: list[dict] = Body(..., embed=True),
    db: DatabasePlugin = Injected(DatabasePlugin),
):
    count = upsert_discovered_tasks(db, tasks)   # 按 task_id 自然键 upsert
    return envelope({"written": count}, request)

@router.delete("/discovery/tasks")
async def clear_discovered_tasks_endpoint(
    request: Request,
    db: DatabasePlugin = Injected(DatabasePlugin),
):
    count = clear_discovered_tasks(db)
    return envelope({"deleted": count}, request)
```

带来的收益：

| 维度 | 之前（SQLite 文件） | 之后（ORM） |
| --- | --- | --- |
| 跨实例共享 | ✗（每 pod 独立文件） | ✓（prod 共用 ZDAS / OceanBase） |
| DI 一致性 | ✗（handler new 自己的 reader） | ✓（`Injected(TaskReader)` 与 service 共享 singleton） |
| env 访问侵入 | ✗（`_resolve_db_path` 在 core/router） | ✓（DatabasePlugin 注入，raw env 仅在 DI factory） |
| 测试可隔离性 | ✗（需要 OS 文件路径） | ✓（in-memory SQLAlchemy + DatabasePlugin stub） |
| 写入 API | ✗（需直接操作 .db 文件） | ✓（`POST /discovery/tasks` / `DELETE /discovery/tasks`） |

`SqliteTaskReader` 暂时保留（`.. deprecated::`），供历史测试迁移期间不破环；后续会在下一轮收紧。

## 3. OrmTaskReader 与 LockRepository 同构参考

`DiscoveredTaskModel` 与 `TaskDiscoveryLockModel`（`lock_models.py`）共享同一组约定：

- 同 `Base`：`from agentclaw.community.core.base import Base`
- `AutoIncrementBigInteger = Integer` —— SQLite-friendly，matches task/repository/models.py（prod: bigint，local/test: SQLite Integer alias）
- `gmt_create` / `gmt_modified`：`DateTime + default=func.now()` / `onupdate=func.now()`
- 通过 `import-only-for-side-effect` 在 `plugins/local/database.py` 中注册到 `Base.metadata`，让 `Base.metadata.create_all()` 在 SQLite 内存库中自动建表
- `to_domain()` 把 ORM row 还原为领域 dataclass；防御性读取旧库缺列与 JSON 异常

差异：

- `LockRepository` 暴露明确 Protocol（`TaskDiscoveryLockRepositoryProtocol`）+ 具体实现 `TaskDiscoveryLockRepository`，因为锁要支持"分布式原子 INSERT（INSERT ... ON CONFLICT / UNIQUE 约束仲裁）"语义，需要独立 repo class
- `DiscoveredTaskModel` 不是锁 —— 是普通 CRUD 数据，**复用现有 DatabasePlugin 的 `orm_session()` / `transactional_orm_session()` 上下文管理器即可**，不再单独立 Repository class；helper（`upsert/clear/seed`）+ OrmTaskReader（读）两角色用 module-level 函数 / 单独 reader class 分开表达，更简单

## 4. يدار DDL 落地路径

### corp + 生产环境（OceanBase / ZDAS）

1. DB 拿到此 PR 的 `src/backend/src/agentclaw/community/core/task/sql/2026_08_26_discovered_tasks.sql`
2. 在目标库执行该 DDL，建成 `ac_discovered_tasks`
3. 后端实例启动，`SqliteDB` 不生效，DI 注入 ZDAS / OceanBase 的 `DatabasePlugin`
4. `OrmTaskReader(db)` 在第一次查询时使用 ORM session，依赖已存在的表

注：PR 中**不携带自动迁移**，要求 DBA 同步建表 —— 与 `TaskDiscoveryLockModel` 同一约定（"UNIQUE 即锁本体；auto create_all 仅 local"）。

### local / singlebox / 仓库内测试

1. DI 注入 `plugins/local/database.py` 中的 `SqliteDB`（继承 `DatabasePlugin`）
2. `SqliteDB._setup_metadata()` 通过 side-effect import 把 `DiscoveredTaskModel` 注册到 `Base.metadata`：
   ```python
   import agentclaw.community.core.task.task_discovery.discovered_task_models  # noqa: F401  ac_discovered_tasks
   ```
3. `Base.metadata.create_all()` 自动在 SQLite 内存库建表（与生产 schema 一致，仅类型映射为 SQLite-friendly）

### e2e 测试播种

singlebox e2e 不再写 OS-level SQLite 文件，统一走 HTTP：

```
setUpClass:
  POST /api/v1/collaboration/tasks/discovery/tasks
       body: {"tasks": [seed_task, ...]}
       → 走 upsert_discovered_tasks → 写入 ac_discovered_tasks 表

tearDown:
  DELETE /api/v1/collaboration/tasks/discovery/tasks
       → 走 clear_discovered_tasks → 清空表
```

## 5. 接口契约与更改风险

### TaskReader Protocol（不变）

```python
class TaskReader(Protocol):
    def read_discovered_tasks(self) -> list[DiscoveredTask]: ...
    def read_pending_tasks(self) -> list[DiscoveredTask]: ...
    def read_pending_tasks_for_bot(self, bot_id: str, owner_id: str, dt: str) -> list[DiscoveredTask]: ...
```

`OrmTaskReader` 满足此 Protocol，DI 切换后调用方不需要任何修改。

### 新增 HTTP 端点（中国官网接口契约范围）

| 端点 | 方法 | Body | 返回 |
| --- | --- | --- | --- |
| `/discovery/tasks` | POST | `{"tasks": [<task_dict>]}` | `{"code": 200000, "data": {"written": <int>}}` |
| `/discovery/tasks` | DELETE | (none) | `{"code": 200000, "data": {"deleted": <int>}}` |

错误：维持 1.0 通用错误条约（FastAPI 422 / `ErrorEnvelope` 500 / 401）。

### 已废弃字段说明

- `TASK_DISCOVERY_DATA_FILE` 环境变量：本来用于在测试时指向一个指定的 SQLite 文件，迁移后**通过 ORM 不再使用**；本 PR 不强行删除该 env var 的所有读取点（DI 模块的 `_resolve_db_path` 已删除，但如果某处仍有用此 var，5 秒内不报警）—— 后续可以单独 PR 清理
- `SqliteTaskReader`：保留为 deprecated，仅在历史测试构造路径中仍使用，不在 DI / 生产路径中实例化

### 升级影响

- 部署到 prod 之前 DBA 必须建 `ac_discovered_tasks` 表 — 这是 DDL 文件存在的核心目的
- local / singlebox / 测试在 PR 合并后立即工作（SQLite 内存库自动建表）
- 跨 pod 共享场景（多 pod cron 触发） — 之前每 pod 各自看自己的 SQLite 文件，现在共享 OceanBase 表，发现 / 确认链路跨 pod 可见 → **行为变化点，需关注**：现有 e2e 用例已经覆盖"DB 共享语义"，但 prod 上若仍想保留 per-pod 隔离（例如灰度期间），需要把 `DiscoveredTaskModel` 切回 `SqliteTaskReader` 或者按 env 区分；本 PR 不做这个层级

## 6. 测试矩阵

| 路径 | 覆盖内容 | 测试文件 |
| --- | --- | --- |
| `OrmTaskReader.read_discovered_tasks` | 全量读；旧库缺列时 `objective` / `acceptances` 防御性回退 | `test_task_discovery_unit.py`（`TestTaskReader`） |
| `OrmTaskReader.read_pending_tasks_for_bot` | 按 (bot_id, owner_id, dt) + status='pending_confirmation' 过滤 | `test_task_discovery_unit.py` |
| `DiscoveredTaskModel.to_domain` JSON 异常 | `acceptances="not-valid-json{{"` → `[]`；`acceptances=None` → `[]` | `test_task_discovery_coverage.py`（`test_orm_reader_invalid_acceptances_json`） |
| `upsert_discovered_tasks` 幂等性 | 第一次 insert；同 `task_id` 第二次走 update 分支 | `test_task_discovery_unit.py`（隐式） |
| `POST /discovery/tasks` | 端点返回 `{"code": 200000, "data": {"written": <int>}}` | `test_task_discovery_router.py`（happy path） |
| `DELETE /discovery/tasks` | 端点返回 `{"code": 200000, "data": {"deleted": <int>}}` | `test_task_discovery_router.py`（happy path） |
| `GET /discovery/status` | ORM 播种 → 进程内 `DiscoveryService` 聚合 + status 读到 | `test_task_discovery_router.py`（`get_status_happy_discovered_and_pending_tasks`） |
| 本地 SQLite 内存库自动建表 | `Base.metadata.create_all` 后 `ac_discovered_tasks` 存在 | `tests/community/repository/task/test_task_discovery_lock_repository.py` 及单测套件 |
| singlebox e2e HTTP 播种 | setUpClass POST /discovery/tasks → e2e status / cron fire 流程可用 | `test_cron_timed_fire_e2e.py`（已在 #1544 验证 PASS） |
