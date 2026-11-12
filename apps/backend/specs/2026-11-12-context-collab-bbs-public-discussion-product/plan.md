# Context Collab BBS 公开讨论产品层 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 context_collab 协议原语之上实现 BBS 公开讨论产品层四用例（发帖/回帖/列表/详情），product profile 复用既有 `bbs_public_discussion`，不动 `core/task/forum`、不动表结构与既有 wire。

**Architecture:** 产品层 `products/` 编排既有协议服务——写路径走 `ContextCollabServiceProtocol`（create_context + append_event，复用 G1 窄授权与乐观并发），profile 校验与 parent 域断言经 `ContextCollabRepositoryProtocol` 读 record 伴随字段；列表靠仓储一条**新增只读方法**（按 product_kind 列 Collet + Message 事件聚合计数）；HTTP 新路由两侧（内部 + OpenAPI 镜像）。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy（SQLite 测试）、python-injector DI、pytest。

**Spec:** `apps/backend/specs/2026-11-12-context-collab-bbs-public-discussion-product/spec.md`（Accepted；§2 域映射、§6 裁决表 A–F 为权威，本计划不重复）。

## Global Constraints

- 分支 `dev_bbs_help_1019_3` 直接叠加 HEAD（`ada09890b`）。
- **不动** `core/task/forum` 任何代码；**不动** `ac_context_collab_collet`/`ac_context_collab_event` 表结构与既有迁移；**不改**三个既有 context_collab wire 路由。
- 测试命令：`cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/repository/task/context_collab/ tests/community/di/test_task_module.py tests/community/endpoints/test_bbs_public_discussion_endpoints.py -v`（用户指示：CI 门禁与全量套件不跑）。
- 提交规范 `<type>(backend): <outcome>`，末尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 任一文件 ≤1000 行；不格式化未改动行；不新增依赖。
- 异常只用既有 `ContextCollabNotFoundError` / `ContextCollabValidationError` / `ContextCollabVersionConflict`；HTTP 侧沿用既有 `ENVELOPE_ERRORS` 映射（404/409/422）。
- `T | None` 只用于合法领域可空（`parent_event`、孤儿帖 `body`、`parent_event_id` 入参）。
- 词汇常量从源头 import（`MethodKind.OPEN_GROUP.value`、`ProductProfile.BBS_PUBLIC_DISCUSSION.value`、`VISIBILITY_PUBLIC`），不得写字符串字面量漂移；event_kind `"user_interaction.posted"` / primitive `"Message"` 与 `schema.validate_event_body` 合同一致（payload `{"content": <非空str>}`）。

## Review Focus

1. **骨架事件被当 parent**：回帖 `parent_event_id` 指向 `task.created`/Task 事件能通过仓储同流存在性校验——产品层必须以 Message 域断言拒绝，否则详情投影把骨架吸进回复树。→ Task 2 Step 1 `test_reply_rejects_skeleton_event_parent`。
2. **跨产品面误入**：其他 profile 的 Collet（如 `team_todo`）凭 collet_id 打本产品回帖/详情——必须 404 拒（协议级 404 由产品先判 profile 再转发）。→ Task 2 Step 1 `test_reply_rejects_foreign_profile_collet`、Task 3 `test_detail_rejects_foreign_profile`。
3. **私有事件泄漏进公开读面**：经既有通用路由写入的 workspace/private Message 出现在详情回帖列表——只投影 public。→ Task 3 `test_detail_only_projects_public_messages`。
4. **孤儿帖读面**：发帖两步第二步失败留下的"有标题无正文"行——详情 `body=None` 容忍不 422，列表照常展示。→ Task 3 `test_detail_tolerates_bodyless_collet`。
5. **不可解码外部行炸整页列表**：裸 SQL 插 `actor_ref=NULL` 行后，列表跳过该行继续返回其它帖；单点 get/详情维持方案 i 严格失败。→ Task 3 `test_list_skips_undecodable_rows` + 单点反例。
6. **并发回帖 CAS**：两回帖争读同一 version，后落者必须 409 冲突拒绝，不静默覆盖。→ Task 2 `test_reply_version_conflict_propagates`。

---

### Task 1: 仓储列表读路径（按 product_kind 列 Collet + 发言计数）

**Files:**
- Modify: `src/agentclaw/community/core/task/context_collab/persistence/repository.py`（协议类 + 实现类各加一方法；`ContextSummaryRecord` dataclass 放本文件，紧邻 record 定义）
- Test: `tests/community/repository/task/context_collab/test_repository_listing.py`（新建）

**Interfaces:**
- Produces（先于实现者读到的唯一权威签名）：

```python
@dataclass
class ContextSummaryRecord:
    collet: Collet            # 六字段聚合根（复用 _collet_from_row 的严格解码）
    context_spec: ContextSpec  # 伴随字段：title(objective)/initiator 从这里读
    created_at: datetime      # collet 表 gmt_create 列
    message_count: int        # 该 Collet primitive_type="Message" 事件总数（含首帖正文）

def list_collab_contexts_by_product(self, product_kind: str) -> list[ContextSummaryRecord]: ...
# SQLAlchemy 实现:collet 行全读 + 单条 GROUP BY 事件计数查询(2 次往返,禁 N+1);
# product_kind 过滤在 Python 侧解 JSON(spec §6-E);返回按 gmt_create 倒序;
# _collet_from_row 抛 ContextCollabValidationError 的行跳过并 logger.warning(含 collab_context_id),不炸整页。
```
- Consumes: `_collet_from_row`、`ContextCollabEventModel`/`ContextCollabColletModel`（同文件既有）。
- 既有手写 fake 仓储（tests 内 `_FakeCollabRepository`、endpoints/DI 测试 stub）不受影响——它们以鸭子类型服务测试，不实现新法；DI/接口 conformance 无 isinstance 检查，无需同步。

- [ ] **Step 1: 写失败测试**——真 SQLite 仓储（沿 `test_repository_create_get.py` 的 engine/session 夹具模式）：`test_lists_only_matching_product_kind`（建 `bbs_public_discussion` 与 `team_todo` 各一帖，只回前者）、`test_message_count_counts_all_message_events`（一 Collet 追加正文+2 回帖 + 一条 Task 类事件，`message_count==3`）、`test_list_orders_by_gmt_create_desc`、`test_list_skips_undecodable_rows`（裸 SQL 插 `actor_ref=NULL` 行 + 一条好行 → 返回只剩好行）。

- [ ] **Step 2: 跑失败**——`uv run pytest tests/community/repository/task/context_collab/test_repository_listing.py -v`，预期 FAIL（方法不存在）。
- [ ] **Step 3: 实现**——协议抽象方法 + ORM 实现（spec §4：Python 侧过滤、GROUP BY 计数、倒序、坏行跳过+警告日志）；只读，无写路径改动。
- [ ] **Step 4: 跑通过**，并补跑 `tests/community/repository/task/context_collab/ -v` 零回归。
- [ ] **Step 5: Commit** `feat(backend): add collab context list read path by product profile`

### Task 2: 发帖 + 回帖写用例（products 服务）

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/products/service_api.py`（命令/视图/协议）
- Create: `src/agentclaw/community/core/task/context_collab/products/bbs_public_discussion/__init__.py`
- Create: `src/agentclaw/community/core/task/context_collab/products/bbs_public_discussion/service.py`
- Create（空文件已在）：`src/agentclaw/community/core/task/context_collab/products/__init__.py`
- Test: `tests/community/core/task/context_collab/products/test_bbs_public_discussion_write.py`（新建）

**Interfaces:**
- Produces（Task 3/4/5 消费，签名逐字）：

```python
# products/service_api.py
@dataclass
class CreateDiscussionCommand:
    title: str
    body: str
    space: SpaceRef
    resource_refs: list[ResourceRef] = []    # dataclass 默认用 field(default_factory=list)

@dataclass
class ReplyDiscussionCommand:
    collab_context_id: str
    content: str
    parent_event_id: str | None = None

@dataclass
class DiscussionCreatedView:
    collet_id: str
    title: str
    body_event_id: str
    context_version: int

@dataclass
class ReplyCreatedView:
    collab_context_id: str
    event_id: str
    parent_event_id: str | None
    context_version: int

@runtime_checkable
class BbsPublicDiscussionServiceProtocol(Protocol):
    def create_discussion(self, command: CreateDiscussionCommand, context: ActorContext) -> DiscussionCreatedView: ...
    def reply_discussion(self, command: ReplyDiscussionCommand, context: ActorContext) -> ReplyCreatedView: ...
    def list_discussions(self) -> list["DiscussionSummaryView"]: ...     # Task 3 实现
    def get_discussion(self, collet_id: str) -> "DiscussionDetailView": ...  # Task 3 实现
```
- 读方法本任务签名先钉、体留 `NotImplementedError`（Task 3 用真实现替换）；协议一次性定形避免两改。
- Consumes: `ContextCollabServiceProtocol.create_context(command, context)` / `.append_event(collab_context_id, command, context)` / `.get_status(...)`、`ContextCollabRepositoryProtocol.get_current_status`（Task 1 前既有）、`ContextCollabCreatedResult` / `EventAcceptedResult`、`MethodKind.OPEN_GROUP`、`ProductProfile`/`WorkflowKind.BBS_PUBLIC_DISCUSSION`、`VISIBILITY_PUBLIC`。
- 实现要点（spec §3.1 权威转写）：
  - `create_discussion`：title 非空、body 非空；`ContextCreateCommand(空间=command.space, principal_ref=actor.actor, product_kind=BBS_PUBLIC_DISCUSSION.value, workflow_kind=BBS_PUBLIC_DISCUSSION.value, method_kind=MethodKind.OPEN_GROUP.value, executor_ref=None, objective=title, resource_refs=...)` → `append_event(expected_version=1, event_kind="user_interaction.posted", primitive_type="Message", actor_ref=actor.actor, parent_event=None, visibility=VISIBILITY_PUBLIC, payload={"content": body})`；视图取 `EventAcceptedResult`。
  - `reply_discussion`：`get_current_status` → None `ContextCollabNotFoundError`；`record.context_spec.product_kind != BBS_PUBLIC_DISCUSSION.value` → `NotFound`；`parent_event_id` 非 None 时找目标事件、非 `primitive_type=="Message"` → `ValidationError`（骨架事件断言；存在性仓储会把守）；`append_event(expected_version=record.collet.version, ...公共承载体同上)`。

- [ ] **Step 1: 写失败测试**——夹具：真 `ContextCollabService`（`InMemoryMethodStore` + 结构授权器 + 内存假仓储，沿 `test_context_collab_service.py` 桩仓储模式）：
  - `test_create_discussion_wires_title_objective_and_body_event`（发帖后假仓储里 Collet `objective==title`、事件流 `[task.created, Message正文]`、视图四字段）；
  - `test_reply_baseline`（发帖→回帖 None parent→回帖挂 body_event_id 楼中楼，event_id/parent 往返）；
  - `test_reply_rejects_skeleton_event_parent`（parent 指向 `task.created` 事件 → Validation 错误）；
  - `test_reply_rejects_foreign_profile_collet`（假仓储回 `team_todo` spec 的 record → NotFound）；
  - `test_reply_unknown_collet_not_found`；
  - `test_reply_version_conflict_propagates`（expected_version 落后 → 冲突异常透传）；
  - `test_reply_allows_stranger_authenticated_actor`（陌生 actor 走 G1 窄规则放行 public Message）。
- [ ] **Step 2: 跑失败**（ImportError：模块不存在）。
- [ ] **Step 3: 实现** service_api.py + service.py（两文件同一次 RED→GREEN）。
- [ ] **Step 4: 跑通过** + `tests/community/core/task/context_collab/ -v` 目录零回归。
- [ ] **Step 5: Commit** `feat(backend): add BBS public discussion write use cases`

### Task 3: 列表 + 详情读用例 + DI 绑定

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/products/bbs_public_discussion/views.py`（读视图 dataclass + 纯函数投影）
- Modify: `.../products/bbs_public_discussion/service.py`（补两个读方法实现）
- Modify: `src/agentclaw/community/di/modules/task_module.py`（Import + bind）
- Test: `tests/community/core/task/context_collab/products/test_bbs_public_discussion_read.py`（新建）、`tests/community/di/test_task_module.py`（追加一用例）

**Interfaces:**
- Produces：

```python
# views.py
@dataclass
class DiscussionSummaryView:
    collet_id: str; title: str; initiator_id: str; message_count: int; created_at: datetime

@dataclass
class DiscussionDetailBody:    # 首帖正文事件
    event_id: str; content: str; author_id: str; occurred_at: datetime

@dataclass
class DiscussionReplyItem:
    event_id: str; content: str; author_id: str; parent_event_id: str | None; occurred_at: datetime

@dataclass
class DiscussionDetailView:
    collet_id: str; title: str; state: str  # ColletState.value
    body: DiscussionDetailBody | None      # 孤儿帖 None（合法领域态）
    replies: list[DiscussionReplyItem]
```
- 投影规则（spec §3.1 权威转写）：标题=初始事件 payload["objective"]，NULL-payload 外部行回退伴随 `context_spec.objective`（P3 撤列时移除；[创建事实 spec](../2026-11-12-context-collab-l4-creation-event-facts/) §3 裁决 1）；`initiator_id`/`author_id` 取 `PrincipalRef.principal_id`；正文=事件流第一条 `user_interaction.posted`+`Message` 事件（payload 从 `EventRecord.payload["content"]` 读）；replies=其余 Message 事件、**只投影 public**（visibility NULL 按 private 处理）、按表插入序；外 profile Collet → NotFound。
- DI：`binder.bind(BbsPublicDiscussionServiceProtocol, to=BbsPublicDiscussionService, scope=singleton)`，注释风格沿 MethodStoreProtocol 段；服务构造 `@inject(ContextCollabServiceProtocol, ContextCollabRepositoryProtocol)`。

- [ ] **Step 1: 写失败测试**——夹具沿 Task 2（假仓储或真 SQLite 二选一，照 Task 2 已立模式）：
  - `test_lists_discussions_with_message_count_and_order`（两帖不同 gmt_create，断言倒序、title/initiator/message_count）；
  - `test_get_discussion_assembles_title_body_and_replies`（标题+正文+2 回复+楼中楼 parent 链）；
  - `test_detail_only_projects_public_messages`（假仓储事件混入 `workspace` Message → 不在 replies）;
  - `test_detail_tolerates_bodyless_collet`（事件流只有 task.created → `body is None`）；
  - `test_detail_rejects_foreign_profile`（team_todo record → NotFound）；
  - `test_get_discussion_unknown_not_found`。
- [ ] **Step 2: 跑失败 → Step 3: 实现 views.py + service 两读方法 + DI 绑定 → Step 4: 跑通过**（含 `tests/community/di/test_task_module.py -v`：追加 `test_task_module_binds_bbs_public_discussion_service_singleton`——解析协议、instanceof 实现类、两次取同对象、共享注入的 context_collab 服务/repo）。
- [ ] **Step 5: Commit** `feat(backend): add BBS public discussion read use cases and DI wiring`

### Task 4: HTTP 路由（内部 + OpenAPI 镜像）

**Files:**
- Create: `src/agentclaw/community/adapters/http/task/schemas_bbs_discussions.py`（Pydantic 请求/响应模型，命名沿 `schemas_context_collab.py` 惯例；响应 DTO 字段名 = 视图字段名，camel 由既有惯例定夺，先钉 snake_case 与 `to_domain()` 对齐三既有 schema 文件风格）
- Create: `src/agentclaw/community/adapters/http/task/bbs_discussions.py`（内部路由）
- Create: `src/agentclaw/community/adapters/http/openapi_v1/task/bbs_discussions.py`（镜像路由）
- Modify: `src/agentclaw/community/adapters/http/app.py`（import + `app.include_router(...)`，紧邻 context_collab 段落与行号）
- Modify: `src/agentclaw/community/adapters/http/openapi_v1/authorization_rules_task.py`（4 行 NoCheck，理由文案沿 context_collab 既例）
- Create: `src/agentclaw/community/api/task/bbs_public_discussion_service.py`（协议再导出，沿 `api/task/context_collab_service.py` 惯例）
- Test: `tests/community/endpoints/test_bbs_public_discussion_endpoints.py`（新建，沿 `test_context_collab_endpoints.py` 配方：fresh FastAPI + 路由 + 认证 override + injector 绑真服务/真仓储 + 内存 SQLite）

**Interfaces:**
- 路由钉死（spec §5）：

| 路由 | 规格 |
| --- | --- |
| `POST /api/v1/collaboration/task/context_collab/bbs/discussions` | body `{"title", "body", "space", "resource_refs"?}` → `DiscussionCreatedView` |
| `POST /api/v1/collaboration/task/context_collab/bbs/discussions/{collab_context_id}/replies` | body `{"content", "parent_event_id"?}` → `ReplyCreatedView` |
| `GET /api/v1/collaboration/task/context_collab/bbs/discussions` | → `list[DiscussionSummaryView]` |
| `GET /api/v1/collaboration/task/context_collab/bbs/discussions/{collab_context_id}` | → `DiscussionDetailView` |

- `/openapi/v1/collaboration/task/context_collab/bbs/discussions` 同四者镜像（路径前缀不同，body/响应合同相同；actor 解析为网关认证主体，沿 `openapi_v1/task/context_collab.py` 既有模式；写入路径拒绝伪造 actor_ref 与三既有路线同款由服务层承载）。
- 错误映射：`@envelope_errors` + 既有 `ENVELOPE_ERRORS`（NotFound404/冲突409/校验422）零新增；`_actor_context` 同款内部。
- Consumes: Task 2 `BbsPublicDiscussionServiceProtocol` × DI `Injected(...)`。

- [ ] **Step 1: 写失败测试**——同文件两套客户端（内部 + OpenAPI）：发帖→回帖→列表→详情全链 e2e；断言：路由前缀逐字、列表计数、详情 body/replies、404（乱 id）、回帖 422（parent 骨架）、409 不在本测试重测（服务层已钉）；OpenAPI 客户端同链 smoke 一条。
- [ ] **Step 2: 跑失败**（路由不存在）→ **Step 3: 实现四文件 + 两处 Wiring 修改** → **Step 4: 跑通过** + `tests/community/endpoints/ -v` 既有 endpoints 零回归。
- [ ] **Step 5: Commit** `feat(backend): expose BBS public discussion product routes`

### Task 5: 收尾——spec 登记 + 回归

**Files:**
- Modify: `specs/README.md`（按字母序插一行）
- 回归：目录全量

- [ ] **Step 1:** README 索引登记 + 按需把本次工作目录（`apps/backend/specs/2026-11-12-context-collab-bbs-public-discussion-product/`）随同入提交。
- [ ] **Step 2:** `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/repository/task/context_collab/ tests/community/di/test_task_module.py tests/community/endpoints/ -v` 整段全绿（原有 745 + 新增 L5/L6 增量）。
- [ ] **Step 3: Commit** `docs(backend): register BBS public discussion product spec`

---

## Self-Review 结论

- Spec 覆盖：§2 域映射→T2/T3（标题=objective、正文事件、parent=None、楼中楼）；§3.1 四用例→T2/T3；§3.1 写结果视图（无时间字段）与 A 裁决（message_count）→T2/T3 接口块逐字；§4 列表读路径与坏行跳过→T1；§5 路由+镜像→T4；§6 A–F 全数为接口块/实现要点引用；§7 测试表分解到各 Task Step 1。
- Review Focus 6 处输入类全部有归属测试（骨架 parent/外 profile→T2、泄漏/孤儿/坏行→T3、CAS→T2）。
- 类型一致：`ContextSummaryRecord`、`CreateDiscussionCommand` 系、视图五元、协议方法名五处引用（T1/T2/T3/T4）逐字对齐。
- 比例：5 任务约 14 个新文件，签名与测试断言为主、零方法体转写；用户已选的执行法为 subagent-driven 之外——本计划与前两单同库同模式，推荐 native（executing-plans）——5 任务接口顺势依赖，最后有整支评审兜底。
