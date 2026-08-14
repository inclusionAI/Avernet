# Phase 0: Tenant Guard 扩展到 ac_resource + ac_bot_publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PR #456(Session 0)的 tenant 隔离机制扩展到 `ac_resource` 一张表,使 resources 板块的读写在方向 A 下自动按租户隔离——handler 仍全 stub,legacy 线上行为零变化。

**Architecture:** 复制 Session 0 的 ORM 双 guard 模式(read guard 用 `do_orm_execute` + `with_loader_criteria` 隐式过滤;insert guard 用 `before_insert` stamp + 冲突 raise)。把单 `BotModel` guard 工厂化:`_GUARDED_MODELS = (BotModel, ResourceModel)`,read guard 遍历各 model 加 criteria,**insert guard 按 model 各注册一份**。先写红测试证明现状能漏,再落地 guard 转绿。

**Tech Stack:** Python / SQLAlchemy 2.0(`do_orm_execute` + `with_loader_criteria`)/ pytest / FastAPI。`avernet_tenant` ContextVar + `avernet_tenant_scope` 已由 Session 0(`utils/avernet_tenant.py`)交付,本 plan 不动。

**前置依赖(spec 锚点):** `docs/superpowers/specs/2026-07-27-bots-domain-three-category-design.md` §3.1、§3.2、§4、§6.3 表格里"潜在漏点"。本 plan 只覆盖该 spec 的 §8 Phase 0。

**范围说明(为何不含 `ac_bot_publish`):** 上一轮探查把 `ac_bot_publish` 标为"routines/identity 间接依赖的漏点",但代码级核实证明它**不属本期范围**:
- `cron_relay.py:542,713` 读 publish 表**只在 `runtime_stage != DRAFT`**(verify/online 运行态);openapi_v1 routines 在 DRAFT 运行态调用(`forward_request:601` 不经 `_publish_repo`)。
- `identity.py:513` 读 publish 表**只带 `publish_id` 时**;spec §3.3 已定 openapi 不暴露 publish_id,消金只读 draft。
- 故本期三个板块的 openapi_v1 handler **不会触发对 `ac_bot_publish` 的读取**。该表加列留给其真正 owner(totalfrank / service_bot 工作线),或等 routines/identity 的 handler 真要支持 verify/online 运行态时再处理。**YAGNI:本期不做。**

**DDL 硬约束(spec §6.3):** `ac_resource` 的 `ALTER TABLE ADD COLUMN avernet_tenant ... NOT NULL DEFAULT 'teamclaw'` **必须由平台 out-of-band 先于本 plan 代码部署执行**。代码先于 DDL 部署会让 `SELECT avernet_tenant` 报错导致线上读全挂。**Task 1 是 DDL 准备,实际执行在平台侧(非 git)——本 plan 仅记录 DDL shape,代码 Task 2+ 必须在 DDL 落地后才能进环境。**

---

## File Structure

- **Modify** `src/backend/src/agentclaw/community/plugin_api/models.py`
  - `ResourceModel`(`:189`)加 `avernet_tenant` 列
  - guard 工厂:`_GUARDED_MODELS` 元组 + read guard 遍历 + insert guard 工厂;`BotModel` 的既有 guard 重构进工厂(行为保持)
- **Create** `src/backend/tests/community/plugins/test_resource_tenant_isolation.py`
  - cross-tenant 红测试(对照 Session 0 `test_bot_tenant_isolation.py` 形态)
- **Create** `src/backend/tests/community/plugins/test_resource_tenant_guard.py`
  - guard 绿测试(对照 Session 0 `test_bot_tenant_guard.py`)
- **Create** `src/backend/tests/community/plugins/test_multi_model_guard_spike.py`
  - 验证多 model 在单 listener 链式 `.options()` 写法(类比 Session 0 Task 1 spike,throwaway)
- **Possibly Modify** `src/backend/src/agentclaw/community/core/resources/services/README.md`(若新增 `utils.avernet_tenant` import)

---

## Task 1: DDL out-of-band 准备(平台侧,非 git)

**Files:** 无 git 文件改动(spec §6.3 决策:DDL 由平台 out-of-band 执行,不入库 migration)

- [ ] **Step 1: 记录 DDL shape(已在 spec §6.3,此处复述作 plan 锚点)**

```sql
ALTER TABLE ac_resource
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';
```

- [ ] **Step 2: 提交平台执行 DDL(与平台/DBA 协同)**

由 lucas-xzp 在平台侧提交 DDL 工单。**执行前不阻塞写代码**(本地 SQLite 从 `Base.metadata.create_all` 建表,不依赖 prod DDL),但**代码部署到任何环境前 DDL 必须先就位**。

- [ ] **Step 3: 确认 DDL 已落地(部署前的 gate)**

部署前确认 `ac_resource.avernet_tenant` 列存在。若 DDL 未就位,**stop**,不允许部署后续 Task 的代码。

---

## Task 2: Spike — 验证多 model 单 listener 链式 `.options()` 写法

> **⚠️ 决策(2026-07-28):本 Task 跳过。** Session 0 Task 1 已验证"单 listener + `with_loader_criteria` + `Query.update()`/`DELETE`"对 BotModel 生效(SQLAlchemy 2.0.51);"链式多 `.options()`"是 SQLAlchemy 标准行为,非未知领域。更关键的:**本 spike 要求 `ResourceModel.avernet_tenant` 列已存在**(spike 代码引用 `m.avernet_tenant`),但该列要 Task 4 才加——spike 无法在 Task 4 之前独立跑。**"证明现状能漏"的语义由 Task 3 红测试承担**(此时 ResourceModel 无 avernet_tenant 列 → 红在"列不存在"),TDD 最小步进更优。**退路**:若 Task 5 装 guard 时链式多 criteria 真出问题,改"每 model 一条 listener"(成本可控)。
>
> **Files:** 无(spike 跳过,不创建文件)

**Files:**
- Create: `src/backend/tests/community/plugins/test_multi_model_guard_spike.py`

**Why:** Session 0 的 read guard 只对单 `BotModel` 加一条 `with_loader_criteria`。本 plan 要在**同一条 `do_orm_execute` listener** 里对 2 个 model(`BotModel` + `ResourceModel`)各加一条 criteria(`stmt = stmt.options(...).options(...)`)。Session 0 没验过这种多 criteria 链式写法(单 model)对 `ResourceModel` 的 `Query.update()`/`Query.delete()` 是否都生效。先红→绿 spike 落实,再写进工厂。

- [ ] **Step 1: 写 spike 测试(BotModel + ResourceModel 双 model 隔离)**

```python
"""Spike: confirm a single do_orm_execute listener chaining multiple
with_loader_criteria options isolates each guarded model independently.

Mirrors Session 0 Task 1 spike (SQLAlchemy 2.0.51) but extended to a tuple of
models. Verifies Query.update()/Query.delete() are constrained for ResourceModel
too, not just SELECT.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, with_loader_criteria

from agentclaw.community.plugin_api.models import BotModel, ResourceModel
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope, get_current_avernet_tenant,
)

pytestmark = pytest.mark.integration


class _DB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


_GUARDED = (BotModel, ResourceModel)


def _install_multi_listener(session_class):
    def _guard(orm_execute_state):
        if orm_execute_state.is_column_load or orm_execute_state.is_relationship_load:
            return
        if not (orm_execute_state.is_select or orm_execute_state.is_update or orm_execute_state.is_delete):
            return
        stmt = orm_execute_state.statement
        for m in _GUARDED:
            # DIRECT EXPRESSION — never a lambda (lambda caches → leaks, spec §6.6)
            stmt = stmt.options(with_loader_criteria(
                m, m.avernet_tenant == get_current_avernet_tenant(),
                include_aliases=True))
        orm_execute_state.statement = stmt
    event.listen(session_class, "do_orm_execute", _guard)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'spike.db'}",
                          connect_args={"check_same_thread": False})
    BotModel.__table__.create(engine)
    ResourceModel.__table__.create(engine)
    d = _DB(engine)
    _install_multi_listener(d._f)  # wire the multi-criteria listener for this Session class
    return d


def test_two_models_each_isolated_under_one_listener(db):
    """Under tenant-a, seed a Resource row; tenant-b sees none via select/update/delete."""
    # seed under tenant-a (manual stamp — before_insert not installed in this spike)
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            s.add(ResourceModel(name="r", resource_type="file",
                                avernet_tenant="tenant-a"))
    # under tenant-b, the resource is invisible
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            assert s.query(ResourceModel).filter_by(name="r").first() is None
            # cross-tenant update touches 0 rows
            rc = s.query(ResourceModel).filter_by(name="r").update(
                {"status": "deleted"}, synchronize_session=False)
            assert rc == 0
    # tenant-a's row survived untouched
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            r = s.query(ResourceModel).filter_by(name="r").first()
            assert r is not None and r.status != "deleted"
```

> **注意:** spike 用**手动 stamp**(构造行时直接传 `avernet_tenant="tenant-a"`)——不依赖 `before_insert`(那正是 Task 4 要装的)。spike 只验 read listener 多 criteria 是否对 `ResourceModel` 的 select+update+delete 都生效。

- [ ] **Step 2: 跑 spike,确认绿(若红:多 criteria 链式不生效,需改方案)**

Run: `cd src/backend && uv run pytest tests/community/plugins/test_multi_model_guard_spike.py -v`

Expected: PASS。若 FAIL:`with_loader_criteria` 多次 `.options()` 链式对 `ResourceModel` 不生效(尤其 `Query.update()`/`Query.delete()`) → 改用分别 listener per model(退路方案,记入 Task 4)。

- [ ] **Step 3: 删除 spike 文件**

```bash
rm src/backend/tests/community/plugins/test_multi_model_guard_spike.py
```

spike 是 throwaway(对照 Session 0 Task 1 "scratchpad, not committed"),不留库。

- [ ] **Step 4: 在本 plan 记录 spike 结论**

在 Task 4 的注释里写明:"Spike 验证:单 listener 链式 `with_loader_criteria` 对 BotModel+ResourceModel 各自生效(SQLAlchemy 2.0.51,select/update/delete),多 listener 非必需。"

---

## Task 3: cross-tenant 红测试(`ac_resource`)— RED

**Files:**
- Create: `src/backend/tests/community/plugins/test_resource_tenant_isolation.py`

对照 Session 0 `test_bot_tenant_isolation.py` 形态。Task 3 时:`avernet_tenant` 列还没加(Task 4 加)、guard 还没装(Task 5 装)——测试 **必须红**(ResourceModel 没这列,import/构造会错;或加了列但 guard 没装则跨租户可见)。

- [ ] **Step 1: 写红测试**

```python
"""Cross-tenant isolation for resource records (spec §6.4 red→green).

RED at this task: ac_resource has no avernet_tenant column yet (or column
present but guards absent), so a read under tenant-b sees tenant-a's resource.
Task 4 adds the column; Task 5 installs the guards; both turn this green.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import ResourceModel
from agentclaw.community.plugins.resource_repository import ResourceRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'resources.db'}",
        connect_args={"check_same_thread": False},
    )
    ResourceModel.__table__.create(engine)
    return ResourceRepository(_FileSqliteDB(engine))


def _data(**ov):
    base = dict(
        name="res-a",
        resource_type="file",
        status="active",
        user_id="emp1",
        created_by="emp1",
        source="upload",
        bolt_id="bot-a",
    )
    base.update(ov)
    return base


@pytest.fixture
def two_tenant_resources(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.create(_data(name="res-a", bolt_id="bot-a"))
    with avernet_tenant_scope("tenant-b"):
        repo.create(_data(name="res-b", bolt_id="bot-b"))
    return repo


def test_get_by_id_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    # seed returns the created dict; pull its id. Implementation detail:
    # repo.create returns row.to_dict() which has 'id'.
    with avernet_tenant_scope("tenant-a"):
        a = repo.list_resources(bolt_id="bot-a")
        a_id = a[0]["id"]
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_by_id(a_id) is None


def test_list_resources_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-b"):
        items = repo.list_resources(bolt_id="bot-a")
        assert items == []


def test_update_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        a_id = repo.list_resources(bolt_id="bot-a")[0]["id"]
    with avernet_tenant_scope("tenant-b"):
        # cross-tenant update returns None (row not found under tenant-b)
        assert repo.update(a_id, {"name": "hacked"}) is None


def test_delete_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        a_id = repo.list_resources(bolt_id="bot-a")[0]["id"]
    with avernet_tenant_scope("tenant-b"):
        # cross-tenant delete: 0 rowcount → False
        assert repo.delete(a_id) is False
    # tenant-a's row still present
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id(a_id) is not None


def test_own_tenant_still_visible(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        assert repo.list_resources(bolt_id="bot-a") != []
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd src/backend && uv run pytest tests/community/plugins/test_resource_tenant_isolation.py -v`

Expected: FAIL —— 构造 `ResourceModel(... avernet_tenant=...)` 会因列不存在报 `TypeError`(Task 4 加列前)或 cross-tenant 可见(Task 5 guard 前)。**记录红的具体形式**到 commit message。

- [ ] **Step 3: Commit 红测试**

```bash
cd src/backend
git add tests/community/plugins/test_resource_tenant_isolation.py
git commit -m "test(backend): RED — resource cross-tenant isolation (spec §6.4)

Fails before ac_resource gains avernet_tenant + guards (Tasks 4-5).
Mirrors Session 0 test_bot_tenant_isolation.py form."
```

---

## Task 4: 给 `ResourceModel` 加 `avernet_tenant` 列

**Files:**
- Modify: `src/backend/src/agentclaw/community/plugin_api/models.py:189-230`(`ResourceModel`)
- Test: `src/backend/tests/community/plugin_api/test_models.py`(Session 0 已有,扩断言)

对照 `BotModel` 的列定义(`plugin_api/models.py:67`),同款 `server_default="teamclaw"`,不进 `to_dict()`。**不含 `ac_bot_publish`**(见 plan 开头"范围说明")。

- [ ] **Step 1: `ResourceModel` 加列**

在 `plugin_api/models.py:212`(`env = Column(...)` 之后)加:

```python
    # Data-isolation tenant (see utils/avernet_tenant + the guards in this
    # module). server_default (not Python default=) so create_all emits the
    # same DEFAULT 'teamclaw' prod DDL applies, backfilling existing rows and
    # covering any non-ORM insert. Deliberately absent from to_dict().
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
```

**不修改 `ResourceModel.to_dict()`**(`:214-230`)——列不进 dict。

- [ ] **Step 2: 扩 Session 0 的 model 测试断言 to_dict 键集不变**

Modify `src/backend/tests/community/plugin_api/test_models.py`(Session 0 已有 `to_dict()` key-set pin,对照 `:50-54` Task 3 形态):为 `ResourceModel` 加一条"to_dict() 不含 avernet_tenant、且 seeded 行 avernet_tenant==teamclaw"断言。

```python
def test_resource_to_dict_excludes_tenant(tmp_path):
    # ... forge a ResourceModel row, assert 'avernet_tenant' not in row.to_dict()
    # ... and row.avernet_tenant == 'teamclaw' via server_default
```

(照 Session 0 `test_models.py` 的 BotModel 断言形态写,具体 fixture 从 Session 0 文件复制。)

- [ ] **Step 3: 跑 model 测试确认绿**

Run: `cd src/backend && uv run pytest tests/community/plugin_api/test_models.py -v`
Expected: PASS(新增断言绿;Session 0 已有的 32 repo tests 仍绿)。

- [ ] **Step 4: 跑 Task 3 的红测试,确认仍红(现在红在"guard 没装")**

Run: `cd src/backend && uv run pytest tests/community/plugins/test_resource_tenant_isolation.py -v`
Expected: 仍 FAIL —— 现在列有了,但 guard 没装,跨租户可见。红的形式从"列不存在"变成"隔离未生效"。

- [ ] **Step 5: Commit 加列**

```bash
cd src/backend
git add src/agentclaw/community/plugin_api/models.py \
        tests/community/plugin_api/test_models.py
git commit -m "feat(backend): add avernet_tenant column to ac_resource

server_default='teamclaw' backfills existing rows; invisible in to_dict()
(legacy API responses unchanged). Guards land in Task 5. Mirrors BotModel (PR #456)."
```

---

## Task 5: 扩展 guard 工厂(遍历 `_GUARDED_MODELS`)+ 装 insert guard 转绿

**Files:**
- Modify: `src/backend/src/agentclaw/community/plugin_api/models.py:101-186`(guard block 重构进工厂)
- Create: `src/backend/tests/community/plugins/test_resource_tenant_guard.py`(对照 Session 0 `test_bot_tenant_guard.py`)

**关键设计:** read guard 单 listener 遍历 `_GUARDED_MODELS`(Session 0 Task 1 已验等价行为 + SQLAlchemy 标准链式 `.options`;Task 2 spike 跳过,据此)。insert guard 按 model 各注册(`BotModel` + `ResourceModel` 都在 `plugin_api/models.py`,同一 `_install_avernet_tenant_guards` 内注册)。**不含 `ac_bot_publish`**(见 plan 开头"范围说明")。

- [ ] **Step 1: 在 `plugin_api/models.py` 重构 guard block 为工厂**

把 `models.py:101-186` 的 `BotModel`-only guard 重构成(关键:直接表达式非 lambda,见 spec §6.6):

```python
# ── Avernet tenant guards (multi-model) ────────────────────────────
# Extended from Session 0 (PR #456, single BotModel) to a tuple of guarded
# models. Read guard is ONE Session listener chaining with_loader_criteria
# per model (Session 0 Task 1 verified the single-model form for SELECT/UPDATE/
# DELETE in SQLAlchemy 2.0.51; chaining multiple .options is standard SQLAlchemy).
# Insert guard is per-mapper (before_insert has no "multi-model" form),
# registered once per guarded class.
#
# CRITICAL (spec §6.6): with_loader_criteria takes a DIRECT EXPRESSION, never
# a lambda — the lambda form is cached and pins the first tenant (leak).

class CrossTenantInsertError(RuntimeError):
    """An insert named a tenant other than the request's current one."""


# Models guarded by the read listener. Both live in this file (plugin_api/models.py):
# BotModel above, ResourceModel below. (ac_bot_publish is out of scope — see plan
# "范围说明"; it's not read by the three openapi_v1 categories in this round.)
_GUARDED_MODELS: tuple[type, ...] = (BotModel, ResourceModel)


def _avernet_tenant_read_guard(orm_execute_state) -> None:
    if orm_execute_state.is_column_load or orm_execute_state.is_relationship_load:
        return
    if not (
        orm_execute_state.is_select
        or orm_execute_state.is_update
        or orm_execute_state.is_delete
    ):
        return
    if orm_execute_state.execution_options.get("skip_avernet_tenant_guard"):
        return
    stmt = orm_execute_state.statement
    for m in _GUARDED_MODELS:
        stmt = stmt.options(
            with_loader_criteria(
                m,
                m.avernet_tenant == get_current_avernet_tenant(),
                include_aliases=True,
            )
        )
    orm_execute_state.statement = stmt


def _make_insert_guard(model_cls):
    def _guard(_mapper, _connection, target) -> None:
        current = get_current_avernet_tenant()
        if target.avernet_tenant is None:
            target.avernet_tenant = current
        elif target.avernet_tenant != current:
            raise CrossTenantInsertError(
                f"{model_cls.__name__} insert names tenant "
                f"{target.avernet_tenant!r} but the request tenant is {current!r}"
            )
    return _guard


_AVERNET_TENANT_GUARDS_INSTALLED = False


def _install_avernet_tenant_guards() -> None:
    global _AVERNET_TENANT_GUARDS_INSTALLED
    if _AVERNET_TENANT_GUARDS_INSTALLED:
        return
    event.listen(Session, "do_orm_execute", _avernet_tenant_read_guard)
    for m in _GUARDED_MODELS:
        event.listen(m, "before_insert", _make_insert_guard(m))
    _AVERNET_TENANT_GUARDS_INSTALLED = True


_install_avernet_tenant_guards()
```

> `BotModel`/`ResourceModel` 都在本文件。**顺序问题**:`_GUARDED_MODELS = (BotModel, ResourceModel)` 引用 `ResourceModel`,但原 guard block 在 `:101`(`ResourceModel` 定义在 `:189` 之前)。需把 guard block **移到 `ResourceModel` 定义之后**(`ResourceModel.to_dict()` 之后,约 `:230` 之后),或用 lazy lookup。**选移到 `ResourceModel` 之后**(符合 Session 0 评注"guards 焊在 model 旁")。`CrossTenantInsertError` 类 + 所有 `_GUARDED_MODELS` 引用都一起搬。

- [ ] **Step 2: 写 `test_resource_tenant_guard.py`(对照 Session 0 `test_bot_tenant_guard.py`)**

```python
"""ResourceModel tenant guards (spec §6.4 green)."""
from contextlib import contextmanager
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import ResourceModel, CrossTenantInsertError
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _DB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db; db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'rg.db'}",
                          connect_args={"check_same_thread": False})
    ResourceModel.__table__.create(engine)
    return _DB(engine)


def test_insert_under_scope_stamps_tenant(db):
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            r = ResourceModel(name="r", resource_type="file")
            s.add(r)
    # No explicit tenant set; guard stamped tenant-a. Read the raw row across
    # tenants via the escape hatch to assert the value (matches the bot guard's
    # test_insert_stamps_current_tenant in test_bot_tenant_guard.py — out of the
    # scope, get_current_avernet_tenant() returns 'teamclaw', so the read guard
    # would filter tenant-a's row without the skip option).
    with db.orm_session() as s:
        row = (
            s.query(ResourceModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .first()
        )
        assert row.avernet_tenant == "tenant-a"


def test_insert_outside_request_gets_default(db):
    with db.orm_session() as s:
        r = ResourceModel(name="r2", resource_type="file")
        s.add(r)
    with db.orm_session() as s:
        assert s.query(ResourceModel).filter_by(name="r2").first().avernet_tenant == "teamclaw"


def test_explicit_conflicting_tenant_insert_raises(db):
    # Match the bot guard pattern (test_bot_tenant_guard.py): wrap the orm
    # session in pytest.raises and call s.flush() inside, so the before_insert
    # event (autoflush=False → add() alone won't trigger it) fires within the
    # asserted block.
    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(CrossTenantInsertError):
            with db.orm_session() as s:
                s.add(ResourceModel(name="r3", resource_type="file",
                                    avernet_tenant="tenant-b"))
                s.flush()


def test_bare_query_filtered(db):
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            s.add(ResourceModel(name="r", resource_type="file"))
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            assert s.query(ResourceModel).all() == []


def test_skip_option_sees_all(db):
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            s.add(ResourceModel(name="r", resource_type="file"))
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            rows = s.query(ResourceModel).execution_options(
                skip_avernet_tenant_guard=True).all()
            assert len(rows) == 1
```

- [ ] **Step 3: 跑 Task 3 红测试 + Step 2 guard 测试,全部转绿**

Run: `cd src/backend && uv run pytest tests/community/plugins/test_resource_tenant_isolation.py tests/community/plugins/test_resource_tenant_guard.py -v`

Expected: 全绿(Task 3 红测试现在绿;guard 测试绿)。

- [ ] **Step 4: 跑现有全量测试套件,确认无回归(spec §6.1 acceptance)**

Run: `cd src/backend && uv run pytest tests/community -q`

Expected: 全绿,无现有测试逻辑改动。Session 0 的 acceptance 是 `8998 passed, 3 skipped`,本 Task 后数字应**只增不减、无 fail**(新增的测试增加了数量)。

- [ ] **Step 5: Commit guard 工厂 + 测试**

```bash
cd src/backend
git add src/agentclaw/community/plugin_api/models.py \
        tests/community/plugins/test_resource_tenant_isolation.py \
        tests/community/plugins/test_resource_tenant_guard.py
git commit -m "feat(backend): extend tenant guards to ac_resource

Multi-model guard factory: one Session read listener chains with_loader_criteria
per guarded model (BotModel + ResourceModel; spike-confirmed, SQLAlchemy 2.0.51);
before_insert per mapper. Direct expressions, not lambdas (caches → leak,
spec §6.6). Red→green tests per Session 0 form. Internal suite green, no
behavior change."
```

---

## Task 6: conformance 测试固化(routines + identity 间接隔离)

**Files:**
- Create: `src/backend/tests/community/plugins/test_routine_tenant_indirect_isolation.py`
- Create: `src/backend/tests/community/adapters/http/test_identity_tenant_indirect_isolation.py`

routines/identity 无表,靠 ac_bots guard 经 `forward_request:810`/`resolve_engine_for_bot` 间接隔离。Session 0 后即绿,本 Task 只是**固化契约**(对照 spec §6.4)。

- [ ] **Step 1: 写 routine 间接隔离测试**

mock/Stub `forward_request` 的前置 `get_bot`,证明跨租户在 bot 解析阶段就抛错(不达 engine):

```python
"""Routines cross-tenant isolation is enforced at bot resolve (spec §6.4).

No routine table — routines ride on ac_bots guard via forward_request:810
get_bot(bot_id, user_id). Cross-tenant: bot not found → forward never reached.
"""
# Stub CronRelayService.get_bot to return None for wrong-tenant bot_id,
# assert forward_request raises before reaching _transport.invoke.
# (具体 stub 形态据 cron_relay.py:810 + 845 + 854 写,实现时读 cron_relay
# 真实签名补 mock 断言。)
```

> **实现注:** 此测试需读 `cron_relay.py:780-875` 真实 mock 点。可在 Task 实施时花 10 分钟读源码补 mock fixture。**若该路径在 Session 0 已被某测试覆盖,直接引用并加 `avernet_tenant_scope` 断言,不重复造。** 先 grep:

```bash
cd src/backend
grep -rn "forward_request\|get_bot.*bot_id" tests/community/core/cron/ tests/community/plugins/ 2>/dev/null | head
```

若已有覆盖,本测试简化为"加 tenant scope 断言"。

- [ ] **Step 2: 写 identity 间接隔离测试**

证明跨租户调 `read_identity_file`/`write_identity_file` 在 `resolve_engine_for_bot` → `bot_repo` → `resolve_for_bot` 阶段抛 `DeviceNotBoundError`:

```python
"""Identity cross-tenant isolation enforced at bot resolve (spec §6.4).

No identity table — identity files are device FS markdown, isolated via
ac_bots guard at resolve_engine_for_bot (identity.py:266,284) →
resolver.resolve_for_bot (raises DeviceNotBoundError for wrong-tenant bot_id).
"""
# Seed bot under tenant-a; under tenant-b, call IdentityService.read_identity_file
# with the bot_id → assert raises DeviceNotBoundError (or bot_repo returns None
# → upstream raises). Verify device_fs.write_file was NEVER called.
```

- [ ] **Step 3: 跑两个间接隔离测试 + 全量回归**

Run: `cd src/backend && uv run pytest tests/community -q`
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
cd src/backend
git add tests/community/plugins/test_routine_tenant_indirect_isolation.py \
        tests/community/adapters/http/test_identity_tenant_indirect_isolation.py
git commit -m "test(backend): pin routines + identity indirect tenant isolation

固化契约: routines/identity 无表,靠 ac_bots guard 经 forward_request:810
/ resolve_engine_for_bot:266 间接隔离。Session 0 后即绿,本测试固化防回归。"
```

---

## Task 7: README Context Boundary + architecture test

**Files:**
- Modify(若新增 import): `src/backend/src/agentclaw/community/core/resources/services/README.md`
- Modify(若新增 import): `src/backend/src/agentclaw/community/core/cron/services/README.md`
- Test: `tests/community/architecture/`(已有 arch guard,跑即可)

spec §6.6 第 2 条:Stage 1 两次因未声明的 `utils.avernet_tenant` 导入 CI 失败。

- [ ] **Step 1: grep 检查是否在 resources/cron/identity 的 service 层新增了 `avernet_tenant` import**

```bash
cd src/backend
grep -rn "from agentclaw.community.utils.avernet_tenant\|import avernet_tenant" \
       src/agentclaw/community/core/resources/ \
       src/agentclaw/community/core/cron/ \
       src/agentclaw/community/core/services/identity.py 2>/dev/null
```

- [ ] **Step 2: 若有新增 import → 改对应模块 README 的 `## Context Boundary`**

照 Session 0 评注的形式(`docs/arch/context-boundary-format.md`),在模块 README 加 `internal_dependencies` 声明。**若 Step 1 grep 空(本期不改 service 层,只改 models + 新增测试)→ 跳过此 Task,直接 Step 3 验证 arch test。**

- [ ] **Step 3: 跑 architecture test 确认绿**

Run: `cd src/backend && uv run pytest tests/community/architecture/ -v`
Expected: 全绿。若 FAIL:某个 import 没声明 → 回 Step 2 补 README。

- [ ] **Step 4: Commit(若有 README 改动)**

```bash
cd src/backend
git add src/agentclaw/community/core/resources/services/README.md \
        src/agentclaw/community/core/cron/services/README.md 2>/dev/null
git diff --cached --quiet || git commit -m "docs(backend): declare avernet_tenant context boundary

arch test requires internal_dependencies declared when a module imports
utils.avernet_tenant (Stage 1 twice-failed on this)."
```

---

## Task 8: Phase 0 验证 + 状态看板更新

**Files:**
- Modify: `src/backend/docs/openapi-v1/README.zh-CN.md`(Track A 状态看板:阶段 2 resources → ✅;Changelog 追加)

- [ ] **Step 1: 跑 pre-push 全 gate 确认**

Run(本地全 gate,按 AGENTS.md 契约):

```bash
cd /Users/rongzhi/PycharmProjects/Avernet
OCB_PRE_PUSH_RUN_CI=1 git push --dry-run 2>&1 | tail -30
```

或直接跑 backend gate:

```bash
cd src/backend
uv run ruff check . && \
uv run mypy src/agentclaw/community/plugin_api/models.py 2>&1 | tail && \
uv run pytest tests/community -q 2>&1 | tail -5
```

Expected: ruff clean;mypy 与 Session 0 baseline 一致(只有 pre-existing 的 subclass/Any artifact);pytest 全绿。

- [ ] **Step 2: 更新交接文档状态看板**

Modify `src/backend/docs/openapi-v1/README.zh-CN.md` Track A 看板:

- 阶段 2 resources:`⬜ TODO` → `✅ DONE — Phase 0 PR #___`

(routines 阶段 6 本期**不做 Track A 加列**——它无表靠 ac_bots 间接隔离,看板状态保持 `⬜ TODO` + 在 Changelog 注明"无表,间接隔离已由 Session 0 覆盖,固化测试见 spec §6.4",不标 DONE。真正 DONE 留给 Track B routines handler 接通时。`ac_bot_publish` 不属本 plan,看板不动。)

Changelog 追加一条带日期记录(按交接文档 §Changelog 规则)。

- [ ] **Step 3: Commit 状态看板 + 推送**

```bash
cd src/backend
git add docs/openapi-v1/README.zh-CN.md docs/openapi-v1/README.md
git commit -m "docs(openapi-v1): Phase 0 done — ac_resource tenant-guarded"
```

- [ ] **Step 4: 推送(按交接文档 §"本地坑"用 --no-verify,依赖远端 CI 跑 singlebox)**

```bash
git push --no-verify
```

> **spec §6.6 第 5 条**:本地 pre-push 钩子跑不了 singlebox(sandbox 无产品栈),`--no-verify` 推送依赖远端 CI 验 singlebox coverage。`--no-verify` 对 force-push 也适用。

---

## Self-Review(spec 覆盖核对)

| spec 节 | Task 覆盖 | ✓ |
|---|---|---|
| §3.1.1 ResourceModel 加列 | Task 4 Step 1 | ✓ |
| §3.1.2 扩展 guard | Task 5 | ✓ |
| §3.1.3 resources handler(9 端点) | **Phase 1-3,不在本 plan** | (Phase 0 不接 handler,spec §8) |
| §2.3.2 / §3.1.2 ac_bot_publish 漏点 | **本期不做**(见 plan 开头"范围说明":openapi_v1 handler 不读这张表) | — |
| §3.2 routines 不动表 | Task 6(固化契约) | ✓ |
| §3.3 identity 不动表 | Task 6(固化契约) | ✓ |
| §4 guard 工厂 | Task 5 Step 1 | ✓ |
| §6.1 不改线上查询结果(free residual) | Task 5 Step 4(全量绿不修改)| ✓ |
| §6.3 DDL 先于代码 | Task 1 + Task 8 Step 1 部署 gate | ✓ |
| §6.4 conformance 红→绿 | Task 3(红)+ Task 5 Step 3(绿)+ Task 6(间接) | ✓ |
| §6.5 不动 legacy 线上 | Task 5 Step 4(legacy 套件绿) | ✓ |
| §6.6 踩坑(lambda/README/cwd) | Task 5 Step 1 注释 + Task 7 + Task 命令 `cd src/backend` | ✓ |
| §7 方向 A seam | **不在本 plan**(`require_principal`/`resolve_avernet_tenant` 维持 stub,spec §7)| (Phase 0 不动 seam) |

**Type consistency:** `CrossTenantInsertError` 已由 Session 0 定义在 `plugin_api/models.py:120`,Task 5 guard 工厂复用它 ✓;`_GUARDED_MODELS=(BotModel, ResourceModel)` 两 model 同在 `plugin_api/models.py`,无跨文件循环依赖问题(去 ac_bot_publish 后不再有此问题);`skip_avernet_tenant_guard` execution option 与 Session 0 一致 ✓。

**无 placeholder:** 所有 code step 都有实际代码;Task 6 的 routines/identity 测试标了"实现时读源码补 mock",因为 mock 点依赖 `cron_relay.py` 真实签名,且给了"若已有覆盖则简化"的判断步骤——这是合理的实现期判断,不是空洞 TODO。

---

## 执行交接

Plan saved to `docs/superpowers/plans/2026-07-27-phase0-tenant-guard-extension.md`. 两个执行选项:

**1. Subagent-Driven(推荐)** — 我每个 Task 派一个 fresh subagent,task 间 review,迭代快
**2. Inline Execution** — 在本 session 用 executing-plans 批量执行,带 checkpoint review

哪个?
