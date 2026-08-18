# Bot 工坊 OpenAPI 接入矩阵与分工

- **日期**：2026-08-18
- **核对分支**：`bot_workshop_reconstruction_v1`
- **核对基线提交**：`f167df1285c0ee7e2b985619e94d28fb6113dfc6`
- **核对方式**：上述基线提交 + 本轮工作区变更（最终提交 SHA 以合入记录为准）
- **文档性质**：开发对账与任务分工文档，不替代正式 API Contract 或领域 Spec。
- **主要依据**：
  1. `docs/superpowers/specs/2026-08-12-bot-workshop-openapi-inventory.md` 的产品清单；其中旧的“组件在前、`{bot_id}` 在后”路径约定已经过时，新增 Bot-scoped 接口以当前 Bot-first 规范为准。
  2. `src/backend/src/agentclaw/community/adapters/http/openapi_v1/admission.py`。
  3. `src/gateway/configs/schemas/bots.openapi.json`。
  4. Backend、Engine、BaaS、BCS 中各领域的 Service API、Core 和内部 Adapter。
  5. `docs/arch/arch.rules.md` 与仓库 `AGENTS.md` 的领域所有权和分层约束。

---

## 1. 定位与架构边界

### 1.1 Bot 工坊是产品聚合面，不是单一领域

Bot 工坊会消费多个独立领域的公开能力，包括：

- Bot Inventory / Bot Management
- Desktop Bot
- Service Bot Publication
- Engine Runtime
- BaaS/PaaS Runtime
- Quality / Evaluation
- Harness / 任务护航与健康诊断
- Channel
- Skill Center / Skills Pool / MCP
- Device Filesystem / Workspace Runtime
- BCS State Machine
- Business Space
- Render Screen

因此：

- “Bot 工坊尚未接入某能力”不等于“该能力属于 Bots Core 且尚未实现”。
- URL 带 `bot_id` 只是寻址方式，不改变领域所有权。
- OpenAPI Adapter 只负责身份解释、请求校验、调用 Service API、错误映射和统一响应，不重新实现上游业务规则。
- Flow、Runtime、Containers 等能力可以由 Gateway 直接路由到领域 Owner，或通过 Backend 的授权 facade 转发；不能默认所有能力都进入 `bots/router.py`。

### 1.2 新增路径规范

新增 Bot-scoped 接口统一使用 Bot-first：

```text
/openapi/v1/bots/{bot_id}/<component>
```

例如：

```text
/openapi/v1/bots/{bot_id}/channels
/openapi/v1/bots/{bot_id}/evaluations
/openapi/v1/bots/{bot_id}/render-screens
/openapi/v1/bots/{bot_id}/skill-sets
/openapi/v1/bots/{bot_id}/nodes
```

> **不再单独新增 `/openapi/v1/bots/{bot_id}/files` 接口**。容器/沙箱目录树的需求统一并入
> `/openapi/v1/bots/{bot_id}/resources` 模块:`ResourceFileService` 作为 facade,按
> `bot_type` 在内部 选择真源(personal/desktop 走 workspace file tree;service bot 走
> publish-sandbox `read-only/tree`);写操作经 `read_only_rules`(来自
> `ac_bot_publish.ext.read_only_rules`)与 service-bot 全路径只读策略后 **403 fail-closed**
> 拒绝。详见 §3.1.1 与 §6.7。

如果资源本身有独立身份和生命周期，应优先采用独立领域路径，例如：

```text
/openapi/v1/channels
/openapi/v1/evaluations
/openapi/v1/diagnostics
/openapi/v1/render-screens
/openapi/v1/spaces
/openapi/v1/flows
```

不再为新接口采用以下旧式路径：

```text
/openapi/v1/bots/channels/{bot_id}
/openapi/v1/bots/evaluation/{bot_id}
/openapi/v1/bots/nodes/{bot_id}
```

### 1.3 公开接入的完成标准

一项能力只有同时满足以下条件，才能标记为“工坊已接入”：

1. 领域 Owner 的 Service API 或上游 Contract 已就绪。
2. OpenAPI Adapter 已注册且保持薄适配。
3. 身份、用户作用域、Bot Owner/Collaborator/App grant 规则已明确。
4. 成功和错误响应符合统一 `Envelope`：`code`、`message`、`data`、`request_id`。
5. `admission.py` 与 Gateway `route_security` 的身份策略一致。
6. 生成 OpenAPI 与 Gateway served schema 已同步。
7. Avernet 与 OCB/Sofapy 两份 Gateway 配置均已更新。
8. Contract、权限、Gateway 和必要的 E2E 测试已通过。

---

## 2. 分工原则

### 2.1 A 线

A 线负责：

- 个人云端 Bot。
- 本地 Desktop Bot。
- 工坊 Bot Inventory 壳层和聚合视图。
- 沉寂 Bot 激活、初始化配置等个人 Bot 操作。
- Business Space 在 Bot Inventory 壳层中的消费，包括 `/all` 的空间上下文和筛选；独立 Spaces OpenAPI 与 Bot 迁移归 B 线接入。
- A 线已实现接口的 OpenAPI、admission、Gateway schema 和联调收口。

A 线不重新实现：

- 业务空间成员和团队 CRUD。
- Runtime 日志、任务护航、健康诊断和评测领域逻辑。
- SkillSet、Skills Pool 和 MCP 领域逻辑。
- BCS Flow 状态机。

### 2.2 B 线

B 线负责：

- Service Bot lifecycle。
- Service Bot 发布、预发、上线、下线、重试等工坊入口。
- Service Bot edit-lock。
- Containers summary、实例状态和重启的工坊接入及 BaaS 上游协调；实例日志仍由日志团队实现。
- 后续 Service Bot editors/协作者入口；实施前必须等待空间成员模型和统一协作契约明确。
- 编辑页内核中除 Skills、任务护航/日志/评测外的工坊接入：Container files、Channels、Nodes、Render screens。
- 独立 Spaces OpenAPI 和 Bot 跨空间迁移的工坊接入；Business Space 实体、成员和权限规则仍由领域 Owner 提供。

B 线不在 Backend 重建 BaaS/Engine/Channel/Render/Business Space 的领域状态。B 线负责的是工坊公开契约、Bot/Stage 授权、薄 Adapter 或 relay、Gateway 配置和联调；上游领域逻辑仍由对应 Owner 实现。

Flow 若用于任务护航，按本次分工交由任务护航团队牵头、BCS 提供 State Machine 能力，B 线不承担其实现。Skills/per-bot MCP 由 Skills 相关同学实现，B 线只配合 Service Bot 上下文联调。

### 2.3 其他团队或其他同学

以下能力不由 A/B 线实现其领域逻辑：

| 能力 | 实施方/领域 Owner | A/B 线责任 |
|---|---|---|
| Runtime logs | 日志/Runtime Observability 相关团队 | 跟进 Contract、核对工坊入口，不实现日志采集和存储 |
| 任务护航、健康诊断、Health Check | 任务护航/Harness 相关团队 | 跟进公开契约和工坊联调，不实现诊断算法 |
| Evaluation | Quality/任务护航相关团队 | 跟进评测入口和结果页契约，不实现评测任务领域 |
| Skill sets、引用型 Skill、per-bot MCP | Skills 相关同学 | A/B 线不实现 Skill/MCP 领域逻辑；只配合 Bot 身份、权限和工坊联调 |
| Flow（任务护航 DAG/运行历史） | 任务护航团队牵头，BCS 团队提供 State Machine | A/B 线只提供 Bot/用户上下文并配合联调，不实现任务护航或状态机 |
| Nodes | Engine 团队 | B 线负责工坊接入/授权 relay；Engine 实现 Node Contract 和运行态能力 |
| Channels | Channel 模块 Owner/相关同学 | B 线负责工坊接入；Channel Owner 先补 tenant/owner/bot guard 和领域 Contract |
| Render screens | Render Screen 模块 Owner/相关同学 | B 线负责工坊接入；Render Screen Owner 提供领域 Contract |
| Container files | Device Filesystem/Runtime Owner，具体执行人待确认 | B 线负责工坊接入；Runtime Owner 提供文件 Contract，禁止直接操作引擎物理路径 |
| Business Space 实体与成员 | Business Space/管理后台 Owner | A 线消费空间上下文；B 线接入独立 Spaces API/迁移；领域 Owner 实现实体、成员和权限规则 |

---

## 3. 当前代码快照

基于本文件顶部提交：

| 维度 | 当前值 | 说明 |
|---|---:|---|
| `bots.openapi.json` 唯一路径 | **101** | Gateway served schema |
| `bots.openapi.json` HTTP operations | **140** | GET/POST/PUT/PATCH/DELETE 等操作总数 |
| `admission.py` 注册项 | **100** | 包括一个 WebSocket admission |
| A 线清单中已实现 operations | **14** | `data-init` trigger + status API 已闭环，真实环境 E2E 待验证 |
| B 线 lifecycle admission entries | **10** | 对应 8 个 served paths |
| B 线 edit-lock admission entries | **4** | 对应 2 个 served paths |

统计时必须区分：

- 功能事项数；
- 唯一路径数；
- HTTP operation 数；
- admission entry 数。

不能把这四种计数混为一个“接口数”。

### 3.1 Bot 工坊二级页面端到端对账

本节按“从工坊列表进入 Bot 详情/编辑页后，页面实际需要什么”重新核对，
而不是按仓库中是否出现相似名词判断。结论是：**二级页面 OpenAPI 尚未齐备**。
已有能力主要覆盖 Bot 基础信息、Engine、调试会话、模型、审批、Service
lifecycle 和 edit-lock；编辑页内核及运行治理仍有整块缺口。

判定口径：

- `INTEGRATED`：Backend 公开 Router、admission、Avernet Gateway 路由/鉴权、
  pinned schema 和上游 Service API 均已具备。
- `OWNER_ONLY`：公开接口存在，但当前只能按 Owner 自有 Bot 使用，不能直接认定
  Service Bot 协作者的二级页也可用。
- `SEMANTIC_MISMATCH`：存在近似公开接口，但资源含义不同，不能替代页面需求。
- `DOMAIN_EXISTS`：只有内部 `/api`、Core 或 Protocol；尚无工坊公开 OpenAPI。
- `MISSING`：公开 Adapter、admission、Gateway/schema 均缺失，并可能还缺稳定上游 Contract。

#### 3.1.1 已有或可复用能力

| 二级页区域 | 当前公开能力 | admission / 协作者语义 | Gateway / schema | 结论 |
|---|---|---|---|---|
| Bot 基础信息、改名、描述、状态、Passport | `GET/PUT/DELETE /openapi/v1/bots/{bot_id}`、`/status`、`/passport` | `GRANT_CHECKED_OWN_BOT` | 已由 `/openapi/v1/bots/**` 转发并进入 pinned schema | `OWNER_ONLY`；Owner 页面可用，不能作为 Service Bot 协作者详情页的完整 bootstrap |
| Engine 状态、能力、候选、重启 | `/{bot_id}/engine/status`、`capabilities`、`available`、`restart` | `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED` |
| Engine 配置 | `GET/PUT /{bot_id}/engine/config`，另保留 `engine-config` 兼容入口 | 当前 canonical config 为 `GRANT_CHECKED_OWN_BOT` | 已同步 | `OWNER_ONLY`；若协作者被允许修改发布配置，需要单独收敛权限 |
| 调试会话与消息 | `GET/POST /{bot_id}/sessions`、session 详情/更新/删除、消息列表/清空 | 全部 `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED` |
| 模型 | `GET /{bot_id}/models`、`/{model_id}` | `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED` |
| 审批模式 | `GET/PUT /{bot_id}/approvals/mode`、`GET /modes` | `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED`；仍受各 Engine capability 限制，可能返回 501 |
| Engine 连接诊断 | `GET /{bot_id}/connection` | `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED`；它提供连接信息，不是 Runtime logs 或健康诊断 |
| Identity / MD 管理 | `GET /{bot_id}/identity`、`GET/PUT /{file_type}` | `GRANT_CHECKED_OWN_BOT` | 已同步 | `OWNER_ONLY`；当前枚举与 Core 白名单一致，实际支持 16 种文件（含原需求所称 13 类），不再是“枚举待补” |
| Startup script | `GET/PUT/DELETE /{bot_id}/startup-script` | `GRANT_CHECKED_OWN_BOT` | 已同步 | `OWNER_ONLY` |
| 用户/工作区 Resources(本期起也承载容器/沙箱只读目录树) | `/{bot_id}/resources` 下 list/delete/stat/upload/download/preview/mkdir | `GRANT_CHECKED_OWN_BOT` | 已同步 | `OWNER_ONLY`;并已决定**不再单独新增 `/files` 接口**,Service Bot 沙箱只读目录树统一并入 `/resources`:`ResourceFileService` facade 按 `bot_type` 内部分支真源(personal/desktop → workspace file tree;service → publish-sandbox `read-only/tree`),写操作对 service bot 经 `read_only_rules`(`ac_bot_publish.ext.read_only_rules`)+ 全路径只读策略 **403 fail-closed**。落地需补:`service-bot write 操作 fail-closed 路径` 与 `read_only_rules 接入 ResourceFileService.is_readonly` |
| Routines | `/{bot_id}/routines` CRUD、run、runs | `GRANT_CHECKED_OWN_BOT` | 已同步 | `OWNER_ONLY`；属于定时任务能力，不等于任务护航 Flow |
| Local Skills | `/{bot_id}/skills` 及单 Skill 激活/停用/删除 | collection 支持 addressed bot；单 Skill 操作在 handler 内按记录做 grant check | 已同步 | 接口本身可用，但为 `SEMANTIC_MISMATCH`：不是能力集分组、引用型市场 Skill 或 per-bot MCP |
| 租户级 MCP 目录/配置 | `/bots/mcp/servers`、`tenants`、server config/permissions | 目录为 `OPEN`；账户配置要求 human，application 被拒绝 | 已同步 | `SEMANTIC_MISMATCH`：不是 per-bot MCP binding |
| Service lifecycle | `/{bot_id}/lifecycle` 下 upgrade/read/delete/approval/advance/restart/cancel-staging/offline/retry | 全部 `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED` |
| Edit lock | `GET/POST/DELETE /{bot_id}/edit-lock`、`POST /steal` | 全部 `GRANT_CHECKED_ADDRESSED_BOT` | 已同步 | `INTEGRATED` |

`IdentityFileType` 和 Core `VALID_IDENTITY_FILES` 当前均覆盖 16 项：
`RULES`、`OKR`、`SAFETY`、`SOUL`、`OUTPUT`、`MEMORY`、`IDENTITY`、
`AGENTS`、`USER`、`TOOLS`、`HEARTBEAT`、`BOOTSTRAP`、`KNOWLEDGE`、
`CLAUDE`、`GREETING`、`README`。因此旧清单 #35/#36 的“13 个 MD 待核”
已经可以关闭；真正遗留的是协作者访问语义，而不是文件枚举。

#### 3.1.2 缺失或只有内部能力的区域

| 二级页区域 | 公开 OpenAPI / admission / Gateway schema | 仓库上游现状 | 状态与负责人 |
|---|---|---|---|
| Containers summary / 实例状态 | 全部缺失 | BaaS 有设备/发布相关能力，但未形成工坊所需 summary + instances 稳定 Contract，且无 CPU/Memory metrics | `MISSING`；B 线接入，BaaS/PaaS Owner 补 Contract |
| 单实例重启 | 全部缺失 | 存在若干 BaaS restart 能力，但未形成按 Bot/Stage/instance 寻址且可证明幂等的公开 Contract | `MISSING`；B 线 + BaaS Owner |
| Runtime logs / 容器实例日志 | 全部缺失 | `/bots/logs/*` 是对话 trace，不是 Runtime 日志；日志来源和脱敏 Contract 未定 | `MISSING`；日志/Runtime Observability 团队，A/B 只配合上下文 |
| Evaluation | 全部缺失 | `QualityTaskServiceProtocol` 和内部 `/api/quality` 已有 | `DOMAIN_EXISTS`；Quality/任务护航同学建设公开契约 |
| Health score / Health check | 全部缺失 | Harness Core 与内部 `/api/harness` 已有 | `DOMAIN_EXISTS`；任务护航/Harness 团队 |
| Skill sets / 引用型 Skill / per-bot MCP | 全部缺失 | Skill Center、SkillSet Service、内部 `/api/skillsets` 和 MCP 关联能力已有 | `DOMAIN_EXISTS`；Skills 同学负责，不得把现有 Local Skills/MCP 目录误算为完成 |
| ~~Container files~~(已并入 `/resources`) | 不再单独建设公开 `/files`;旧内部 `/api/service-bot/read-only/tree` 仍可作 legacy 保留 | 内部 `/api/service-bot/read-only/tree` 可读目录树,但仍依赖 Runtime layout 和内部响应规范 | `FOLDED_INTO_RESOURCES`(见 §3.1.1 / §6.7);B 线不再为此单开公开接口;落地仍需在 `ResourceFileService` 加 service-bot sandbox 分支、`read_only_rules` 接入 `is_readonly` 以及写操作 403 fail-closed |
| Flow / runs | 全部缺失 | BCS 已实现 State Machine HTTP 路由，但正式 OpenAPI Contract 未覆盖工坊 Flow | `DOMAIN_EXISTS` / `CONTRACT_MISSING`；任务护航团队牵头，BCS Owner 提供状态机 Contract |
| Channels | 全部缺失 | `ChannelServiceProtocol` 与内部 `/api/channels` CRUD 已有 | `DOMAIN_EXISTS`；B 线接入，Channel Owner 先收敛 tenant/owner/bot guard |
| Nodes | 全部缺失 | Engine 有内部 `GET /api/nodes` 与 capability | `DOMAIN_EXISTS`；B 线接入，Engine Owner 提供字段 Contract/relay |
| Render screens | 全部缺失 | `RenderScreenServiceProtocol` 与内部 `/api/bot-render-screens` CRUD 已有 | `DOMAIN_EXISTS`；B 线接入，Render Owner 明确公开读/写范围 |
| Editors / 协作者 CRUD | 全部缺失 | edit-lock 已有，但成员来源、唯一管理员和空间权限 Contract 未完成 | `MISSING`；B 线后续批次 + Business Space/协作 Owner |
| Spaces list | `/openapi/v1/spaces` 缺失 | 当前只有 `NoopBusinessSpaceContext` personal fallback，prod Service API 未接入 | `MISSING`；B 线接入 + Business Space Owner |
| Bot 跨空间迁移 | `/{bot_id}/migrations` 或独立 migration resource 均缺失 | 跨域 Application Service、补偿和回滚语义缺失 | `MISSING`；B 线 + Business Space/协作 Owner |

上述缺失路径在当前 `admission.py` 中没有注册项，在
`src/gateway/configs/schemas/bots.openapi.json` 中也没有 operation；Gateway 的
`/openapi/v1/bots/**` 通配转发规则只能转发已经存在的请求，**不能把一个没有
Backend/上游 Contract 的接口变成已接入能力**。

#### 3.1.3 二级页整体判定

- **Owner 的基础详情/调试页：大部分具备**，但 Container、日志、健康、评测和
  编辑页内核仍缺。
- **Service Bot 发布页：lifecycle + edit-lock 已具备**，Containers、Evaluation、
  Editors 仍缺。
- **Service Bot 协作者编辑页：不能判定完成**。Sessions、Engine runtime、Models、
  Approvals、Lifecycle、Edit-lock 已使用 addressed-bot 语义；Bot 基础详情、Identity、
  Engine config、Startup Script、Resources、Routines 等仍是 Owner-only。需要产品和
  Collaboration/Business Space Contract 明确哪些区域允许协作者读写，再逐项调整，
  不能把 `GRANT_CHECKED_OWN_BOT` 直接批量放宽。
- **应用身份调用：也不能等同于页面已完成**。Gateway 对 `/bots/**` 允许 user/app
  可选只是把身份送到 Backend；最终是否允许由 admission 和 bot grant 决定。
- **当前 Frontend 也尚未完成 OpenAPI 切换**。`ServiceBotController.ts` 仍调用
  `/api/service-bot/publish/*`、`/api/bot/collaborator/*` 和
  `/api/service-bot/read-only/tree`，`BotRenderController.ts` 仍调用
  `/api/bot-render-screens`。所以即便 lifecycle/edit-lock 的公开端已经存在，也不能把
  “Backend OpenAPI 已具备”直接写成“二级页面端到端已交付”；前端迁移和联调仍需单列验收。

---

## 4. A 线进展

### 4.1 已实现

| 接口 | 状态 | 上游/说明 |
|---|---|---|
| `GET /openapi/v1/bots/all` | 已实现 | `BotInventoryServiceProtocol`；应用调用仍为 `REFUSED`，只允许人类调用 |
| `GET /openapi/v1/bots/local/devices` | 已实现 | Desktop device inventory |
| `GET /openapi/v1/bots/local/devices/{machine_id}/files` | 已实现 | 本地设备挂载目录选择，不是容器文件 API |
| `POST /openapi/v1/bots/local` | 已实现 | 本地 Bot 创建，支持 201/202 |
| `GET /openapi/v1/bots/local` | 已实现 | 本地 Bot 列表 |
| `GET /openapi/v1/bots/{bot_id}/local` | 已实现 | 本地 Bot 详情 |
| `GET /openapi/v1/bots/{bot_id}/local/auth-status` | 已实现 | Passport 授权轮询和完成创建 |
| `POST /openapi/v1/bots/{bot_id}/local/restart` | 已实现 | Desktop Bot 重启 |
| `DELETE /openapi/v1/bots/{bot_id}/local` | 已实现 | Desktop Bot 删除 |
| `POST /openapi/v1/bots/{bot_id}/local/open-folder` | 已实现 | 本地打开目录 |
| `POST /openapi/v1/bots/{bot_id}/activate` | 已实现 | 沉寂个人云端 Bot 激活 |
| `POST /openapi/v1/bots/{bot_id}/data-init` | 已实现 | 传递 `IAM_TOKEN` 到 typed Service API；异步执行，异常被观察和记录 |
| `GET /openapi/v1/bots/{bot_id}/data-init` | 已实现 | 仅返回公开状态字段，不暴露 `bot.ext`、IAM token 或下游内部状态 |
| `POST /openapi/v1/bots/{bot_id}/engine/restart` | 已实现 | 委托 `EngineRuntimeRelayProtocol`；不同于容器重启 |

同时已经完成：

- `POST /openapi/v1/bots` 接收 `space_id`。
- Passport 响应增加可选 `expire_at`、`certificate_url`。
- `/all` 使用独立 `BotInventoryItem`，不扩大基础 `Bot` response 的 required contract。

### 4.2 A 线阻塞和待完成

| 事项 | 当前问题 | A 线下一步 | 外部依赖 |
|---|---|---|---|
| `data-init` 真实环境验证 | 本仓 API 闭环已完成：Cookie `IAM_TOKEN` 在 HTTP 边界解析并传入 typed Service API，新增安全状态查询；尚缺真实 IAM/Engine/下游环境的 E2E 证据 | 完成 singlebox/集成环境 trigger→poll→completed/failed 验证，并验证现有“读取后立即清除”临时凭证流程 | IAM、Engine 与数据初始化下游环境 |
| `/all` service Bot 与富字段 | 是否统一聚合以及 health/version/container/lock 等字段的批量策略需收敛 | 只做 read model 编排，不能读取其他领域 Repository | Service lifecycle、Harness、BaaS、Lock 等 Service API |
| Bot Inventory 空间上下文 | 当前只有 `NoopBusinessSpaceContext` personal fallback | A 线接入 Business Space Owner 的正式上下文 Service API，完成 `/all` 团队空间消费 | Business Space prod adapter |
| `ac_bots.space_id` DDL | 已按本仓约定补入 OpenAPI README 的带外 DDL 权威记录；尚无平台执行证据 | 发布前取得环境、变更单/版本、执行时间、结果和回滚负责人 | 平台数据库变更流程 |

独立 `GET /openapi/v1/spaces` 与 Bot 跨空间迁移归 B 线接入，见 §5.3。Bot 迁移推荐接口二选一：

```text
POST /openapi/v1/bots/{bot_id}/migrations
```

或：

```text
POST /openapi/v1/bot-space-migrations
```

迁移应由类似 `BotSpaceMigrationServiceProtocol` 的 Application Service 编排，不能复用当前仅负责 AICoding workspace 初始化的 `WorkspaceServiceProtocol`。A 线负责提供 Bot Inventory/空间消费约束，B 线负责公开接入与联调。

### 4.3 授权关系部分成功问题

公开 Bot 工坊涉及的三条外部身份写路径已经统一停止“记日志后返回成功”：

- 云端 Bot 创建和授权完成：owner 授权关系写失败会传播，`None` 结果视为失败；
- Local Bot 授权完成：owner 授权关系写失败会传播，`None` 结果映射为上游失败；
- 公开 OpenAPI 更新 Bot：Passport 元数据同步异常统一映射为 502 Envelope。

这些入口在外部写失败时都不会再向调用方确认成功；已签发 Passport 但缺少 `agent_code` 的不完整身份也会 fail-closed。需要注意，前一步 Bot/设备写入可能已经发生，当前 Contract 尚未证明通用重试是幂等的，因此不能把简单重试当作完整恢复方案。遗留内部 `/api` 更新路由仍采用日志后继续；它不属于本轮公开 OpenAPI 收口，需由对应维护者处理。

仍需注意：Bot 数据写入和外部身份写入是跨系统两步操作，当前仓库没有持久化 repair/reconciliation worker。生产交付仍应由 Contract Owner 补充至少一种长期恢复机制：

- 持久化 repair/retry work；
- 明确的 partial-success 状态和幂等恢复入口；
- 可行时执行补偿删除；
- 或将两步纳入持久化工作流。

---

## 5. B 线进展

### 5.1 Lifecycle 已实现

以下 10 个 operations 已进入 `admission.py`，统一为 `GRANT_CHECKED_ADDRESSED_BOT`：

| 接口 | 用途 |
|---|---|
| `POST /openapi/v1/bots/{bot_id}/lifecycle/upgrade` | personal → service 升级 |
| `GET /openapi/v1/bots/{bot_id}/lifecycle` | 发布态、版本和阶段 |
| `DELETE /openapi/v1/bots/{bot_id}/lifecycle` | 删除 lifecycle/降级语义以正式 Contract 为准 |
| `GET /openapi/v1/bots/{bot_id}/lifecycle/approval` | 查询审批开关 |
| `PUT /openapi/v1/bots/{bot_id}/lifecycle/approval` | Owner 管理审批开关 |
| `POST /openapi/v1/bots/{bot_id}/lifecycle/advance` | 草稿推进到预发或上线 |
| `POST /openapi/v1/bots/{bot_id}/lifecycle/restart` | 重启已发布服务 |
| `POST /openapi/v1/bots/{bot_id}/lifecycle/cancel-staging` | 取消预发 |
| `POST /openapi/v1/bots/{bot_id}/lifecycle/offline` | 下线 |
| `POST /openapi/v1/bots/{bot_id}/lifecycle/retry` | 重试发布动作 |

领域实现委托 `service_bot` publication/lifecycle Service API，OpenAPI Router 不拥有发布策略。

### 5.2 Edit-lock 已实现

| 接口 | 用途 |
|---|---|
| `GET /openapi/v1/bots/{bot_id}/edit-lock` | 查询锁 |
| `POST /openapi/v1/bots/{bot_id}/edit-lock` | 获取锁 |
| `POST /openapi/v1/bots/{bot_id}/edit-lock/steal` | 抢占锁 |
| `DELETE /openapi/v1/bots/{bot_id}/edit-lock` | 释放锁 |

委托 `CollaboratorLockService`，四个 operations 均为 `GRANT_CHECKED_ADDRESSED_BOT`。

### 5.3 B 线剩余

| 能力 | 优先级 | 状态 | B 线责任 | 上游责任 |
|---|---|---|---|---|
| Containers summary | P0 | 未接入 | 定义工坊所需 read model、Bot/Stage 授权和 facade | BaaS/PaaS 提供实例与 metrics Contract |
| 单实例重启 | P0 | 未接入 | 提供 Bot-scoped 授权入口和错误规范化 | BaaS 提供幂等实例重启能力 |
| Editors/协作者 CRUD | 后续批次 | 未接入 | 等统一协作契约后实现 Service Bot 工坊入口 | Business Space/协作 Owner 提供成员与权限规则 |
| ~~Container files~~ | ~~P2~~ | 已并入 `/resources` | 不再单独建设 `/files`;落地仍在 `ResourceFileService` 内做 service-bot sandbox 分支与 `read_only_rules` 合并,写操作全路径 403 fail-closed。详见 §3.1.1 / §6.7 | Device Filesystem/Runtime Owner 提供内部 `read-only/tree` Contract 与路径规范 |
| Channels | P2 | 未接入 | 建设 Bot-scoped 或独立领域公开入口、Gateway 和联调 | Channel Owner 补 tenant/owner/bot guard 与正式 Service API |
| Nodes | P2 | 未接入 | 建设 authorized relay、501 capability 语义和 Gateway | Engine Owner 收敛 Node 字段与运行态 Contract |
| Render screens | P2 | 未接入 | 明确读或 CRUD 范围并建设公开入口 | Render Screen Owner 提供领域权限与 Service API |
| Spaces list | P3 | 未接入 | 建设独立 Spaces OpenAPI；与 A 线 `/all` 空间消费保持同一语义 | Business Space Owner 提供 prod Service API |
| Bot migrate | P3 | 未接入 | 建设跨域 Application Service、公开契约和补偿/回滚语义 | Business Space/协作 Owner 提供成员与权限规则 |

任务护航 Flow、Bot/容器 Runtime logs、Health、Evaluation 和 Skills/per-bot MCP 不在本表中作为 B 线实现项，统一按 §6 由其他团队或其他同学负责。

Containers 推荐路径需要与 BaaS Contract 一起确定。例如使用 Bot-scoped facade 时：

```text
GET  /openapi/v1/bots/{bot_id}/containers
POST /openapi/v1/bots/{bot_id}/containers/{instance_id}/restart
```

如果 Gateway 可以在统一身份校验后直接路由 BaaS，也可以采用独立 Runtime domain。无论采用哪种路径，Backend 不保存 BaaS 实例运行状态。容器实例日志的公开路径和 Contract 由日志团队确定；B 线仅提供 Bot/Stage/instance 上下文并配合联调。

---

## 6. 其他团队/其他同学负责的工坊相关能力

本节中的能力仍属于“Bot 工坊产品交付范围”，但领域逻辑不归 A/B 线。分工分为两类：

- Runtime/容器日志、任务护航、健康诊断、Evaluation、Flow 和 Skills/per-bot MCP：由其他团队或其他同学负责公开能力实现，A/B 线只配合需求、身份上下文和联调。
- Nodes、Channels、Render screens、Container files、Business Space：对应领域 Owner 提供稳定 Contract 和领域能力，B 线仍按原分工负责工坊公开接入；A 线继续负责 Bot Inventory 中的空间消费。

### 6.1 日志、任务护航、诊断和评测

**实施方：日志/Runtime Observability 团队、任务护航/Harness/Quality 相关团队。A/B 线仅配合身份、Bot 上下文和最终联调。**

| 能力 | 领域现状 | 缺口 | 备注 |
|---|---|---|---|
| Runtime logs（含容器实例日志） | Bot Inventory 只有 action 声明；日志来源尚需确定 | Runtime Log Contract、受限参数、公开 Adapter、Gateway | 必须限制日志来源、路径、`tail`、level 和敏感信息；A/B 不实现采集或日志公开接口 |
| Health score | `community/core/harness` 已有领域与内部 API | 稳定 Service API、OpenAPI、能力限制和 Gateway | 当前规划仅 OpenClaw + cloud；由任务护航团队实现 |
| Health check | Harness 已有诊断能力 | 触发、轮询、报告 Contract | 由任务护航团队实现 |
| Evaluation | `community/core/quality` 与 `QualityTaskServiceProtocol` 已存在 | 公开创建/查询、结果页 URL/token Contract | 由 Quality/任务护航相关同学实现 |

推荐按独立领域公开：

```text
/openapi/v1/diagnostics
/openapi/v1/evaluations
```

如产品需要 Bot-scoped convenience endpoint，也必须委托 Harness/Quality Service API，不在 Bots Core 重写诊断或评测逻辑。

### 6.2 Skill sets 与 per-bot MCP

**实施方：Skills 相关同学。A/B 线不承担实现。**

当前并非“无现成上游”：仓库已经存在：

```text
src/backend/src/agentclaw/community/core/skill_center/
src/backend/src/agentclaw/community/core/skills_pool/
src/backend/src/agentclaw/community/core/mcp/
```

并已有 `SkillSetService`、SkillSet 激活/切换、Bot 级布局和 MCP 关联能力。

剩余工作需要 Skills 同学完成：

- 对照工坊产品需求收敛正式 Service API Contract。
- 明确引用型市场 Skill 的只读和版本语义。
- 明确 per-bot MCP 与租户级 MCP 配置的边界。
- 明确 caller、owner、collaborator 和 application grant 的权限规则。
- 建设公开 OpenAPI Adapter、统一 Envelope 和 Contract tests。
- 同步 Gateway route/auth/schema 以及 OCB/Sofapy 副本。

候选路径：

```text
/openapi/v1/bots/{bot_id}/skill-sets
/openapi/v1/bots/{bot_id}/skill-sets/{skill_set_id}
/openapi/v1/bots/{bot_id}/skill-sets/{skill_set_id}/mcps
```

或按独立资源公开：

```text
/openapi/v1/skill-sets
/openapi/v1/skill-activations
/openapi/v1/mcp/bindings
```

最终路径由 Skills Contract Owner 决定，但不得继续采用 `/bots/skill-sets/{bot_id}` 旧式寻址。

### 6.3 Flow

**实施方：任务护航团队牵头公开能力和产品交付，BCS 团队负责 State Machine 领域能力。A/B 线不承担实现。**

Flow 在原工坊清单中属于任务护航 DAG/YAML 和执行历史。BCS 已存在 State Machine 相关领域实现，并非“上游未落”，但这不代表工坊任务护航公开面已经完成。当前主要缺口是：

- 面向工坊的正式 Service API/OpenAPI Contract；
- 用户、Bot、Group/Session 的访问控制；
- 定义、运行、取消、运行图和历史的公开范围；
- Gateway schema 和路由。

推荐进入 `bcn.openapi.json` 或独立 `flow.openapi.json`，由 Gateway 路由 BCS，或由任务护航团队提供授权 facade。Backend Bots Core 不实现状态机，B 线也不把 Flow 当作编辑页普通 CRUD 自行实现。

### 6.4 Nodes

**领域实现方：Engine 团队；工坊接入方：B 线。**

Engine 已有：

```text
GET /api/nodes
Capability.NODE_LIST
```

剩余工作是 Engine 字段 Contract、Backend authorized relay 或 Gateway direct route，以及 501 capability 语义。B 线负责公开接入和联调，但不保存 Node 状态。

### 6.5 Channels

**领域实现方：Channel 模块 Owner/相关同学；工坊接入方：B 线。**

当前已有：

- `ChannelServiceProtocol`；
- `src/backend/src/agentclaw/community/core/channel/`；
- 内部 `/api/channels` Router；
- Channel DI 和持久化。

但在公开化之前必须先完成 tenant/owner/bot guard 和协作者权限 Contract。不能简单把内部 Router 包装成公开 proxy。

推荐独立：

```text
/openapi/v1/channels
```

也可以提供：

```text
/openapi/v1/bots/{bot_id}/channels
```

作为 convenience facade，但策略仍归 Channel Service API。B 线不得绕过 guard 直接转发现有内部 Router。

### 6.6 Render screens

**领域实现方：Render Screen 模块 Owner/相关同学；工坊接入方：B 线。**

当前已有 `RenderScreenServiceProtocol` 和内部 CRUD Router。领域 Owner 负责稳定 Contract 和权限规则；B 线需要确认工坊本期只开放读取，还是开放完整 CRUD，并补齐公开 Adapter、Envelope、OpenAPI、Gateway 和联调。

### 6.7 Container files(已并入 `/resources`,不再单独建设 `/files` 接口)

**决定(2026-08-18 复核)**:不单开 `/openapi/v1/bots/{bot_id}/files`;容器/沙箱目录树需求统一合并到 `/openapi/v1/bots/{bot_id}/resources` 模块(详见 §3.1.1)。理由:`/resources` 已经有完整的 list/upload/download/preview/mkdir/stat/delete,个人/本地 Bot 的工作区目录树已覆盖;为 service bot 只读沙箱再新开一条公开接口与现有能力重叠,徒增一份契约/docs/测试。

合并后的边界仍有上限保护:

- 公开契约仍是 `/openapi/v1/bots/{bot_id}/resources` 一条;`ResourceFileService` 作为 facade,按 `bot_type` 内部选择真源:
  - personal/desktop bot → engine workspace file tree(现状)
  - service bot → publish-sandbox `provider.list_directory`(`router_build.py:read-only/tree`)
- 只读规则:`/resources` 现有 `is_readonly` 只覆盖 workspace identity dotfile;需要扩展为在 `bot_type=service` 时合并 `ac_bot_publish.ext.read_only_rules`(custom + default),并对 service bot **全路径只读**。
- 写操作 fail-closed:service bot 调 `POST /resources/upload`、`POST /resources/mkdir`、`DELETE /resources` 必须返回 403,守住 publish-sandbox 不可写。
- 内部 `/api/service-bot/read-only/tree`(`service_bot/router_build.py:166-189`)保持不变,作为老前端/调试遗留,不宣布弃用,后续等公开 facade 与 read_only_rules 合并稳定再清。

上游 Contract 仍由 Device Filesystem/Runtime Owner 提供(`provider.list_directory` 稳定 Contract 与路径规范);B 线不再为 `files` 单建公开 Adapter,但 `ResourceFileService` facade 内分支与 `read_only_rules` 合并这块落地仍需 A 线 `/resources` owner(lucas-xzp)与 B 线 service-bot/Joseph 协作。Backend 一律不得直接拼接 Engine 物理路径,必须经 provider Contract。

---

## 7. Gateway 与 OpenAPI schema 拆分建议

不要默认把所有工坊能力继续追加到：

```text
src/gateway/configs/schemas/bots.openapi.json
```

建议按领域拆分：

```text
bots.openapi.json
runtime.openapi.json
quality.openapi.json
diagnostics.openapi.json
skills.openapi.json
channels.openapi.json
render-screens.openapi.json
spaces.openapi.json
```

Flow 使用：

```text
bcn.openapi.json
```

或：

```text
flow.openapi.json
```

建议路由 Owner：

| Domain | 推荐 upstream |
|---|---|
| Bots、Inventory、Local、Lifecycle facade | Backend |
| Quality/Evaluation | Backend Quality Adapter |
| Harness/Diagnostics | Backend Harness Adapter |
| Skills/MCP | Backend Skill/MCP Adapter |
| Channels | Backend Channel Adapter |
| Spaces | Business Space Owner 或 Backend Adapter |
| Runtime/Containers/Nodes | Engine/BaaS,或 Backend authorized facade(`Files` 已并入 `/resources`,见 §3.1.1 / §6.7)|
| Flow | BCS |

每个领域接入都必须同步两套部署配置：

1. Avernet：
   - `src/gateway/configs/application.yaml`
   - 对应 `src/gateway/configs/schemas/*.openapi.json`
2. OCB/Sofapy：
   - OCB 对应 Gateway `application.yaml`
   - OCB 对应 served OpenAPI schema

本轮已在 OCB/Sofapy 仓库 `dev` 分支、基线 `6fbdc74e4fb9032ae98afe942c2f83611c3b908b` 上同步：

- `src/gateway/configs/application.yaml`：补齐 `/bots/all` 与 local 两类 human-only 规则；
- `src/gateway/configs/schemas/bots.openapi.json`：与 Avernet 本轮生成产物一致。

该 OCB 工作区原有未跟踪目录 `openocb/`，本轮未触碰；上述两项当前仍是未提交修改，最终完成状态以 OCB 独立提交/PR SHA 为准。

---

## 8. 剩余任务总表

| 产品能力 | 领域 Owner | 执行分工 | 上游状态 | 工坊公开接入 | 当前结论 |
|---|---|---|---|---|---|
| Personal/Local Bot | Bot Inventory/Desktop | A 线 | 已有 | 已实现 | `data-init` API 闭环完成，真实环境 E2E 待验证 |
| Workshop `/all` | Bot Inventory 聚合 | A 线 | 已有 | 已实现 | 富字段批量策略待收敛 |
| Inventory 空间消费 | Business Space + Bot Inventory | A 线接入 | Prod context adapter 缺失 | 部分实现 | `/all` 当前仅 personal fallback |
| Spaces list | Business Space | B 线接入 + Business Space Owner | Prod Service API 缺失 | 未实现 | B 线 P3，A 线消费结果 |
| Bot migrate | 跨域 Application Service | B 线接入 + Business Space/协作 Owner | Migration Service 缺失 | 未实现 | B 线 P3，需跨域设计 |
| Service lifecycle | Service Bot Publication | B 线 | 已有 | 已实现 | 需持续验证兼容性 |
| Edit-lock | Collaboration Lock | B 线 | 已有 | 已实现 | 已接入 |
| Containers summary/restart | BaaS/PaaS Runtime | B 线接入 + BaaS Owner | 部分缺失 | 未实现 | B 线 P0；实例日志归日志团队 |
| Editors | Collaboration/Space | B 线接入 + 协作 Owner | Contract 待定 | 未实现 | 后续批次 |
| Runtime/容器日志 | Runtime Observability | 日志相关团队 | 来源/Contract 待定 | 未实现 | A/B 只跟进 |
| Health/任务护航 | Harness | 任务护航团队 | 内部能力已有 | 未实现 | 其他团队实现 |
| Evaluation | Quality | Quality/任务护航团队 | Core/Protocol 已有 | 未实现 | 其他团队实现 |
| Skill sets/per-bot MCP | Skill Center/Skills Pool/MCP | Skills 相关同学 | 大量能力已有 | 未实现 | 其他同学实现 |
| Flow | Harness/BCS | 任务护航团队 + BCS 团队 | State Machine 已有 | 未实现 | 其他团队实现，A/B 配合 |
| Nodes | Engine Runtime | B 线接入 + Engine 团队 | Engine API 已有 | 未实现 | 等 Engine Contract/relay |
| Channels | Channel | B 线接入 + Channel Owner | 内部 CRUD 已有 | 未实现 | 先补 tenant guard |
| Render screens | Render Screen | B 线接入 + Render Owner | Protocol/CRUD 已有 | 未实现 | 产品公开范围待定 |
| ~~Container files~~(已并入 `/resources`) | Device Filesystem/Runtime | 已并入 `/resources`(A 线 `/resources` owner + B 线 service-bot 协作);不再单开 `/files` 接口 | 内部 `read-only/tree` 已有;未做 facade 分支 + `read_only_rules` 合并 + 写 403 fail-closed | 部分归入 `/resources` backlog | 落地仍需在 `ResourceFileService` 补分支,不要硬编码 Engine 物理路径 |

---

## 9. 状态维护规则

每次移动一项状态，至少更新：

1. 本文的领域 Owner、执行分工、上游状态和工坊接入状态。
2. 对应正式 Service API/OpenAPI Contract。
3. Backend Router 和 `admission.py`，如果请求经过 Backend。
4. Avernet Gateway route、auth 和 schema。
5. OCB/Sofapy 对应配置与 schema。
6. 中英文 OpenAPI README；如新增 Bot path 第一段 literal，同时更新保留名检查。
7. Contract、权限、schema、Gateway 和必要 E2E 测试证据。
8. 数据库 DDL、外部配置或部署变更的执行记录与回滚方案。

推荐状态枚举：

```text
NOT_STARTED
DOMAIN_EXISTS
CONTRACT_MISSING
ADAPTER_MISSING
GATEWAY_MISSING
UPSTREAM_BLOCKED
E2E_BLOCKED
INTEGRATED
```

“内部 `/api` 已存在”只能说明 `DOMAIN_EXISTS`，不能直接标记为 `INTEGRATED`。

---

## 10. Validation 记录模板

每次将能力标记为 `INTEGRATED` 时，在 PR 或交付记录中填写：

```markdown
- 基于提交：<commit sha>
- Backend targeted tests：<command + result>
- Backend architecture tests：<command + result>
- Gateway tests：<command + result>
- Generated OpenAPI vs served schema：<result>
- OCB/Sofapy config/schema：<repo sha + result>
- Singlebox/E2E：<command + result，或未执行原因>
```

本轮核对已执行：

```text
# Avernet Backend：data-init、Local、admission、principal seam、Service API conformance
src/backend/.venv/bin/pytest -q \
  tests/community/adapters/http/openapi_v1/test_bots_data_init.py \
  tests/community/adapters/http/openapi_v1/local/test_local_handlers.py \
  tests/community/core/bot_management/services/test_data_init_service.py \
  tests/community/adapters/http/openapi_v1/test_admission_inventory.py \
  tests/community/adapters/http/openapi_v1/test_principal_seam.py \
  tests/community/architecture/test_service_api_conformance.py
# 121 passed

# Avernet Backend：架构边界（API 仅依赖 Protocol、HTTP/Core 分层、模块边界）
src/backend/.venv/bin/pytest -q \
  tests/community/architecture/test_api_layer_is_protocols_only.py \
  tests/community/architecture/test_http_adapter_layer_is_http_only.py \
  tests/community/architecture/test_no_fastapi_in_core.py \
  tests/community/architecture/test_module_boundaries.py
# 13 passed

# Avernet Gateway：route security 与 served schema
src/gateway/.venv/bin/pytest -q \
  tests/unit/core/authn/test_route_security.py \
  tests/unit/core/forwarding/test_served_openapi.py
# 43 passed

# OCB/Sofapy Gateway：当前仓库具备的配置解析测试
PYTHONDONTWRITEBYTECODE=1 <Avernet gateway venv>/bin/pytest -q \
  -p no:cacheprovider tests/test_gateway_config.py
# 2 passed
```

Gateway schema 已由 Backend 生成器更新并同步到 OCB/Sofapy 工作区。尚未执行真实 IAM/Engine/数据初始化下游 E2E，也未为 OCB 修改生成独立提交 SHA；因此这两项仍不能标记为部署完成。

在将本分支 rebase 到 2026-08-18 的最新 `origin/dev`（`90d7fbce7`）并完成本轮 A 线收口后，追加执行：

```text
# Bot 工坊 Backend 广覆盖回归
254 passed, 18 warnings

# Backend architecture 全套
147 passed, 17 warnings

# Gateway route security / served OpenAPI / domain map
152 passed

# Backend 生成 OpenAPI 与 Gateway pinned schema
PATHS=101
OPERATIONS=140
SCHEMA_CMP=0

# Python SAST 本地阻断扫描
passed
```
