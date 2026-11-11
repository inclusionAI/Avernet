# Context 协作协议 P1 实现计划(bbs-context-collaboration-protocol)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为三个北向 API(创建协作上下文 / 追加协作事件 / 查询最新状态)落地 P1 最小持久化闭环:两张表(`ac_context_collab_collet`、`ac_context_collab_event`)、一个仓储协议、完整 L1-L6/G1-G4 领域对象模型、最小授权/并发/事件 DAG 校验与查询投影。

**Architecture:** 核心代码全部落在 `apps/backend/src/agentclaw/community/core/task/context_collab/`,按 ACCS 层次分目录;`service_api.py` 定义 transport-agnostic 协议,`services/context_collab_service.py` 编排 G1-G4 校验与仓储;`adapters/http/task/context_collab.py` 与 `adapters/http/openapi_v1/task/context_collab.py` 两个 Delivery 只做解析/认证/序列化。权威事实只有两张表;Task/Message 等领域对象序列化在 JSON 字段中,不建独立表。

**Tech Stack:** Python 3.12(包 `agentclaw.community`)、SQLAlchemy(共享 `Base`,租户 guard)、injector DI、FastAPI + `Injected(Protocol)`、Pydantic DTO、pytest(`DEPLOY_PROFILE=test`)。

**Spec:** `apps/backend/specs/2026-11-09-bbs-context-collaboration-protocol/spec.md`(本计划与 spec 配套阅读;执行者须两者都读)

## Global Constraints(每个任务的要求都隐含包含本节)

- **状态词汇(已由 spec 作者裁决):** `current_snapshot.status` 以 §6 API 示例为准:
  `created | in_progress | reviewing | blocked | done | cancelled`。
  §4.1 状态机迁移规则保留,状态名归一:`ready→created`、`active→in_progress`(其余不变)。
- **路径逐字保留:** `POST /api/v1/collaboration/task/context_collab`、
  `POST /api/v1/collaboration/task/context_collab/{collab_context_id}/events`、
  `GET /api/v1/collaboration/task/context_collab/{collab_context_id}?trunk_events=true`;
  OpenAPI 面同语义换成 `/openapi/v1/...` 前缀。兼容字段名 `collab_context_id`、`parent_event`、`trunk_events` 在 API 与两张表中逐字保留。
- **method_kind 合法值:** `"Single"` 与 `"Open + Group"`(字符串,含空格);`Single` 时 `executor_ref` 必填。
- **product_kind 合法值:** `team_todo`、`public_discussion`(可缺省);**workflow_kind 合法值:** `team_todo_execution`、`forum_public_discussion`(可缺省)。未知值拒绝。
- **P1 注册 event_kind 集合(未注册即拒绝):** `task.created`、`task.assigned`、`task.started`、`task.progress.updated`、`task.submitted`、`task.accepted`、`task.rejected`、`task.cancelled`、`task.completed`、`user_interaction.posted`。
- **primitive_type 合法值:** `Message | Task | Activity`;**visibility 合法值:** `public | workspace | private`。
- **Message 规则:** `event_kind=user_interaction.posted` 且 `primitive_type=Message` 的 payload 必须直接含非空 `content`,出现 `content_ref` 即拒绝。
- **事件 DAG:** 主事件 `parent_event=null`;`is_trunk` 在接纳时计算,规则:初始事件 trunk=True;后续事件 trunk=False 当且仅当 `parent_event` 不等于接纳前的 `current_event_id`。`sequence_no` 为上下文内自增序号,顺序即读取顺序。
- **幂等:** 创建重复 `create_request_id` 返回原上下文(HTTP 200,非错误);追加重复 `(collab_context_id, request_id)` 返回原事件结果(HTTP 200,`accepted: true`)。`expected_version` 与行内 `context_version` 不匹配报 `ContextCollabVersionConflict`(HTTP 409)。
- **ID 生成:** `collab_context_id = "collet_" + uuid4().hex`;`event_id = "evt_" + uuid4().hex`。
- **G1 P1 边界:** Authentication 在 delivery(`get_current_user` / `require_principal`)完成,核心只接收 `ActorContext`;授权做结构校验(Single 必须有 executor/principal;visibility 读取过滤规则见 Task 11)+ `ContextCollabAuthorizerProtocol` 接缝,外部 Membership/Grant/Policy Authority 真实校验留给 P2(spec §13)。
- **禁止:** 不修改 `plugin_api/`、`task/forum/`、`task/bbs/` 下任何文件;不为 Task/Message/Participant/Method/Profile/StateMachine 建表;`context_collab/` 下不建 `source/`;核心代码不 import FastAPI/HTTP;本地持久化记录类不得命名 `ColletEvent`(用 `EventRecord`)。
- **仓库约定:** 所有新 ORM 模型必须在 `core/schema.py::import_all_models()` 中强制 import,带 `avernet_tenant` 列的模型必须 `register_avernet_tenant_guard()`;DDL 文件放 `core/task/context_collab/sql/`;每个文件 < 1000 行;持久化写失败必须向上抛错,禁止吞错返回成功。
- **测试命令(在 `apps/backend/` 下):** `DEPLOY_PROFILE=test uv run pytest <路径> -v`。
- **不改无关代码:** 不 reformat 未触及行;提交信息格式 `feat(backend): ...` / `test(backend): ...`,尾部带 `Co-Authored-By: Claude <noreply@anthropic.com>`。

## Review Focus(最可能咬人的五类输入;每条已钉在所属任务的测试里)

1. **Message 事件缺 `content` 或改用 `content_ref`** —— 必须拒绝(HTTP 422),不得静默落库(Task 4 + Task 10)。
2. **重复 `request_id`** —— 必须幂等返回原结果,不得 500、不得产生第二行事件(Task 8 + Task 9 + Task 12)。
3. **过期 `expected_version`** —— 必须 409 冲突,不得覆盖别人的快照(Task 9 + Task 13)。
4. **`parent_event` 指向不存在或其它上下文的事件** —— 必须拒绝,不得写出悬空外键(Task 9)。
5. **`trunk_events=true`** —— 只过滤 `task_progress.events[]`,不得改变 `current_snapshot`、`current_event` 与统计(验收 §12.2 #4;Task 11 + Task 12)。

---

### Task 1: 持久化模型与 DDL(两张表)

**Files:**
- Create: `apps/backend/src/agentclaw/community/core/task/context_collab/__init__.py`(空)
- Create: `apps/backend/src/agentclaw/community/core/task/context_collab/persistence/__init__.py`(空)
- Create: `apps/backend/src/agentclaw/community/core/task/context_collab/persistence/models.py`
- Create: `apps/backend/src/agentclaw/community/core/task/context_collab/sql/2026_11_11_context_collab.sql`
- Modify: `apps/backend/src/agentclaw/community/core/schema.py`(`import_all_models()` 末尾追加 import)
- Create: `apps/backend/tests/community/repository/task/context_collab/conftest.py`(复制 `tests/community/repository/task/conftest.py` 的 InMemorySqliteDB 模式,side-effect import 路径改为 `core.task.context_collab.persistence.models`)
- Test: `apps/backend/tests/community/repository/task/context_collab/test_models.py`

**Interfaces:**
- Produces(Task 8 消费): `ContextCollabColletModel`(`__tablename__ = "ac_context_collab_collet"`)与 `ContextCollabEventModel`(`__tablename__ = "ac_context_collab_event"`),列名与 §9.5 DDL 逐列一致;`ContextCollabColletModel.context_spec / current_snapshot` 为 `Text`(SQLite)/mediumtext(MySQL),存 JSON。
- 模型列使用的公共轴列helper:参照 `core/task/forum/repository/models.py` 的四轴 `env`(String20, default=get_current_env)、`avernet_tenant`(String64, server_default="teamclaw")、`gmt_create/gmt_modified`(server_default=func.now(),onupdate);主键用 `AutoIncrementBigInteger`(即 `BigInteger.with_variant(Integer, "sqlite")`,定义在 forum models 内,自行复制该 helper)。

- [ ] **Step 1: 写失败测试**

`test_models.py`(列名比照 §9.5 DDL + tenant guard 注册三件事;写法比照上游 `tests/community/repository/task/test_models_schema.py` 与 forum 的模型 schema 测试):
- 断言两张表都存在(engine 的 `Base.metadata.create_all` 已建);
- 断言 collet 表含列:`id, avernet_tenant, env, collab_context_id, create_request_id, context_spec, current_snapshot, current_event_id, context_version, gmt_create, gmt_modified`;唯一约束名 `uk_context_collab_collet_context` / `uk_context_collab_collet_request`;
- 断言 event 表含列:`id, avernet_tenant, env, collab_context_id, event_id, request_id, sequence_no, parent_event, is_trunk, event_kind, primitive_type, actor_ref, visibility, payload, context_version_after, occurred_at, gmt_create`;唯一约束名 `uk_context_collab_event` / `uk_context_collab_event_request` / `uk_context_collab_event_seq`;
- 断言 `ContextCollabColletModel` 与 `ContextCollabEventModel` 都在 `_GUARDED_MODELS` 中(tenant guard 已注册)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/repository/task/context_collab/ -v`
Expected: FAIL(ImportError: module not found)

- [ ] **Step 3: 写 SQL 文件**

`sql/2026_11_11_context_collab.sql`:把 spec §9.5 的两张表 DDL **逐字**复制进去,文件头加两行注释(参照 `2026_09_20_forum_topics_posts.sql` 的头部格式:说明 OceanBase MySQL-mode 为生产方言、SQLite 测试由 ORM create_all、`GLOBAL` 索引语义)。不改任何列名/索引名。

- [ ] **Step 4: 实现 ORM 模型**

`persistence/models.py`:两个类 + `_base_axes()` helper(env / avernet_tenant / gmt_create / gmt_modified),JSON 列用 `sa.Text`(SQLite)带 `with_variant(sa.dialects.mysql.MEDIUMTEXT, "mysql")`(参照 forum models 的 `_binary_string` 写法);每个类后立即调用 `register_avernet_tenant_guard(Model)`。在 `core/schema.py::import_all_models()` 末尾加:

```python
import agentclaw.community.core.task.context_collab.persistence.models  # noqa: F401  ac_context_collab_*
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/repository/task/context_collab/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/src/agentclaw/community/core/schema.py \
        apps/backend/tests/community/repository/task/context_collab/
git commit -m "feat(backend): add context collab tables with ORM models and DDL"
```

---

### Task 2: L2/L3/L5/L6 引用值对象与枚举

**Files:**
- Create: `.../core/task/context_collab/l2_agent_network/{__init__.py,network_refs.py}`
- Create: `.../core/task/context_collab/l3_spaces_resources/{__init__.py,space_resource_refs.py}`
- Create: `.../core/task/context_collab/l5_methods/{__init__.py,method_binding.py}`
- Create: `.../core/task/context_collab/l6_products/{__init__.py,profiles.py}`
- Test: `apps/backend/tests/community/core/task/context_collab/test_network_profiles.py`

**Interfaces:**
- Produces(后续所有任务消费,import 路径逐字):

```python
# l2_agent_network/network_refs.py
@dataclass(frozen=True)
class PrincipalRef:
    principal_type: str   # "human" | "bot" | "org" | "system";非空
    principal_id: str     # 非空
    @classmethod
    def from_dict(cls, data: dict) -> "PrincipalRef": ...   # 缺字段/空串抛 ContextCollabValidationError
    def to_dict(self) -> dict: ...

@dataclass(frozen=True)
class AgentRef:
    agent_type: str       # "bot" | "service";非空
    agent_id: str         # 非空
    @classmethod
    def from_dict(cls, data: dict) -> "AgentRef": ...
    def to_dict(self) -> dict: ...

# l3_spaces_resources/space_resource_refs.py
@dataclass(frozen=True)
class SpaceRef:
    space_id: str
    environment: str
    @classmethod
    def from_dict(cls, data: dict) -> "SpaceRef": ...

@dataclass(frozen=True)
class ResourceRef:
    resource_type: str
    resource_id: str
    revision: str | None
    @classmethod
    def from_dict(cls, data: dict) -> "ResourceRef": ...

# l5_methods/method_binding.py
class MethodKind(str, Enum):
    SINGLE = "Single"
    OPEN_GROUP = "Open + Group"
    @staticmethod
    def from_wire(value: str) -> "MethodKind": ...  # 未知值抛 ContextCollabValidationError

# l6_products/profiles.py
class ProductProfile(str, Enum):
    TEAM_TODO = "team_todo"
    PUBLIC_DISCUSSION = "public_discussion"

class WorkflowKind(str, Enum):
    TEAM_TODO_EXECUTION = "team_todo_execution"
    FORUM_PUBLIC_DISCUSSION = "forum_public_discussion"
```
- **错误类型(本任务一并创建,后续共享):** `.../core/task/context_collab/errors.py`:
  `ContextCollabValidationError`(继承 `agentclaw.community.core.errors.ValidationError`)、
  `ContextCollabNotFoundError`(继承 NotFound)、
  `ContextCollabVersionConflict`(继承 Conflict)、
  `ContextCollabParentEventError`(继承 ValidationError)。
  命名与文件位置后续任务不再重复声明。

- [ ] **Step 1: 写失败测试**

`test_network_profiles.py`(纯构造单测,关键值):

```python
import pytest
from agentclaw.community.core.task.context_collab.errors import ContextCollabValidationError
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import MethodKind

@pytest.mark.parametrize("bad", ["", "Swarm", "open+group", "single"])
def test_method_kind_rejects_unknown_wire_values(bad):
    with pytest.raises(ContextCollabValidationError):
        MethodKind.from_wire(bad)

def test_method_kind_accepts_spec_values():
    assert MethodKind.from_wire("Single") is MethodKind.SINGLE
    assert MethodKind.from_wire("Open + Group") is MethodKind.OPEN_GROUP
```
同文件补:PrincipalRef/AgentRef/SpaceRef 的 `from_dict` round-trip 与缺字段拒绝;`ProductProfile`/`WorkflowKind` 覆盖四个合法值。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_network_profiles.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现四处包与 errors.py**

四个模块按 Interfaces 块的签名实现:frozen dataclass + `from_dict`/`to_dict`(JSON 可序列化值)。不做超范围配置。

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/
git commit -m "feat(backend): add context collab L2/L3/L5/L6 reference value objects"
```

---

### Task 3: L4 领域对象(Collet / EventRecord / 状态词汇)

**Files:**
- Create: `.../core/task/context_collab/l4_context_primitives/__init__.py`
- Create: `.../core/task/context_collab/l4_context_primitives/collet.py`
- Create: `.../core/task/context_collab/l4_context_primitives/event_envelope.py`
- Create: `.../core/task/context_collab/l4_context_primitives/task.py`
- Create: `.../core/task/context_collab/l4_context_primitives/{participants.py,activity.py,decision.py,message.py,artifact.py,query.py}`
- Test: `apps/backend/tests/community/core/task/context_collab/test_l4_primitives.py`

**Interfaces:**
- Consumes: Task 2 的全部引用对象。
- Produces:

```python
# task.py — G4 有限状态值(spec 全局约束状态词汇)
class TaskStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"

# collet.py
@dataclass
class ContextSpec:
    space: SpaceRef
    principal_ref: PrincipalRef
    method_kind: str                      # MethodKind 合法字符串
    executor_ref: AgentRef | None
    objective: str
    resource_refs: tuple[ResourceRef, ...]
    product_kind: str | None
    workflow_kind: str | None
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ContextSpec": ...

@dataclass
class ProgressSnapshot:
    percent: int = 0
    completed_count: int = 0
    active_count: int = 0
    blocked_count: int = 0
    current_event_id: str | None = None
    def to_dict(self) -> dict: ...

@dataclass
class CurrentSnapshot:
    objective: str
    status: str                           # TaskStatus 合法字符串
    principal_ref: PrincipalRef
    executor_ref: AgentRef | None
    method_kind: str
    progress: ProgressSnapshot
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "CurrentSnapshot": ...

@dataclass
class Collet:
    collab_context_id: str
    create_request_id: str
    context_spec: ContextSpec
    current_snapshot: CurrentSnapshot
    current_event_id: str | None
    context_version: int

# event_envelope.py
@dataclass
class EventRecord:                        # 本地事件持久化记录;不得命名 ColletEvent
    event_id: str
    request_id: str
    sequence_no: int
    parent_event: str | None
    is_trunk: bool
    event_kind: str
    primitive_type: str                   # Message | Task | Activity
    actor_ref: PrincipalRef
    visibility: str                       # public | workspace | private
    payload: dict
    context_version_after: int
    occurred_at: datetime
```
- `event_envelope.py` 同时定义薄适配 `EventEnvelope`(event_id / event_type=event_kind / event_version=1 / aggregate_ref=collab_context_id / aggregate_revision=context_version_after /causation_id=parent_event / payload),`EventRecord.to_envelope()` 互转,字段名取自 ACCS 公共合同(spec §3.4.1)。
- `participants.py`(Participant/RoleBinding 值对象)、`message.py`(Message 语义常量)、`activity.py`、`decision.py`、`artifact.py`(ResourceBinding 值对象)、`query.py`(Query 参数对象 `Query(collab_context_id: str, trunk_events: bool)`)在本版均为薄值对象:仅 dataclass + 序列化,无独立生命周期;不要生成任何 Repository。

- [ ] **Step 1: 写失败测试**(核心一条 + 快照 round-trip)

```python
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import TaskStatus

def test_status_vocabulary_matches_api_examples():
    assert [s.value for s in TaskStatus] == [
        "created", "in_progress", "reviewing", "blocked", "done", "cancelled",
    ]
```
另断言:`ContextSpec.to_dict()/from_dict()` round-trip 保真(含 None executor_ref);`CurrentSnapshot.from_dict` 拒绝未知 status 值;`EventRecord.to_envelope()` 的 `causation_id == parent_event`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_l4_primitives.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 L4 各文件**(按 Interfaces 签名;六个薄值对象文件如果没超过 ~60 行可合并写但仍分文件创建)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/
git commit -m "feat(backend): add context collab L4 domain objects"
```

---

### Task 4: G4 Schema —— 事件注册表与 payload 校验

**Files:**
- Create: `.../core/task/context_collab/g4_contracts_consistency/__init__.py`
- Create: `.../core/task/context_collab/g4_contracts_consistency/schema.py`
- Test: `apps/backend/tests/community/core/task/context_collab/test_event_schema.py`

**Interfaces:**
- Consumes: `ContextCollabValidationError`。
- Produces(签名固定):

```python
REGISTERED_EVENT_KINDS: frozenset[str]   # 全局约束中那 10 个 event_kind
ALLOWED_PRIMITIVE_TYPES: frozenset[str]   # {"Message", "Task", "Activity"}
ALLOWED_VISIBILITY: frozenset[str]        # {"public", "workspace", "private"}

def validate_event_body(
    *, event_kind: str, primitive_type: str, visibility: str, payload: dict,
) -> None:
    """抛 ContextCollabValidationError。规则:
    1. event_kind 必须在 REGISTERED_EVENT_KINDS(P1 注册表,spec §11.4 未注册即拒);
    2. primitive_type / visibility 必须在白名单;
    3. event_kind=user_interaction.posted 时:
       - primitive_type 必须 Message(spec §6.3);payload.content 必须非空 str;
       - payload 含 content_ref 即拒绝(验收 §12.2 #3)。
    """
```

- [ ] **Step 1: 写失败测试**(钉死 Review Focus #1)

```python
import pytest
from agentclaw.community.core.task.context_collab.errors import ContextCollabValidationError
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.schema import (
    validate_event_body,
)

def test_user_message_requires_inline_content():
    validate_event_body(
        event_kind="user_interaction.posted", primitive_type="Message",
        visibility="workspace", payload={"message_kind": "user_interaction", "content": "上下文做一下补充xxx"},
    )

@pytest.mark.parametrize("payload", [
    {"content_ref": "post_123"},                       # 出现 content_ref 即拒
    {"message_kind": "user_interaction"},               # 缺 content
    {"content": "   "},                                # 空 content
])
def test_user_message_rejects_content_ref_or_missing_content(payload):
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="user_interaction.posted", primitive_type="Message",
                            visibility="workspace", payload=payload)

def test_unregistered_event_kind_rejected():
    with pytest.raises(ContextCollabValidationError):
        validate_event_body(event_kind="artifact.delivered", primitive_type="Activity",
                            visibility="public", payload={})
```
其余各 kind(如 task.progress.updated + Activity + workspace)合法通过;primitive_type/visibility 非法值拒绝。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_event_schema.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 schema.py**(按上述 docstring;纯函数,无 IO)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/test_event_schema.py
git commit -m "feat(backend): add context collab event schema registry"
```

---

### Task 5: G4 State Machine —— 声明式状态机 + 守卫 + 内置 machine

**Files:**
- Create: `.../core/task/context_collab/g4_contracts_consistency/state_machine.py`
- Test: `apps/backend/tests/community/core/task/context_collab/test_state_machine.py`

**Interfaces:**
- Consumes: `TaskStatus`、`ContextCollabValidationError`。
- Produces:

```python
@dataclass(frozen=True)
class StateDefinition:
    name: str                 # TaskStatus 值
    governance_kind: str      # created→waiting, in_progress→active, reviewing→review,
                              # blocked→blocked, done→terminal_success, cancelled→terminal_cancelled
    allowed_methods: tuple[str, ...]   # P1 全部 ("Single",)

@dataclass(frozen=True)
class TransitionRule:
    rule_id: str
    from_states: tuple[str, ...]
    event_name: str           # 必须在 REGISTERED_EVENT_KINDS
    guard_refs: tuple[str, ...]
    to_state: str

@dataclass(frozen=True)
class StateMachineDefinition:
    schema_version: str        # "context-state-machine/1"
    profile_ref: str          # workflow_kind 值
    profile_version: int      # 1
    compatibility_policy: str # "new_contexts_only"
    initial_state: str
    states: tuple[StateDefinition, ...]
    transitions: tuple[TransitionRule, ...]

@dataclass(frozen=True)
class GuardOutcome:
    verdict: str               # "PASS" | "FAIL" | "BLOCKED"
    reason: str



def validate_definition(d: StateMachineDefinition) -> None:
    """spec §4.4 第 1-7 条:initial 唯一存在;全状态可达;terminal_* 无出边;
    引用状态都已定义;rule_id 唯一;同 state+event 多 guard 不明确即拒;未知字段上限;
    非法为止抛 ContextCollabValidationError。"""

def resolve_transition(
    d: StateMachineDefinition, current_state: str, event_kind: str,
    guard_results: dict[str, GuardOutcome],
) -> str | None:
    """返回 to_state;无规则命中返回 None(事件仍可接纳,状态不变);
    命中规则但 guard FAIL 抛 ContextCollabValidationError(拒绝事件,不静默降级);
    多条规则同时命中抛 ContextCollabValidationError。"""

TEAM_TODO_EXECUTION_MACHINE: StateMachineDefinition     # 唯一内置 machine
MACHINE_REGISTRY: dict[str, StateMachineDefinition]   # {"team_todo_execution": ...}

@dataclass(frozen=True)
class GuardEvaluationInput:                 # 守卫求值的纯输入
    actor: PrincipalRef
    responsible: PrincipalRef
    executor: AgentRef | None
    payload: dict

GuardFunction = Callable[[GuardEvaluationInput], GuardOutcome]
GUARD_REGISTRY: dict[str, GuardFunction]    # 5 个守卫,语义见下
```
- **内置 machine 内容(spec §4.1 迁移规则 + 全局约束的状态重命名):**
  states: 6 个(见 StateDefinition 注释)。
  transitions(6 条,`guard_refs` 逐字保留):
  - `start_task`: [created] → event_name=`task.started`, guard `[actor_can_start_task]` → in_progress
  - `update_progress`: [in_progress] → `task.progress.updated` + `[event_matches_task]` → in_progress
  - `submit_for_review`: [in_progress] → `task.submitted` + `[required_artifacts_present]` → reviewing
  - `accept_task`: [reviewing] → `task.accepted` + `[actor_is_responsible_principal]` → done
  - `reject_task`: [reviewing] → `task.rejected` + `[actor_is_responsible_principal]` → in_progress
  - `cancel_task`: [created, in_progress, reviewing, blocked] → `task.cancelled` + `[actor_can_cancel]` → cancelled
- **守卫语义(纯谓词,禁止静默失败,§4.3):** 每个守卫是 `GuardEvaluationInput -> GuardOutcome` 的纯函数:
  - `actor_can_start_task`: PASS if actor == responsible or actor == executor;else FAIL("actor is neither responsible principal nor executor")
  - `actor_is_responsible_principal`: PASS if actor == responsible;else FAIL
  - `actor_can_cancel`: PASS if actor == responsible;else FAIL
  - `required_artifacts_present`: PASS if isinstance(payload.get("artifacts"), list) and 非空;else FAIL("no artifacts in payload")
  - `event_matches_task`: PASS if "task_id" not in payload;else FAIL("explicit task_id not supported in P1")

- [ ] **Step 1: 写失败测试**(覆盖 §4.4 三类合同断言 + §4.3 PASS/FAIL 事实)

要点写为具体测试:
1. `validate_definition(TEAM_TODO_EXECUTION_MACHINE)` 不抛;构造一个 `initial_state` 未定义的副本抛 ValidationError;构造一个 rule_id 重复的副本抛;
2. `resolve_transition`:created + `task.started` + actor 为 responsible → `"in_progress"`;created + `user_interaction.posted` → `None`(无规则,状态不变);done + 任何事件 → None 或 FAIL(终态无出边);reviewing + `task.accepted` + actor 非 responsible → ValidationError(guard FAIL 显式拒绝);
3. `update_progress` 时 payload 含 `task_id` → FAIL;
4. moral: "同一 state/event 多 guard 不明确" - 构造两条 [created]+task.started 冲突规则抛错。
5. 全部状态 governance_kind 落在 §4.2 七个类别词汇内。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_state_machine.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 state_machine.py**(纯声明式,无 IO;`MACHINE_REGISTRY` 暂含 team_todo 一项 — P1 不发明 forum 状态)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/test_state_machine.py
git commit -m "feat(backend): add declarative G4 state machine with team_todo_execution profile"
```

---

### Task 6: G2/G3 —— 可靠性与投影规则

**Files:**
- Create: `.../core/task/context_collab/g2_keep_reliable_live/__init__.py`
- Create: `.../core/task/context_collab/g2_keep_reliable_live/reliability.py`
- Create: `.../core/task/context_collab/g3_observe_evaluate_evolve/__init__.py`
- Create: `.../core/task/context_collab/g3_observe_evaluate_evolve/observation.py`
- Test: `apps/backend/tests/community/core/task/context_collab/test_reliability_observation.py`

**Interfaces:**
- Consumes: Task 3 的 `EventRecord`、`ProgressSnapshot`;`ContextCollabVersionConflict`。
- Produces:

```python
# g2/reliability.py
def compute_is_trunk(parent_event: str | None, previous_current_event_id: str | None) -> bool:
    """(全局约束)parent_event is None → True;
    previous_current_event_id is None → True;  # 初始事件
    parent_event == previous_current_event_id → True;  # 接在链头继续主干
    否则 False。"""

def ensure_expected_version(expected: int, current: int) -> None:
    """不匹配抛 ContextCollabVersionConflict(带 current version)。"""

# g3/observation.py — GET 查询投影(非权威)
@dataclass(frozen=True)
class EventProjection:
    event_id: str
    parent_event: str | None
    event_kind: str
    primitive_type: str
    status: str                  # "completed" | "active" | "pending"
    summary: str | None          # payload.get("summary")
    content: str | None          # payload.get("content")(Message)
    occurred_at: datetime

def project_events(events: Sequence[EventRecord], *, current_event_id: str | None,
                   trunk_events: bool) -> list[EventProjection]:
    """trunk_events=True 仅筛 is_trunk=True(不改变事件 status 与顺序,§12.2 #4);
    event status:sequence 小于 current 为 "completed",等于为 "active",其后(或 current 为 None)为 "pending"。"""

def build_progress(events: Sequence[EventProjection], *,
                   last_percent: int | None) -> ProgressSnapshot:
    """completed_count/active_count/blocked_count 由投影 status 统计;
    percent = last_percent(最后一个 payload 带 "percent" 整数的事件)否则
    0 if 无事件 else round(completed_count*100/len(events))。current_event_id 取最后一个事件。"""
```

- [ ] **Step 1: 写失败测试**(钉死 Review Focus #5)

test_reliability_observation.py:
1. `compute_is_trunk(None, "evt_001") is True`;`compute_is_trunk("evt_001", "evt_001") is True`;`compute_is_trunk("evt_001", "evt_003") is False`;
2. `ensure_expected_version(1, 1)` 不抛;`ensure_expected_version(1, 2)` 抛 Conflict;
3. 构造 5 个事件(链 evt_001..evt_005,末尾 current="evt_004",其中 evt_002 `is_trunk=True`,另造一个 `is_trunk=False` 的分支事件 evt_002b):
   - `trunk_events=True` 时投影不含 evt_002b、其余照旧;
   - `trunk_events=True/False` 两种情况下其它每个事件的 `status` 完全一致(completed×3/active/pending);
4. `build_progress` 在 last_percent=None、5 事件 3 completed 时:`percent == 60`、`completed_count == 3`、`active_count == 1`、`blocked_count == 0`;payload 带 percent=40 (log) 时 percent 取 40。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_reliability_observation.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现两个模块**(纯函数)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/test_reliability_observation.py
git commit -m "feat(backend): add context collab reliability rules and query projections"
```

---

### Task 7: G1 授权接缝 + Service API 协议 + 创建用例

**Files:**
- Create: `.../core/task/context_collab/g1_secure_govern/{__init__.py,authorization.py}`
- Create: `.../core/task/context_collab/service_api.py`
- Create: `.../core/task/context_collab/services/__init__.py`
- Create: `.../core/task/context_collab/services/context_collab_service.py`(本任务只实现 `create_context`)
- Create: `apps/backend/src/agentclaw/community/api/task/context_collab_service.py`(re-export)
- Test: `apps/backend/tests/community/core/task/context_collab/test_context_collab_service.py`(本任务写 create 部分)

**Interfaces:**
- Consumes: Task 2/3/4/6 全部产物。
- Produces(签名固定,Task 8/10/11/12/13 消费):

```python
# service_api.py
@dataclass(frozen=True)
class ActorContext:
    actor: PrincipalRef         # 由 delivery 从 auth 信息构造
    source: str                 # "api" | "openapi"

@dataclass
class ContextCreateCommand:     # 字段与 spec §6.2 表格一一对应
    request_id: str
    space: SpaceRef
    principal_ref: PrincipalRef
    product_kind: str | None
    workflow_kind: str | None
    method_kind: str
    executor_ref: AgentRef | None
    objective: str
    resource_refs: list[ResourceRef]

@dataclass
class ContextCollabCreatedResult:
    collab_context_id: str
    context_version: int
    current_snapshot: dict      # CurrentSnapshot.to_dict()
    current_event: dict         # event 基础字段投影(见 g3)
    replayed: bool              # True = 幂等命中已存在的 create_request_id

@runtime_checkable
class ContextCollabServiceProtocol(Protocol):
    def create_context(self, command: ContextCreateCommand,
                       context: ActorContext) -> ContextCollabCreatedResult: ...
    def append_event(self, collab_context_id: str, command: EventAppendCommand,
                     context: ActorContext) -> EventAcceptedResult: ...
    def get_status(self, collab_context_id: str, *, trunk_events: bool,
                   context: ActorContext) -> ContextCollabStatusProjection: ...

# EventAppendCommand / EventAcceptedResult / ContextCollabStatusProjection 在
# service_api.py 中一并定义(签名见 Task 10/11 的 Interfaces 块)。
# 注:服务层返回值命名带 Projection 后缀,避免与 adapter Pydantic DTO(spec §6.5
# 要求的 ContextCollabStatusResponse 等 wire 名)重名。

# g1/authorization.py
@runtime_checkable
class ContextCollabAuthorizerProtocol(Protocol):
    def check_create(self, spec: ContextSpec, actor: ActorContext) -> None: ...
    def check_append(self, collet: Collet, command, actor: ActorContext) -> None: ...
    def filter_visible_events(self, collet: Collet, events: Sequence[EventRecord],
                              actor: ActorContext) -> list[EventRecord]: ...

class StructuralContextCollabAuthorizer(ContextCollabAuthorizerProtocol):
    """P1 默认实现(spec §13):仅结构校验 — check_create: method=Single 时
    executor_ref 不得为 None(principal/executor 引用非空已在 value object 校验);
    check_append: actor 必须是 principal 或 executor;filter_visible_events:
    public 永远可见,workspace 仅 principal/executor 可见,private 仅 principal。"""
```
- `services/context_collab_service.py`:`class ContextCollabService(ContextCollabServiceProtocol)` 构造器 `@inject def __init__(self, repository: ContextCollabRepositoryProtocol, authorizer: ContextCollabAuthorizerProtocol = StructuralContextCollabAuthorizer())`。仓库协议在本文件**只 import 不实现**(定义在 Task 8 的 `persistence/repository.py`,与本任务的 fake 兼容):

```python
# persistence/repository.py(Task 8 实现;接口本任务即可声明在 service 假设中)
@dataclass
class CreateContextRecordRequest:
    create_request_id: str
    context_spec: ContextSpec
    initial_event: InitialEventDraft    # event_kind="task.created", primitive_type="Task", payload={"objective": ...}
@dataclass
class ContextCreatedRecord:
    collet: Collet
    initial_event: EventRecord
    created: bool

@runtime_checkable
class ContextCollabRepositoryProtocol(Protocol):
    def create_context(self, request: CreateContextRecordRequest) -> ContextCreatedRecord: ...
    def append_event(self, collab_context_id: str, request: AppendEventRecordRequest) -> EventAppendedRecord: ...
    def get_current_status(self, collab_context_id: str, *, trunk_events: bool) -> ContextStatusRecord | None: ...
```

- [ ] **Step 1: 写失败测试**(用 Test-2 中 _FakeRepo 风格手写 fake repository)

test_context_collab_service.py(创建部分),核心断言:
1. 用 §6.2 的指定执行请求示例(request_id=="create-collet-001" 等)创建 → 返回 `collab_context_id` 以 `"collet_"` 开头、`context_version == 1`、`replayed is False`、`current_snapshot["status"] == "created"`、`current_snapshot["method_kind"] == "Single"`、`current_snapshot["executor_ref"] == "bot_coding_001"`、`current_snapshot["progress"]["current_event_id"]` 以 `"evt_"` 开头、`current_event["event_kind"] == "task.created"` 且 `parent_event is None`;
2. `method_kind="Single"` 且缺 executor → ContextCollabValidationError;
3. `product_kind="os"` / `workflow_kind="unknown"` / `method_kind="Swarm"` / `objective=""` → ValidationError;
4. Fake repo 报 `created=False`(幂等重放)时,`replayed is True` 且快照返回原值;
5. `ContextCollabService` 满足 `isinstance(..., ContextCollabServiceProtocol)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_context_collab_service.py -v`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现**

`authorization.py`、`service_api.py`(本任务用到的 dataclass/Protocol 全量定义)、`context_collab_service.py::create_context` 步骤:构造 ContextSpec(枚举校验)→ Authorizer.check_create → repo.create_context(初始 current_snapshot:status=created、progress.percent=0、current_event_id=None)→ 用 repo 返回的初始事件 event_id 回填 snapshot.progress.current_event_id → 组装 ContextCollabCreatedResult。fake repo 自行实现语义,真 repo 在 Task 8。

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/src/agentclaw/community/api/task/context_collab_service.py \
        apps/backend/tests/community/core/task/context_collab/test_context_collab_service.py
git commit -m "feat(backend): add context collab service api and create use case"
```

---

### Task 8: 持久化仓储 —— create_context + get_current_status

**Files:**
- Create: `.../core/task/context_collab/persistence/repository.py`
- Test: `apps/backend/tests/community/repository/task/context_collab/test_repository_create_get.py`

**Interfaces:**
- Consumes: Task 1 模型、Task 2/3/6 值对象、Task 5/6 helpers、`errors.py`。
- Produces(Task 10/11 消费): 完整 `ContextCollabRepositoryProtocol` + `ContextCollabRepository` 子类,与 Task 7 列出的三个方法签名逐字一致;补充类型:

```python
@dataclass
class InitialEventDraft:                 # 创建时固定为 task.created,由 service 提供
    event_kind: str                    # "task.created"
    primitive_type: str                # "Task"
    actor_ref: PrincipalRef            # 创建人
    payload: dict                      # {"objective": <objective>}

@dataclass
class ContextStatusRecord:             # get_current_status 返回
    collet: Collet
    events: list[EventRecord]           # 不做可见性过滤(trunk 过滤在 service/Query 层);顺序 sequence_no 升序
```

实现要点(照 `core/repository/implementations/task/forum/forum_repository.py` 的既有仓储写法):
- `@inject def __init__(self, db: DatabasePlugin)`;
- 读查询过滤 `env == get_current_env()`(tenant 由 guard 加);tenant guard 已 `_GUARDED_MODELS`,不要在 repo 手改 tenant 表达式;
- `create_context`:按 create_request_id 先查重放(命中返回 `created=False` + 原 collet + 初始事件);否则同事务插 Collet(initial snapshot/context_version=1,context_spec JSON 编码)+ 初始 Event(sequence_no=1,is_trunk=True,parent=None,context_version_after=1,occurred_at=datetime.now(timezone.utc));再次冲突 fallback IntegrityError 也走重放(mirror forum `_is_unique_conflict` 分类,索引名 `uk_context_collab_collet_request`);
- `get_current_status`:collet 不存在返回 None;events 全量升序返回,`trunk_events=True` 时只返回 is_trunk=True;
- `with_for_update()` 行锁本任务仅 read 不需要,在 append 路径使用(见 Task 9)。

注:`append_event` 本任务只留方法桩(`raise NotImplementedError(...)`),由 Task 9 完成并测试;协议三个方法的签名本任务一次定全。

- [ ] **Step 1: 写失败测试**

test_repository_create_get.py 配套 conftest(task 1 已放):
1. 用 §6.2 示例 payload 构造 CreateContextRecordRequest → `create_context`:数据库有 1 行 collet + 1 行 event(event_kind=task.created, sequence_no=1, is_trunk=True, parent_event=None, context_version_after=1);
2. 同一 create_request_id 二次调用 → `created=False`、返回原 collab_context_id;数据库仍只有 1 行 collet;
3. 初次创建 `created=True`;
4. `get_current_status` 在 trunk_events=False/True 两种情况下:True 时无分支事件照旧,后续手动插一条 is_trunk=False 的事件(直接 ORM 插)后 True 不含它,False 含它;
5. `get_current_status("<未知>")` 返回 None。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/repository/task/context_collab/ -v`
Expected: FAIL(ImportError: repository)

- [ ] **Step 3: 实现 repository.py**(真实现,deserialize/serialize JSON 列;append 方法本任务最小 stub:`raise NotImplementedError("append_event implemented in next task")`)

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2。Expected: PASS(包括 models 测试)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/persistence/repository.py \
        apps/backend/tests/community/repository/task/context_collab/test_repository_create_get.py
git commit -m "feat(backend): implement context collab repository create and read paths"
```

---

### Task 9: 持久化仓储 —— append_event(锁 + 幂等 + DAG + CAS)

**Files:**
- Modify: `.../core/task/context_collab/persistence/repository.py`
- Test: `apps/backend/tests/community/repository/task/context_collab/test_repository_append.py`

**Interfaces:**
- Consumes: Task 8 中协议签名与helpers;`compute_is_trunk`、`ensure_expected_version`、`ContextCollabVersionConflict`、`ContextCollabParentEventError`。
- Produces(签名在 Task 7 已列,本任务补全类型):

```python
@dataclass
class AppendEventRecordRequest:
    request_id: str
    expected_version: int
    event_kind: str
    primitive_type: str
    actor_ref: PrincipalRef
    parent_event: str | None
    visibility: str
    payload: dict
    snapshot_updater: Callable[[CurrentSnapshot], CurrentSnapshot]
    # ^ 由 Service(Task 10)构建的纯函数:G4 求解出的新快照;repo 在持锁事务内执行它。

@dataclass
class EventAppendedRecord:
    event: EventRecord
    collet: Collet          # snapshot_updater 执行后的新版本
    created: bool           # False = request_id 幂等重放(返回原事件)
```

实现要点(spec §9.4 规则 2、4、5):
- 进入 `transactional_orm_session()`,`with_for_update()` 读 collet 行;
- **版本检查**:`ensure_expected_version(expected_version, row.context_version)`,冲突抛 `ContextCollabVersionConflict`;
- **幂等重放**:按 `(collab_context_id, request_id)` 先查;命中返回 `created=False` + 原行(不再写入、不 bump version);
- **parent_event 校验**:`parent_event` 非 None 时必须命中该上下文内已存在事件(unique key + `(collab_context_id, parent_event)` 查询);否则抛 `ContextCollabParentEventError`;
- **sequence_no**:该上下文 `MAX(sequence_no)+1`(同一事务持锁分配,unique key `uk_context_collab_event_seq` 兜底);
- **is_trunk**:`compute_is_trunk(parent_event, row.current_event_id)`,发生在持锁路径;
- 写入 event + 更新 collet:casual UPDATE `WHERE context_version == expected_version` set current_snapshot=`snapshot_updater(current).to_dict()`、current_event_id、`context_version=expected_version+1`;改数 ≠1(并发被抢)抛 `ContextCollabVersionConflict`;
- event 的 `context_version_after = expected_version+1`、`occurred_at = datetime.now(timezone.utc)`;
- **数据库写失败向上抛** —— 禁止吞错。

- [ ] **Step 1: 写失败测试**(钉死 Review Focus #2/#3/#4)

test_repository_append.py:
1. 正常追加(user_interaction.posted 事件,parent=初始 evt):返回 created=True、`event.sequence_no == 2`、`row.context_version == 2`、`collet.current_event_id == event.event_id`、snapshot_updater 的效果体现(测试中传入 `lambda s: dataclasses.replace(s, status="in_progress")` 之类,断言新快照落库);
2. 同 request_id 二次调用 → `created=False`、返回原 event_id/context_version;数据库仍只有 2 行事件;
3. `expected_version=1` 再发(version 已是 2)→ `ContextCollabVersionConflict`;数据未变;
4. chain:第二次 append parent_event=event1.event_id 但 current_event_id=event2.event_id → `compute_is_trunk` 验证 event2.is_trunk 为 False(接入链头主干,链中为分支);
5. `parent_event="evt_not_exist"` → `ContextCollabParentEventError`;
6. parent_event=None(新主事件)→ `is_trunk=True`;
7. CAS 不变式:模拟并发,UPDATE WHERE 子句在 sqlite 不可测直接,改用:对旧 context_version 行(已实际应用)再次 snapshot_updater — 不必测,版本检查已覆盖。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/repository/task/context_collab/test_repository_append.py -v`
Expected: FAIL(NotImplementedError)

- [ ] **Step 3: 实现 append_event**(删掉 stub)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/repository/task/context_collab/ -v`
Expected: PASS(create/get + append 全绿)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/persistence/repository.py \
        apps/backend/tests/community/repository/task/context_collab/test_repository_append.py
git commit -m "feat(backend): implement locked idempotent context collab event append"
```

---

### Task 10: 追加事件用例(Service + 状态机接入)

**Files:**
- Modify: `.../core/task/context_collab/services/context_collab_service.py`
- Test: `apps/backend/tests/community/core/task/context_collab/test_context_collab_append.py`

**Interfaces:**
- Consumes: Task 5 `MACHINE_REGISTRY`/`resolve_transition`/`GUARD_REGISTRY`、Task 6 `ensure_expected_version`(repo 内)、Task 8/9 仓储。
- Produces — `service_api.py` 中本任务落实:

```python
@dataclass
class EventAppendCommand:
    request_id: str
    expected_version: int
    event_kind: str
    primitive_type: str
    actor_ref: PrincipalRef
    parent_event: str | None
    visibility: str
    payload: dict

@dataclass
class EventAcceptedResult:
    accepted: bool
    collab_context_id: str
    event_id: str
    parent_event: str | None
    event_kind: str
    primitive_type: str
    context_version: int
    current_status: str
```
- `ContextCollabService.append_event` 步骤(签名见 Task 7):repo.get_current_status 加载 collet(404 → NotFound)→ `validate_event_body`(G4 schema)→ `Authorizer.check_append` → 若 `MACHINE_REGISTRY` 命中 collet.context_spec.workflow_kind:
  build GuardOutcome(用 GUARD_REGISTRY + collet 上下文)→ `resolve_transition` 求新 status(guard FAIL 的 ValidationError 自然向外传);否则(无机或 no rule)status 维持 collet.current_snapshot.status;
  → 构造 `snapshot_updater = lambda s: replace(s, status=new_status)`(纯函数,在 repo 锁内执行)→
  repo.append_event → 组装 EventAcceptedResult(`current_status` 取返回 collet 的 current_snapshot.status;重放时 created=False 仍 `accepted: true`)。
- parent_event/expected_version 由 repo 在事务里最终把关;service 不预查。

- [ ] **Step 1: 写失败测试**(手写 fake repo 承载跨步骤语义)

test_context_collab_append.py(fake repo 在 create 场景里复用,内置对 snapshot_updater 的真实求值):
1. §6.3 示例(user_interaction.posted,expected_version=1,parent=初始 event)→ `accepted: true`、`context_version == 2`、`current_status == "created"`(无规则事件不动 status)、`event_id` 以 `"evt_"` 开头、`parent_event` 原样;
2. `workflow_kind=team_todo_execution` 的 collet:`task.started` + actor=principal → `current_status == "in_progress"`;`task.submitted` + payload 含 `artifacts: []` → ValidationError(required_artifacts_present FAIL);`task.accepted` + actor=executor(非 principal)→ ValidationError;
3. `event_kind="artifact.delivered"` → ValidationError(未注册);Message 事件 payload 只有 content_ref → ValidationError(Review Focus #1 在服务层二道闸);
4. 当 repo 抛 ContextCollabVersionConflict → service 原样向上抛(不吞、不改 500);
5. `visibility="team"` → ValidationError。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_context_collab_append.py -v`
Expected: FAIL(NotImplementedError / attr missing)

- [ ] **Step 3: 实现 append_event**(按 Interfaces 步骤;不超过 ~120 行,否则拆 helpers)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/test_context_collab_append.py
git commit -m "feat(backend): add context collab append_event use case with state machine"
```

---

### Task 11: 查询用例 + visibility 过滤(GET 投影)

**Files:**
- Modify: `.../core/task/context_collab/services/context_collab_service.py`
- Test: `apps/backend/tests/community/core/task/context_collab/test_context_collab_status.py`

**Interfaces:**
- Consumes: `project_events`、`build_progress`、`Authorizer.filter_visible_events`。
- Produces — `service_api.py`:

```python
@dataclass
class TaskProgressProjectionDTO:
    total_events: int
    events: list[dict]     # 每项含 event_id,parent_event,event_kind,primitive_type,status,summary,content

@dataclass
class ContextCollabStatusProjection:
    collab_context_id: str
    context_version: int
    current_snapshot: dict
    current_event: dict | None
    task_progress: TaskProgressProjectionDTO
```
- `ContextCollabService.get_status`:repo.get_current_status(不写库;None → NotFound)→ Authorizer.filter_visible_events → `project_events(trunk_events=...)` → `build_progress`;`current_snapshot` 里 progress 字段将被 build_progress 的实时值替换(反应 events 统计);`current_event` 取 `collet.current_event_id` 命中的投影(可能为 None)。

- [ ] **Step 1: 写失败测试**(对照 §6.4 示例组装)

构造 5 事件链(collet, current=evt_004,last_percent payload=40,末事件 pending):
1. GET 返回 `current_snapshot["status"] == "in_progress"`、`current_snapshot["progress"]["percent"] == 40`(payload 提供)、`progress["completed_count"] == 2`、`active_count == 1`、`blocked_count == 0`,以及 `build_progress(...).current_event_id == "evt_005"`(链头为最新事件);断言值全部取自 spec §6.4 示例。
2. `current_event["event_id"] == "evt_004"`、`current_event["status"] == "active"`(由 collet.current_event_id 决定);
3. `task_progress["events"]`:5 条按 sequence_no 升序,最后一条 pending,evt_004 active,前三条 completed;Message 事件条目含 `content == "上下文做一下补充xxx"`(从 payload),无 `steps` 字(§12.2 #5 — 断言字段集合不含 "steps");
4. Review Focus #5:同 collet 加一条 `is_trunk=False` 分支事件(`trunk_events=False` 返回 6 条,`True` 仍返回 5 条),且两次调用的 `current_snapshot`、`current_event`、`percent` 完全相等;
5. 不写库:repo.get_current_status 调用前后 collet 的 context_version 未变(mock repo 来验证二次 get 无写调用即可:简化:test repo 是 in-memory dict,version 字段直接断言未变);
6. visibility 过滤:actor 非 principal/executor 时 workspace 事件与 private 事件被滤除,public 保留(A10 域内最小版)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_context_collab_status.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 get_status**(纯 read 路径,无任何 update)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/core/task/context_collab/ \
        apps/backend/tests/community/core/task/context_collab/test_context_collab_status.py
git commit -m "feat(backend): add context collab read model with visibility filtering"
```

---

### Task 12: DI 装配 + Service 真集成(create→append→get 流,验收 A01-A09)

**Files:**
- Modify: `apps/backend/src/agentclaw/community/di/modules/task_persistence_module.py`(bind `ContextCollabRepositoryProtocol`)
- Modify: `apps/backend/src/agentclaw/community/di/modules/task_module.py`(bind `ContextCollabServiceProtocol`)
- Test: `apps/backend/tests/community/core/task/context_collab/test_service_real_repository.py`

**Interfaces:**
- Consumes: Task 1 conftest(engine/db fixtures)、Task 8/9 真 repo、Task 10/11 service。
- Produces: `ContextCollabRepository(injector)` 可解析;`ContextCollabService(repo, authorizer)` 以真 SQLite 仓储工作。

- [ ] **Step 1: 写失败测试**

test_service_real_repository.py(用 `tests/community/repository/task/context_collab/conftest.py` 的 `db` fixture):
1. **A01** 创建后 engine 直查两张表,各恰好 1 行;snapshot objective 含 spec 示例文本;
2. **A02** 追加 user_interaction.posted:1 行新事件(payload content 直存),collet.context_version=2;
3. **A03** 追加 task.started(actor=principal):status→in_progress;
4. **A04** 追加 task.completed:接纳成功但 status 不越迁(无规则,state 不动);数据库事件行数 +1;
5. **A06** in_progress(经 started)后 task.submitted(payload artifacts 非空)→ reviewing;task.accepted(actor=principal)→ done;
6. **A07/08** trunk_events True/False 差异(接一条分支事件后验证 True 少一条);
7. **A09** 重复 request_id(创建与事件两边)+ 旧 expected_version 分支:
   - 重复 create_request_id → replayed=True、表行数不变;
   - 重复事件 request_id → accepted True、原 event_id、表行数不变;
   - 旧 expected_version → ContextCollabVersionConflict;
8. DI 冒烟:`build_injector(profile=DeployProfile.TEST)`(测试 profile)能 get 出 `ContextCollabServiceProtocol` 实例(mock 不需要,真 resolver—— mirror `tests/community/repository/task/test_task_persistence_module.py` 的写法)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/test_service_real_repository.py -v`
Expected: FAIL(绑定缺失/注入不可解析)

- [ ] **Step 3: 改两个 DI 模块**(各加 1 bind + import;bind `ContextCollabServiceProtocol, to=ContextCollabService, scope=singleton`,authorizer 默认构造在 `ContextCollabService.__init__` 内 Optional)

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/di/modules/ \
        apps/backend/tests/community/core/task/context_collab/test_service_real_repository.py
git commit -m "feat(backend): wire context collab service and repository into DI"
```

---

### Task 13: /api/v1 HTTP Delivery + 错误映射

**Files:**
- Create: `apps/backend/src/agentclaw/community/adapters/http/task/schemas_context_collab.py`
- Create: `apps/backend/src/agentclaw/community/adapters/http/task/context_collab.py`
- Modify: `apps/backend/src/agentclaw/community/adapters/http/app.py`(import + `app.include_router(context_collab_router)`,紧邻 task_internal_router)
- Modify: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/responses.py`(`ENVELOPE_ERRORS` 加 4 行)
- Test: `apps/backend/tests/community/endpoints/test_context_collab_endpoints.py`

**Interfaces:**
- Consumes: `ContextCollabServiceProtocol`(import 自 `agentclaw.community.api.task.context_collab_service`)、`injected APIRouter` 模式(`intake_routes.py` 是最小模板)、`get_current_user` → `AuthenticatedUser`。
- Produces — Pydantic DTO(§6.5 命名逐字):
  `ColletCreateRequest`、`EventCreateRequest`、`ContextCollabStatusResponse`、`TaskProgress`、`ContextSpec`(嵌套 DTO)、L2/L3 引用 DTO(PrincipalRefDto/AgentRefDto/SpaceRefDto/ResourceRefDto)+ `*_to_dto` 转换;路由:

```python
router = APIRouter(prefix="/api/v1/collaboration/task", tags=["task-context-collab"])

@router.post("/context_collab", response_model=Envelope[ContextCollabCreatedResultDTO])
@envelope_errors
def create_context_collab(body: ColletCreateRequest, request: Request,
                          user: AuthenticatedUser = Depends(get_current_user),
                          service: ContextCollabServiceProtocol = Injected(ContextCollabServiceProtocol)):
    ...  # to_domain(); ActorContext(actor=PrincipalRef("human", user.staffId), source="api"); service 调用;DTO 转换

@router.post("/context_collab/{collab_context_id}/events", response_model=Envelope[EventAcceptedResultDTO])
@envelope_errors
def append_context_collab_event(collab_context_id: str, body: EventCreateRequest, ...): ...

@router.get("/context_collab/{collab_context_id}", response_model=Envelope[ContextCollabStatusResponse])
@envelope_errors
def get_context_collab_status(collab_context_id: str, trunk_events: bool = False, ...): ...
```

**ENVELOPE_ERRORS 新行(openapi_v1/responses.py)**:
```
ContextCollabNotFoundError: (404, "Not found")
ContextCollabVersionConflict: (409, "Context version conflict")
ContextCollabValidationError: (422, "Invalid context collab request")
ContextCollabParentEventError: (422, "Parent event not found")
```

- [ ] **Step 1: 写失败测试**

`test_context_collab_endpoints.py`(复制 `tests/community/endpoints/test_task_endpoints.py` 的用例写法 + FixedCaseInput fixture 模式;ServerSide 需 override `ContextCollabServiceProtocol` 到 in-memory fake(参考同文件 `bind_overrides(world, TaskServiceProtocol, {...})` 机制或直接 seed 真 service)):

先取简单路径(参考 knowledge_management/test_http.py 的 conftest 方式,直接 FastAPI() + include_router + dependency_overrides + attach_injector):
1. `POST /api/v1/collaboration/task/context_collab` 用 §6.2 示例 JSON → 200;响应 `Envelope{code,message,data}`;`data.collab_context_id` 以 "collet_" 开头(用真 service + 内存 SQLite);断言 HTTP path 里是单数 `task`(非 `tasks`);
2. `POST .../{id}/events` 用 §6.3 示例 JSON → 200;`data.context_version == 2`、`data.current_status == "created"`;
3. `GET .../{id}?trunk_events=true` → 200;`data.task_progress.total_events == 2`;响应字段含 `collab_context_id/context_version/current_snapshot/current_event/task_progress`;
4. 三个领域错误:`409`(version conflict)、`422`(Message 缺 content)、`404`(不存在 id)响应是 `ErrorEnvelope`(message 符合预期);
5. 未登录(`dependency_overrides[get_current_user]` raises 401)→ 401,fake 未被调用。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_context_collab_endpoints.py -v`
Expected: FAIL(import error / 404 on routes)

- [ ] **Step 3: 实现**

schemas_context_collab.py DTO(所有字段名与 spec §6.2/§6.3/§6.4 表逐字段对应:`trunk_events` query param 默认 False)+ context_collab.py 三个路由 + app.py 挂载 + responses.py 加四行。

- [ ] **Step 4: 跑测试确认通过**——同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/adapters/http/ \
        apps/backend/tests/community/endpoints/test_context_collab_endpoints.py
git commit -m "feat(backend): expose context collab service api via /api/v1 routes"
```

---

### Task 14: /openapi/v1 Delivery + authorization/admission 表注册

**Files:**
- Create: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/task/context_collab.py`
- Modify: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/__init__.py`(`build_public_router()` 末尾 include)
- Modify: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/authorization_rules_task.py`(3 行)
- Modify: `apps/backend/src/agentclaw/community/adapters/http/openapi_v1/admission_task_routes.py`(3 行)
- Test: `apps/backend/tests/community/endpoints/test_openapi_context_collab_endpoints.py`

**Interfaces:**
- Consumes: `PublicAPIRoute`、`require_principal`(`PrincipalDep = Annotated[Principal, Depends(require_principal)]`)、`envelope`/`envelope_errors`、Task 13 的 DTO(复用 schemas_context_collab.py — 起 `双面 adapter` 规则,openapi 任务只做身份转换与路径映射,与内部副本同步维护)。
- Produces:

```python
router = APIRouter(prefix="/openapi/v1/collaboration/task/context_collab",
                   tags=["task-context-collab"], route_class=PublicAPIRoute)
# 三个 handler:同 Task 13 委托同一个 ContextCollabServiceProtocol;
# principal → ActorContext 的转换:对比 openapi_v1/task/router.py 现有写法,从
# Principal 取 end-user 身份字段构造 PrincipalRef。
```
- authorization_rules_task.py 加(`AUTHORIZATION` 结构同现有行):
  `("POST", "/openapi/v1/collaboration/task/context_collab"): NoCheck(...)` 等三行(reason 文案照 house 风格:本 API 的操作者是责任主体,gateway 已认证主体;P1 不挂空间级 Check)。
- admission_task_routes.py 加三行 TASK_ADMISSION(照抄同表现有 permissive/非机器调用行格式)。

- [ ] **Step 1: 写失败测试**

`test_openapi_context_collab_endpoints.py`(完全照抄 `test_openapi_task_endpoints.py` 的 `_boot_verifier`/`_principal()` 规格 - JWT 头注入,断言):
1. 带 `X-Avernet-Principal` 头调用三个路径(路径前缀 `/openapi/v1/collaboration/task/context_collab`)各返回 200;body 是 `Envelope{...data}`;第二个接口请求体同 §6.3;
2. 无头 / 坏头 → 401(参考同文件 existing rejection cases);
3. 409 conflict 与 422 验证错误产生 `ErrorEnvelope`(不然错误映射走不到统一 envelope);
4. `assert_every_route_authorized` 导入期断言不再触发(`build_public_router()` 可成功构建);`tests/community/endpoints/test_admission_inventory.py` 与 `tests/community/endpoints/test_public_routes_require_principal.py` 全部通过。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_openapi_context_collab_endpoints.py tests/community/endpoints/test_admission_inventory.py tests/community/endpoints/test_public_routes_require_principal.py -v`
Expected: FAIL(路由缺失 / authorization 表缺行)

- [ ] **Step 3: 实现**(handler 复用 schemas_context_collab.py 的 DTO;错误已映射在 Task 13;admission/authorization 三行;__init__ include)

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2。Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/agentclaw/community/adapters/http/openapi_v1/ \
        apps/backend/tests/community/endpoints/test_openapi_context_collab_endpoints.py
git commit -m "feat(backend): expose context collab openapi delivery surface"
```

---

### Task 15: 验收清单核查 + 回归

**Files:**
- Test: `apps/backend/tests/community/core/task/context_collab/test_spec_acceptance.py`(把 spec §12.2 六条各钉一个断言,与上面测试 case 的关系是 anchor/防退化的冗余断言,不必重建全部 fixture:用 Task 8 conftest + service 直接序贯调用)

**Interfaces:**
- Consumes: 全部前置任务的接口。

- [ ] **Step 1: 写测试**(§12.2 验收 #1-#6 各一个函数:1=两表两行结构;2=缺 executor 拒绝;3=user_interaction content 直存且无 content_ref;4=每个 event 带 parent_event 字段 + trunk 过滤不改统计;5=响应结构三段式 + 无 steps 字段;6=get 前后 context_version 不变)

- [ ] **Step 2: 跑全量上下文回归**

Run:
```bash
cd apps/backend && DEPLOY_PROFILE=test uv run pytest \
  tests/community/core/task/context_collab/ \
  tests/community/repository/task/context_collab/ \
  tests/community/endpoints/test_context_collab_endpoints.py \
  tests/community/endpoints/test_openapi_context_collab_endpoints.py -v
```
Expected: 全 PASS

- [ ] **Step 3: 跑全局门禁级回归**

Run: `cd apps/backend && ./scripts/ci_test.sh`
Expected: PASS(改动了 core/schema.py、app.py、responses.py 等公共文件,必须全量验证)

- [ ] **Step 4: Commit**

```bash
git add apps/backend/tests/community/core/task/context_collab/test_spec_acceptance.py
git commit -m "test(backend): pin spec acceptance criteria for context collab"
```

---

## 收尾(执行完成后)

1. 检查所有新文件行数 < 1000(spec 与 CI 强制)。
2. 对照本计划 Review Focus 五条,确认每条的钉子测试存在且通过。
3. PR 标题:`feat(backend): add context collab protocol P1 (table, service api, delivery)`;PR 描述按 `.github/pull_request_template.md` 的 Problem/Solution/Validation/Compatibility and risk/Spec(链接本 spec 与本 plan)。

## 本计划未覆盖(留给 spec 评审/P2)

- L1 `a2a_bridge.py`、`g3 observation.py` 中 Observation/Outcome 的事实生成(外部执行系统回报接入后按 §5.3 规则 6 实现);
- forum_public_discussion 专属状态机(spec §15 评审问题 #4 未定);
- 外部 Membership/Grant/Policy Authority 真实授权(评审问题 #2;P1 只做结构校验 + Protocol 接缝);
- `Swarm`、Retry/Lease/Heartbeat、Metric/Candidate/Verification/Experiment Ledger(§2.2/§2.3 显式不引入)。
