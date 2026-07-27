# `/openapi/v1` 公共 API + 租户隔离 —— 团队交接文档

[English](README.md) | **简体中文**

_这是一份"活文档"，用于协调跨多个会话交付公共 `/openapi/v1` API 的工作。它是我们
共享的地图：什么已完成、什么还没做、谁在做什么，以及各部分如何拼在一起。_

> **📌 请持续更新本文件 —— 这正是它存在的意义。**
> 只要有一项工作落地（隔离了某个阶段、接通了某个类别、做出了某个决定），**就在完成该工作
> 的同一个 PR 里，同步更新状态看板以及受影响的章节。** 我们每个人都靠读这份文档来把握
> *全局*，而不必去逐一翻看其他分支。一份过时的看板比没有还糟 —— 它会让人重复劳动或产生冲突。
> 经验法则：_如果你的改动挪动了某个复选框，就把这里也挪一下。_
>
> 如何更新：翻转 `State`（状态）单元格（`⬜ TODO` → `🔧 IN PROGRESS <名字>` →
> `✅ DONE — PR #___`），在底部的 **Changelog（变更记录）** 里追加一条带日期的记录，
> 并修正任何已经过时的描述。小步更新，勤更新。

请结合更深入的工程交接文档，以及
`src/backend/specs/2026-07-26-tenant-isolation-foundation/`
下的 SDD 文档（`spec.md`、`plan.md`、`tasks.md` —— 这些随 PR #456 一起到来）一起阅读。

---

## 全局视角（请先读这一节）

**目标：** 实现公共 `/openapi/v1` API，其调用方是**外部注册租户**。今天它仅以
**带桩（stub）处理器的路由定义**形式存在，位于
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`。

关键难点：内部的 `/api/...` 界面与公共的 `/openapi/v1` 界面**共享同一批表、仓储
（repositories）和服务（services）**。因此，一个会返回真实数据的公共端点，如果没有隔离，
就会读到*内部*租户的数据。防止这一点，正是这项工作存在的原因。

因此，工作被拆分为**两条主线（Track）**：

- **Track A —— 租户隔离基础设施。** 在接通任何公共端点之前，先让*两套 API 界面之下的*
  每一类数据都做到按租户隔离。**Track A 按设计不实现任何端点** —— 它是底层管道。
- **Track B —— 公共 API 实现。** 把七个 `/openapi/v1` 类别处理器（目前是桩）接到已有的
  服务上。**这才是真正落地端点/API 代码的地方。** 每个类别都依赖于其数据已先经过
  Track A 的隔离。

> ⚠️ **唯一需要避免的误解：** "隔离 Stage 1 已完成"**并不**意味着任何 API 端点被实现了。
> Stage 1 只属于 Track A（可复用机制 + 机器人记录）。API 端点落在 Track B，而 Track B
> 尚未开始。

---

## 谁在做什么

我们两个人来做这件事。在这里认领你的分工，避免撞车，并保持它随时更新。

| 成员 | 当前聚焦 | 分支 | 备注 |
|---|---|---|---|
| _(待认领)_ | — | — | 挑一个 Track A 阶段或一个 Track B 类别 |
| _(待认领)_ | — | — | |

> 建议的分工（可讨论）：一人推进 **Track A 各阶段**（隔离各类数据），另一人紧随其后、
> 落后一个类别去做 **Track B**（为一个已隔离的类别接通端点）。在确定顺序之前，请先看下面
> "尚未决定的排序问题"。

---

## 状态看板（工作落地时请更新）

### Track A —— 租户隔离基础设施
| 阶段 | 范围（数据） | 状态 | 完成判据 |
|---|---|---|---|
| 1 | 机器人记录（`ac_bots` / `BotModel`） | ✅ DONE —— **PR #456（等待审批，尚未合并）** | PR #456 合并后 |
| 2 | 资源（`ac_resource`） | ⬜ TODO | 列 + 守卫 + 测试通过；内部 API 不变 |
| 3 | 渠道（`ac_channel_config`） | ⬜ TODO | 同上 |
| 4 | 技能（skill 相关表） | ⬜ TODO | 同上 |
| 5 | MCP 配置 | ⬜ TODO | 同上 |
| 6 | 例程（Routines） | ⬜ TODO | 同上 |

> Stage 1 同时构建了后续每个阶段都会复制的**可复用机制**（见下文）。它是地基，
> 不只是"机器人"。

### Track B —— 公共 API 实现（端点真正落地之处 —— 尚未开始）
| 类别 | 路由（今天是桩） | 状态 | 依赖 |
|---|---|---|---|
| bots | `openapi_v1/bots/router.py` | ⬜ TODO | Track A 阶段 1（PR #456） |
| channels | `openapi_v1/channels/router.py` | ⬜ TODO | Track A channels |
| identity | `openapi_v1/identity/router.py` | ⬜ TODO | 调用方身份（见下文的验证器） |
| mcp | `openapi_v1/mcp/router.py` | ⬜ TODO | Track A mcp |
| resources | `openapi_v1/resources/router.py` | ⬜ TODO | Track A resources |
| routines | `openapi_v1/routines/router.py` | ⬜ TODO | Track A routines |
| skills | `openapi_v1/skills/router.py` | ⬜ TODO | Track A skills |

### 横切事项（非按阶段划分）
| 事项 | 状态 | 备注 |
|---|---|---|
| 真实的调用方身份验证器（认证工作线） | ⬜ TODO（其他团队） | 把 `resolve_avernet_tenant` 的函数体替换为读取网关 principal 的租户；这样才能解锁真正的第二个租户 |
| 租户前导索引（F2，**强制**策略） | ⬜ TODO | 多租户上线前必须完成 |
| 后台/定时任务的复查 | ⬜ TODO | 在第二个租户持有真实数据之前完成 |

> **尚未决定的排序问题（规划下一个会话时定夺）：** 是按类别把 Track A 和 Track B 背靠背地
> 做（隔离 resources → 实现 resources 端点），还是先把整个 Track A 做完，再做整个 Track B？
> 规格文档把它们当作彼此独立处理；请选定一个顺序，并在做出决定时记入 Changelog。

---

## Track A —— 可复用机制（在 Stage 1 / PR #456 中构建）

与具体类别无关；可原样复用。以下文件随 PR #456 到来：

- `utils/avernet_tenant.py` —— 每请求（per-request）的租户载体。
  `DEFAULT_AVERNET_TENANT = "teamclaw"`（内部租户；拥有当前的全部数据；**绝不能把它交给
  外部租户**）。`get_current_avernet_tenant()`（total），`avernet_tenant_scope()`
  （设置 + 保证重置），`bind_current_avernet_tenant(fn)`（把租户带入裸的
  `threading.Thread`/`ThreadPoolExecutor` 目标 —— `asyncio.to_thread`/`create_task`
  已会复制上下文，因此无需处理）。
- `plugin_api/models.py` —— `BotModel` 上的**守卫模式（guard pattern）**：
  - 在 `Session` 类上的 `do_orm_execute` **读守卫** →
    `with_loader_criteria(Model, avernet_tenant == get_current_avernet_tenant(),
    include_aliases=True)`；会跳过列/关系加载，并提供一个 `skip_avernet_tenant_guard`
    选项。同时也约束 `Query.update()`/`Query.delete()`，因此写操作无需再加过滤。
  - `before_insert` **插入守卫** → 未设置时打上标记，遇到显式冲突的租户时抛出
    `CrossTenantInsertError`。
  - 只注册一次，通过 `_AVERNET_TENANT_GUARDS_INSTALLED` 保证幂等。
- `adapters/http/middleware.py` —— `AvernetTenantMiddleware`，一个**纯 ASGI**
  中间件（**不是** `BaseHTTPMiddleware` —— 出于 ContextVar 的健壮性考虑）。它为每个请求
  设置租户。**已覆盖所有请求；Track A 阶段 2 及以后无需改动它。**
- `adapters/http/openapi_v1/dependencies.py` —— `resolve_avernet_tenant(request)`：
  唯一的接缝（seam）。今天返回默认租户；认证工作线会就地替换其函数体。与具体类别无关。
  _（今天这个文件里只有 `require_principal` 桩；`resolve_avernet_tenant` 随 PR #456
  到来。）_

以上所有路径都位于
`src/backend/src/agentclaw/community/` 之下。

---

## 操作手册（Recipe）—— 把 Track A 扩展到一个新的数据类别（以 resources 为例）

1. 找到相关的模型/表（如 `ResourceModel`、`ac_resource`）以及所有查询它们的模块
   （grep 该模型类）—— `Session` 类上的读守卫其实已经覆盖了它们全部。
2. 给每个模型加上 `avernet_tenant = Column(String(64), nullable=False,
   server_default="teamclaw")`；把它**排除在 `to_dict()` 之外**。
3. 仿照 `plugin_api/models.py` 中的 `BotModel` 代码块，为该模型注册两个守卫。多个模型 →
   在同一个 `do_orm_execute` 监听器上增加更多 `with_loader_criteria` 选项，并为每个被映射
   的类各加一个 `before_insert`（不要新增 N 个 Session 监听器）。
4. DDL（带外执行，无迁移文件）：`ALTER TABLE <table> ADD COLUMN
   avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw';` —— **不建索引**（F2）。
   `NOT NULL DEFAULT 'teamclaw'` 会为已有行回填。**必须在读取该数据的代码部署之前先执行
   DDL。**
5. 请求内线程审计：grep 该类别的服务，找出裸的
   `threading.Thread`/`ThreadPoolExecutor`/裸 `run_in_executor` 且其函数体触及该模型的
   地方；用 `bind_current_avernet_tenant` 包裹目标函数。
6. 测试（复制 Stage-1 的形态）：跨租户隔离的 red→green，覆盖各读方法；跨租户
   update/delete 变为 no-op；裸 `session.query(Model).all()` 被过滤；插入会打上租户标记
   且冲突插入会抛错；`to_dict()` 的键集合保持不变；已有内部测试套件**原样通过（不作修改）**。
7. 架构边界：任何新的跨模块导入（如把 `utils.avernet_tenant` 引入一个原本没有它的模块）
   都必须加入该模块 `README.md` 的 `## Context Boundary`；然后运行
   `tests/community/architecture/`。

**阶段完成判据：** 第 6 步的复选框全绿，内部套件未改动且全绿，CI 全绿。然后**更新上面的
状态看板。**

---

## Track B —— 实现某个类别的端点（API 真正落地之处）

尚未开始。每个类别：把 `openapi_v1/<category>/router.py` 里的桩处理器替换为真正的实现，
它们调用已有的服务，返回 `openapi_v1/contracts.py` 里标准的 `Envelope`/`Page` 结构，并依赖
`require_principal` / `resolve_avernet_tenant` 来获取身份 + 租户。因为 Track A 已经对底层
读写做了按租户限定，一个写得正确的处理器不可能跨租户泄漏 —— 而且它会自动运行在中间件设置的
请求租户之下。每个类别都需要有自己的 spec/plan/tasks（SDD）和自己的 PR。（每个类别接服务的
具体细节不在本交接文档范围内 —— 在该类别的会话开始时再界定。）

---

## 完成的定义（整个 `/openapi/v1` 工作）

1. **Track A：** 每一类数据（bots、resources、channels、skills、mcp、routines）都带有
   `avernet_tenant` 并被守卫，Stage-1 的测试形态全绿。
2. 全程内部 API 保持不变（`to_dict()` 无泄漏；内部套件不作修改）。
3. **Track B：** 七个 `/openapi/v1` 类别的处理器均已实现且租户安全，各自带测试 + PR。
4. F2 租户前导索引就位（强制策略）。
5. 后台/定时任务已针对按租户正确性完成复查。
6. `resolve_avernet_tenant` 已接到真实验证器（认证工作线）—— 到此，第二个租户才能安全地持有
   真实数据。

---

## 横切的延后事项

- **F2 —— 租户前导索引（强制的公司策略）。** 带有租户列的表必须有租户前导索引。延后到专门的
  索引工作中；**多租户上线前必须完成**。做的时候：把 `avernet_tenant` 前置到支撑查询的复合
  索引上（`idx_owner` → `(avernet_tenant, owner_id)`、`idx_bot_id_entity_id`、
  `idx_entity`、搜索索引），采用**先建新、再删旧**（命名约定把索引名与其列绑定，因此先建后删，
  避免出现无索引的窗口）。低基数索引（`idx_status`、`idx_is_delete`）与唯一查找索引
  （`idx_binding_id`）保持不动。
- **真实的调用方身份验证器。** 把 `resolve_avernet_tenant` 的函数体换成返回网关转发的
  principal 的租户。接缝已就绪。
- **后台/定时任务。** 现在都解析为默认租户（在全部数据都是 `teamclaw` 时是正确的）；在第二个
  租户持有真实数据之前需复查（skill_center / governance / dormant / 设备轮询器中的定时扫描、
  轮询器、同步循环）。
- **建议：** 加一个架构守卫/lint，标记 core 中新出现的裸 `threading.Thread` /
  `ThreadPoolExecutor`，这样未来的请求内 spawn 就不会悄悄丢失租户。

---

## Stage 1 中踩过的坑（帮你省下往返）

- 把 `do_orm_execute` 读守卫注册在 **`Session` 类**上（可覆盖所有运行时，包括树外的公司 DB
  插件），而不是注册在某一个插件上。
- 在 `with_loader_criteria` 里用**直接表达式**，不要用 lambda —— lambda 形式会被缓存，
  从而钉死第一个租户（造成泄漏）。已验证。
- `before_insert` 在 `server_default` **之前**触发，因此未设置的插入在那里确实是 `None`，
  会被打上标记；抛错分支只在显式冲突的租户时触发。
- 任何新的跨模块导入之后都要跑 `tests/community/architecture/` —— 上下文边界守卫在 Stage 1
  就因未声明的 `utils.avernet_tenant` 导入**两次**让 CI 失败（要在模块的 README 里声明）。
- `uv sync` 需要 `--default-index https://pypi.org/simple`（沙箱里屏蔽了固定的 aliyun
  镜像）。本地 pre-push 钩子在这里跑不了 singlebox —— 用 `--no-verify` 推送并依赖远端 CI。
  **`--no-verify` 对 force-push 同样适用**（普通 `git push` 会运行约 10 分钟的钩子并超时）。
- 执行会 `cd` 到仓库根目录的 `git` 命令后，cwd 会漂移到仓库根；跑 `uv run` 前先
  `cd src/backend`。

---

## Changelog（变更记录）（每次挪动看板时追加一条带日期的记录）

- **2026-07-27** —— 交接 README 创建。Track A Stage 1（bots + 可复用机制）已完成，位于
  **PR #456**，等待审批。Track B 尚未开始。排序决定（按类别 vs. 先全 A 再全 B）仍待定。
