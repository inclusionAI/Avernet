# Context Collab BBS 公开讨论产品层（发帖 / 回帖 / 列表 / 详情）

- 日期：2026-11-12
- 状态：Accepted（设计 + §6 全部裁决经作者确认 2026-11-12）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`（products/）
- 目标消费者：products/bbs_public_discussion 服务、HTTP delivery 新路由、context-collab tests
- 关联设计：
  - [Context collaboration protocol](../../2026-11-09-bbs-context-collaboration-protocol/spec.md)
  - [L6 BBS product forms](../2026-11-12-context-collab-l6-product-forms/spec.md)（profile 值、G1 公开 Message 窄规则、G4 无状态机裁决）
  - [L4 Collet ACCS 对齐](../2026-11-12-context-collab-l4-collet-accs-alignment/spec.md)（聚合根六字段、record 伴随字段）
- 协议依据：ACCS-001 / ACCS-104（L4 Collet 聚合与 Context 操作族；P1 不上 `create/activate/…` 生命周期 wire）

## 1. 目标与范围

在 context_collab 协议原语之上**重实现** BBS"公开讨论"产品能力（发帖、回帖、列表查询、帖子详情），供后续 Bot/用户协作场景消费。**不动** `core/task/forum` 现有业务一行代码——旧 BBS 照常运行，本设计是平行新实现，两边不互通、不加表间引用。

**范围内**：产品层四个用例（发帖/回帖/列表/详情）、一张仓储列表读路径、DI 绑定、HTTP 路由（北向）、测试。

**非目标（P2 留档）**：
- 结帖接口（`ColletLifecycleService.complete`——服务已在，缺产品化与"终态后拒绝回帖"语义闭环）；
- 阅读面细化（楼层号、树形重塑、分页 cursor、热度/计数投影）；
- 通知（旧域 `NotifyingForumService` 语义不搬运）；
- 与 `ac_forum_*` 旧数据的互通或迁移。

## 2. 域映射（权威裁决，实现不需再问）

| BBS 概念 | 协议对象 |
| --- | --- |
| 帖子（主题帖） | 一个 **Collet**（`collet_id` 即帖子 ID） |
| 帖子标题 | **初始事件 payload 的 `objective`**（读源合同新主，[创建事实 spec](../2026-11-12-context-collab-l4-creation-event-facts/) §3）；列表视图过渡期伴随读（`ContextSpec.objective`，P3 随过滤源升级） |
| 帖子正文 | **首帖正文事件**：`user_interaction.posted` / `Message` / `public`，payload=正文 |
| 回帖 | 一条 `user_interaction.posted` / `Message` / `public` 事件（payload=回帖内容） |
| 楼中楼 | 事件 `parent_event` 指针（指向被回复回帖的 event_id）；**回复主题帖 = `parent_event=None`**（与旧 forum `reply_to_post_id` 为空语义对齐，读投影把 None 解为顶层回帖，首正文事件不参与回复树） |
| 公开讨论 profile | `ProductProfile.BBS_PUBLIC_DISCUSSION`（L6 既有），`method_kind=Open + Group`，`executor_ref=None` |
| 帖子列表 | 该 profile 下的 Collet 集合（轻量记录） |

裁决理由（记录，可评审推翻）：
1. 协议固化的 `task.created` 初始事件 payload 只承载 objective——正文属"用户发言"，走 `user_interaction.posted`，不塞初始事件、不扩 payload 合同；
2. 回帖即事件记录（有 event_id、actor、payload、parent、created_at），持久可寻址，产品意义上就是"回复的帖子"，但不升格 Collet（ACCS-104：上下文聚合根只到帖子一级）；
3. 不发明新幂等键（沿协议既有裁决：创建不设防重号；重复回帖即重复事件）；附录 A 列出现有幂等语义给评审对照。

## 3. 产品层结构

```text
context_collab/
  products/
    __init__.py
    service_api.py                      # 产品层协议 + 命令/结果 dataclass
    bbs_public_discussion/
      __init__.py
      service.py                        # 编排用例，DI singleton
      views.py                          # 列表/详情读投影组装（纯函数）
```

- `l6_products/profiles.py` 保持原样（L6 只是词汇表）；products 是其上的产品实现层。
- `service.py` 依赖注入 `ContextCollabServiceProtocol`（写路径）与 `ContextCollabRepositoryProtocol`（列表读路径），不直连 DB、不旁路 G1。

### 3.1 四个用例

**发帖 `create_discussion(command, actor)`**

1. 构造 `ContextCreateCommand`：`objective=command.title`、`product_kind=bbs_public_discussion`、`workflow_kind=bbs_public_discussion`、`method_kind="Open + Group"`、`executor_ref=None`、principal=actor、space=command.space、resource_refs=command.resource_refs（透传）；
2. G1 `check_create`（既有结构授权，未有额外规则）；调 `create_context`；
3. 紧随 `append_event` 首帖正文（`user_interaction.posted`/`Message`/`public`，actor_ref=发帖人，parent=None，payload={"content": command.body}）；
4. 结果：`DiscussionCreatedView{collet_id, title(=objective), body_event_id, context_version}`（写路径结果只取协议原生字段，不带时间——时间在事件 envelope 的 `occurred_at`，属读投影面）；
5. 两步非原子（无跨聚合事务）：步骤 3 失败即整体向调用方报错，调用方重试会创建新帖——与协议"创建不设幂等键"裁决一致；窗口态风险与取舍见 §6 待裁决 D。

**回帖 `reply_discussion(command, actor)`**

1. `get_current_status(collet_id)`：不存在 → `ContextCollabNotFoundError`；读 record 伴随 `context_spec`，确认 `product_kind=bbs_public_discussion`（非本产品帖拒绝，404 语义——避免产品层语义漂到别人的 Collet 上）；
2. `parent_event_id`（可空=回复主题帖）透传前做**产品域断言**（非授权、不违反 G1 裁决）：所指事件必须 `primitive_type=Message`——协议骨架事件（`task.created`/Task）在流内且能通过仓储存在性校验，但把回帖挂到它上面会让详情投影把骨架事件吸进回复树，拒绝；仓储同流存在性校验照旧（仓储已做，不重做）；
3. 调 `append_event`（`expected_version` 由产品层从 record 读出后透传，沿用协议乐观并发）；G1 既有窄规则（Open+Group + public Message → 任意已认证 actor 放行）即授权——产品层不加新授权；
4. 结果：`ReplyCreatedView{collab_context_id, event_id, parent_event_id, context_version}`（楼层号/时间是读投影概念，不在写路径返回值里）。

**列表 `list_discussions()`**

1. 仓储新读路径 `list_collab_contexts(product_kind)`（§4）：回全部 `bbs_public_discussion` Collet，按创建时间倒序；
2. 组装 `DiscussionSummaryView{collet_id, title(objective), initiator_id, message_count, created_at}`——计数字段语义是**发言数**（该 Collet 全部 Message 事件数，含首帖正文）：一次按 `primitive_type='Message'` 的 GROUP BY 即得，免逐帖正文识别；若要"纯回帖数"前端减 1 即可。

**详情 `get_discussion(collet_id)`**

1. `get_current_status`：不存在/非本 profile → `ContextCollabNotFoundError`；
2. 组装 `DiscussionDetailView`：`{collet_id, title=初始事件 payload["objective"]、NULL-payload 外部行回退伴随 context_spec.objective（P3 撤列时移除）, body: {event_id, content, author, created_at}(首帖正文事件), replies: [{event_id, content, author, parent_event_id, created_at}, …](扁平有序、不做树形重塑)}`；
3. 首帖正文事件的识别：该 Collet 事件流中第一条 primitive_type=Message 的 user_interaction.posted 事件（协议初始事件是 `task.created`/Task，不冲突）；**未经裁决 D 接受窗口态**：正文事件可能不存在（发帖两步的第二步失败且未重试），此时 `body=None` 回 `T | None`（合法领域态：发帖窗口孤儿行），不 422；
4. 详情只投影 `user_interaction.posted` / Message 事件为回帖列表；同流的私有/工作区事件不在公开讨论产品视图展开（可见性语义待作者裁决 F，本条为默认形态）。

## 4. 仓储列表读路径（唯一协议扩展点）

`ContextCollabRepositoryProtocol` P1 只有 create/append/get 三法。新增：

```python
@dataclass
class ContextSummaryRecord:
    collet: Collet                      # 六字段聚合根（L4 对齐已定形）
    context_spec: ContextSpec           # 伴随字段（同 Task 2 record 风格）：列表过滤源(product_kind) + 过渡期 title 伴随读（[创建事实 spec](../2026-11-12-context-collab-l4-creation-event-facts/) §3 裁决 2）
    created_at: str                     # gmt_create 列（_base_axes mixin，非 created_at）

def list_collab_contexts_by_product(
    self, product_kind: str
) -> list[ContextSummaryRecord]
```

- 实现：直查 `ac_context_collab_collet`（`context_spec` JSON 含 product_kind）——`ac_*` 表加列不动，SQL 查询取该 JSON 字段（传行到手 + Python 侧过滤，性能只在 P1 全量规模可接受，详情见 §6 风险表）；
- 创建时间直取表列：mixin 轴列名为 `gmt_create`（`_base_axes()`），实现时取该列，无需从事件侧推导；
- bulk 读容错（产品裁决，builder check Q7）：列表对**解码失败行**（外部 NULL `actor_ref` 部分行）跳过并记日志，不出现在列表里；单点 `get_current_status` / `get_discussion` 维持方案 i 严格失败，不松动。
- 该方法只读，凡实现 protocol 者同步补签名；conformance 由 DI 单测兜底；
- 不动 `ac_context_collab_collet` 表结构与既有迁移。

## 5. HTTP delivery（北向新路由）

 HTTP 层实现完全对齐既有 `context_collab.py` 三路由的风格与绑定惯例（actor 来源、admission、错误映射），本节只钉路由映射骨架：

| 操作 | 建议路由 | 备注 |
| --- | --- | --- |
| 发帖 | `POST /api/v1/collaboration/task/context_collab/bbs/discussions` | body: `title`, `body(内容)`, `space` … |
| 回帖 | `POST .../discussions/{collet_id}/replies` | body: `content`, `parent_event_id?` |
| 列表 | `GET .../discussions` | P1 全量，无分页 |
| 详情 | `GET .../discussions/{collet_id}` | 一次给齐标题/正文/全部回复 |

- Actor 构造沿既有路由惯例（`user.staffId`）；不改三个既有 `context_collab` 路由；
- OpenAPI 镜像路由是否同步补——**待裁决 B**（我倾向补，旧业务两侧都有镜像）。

## 6. 待裁决表 / 风险表

| # | 事项 | 裁决（作者确认 2026-11-12） |
| - | --- | --- |
| A | 列表带计数 | **带**：字段 `message_count`（Message 事件总数，含首帖正文）——一次 GROUP BY 聚合即得，免逐帖正文识别；列表视图字段从此由本裁决定名 |
| B | OpenAPI(`/openapi/v1`) 镜像路由 | **一并补**，两侧对称（与旧 forum、既有 context_collab 三路由同结构） |
| C | 回帖对"结帖后 Collet"暂不加门 | **接受**：结帖接口在 P2，本阶段不存在可结帖的调用方；P2 落结帖时补"终态拒回帖" |
| D | 标题已建、正文未落的窗口态 | **接受**：调用方报错 + 重试建新帖；详情容忍 `body=None`（合法领域态） |
| E | 列表过滤放 SQL 还是 Python 侧 | **先 Python 侧**（表小、方言差异大）；上量后再改 JSON path 索引 |
| F | 详情/列表可见性 | **只投影 public Message**：公开讨论产品面只认公开言论；`workspace`/`private` 事件经通用协议路由读写，不进本产品视图 |

**兼容与边界**：
- `ac_forum_*` 与本实现零引用、零迁移；旧 BBS 行为与表全量保留；
- 两张协议表、列名、非本 spec 的 wire 不动；
- 错误合同沿 `ContextCollabNotFoundError` / `ContextCollabValidationError` / `ContextCollabVersionConflict`（409 透传 expected_version 语义）；
- 文件 ≤1000 行；`T | None` 只用在合法领域可空处（parent_event、计数缺失态见 A 裁决）。

## 7. 测试策略

| 文件 | 覆盖 |
| --- | --- |
| `tests/.../context_collab/products/test_bbs_public_discussion_service.py`（新建） | 四用例正反例：发帖两步串联、回帖 G1 窄规则陌生 actor 放行 / **task.created 骨架事件不可当 parent（产品域断言）** / expected_version 冲突 409、profile 不匹配拒绝、not-found、详情 body=None 容忍 |
| `tests/.../test_l4_primitives.py` / DI 测试（更新） | `list_collab_contexts_by_product` 仓储实现 + DI 单例绑定 |
| `tests/community/repository/task/context_collab/`（更新） | 真 SQLite 列表查询往返、JSON 过滤正确性 |

按需只跑 `context_collab` 目录 + DI + endpoints；CI 门禁延后统一处理。

## 8. 附录 A：现有协议语义（搬运供评审对照，非本 spec 新裁决）

- 创建不设幂等键（spec 作者裁决原话：调用方无需传入防重号）；
- append 不设幂等键：重复提交即重复事件；
- `expected_version` 乐观并发：不匹配 → `ContextCollabVersionConflict`（HTTP 409 语义）；
- G1 窄规则：`method=Open + Group` + `event_kind=user_interaction.posted` + `primitive=Message` + `visibility=public` → 任意已认证 actor 放行；其余写入仍要求 principal/executor。
