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

**Track C**（包装 Bot 的 *engine* 运行时）的逐端点裁定放在配套参考文档：
**[`engine-surface.zh-CN.md`](engine-surface.zh-CN.md)**
（[English](engine-surface.md)）。本 README 仍是唯一的状态看板；清单在那个文件里。

---

## 全局视角（请先读这一节）

**目标：** 实现公共 `/openapi/v1` API，其调用方是**外部注册租户**。它位于
`src/backend/src/agentclaw/community/adapters/http/openapi_v1/*`。其中 **bots**
类别已经实现（PR #494）；其余六个仍然是**带桩（stub）处理器的路由定义**。

> 🔒 **这套界面端到端仍不可被真正调用，但原因已不再是"桩"。**
> `require_principal` 现在会真正校验网关签发的 `X-Avernet-Principal` 令牌，
> `resolve_avernet_tenant` 也会真正从中读出租户（见下文**认证接缝**一节）。上游还有
> 网关的签发 PR（[#599](https://github.com/inclusionAI/Avernet/pull/599)）**已合并**，
> 因此 `dev` 上确实会转发这个头 —— `user` 调用方现已端到端跑通（2026-08-02 对着真实
> 签名器验证过）。仍挡在**外部租户**与 `200` 之间的是"谁可以调用"：网关的
> `route_security` 要求由 Google 链解析出的 `user` 身份，而持访问密钥（access key）的
> 租户满足不了它；且自 2026-08-02 起后端会独立拒绝任何不指向终端用户的身份集合。
> 放宽它属于委托工作线，不是改一行配置。
> "bots 做完了"的含义不变：处理器、契约和测试做完了。

关键难点：内部的 `/api/...` 界面与公共的 `/openapi/v1` 界面**共享同一批表、仓储
（repositories）和服务（services）**。因此，一个会返回真实数据的公共端点，如果没有隔离，
就会读到*内部*租户的数据。防止这一点，正是这项工作存在的原因。

因此，工作被拆分为**三条主线（Track）**：

- **Track A —— 租户隔离基础设施。** 在接通任何公共端点之前，先让*两套 API 界面之下的*
  每一类数据都做到按租户隔离。**Track A 按设计不实现任何端点** —— 它是底层管道。
- **Track B —— 公共 API 实现。** 把七个 `/openapi/v1` 类别处理器接到已有的服务上。
  **这才是真正落地端点/API 代码的地方。** 每个类别都依赖于其数据已先经过 Track A 的
  隔离。**七个里已完成两个：bots（PR #494）、mcp（PR #610）。**
- **Track C —— Engine（运行时）面。** _2026-07-30 新增。_ 把 engine adapter 面向
  客户端的 HTTP 包装到 `/openapi/v1/bots/<component>/{bot_id}/…` 之下，并用一个净化过的
  socket 信息端点取代 `get_device_connection` 的移交。**16 个端点 —— 已实现，PR #630。**

> ⚠️ **唯一需要避免的误解：** "隔离 Stage N 已完成"**并不**意味着任何 API 端点被实现了。
> Track A 的每个阶段都只是底层管道（可复用机制 + 该类别的记录）。API 端点落在
> Track B —— bots 已完成，其余六个仍是桩。
>
> ⚠️ **Track C 没有对应的 Track A 阶段，这是对的。** Track A 与 Track B 是成对的
> （先隔离一个类别，再接通它的端点）；Track C 不是。它的数据在 Bot 的设备上，
> 不在后端表里，因此没有任何对象需要加 `avernet_tenant`，**也没有 DDL**。隔离
> 完全来自"触达设备之前先经 bots 守卫（Stage 1 ✅）解析 `bot_id`" —— 与
> `identity` 没有自己的阶段是同一个道理。别去找一个并不存在的 Track A 阶段。

---

## 谁在做什么

我们按**纵向切片（vertical slice）**分工：每个人端到端地负责若干数据类别 —— 既包括它的
**Track A** 隔离阶段，也包括它的 **Track B** 端点。这样一来，一个 Track B 类别就绝不会被
*另一个人*负责的 Track A 阶段卡住。（你举的 `mcp` 例子：`mcp` 的 Track B 依赖 `mcp` 的
Track A，所以两者都归同一个人。）

| 成员 | 负责（纵向切片） | Track A 阶段 | Track B 端点组 |
|---|---|---|---|
| **totalfrank** | bots、mcp、**skills**（共担） | 1（bots ✅）、5（mcp）、4（skills，共担） | bots、mcp、skills（共担） |
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
| **P2 —— 第二优先** | identity | identity → lucas-xzp |
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
| 2 | 资源（`ac_resource`） | lucas-xzp | P1 | ✅ DONE —— Phase 0（分支 `rongzhi_0727`） | 列 + 守卫 + 测试通过；内部 API 不变 — 已验：to_dict 不含 tenant、guard 直接表达式非 lambda、Changelog 见下 |
| 3 | 渠道（`ac_channel_config`） | —— | ❌ **已放弃** | 该阶段从未开工；其 Track B 组件已于 2026-08-03 删除 | 不适用 |
| 4 | 技能（skill 相关表） | totalfrank + lucas-xzp | P3 | ⬜ TODO | 同上 |
| 5 | MCP 配置（`ac_user_mcp_config` + `ac_bot_mcp_call_config`） | totalfrank | P1 | ✅ DONE —— **PR #564** | PR #564 合并后 |
| 6 | 例程（Routines） | lucas-xzp | P1 | ⬜ TODO | 同上 |

> Stage 1 同时构建了后续每个阶段都会复制的**可复用机制**（见下文）。它是地基，
> 不只是"机器人"。

### Track B —— 公共 API 实现（端点真正落地之处 —— 七个里已完成两个）
_按优先级分层排序。_
| 类别 | 负责人 | 优先级 | 路由 | 状态 | 依赖 |
|---|---|---|---|---|---|
| bots | totalfrank | P1 | `openapi_v1/bots/router.py` | ✅ **DONE —— PR #494 已于 2026-07-29 合并**（13/13 端点） | ~~Track A 阶段 1~~ ✅ |
| mcp | totalfrank | P1 | `openapi_v1/mcp/router.py` | ✅ **DONE —— PR #610**（6/6 端点） | ~~Track A 阶段 5~~ ✅（PR #564） |
| resources | lucas-xzp | P1 | `openapi_v1/resources/router.py` | 🔧 IN PROGRESS（PARTIAL）— 9 handler 全接通但 DEFINITION-ONLY / NOT PUBLIC-READY | Track A resources ✅(Phase 0)，Track B 全 9 端点接通 stub→service；待 auth workstream(gateway principal seam) 落地 + DDL 部署后才可对外 |
| routines | lucas-xzp | P1 | `openapi_v1/routines/router.py` | 🔧 IN PROGRESS（PARTIAL）— 7 handler 全接通但 NOT PUBLIC-READY | Track A routines 无表靠 ac_bots 间接隔离；Track B 7 端点接通；待 gateway principal seam + tenant resolver 落地后才可对外 |
| channels | —— | ❌ **已删除（2026-08-03）** | *(已删除)* | 路由、schema 与两条已发布路径均删除 —— 见下方 channels 小节 | 不适用 |
| identity | lucas-xzp | P2 | `openapi_v1/identity/router.py` | 🔧 IN PROGRESS（PARTIAL）— 3 handler 全接通但 NOT PUBLIC-READY | bots 隔离（Stage 1 ✅）；Track B 3 端点接通；待 gateway principal seam + tenant resolver 落地后才可对外 |
| skills | totalfrank + lucas-xzp | P3 | `openapi_v1/skills/router.py` *(桩)* | ⬜ TODO | Track A skills（共担） |

### Track C —— Engine（运行时）面（5 组已全部实现 —— PR #630）
_所有组只依赖 **bots 隔离（Stage 1 ✅）** —— 没有 Track A 阶段，没有 DDL。
完整裁定与逐端点映射见
**[`engine-surface.zh-CN.md`](engine-surface.zh-CN.md)**。_

| 组 | 端点数 | 负责人 | 优先级 | 路由 | 状态 |
|---|---|---|---|---|---|
| sessions | 7 | ⬜ 未分配 | P1 | `openapi_v1/engine_runtime/sessions/` | ✅ **已实现 —— PR #630**（仅 personal bot；`service` 返回 501） |
| engine（只读） | 3 | ⬜ 未分配 | P1 | `openapi_v1/engine_runtime/engine/` | ✅ **已实现 —— PR #630** |
| connection | 1 | ⬜ 未分配 | P1 | `openapi_v1/engine_runtime/connection/` | ✅ **已实现 —— PR #630** |
| approvals | 3 | ⬜ 未分配 | P2 | `openapi_v1/engine_runtime/approvals/` | ✅ **已实现 —— PR #630** |
| models | 2 | ⬜ 未分配 | P2 | `openapi_v1/engine_runtime/models/` | ✅ **已实现 —— PR #630** |

> **范围规则（为什么只有这些）。** 只包装前端经 proxypass **直连**的 engine HTTP
> （`src/frontend/src/requestConfig.ts:189-205`）。前端**经由后端**触达的 engine
> 路由 —— `/api/cron`（已经是 `routines` 类别）、`/api/file`、`/api/skills`、
> `/api/mcp`、`/api/resource-materializations`、`/api/bash`、`/api/bot/config`、
> `/api/work-items` —— 已经有后端契约在其之上，不纳入。仅 aicoding 的路由不纳入。
> **WebSocket 不包装**：新的 `…/connection` 端点返回一条完整的 socket URL（凭据在其中），
> 由调用方自己建连。
>
> `engine/switch` 与 `engine/restart` 刻意排除 —— 包装 `switch` 等于给 #494 在
> `PUT /openapi/v1/bots/{bot_id}` 上的 `engine` 不可变裁定开后门，包装 `restart`
> 会让同一个 bot 有两个重启动词。`session-favorites` 与 `/api/openclaw` HTTP
> 三件套是**延后，不是取消**（两者以后再加都是增量）。理由见 `engine-surface.zh-CN.md`。
>
> **routines 是 Track C 的样板，而不是 Track B 的。** 后端 `/api/cron` →
> `CronRelayService` → `DeviceAdapterTransport` → engine 一直就是生产上的形状，
> 而 `openapi_v1/routines/router.py:29` 已经 import 了 `CronRelayServiceProtocol`。
> 动手写 handler 前先读它。

### 横切事项（非按阶段划分）
| 事项 | 状态 | 备注 |
|---|---|---|
| 真实的调用方身份验证器（认证工作线） | ✅ **两半均已完成** —— 后端 PR [#634](https://github.com/inclusionAI/Avernet/pull/634)、网关 PR [#599](https://github.com/inclusionAI/Avernet/pull/599) **已合并** | `require_principal` 与 `resolve_avernet_tenant` 会校验网关签发的 `X-Avernet-Principal`（HS256、`aud=backend`），并从中读出租户与 owner。线上契约已通过把**真实**网关签名器接进**真实**后端验证器做往返验证（2026-08-02）：user/bot/app/access_key 四种形状、机密不外投、`aud`/`iss` 不符即拒。**`user` 调用方已可端到端跑通。** 剩下的是*哪些*调用方被接纳 —— 见下一行 |
| **身份接纳：仅 `user`** | ✅ **2026-08-02 完成** | `verify_principal_token` 拒绝任何不指向终端用户的身份集合，因此 `bot` / `app` / `access_key` 调用方是**按设计**返回 `401`，而不是取决于某个 handler 是否去取 owner。放宽它靠委托（认证设计 §15），不是改配置。SDD：`specs/2026-08-02-public-api-user-only-principal/` |
| **没有跨仓测试钉住 principal 线上形状** | ⬜ TODO | 两侧各自对着自己手写的 payload 认知做测试（`test_verifier.py` 拼 dict；网关测自己的 model）。任一侧改个字段名，两边测试都还是绿的，线上却全 401 |
| 租户前导索引（F2，**强制**策略） | ⬜ TODO | 多租户上线前必须完成 |
| 后台/定时任务的复查 | ⬜ TODO | 在第二个租户持有真实数据之前完成 |
| **Agent 身份标识在租户之间会撞车**（[#556](https://github.com/inclusionAI/Avernet/issues/556)） | ⬜ TODO（totalfrank） | Passport、授权关系、BCN、策略行都只用 `bot_id`/`owner_id` 作键，没有租户维度，而每个 owner 的第一个 bot 的 id 就是字符串 `"default"`。**应当成为开启多租户的前置闸口。** #494 里以公共更新路径上的 `sync_to_bcn=False` 做了临时止血 |
| 异步创建出的 bot 可能不是被授权的那个（[#559](https://github.com/inclusionAI/Avernet/issues/559)） | ⬜ TODO（totalfrank） | pending 状态的创建规格从未被持久化，完成时是用轮询请求重建的。`dev` 上既有问题；当前潜伏（社区版 Passport 总是直接签发） |
| 外部身份写入失败被吞掉（[#560](https://github.com/inclusionAI/Avernet/issues/560)） | ⬜ TODO（totalfrank） | 创建时的 owner 授权写入、更新时的 Passport 元数据写入都是"记日志然后继续"，违反 `AGENTS.md:203-204`。一次决策同时覆盖两处；建议做法是*报告部分成功* |
| resources/routines/identity principal/tenant 真正接入 | ⬜ TODO | 三组 handler 已接通但仍依赖 gateway principal verifier 与 `resolve_avernet_tenant` 真正落地；对外开放前必须统一从 `require_principal`/`caller_owner_id` 消费调用者身份 |
| 资源所有权/权限边界 403/404 | ⬜ TODO | 当前跨租户靠 ORM guard（Phase 0） + bot_id 必填；ownership/permission mismatch 显式 403/404 待对外开放前补 |
| 上游/storage/provider 错误统一映射 | ⬜ TODO | handler 现按点抛 HTTPException（400/404/409/500）；对外开放前统一错误码映射 |
| public contract docs + conformance tests | ⬜ TODO | served OpenAPI 已有；契约 conformance 测试（参数/响应/错误码/兼容性）待对外开放前补 |

> 上面 #556/#559/#560 三条来自 #494 的评审，都是 `dev` 上的**既有问题**而非本次引入的回归 —— 记在这里
> 是因为它们是整个工作都要继承的决策，而不是 bots 独有的 bug。其中 #556 尤其必须在
> 第二个租户持有真实数据之前定下来。
| **阶段 5 对 `ac_user_mcp_config` 的唯一键替换** | ⬜ TODO（DDL 见下文） | **在第二个租户写入 MCP 配置之前**完成 —— 不必赶在发布之前 |

> **❌ 渠道已删除（2026-08-03）。** 自 2026-07-29 起以"降级"的形式搁置；现已整体删除。
> 搁置对它并不合适：该组件是**已发布**的，所以一个被搁置的桩并不是看板上一行休眠的记录 ——
> 它是网关所提供文档里的 6 个操作，每一个都回 500。Track A 阶段从未开工，因此没有数据层
> 的工作需要回退。被删除的内容见**端点**部分的 channels 小节。
>
> _（以下为 2026-07-29 的原始说明，保留作为历史。）_ 目前产品并不需要渠道，因此它不应再以"下一个该
> 动手的事项"的形式出现在看板上。这是一次**降级，而不是取消** —— 两行都保留完整范围，
> 可以原样重新启动。如果渠道确实被取消，应当删除这两行，而不是让它们停留在搁置状态。

---

## 带外执行的库表变更（仓库内不放 migration 文件）

按既定决策，租户隔离相关的库表变更一律在平台侧带外执行，因此**下列语句就是权威记录**。
请把它们连同顺序约束一并交给执行 DDL 的同学。

**阶段 1 —— `ac_bots`**（已执行）：

```sql
ALTER TABLE ac_bots
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';
```

**阶段 5 —— MCP 配置**（PR #564）。三条语句，**两个不同的时间点**：

```sql
-- 1. 加列。必须在代码发布之前执行：SELECT 一个不存在的列会直接报错，
--    因此"先发代码"会让 MCP 配置的读取整体不可用。NOT NULL DEFAULT 会就地
--    回填已有行，且对当前已部署的代码是惰性的，所以"先执行 DDL"是安全的。
ALTER TABLE ac_user_mcp_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

ALTER TABLE ac_bot_mcp_call_config
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';

-- 2. 唯一键替换。并不需要赶在代码发布之前：只有一个租户时，新旧两个键接受的
--    行集合完全相同。它真正开始起作用，是在**第二个租户写入 MCP 配置**的那一刻 ——
--    因为 (user_id, server_code, env) 会拒绝第二个租户为同一个用户工号写入的行，
--    报的是一个针对它根本看不见的行的重复键错误。
--    先建后删，确保唯一性约束不出现空窗。给唯一键前置一列只会放宽约束，
--    因此所有已有行都仍然合法。
ALTER TABLE ac_user_mcp_config
  ADD UNIQUE KEY uix_user_mcp_config_tenant
    (avernet_tenant, user_id, server_code, env) GLOBAL;
ALTER TABLE ac_user_mcp_config
  DROP INDEX uix_user_mcp_config;
```

`ac_bot_mcp_call_config` **不需要**改键：它的
`(bot_pk, server_code, engine_type, env)` 以 `ac_bots.id` 这个全局主键打头，
租户已由它函数式决定，上面那种冲突在这张表上根本无法表达。

本地与 singlebox 运行时无需执行 DDL —— `Base.metadata.create_all` 会直接依据模型
建表。
>
> ⚠️ **NOT PUBLIC-READY 总标记**：resources/routines/identity 三组 handler 已全接通并绿，**但当前判断为 NOT PUBLIC-READY** —— gateway principal verifier 与 `resolve_avernet_tenant` 仍未真正落地。**可阶段性合入 dev/分支**，但**不可对外开放**，需先完成上表 principal/tenant 接入等横切项后才能转 PUBLIC-READY。

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
- `utils/avernet_tenant_guard.py` —— **守卫模式（guard pattern）**，自阶段 5 起
  与具体模型解耦。模型在类定义之后紧跟一行 `register_avernet_tenant_guard(Model)`
  即可接入；该模型必须声明
  `avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")`。
  - 在 `Session` 类上的 `do_orm_execute` **读守卫**（只安装一次）→ 为**每个已注册
    模型**追加一个 `with_loader_criteria(Model, avernet_tenant ==
    get_current_avernet_tenant(), include_aliases=True)`；会跳过列/关系加载，并提供
    一个 `skip_avernet_tenant_guard` 选项。同时也约束
    `Query.update()`/`Query.delete()`，因此写操作无需再加过滤。若某个选项指向的模型
    并未出现在该语句中，它就是空操作 —— 这正是"一个监听器服务 N 个模型"成立的前提。
  - 每个模型各自的 `before_insert` **插入守卫** → 未设置时打上标记，遇到显式冲突的
    租户时抛出 `CrossTenantInsertError`。
  - `register_avernet_tenant_guard` 校验的是 **mapper 的列**，而不是 `hasattr`：
    否则一个把 `avernet_tenant` 声明成普通值的模型也能注册成功，而守卫会生成
    `WHERE 1 = 1` —— 一次静默且彻底的隔离失效。
  - 每个模型的注册是幂等的；`guarded_models()` 把注册表暴露给测试与诊断使用。
  - 阶段 1 时这套守卫是焊死在 `plugin_api/models.py` 里的 `BotModel` 上的；阶段 5
    将其抽出，使 `core/` 下的模型无需让 `plugin_api` 反向导入它们即可注册。
    `plugin_api/models.py` 仍会重新导出 `CrossTenantInsertError`。
- `adapters/http/middleware.py` —— `AvernetTenantMiddleware`，一个**纯 ASGI**
  中间件（**不是** `BaseHTTPMiddleware` —— 出于 ContextVar 的健壮性考虑）。它为每个请求
  设置租户。**已覆盖所有请求；Track A 阶段 2 及以后无需改动它。**
- `adapters/http/openapi_v1/dependencies.py` —— `resolve_avernet_tenant(request)`：
  唯一的接缝（seam）。与具体类别无关。**已不再是桩** —— 它和 `require_principal` 都读取
  校验过的网关 principal，见下文**认证接缝**一节。建立在后者之上的 owner 侧接缝是
  `openapi_v1/principal.py::caller_owner_id`。

以上所有路径都位于
`src/backend/src/agentclaw/community/` 之下。

---

## 认证接缝 —— 一个调用方如何变成"租户 + owner"

两处公共接缝读的是**同一个**头，且每个请求只校验**一次**。
SDD：`src/backend/specs/2026-07-30-gateway-principal-verifier/`。

```
网关                校验凭证 → 解析出身份集合 → 签名
  │                 （HS256、aud = 上游服务名、TTL 60s、principals[]）
  ▼  X-Avernet-Principal
AvernetTenantMiddleware → resolve_avernet_tenant(request)  ─┐
                                                            ├─ 只校验一次，
路由依赖                → require_principal(request)       ─┘  结果缓存在 scope 上
                             │
                             └→ caller_owner_id(principal) → owner 作用域的服务调用
```

- `core/gateway_principal/` —— 验证器与**我们自己**的线上格式 DTO。后端从不 import
  网关类型（Rule 7 / §9），而是做投影（project）。
- `utils/gateway_principal_config.py` —— 通过 `SecretResolver` 按
  `SecretNamesConfig.gateway_principal_signing_key` 解析共享密钥。该密钥名**自带默认
  值**，因此部署只需配置「值」：公司密钥库（corp，overlay 同时覆盖密钥名）、
  `AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE`（community）。单盒**不解析任何值** ——
  既没有密钥库，也不提供本地替代品，因此单盒的 `/openapi/v1` 一律拒绝。
  单盒没有任何配置项可以改变这一点；要让单盒拿到密钥属于一次刻意的改动，而不是加一行配置。
  这一侧故意**不带** dev 兜底密钥（提交进仓库的共享密钥就是提交进仓库的凭据）。密钥只在启动
  时解析一次，因此轮换密钥需要两侧都重启。

  **拿不到密钥时的行为按环境区分**，这个区别正是要点：

  | 环境 | 拿不到可用密钥时 |
  | --- | --- |
  | `pre` / `prod` | **进程拒绝启动** —— `init_principal_verifier_config` 抛错，让发布明确失败，而不是让一个「看起来健康、实际全 401」的服务上线 |
  | local / dev / 单盒 | **每个 `/openapi/v1` 请求返回 401** —— 这些环境本就没有密钥，因此保持可启动、只拒绝请求 |

  两种情况的触发条件相同：密钥不存在、值为空，或解析器抛错。

- `aud` 与 `iss` 在后端固定写在代码里，不做成配置项 —— 一份线上契约只保留一种写法。但
  签名侧并不对称：
  - `aud` 在网关那侧同样不可配（网关用上游 server 自己的名字签发），所以只在验证侧加开关
    无法改变契约，只会把它弄坏。
  - `iss` 在网关那侧**是可配的** —— 自 gateway #673 起为
    `user_config.principal_signer.issuer`，默认值 `gateway` 正好与这里的常量一致，因此
    出厂状态契约成立。**一旦改动网关的 `issuer`，必须在同一个版本里同步改后端常量**，
    否则所有请求都会 401。这与 `aud` ↔ `servers:` 名字之间的耦合一样，没有任何机制强制
    校验。
- 会被拒绝的情形，全部返回**完全一致**的 `401`：签名错误、`alg: none`、`aud` 指向别的
  上游、`iss` 不对、已过期、缺少必需 claim、未知的 `type` tag、契约字段被改名、身份集合
  内部租户不一致、**声称自己是 `teamclaw` 的租户**（后者会把全部内部数据交给外部
  调用方），以及**没有指向任何终端用户的身份集合**（见下）。
- 网关的租户 id **就是** `avernet_tenant` 的值 —— 没有映射表。因此真实外部租户在拥有
  自己的数据之前读到的是空集；这是隔离在正常工作，不是 bug。

**如果你负责某个 Track B 类别，有两件事会传导到你：**

1. **只接纳 `user` 调用方 —— `bot` / `app` / `access_key` 在校验阶段即被拒绝。**
   _2026-08-02 定案。_ owner id 只从 `user` principal 推导。网关的 `app.owners` 是
   自由文本的组织归属，其访问密钥注册表根本没有 owner 列，两者都指不出一个可用于作用域
   的人；`bot` 虽然带 `owner_id`，但让一个 bot 在整个公共契约上以其 owner 的身份行事，
   是没有人做过的授权。**拒绝发生在 `verify_principal_token`，而不是
   `caller_owner_id`** —— 这才是关键：放在 handler 里的拒绝只覆盖会去取 owner 的
   handler，而 `resources/router.py` 里有四个并不取；拒绝整个身份集合，意味着无法作用域
   的调用方**任何**路由都进不来（包括以后新增的）。`app` / `access_key` 究竟该拥有什么
   仍未定（认证设计 §14 Q4）；委托（§15）才是放宽它的设计路径。SDD：
   `src/backend/specs/2026-08-02-public-api-user-only-principal/`。
2. **依赖（dependency）里抛出的已映射错误现在也会被套上信封。** `@envelope_errors`
   只包裹 handler，所以接缝的 401（在依赖里抛出）会绕过它；现在查表逻辑落在
   `responses.py::mapped_error_response`，应用的 catch-all 查的是同一张表。你新增的依赖
   若抛出领域错误，已经会以信封格式作答。

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
   `src/backend/specs/2026-07-27-openapi-v1-bots-track-b/` +
   `openapi_v1/bots/router.py` 当作已经做过一遍的参考样板；第二个样板是
   `src/backend/specs/2026-07-30-openapi-v1-mcp-track-b/` + `openapi_v1/mcp/router.py`
   —— 它示范了当一个类别需要**从仍在运行的内部路由里抽取共享逻辑**（配方第 6 步）到
   `core/mcp/` 时的做法，并通过让内部测试套件保持不修改来证明抽取是行为保持的。

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

## 寻址规则

**每个操作的地址都是 `/openapi/v1/bots/<component>/…`。** 组件的**字面**名称在前；
带 Agent 作用域的操作把 `{bot_id}` 放在组件名**之后**的第一段 —— 不在它前面，中间
也不再夹一个 `/bot/`。

```text
/openapi/v1/bots/<component>            # 该组件自己的集合
/openapi/v1/bots/<component>/{bot_id}   # …限定到某一个 Agent
```

`bots` 组件是唯一的例外，而且仅仅因为它**就是** base 所命名的那个组件：它拥有
`/openapi/v1/bots` 与 `/openapi/v1/bots/{bot_id}`，它自己的子资源（`/status`、
`/passport`、`/restart`、`/auth-status`、`/engine-config`）挂在这个 Agent 记录之下。
这些是 Agent 本身的属性，而不是别的组件来借用 Agent 的地址。

**为什么。** 曾有三处违反此规则 —— `identity` 多带了一段冗余的 `/bot/`；
`connection`/`engine`/`approvals`/`sessions`/`models`/`skills` 则把 `{bot_id}` 放在了
自己的组件名之前。这让一个路由文件无法自述其地址（读
`engine_runtime/sessions/router.py` 的人无从判断 `/openapi/v1/bots/{bot_id}/sessions`
是由该文件提供，还是由 bots 组件里某个 `{bot_id}` 形态的路由提供），也堵死了同一个
base 之下再容纳第二个 owner 的可能 —— BCS 正是从另一侧撞上同一问题，并以同样方式把自
己的控制面迁到了 `/openapi/v1/bots/collaboration/{bot_id}`
（`src/bcs/docs/plans/2026-08-03-bcn-collaboration-paths-design.md`）。本次在
`2026-08-03-openapi-v1-path-normalization` 规格中统一；测试
（`tests/…/openapi_v1/test_path_convention.py`）直接针对生成的文档断言该规则，因此违反
它的路由会在测试里失败，而不是留给评审去发现。

**保留名。** 由于 `bots` 组件保留了裸的 `/openapi/v1/bots/{bot_id}`，如果某个 Agent 的
id 恰好等于某个组件名，它在该地址上就不可达。这个集合是固定的，同一个测试会断言下面这份
清单仍然等于路由实际发布的字面量（英文版 `README.md` 中的同名清单是被解析的那一份）：

<!-- reserved-component-names -->
```text
approvals  ceiling  check-name  connection  engine  identity
mcp  models  resources  routines  sessions  skills
```

**先于路由保留的名字。** 另有一份独立清单 —— 在任何路由发布它们之前就已在此占位的名字。
它们的保留理由与上面那份**不同**：没有任何路由提供它们，因此当前也不存在"某个地址不可达"
的问题。保留是因为该地址已被别处占用，且我们确实打算在那里放一个组件，所以在此期间不能让
某个 Agent id 把它占走。

<!-- reserved-component-names-unrouted -->
```text
messages
```

- `messages` —— 网关在 `/openapi/v1/bots/messages/**` 上提供 Agent 的聊天 WebSocket，
  并中继到 engine proxy（`src/gateway/configs/application.yaml`）。该占用**只在 socket
  平面**上成立，因此发往该地址的 HTTP 请求仍会到达本服务；这个名字是为将来要放在那里的
  HTTP 端点保留的。参见
  `src/gateway/specs/2026-08-03-gateway-path-specific-domain-routing/`。

一旦有路由发布了这份清单里的某个名字，就必须把它移到上面那份已路由的清单里 —— 约定测试会
断言两份清单互不相交，因此"加了路由却没搬名字"会在测试里失败，而不是留给评审去发现。

> **挂载顺序是有承重作用的。** `build_public_router()` 会先挂字面量子组，再挂 bots 组，
> 这样 `/openapi/v1/bots/resources` 才能排在通配的 `/openapi/v1/bots/{bot_id}` 前面被
> 解析。如今真正依赖它的只剩下那些提供单段集合根的组件（`resources`、`routines`，以及
> bots 自己的 `check-name`/`ceiling`）—— 其余组件都只在两段及以上才可达 —— 但新增的组
> 仍应放进 `_SUBGROUPS` 列表里、位于 bots 路由之前，而不是每次都去重新推演这个例外。

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

### ❌ channels —— **已删除（2026-08-03）**
该组件已整体删除：路由、schema、包、挂载项，以及它发布出去的两条路径。它从未被实现；
而与"尚未动工的组件"不同，它是**已发布**的 —— 集成方在服务端文档里看到一套渠道 API，
每次调用却得到 500。搁置保留了这份代价，却没有换来任何好处。

重新加回来所需要的东西并没有丢：那 6 个操作（钉钉渠道配置 CRUD + 状态切换）记录在本文
2026-07-27 的历史与删除它的 PR 中。如果渠道要回来，它应当作为一个经过设计的组件回来，
而不是复活一个桩。

_注：桩里的列表返回 `Envelope[list[Channel]]`（不是 `Page`）；PR #363 里写的是
`Page[Channel]`。接线时请确认用哪种。_

### ✅ totalfrank · P1 —— mcp（6 个端点）· `openapi_v1/mcp/router.py` —— **已实现（PR #610）**
市场 + 租户 + 调用者的统一 per-server 配置。6 个端点全部接到内部 MCP 服务，经由从内部
路由抽取出来的共享 `core/mcp/` 流程（抽取后两套界面回答一致）；用 `caller_owner_id`
做 owner 作用域，由 Stage 5 守卫做租户作用域。
| 方法 | 路径 | 用途 | 成功响应 |
|---|---|---|---|
| GET | `/openapi/v1/bots/mcp/servers` | 列出市场 MCP 服务器（`keyword`、分页） | `Envelope[Page[McpServer]]` |
| GET | `/openapi/v1/bots/mcp/tenants` | 列出 MCP 租户 | `Envelope[list[McpTenant]]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}` | 服务器详情 | `Envelope[McpServerDetail]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/permissions` | 查询调用者对该服务器的权限 | `Envelope[McpPermission]` |
| GET | `/openapi/v1/bots/mcp/servers/{server_code}/config` | 读取调用者的统一服务器配置 | `Envelope[McpConfig]` |
| PUT | `/openapi/v1/bots/mcp/servers/{server_code}/config` | 写入配置（下发到设备） | `Envelope[McpConfig]` |

_已定案的决策（PR #610）：路径保持嵌套（`/openapi/v1/bots/mcp/...`）；写入体去掉了
`sync_mode`（不存在单设备下发路径 —— `extra="forbid"` 让它变成 422）；下发设备失败会回滚
写入并返回 502（与内部界面一致）；`endpoint_env`/`transport_protocol` 为严格枚举
（`PROD`/`PRE`、`SSE`/`STREAMABLE_HTTP`）。**保留 fail-open：** 市场调用异常时仍报告
调用者"有权限"（该端点仅供参考，真正的强制点是 MCP 服务器本身）—— 已用测试钉住，使其读起来
是一个决策而非 bug。_

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

_注：upload 已定稿为原始 `application/octet-stream` body（非 multipart）。与 PR #363 总览的 multipart 描述不一致——实现以路由为准，若后续需改 multipart 是契约 PR。_

### 🟪 totalfrank + lucas-xzp · P3 —— skills，共担（7 个端点：桩里 5 个 + 提议新增 2 个 ★）· `openapi_v1/skills/router.py`
一个组件下的两类资源：全局目录在 `/openapi/v1/bots/skills/catalog`，某个 Agent 已安装的
技能在 `/openapi/v1/bots/skills/{bot_id}`。

> **目录为什么需要一个字面的 `catalog` 段。** 按寻址规则，带 Agent 作用域的那一类占用
> `/openapi/v1/bots/skills/{bot_id}`；而目录详情若写成 `/openapi/v1/bots/skills/{skill_id}`，
> 就会以不同含义占据同一个位置 —— 同一深度上的两个通配，任何顺序规则都无法区分。`catalog`
> 用的正是本界面已经在 `check-name`、`ceiling` 上使用过的手法。让字面量排在 `{bot_id}`
> 之前靠的是路由内的声明顺序，文件里已写明这一点。

> **共担 —— 最棘手的类别。** skills 有三层生命周期（全局**上传** → per-bot **安装** →
> per-bot **启用/停用**）、两个尚未纳入桩的 ★ 端点，以及一个悬而未决的问题：后端更丰富的
> skill-set 模型是否要升为一等公民。因此**两人共担**。动手前先商定一份共同的子计划 ——
> 例如把 目录/上传 与 per-bot 安装/生命周期 分开 —— 并为它单独走一遍 SDD。在各自的 P1/P2
> 切片之后再做。

**状态**列标明每个端点是已经在路由桩里（`桩内`），还是来自 PR #363 的提议新增
（`★ 提议` —— 尚未在桩里；实现前请与 totalfrank 确认）。

| 方法 | 路径 | 用途 | 成功响应 | 状态 |
|---|---|---|---|---|
| GET | `/openapi/v1/bots/skills/catalog` | 技能目录（`keyword`、分页） | `Envelope[Page[Skill]]` | 桩内 |
| GET | `/openapi/v1/bots/skills/catalog/{skill_id}` | 技能详情 | `Envelope[SkillDetail]` | 桩内 |
| POST ★ | `/openapi/v1/skills/upload` | 上传自定义技能（全局，归属调用者） | `Envelope[Skill]` | ★ 提议 |
| GET | `/openapi/v1/bots/skills/{bot_id}` | 列出某个 Agent 已安装的技能 | `Envelope[list[BotSkill]]` | 桩内 |
| POST | `/openapi/v1/bots/skills/{bot_id}` | 为 Agent 安装技能（默认启用） | `201 Envelope[BotSkill]` | 桩内 |
| PATCH ★ | `/openapi/v1/bots/skills/{bot_id}/{skill_id}` | 启用/停用已安装技能（`status`） | `Envelope[BotSkill]` | ★ 提议 |
| DELETE | `/openapi/v1/bots/skills/{bot_id}/{skill_id}` | 卸载（解除绑定） | `Envelope[Deleted]` | 桩内 |

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
| GET | `/openapi/v1/bots/identity/{bot_id}` | 列出身份文件（含是否存在） | `Envelope[IdentityFileList]` |
| GET | `/openapi/v1/bots/identity/{bot_id}/{file_type}` | 读取单个身份文件 | `Envelope[IdentityFile]` |
| PUT | `/openapi/v1/bots/identity/{bot_id}/{file_type}` | 覆写单个身份文件（`content`） | `Envelope[IdentityFileRef]` |

### ⬜ 未分配 · Track C —— engine 运行时（16 个端点）
这不是一个 Track B 类别 —— 它们包装的是 Bot 设备上的 **engine adapter**，
而不是某个后端服务。逐端点清单、每个端点对应的 engine 路由，以及那约 72 条
*不*包装的 engine 路由的裁定，都在
**[`engine-surface.zh-CN.md`](engine-surface.zh-CN.md)**。摘要：

| 组 | 端点数 | 公共路径 |
|---|---|---|
| sessions | 7 | `/openapi/v1/bots/sessions/{bot_id}…` —— 仅 personal bot |
| engine | 3 | `/openapi/v1/bots/engine/{bot_id}/{status,capabilities,available}` |
| models | 2 | `/openapi/v1/bots/models/{bot_id}`、`…/{bot_id}/{model_id}` |
| approvals | 3 | `/openapi/v1/bots/approvals/{bot_id}/mode`（GET/PUT）、`…/modes` |
| connection | 1 | `/openapi/v1/bots/connection/{bot_id}` —— 完整 WS URL，取代 `get_device_connection` |

---

## 完成的定义（整个 `/openapi/v1` 工作）

1. **Track A：** 每一类数据（bots、resources、skills、mcp、routines）都带有
   `avernet_tenant` 并被守卫，Stage-1 的测试形态全绿。—— _6 个里完成 2 个（bots ✅、mcp ✅ PR #564）。_
2. 全程内部 API 保持不变（`to_dict()` 无泄漏；内部套件不作修改）。—— _仍然成立：#494
   时整个 `tests/community` 全绿（9171 通过，3 跳过）。_
3. **Track B：** 七个 `/openapi/v1` 类别的处理器均已实现且租户安全，各自带测试 + PR。
   —— _7 个里完成 2 个（bots ✅、mcp ✅）。_
4. F2 租户前导索引就位（强制策略）。—— _⬜_
5. 后台/定时任务已针对按租户正确性完成复查。—— _⬜_
6. `require_principal` / `resolve_avernet_tenant` 已接到真实验证器（认证工作线）——
   到此，第二个租户才能安全地持有真实数据，公共界面也才会停止一律返回 401。
   —— _✅ 两半均已合并（[#634](https://github.com/inclusionAI/Avernet/pull/634)、[#599](https://github.com/inclusionAI/Avernet/pull/599)），`user` 调用方已可往返。
   接纳**外部租户**如今是一个独立且需要刻意为之的步骤：`route_security` 必须接受其凭证，
   **并且**委托必须给这份凭证一个可用于作用域的终端用户（认证设计 §15）。_
7. **跨租户的外部身份问题已定案（[#556](https://github.com/inclusionAI/Avernet/issues/556)）** —— Passport、授权关系与 BCN 都带上
   租户维度，从而可以在公共路径上重新打开 BCN 同步。—— _⬜（2026-07-29 新增；它是开启
   多租户的前置闸口）。_
8. **Track C：** 五个 engine 运行时组（16 个端点）均已实现、按 owner 收敛且能力感知，
   并且 `…/connection` 返回 socket URL，使任何外部调用方都看不到 proxypass target
   或裸设备 token。—— _✅ 5 组全部完成（PR #630）。与其它类别一样，在第 6 项落地
   之前一律返回 401；singlebox E2E 流也阻塞在同一个事件上。_

---

## 横切的延后事项

- **F2 —— 租户前导索引（强制的公司策略）。** 带有租户列的表必须有租户前导索引。延后到专门的
  索引工作中；**多租户上线前必须完成**。做的时候：把 `avernet_tenant` 前置到支撑查询的复合
  索引上（`idx_owner` → `(avernet_tenant, owner_id)`、`idx_bot_id_entity_id`、
  `idx_entity`、搜索索引），采用**先建新、再删旧**（命名约定把索引名与其列绑定，因此先建后删，
  避免出现无索引的窗口）。低基数索引（`idx_status`、`idx_is_delete`）与唯一查找索引
  （`idx_binding_id`）保持不动。
- ~~**真实的调用方身份验证器。**~~ **两半均已完成** —— 见上文**认证接缝**一节。
  `caller_owner_id` 本来就接受带 `user_id` 的对象，这正是它能零改动落地的原因。网关
  [#599](https://github.com/inclusionAI/Avernet/pull/599) 已合并，线上形状也已通过往返
  验证，`user` 调用方端到端可用。
- **`app` / `access_key` 调用方的 owner 语义。** 仍未定，但现在是显式的：这类调用方在
  校验阶段即被**拒绝**（2026-08-02），而不是悄悄地没有作用域。要定它属于委托工作线
  （认证设计 §15）；只加一条 `route_security` 规则是不够的 —— 一个指不出人的凭证，
  依然无法按 owner 作用域。
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
- **2026-07-27** —— 按**纵向切片**完成分工（无跨人阻塞）：**totalfrank** = bots、
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
- **2026-07-28** —— **Track A 阶段 2（ac_resource）DONE**（lucas-xzp，分支 `rongzhi_0727`，pending rebase/push）。把 PR #456 的 BotModel guard 工厂化到 `(BotModel, ResourceModel)`：单 Session read listener 链式 `with_loader_criteria`（直接表达式非 lambda）+ per-mapper `before_insert`。红→绿：resource tenant isolation/guard 以及 routines/identity 间接隔离测试。`to_dict()` 不暴露 tenant。**DDL（ac_resource ADD COLUMN）已由 lucas-xzp 在平台提交工单，部署前须先落地；`ac_bot_publish` 经核实本期 openapi_v1 handler 不读，留待 service_bot owner 或后续 verify/online 阶段。** 阶段 6（例程）保持 ⬜ TODO——无表，靠 ac_bots 间接隔离已由 Session 0 覆盖，真 DONE 留给 Track B routines handler 接通时。
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
- **2026-07-29** —— **Track A 阶段 5（MCP 配置）完成 —— PR #564。** 隔离了两张表：
  `ac_user_mcp_config` 与 `ac_bot_mcp_call_config`。Track B 的 `mcp` 已解除阻塞。
  在照搬这个阶段之前，有四点值得先了解：
  1. 阶段 1 的守卫现在是**与模型解耦**的 —— 即 `utils/avernet_tenant_guard`
     配合 `register_avernet_tenant_guard(Model)`。后续阶段只需注册，不必重新实现。
     `plugin_api/models.py` 中已不再保留守卫的实现体。
  2. **先检查你的表上有没有包含被隔离维度的唯一键。** `ac_user_mcp_config` 上原本是
     `UNIQUE (user_id, server_code, env)`，这会导致"两个租户、同一个用户工号"在**写入**
     时报重复键错误 —— 哪怕读取侧的隔离完全正确。`ac_bots` 没有唯一键，所以阶段 1
     从未遇到这个问题。该键必须以 `avernet_tenant` 打头。
  3. 它的**上线时间点与加列不同**：加列必须赶在代码发布之前，而换键只需赶在第二个租户
     写入之前。两者都记录在上文新增的"带外执行的库表变更"一节中。
  4. **先摸清范围，再动手隔离。** 六个 `mcp` 端点里有四个其实是走 HTTP 调 MCP Center，
     根本没有本地表；而配置写入链路确实会碰到的 `ac_entity_device_binding` 则不需要任何
     改动 —— 因为那条查询是 JOIN 到 `ac_bots` 上的，而 `with_loader_criteria` 对 JOIN
     子句同样生效。这是实测确认的，不是推断。
- **2026-07-29** —— **渠道降级（并非取消）**，Track A 阶段 3 与 Track B 端点均已搁置，
  范围保持不变。
- **2026-07-30** —— **Track B mcp 合并（PR #610）—— 第二个落地的公共类别（7 个里 2 个）。**
  6 个 `/openapi/v1/bots/mcp` 端点全部接到内部 MCP 服务，经由一套从内部 `/api/mcp` 路由
  **抽取出来的共享 `core/mcp/` 流程**（`presentation.py` 里的掩码 / `extInfo` 剥除 /
  网络类型白名单；`config_flow.py` 里的 写入→下发→回滚 与 读取；`errors.py` 里的类型化
  领域错误），使两套界面回答一致。内部路由现在调用这套流程 —— 由
  `test_mcp_config_internal_unchanged.py` + `test_mcp.py` **保持不修改**通过来证明行为保持。
  公共处理器用 `caller_owner_id` 做 owner 作用域，由 Stage 5 守卫做租户作用域（跨租户配置
  不可见且不可覆写，已通过流程针对真实守卫验证）。决策：路径保持嵌套（**为 mcp 定案了路径
  分歧**）、去掉 `sync_mode`（`extra="forbid"` → 422）、下发失败回滚 → 502、严格枚举
  `endpoint_env`/`transport_protocol`（无 `DEV`）。**保留 fail-open** 的权限行为（市场异常时；
  仅供参考的端点），已用测试钉住。看板已挪动：Track B mcp → 完成；"参考样板"清单现在把这里
  列为第二个样板，专门示范*抽取共享逻辑*的模式。
- **2026-07-30** —— **网关 principal 校验落地（后端这一半）。** PR #456 埋下的两处接缝
  变成了真的：`require_principal` 校验网关签发的 `X-Avernet-Principal`（HS256、
  `aud=backend`、`principals[]`），`resolve_avernet_tenant` 从中读出租户 —— **没有改动
  任何 handler、路由或中间件**，这正是当初留这两处接缝的目的。新增
  `core/gateway_principal/`（我们自己的 DTO + 验证器，不 import 网关类型）与
  `utils/gateway_principal_config.py`（环境变量，**没有 dev 兜底密钥** —— 未设置即 401）。
  是对着网关 PR [#599](https://github.com/inclusionAI/Avernet/pull/599) 的契约写的，
  没有等它合并 —— 因为 #599 明确把组件侧验证器留给我们。刻意加的守卫：线上传来的租户是
  `teamclaw` 一律拒绝、身份集合租户不一致一律拒绝、转发过来的密钥不做投影、"没带凭据"与
  "凭据非法"的 401 逐字节一致。一处必要的连带修复：查表逻辑挪进
  `responses.py::mapped_error_response`，应用 catch-all 改为查同一张表 —— 因为接缝是在
  *依赖*里抛错的，不改的话未认证的公共请求会返回 500 而不是 401。看板已挪动：横切的验证器
  行与 DoD 第 6 项 → 后端这一半完成。**上游仍有闸口**：#599 合并，以及
  `route_security.yaml` 允许本界面真实的调用方；另外 `app` / `access_key` 调用方在有人
  就"它们归属于谁"定案之前一律 401。SDD：
  `src/backend/specs/2026-07-30-gateway-principal-verifier/`。
- **2026-07-30** —— **Track C 已实现（PR #630）** —— 五个组共 16 个 engine 运行时
  端点，外加 `core/engine_runtime/`（relay 与 connection service）及其 Service API
  Protocol。动这条主线或参考它之前，有七点值得知道：
  1. **Track C 不改动自己前缀之外的任何契约。** 曾经加过 `Envelope.warning` 又移除：
     两个 OSS 引擎里，本面唯一能触达的 *limited* 能力只有 `claude_code` 的
     `SESSION_CREATE`，而那条说明讲的是 session key 如何建立，并非结果不完整 ——
     该字段会在 16 个端点里的 15 个、以及其余六个类别上永远为空。`501`/`504`
     放进按组的字典，也是同一个道理。
  2. **engine 自己的文案永远不会到达调用方。** 能力说明与 `limited`/`fallback` 的
     解释是内部工程文案且不总是英文；只发布能力**名字**。字段描述与 docstring 会被
     原样发布进 OpenAPI 文档，所以理由要写在 `#` 注释里 —— 现已有测试在发布文本里
     出现内部标记时让构建失败。
  3. **sessions 组只服务 `personal` bot。** engine 在会话列表上接受 `user_id`、
     记日志、然后**丢弃**它，因此设备会返回它持有的全部会话。在 `service` bot 上
     那就是所有 caller 的。门禁在转发**之前**判定，不是事后过滤。
  4. **`GET /api/engine/status` 是唯一没有信封的 engine 路由** —— 它原样返回
     `EngineManager.status()`。把它当作有信封处理，会让每一次对健康设备的调用都失败。
  5. **engine 的任何 404 都是"资源不存在"，不是"能力缺失"。** 传输层对未知
     session id、未知 model id 抛的是同一个 not-found 错误；映射成 501 会告诉调用方
     它的 bot 失去了 sessions 能力。
  6. **隔离是"别把它弄丢"，不是"把它建起来"。** 没有表、没有 DDL、没有 Track A
     阶段 —— 但守卫看不见设备调用，所以隔离测试在全部 16 条路由上断言的是
     **传输层从未被调用**，而不只是返回了 404。
  7. **singlebox E2E 流阻塞在认证工作线上**，不在本模块 —— 所有 `/openapi/v1`
     路由都返回 401，流只能断言 401。在网关校验器落地之前，`engine_runtime`
     继续留在 `SINGLEBOX_E2E_EXEMPT` 里。
- **2026-07-30** —— **新增 Track C —— 公共 API 现在也包装 engine。** 此前前端从
  `get_device_connection` 拿到连接，再经 `/proxypass/{target}` 自己去调 Bot 的
  engine adapter；那次移交会对外发布 proxypass 拓扑和裸设备 token，并且让
  engine —— 一个从未被设计成公共契约的东西 —— 成为集成方直接编程的对象。Track C
  改为把 engine 面向客户端的 HTTP 包装到 `/openapi/v1/bots/{bot_id}/…` 之下。
  有五点值得知道：
  1. **是 16 个端点，不是 89 个。** engine 在 25 个 router 中提供 89 条 HTTP 路由
     + 6 个 WS。范围规则只包装前端*直连*的那些（sessions 7、engine 3、models 2、
     approvals 3），外加一个新增的 `…/connection`。经由后端的 engine 路由仍归
     已经在其之上的后端契约；**nodes 已移除** —— 前端确实 proxypass 了它，按规则
     本应包装，但产品并不需要在公共面上暴露节点清单。
  2. **`/api/cron` 早就是这个形状了。** 后端 `/api/cron` → `CronRelayService` →
     `DeviceAdapterTransport` → engine 一直在生产上跑，而 `routines` 的桩已经
     import 了 `CronRelayServiceProtocol`。Track C 是把既有形状一般化，不是发明
     一个新的。
  3. **没有 Track A 阶段，没有 DDL** —— 这是第一条在构造上就成立（而非碰巧成立）
     的主线。已在开头加了提示，免得有人去找一个不存在的阶段。
  4. **WebSocket 不包装。** `…/connection` 交还一条可直接使用的 `wss://` URL，
     凭据就在 URL 里，socket 由调用方自己持有。不做 `POST /chat`，也不转发
     engine 的帧格式。
  5. **两处排除是契约决策，不是偷懒。** 包装 `engine/switch` 会成为绕过 #494
     `engine` 不可变裁定的后门；包装 `engine/restart` 会让同一个 bot 有两个重启动词。

  完整清单、逐端点映射，以及每一条未包装 engine 路由的裁定：
  **[`engine-surface.zh-CN.md`](engine-surface.zh-CN.md)**。看板变动：新增 Track C
  小节（6 组完成 0 组），新增 DoD 第 8 条。**负责人仍未分配。**
- **2026-08-02** —— **身份接纳定案：仅 `user`。** `verify_principal_token` 现在会拒绝
  任何不指向终端用户的身份集合，因此 `bot` / `app` / `access_key` 调用方是**按设计**
  返回 `401`，而不是因为 `caller_owner_id` 拿到空 owner 才抛错。四点值得知道：
  1. **拒绝之所以挪位置，是因为原来的位置可以被绕过。** 来自 `caller_owner_id` 的 401
     只覆盖会调用它的 handler —— 而 `resources/router.py` 里有四个不调用
     （`list_resources`、`create_resource`、`get_resource`、`update_resource`），它们按
     调用方自带的 `bot_id` 作用域。无法作用域的调用方能进到这些 handler 并拿到数据。把
     拒绝放到校验阶段，这条规则对所有路由都成立，包括还没写的。**每个 handler 都得记住
     的不变量，不是不变量。**
  2. **`bot` 失去了 owner 兜底。** `VerifiedCaller.user_id` 不再回退到 `bot.owner_id`。
     它原本不可达（没有任何 `route_security` 规则要求或接受 `bot`），但那是一份没人决定
     过的常设授权 —— 让 bot 在整个公共契约上以其 owner 的身份行事。要做面向 bot 的界面，
     应当刻意重新加回来。
  3. **user **加上**其他身份依然被接受。** 网关会转发它解析出的整个集合，因此声明
     `user: required, app: optional` 的路由会产生两个 principal。规则是"必须含 user"，
     不是"不能含别的" —— 后者会拒掉网关认为合法的请求。
  4. **认证现在挂在 `build_public_router()`**，与 `ERROR_RESPONSES` 并列，因此以后新增
     的路由**在构造上**无法遗漏，而不是靠 `test_public_routes_require_principal` 事后
     抓到。行为零变化 —— 每个路由本就声明了 `PrincipalDep`，且 FastAPI 会缓存依赖，
     每请求只解析一次。

  本次同时更正了看板：网关 [#599](https://github.com/inclusionAI/Avernet/pull/599)
  **已合并**（此前有三处仍记为未合并）；principal 线上契约已通过把真实网关签名器接进
  真实后端验证器做往返验证 —— 形状一致、转发来的机密不外投、`aud`/`iss` 不符即拒。
  **新记为待办：** 没有任何跨仓测试钉住这份契约，任一侧改字段名都会让两边测试保持绿色
  而线上全 401。整套测试 10204 通过 / 3 跳过。SDD：
  `src/backend/specs/2026-08-02-public-api-user-only-principal/`。

- **2026-08-03** —— **`/openapi/v1/bots` 路径规范化 + 删除 channels。**
  每个组件的路由现在都位于 `/openapi/v1/bots/<component>/…` 之下，`{bot_id}` 作为组件
  **之内**的第一段 —— 见新增的**寻址规则**一节，新增组件前应先读它。此前并存三种形态，而
  其中只有一种是原本设计的那种：`identity` 多带一段冗余的 `/bot/`；`connection`、
  `engine`、`approvals`、`sessions`、`models`、`skills` 把 `{bot_id}` 放在了自己的组件名
  之前 —— 这让那些路由文件无法自述地址，也让共享 base 之下再容纳第二个 owner 变得不可能
  （BCS 从另一侧撞上同一冲突，并以同样方式解决）。`skills` 另外获得一段字面的 `catalog`，
  否则它的两类资源都会去争 `/openapi/v1/bots/skills/{…}`。`channels` 是删除而非搁置。
  已发布路径 41 条（此前 43 条）。handler、schema、状态码、鉴权规则与租户隔离规则均未改变
  —— 只改地址，且**不提供兼容别名**：该界面尚无可达的外部调用方，因此没有需要保留的契约。
  网关钉住的 `bots.openapi.json` 经真实兼容性闸门（`--allow-breaking`）重新生成；它自
  Track C 起就已过期，只有 32 条路径，而后端发布的是 43 条。新增测试
  `tests/…/openapi_v1/test_path_convention.py` 针对生成的文档断言该规则以及本文的保留名
  清单，使两者都在测试而非评审中失败。SDD：
  `src/backend/specs/2026-08-03-openapi-v1-path-normalization/`。
