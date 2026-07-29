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

请结合更深入的工程交接文档，以及 `src/backend/specs/` 下的 SDD 文档一起阅读 ——
`2026-07-26-tenant-isolation-foundation/`（Track A Stage 1，已随 PR #456 合并）
与 `2026-07-27-openapi-v1-bots-track-b/`（Track B bots，已随 PR #494 合并）。
两者各自都带有 `spec.md`、`plan.md`、`tasks.md`。

---

## 全局视角（请先读这一节）

**目标：** 实现公共 `/openapi/v1` API，其调用方是**外部注册租户**。它位于
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`。其中 **bots**
类别已经实现（PR #494）；其余六个仍然是**带桩（stub）处理器的路由定义**。

> 🔒 **这套界面目前还不可被真正调用 —— 这是设计如此。** `require_principal` 仍是
> 返回 `None` 的桩，因此任何真实请求打到 `/openapi/v1/...` 都会得到 `401` ——
> 已实现的 bots 端点也不例外。真实的调用方认证器属于另一条工作线，DoD 也把公共界面
> 的开放门槛压在它上面。"bots 做完了"指的是处理器、契约和测试做完了，**不是**指
> 外部租户已经可以调用它们。

关键难点：内部的 `/api/...` 界面与公共的 `/openapi/v1` 界面**共享同一批表、仓储
（repositories）和服务（services）**。因此，一个会返回真实数据的公共端点，如果没有隔离，
就会读到*内部*租户的数据。防止这一点，正是这项工作存在的原因。

因此，工作被拆分为**两条主线（Track）**：

- **Track A —— 租户隔离基础设施。** 在接通任何公共端点之前，先让*两套 API 界面之下的*
  每一类数据都做到按租户隔离。**Track A 按设计不实现任何端点** —— 它是底层管道。
- **Track B —— 公共 API 实现。** 把七个 `/openapi/v1` 类别处理器接到已有的服务上。
  **这才是真正落地端点/API 代码的地方。** 每个类别都依赖于其数据已先经过 Track A 的
  隔离。**七个里已完成一个：bots（PR #494）。**

> ⚠️ **唯一需要避免的误解：** "隔离 Stage N 已完成"**并不**意味着任何 API 端点被实现了。
> Track A 的每个阶段都只是底层管道（可复用机制 + 该类别的记录）。API 端点落在
> Track B —— bots 已完成，其余六个仍是桩。

---

## 谁在做什么

我们按**纵向切片（vertical slice）**分工：每个人端到端地负责若干数据类别 —— 既包括它的
**Track A** 隔离阶段，也包括它的 **Track B** 端点。这样一来，一个 Track B 类别就绝不会被
*另一个人*负责的 Track A 阶段卡住。（你举的 `mcp` 例子：`mcp` 的 Track B 依赖 `mcp` 的
Track A，所以两者都归同一个人。）

| 成员 | 负责（纵向切片） | Track A 阶段 | Track B 端点组 |
|---|---|---|---|
| **totalfrank** | bots、mcp、channels、**skills**（共担） | 1（bots ✅）、5（mcp）、3（channels）、4（skills，共担） | bots、mcp、channels、skills（共担） |
| **lucas-xzp** | resources、routines、identity、**skills**（共担） | 2（resources）、6（routines）、4（skills，共担） | resources、routines、identity、skills（共担） |

- **totalfrank** 同时负责**可复用的 Track A 机制**（在 Stage 1 / PR #456 中构建）—— 其余
  阶段都复制这套模式。
- **skills 由两人共担**（第三优先级，但也是最棘手的 —— 见其端点表里的说明）。把它的 Track A
  阶段和端点在两人之间分好，动手前先商定一份共同的子计划。
- **identity**（仅 Track B）没有自己独立的 Track A 阶段：它的数据是 bot 的子资源，因此已由
  **bots 隔离（Stage 1 ✅）** 覆盖。为均衡工作量分给 **lucas-xzp**；它唯一的依赖已经满足，
  因此不构成跨人阻塞。

### 优先级分层（先做哪些）

| 层级 | 类别 | 负责人 |
|---|---|---|
| **P1 —— 第一优先** | bots、mcp、resources、routines | bots + mcp → totalfrank；resources + routines → lucas-xzp |
| **P2 —— 第二优先** | channels、identity | channels → totalfrank；identity → lucas-xzp |
| **P3 —— 第三优先** | skills | **共担**（totalfrank + lucas-xzp）—— 最复杂的类别 |

在各自的分工里，先做 **P1** 切片，再做 P2，最后做 P3。skills（P3）是共担且最复杂的那个 ——
等 P1/P2 的工作跑起来后两人一起攻。

> **共同的闸口 —— 已解除（2026-07-27）。** 任何触及 bots 的工作原本都要等 **PR #456**
> 合并；它已经合并，这个一次性闸口不复存在。两位负责人现在可以各自并行推进自己的切片。

_具体每个切片要实现哪些端点，见下方的 **各组件端点清单**。_

---

## 状态看板（工作落地时请更新）

### Track A —— 租户隔离基础设施
| 阶段 | 范围（数据） | 负责人 | 优先级 | 状态 | 完成判据 |
|---|---|---|---|---|---|
| 1 | 机器人记录（`ac_bots` / `BotModel`） | totalfrank | P1 | ✅ **DONE —— PR #456 已于 2026-07-27 合并** | —— |
| 2 | 资源（`ac_resource`） | lucas-xzp | P1 | ⬜ TODO | 列 + 守卫 + 测试通过；内部 API 不变 |
| 3 | 渠道（`ac_channel_config`） | totalfrank | P2 | ⬜ TODO | 同上 |
| 4 | 技能（skill 相关表） | totalfrank + lucas-xzp | P3 | ⬜ TODO | 同上 |
| 5 | MCP 配置 | totalfrank | P1 | ⬜ TODO | 同上 |
| 6 | 例程（Routines） | lucas-xzp | P1 | ⬜ TODO | 同上 |

> Stage 1 同时构建了后续每个阶段都会复制的**可复用机制**（见下文）。它是地基，
> 不只是"机器人"。

### Track B —— 公共 API 实现（端点真正落地之处 —— 七个里已完成一个）
_按优先级分层排序。_
| 类别 | 负责人 | 优先级 | 路由 | 状态 | 依赖 |
|---|---|---|---|---|---|
| bots | totalfrank | P1 | `openapi_v1/bots/router.py` | ✅ **DONE —— PR #494 已于 2026-07-29 合并**（13/13 端点） | ~~Track A 阶段 1~~ ✅ |
| mcp | totalfrank | P1 | `openapi_v1/mcp/router.py` *(桩)* | ⬜ TODO | Track A mcp（totalfrank） |
| resources | lucas-xzp | P1 | `openapi_v1/resources/router.py` *(桩)* | ⬜ TODO | Track A resources（lucas-xzp） |
| routines | lucas-xzp | P1 | `openapi_v1/routines/router.py` *(桩)* | ⬜ TODO | Track A routines（lucas-xzp） |
| channels | totalfrank | P2 | `openapi_v1/channels/router.py` *(桩)* | ⬜ TODO | Track A channels（totalfrank） |
| identity | lucas-xzp | P2 | `openapi_v1/identity/router.py` *(桩)* | ⬜ TODO | bots 隔离（Stage 1 ✅） |
| skills | totalfrank + lucas-xzp | P3 | `openapi_v1/skills/router.py` *(桩)* | ⬜ TODO | Track A skills（共担） |

### 横切事项（非按阶段划分）
| 事项 | 状态 | 备注 |
|---|---|---|
| 真实的调用方身份验证器（认证工作线） | ⬜ TODO（其他团队） | 把 `require_principal` 与 `resolve_avernet_tenant` 的函数体替换为读取网关 principal；**在它落地之前，整个公共界面都只会返回 401** |
| 租户前导索引（F2，**强制**策略） | ⬜ TODO | 多租户上线前必须完成 |
| 后台/定时任务的复查 | ⬜ TODO | 在第二个租户持有真实数据之前完成 |
| **Agent 身份标识在租户之间会撞车**（[#556](https://github.com/inclusionAI/Avernet/issues/556)） | ⬜ TODO（totalfrank） | Passport、授权关系、BCN、策略行都只用 `bot_id`/`owner_id` 作键，没有租户维度，而每个 owner 的第一个 bot 的 id 就是字符串 `"default"`。**应当成为开启多租户的前置闸口。** #494 里以公共更新路径上的 `sync_to_bcn=False` 做了临时止血 |
| 异步创建出的 bot 可能不是被授权的那个（[#559](https://github.com/inclusionAI/Avernet/issues/559)） | ⬜ TODO（totalfrank） | pending 状态的创建规格从未被持久化，完成时是用轮询请求重建的。`dev` 上既有问题；当前潜伏（社区版 Passport 总是直接签发） |
| 外部身份写入失败被吞掉（[#560](https://github.com/inclusionAI/Avernet/issues/560)） | ⬜ TODO（totalfrank） | 创建时的 owner 授权写入、更新时的 Passport 元数据写入都是"记日志然后继续"，违反 `AGENTS.md:203-204`。一次决策同时覆盖两处；建议做法是*报告部分成功* |

> 上面三条来自 #494 的评审，都是 `dev` 上的**既有问题**而非本次引入的回归 —— 记在这里
> 是因为它们是整个工作都要继承的决策，而不是 bots 独有的 bug。其中 #556 尤其必须在
> 第二个租户持有真实数据之前定下来。

> **排序决定 —— 已定（2026-07-27）：** 采用按类别的**纵向切片**。每位负责人先隔离一个类别
> （Track A），紧接着就实现它的端点（Track B），而不是先把整个 Track A 全部做完再做
> Track B。这正是让我们两人彼此不阻塞的做法。

---

## Track A —— 可复用机制（在 Stage 1 / PR #456 中构建）

与具体类别无关；可原样复用。以下文件**已在 `dev` 上**（PR #456）：

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
  _（这个文件里现在是两个桩：`require_principal` 与 `resolve_avernet_tenant`；建立在
  前者之上的 owner 侧接缝是 `openapi_v1/principal.py::caller_owner_id`。）_

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

## Track B —— 可复用的公共 API 基建（随 bots 一起构建，PR #494）

**动手做任何一个类别之前，先读这一节。** bots 切片已经把公共 API 的共享层做了一遍；
其余六个类别应当**复用**它，而不是重造。以下内容都与具体类别无关，位于
`adapters/http/openapi_v1/`：

- **`responses.py`** —— 信封构造器（`envelope`、`page`、`created`、`accepted`、
  `deleted`）以及 `@envelope_errors` 装饰器。装饰器通过 `ENVELOPE_ERRORS` 这个
  `{异常类型: (HTTP 状态码, 固定文案)}` 字典把领域错误映射成信封响应。它强制的规则，
  你的类别都会继承：
  - **文案是固定的，绝不用 `str(exc)`** —— 内部标识符和内部语言的文本不能流到外部调用方。
  - **两条 404 路径逐字节相同**（"不存在" vs "存在但不属于你/属于其他租户"），
    这样调用方无法借此探测某个对象是否存在。
  - 顺序有意义：**具体的叶子错误必须列在其基类之前**；查找会按插入顺序在第一个
    `isinstance` 命中时返回。
  - 把*你自己*类别的错误加进 `ENVELOPE_ERRORS`。没有映射的异常会逃逸到应用级 500
    处理器 —— 那里现在也会套信封，但只能给出泛化文案。
- **`contracts.py`** —— `Envelope[T]` / `Page[T]` / `Deleted` / `NameCheck`，
  外加 `ErrorEnvelope` 与 `ERROR_RESPONSES`。`ERROR_RESPONSES` 在
  `openapi_v1/__init__.py::build_public_router()` 里**统一挂一次**，因此每个组的
  每条路由都会在生成的 schema 里描述真实的失败结构。这是白拿的；不要再逐个处理器声明。
- **`principal.py::caller_owner_id(principal)`** —— 把 `require_principal` 的返回值
  转成调用方 owner id 的唯一接缝。**每一次服务调用都要用它来限定范围。** 租户把数据
  限定在租户内，owner id 把数据限定在调用者自己 —— 两者缺一不可。
- **`clusters.py`** —— 公共 `cluster_name` 枚举（`ACRA` / `ANDC`），与引擎严格一一对应
  （`ANDC` ⟺ `teclaw`，`ACRA` ⟺ 其余全部）：读取时推导，创建时校验。如果你的类别也要
  暴露 cluster，请复用它，不要另造一套映射。
- **`errors.py`** —— 不带重依赖的公共错误类型（`MissingPrincipalError`、
  `ClusterMismatchError`、`UnsupportedEngineError`），让 schema / cluster 这些轻量模块
  可以直接抛错而不必导入服务层。
- **`PUBLIC_API_PREFIX`** 以及 `adapters/http/app.py` 里的应用级处理器 ——
  `RequestValidationError`、`DomainError`、`StarletteHTTPException` 和兜底的
  `Exception` 都按路径限定在公共前缀上，因此即使失败发生在处理器*之前*或*之外*
  （未知路径、方法不对、请求体校验失败），响应也仍然是信封。内部 `/api` 路由保持
  FastAPI 原本的 `{"detail": ...}`。**这是从结构上封死的 —— 每个类别都不需要再做一遍。**

### 操作手册（Recipe）—— 实现一个类别的端点

1. 先让该类别的 **Track A 阶段落地**（见上面的 Track A 手册）。没有它，即使处理器写得
   完全正确，读到的仍然是内部租户的数据。
2. 把 `openapi_v1/<category>/router.py` 里的桩处理器换成真正的实现，去调用已有的服务。
   依赖 `require_principal`，参数里带上 `request: Request`（`@envelope_errors`
   需要它来取 `request_id`），并且每一次调用都用 `caller_owner_id(principal)` 限定范围。
3. 用 `responses.py` 的构造器返回 `Envelope`/`Page`。二进制流（如资源下载）不走信封 ——
   这是唯一的例外。
4. 把你的领域错误加进 `ENVELOPE_ERRORS`，配上固定的对外文案。
5. 公共请求模型加 `extra="forbid"`。未知字段或不可变字段应当是 422，而不是被悄悄忽略 ——
   bot 更新时的 `engine` 就是这样被拒绝的。
6. **如果某个行为与内部 `/api` 界面共享，就把它抽到 `core/` 里让两边都调用**，不要复制。
   #494 对创建 + Passport 编排（`core/bot_management/create_flow.py`）和就绪判定
   （`core/bot_management/readiness.py`）就是这么做的 —— 否则两套界面会在一个版本之内
   就对同一个问题给出不同答案。
7. 测试：单元测试（响应构造器/错误映射）、端点测试（所有处理器，成功路径 + 每一条被映射的
   错误），以及**针对真实 Track A 守卫的跨租户隔离测试**（别的租户的 `{id}` 必须是被掩盖
   的 404）。内部测试套件保持不修改且全绿。
8. 每个类别有自己的 SDD（`spec.md`/`plan.md`/`tasks.md`）和自己的 PR。可以把
   `src/backend/specs/2026-07-27-openapi-v1-bots-track-b/` 和
   `openapi_v1/bots/router.py` 当作已经做过一遍的参考样板。

> **架构门禁：** `tests/community/architecture/` 现在还会跑
> `test_service_api_conformance.py` —— 这就是 `api/README.md` 在两处承诺过、但一直没有
> 写出来的 Service API 门禁。如果你给 `api/` 里的某个 Protocol 补上了真实签名，记得把它的
> `(Protocol, ConcreteService)` 组合注册进去。

---

## 各组件端点清单（每个切片需要实现哪些端点）

下面的表格就是 Track B 的**各组件端点清单** —— 谁负责、以及具体要落地哪些端点。**权威来源是
已服务的路由**（`openapi_v1/<category>/router.py` —— bots 已实现，其余仍是带着路由定义的
桩）；描述则与
**PR #363**（`docs/api-endpoints.zh-CN.md`，totalfrank 写的中文端点参考 —— 截至
2026-07-29 仍是 open/draft；此处作为参考保留）中的 v1 契约总览做了交叉核对。

> ⚠️ **路径分歧 —— 对其余六个桩组仍未对齐。** 路由把所有非 `bots` 的组都嵌套在
> `/openapi/v1/bots/...` 之下（如 `/openapi/v1/bots/resources`、`/openapi/v1/bots/mcp`）。
> 而 PR #363 的总览用的是**顶层**路径（`/openapi/v1/resources`、`/openapi/v1/mcp` 等）。
> 实现以**路由为准** —— 下面的路径与路由一致。负责人：如果顶层形态才是想要的对外形状，
> 请修改路由的 `prefix`，并在同一个 PR 里更新本节。_（bots 不受影响：两种读法下它都是
> `/openapi/v1/bots`，#494 也正是按这个形状上线的。）_
>
> **挂载顺序是有承重作用的。** `build_public_router()` 会先挂那六个字面量子组，再挂
> bots 组，这样 `/openapi/v1/bots/channels` 才能排在通配的
> `/openapi/v1/bots/{bot_id}` 前面被解析。新增的组要放进 `_SUBGROUPS` 列表里，位于
> bots 路由之前。

除注明外，所有响应都使用 `openapi_v1/contracts.py` 里的 `Envelope[T]` / `Page[T]` 结构
（二进制流不走信封）。

### ✅ totalfrank · P1 —— bots（13 个端点）· `openapi_v1/bots/router.py` —— **已实现（PR #494）**
13 个端点已全部接到内部 bot 服务上。这里保留下来，是作为其余六个类别的参照形态：
一个类别"做完了"长什么样。

| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| POST | `/openapi/v1/bots` | 创建 Agent；可能需要 Passport 授权 | `201 Envelope[Bot]` 或 `202 Envelope[BotAuthPending]` |
| GET | `/openapi/v1/bots` | 列出调用者的 Agent（`keyword`、`engine`、`status`、分页） | `Envelope[Page[Bot]]` |
| GET | `/openapi/v1/bots/check-name` | 重名检查（`name`） | `Envelope[NameCheck]` |
| GET | `/openapi/v1/bots/ceiling` | 创建配额上限 | `Envelope[Ceiling]` |
| GET | `/openapi/v1/bots/{bot_id}` | 获取详情 | `Envelope[Bot]` |
| PUT | `/openapi/v1/bots/{bot_id}` | 更新（`engine` 不可改） | `Envelope[Bot]` |
| DELETE | `/openapi/v1/bots/{bot_id}` | 删除 | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/{bot_id}/restart` | 重启（重新置备设备） | `Envelope[Bot]` |
| GET | `/openapi/v1/bots/{bot_id}/auth-status` | 轮询 Passport 授权；ISSUED 时完成创建 | `Envelope[BotAuthStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/status` | 运行时/设备就绪状态 | `Envelope[BotStatus]` |
| GET | `/openapi/v1/bots/{bot_id}/passport` | 获取 Agent Passport | `Envelope[Passport]` |
| GET | `/openapi/v1/bots/{bot_id}/engine-config` | 读取引擎配置（自由格式 JSON） | `Envelope[dict]` |
| PUT | `/openapi/v1/bots/{bot_id}/engine-config` | 写入引擎配置（自由格式 JSON） | `Envelope[dict]` |

_bots 上**刻意不暴露**的字段：创建时的 `engine_options`（下游目前没有任何代码会读
`BotCreateSpec.extra_properties`，暴露它等于承诺一个服务端其实会忽略的东西），以及更新时的
`cluster_name`/`engine_options`。有了 `extra="forbid"`，这些字段现在会得到 422，而不是被
悄悄丢弃。_

_内部 `/api/bots` 也有变化，全部是有意为之，并由 #494 覆盖：创建前置检查现在也会拒绝已被
占用的 bot 名字（于是重名会在申请外部 Passport **之前**就失败）；创建时持久化的是配置化的
引擎注册表，并且会补上该 bot 自己的 active engine；更新时的重名检查会把 owner **和**
`bot_id` 一起比较；删除默认 bot 会抛 `BotOperationNotAllowedError`（内部响应结构不变，
公共界面映射为 409）。_

### 🟦 totalfrank · P2 —— channels（6 个端点）· `openapi_v1/channels/router.py`
钉钉（`dingding`）渠道配置 CRUD + 状态切换。
| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| GET | `/openapi/v1/bots/channels` | 列出渠道（可选 `bot_id`） | `Envelope[list[Channel]]` |
| POST | `/openapi/v1/bots/channels` | 创建渠道（初始为停用） | `201 Envelope[Channel]` |
| GET | `/openapi/v1/bots/channels/{channel_id}` | 获取渠道 | `Envelope[Channel]` |
| PUT | `/openapi/v1/bots/channels/{channel_id}` | 全量更新 | `Envelope[Channel]` |
| PATCH | `/openapi/v1/bots/channels/{channel_id}` | 启用/停用切换 | `Envelope[Channel]` |
| DELETE | `/openapi/v1/bots/channels/{channel_id}` | 删除 | `Envelope[Deleted]` |

_注：桩里的列表返回 `Envelope[list[Channel]]`（不是 `Page`）；PR #363 里写的是
`Page[Channel]`。接线时请确认用哪种。_

### 🟦 totalfrank · P1 —— mcp（6 个端点）· `openapi_v1/mcp/router.py`
市场 + 租户 + 调用者的统一 per-server 配置。
| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| GET | `/openapi/v1/bots/mcp/servers` | 列出市场 MCP 服务器（`keyword`、分页） | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/bots/mcp/tenants` | 列出 MCP 租户 | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}` | 服务器详情 | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/permissions` | 查询调用者对该服务器的权限 | `Envelope[McpPermission]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/config` | 读取调用者的统一服务器配置 | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/bots/mcp/servers/{server_code}/config` | 写入配置（下发到设备） | `Envelope[McpConfig]` |

### 🟩 lucas-xzp · P1 —— resources（9 个端点）· `openapi_v1/resources/router.py`
文件/链接/文件夹的统一抽象；存储位置从不暴露。
| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| GET | `/openapi/v1/bots/resources` | 列表（`bot_id`、`type`、分页） | `Envelope[Page[Resource]]` |
| GET | `/openapi/v1/bots/resources/check-name` | 重名检查（`name`） | `Envelope[NameCheck]` |
| POST | `/openapi/v1/bots/resources` | 创建（文件占位 / 链接 / 文件夹） | `201 Envelope[Resource]` |
| POST | `/openapi/v1/bots/resources/upload` | 上传原始字节为资源（`application/octet-stream`） | `201 Envelope[Resource]` |
| GET | `/openapi/v1/bots/resources/{resource_id}` | 获取 | `Envelope[Resource]` |
| PUT | `/openapi/v1/bots/resources/{resource_id}` | 更新 | `Envelope[Resource]` |
| DELETE | `/openapi/v1/bots/resources/{resource_id}` | 删除 | `Envelope[Deleted]` |
| GET | `/openapi/v1/bots/resources/{resource_id}/download` | 下载字节（**原始，不走信封**） | `application/octet-stream` |
| GET | `/openapi/v1/bots/resources/{resource_id}/preview` | 预览 | `Envelope[Preview]` |

_注：桩里的上传是原始 `octet-stream`；PR #363 描述的是 `multipart`。接线时二选一。_

### 🟪 totalfrank + lucas-xzp · P3 —— skills，共担（7 个端点：桩里 5 个 + 提议新增 2 个 ★）· `openapi_v1/skills/router.py`
目录在 `/openapi/v1/bots/skills`；某个 Agent 已安装的技能是 bot 的子资源。

> **共担 —— 最棘手的类别。** skills 有三层生命周期（全局**上传** → per-bot **安装** →
> per-bot **启用/停用**）、两个尚未纳入桩的 ★ 端点，以及一个悬而未决的问题：后端更丰富的
> skill-set 模型是否要升为一等公民。因此**两人共担**。动手前先商定一份共同的子计划 ——
> 例如把 目录/上传 与 per-bot 安装/生命周期 分开 —— 并为它单独走一遍 SDD。在各自的 P1/P2
> 切片之后再做。

**状态**列标明每个端点是已经在路由桩里（`桩内`），还是来自 PR #363 的提议新增
（`★ 提议` —— 尚未在桩里；实现前请与 totalfrank 确认）。

| 方法 | 路径 | 用途 | 成功响应 | 状态 |
|---|---|---|---|---|
| GET | `/openapi/v1/bots/skills` | 技能目录（`keyword`、分页） | `Envelope[Page[Skill]]` | 桩内 |
| GET | `/openapi/v1/bots/skills/{skill_id}` | 技能详情 | `Envelope[SkillDetail]` | 桩内 |
| POST ★ | `/openapi/v1/skills/upload` | 上传自定义技能（全局，归属调用者） | `Envelope[Skill]` | ★ 提议 |
| GET | `/openapi/v1/bots/{bot_id}/skills` | 列出某个 Agent 已安装的技能 | `Envelope[list[BotSkill]]` | 桩内 |
| POST | `/openapi/v1/bots/{bot_id}/skills` | 为 Agent 安装技能（默认启用） | `201 Envelope[BotSkill]` | 桩内 |
| PATCH ★ | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | 启用/停用已安装技能（`status`） | `Envelope[BotSkill]` | ★ 提议 |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | 卸载（解除绑定） | `Envelope[Deleted]` | 桩内 |

### 🟩 lucas-xzp · P1 —— routines（7 个端点）· `openapi_v1/routines/router.py`
定时/触发的 Agent 任务（原来的 "cron"）；触发器是嵌套对象。
| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| GET | `/openapi/v1/bots/routines` | 列表（`bot_id`、`status`、分页） | `Envelope[Page[Routine]]` |
| POST | `/openapi/v1/bots/routines` | 创建 | `201 Envelope[Routine]` |
| GET | `/openapi/v1/bots/routines/{routine_id}` | 获取 | `Envelope[Routine]` |
| PATCH | `/openapi/v1/bots/routines/{routine_id}` | 局部更新 | `Envelope[Routine]` |
| DELETE | `/openapi/v1/bots/routines/{routine_id}` | 删除 | `Envelope[Deleted]` |
| POST | `/openapi/v1/bots/routines/{routine_id}/run` | 立即执行一次 | `Envelope[RoutineRun]` |
| GET | `/openapi/v1/bots/routines/{routine_id}/runs` | 执行历史（分页） | `Envelope[Page[RoutineRun]]` |

### 🟩 lucas-xzp · P2 —— identity（3 个端点）· `openapi_v1/identity/router.py`
读写某个 Agent 的身份 markdown 文件（RULES、SOUL 等），`file_type` 是枚举白名单。没有自己的
Track A 阶段 —— 由 bots 隔离（Stage 1 ✅）覆盖。
| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| GET | `/openapi/v1/bots/identity/bot/{bot_id}` | 列出身份文件（含是否存在） | `Envelope[IdentityFileList]` |
| GET | `/openapi/v1/bots/identity/bot/{bot_id}/{file_type}` | 读取单个身份文件 | `Envelope[IdentityFile]` |
| PUT | `/openapi/v1/bots/identity/bot/{bot_id}/{file_type}` | 覆写单个身份文件（`content`） | `Envelope[IdentityFileRef]` |

---

## 完成的定义（整个 `/openapi/v1` 工作）

1. **Track A：** 每一类数据（bots、resources、channels、skills、mcp、routines）都带有
   `avernet_tenant` 并被守卫，Stage-1 的测试形态全绿。—— _6 个里完成 1 个（bots ✅）。_
2. 全程内部 API 保持不变（`to_dict()` 无泄漏；内部套件不作修改）。—— _仍然成立：#494
   时整个 `tests/community` 全绿（9171 通过，3 跳过）。_
3. **Track B：** 七个 `/openapi/v1` 类别的处理器均已实现且租户安全，各自带测试 + PR。
   —— _7 个里完成 1 个（bots ✅）。_
4. F2 租户前导索引就位（强制策略）。—— _⬜_
5. 后台/定时任务已针对按租户正确性完成复查。—— _⬜_
6. `require_principal` / `resolve_avernet_tenant` 已接到真实验证器（认证工作线）——
   到此，第二个租户才能安全地持有真实数据，公共界面也才会停止一律返回 401。—— _⬜_
7. **跨租户的外部身份问题已定案（[#556](https://github.com/inclusionAI/Avernet/issues/556)）** —— Passport、授权关系与 BCN 都带上
   租户维度，从而可以在公共路径上重新打开 BCN 同步。—— _⬜（2026-07-29 新增；它是开启
   多租户的前置闸口）。_

---

## 横切的延后事项

- **F2 —— 租户前导索引（强制的公司策略）。** 带有租户列的表必须有租户前导索引。延后到专门的
  索引工作中；**多租户上线前必须完成**。做的时候：把 `avernet_tenant` 前置到支撑查询的复合
  索引上（`idx_owner` → `(avernet_tenant, owner_id)`、`idx_bot_id_entity_id`、
  `idx_entity`、搜索索引），采用**先建新、再删旧**（命名约定把索引名与其列绑定，因此先建后删，
  避免出现无索引的窗口）。低基数索引（`idx_status`、`idx_is_delete`）与唯一查找索引
  （`idx_binding_id`）保持不动。
- **真实的调用方身份验证器。** 把 `require_principal` 的函数体换成返回网关转发的
  principal，把 `resolve_avernet_tenant` 换成返回它的租户。两处接缝都已就绪，而且
  `caller_owner_id` 既接受裸的 id 字符串、也接受带 `user_id` 的对象/字典，因此处理器
  不需要改动。**在它落地之前，公共界面对任何请求都只会返回 401。**
- **后台/定时任务。** 现在都解析为默认租户（在全部数据都是 `teamclaw` 时是正确的）；在第二个
  租户持有真实数据之前需复查（skill_center / governance / dormant / 设备轮询器中的定时扫描、
  轮询器、同步循环）。
- **建议：** 加一个架构守卫/lint，标记 core 中新出现的裸 `threading.Thread` /
  `ThreadPoolExecutor`，这样未来的请求内 spawn 就不会悄悄丢失租户。

---

## Track A Stage 1 中踩过的坑（帮你省下往返）

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

## Track B bots 中踩过的坑（PR #494）

- **信封会从你没看的地方漏出去。** 处理器级别的装饰器只覆盖处理器*内部*的失败。未知路径
  （404）、方法不对（405）、请求体校验失败（422）都在路由跑起来*之前*就抛出了，此前它们
  返回的是 `{"detail": ...}` —— 而这恰恰是新接入方最先撞上的三件事。已在 `app.py` 里按
  路径限定于 `/openapi/v1` 一次性解决，不要再重复解决。
- **基类必须映射在最后。** `ENVELOPE_ERRORS` 按插入顺序在第一个 `isinstance` 命中时返回，
  因此列在基类*之后*的具体子类永远轮不到。
- **不在你类别继承体系里的错误照样会逃逸。** engine-config 的几个失败是平级的
  `RuntimeError` 兄弟类，不是 `BotServiceError` 的子类 —— 每一条有据可查的传播路径都得单独
  加一条映射。请 grep 你的服务实际会抛什么，不要假设一个基类就都覆盖了。
- **绝不要转发异常自带的实体头**（`Content-Length`/`Content-Type`）—— 它们描述的是被丢弃的
  那个响应体。但协议头要转发（405 的 `Allow`、401 的 `WWW-Authenticate`）。
- **`extra="forbid"` 是表达"不可变"的方式。** 没有它，bot 更新里的 `engine` 会被悄悄丢掉，
  而调用方以为改成功了。
- **凡是内部界面也在做的事，抽出来，别复制。** 创建/Passport 编排如果有两份拷贝，一个版本
  之内就会漂移。抽取必须是行为保持的 —— 用"内部测试套件不作修改"来证明这一点。
- **即便是行为保持的抽取，也会照出内部的既有 bug。** 有四个内部 `/api/bots` 的缺陷，是把
  逻辑从路由里读出来之后才看见的（见 bots 表下方的说明）。要预期到这一点，并明确决定要不要
  在同一个 PR 里修。
- 一个"只是接线"的 PR 走了十六轮自动评审。请为评审轮次留出预算；那些在接线 PR 里无法定案的
  问题，应当立成 issue（#556 / #559 / #560），而不是绕着它们打补丁。

---

## Changelog（变更记录）（每次挪动看板时追加一条带日期的记录）

- **2026-07-27** —— 交接 README 创建。Track A Stage 1（bots + 可复用机制）已完成，位于
  **PR #456**，等待审批。Track B 尚未开始。
- **2026-07-27** —— 按**纵向切片**完成分工（无跨人阻塞）：**totalfrank** = bots、channels、
  mcp；**lucas-xzp** = resources、skills、routines、identity。排序问题已定 → 按类别纵向切片。
  新增**各组件端点清单**（来自路由桩 + PR #363），并标注 `/openapi/v1/bots/...` 与顶层路径的
  分歧、以及两个提议的 ★ skills 端点。
- **2026-07-27** —— 新增**优先级分层**：P1 = bots、mcp、resources、routines；P2 =
  channels、identity；P3 = skills。**skills 改为共担**（totalfrank + lucas-xzp，包含它的
  Track A 阶段与端点），因为它是最复杂的类别。两个状态看板都加了优先级列；各组件标题也标注了
  层级。
- **2026-07-27** —— skills 端点从两张表（桩里 5 个 + 提议 2 个）合并为**一张带"状态"列的
  7 行表**，这样一眼就能看出是 7 个端点，而不是看起来像 2 个。
- **2026-07-27** —— **Track A Stage 1 已合并（PR #456）。** bots 带上了
  `avernet_tenant`；可复用机制（租户载体、守卫、中间件、`resolve_avernet_tenant` 接缝）
  已在 `dev` 上。bots 的共同闸口就此解除。
- **2026-07-29** —— **Track B bots 已合并（PR #494）—— 第一个落地的公共类别。**
  13 个 `/openapi/v1/bots` 端点全部接到内部服务，通过 `caller_owner_id` 做 owner 限定，
  并由 Track A 守卫做租户限定（别的租户的 `{bot_id}` → 被掩盖的 404，且是针对真实守卫
  验证过的）。同时沉淀了其余六个类别可复用的 **Track B 共享基建**（`responses.py`、
  `contracts.py`/`ERROR_RESPONSES`、`principal.py`、`clusters.py` 的 ACRA/ANDC、
  `errors.py`），并在 `app.py` 里从结构上封死了信封逃逸。抽出了
  `core/bot_management/create_flow.py` 与 `readiness.py`，让两套界面共用一份实现。
  新增架构门禁 `test_service_api_conformance.py`。全量测试 9171 通过 / 3 跳过。
  看板更新：Track A 阶段 1 → 已合并，Track B bots → 已完成；新增 **Track B 基建 +
  操作手册**一节以及 Track B 的踩坑记录。
- **2026-07-29** —— 从 #494 的评审中沉淀出三条需要继承的决策，并加入横切看板：
  **[#556](https://github.com/inclusionAI/Avernet/issues/556)** 跨租户的 Agent 身份撞车（**开启多租户的前置闸口**，现已列为 DoD 第 7 条）、
  **[#559](https://github.com/inclusionAI/Avernet/issues/559)** 异步创建出的 bot 可能不是被授权的那个、
  **[#560](https://github.com/inclusionAI/Avernet/issues/560)** 外部身份写入失败被吞掉。三者都是 `dev` 上的既有问题。
  同时把**"认证落地前一律返回 401"**这一状态写到了文档开头 —— 公共界面虽已实现，但尚不可
  被真正调用。
