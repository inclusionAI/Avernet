# task_config 配置存储与匹配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `apps/backend` 新增一张 `task_config` 表和三个 HTTP 路由(内部 `set`+`match`、公开 `match`),提供按 `(config_name, env)` 维护的类型化配置存储与匹配。

**Architecture:** `core/task/task_config/` 自洽子包(ORM / record / repository / service / Service-API Protocol),经 `api/task/task_config_service.py` 再导出 Protocol;DI 模块把具体类与 Protocol 绑同一 singleton 并注册进 `di/container.py`。内部面在 task umbrella router 下挂 `/config` 子路由(`get_current_user`);公开面在 `openapi_v1/task/router.py` 直接加 `/config/match` handler(`require_principal`,补 AUTHORIZATION + ADMISSION 两表行)。两面对外契约一致,共享同一 service 单例。响应用 task surface 的 `Envelope[T]` + `@envelope_errors`(失败抛域异常,不由 handler 构造 `success=False`)。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy(单 `core.base.Base`), injector, OceanBase/MySQL 平行 DDL + 内存 SQLite 测试。

**Spec:** `apps/backend/specs/2026-11-02-task-config/spec.md`(本计划从该 spec 出发;执行者 spec 与 plan 都读)。

## Global Constraints

- `env` 列值由 router 内 `env_utils.get_current_env()` 取,不作为接口参数;ORM 默认用 callable `get_current_env`,不用字面量 `"prod"`。(spec Anti-goals + 决定 #6)
- 表名 `task_config`(裸名,不用 `ac_` 前缀);`UniqueConstraint(config_name, env)` 名 `uk_task_config_name_env`;二级 `idx_task_config_env (env)`。(spec 表结构 / DDL)
- 时间/审计列固定为 `creator` / `modifier` / `gmt_create` / `gmt_modify`(非 `operator`/`created_at`/`updated_at`)。upsert 冲突更新只刷 `config_type`/`config_value`/`modifier`/`gmt_modify`,不覆盖 `creator`/`gmt_create`。(spec 表结构 + set 语义)
- `config_value` 列 `TEXT NOT NULL`,存 JSON 文本;`config_type ∈ {single_string, bool, set}`(governed 枚举,DB 不约束)。(spec 表结构 + DDL)
- match 只回 `matched: bool`,不回存储值;未知配置名 → `matched: false`(不报错)。(spec Anti-goals)
- 写接口整体覆盖 upsert(`set` 类型可与存行不同,按本次为准)。(spec set 语义)
- 鉴权:内部 `Depends(get_current_user)`;公开 `require_principal` + `AdmissionMode.OPEN`;不做 per-config 鉴权。(spec 鉴权)
- 响应信封 task surface `Envelope[T]` + `@envelope_errors`,import 自 `agentclaw.community.adapters.http.openapi_v1.{contracts,responses}`;`Injected` 自 `agentclaw.community.di`。(spec 决定 #8 + recon)
- 新 ORM 模块**必须**登记进 `core/schema.py::import_all_models()`,否则测试的内存 SQLite `create_all` 漏建表。
- 每条新路由必须在 `tests/community/endpoints/` 配声明式 happy+error 用例,否则 `framework/test_coverage_gate.py` 挂 gate;gate 按字面 `(method, 完整 path)` 计,`match` 两个前缀 = 两条独立活路由。
- **不格式化无关代码**;改 BCS 不涉及;1000 行/文件上限;不引入新依赖。
- 提交用 `feat(backend)`/`test(backend)` 前缀,从 `dev` rebase,不要 merge。

## Review Focus

执行前先把这五条读完(它们是 spec 暗示、但单看任务标题看不出最可能坑用户的点),并在各自归属任务里落测试:

1. **`set` 成员缺席不报错**:`config_type=set` 且传入元素不在集合内 → `matched:false`(不是 error、不是 404)。→ Task 3 `test_match_set_member_absent_returns_false`。
2. **未知配置名不报错**:match 一个不存在的 `config_name` → `matched:false`(不是 404/500)。→ Task 3 `test_match_unknown_name_returns_false`。
3. **upsert 不抹掉 creator/gmt_create**:同名 `(config_name, env)` 二次写入,`creator` 与 `gmt_create` 保持首写值,`modifier`/`gmt_modify` 刷新。→ Task 2 `test_upsert_preserves_creator_and_gmt_create`。
4. **环境隔离**:同一 `config_name` 在 `pre` 与 `prod` 互不可见;在 `pre` 写、在 `prod` match → `false`。→ Task 3 `test_match_isolated_across_envs`。
5. **type/form 校验不静默匹配**:存行为 `bool` 却传 string `"true"`(或存 `set` 传 bool)→ 抛错/422,**不**返回 `matched:true`,也**不**用 `==` 把 `"true"` 当 `True`。→ Task 3 `test_match_rejects_value_type_mismatch` + Task 5 set 端点 error 用例(可校验 config_type 非法 → 422)。

---

## File Structure

新建:
- `src/agentclaw/community/core/task/task_config/__init__.py`
- `src/agentclaw/community/core/task/task_config/orm.py` — `TaskConfigModel(Base)`(表 `task_config`)
- `src/agentclaw/community/core/task/task_config/models.py` — `@dataclass TaskConfigRecord`
- `src/agentclaw/community/core/task/task_config/repository_protocol.py` — `TaskConfigRepositoryProtocol`
- `src/agentclaw/community/core/task/task_config/repository.py` — `TaskConfigRepository` 实现
- `src/agentclaw/community/core/task/task_config/task_config_service_protocol.py` — `TaskConfigServiceProtocol`
- `src/agentclaw/community/core/task/task_config/service.py` — `TaskConfigService` 实现
- `src/agentclaw/community/core/task/task_config/sql/2026_11-02_task_config.sql` — OceanBase/MySQL DDL
- `src/agentclaw/community/api/task/task_config_service.py` — Protocol 再导出
- `src/agentclaw/community/di/modules/task_config_module.py` — DI 绑定
- `src/agentclaw/community/adapters/http/task/task_config_router.py` — 内部 sub-router(`prefix="/config"`)
- `tests/community/core/task/test_task_config_orm.py`
- `tests/community/core/task/test_task_config_repository.py`
- `tests/community/core/task/test_task_config_service.py`
- `tests/community/endpoints/test_task_config_router.py`

修改:
- `src/agentclaw/community/core/schema.py` — `import_all_models` 增一行 import
- `src/agentclaw/community/di/container.py` — import + 在 base 模块列表加 `TaskConfigModule()`
- `src/agentclaw/community/adapters/http/task/router.py` — 引入并 `task_internal_router.include_router(task_config_router)`
- `src/agentclaw/community/adapters/http/openapi_v1/task/router.py` — 加 `/config/match` handler
- `src/agentclaw/community/adapters/http/openapi_v1/authorization_rules_task.py` — 加 `config/match` → `NoCheck(...)`
- `src/agentclaw/community/adapters/http/openapi_v1/admission_task_routes.py` — 加 `config/match` → `AdmissionMode.OPEN`(进 `TASK_PUBLIC_ADMISSION`)

---

## Task 1: ORM model + DDL + schema registration

**Files:**
- Create: `src/agentclaw/community/core/task/task_config/__init__.py`、`.../orm.py`
- Create: `src/agentclaw/community/core/task/task_config/sql/2026-11-02_task_config.sql`
- Modify: `src/agentclaw/community/core/schema.py`(`import_all_models` 函数体,~30-86 行)
- Test: `tests/community/core/task/test_task_config_orm.py`

**Interfaces:**
- Consumes: `agentclaw.community.core.base.Base`、`agentclaw.community.utils.env_utils.get_current_env`、SQLAlchemy `Column`/`String`/`Text`/`BigInteger`/`DateTime`/`UniqueConstraint`/`Index`。
- Produces: `TaskConfigModel`(表名 `task_config`),供 Task 2 的 repository 使用;`import_all_models` 注册后在 `Base.metadata.create_all` 时建表。

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_task_config_orm.py
from sqlalchemy import create_engine, inspect
from agentclaw.community.core.base import Base
from agentclaw.community.core.schema import import_all_models

def test_task_config_table_registered_and_created():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    assert "task_config" in inspector.get_table_names()

def test_task_config_columns_and_constraints():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("task_config")}
    assert cols >= {"id", "config_name", "config_type", "config_value", "env",
                    "creator", "modifier", "gmt_create", "gmt_modify"}

    uqs = {tuple(u["column_names"]) for u in inspect(engine).get_unique_constraints("task_config")}
    assert ("config_name", "env") in uqs

    idxs = {i["name"] for i in inspect(engine).get_indexes("task_config")}
    assert "idx_task_config_env" in idxs
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/test_task_config_orm.py -v`
Expected: FAIL —— `task_config` 不在 metadata 里(模块还没登记/模型还没建)。

- [ ] **Step 3: 实现 `orm.py`**

`core/task/task_config/orm.py`:`class TaskConfigModel(Base)`,`__tablename__ = "task_config"`,列按 spec 表结构 + DDL:`id`(BigInteger PK autoincrement)、`config_name`(String(128) NOT NULL)、`config_type`(String(32) NOT NULL)、`config_value`(Text NOT NULL)、`env`(String(32) NOT NULL, `default=get_current_env` —— 注意传 callable 本身,不调用)、`creator`(String(128) nullable)、`modifier`(String(128) nullable)、`gmt_create`(DateTime, `server_default=func.now()`)、`gmt_modify`(DateTime, `server_default=func.now()`, `onupdate=func.now()`);`__table_args__ = (UniqueConstraint("config_name", "env", name="uk_task_config_name_env"), Index("idx_task_config_env", "env"))`。`from agentclaw.community.utils.env_utils import get_current_env`。

- [ ] **Step 4: 写 DDL 文件**

`core/task/task_config/sql/2026-11-02_task_config.sql`:照抄 spec 的「### DDL」整段(`CREATE TABLE IF NOT EXISTS task_config ...`,列注释、`uk_task_config_name_env`、`idx_task_config_env`、`DEFAULT CHARSET = utf8mb4`)。文件头加一行 `-- 落地说明:见 spec 2026-11-02-task-config §DDL;ORM 在 core/task/task_config/orm.py,两套保持平行。`

- [ ] **Step 5: 登记 `import_all_models`**

在 `core/schema.py` 的 `import_all_models()` 函数体里,与现存 `import agentclaw.community.core.task.repository.models  # noqa: F401` 同款式加一行:
`import agentclaw.community.core.task.task_config.orm  # noqa: F401  task_config`(放在 task 域 import 簇里,保持就近)。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/test_task_config_orm.py -v`
Expected: PASS(两例)。

- [ ] **Step 7: Commit**

```bash
git add src/agentclaw/community/core/task/task_config/__init__.py \
        src/agentclaw/community/core/task/task_config/orm.py \
        src/agentclaw/community/core/task/task_config/sql/2026-11-02_task_config.sql \
        src/agentclaw/community/core/schema.py \
        tests/community/core/task/test_task_config_orm.py
git commit -m "feat(backend): add task_config ORM model, DDL, and schema registration"
```

---

## Task 2: Domain record + repository (upsert + get_by_name)

**Files:**
- Create: `src/agentclaw/community/core/task/task_config/models.py`、`.../repository_protocol.py`、`.../repository.py`
- Test: `tests/community/core/task/test_task_config_repository.py`

**Interfaces:**
- Consumes: `TaskConfigModel`(Task 1)、`agentclaw.community.plugins.DatabasePlugin`(`@inject __init__(db: DatabasePlugin)`;`with self._db.orm_session() as db:` 提供 session)。
- Produces: `TaskConfigRepositoryProtocol.upsert(*, config_name, config_type, config_value, env, operator=None) -> int`(返回 id)、`get_by_name(*, env, config_name) -> TaskConfigRecord | None`;配置值以**已序列化的 JSON 文本**传入(repository 不解释语义,只存取)。

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_task_config_repository.py
from agentclaw.community.core.base import Base
from agentclaw.community.core.schema import import_all_models
from agentclaw.community.core.task.task_config.repository import TaskConfigRepository
# DatabasePlugin 测试替身:见 fixtures 既有模式(内存 SQLite);本测试用 in-memory engine 直注

def _repo_with_memory_db():
    import_all_models()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # 用一个把 orm_session 指向该 engine 的最小 DatabasePlugin 替身
    ...  # 用 task/repository 既有测试同款的内存 db 替身(见 resources/taskrepository 测试惯例)

def test_upsert_insert_sets_creator_and_modifier():
    repo = _repo_with_memory_db()
    rid = repo.upsert(config_name="skill_set", config_type="set",
                      config_value='["a"]', env="pre", operator="u1")
    row = repo.get_by_name(env="pre", config_name="skill_set")
    assert row.creator == "u1" and row.modifier == "u1"
    assert row.config_value == '["a"]'

def test_upsert_preserves_creator_and_gmt_create():
    repo = _repo_with_memory_db()
    repo.upsert(config_name="x", config_type="single_string",
                config_value='"v1"', env="pre", operator="u1")
    first = repo.get_by_name(env="pre", config_name="x")
    repo.upsert(config_name="x", config_type="single_string",
                config_value='"v2"', env="pre", operator="u2")
    second = repo.get_by_name(env="pre", config_name="x")
    assert second.config_value == '"v2"'
    assert second.creator == first.creator == "u1"            # creator 不覆盖
    assert second.modifier == "u2"                            # modifier 刷新
    assert second.gmt_create == first.gmt_create              # gmt_create 不覆盖
    # gmt_modify 刷新(可能同瞬,允许 >= )
    assert second.gmt_modify >= first.gmt_modify

def test_upsert_changes_type_on_overwrite():
    repo = _repo_with_memory_db()
    repo.upsert(config_name="x", config_type="bool", config_value="true", env="pre")
    repo.upsert(config_name="x", config_type="set", config_value='["a"]', env="pre")
    assert repo.get_by_name(env="pre", config_name="x").config_type == "set"

def test_get_by_name_absent_returns_none():
    repo = _repo_with_memory_db()
    assert repo.get_by_name(env="pre", config_name="nope") is None

def test_env_isolation_same_name_coexist():
    repo = _repo_with_memory_db()
    repo.upsert(config_name="x", config_type="single_string", config_value='"pre_v"', env="pre")
    repo.upsert(config_name="x", config_type="single_string", config_value='"prod_v"', env="prod")
    assert repo.get_by_name(env="pre", config_name="x").config_value == '"pre_v"'
    assert repo.get_by_name(env="prod", config_name="x").config_value == '"prod_v"'
```

> 内存 db 替身:复用 `tests/community/repository/task/...` 既有把 `DatabasePlugin` 接到内存 SQLite 的模式;若没有现成 helper,本步就写一个模块级最小替身(实现 `orm_session()` 上下文管理器返回 SQLAlchemy `Session`),放测试文件内。不要污染生产代码。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/test_task_config_repository.py -v`
Expected: FAIL —— `TaskConfigRepository` 未定义。

- [ ] **Step 3: 实现 `models.py`**

`@dataclass(slots=True) TaskConfigRecord`:字段 `id: int`、`config_name: str`、`config_type: str`、`config_value: str`(原始 JSON 文本)、`env: str`、`creator: str | None`、`modifier: str | None`、`gmt_create: datetime | None`、`gmt_modify: datetime | None`。

- [ ] **Step 4: 实现 `repository_protocol.py`**

```python
@runtime_checkable
class TaskConfigRepositoryProtocol(Protocol):
    @abstractmethod
    def upsert(self, *, config_name: str, config_type: str, config_value: str,
               env: str, operator: str | None = None) -> int: ...
    @abstractmethod
    def get_by_name(self, *, env: str, config_name: str) -> TaskConfigRecord | None: ...
```
规则与 `core/repository/protocols/config.py` 同:`@runtime_checkable` + `@abstractmethod`,全 `*,` 关键字参数,`...` body;`TaskConfigRecord` 在 `TYPE_CHECKING` 下导入。

- [ ] **Step 5: 实现 `repository.py`**

`class TaskConfigRepository(TaskConfigRepositoryProtocol)`、`@inject def __init__(self, db: DatabasePlugin)`;两方法各 `with self._db.orm_session() as db:`。`upsert` 按 `db.bind.dialect.name` 分支:
- `sqlite`:`INSERT INTO task_config (...) VALUES (...) ON CONFLICT(config_name, env) DO UPDATE SET config_type=excluded.config_type, config_value=excluded.config_value, modifier=excluded.modifier, gmt_modify=CURRENT_TIMESTAMP`(SET 列**不含** `creator`/`gmt_create`)。用 `sqlalchemy.dialects.sqlite.insert(...)` 构造,`.on_conflict_do_update(index_elements=["config_name","env"], set_=...)`。
- `mysql`:`sqlalchemy.dialects.mysql.insert(...).on_duplicate_key_update(...)`(同样只更新 `config_type`/`config_value`/`modifier`,通过 `gmt_modify` 的 `onupdate=func.now()` 自动刷新)。
两路径插入新行时 `creator=operator, modifier=operator`(`gmt_create/gmt_modify` 由 server_default 设)。返回新行/既有行 `id`。
`get_by_name`:`SELECT ... WHERE env=:env AND config_name=:cn LIMIT 1`, 映射到 `TaskConfigRecord`。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/test_task_config_repository.py -v`
Expected: PASS(5 例,含 ReviewFocus #3、#4 的仓储层验证)。

- [ ] **Step 7: Commit**

```bash
git add src/agentclaw/community/core/task/task_config/{models,repository_protocol,repository}.py \
        tests/community/core/task/test_task_config_repository.py
git commit -m "feat(backend): add task_config repository with env-scoped upsert"
```

---

## Task 3: Service-API Protocol + service (value encode/validate + match)

**Files:**
- Create: `src/agentclaw/community/core/task/task_config/task_config_service_protocol.py`、`.../service.py`
- Create: `src/agentclaw/community/api/task/task_config_service.py`
- Test: `tests/community/core/task/test_task_config_service.py`

**Interfaces:**
- Consumes: `TaskConfigRepositoryProtocol`(Task 2)。
- Produces: `TaskConfigServiceProtocol.set_config(*, config_name, config_type, config_value, env, operator=None) -> int`(`config_value` 是**已反序列化的 Python 值**:single_string→str、bool→bool、set→list[str]);`match(*, config_name, value, env) -> bool`(`value` 是调用方待判定的单个值:set/single_string→str、bool→bool)。`TaskConfigServiceProtocol` 由 `api/task/task_config_service.py` 再导出,供 router `Injected(...)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/community/core/task/test_task_config_service.py
class _FakeRepo:  # 内存实现 TaskConfigRepositoryProtocol 协议
    def __init__(self): self.rows = {}
    def upsert(self, *, config_name, config_type, config_value, env, operator=None):
        key = (env, config_name)
        existing = self.rows.get(key)
        creator = existing.creator if existing else operator
        row = _Row(id=existing.id if existing else len(self.rows)+1,
                   config_name=config_name, config_type=config_type,
                   config_value=config_value, env=env,
                   creator=creator, modifier=operator)
        self.rows[key] = row; return row.id
    def get_by_name(self, *, env, config_name): return self.rows.get((env, config_name))

def _svc(): return TaskConfigService(repo=_FakeRepo())
# (TaskConfigService 是 @inject;测试里直接构造传 repo,绕过 injector — 见系统已有 service 测试惯例)

def test_match_set_member_present_and_absent():
    s = _svc(); s.set_config(config_name="skill_set", config_type="set",
                             config_value=["a","b"], env="pre", operator="u")
    assert s.match(config_name="skill_set", value="a", env="pre") is True
    assert s.match(config_name="skill_set", value="z", env="pre") is False   # ReviewFocus #1

def test_match_unknown_name_returns_false():
    assert _svc().match(config_name="nope", value="a", env="pre") is False   # ReviewFocus #2

def test_match_single_string_and_bool():
    s = _svc()
    s.set_config(config_name="ss", config_type="single_string", config_value="hello", env="pre")
    assert s.match(config_name="ss", value="hello", env="pre") is True
    assert s.match(config_name="ss", value="world", env="pre") is False
    s.set_config(config_name="fl", config_type="bool", config_value=True, env="pre")
    assert s.match(config_name="fl", value=True, env="pre") is True
    assert s.match(config_name="fl", value=False, env="pre") is False

def test_set_invalid_type_raises():
    import pytest
    with pytest.raises(ValueError):
        _svc().set_config(config_name="x", config_type="bogus", config_value="y", env="pre")

def test_set_value_shape_mismatch_raises():
    import pytest
    s = _svc()
    with pytest.raises(ValueError): s.set_config(config_name="x", config_type="set", config_value="not_a_list", env="pre")
    with pytest.raises(ValueError): s.set_config(config_name="y", config_type="bool", config_value="not_bool", env="pre")

def test_match_rejects_value_type_mismatch():
    import pytest
    s = _svc()
    s.set_config(config_name="fl", config_type="bool", config_value=True, env="pre")
    with pytest.raises(ValueError): s.match(config_name="fl", value="true", env="pre")     # ReviewFocus #5: 不静默匹配

def test_match_isolated_across_envs():
    s = _svc()
    s.set_config(config_name="x", config_type="single_string", config_value="pre_v", env="pre")
    assert s.match(config_name="x", value="pre_v", env="prod") is False                    # ReviewFocus #4
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/test_task_config_service.py -v`
Expected: FAIL —— `TaskConfigService` 未定义。

- [ ] **Step 3: 实现 `task_config_service_protocol.py`**

```python
@runtime_checkable
class TaskConfigServiceProtocol(Protocol):
    @abstractmethod
    def set_config(self, *, config_name: str, config_type: str, config_value, env: str,
                   operator: str | None = None) -> int: ...
    @abstractmethod
    def match(self, *, config_name: str, value, env: str) -> bool: ...
```
(config_value/value 故意不写死类型,文档里注释三种 type 各自形态。)

- [ ] **Step 4: 实现 `service.py`**

`class TaskConfigService(TaskConfigServiceProtocol)`、`@inject def __init__(self, repo: TaskConfigRepositoryProtocol)`。

- 校验 `config_type ∈ {"single_string","bool","set"}`,否则 `raise ValueError`。
- `set_config` 校验 `config_value` 形态:`single_string`→`isinstance(str)`、`bool`→`isinstance bool`(注意 `bool` 是 `int` 子类,先判 `isinstance(x, bool)`)、`set`→`isinstance(list)` 且元素全 str;不符 `raise ValueError`。然后 `json.dumps(config_value)` 得 JSON 文本(确保中文/ascii 一致:用 `json.dumps(v, ensure_ascii=False)`),调 `self._repo.upsert(...)` 返回 id。
- `match`:`row = repo.get_by_name(env=env, config_name=config_name)`;`row is None` → 返回 `False`。`stored = json.loads(row.config_value)`。按 `row.config_type`:对 `value` 形态校验(`single_string`/`set` 需 `isinstance(value, str)`;`bool` 需 `isinstance(value, bool)`,且**不能**对 `str "true"` 放行 —— 不符 `raise ValueError`)。判定:
  - `single_string`:`value == stored`
  - `bool`:`value == stored`(且两端都已是 `bool`)
  - `set`:`value in stored`(`stored` 是 list[str])
  返回 bool。**绝不让 `str "true"` 与 `bool True` 误判**(先形态校验,再比较)。

- [ ] **Step 5: 实现 `api/task/task_config_service.py`**

照 `api/system_config_service.py` 模板(再导出):
```python
from agentclaw.community.core.task.task_config.task_config_service_protocol import TaskConfigServiceProtocol
__all__ = ["TaskConfigServiceProtocol"]
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/test_task_config_service.py -v`
Expected: PASS(7 例,含 ReviewFocus #1/#2/#4/#5)。

- [ ] **Step 7: Commit**

```bash
git add src/agentclaw/community/core/task/task_config/{task_config_service_protocol,service}.py \
        src/agentclaw/community/api/task/task_config_service.py \
        tests/community/core/task/test_task_config_service.py
git commit -m "feat(backend): add task_config service with typed value match"
```

---

## Task 4: DI module + container registration

**Files:**
- Create: `src/agentclaw/community/di/modules/task_config_module.py`
- Modify: `src/agentclaw/community/di/container.py`(import 段 ~117-122;base 列表 ~208-230)
- Test: 通过既有端点测试集合保证容器装配不挂(无需新测试文件)。

**Interfaces:**
- Consumes: `TaskConfigRepository`、`TaskConfigRepositoryProtocol`、`TaskConfigService`、`TaskConfigServiceProtocol`(Task 2/3)。
- Produces: `Injected(TaskConfigServiceProtocol)` 解析到 `TaskConfigService` 单例(供 Task 5/6 router)。

- [ ] **Step 1: 实现 `task_config_module.py`**(照 `system_config_module.py` 结构)

```python
from injector import Binder, Module, singleton, inject, provider
from agentclaw.community.api.task.task_config_service import TaskConfigServiceProtocol
from agentclaw.community.core.task.task_config.repository import TaskConfigRepository
from agentclaw.community.core.task.task_config.repository_protocol import TaskConfigRepositoryProtocol
from agentclaw.community.core.task.task_config.service import TaskConfigService

class TaskConfigModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(TaskConfigRepository, to=TaskConfigRepository, scope=singleton)
        binder.bind(TaskConfigRepositoryProtocol, to=TaskConfigRepository, scope=singleton)
        binder.bind(TaskConfigService, to=TaskConfigService, scope=singleton)

    @singleton
    @provider
    @inject
    def _task_config_service_protocol(self, svc: TaskConfigService) -> TaskConfigServiceProtocol:
        return svc
```

- [ ] **Step 2: 注册进 `container.py`**

`container.py` import 段加 `from agentclaw.community.di.modules.task_config_module import TaskConfigModule`(紧邻 `TaskModule` import)。base 模块列表里加 `TaskConfigModule(),`(紧邻 `TaskModule(),`,~228 行)。

- [ ] **Step 3: 验证容器装配不挂**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/framework/test_coverage_gate.py -v`
Expected: PASS(若 `TaskConfigModule` 绑定/类型错,容器构造将抛,coverage gate 与所有用真容器的测试将集中 500/fail)。同时跑一个既有 task 端点测试作廉价冒烟:
Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_openapi_task_endpoints.py -v`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add src/agentclaw/community/di/modules/task_config_module.py \
        src/agentclaw/community/di/container.py
git commit -m "feat(backend): wire task_config service/repo via DI module"
```

---

## Task 5: Internal task-config sub-router (set + match) + endpoint coverage

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/task_config_router.py`
- Modify: `src/agentclaw/community/adapters/http/task/router.py`(import + `include_router`)
- Test: `tests/community/endpoints/test_task_config_router.py`(内部 `set`/`match` 各 happy+error)

**Interfaces:**
- Consumes: `TaskConfigServiceProtocol`(Task 4,`Injected(...)`)、`get_current_user`(`from agentclaw.community.adapters.http.auth.dependencies import get_current_user`)、`AuthenticatedUser`(`...auth.models`)、`Envelope`/`envelope`/`envelope_errors`(`from agentclaw.community.adapters.http.openapi_v1.{contracts,responses}`)。
- Produces: 两条活路由 `POST /api/v1/collaboration/tasks/config/set`、`POST /api/v1/collaboration/tasks/config/match`(供 coverage gate)。

- [ ] **Step 1: 写失败测试 / endpoint 用例**

```python
# tests/community/endpoints/test_task_config_router.py
from tests.community.framework import endpoint_test, CaseInput, ExpectSuccess, ExpectError

_USER = {"x-user-id": "u_task_config_test"}

def _seed_set(world):
    svc = world.get(__import__("agentclaw.community.api.task.task_config_service", fromlist=["TaskConfigServiceProtocol"]).TaskConfigServiceProtocol)
    svc.set_config(config_name="skill_set", config_type="set",
                   config_value=["bot_a"], env="dev", operator="seeder")

@endpoint_test(method="POST", path="/api/v1/collaboration/tasks/config/set", scenario="happy",
    input=CaseInput(headers=_USER, json_body={"config_name":"ns","config_type":"set","config_value":["a","b"]}),
    expect=ExpectSuccess(status=200, json_contains={"code":200000}))
def set_happy(): """write a set config"""
# (可选 extra_assertions:回查 world.get(...).match 命中 "a")

@endpoint_test(method="POST", path="/api/v1/collaboration/tasks/config/set", scenario="error",
    input=CaseInput(headers=_USER, json_body={"config_name":"ns","config_type":"bogus","config_value":["a"]}),
    expect=ExpectError(status=422))                       # config_type 非法 → pydantic 422
def set_error(): """invalid config_type rejected"""
# ↑ 前提:请求 schema 把 config_type 声明为 Literal["single_string","bool","set"]

@endpoint_test(method="POST", path="/api/v1/collaboration/tasks/config/match", scenario="happy",
    input=CaseInput(headers=_USER, json_body={"config_name":"skill_set","value":"bot_a"}, seed=None,
    expect=ExpectSuccess(status=200, json_contains={"code":200000, "data":{"matched": True}}))
# (论:测试在 DEPLOY_PROFILE=test 下 get_current_env() 返回 "dev",seed 写 env="dev" 的配置;
#  因此 seed 必须用 svc.set_config(..., env="dev") —— 见 _seed_set 里硬写 "dev" 与 spec §测试 一致。
#  注意:env 不要从请求体传,seed 与 router 都走 "dev";若想稳,seed 调 env_utils.get_current_env() 取当前值再写。)
def match_happy(): """match a present set member"""

@endpoint_test(method="POST", path="/api/v1/collaboration/tasks/config/match", scenario="error",
    input=CaseInput(headers=_USER, json_body={"config_name":"skill_set"}),   # 缺 value
    expect=ExpectError(status=422))
def match_error(): """missing value rejected"""
```

> env 说明:`DEPLOY_PROFILE=test`(无 `SERVER_ENV`)下 `get_current_env()` 返回 `"dev"`,所以内部 match happy 的 seed 写 `env="dev"`。为防 conftest 日后改 `SERVER_ENV`,seed 用 `from agentclaw.community.utils.env_utils import get_current_env; env=get_current_env()` 写,不硬编码。`happy` match 需先 seed 一条配置(`seed=_seed_set`),用 `world.get(TaskConfigServiceProtocol)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_config_router.py -v`
Expected: FAIL —— 路由不存在(coverage gate 也会报新路由未覆盖,应等到路由建后)。

- [ ] **Step 3: 实现 `task_config_router.py`**

`APIRouter(prefix="/config", tags=["task-config"])`。

Pydantic 请求 schema(`from pydantic import BaseModel, Field` + `typing.Literal`):
- `TaskConfigSetRequest`:`config_name: str`、`config_type: Literal["single_string","bool","set"]`、`config_value: str | bool | list[str]`(set 的元素限 str,top-level 不接 dict)。
- `TaskConfigMatchRequest`:`config_name: str`、`value: str | bool`。

两个 handler:`@router.post("/set", response_model=Envelope[dict])` + `@envelope_errors`,签名 `body: TaskConfigSetRequest, user: AuthenticatedUser = Depends(get_current_user), service: TaskConfigServiceProtocol = Injected(TaskConfigServiceProtocol)`。`env = env_utils.get_current_env()`;`operator = user.operatorName or user.staffId`;`rid = service.set_config(config_name=body.config_name, config_type=body.config_type, config_value=body.config_value, env=env, operator=operator)`;返回 `envelope({"config_id": rid}, request)`。

`/match` 同型:`matched = service.match(config_name=body.config_name, value=body.value, env=env)`;返回 `envelope({"matched": matched}, request)`。`request: Request` 入参用于 `envelope(...)` 取 trace id。

import:`from agentclaw.community.di import Injected`、`from agentclaw.community.adapters.http.auth.dependencies import get_current_user`、`from agentclaw.community.adapters.http.auth.models import AuthenticatedUser`、`from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope`、`from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors`、`from agentclaw.community.utils import env_utils`(用 `env_utils.get_current_env()`)、`from agentclaw.community.api.task.task_config_service import TaskConfigServiceProtocol`。

- [ ] **Step 4: 挂入 umbrella router**

`adapters/http/task/router.py`:import `from agentclaw.community.adapters.http.task.task_config_router import router as task_config_router`,并在 `task_internal_router` 构造处 `task_internal_router.include_router(task_config_router)`(与现存 relay/bbs 等 include 同位置,~91-99)。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_config_router.py tests/community/framework/test_coverage_gate.py -v`
Expected: PASS(4 端点用例 + coverage gate 不因新内部路由报 missing —— 因 happy/error 都配了)。

- [ ] **Step 6: Commit**

```bash
git add src/agentclaw/community/adapters/http/task/task_config_router.py \
        src/agentclaw/community/adapters/http/task/router.py \
        tests/community/endpoints/test_task_config_router.py
git commit -m "feat(backend): add internal task_config set/match routes under collaboration/tasks"
```

---

## Task 6: Public openapi match handler + authz/admission + endpoint coverage

**Files:**
- Modify: `src/agentclaw/community/adapters/http/openapi_v1/task/router.py`
- Modify: `src/agentclaw/community/adapters/http/openapi_v1/authorization_rules_task.py`
- Modify: `src/agentclaw/community/adapters/http/openapi_v1/admission_task_routes.py`
- Test: 追加进 `tests/community/endpoints/test_task_config_router.py`(或新建对应 openapi 用例,复用 `test_openapi_task_endpoints.py` 的签名 principal helper)

**Interfaces:**
- Consumes: `TaskConfigServiceProtocol`(Task 4)、`require_principal`/`PrincipalDep`(`openapi_v1/task/router.py` 已有 `from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal`,`Principal = VerifiedCaller`)、`PublicAPIRoute`(router 已设 `route_class`)、`envelope`/`envelope_errors`/`Envelope`。
- Produces: 活路由 `POST /openapi/v1/collaboration/tasks/config/match`。

- [ ] **Step 1: 写失败测试 / endpoint 用例**(复用 `test_openapi_task_endpoints.py` 的 principal 签名工具)

```python
# 在 test_task_config_router.py 或 test_openapi_task_endpoints.py 风格的文件里
import jwt
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config

_KEY = "public-task-framework-signing-key-32b"; _CALLER = "task-config-caller"
class _Secret: ...               # 照 test_openapi_task_endpoints.py:_Secret/_Resolver 同款
def _boot_verifier(world): init_principal_verifier_config(_Resolver(), "test-key", strict=False)
def _principal():
    import time
    now = int(time.time())
    return jwt.encode({"iss":"gateway","aud":"backend","iat":now,"exp":now+3600,
        "principals":[{"type":"user","subject":{"id":_CALLER,"username":"task-config@example.test"}}]},
        _KEY, algorithm="HS256")
def _headers(): return {PRINCIPAL_HEADER: _principal()}

def _seed_set(world):
    svc = world.get(TaskConfigServiceProtocol)
    from agentclaw.community.utils.env_utils import get_current_env
    svc.set_config(config_name="skill_set", config_type="set",
                   config_value=["bot_a"], env=get_current_env(), operator="seeder")

@endpoint_test(method="POST", path="/openapi/v1/collaboration/tasks/config/match", scenario="happy",
    input=CaseInput(headers=_headers(), json_body={"config_name":"skill_set","value":"bot_a"}, seed=None),
    expect=ExpectSuccess(status=200, json_contains={"code":200000, "data":{"matched": True}}))
def openapi_match_happy(): """public match with signed principal"""        # happy:需先 seed(_seed_set)

@endpoint_test(method="POST", path="/openapi/v1/collaboration/tasks/config/match", scenario="error",
    input=CaseInput(json_body={"config_name":"skill_set","value":"bot_a"}, seed=_boot_verifier),
    expect=ExpectError(status=401, json_contains={"code":401000, "message":"Unauthorized"}))
def openapi_match_unauth(): """no principal → 401"""
```
> happy 的 seed:`seed=_boot_verifier` 安装签名验证器,再叠加一条配置 seed;若框架 seed 只接一个 callable,把两步合并进一个 `_seed` 内先 `_boot_verifier(world)` 再 `world.get(...).set_config(...)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_config_router.py -v -k openapi`
Expected: FAIL —— `/openapi/v1/collaboration/tasks/config/match` 不存在;若你已有路由但缺 AUTHORIZATION/ADMISSION 行,装配期 `PublicRouteNotAuthorized` 报错。

- [ ] **Step 3: 在 `openapi_v1/task/router.py` 加 match handler**

照 `/dashboard`、`/list` 同形:`@router.post("/config/match", response_model=Envelope[dict])` + `@envelope_errors`,签名 `body: TaskConfigMatchRequest, request: Request, principal: PrincipalDep, service: TaskConfigServiceProtocol = Injected(TaskConfigServiceProtocol)`。`env = env_utils.get_current_env()`;`matched = service.match(config_name=body.config_name, value=body.value, env=env)`;`return envelope({"matched": matched}, request)`。`principal` 不消费,可 `del principal`(与现存 read handler 同)。`TaskConfigMatchRequest` 复用 Task 5 在 `task_config_router.py` 定义的 schema(或公共 schema 模块);若跨 router 引用不顺,在 openapi router 内重复定义同款 Literal schema(保持参数语义一致)。import `TaskConfigServiceProtocol` 自 `agentclaw.community.api.task.task_config_service`。

- [ ] **Step 4: 登记 AUTHORIZATION 与 ADMISSION**

- `authorization_rules_task.py`:加 `config/match` → `NoCheck(...)`(与 execute/dashboard/list 同段,无 addressed-bot grant;match 不针对某个 bot 资源)。
- `admission_task_routes.py` 的 `TASK_PUBLIC_ADMISSION`:加 `config/match` → `AdmissionMode.OPEN`(与 execute/dashboard/list 同段 —— 任意已认证 principal 可读)。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_task_config_router.py tests/community/framework/test_coverage_gate.py -v`
Expected: PASS(全部 task_config 端点用例 + coverage gate:三条活路由各 happy+error 齐)。

- [ ] **Step 6: 跑全量回归冒烟**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community -q`
Expected: 无新增失败(既有基线失败保持基线,不新增与本特性无关的红)。

- [ ] **Step 7: Commit**

```bash
git add src/agentclaw/community/adapters/http/openapi_v1/task/router.py \
        src/agentclaw/community/adapters/http/openapi_v1/authorization_rules_task.py \
        src/agentclaw/community/adapters/http/openapi_v1/admission_task_routes.py \
        tests/community/endpoints/test_task_config_router.py
git commit -m "feat(backend): expose task_config match on public openapi surface"
```

---

## Notes for the executor

- `env` 在测试 profile 下解析为 `"dev"`;所有 seed 一律调 `env_utils.get_current_env()` 取当前值,不硬编码 "dev"(spec §测试决定 #6 的延伸)。
- `bool` 校验须先 `isinstance(x, bool)` 再判 `int`,否则 `True` 被当 `1`;且 `str "true"` **不得**与 `bool True` 比较相等(ReviewFocus #5)。
- `@envelope_errors` 只映射 `ENVELOPE_ERRORS` 里的域异常;未被映射的异常会 re-raise → 500。Schema 形态不符走 pydantic 422(Task 5 的 set/match error 用例都走 422);依赖存行 type 才能判定的形态不符在 service 抛 `ValueError`(Task 3 单测覆盖),但**端点 error 用例不依赖这条映射**(用 422 与 401 这两条已映射的稳态)。如发现 `ValueError` 在端点层变 500,在 Task 5/6 改用 `/openapi_v1/responses.py` 已映射的 bad-request 域异常类(镜像现存 task handler 的 bad-input 处理),并在该任务里加注。
- DDL 与 ORM 必须平行:`UniqueConstraint(config_name, env)`、`idx_task_config_env`、列名与可空性一致,否则 test/ corp 行为分叉。
- 不动 `core/task` 既有协作任务代码;新代码全在 `core/task/task_config/` 子包。
