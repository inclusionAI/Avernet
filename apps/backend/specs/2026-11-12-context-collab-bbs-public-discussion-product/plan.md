# Context Collab BBS 公开讨论产品层 实现计划（v2 乙案）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 context_collab 层之上实现 BBS 公开讨论产品四用例（发帖/回帖/列表/详情），P1 交付面=模块级 service 接口（产品协议 + DI singleton，无 HTTP）：products/ 只组合协议层模块与三处基建端口（乙案），`service_api.py` 对产品永久不依赖，架构测试执法；不动 forum、DDL、既有 wire。

**Architecture:** 平台归位二连（ActorContext 迁 G1、creation_payload 纯函数迁 L4）→ 仓储两读方法（域类型直出，零发明 record）→ 产品自持编排（L4 对象 + G1/G4 + 仓储端口 + L5 顶层投影）+ 架构测试 → 读投影 + api 协议再导出 → 收尾。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy（SQLite 测试）、python-injector DI、pytest。

**Spec:** `apps/backend/specs/2026-11-12-context-collab-bbs-public-discussion-product/spec.md`（v2 Accepted；§2 域映射、§2a 操作族映射表、§3.1 依赖矩阵、§6 裁决 A–J 为权威，本计划不重复）。

## Global Constraints

- 分支 `dev_bbs_help_1019_3` 直接叠加 HEAD。
- **乙案依赖禁令**：`src/…/context_collab/products/**/*.py` 的 import 白名单 = 层模块（`l2_agent_network`/`l3_spaces_resources`/`l4_context_primitives`/`l5_methods`/`l6_products`/`g1_secure_govern`/`g2_keep_reliable_live`/`g3_observe_evaluate_evolve`/`g4_contracts_consistency`）+ `errors` + `persistence.repository` + `persistence.method_store` + `services.method_lifecycle_service`；`service_api.py`、`services/context_collab_service.py`、`method_service_api.py` 等其余模块**永久禁止**。
- 不动 `core/task/forum`；不动 `ac_context_collab_collet`/`ac_context_collab_event` 表结构；不改三个既有 wire 路由。
- 测试命令：`cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/repository/task/context_collab/ tests/community/di/test_task_module.py -v`（CI 门禁与全量套件不跑——用户指示；P1 无 HTTP 改动，不跑 endpoints）。
- 提交 `<type>(backend): <outcome>`，尾行 `Co-Authored-By: Claude <noreply@anthropic.com>`；任一文件 ≤1000 行；不格式化未改动行；不新增依赖。
- 异常只用既有 `ContextCollabNotFoundError` / `ContextCollabValidationError` / `ContextCollabVersionConflict`；HTTP 沿 `ENVELOPE_ERRORS`。
- 词汇常量从源头 import（`MethodKind.OPEN_GROUP.value`、`ProductProfile/WorkflowKind.BBS_PUBLIC_DISCUSSION.value`、`VISIBILITY_PUBLIC`）；`"user_interaction.posted"`/`"Message"` 与 G4 `validate_event_body` 合同一致（payload `{"content": 非空str}`）。

## Review Focus

1. 骨架事件被当 parent：回帖指向 `task.created` 能过仓储存在性校验——产品域断言拒。→ T3 `test_reply_rejects_skeleton_event_parent`
2. 跨产品面误入：他 profile Collet 凭 id 打回帖/详情——404。→ T3 `test_reply_rejects_foreign_profile_collet`、T4 `test_detail_rejects_foreign_profile`
3. 私有事件泄漏：workspace/private Message 出现在详情回帖列表——只投影 public。→ T4 `test_detail_only_projects_public_messages`
4. 孤儿帖：正文事件缺失——详情 body=None 不 422。→ T4 `test_detail_tolerates_bodyless_collet`
5. 坏行炸整页：NULL actor_ref 外部行在列表被跳过带日志；单点照旧严格失败。→ T2 `test_list_skips_undecodable_rows`
6. 并发 CAS：同 version 争抢后落者 409 不静默覆盖。→ T3 `test_reply_version_conflict_propagates`
7. **依赖违约**：products 任一文件 import `service_api` 即架构测试红。→ T3 `test_products_import_whitelist`（含故意探针断言）

---

### Task 1: 平台归位二连（行为不变重构）

**Files:**
- Create: `src/agentclaw/community/core/task/context_collab/g1_secure_govern/actor_context.py`（`ActorContext` 定义迁移；`g1` 包 `__init__` 视现状补导出）
- Modify: `src/agentclaw/community/core/task/context_collab/service_api.py`（类定义换为 `from …g1_secure_govern.actor_context import ActorContext` 再导出）
- Create: `src/agentclaw/community/core/task/context_collab/l4_context_primitives/creation_facts.py`（`creation_payload(spec: ContextSpec, objective: str) -> dict`）
- Modify: `src/agentclaw/community/core/task/context_collab/services/context_collab_service.py`（payload inline dict 改调 `creation_payload`）
- 大约 10 处 `from service_api import ActorContext` 的 import **零改动**（再导出兼容）；`g1_secure_govern/authorization.py` 等 G1 模块可顺手改从 G1 新家 import（属零行为变化）

**Interfaces:**
- Produces（T3–T5 消费，签名逐字）：

```python
# g1_secure_govern/actor_context.py
@dataclass(frozen=True)
class ActorContext:
    actor: PrincipalRef
    source: str  # "api" | "openapi"

# service_api.py 顶部：from …g1_secure_govern.actor_context import ActorContext  # 再导出,既有 import 零破坏

# l4_context_primitives/creation_facts.py
def creation_payload(spec: ContextSpec, objective: str) -> dict
# 返回六键全量创建事实(objective/resource_refs/method_kind/executor_ref/
# product_kind/workflow_kind),与 f91d98cd5 行为逐字一致
```
- Consumes: `ContextSpec`、`ResourceRef.to_dict()`。

- [ ] **Step 1:** 先跑 `tests/community/core/task/context_collab/ -v` 记基线全绿。
- [ ] **Step 2:** `creation_facts.py` 实现 + `context_collab_service.py` 改用（服务 pin `test_creates_collet_with_initial_event` 已钉 six-key 全量断言，跑它必须仍绿——这是行为不变的证据）。
- [ ] **Step 3:** `ActorContext` 定义迁 `g1_secure_govern/actor_context.py`,`service_api` 改再导出;`g1`/`method_authorization`/`authorization` 两处 import 换新家。
- [ ] **Step 4:** 全回归 `tests/community/core/task/context_collab/ tests/community/repository/task/context_collab/ -v` 零回归。
- [ ] **Step 5: Commit** `refactor(backend): move ActorContext home to G1 and extract creation payload helper`

### Task 2: 仓储列表读路径（persistence 内部）

**Files:**
- Modify: `src/…/context_collab/persistence/repository.py`（协议+实现各一方法；`ContextSummaryRecord` dataclass 紧邻 record 定义）
- Test: `tests/community/repository/task/context_collab/test_repository_listing.py`（新）

**Interfaces:**
- Produces（T4 消费）：

```python
def list_collets_by_product(
    self, product_kind: str
) -> list[tuple[Collet, ContextSpec, datetime]]
# 元组三元素:六字段聚合根、创建参数(过滤源+过渡期 title 伴随)、gmt_create;
# Python 侧过滤(§6-E);gmt_create 倒序;解码失败外部行跳过+logger.warning。

def count_message_events(
    self, collab_context_ids: Sequence[str]
) -> dict[str, int]
# 单条 GROUP BY(primitive_type="Message"),键=collab_context_id;禁 N+1。
```
- Consumes: `_collet_from_row`、两 ORM 模型。**零发明 record**——v1 的 `ContextSummaryRecord` 作废（spec §4）。

- [ ] **Step 1: 失败测试**：真 SQLite（沿 `test_repository_create_get.py` 夹具模式）`test_lists_only_matching_product_kind`（bbs_public_discussion 与 team_todo 各一行只回前者）、`test_list_orders_by_gmt_create_desc`（等宽时钟或秒级插入两行断言倒序）、`test_list_skips_undecodable_rows`（裸 SQL NULL actor_ref 行+好行 → 只回好行）、`test_count_message_events_groups_in_one_query`（两 Collet 计数互不串、Task 事件不计、未知 id 不在结果键里）。
- [ ] **Step 2: 跑失败** → **Step 3: 实现** → **Step 4: 跑绿** + `tests/community/repository/task/context_collab/ -v` 零回归。
- [ ] **Step 5: Commit** `feat(backend): add collab context read methods speaking domain types`

### Task 3: products 写用例 + 架构测试

**Files:**
- Create: `src/…/context_collab/products/service_api.py`、`src/…/products/bbs_public_discussion/__init__.py`、`src/…/products/bbs_public_discussion/service.py`
- Modify: `src/agentclaw/community/di/modules/task_module.py`（产品协议 singleton 绑定）
- Test: `tests/community/core/task/context_collab/products/test_products_layering.py`（新）、`tests/community/core/task/context_collab/products/test_bbs_public_discussion_write.py`（新）、`tests/community/di/test_task_module.py`（追加）

**Interfaces:**
- Produces（T4/T5 消费，签名逐字）：

```python
# products/service_api.py
@dataclass
class CreateDiscussionCommand:
    title: str
    body: str
    space: SpaceRef
    resource_refs: list[ResourceRef] = field(default_factory=list)

@dataclass
class ReplyDiscussionCommand:
    collab_context_id: str
    content: str
    parent_event_id: str | None = None

@dataclass
class DiscussionCreatedView:
    collet_id: str; title: str; body_event_id: str; context_version: int

@dataclass
class ReplyCreatedView:
    collab_context_id: str; event_id: str; parent_event_id: str | None; context_version: int

@runtime_checkable
class BbsPublicDiscussionServiceProtocol(Protocol):
    def create_discussion(self, command: CreateDiscussionCommand, context: ActorContext) -> DiscussionCreatedView: ...
    def reply_discussion(self, command: ReplyDiscussionCommand, context: ActorContext) -> ReplyCreatedView: ...
    def list_discussions(self) -> list["DiscussionSummaryView"]: ...        # T4 实现
    def get_discussion(self, collab_context_id: str) -> "DiscussionDetailView": ...  # T4 实现

# service.py 编排(spec §3.3 权威转写):
#   @inject(collab_repo: ContextCollabRepositoryProtocol,
#           authorizer: ContextCollabAuthorizerProtocol,
#           method_store: MethodStoreProtocol)
#   私有 _PublicMessageIntent(event_kind, primitive_type, actor_ref, parent_event,
#                              visibility, payload) 喂 G1(duck-typed);
#   回帖 snapshot_updater=恒等(裁决 J);expected_version=record.collet.version;
#   发帖正文 expected_version=1;creation_payload() 出初始事件 payload。
```
- Consumes: T1 `ActorContext`/`creation_payload`；既有 `project_created_context`/`current_axes`。
- 架构测试白名单（与 spec §3.1 逐字同表）；含故意探针：临时脚本断言"违规 import 会红"后删探针（自证测试有效）。

- [ ] **Step 1: 失败测试** `test_bbs_public_discussion_write.py`——夹具：真 `InMemoryMethodStore` + 内存假仓储（沿 `test_context_collab_service.py` 桩模式）+ `StructuralContextCollabAuthorizer`：
  `test_create_discussion_wires_full_creation_facts`（Collet objective==title、初始事件 payload 六键、正文事件落库、L5 顶层 binding 建立、视图四字段）、`test_reply_baseline_and_nested`（None parent=回主题；parent 指正文事件=楼中楼）、`test_reply_rejects_skeleton_event_parent`、`test_reply_rejects_foreign_profile_collet`、`test_reply_unknown_collet_not_found`、`test_reply_version_conflict_propagates`、`test_reply_allows_stranger_authenticated_actor`。
- [ ] **Step 2: 失败确认（ImportError）** → **Step 3: 实现三文件 + DI 绑定** → **Step 4: 跑绿**：write 测试 + 架构测试产品目录全绿 + `tests/community/di/test_task_module.py -v`（追加产品协议 singleton 用例）零回归。
- [ ] **Step 5: Commit** `feat(backend): add BBS public discussion write use cases under products layer`

### Task 4: products 读用例 + views + DI 测试

**Files:**
- Create: `src/…/products/bbs_public_discussion/views.py`
- Modify: `src/…/products/bbs_public_discussion/service.py`（两读方法）
- Test: `tests/community/core/task/context_collab/products/test_bbs_public_discussion_read.py`（新）

**Interfaces:**
- Produces：

```python
# views.py
@dataclass
class DiscussionSummaryView:
    collet_id: str; title: str; initiator_id: str; message_count: int; created_at: datetime

@dataclass
class DiscussionDetailBody:
    event_id: str; content: str; author_id: str; occurred_at: datetime

@dataclass
class DiscussionReplyItem:
    event_id: str; content: str; author_id: str; parent_event_id: str | None; occurred_at: datetime

@dataclass
class DiscussionDetailView:
    collet_id: str; title: str; state: str  # ColletState.value
    body: DiscussionDetailBody | None      # 孤儿帖 None(合法领域态)
    replies: list[DiscussionReplyItem]
```
- 投影规则（spec §3.3 权威转写）：标题=初始事件（`events[0]` 且 `event_kind=="task.created"`）payload["objective"]，NULL-payload 回退伴随 `context_spec.objective`；`author_id`/`initiator_id` 取 `PrincipalRef.principal_id`；正文=首条 `user_interaction.posted`+`Message` 事件；replies=其余 Message、**只投影 public**（NULL visibility 按 private）、表插入序；外 profile → NotFound。

- [ ] **Step 1: 失败测试**：`test_lists_discussions_with_message_count_and_order`、`test_get_discussion_assembles_title_body_and_replies`（含楼中楼 parent 链）、`test_detail_title_reads_initial_event_with_legacy_fallback`（正常行 title 来自事件；NULL-payload 行回退伴随 spec）、`test_detail_only_projects_public_messages`、`test_detail_tolerates_bodyless_collet`、`test_detail_rejects_foreign_profile`、`test_get_discussion_unknown_not_found`。
- [ ] **Step 2: 失败确认** → **Step 3: 实现 views.py + service 读方法** → **Step 4: 全绿** + products+DI 全回归。
- [ ] **Step 5: Commit** `feat(backend): add BBS public discussion read use cases`

### Task 5: api 协议再导出 + 收尾回归

**Files:**
- Create: `src/agentclaw/community/api/task/bbs_public_discussion_service.py`（协议再导出，沿 `api/task/context_collab_service.py` 先例一行件）
- Test: 全目录回归

- [ ] **Step 1:** 再导出文件（`from …products.service_api import BbsPublicDiscussionServiceProtocol`，注释注明 P1 模块级交付面、HTTP 后置 spec §5）。
- [ ] **Step 2:** 全目录回归 `DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/repository/task/context_collab/ tests/community/di/test_task_module.py -v` 整段全绿（≥819 基线 + 本计划增量）。
- [ ] **Step 3: Commit** `feat(backend): expose BBS public discussion service at module level`

---

## Self-Review 结论

- Spec 覆盖：§2 域映射→T3/T4；§2a 操作族映射为 spec 层治理（无独立实现动作,T3 编排逐用例对应四族）；§3.1 依赖矩阵→T3 架构测试；§3.2 归位二连→T1；§3.3 四用例→T3/T4；§3.4 DI→T3；§4 两读方法→T2；§5 模块级交付面→T5（api 协议再导出）,HTTP 后置为非目标；§6 A–J 全数落在接口块与嵌注（A/F→T4、B=HTTP 后置、C/D/E→文本裁决、G→T1 消最大面+T3 编辑距离、H→T3 白名单、I→T1、J→T3 恒等）；§7 测试表 1:1 分解到各 Task Step 1。
- 类型一致：`ActorContext`/`creation_payload`（T1 产,T3 消）、`list_collets_by_product`/`count_message_events`（T2 产,T4 消）、产品协议/视图（T3/T4 产,T5 消）签名逐字；v1 的 `ContextSummaryRecord` 作废（零发明 record,spec §4）。
- Review Focus 7 项全有归属测试（骨架 parent/外 product→T3；泄漏/孤儿/NO-title 回退→T4；坏行→T2；CAS→T3；依赖违约→T3）。
- 比例：6 任务、4 个新源文件 + 3 个端到端测试文件 + 2 架构小步，接口与断言为主、零方法体转写。
