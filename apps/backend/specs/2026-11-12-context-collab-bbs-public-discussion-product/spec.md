# Context Collab BBS 公开讨论产品层（发帖 / 回帖 / 列表 / 详情）

- 日期：2026-11-12（2026-11-13 修订 v2：乙案依赖收敛）
- 状态：Accepted（v2 全演绎经作者确认：产品只组合协议层 + 三处基建端口白名单，`service_api.py` 对产品永久出局）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`（products/）
- 目标消费者：products/bbs_public_discussion 服务、HTTP delivery 新路由、context-collab tests
- 关联设计：
  - [Context collaboration protocol](../2026-11-09-bbs-context-collaboration-protocol/spec.md)
  - [L6 BBS product forms](../2026-11-12-context-collab-l6-product-forms/spec.md)（profile 值、G1 公开 Message 窄规则、G4 无状态机裁决）
  - [L4 Collet ACCS 对齐](../2026-11-12-context-collab-l4-collet-accs-alignment/spec.md)
  - [ResourceBinding 事件承载](../2026-11-12-context-collab-l4-resource-binding/spec.md)、[创建事实全量入流](../2026-11-12-context-collab-l4-creation-event-facts/spec.md)
- 协议依据：ACCS-001 / ACCS-104 §2–§7；ACCS-105 §2；ACCS-106 §2

**v2 修订内容**（推翻 v1 的".Query 协议入 service_api"与"产品直注仓储"设计）：
产品模块的 import 面**只许是 ACCS 层模块与三处基建端口**；通用三北向的应用合同（`service_api.py`）不再是产品的奠基石，产品**自持编排**直接组合 L4 对象、G1/G4 与仓储端口。

## 1. 目标与范围

在 context_collab 层之上**重实现** BBS"公开讨论"产品能力（发帖、回帖、列表、详情），作为 ACCS 层蛋糕足以组合出产品的第一个实体证明。**不动** `core/task/forum` 现有业务、不动两张协议权威表结构、不动既有三个北向 wire 路由。

**范围内**：乙案依赖矩阵（§3）与架构测试；平台归位二连（`ActorContext` 迁 G1、`creation_payload` 纯函数迁 L4）；产品四用例自持编排；仓储两个域类型读方法（§4）；模块级 service 接口（产品协议 + DI singleton）。

**非目标（后置留档）**：
- **HTTP/`OpenAPI` 对外交付整体后置**（作者裁决 2026-11-13：P1 只提供模块级 service 接口，对外 wire 以后再议）；
- 结帖接口（`Context.complete`）；阅读面细化（楼层号、树形、分页）；通知；产品编排与通用服务的重复装配合并（§6-G）；`service_api` 自身退役讨论（wire 合同，P3+）。

## 2. 域映射（权威裁决）

| BBS 概念 | 协议对象 |
| --- | --- |
| 帖子（主题帖） | 一个 **Collet**（`collet_id` 即帖子 ID） |
| 帖子标题 | **初始事件 payload 的 `objective`**（读源合同新主，[创建事实 spec](../2026-11-12-context-collab-l4-creation-event-facts/) §3）；列表视图过渡期伴随读（`ContextSpec.objective`，P3 随过滤源升级） |
| 帖子正文 | **首帖正文事件**：`user_interaction.posted` / `Message` / `public`，payload=正文 |
| 回帖 | 一条 `user_interaction.posted` / `Message` / `public` 事件（payload=回帖内容） |
| 楼中楼 | 事件 `parent_event` 指针（指向被回复回帖的 event_id）；**回复主题帖 = `parent_event=None`**（与旧 forum `reply_to_post_id` 为空语义对齐） |
| 公开讨论 profile | `ProductProfile.BBS_PUBLIC_DISCUSSION`（L6 既有），`method_kind=Open + Group`，`executor_ref=None` |
| 帖子列表 | 该 profile 下的 Collet 集合（轻量投影） |

裁决理由：协议固化的 `task.created` 初始事件 payload 承载 objective——正文属"用户发言"，走 `user_interaction.posted`，不塞初始事件、不扩其 payload 合同；回帖即有 event_id 的持久事件记录，不升格 Collet（ACCS-104：上下文聚合根只到帖子一级）；不发明新幂等键（沿协议既有裁决）。

### 2a. 操作族映射表（ACCS-104 §3 一致性："语义效果、权限检查和事件证据必须可映射到操作族"）

| BBS 产品合同 | ACCS 操作族 | 权限检查 | 事件证据 |
| --- | --- | --- | --- |
| `create_discussion` | Context.**create** + Message.**publish**（首帖正文） | G1 `check_create` | 初始事件 + 首帖正文事件 |
| `reply_discussion` | Message.**publish**（含 reply 因果） | G1 `check_append`（窄规则放行 public Message） | 回帖事件 |
| `list_discussions` / `get_discussion` | Query.**search / filter / project** | 授权可见视图（本产品仅 public 投影） | 无副作用（Query 操作不产生事件） |
| （P2）结帖接口 | Context.**complete** | 发起者/executor 守卫（已裁决） | 已由 collet_lifecycle 持有 |

## 3. 产品层结构（乙案核心）

```text
context_collab/
  l6_products/profiles.py              # 保持原样：L6 词汇表
  products/
    __init__.py
    service_api.py                      # 产品自己的合同：命令/视图/协议（四用例）
    bbs_public_discussion/
      __init__.py
      service.py                        # 产品编排：层对象 + G1 + 仓储端口 + L5 投影
      views.py                          # 读投影（纯函数）
```

### 3.1 依赖矩阵（架构测试机械执法）

| import 允许（白名单） | 说明 |
| --- | --- |
| `…context_collab.l2_agent_network.*` | PrincipalRef / AgentRef |
| `…context_collab.l3_spaces_resources.*` | SpaceRef / ResourceRef |
| `…context_collab.l4_context_primitives.*` | ContextSpec / EventRecord / creation_payload 纯函数 |
| `…context_collab.l5_methods.*` | MethodKind |
| `…context_collab.l6_products.*` | ProductProfile / WorkflowKind |
| `…context_collab.g1_secure_govern.*` | StructuralContextCollabAuthorizer / **ActorContext（归位后）** |
| `…context_collab.g2/g3/g4_*` | （本产品当前仅用 G4 `validate_event_body`） |
| `…context_collab.errors` | 错误合同 |
| `…context_collab.persistence.repository` + `persistence.method_store` | **基建端口**：仓储协议与请求/记录类型、MethodStore 协议 |
| `…context_collab.services.method_lifecycle_service` | **基建端口**：L5 顶层投影 `project_created_context` / `current_axes` |

**永久禁止**：`service_api.py`、`services/context_collab_service.py`、`method_service_api.py`、`services/collet_lifecycle_service.py` 及 products 兄弟产品之外的任何非白名单模块。架构测试扫描 `src/…/context_collab/products/**/*.py` 的全部 import，白名单外即红。

### 3.2 平台归位二连（前置小步，零行为变化）

1. **`ActorContext` 归 G1**：迁至 `g1_secure_govern/actor_context.py`（语义槽位即"每个操作必须 G1 授权"的调用者上下文）；`service_api.py` 改为兼容再导出（既有 ~10 处 import 零破坏），同时修正"G1 层反向依赖应用合同"的方向错误。
2. **`creation_payload(spec, objective) -> dict` 纯函数归 L4**：新建 `l4_context_primitives/creation_facts.py`，通用服务与产品共用（初始事件 payload 全量装配唯一定义点，消除产品自持编排的漂移风险）。

### 3.3 四用例（产品自持编排，权威转写）

**发帖 `create_discussion(command, context)`**

1. title / body 非空校验；
2. 构建 `ContextSpec`（L4）：`space=command.space`、`principal_ref=context.actor`、`product_kind=BBS_PUBLIC_DISCUSSION`、`workflow_kind=BBS_PUBLIC_DISCUSSION`、`method_kind=MethodKind.OPEN_GROUP.value`、`executor_ref=None`、`objective=title`、`resource_refs=tuple(command.resource_refs)`；
3. G1 `check_create(spec, context)`；
4. `creation_payload(spec, command.title)` 装配初始事件 payload；`repo.create_context(CreateContextRecordRequest(context_spec=spec, initial_event=InitialEventDraft(event_kind="task.created", primitive_type="Task", actor_ref=context.actor, payload=…)))`——inline dict 构造文案与旧服务完全相同的零漂移；
5. L5 顶层投影 `project_created_context(method_store, current_axes(), collet_id, spec, context, now=…)`（store 写失败如实上抛）；
6. 首帖正文：`G4 validate_event_body` → G1 `check_append(collet, spec, intent, context)` → `repo.append_event(collet_id, AppendEventRecordRequest(expected_version=1, event_kind="user_interaction.posted", primitive_type="Message", actor_ref, parent_event=None, visibility=public, payload={"content": body}, snapshot_updater=恒等))`；
7. 结果：`DiscussionCreatedView{collet_id, title, body_event_id, context_version}`（无时间字段，时间属读投影）。

两步非原子：正文事件失败即整体报错，调用方重试建新帖（创建不幂等裁决）；窗口态容忍见 §6-D。

**回帖 `reply_discussion(command, context)`**

1. `repo.get_current_status(collet_id)`：None → NotFound；`record.context_spec.product_kind != bbs_public_discussion` → NotFound（跨产品面拦截）；
2. `parent_event_id` 非 None 时：目标事件不存在 → 由仓储把守；`primitive_type != "Message"` → ValidationError（骨架事件域断言——协议仓储只做存在性校验，产品必须自持本规则）；
3. G4 `validate_event_body` → G1 `check_append(collet, record.context_spec, intent, context)`（窄规则：任意已认证 actor 放行 public Message）；
4. `repo.append_event(collet_id, AppendEventRecordRequest(expected_version=record.collet.version, …同上, parent_event=command.parent_event_id, snapshot_updater=恒等))`——**恒等依据**：`bbs_public_discussion` 无 G4 状态机（L6 spec 裁决），Message 事件不改 Task status，`expected_version` CAS 由仓储把守，冲突 409 透传；
5. 结果：`ReplyCreatedView{collab_context_id, event_id, parent_event_id, context_version}`。

G1 intent 载体：产品内部私有 dataclass `_PublicMessageIntent(event_kind, primitive_type, actor_ref, parent_event, visibility, payload)`（duck-typed 喂 `check_append`/`_is_public_open_message`），不引 `EventAppendCommand`。

**列表 `list_discussions()`**

`repo.list_collets_by_product(...)` + `repo.count_message_events(...)`（§4 两方法）→ 组装 `DiscussionSummaryView{collet_id, title, initiator_id, message_count, created_at}`（标题过渡期伴随读自第二个元素，P3 随过滤源升级）。`message_count`=Message 事件总数含首帖正文。

**详情 `get_discussion(collet_id)`**

`repo.get_current_status` → NotFound/跨产品拦截 → 组装 `DiscussionDetailView`：`title`=初始事件（`events[0]` 且 `event_kind=="task.created"`）payload["objective"]，**NULL-payload 外部行回退**伴随 `context_spec.objective`（P3 撤列时移除）；`body`=事件流第一条 `user_interaction.posted`+`Message` 事件（payload["content"]），孤儿帖 `body=None`；`replies`=其余 Message 事件、**只投影 public**（visibility NULL 按 private 处理）、按表插入序、`parent_event` 指针原样给出（不做树形重塑）。

**产品编排的重复装配**：create/append 的装配步骤与通用服务存在约 30 行/流程的相似（§3.2 的 `creation_payload` 已消最大一处）；合并评估记 P2（§6-G）。

### 3.4 DI

`binder.bind(BbsPublicDiscussionServiceProtocol, to=BbsPublicDiscussionService, scope=singleton)`；服务构造 `@inject(collab_repo, authorizer: ContextCollabAuthorizerProtocol, method_store: MethodStoreProtocol)`——三依赖均在 DI 既有绑定/storage 之内，无新基建。

## 4. 仓储读路径（persistence 内部，只说领域类型）

不发明中间 record（v1 的 `ContextSummaryRecord` 作废——bespoke 载体病）：仓储端口新增两方法，返回值全部是领域类型/内建类型：

```python
def list_collets_by_product(
    self, product_kind: str
) -> list[tuple[Collet, ContextSpec, datetime]]
# 元组三元素：六字段聚合根、创建参数（过滤源+过渡期 title 伴随）、gmt_create。
# product_kind 过滤在 Python 侧（§6-E）；按 gmt_create 倒序；解码失败的
# 外部部分行跳过并 logger.warning（全页不炸）；单点读维持方案 i 严格失败。

def count_message_events(
    self, collab_context_ids: Sequence[str]
) -> dict[str, int]
# 单条 GROUP BY（primitive_type="Message"），键=collab_context_id；禁 N+1。
```

## 5. 模块级交付面（HTTP 后置）

P1 交付面 = **产品协议（`products/service_api.py` 的 `BbsPublicDiscussionServiceProtocol`）+ DI singleton**（§3.4），内部模块经 injector 消费；另照 `api/task/context_collab_service.py` 先例补一行协议再导出（`api/task/bbs_public_discussion_service.py`）。HTTP/`OpenAPI` 万向对外交付（含内部路由、镜像、NoCheck 行、endpoints 测试）整体后置，届时的路由骨架 v1 稿留档于 git 历史（`468eec4ea` 版 spec §5）。

## 6. 裁决表（作者确认；v2 增 G/H/I/J）

| # | 事项 | 裁决 |
| - | --- | --- |
| A | 列表计数 | `message_count`（Message 事件总数，含首帖正文） |
| B | HTTP 对外交付 | **后置**（2026-11-13 作者裁决：P1 只提供模块级 service 接口；HTTP/镜像路由 P2 再议，原"补镜像"裁决存档） |
| C | 结帖门 | P2 落结帖时补"终态拒回帖" |
| D | 发帖两步窗口态 | 调用方报错+重试建新帖；详情容忍 `body=None`（合法领域态） |
| E | 列表过滤 | Python 侧（表小、方言差异大）；上量后改 JSON path |
| F | 读可见性 | 只投影 public Message；workspace/private 事件经通用路由读写，不进本产品视图 |
| G | 产品与通用服务的重复编排 | 接受（≤~30 行/流程），P2 统一评估；`creation_payload` 已先消最大公共面 |
| H | 产品依赖 | **乙案**：白名单=层模块+errors+三处基建端口；`service_api.py` 永久出局；架构测试执法 |
| I | `ActorContext` 归位 | 迁 `g1_secure_govern/`，`service_api` 兼容再导出（既有 import 零破坏） |
| J | 快照推进（回帖） | 恒等 updater——公开讨论无 G4 状态机，Message 不动 Task status |

**兼容与边界**：`ac_forum_*` 零接触；协议两表/列/迁移与三个既有 wire 路由零改动；错误合同沿既有三类；文件 ≤1000 行；`T | None` 仅合法领域可空（parent_event、孤儿帖 body、parent_event_id 入参）。

## 7. 测试策略

| 文件 | 覆盖 |
| --- | --- |
| `tests/…/context_collab/products/test_products_layering.py`（新） | **架构测试**：products 全量 import 扫描，白名单外红（含"service_api 出局"钉死） |
| `tests/…/products/test_bbs_public_discussion_write.py`（新） | 发帖两步串联（payload 全量材料）、回帖 G1 窄规则陌生 actor 放行、**task.created 骨架不可当 parent**、外 profile 404、not-found、CAS 409 |
| `tests/…/products/test_bbs_public_discussion_read.py`（新） | 列表倒序+message_count、详情 title=初始事件 objective + **NULL-payload 回退**、public-only 投影、孤儿帖 body=None、外 profile 404 |
| `tests/community/repository/task/context_collab/`（追加） | 真 SQLite：两读方法按 profile 过滤/计数/倒序/坏行跳过 |
| DI 测试（追加） | 产品协议 singleton 解析、作者器/仓储/store 共享注入 |

按需只跑 `context_collab` 目录 + products + repository + DI；CI 门禁延后统一处理。endpoints 面无改动（P1 不上 wire）。
