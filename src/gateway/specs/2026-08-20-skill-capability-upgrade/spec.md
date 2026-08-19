# Skill 能力全量 OpenAPI Gateway 化与 Space Skill 升级 Spec

> 状态：**正式 Spec / 开发与团队 Review 共同基线**
> 日期：2026-08-20
> 目标分支：`dev_refactory_collaboration`
> 本文是 Phase 1、Phase 2 开发、测试、Ticket 拆分和团队 Review 的唯一方案基线。

## Problem Statement

TeamClaw 当前的 Skill 能力同时存在两类问题。

第一，存量产品能力没有统一收敛到 OpenAPI Gateway。Local Skill 已经发布了
Bot-scoped OpenAPI，Repo Catalog、SkillSet、MCP、CLI 和部分市场能力仍分散在
Legacy BFF；Local 的 active 又通过 Default SkillSet exclusion 间接表达。前端、
外部调用方和 Runtime 因此面对不同入口、不同关系模型和重复的软链刷新逻辑，
已发布 OpenAPI 还要求所有演进严格向前兼容。

第二，新的 Space Skill 需要稳定身份、可变 Draft、不可变 Version、Skill Center
发布、Track Latest、Service Artifact 精确版本和多引擎交付。历史 Center 实现曾把
版本拆成多条 ac_skill、以名称映射 Skill Center，并在回滚后留下外围数据；若在旧
模型上直接扩展，会再次产生身份漂移、状态混用、不可复现 Artifact 和不可安全回滚。

用户需要的是一套统一但不强制转换既有资产的能力模型：Phase 1 先把所有存量能力
迁入 Gateway 并保持兼容，Phase 2 再在该控制面上交付 Space Skill 生命周期。两阶段
可以并行开发部分内部模块，但必须分别验收，且共享同一份领域与接口合同。

## Solution

建设一个深的 Skill/MCP 控制面，以稳定 Skill Asset、Bot Installation、SkillSet
Membership 和 Runtime Full Projection 为核心。Local、Repo、Space 是三种长期并存
的资产类型；Bot 当前期望生效的 Skill/MCP 由 Installation 物化，普通 SkillSet
通过原子批量命令维护 Membership 与 Installation，Direct activate/deactivate
直接维护 Installation。所有 Runtime 更新和 Bot 重启都经过同一个 Resolver 生成
完整投影。

Phase 1 保留已发布 Local/MCP/Space wire，新增 canonical Repo、SkillSet、Bot MCP
接口，并让 Legacy BFF 退化为兼容 Adapter。Phase 2 在稳定 ac_skill Identity 上增加
Space Ownership、Owner/Manager、永久 Edit Lease、Draft、Publication Attempt、
不可变 Version、精确 Store 物化、Track Latest 和 Service Artifact 固化。

OpenAPI 以 Backend 实际路由为源，通过标准 dump 与 compatibility gate 生成 Gateway
artifact。本文先冻结待实现合同；实现完成后生成的 OpenAPI JSON 是前端、外部调用方
和 Gateway Swagger 的机器可读权威。

## User Stories

1. 作为既有 OpenAPI 调用方，我希望不传 type 时继续只看到 Local Skill，从而升级后无需修改客户端。
2. 作为既有 OpenAPI 调用方，我希望 Local 上传、同名替换、激活、停用和删除保持原状态码与响应结构。
3. 作为产品前端，我希望显式请求 ALL 后看到 Bot 下全部 Local、Repo、Space Skill。
4. 作为产品前端，我希望通过同一个 skill_id 查询 Skill 类型和详情，而不必额外传 type。
5. 作为 Bot 用户，我希望上传 Local Skill 后先保持 inactive，再由 Direct 或 SkillSet 激活。
6. 作为 Bot 用户，我希望停用 Local Skill 时保留资产，以便以后再次激活。
7. 作为 Bot 用户，我希望删除 Local Skill 时真正删除 Bot-owned 资产，并在有引用时得到明确阻断。
8. 作为 Bot 用户，我希望直接激活 Repo/Space Skill 时立即建立 Bot 的有效关系和运行时投影。
9. 作为 Bot 用户，我希望停用 Repo/Space Skill 时只移除 Bot 关系，不删除共享资产。
10. 作为 SkillSet 用户，我希望一个 SkillSet 只有整体 active 或 inactive，不出现半选状态。
11. 作为 SkillSet 用户，我希望激活一个 SkillSet 时其中全部 Skill/MCP 原子生效。
12. 作为 SkillSet 用户，我希望停用一个 SkillSet 时其中全部 Skill/MCP 原子失效。
13. 作为 SkillSet 用户，我希望向 active SkillSet 添加成员后新成员立即生效。
14. 作为 SkillSet 用户，我希望从 active SkillSet 移除成员后该成员立即失效。
15. 作为用户，我希望同一资源最多属于一个普通 SkillSet，避免来源冲突。
16. 作为用户，我希望 Direct active 资源加入 SkillSet 前被要求先停用 Direct 来源。
17. 作为用户，我希望 SkillSet 管理的资源不能被单独 activate/deactivate，避免破坏原子状态。
18. 作为平台管理员，我希望 System Default 始终生效且不可删除或停用。
19. 作为 Repo 市场用户，我希望搜索、列表、目录树和同步都从 aiworkbench master 得到一致结果。
20. 作为 Repo 市场用户，我希望同步接口同步完成后才返回，并在并发同步时收到稳定冲突。
21. 作为 MCP 用户，我希望 MCP 与 Skill 使用一致的 Direct/SkillSet 激活语义。
22. 作为 MCP 用户，我希望安装、加入 SkillSet 或激活前完成权限校验。
23. 作为 Bot 用户，我希望 Bot 重启时根据数据库 Desired State 重建全部 Skill/MCP 投影。
24. 作为 Bot 用户，我希望一次 Runtime 明确失败不会留下新的半套 Desired State。
25. 作为运维人员，我希望再次 activate/deactivate 或修改 active SkillSet 能全量修复上次崩溃窗口。
26. 作为 Space 用户，我希望从 ZIP 或 Git 创建全新的 Space Skill Identity，而不转换 Legacy Repo。
27. 作为 Space Skill Owner，我希望升级只创建新 Draft/Version，skill_id 和 skill_uuid 永远不变。
28. 作为 Space Skill Owner，我希望名称和描述只来自 SKILL.md，避免页面和发布内容不一致。
29. 作为 Team Skill Owner/Manager，我希望编辑前取得可抢占的数据库 Lease，旧页面在抢占后不能保存。
30. 作为 Personal Space 用户，我希望编辑不需要协作 Lease。
31. 作为 Skill Owner，我希望可以转移 Owner，并保留多个 Manager。
32. 作为 Skill Owner/Manager，我希望 Git 刷新失败时原 Draft 完全不变。
33. 作为 Skill 发布者，我希望一次发布冻结确定内容并可查询持久 Attempt。
34. 作为 Skill 发布者，我希望 SC 超时且结果未知时系统不盲目重复发布。
35. 作为 Skill 消费者，我希望只有全部 Runtime Store 就绪的 Version 才可见。
36. 作为 Personal/Desktop Bot 用户，我希望新 Version 发布后 Bot 草稿尽力刷新，并在重启时自愈。
37. 作为 Service Bot 用户，我希望一次 Bot Release 固化精确 Skill 版本。
38. 作为 Service Bot 运维人员，我希望扩容、重启和回滚始终复现历史 Release 内容。
39. 作为 Teclaw 用户，我希望继续使用 Artifact v4，并通过新增 skill-center Store 获取精确版本。
40. 作为文件型 Runtime 用户，我希望 Local、Repo、Center 在统一投影中共存但不互相转换。
41. 作为历史用户，我希望旧 Local、Repo、Bot-local 和不含 Center 的 Artifact 行为保持不变。
42. 作为安全负责人，我希望跨 tenant/env、路径穿越、非法压缩包和越权访问失败关闭。
43. 作为产品负责人，我希望仍被 SkillSet、Artifact 或进行中操作引用的 Skill 不能退役。
44. 作为前端开发者，我希望实现后直接使用 Gateway OpenAPI/Swagger 获取准确请求、响应和错误合同。
45. 作为发布负责人，我希望 Phase 1 和 Phase 2 各有独立验收门禁，并能定位安全回滚下限。

## Implementation Decisions

### 1. Review 与 OpenAPI 发布边界

本 PR 只提交待评审合同，不提前发布尚未实现的 HTTP 路由。Gateway OpenAPI artifact
是 Backend 实际公共路由的生成产物，不得手工加入本文中的候选接口。

Phase 1/Phase 2 实现 PR 必须分别通过标准 OpenAPI dump 和向前兼容 Gate。只有 Gate
通过后，生成 artifact 才随实现提交。这样可保证 Gateway 文档描述的每个接口都由
Backend 真实提供，同时避免在 Review 阶段暴露返回 `501` 的占位接口。

### 2. 交付阶段

#### Phase 1：存量能力全量 Gateway 化

- 严格兼容已发布 Local Skill、MCP 和 Space OpenAPI。
- 将 Legacy Repo Catalog、SkillSet、MCP、CLI 产品能力迁入 canonical OpenAPI。
- 建立统一的 Bot Skill/MCP 有效安装清单和 Runtime Resolver。
- Local、Repo、Space 不发生原地转换。
- 本阶段只交付 Backend、Gateway 合同和 Runtime 兼容能力；产品前端切流由前端团队
  独立实施与验收，不作为 Backend 实现 Ticket 的完成条件。

#### Phase 2：Space Skill 能力升级

- Space Skill Identity、Draft、Version、Edit Lease、Publication Attempt。
- Skill Center 发布、精确版本物化、升级传播和整体退役。
- 七桃负责的 Space、Member、Join Request、Favorite 接口保持现有合同，不在本文重构。

### 3. 最终领域模型

#### 3.1 Skill Asset

| 类型 | 资产归属 | 内容来源 | Bot 删除语义 |
| --- | --- | --- | --- |
| `LOCAL` | Bot-owned | Bot 上传 ZIP | 删除 Local 资产 |
| `REPO` | 环境共享 | `security_release/aiworkbench` master 扫描 | 不能删除共享资产 |
| `SPACE` | Space-owned | Draft 发布到 Skill Center | 不能通过 Bot API 删除 |

公开 `skill_id` 统一使用十进制字符串形式的 `ac_skill.id`。Item API 根据 `skill_id` 在服务端解析类型，调用方不传 `type`。

#### 3.2 Bot 当前有效 Skill

`ac_bot_skill_installation` 是 **Bot 当前期望生效 Skill 的物化清单**：

```text
UNIQUE(tenant, env, bot_id, skill_id)
```

- 行存在即 active，不保存 inactive 行。
- 不保存 `source_type`、`skill_set_id`、`direct_active` 或 Runtime observed status。
- `Installation + 普通 SkillSet Membership` 表示由 SkillSet 管理。
- `Installation + 无普通 Membership` 表示 Direct 管理。
- Active SkillSet、Direct Activate、System Default 最终物化到同一张表。

| 场景 | Asset | Membership | Installation |
| --- | --- | --- | --- |
| Local 已上传未激活 | 有 | 无 | 无 |
| Repo/Space 在 inactive SkillSet | 有 | 有 | 无 |
| Direct active | 有 | 无 | 有 |
| Active SkillSet 成员 | 有 | 有 | 有 |

不存在公开的 Installation Resource API：

```text
PUT/GET/DELETE /bots/{bot_id}/skills/{skill_id}/installation
```

#### 3.3 SkillSet 原子语义

- 一个普通 SkillSet 只有 active 或 inactive，不存在半选。
- 同一 Bot 下，同一 Skill/MCP 最多属于一个普通 SkillSet。
- Direct active 资源不能加入普通 SkillSet；必须先 Direct deactivate。
- 已属于普通 SkillSet 的资源不能单独 activate/deactivate。
- 激活/停用 SkillSet 原子批量增删成员对应的 Installation 行。
- 向 active SkillSet 添加成员时，同事务创建 Installation。
- 从 active SkillSet 移除成员时，同事务删除 Installation。
- System Default 始终 active，不承接历史 Local exclusion hack。

稳定错误码：

```text
RESOURCE_DIRECT_ACTIVE
RESOURCE_MANAGED_BY_SKILL_SET
RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET
```

#### 3.4 MCP 对齐

`ac_bot_mcp_installation` 与 Skill 使用相同定义：它是 Bot 当前期望生效 MCP 的物化清单，不保存 inactive 行。

- Direct activate/deactivate 增删 Installation。
- Active SkillSet 原子增删显式 MCP 成员。
- Effective Skill 声明的 MCP 依赖和 System Default 由全量 Resolver 物化。
- 安装、加入 SkillSet、激活前执行服务端权限校验。
- MCP Catalog、用户配置、调用身份仍是独立事实。

#### 3.5 持久化合同

Phase 1 新增两张 Desired State 表：

| 表 | 唯一键 | 行语义 |
| --- | --- | --- |
| `ac_bot_skill_installation` | `tenant + env + bot_id + skill_id` | Skill 当前应在 Bot 生效 |
| `ac_bot_mcp_installation` | `tenant + env + bot_id + server_code` | MCP 当前应在 Bot 生效 |

两表都不保存 inactive 行、来源类型、SkillSet ID 或 Runtime observed status。
普通 SkillSet Membership 继续复用既有关系表；同一 Bot 的同一资源最多属于一个
普通 SkillSet，由数据库可表达的唯一约束与事务内校验共同保证。System Default
保留平台默认 Skill/MCP/CLI 配置，但不再作为 Local active 的 exclusion 存储技巧。

Phase 2 复用一条 `ac_skill` 作为跨版本稳定 Identity：

- Legacy Local/Repo 可以没有 `skill_uuid`；公开 API 身份仍是 `ac_skill.id`。
- 新 Space Skill 必须由服务端生成唯一 UUID，并直接作为 SC `skillCode`。
- `ac_skill` 只增加活动 Draft 目标版本、Draft 状态、Git 来源和整体退役事实；
  不新增 `ac_skill_draft`，也不把 Version 再写成一条 `ac_skill`。
- `ac_skill_space_binding` 是一个 Skill 在一个环境下唯一的 Space Ownership。
- `ac_skill_grant` 表达恰好一个 Owner 和多个 Manager，不读取 Legacy 权限表。
- `ac_skill_draft_edit_lease` 是永久协作锁事实，保存 holder 与单调递增 fencing
  token；本期没有 TTL、expires、renewal 或自动释放语义。
- `ac_skill_version` 保存不可变 version ordinal、SC version number/version id、
  冻结元信息和 MATERIALIZING/PUBLISHED 状态，不保存长期 Snapshot URI/Hash。
- `ac_skill_publication_attempt` 保存幂等键、目标版本、外部提交阶段、失败原因和
  RESULT_UNKNOWN；一个 Skill 同时最多一个进行中 Attempt。
- 所有新增表的 `env` 非空，所有查询和唯一键同时包含 tenant/env。

当前目标分支已存在部分 Additive Schema。实现必须以本节覆盖其中仍残留的 TTL、
Space 删除或旧状态定义；已合入代码不因“已经存在”而自动成为新合同。

### 4. Runtime 一致性

```text
ac_bot_skill_installation
+ ac_bot_mcp_installation
+ System Default / CLI
        ↓
统一 Resolver
        ↓
skills-local / skills-repo / skill-center / MCP projection
```

1. Mutation 前校验 ACL、Bot ready、Membership、MCP 权限和 runtime name 冲突。
2. Runtime name 统一使用 `ac_skill.name`，由单一 `RuntimeNamePolicy` 规范化。
3. 每次 mutation 后执行一次完整 reconcile，不逐资源增量推送。
4. Runtime 明确失败时按方案 A 恢复旧 Installation 集合，再恢复旧 Projection。
5. activate/deactivate 即使 `changed=false`，仍完整 reconcile，作为自愈入口。
6. 不建设 Runtime 持久重试任务，不为此使用 `ac_task_queue`。
7. 进程崩溃窗口接受暂时不一致；下次 mutation 或 Bot 重启时全量自愈。
8. Installation 是 Desired State，不是 Runtime observed state。
9. Bot offline/not-ready 返回 `409 BOT_NOT_READY`，不写新的 Desired State。

### 5. Phase 1：Bot Skill OpenAPI

#### 5.1 已发布 Local 合同

| Method | Path | 用途 | 兼容要求 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/skills` | Local Skill 列表 | 不传 `type` 时严格保持 Local-only |
| POST | `/openapi/v1/bots/{bot_id}/skills` | raw ZIP 上传或同名替换 Local | 新建 inactive；替换保留 `skill_id` 和 active |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Local 详情 | 旧 wire 不变 |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | 删除 inactive Local 资产 | Repo/Space 不能复用为资产删除 |
| POST | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate` | 激活 Skill | 旧 Local 不变；additive 支持 Repo/Space |
| POST | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate` | 停用 Skill | 旧 Local 不变；additive 支持 Repo/Space |

已发布 `/openapi/v1/bots/skills/**` deprecated shim 全部保留。

#### 5.2 Additive 统一接口

| Method | Path | Request | 语义 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/skills` | optional `type=LOCAL\|REPO\|SPACE\|ALL` | 产品显式传 `ALL`；过滤、去重后再分页 |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | path | 服务端解析类型和 Bot 关系 |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/content` | path | 返回可消费 `SKILL.md` |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` | path | Bot 级参数 |
| PUT | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` | 全量参数对象 | 按 `SKILL.md config` 校验；保存失败必须传播 |

统一列表集合：

```text
Bot-owned Local Asset
∪ 普通 SkillSet Membership
∪ 当前 Installation
```

旧 `active` 字段表示当前是否存在 Installation。只增加 optional `type`、`managed_by`、`skill_set_id`，不增加 `direct_active/effective_active/sources`。

Direct activate/deactivate：

```text
activate
  → 校验无普通 Membership
  → INSERT Installation
  → 完整 reconcile

deactivate
  → 校验无普通 Membership
  → DELETE Installation
  → 完整 reconcile
```

对 Repo/Space，Direct activate 即 runtime install，deactivate 即 runtime uninstall。Local deactivate 只删除 Installation，Local 资产保留。

### 6. Phase 1：Repo Catalog

Repo 唯一内容源是 `security_release/aiworkbench` master。用户不能通过此接口导入任意 Git URL。

| Method | Path | Request | 语义 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/skills/repository` | `keyword?,path?,sort=latest\|hottest,page,page_size` | 全局 Repo 列表与搜索；真实分页 |
| GET | `/openapi/v1/skills/repository/tree` | 无 Bot 参数 | aiworkbench 目录树 |
| GET | `/openapi/v1/skills/{skill_id}` | path | Repo 资产详情；Phase 2 additive 支持 Consumable Space Skill |
| POST | `/openapi/v1/skills/repository/sync` | 无 Bot/Entity/Engine/Git URL | 同步 aiworkbench master |

Sync 同步执行、环境级互斥；并发返回 `409 SYNC_IN_PROGRESS`。一次完成 fetch、extract、DB scan、cache refresh，不提供新的 `sync-status` 或异步 Operation。

| 旧入口 | Canonical 行为 |
| --- | --- |
| `/api/skills/market/list`、`search` | GET Repository |
| `/api/skills/market/tree` | GET Repository Tree |
| `/api/skills/market/sync` | POST Repository Sync |
| `/api/skills/market/local` | 合并到 Repository List |
| `/api/skills/market/sync-status` | 只兼容旧 wire，不升级为 canonical |
| `/api/skills/sync-from-git` | 保持内部扫描能力 |
| `/api/skills/market/activate-batch` | 归 Bot Skill/SkillSet，不属于 Catalog |

### 7. Phase 1：SkillSet OpenAPI

Canonical 前缀：`/openapi/v1/bots/{bot_id}/skill-sets`。

| Method | 相对路径 | 语义 |
| --- | --- | --- |
| GET | `/skill-sets` | 列出全部 SkillSet，含 Default |
| POST | `/skill-sets` | 创建 inactive SkillSet；要求 `Idempotency-Key` |
| GET | `/skill-sets/{set_id}` | 详情和父子范围校验 |
| PUT | `/skill-sets/{set_id}` | 修改元信息；名称唯一范围 `tenant+env+bot_id` |
| DELETE | `/skill-sets/{set_id}` | 删除 inactive 普通 Set；Default 禁止 |
| GET | `/skill-sets/{set_id}/skills` | Skill Membership |
| PUT | `/skill-sets/{set_id}/skills/{skill_id}` | 幂等添加 Skill |
| DELETE | `/skill-sets/{set_id}/skills/{skill_id}` | 幂等移除 Skill |
| POST | `/skill-sets/{set_id}/activate` | 原子激活全部 Skill/MCP，一次 reconcile |
| POST | `/skill-sets/{set_id}/deactivate` | 原子停用全部 Skill/MCP，一次 reconcile |
| GET | `/skill-sets/resources` | 所有 Set 的 MCP/Default CLI 聚合 |
| GET | `/skill-sets/{set_id}/mcps` | MCP Membership |
| PUT | `/skill-sets/{set_id}/mcps/{server_code}` | 权限校验后添加 MCP |
| DELETE | `/skill-sets/{set_id}/mcps/{server_code}` | 移除 MCP |
| GET | `/skill-sets/{set_id}/mcp-permissions` | 聚合显式 MCP 与 Skill 依赖的权限状态 |
| POST | `/skill-sets/{set_id}/mcp-permission-requests` | 复用既有权限申请能力，不自建审批域 |
| DELETE | `/skill-sets/{set_id}/clis/{resource_code}` | 保留 Default CLI 排除能力 |

Default 不提供独立 `/skill-set-default` API，始终 active，不能 deactivate/delete。历史 SkillSet OpenAPI/BFF 只做 Compatibility Adapter，不拥有领域 SQL 或 Runtime 操作。

### 8. Phase 1：MCP OpenAPI

已发布 Catalog/Permission/Config 接口冻结：

```text
GET     /openapi/v1/bots/mcp/servers
GET     /openapi/v1/bots/mcp/tenants
GET     /openapi/v1/bots/mcp/servers/{server_code}
GET     /openapi/v1/bots/mcp/servers/{server_code}/permissions
GET/PUT /openapi/v1/bots/mcp/servers/{server_code}/config
```

新增：

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/mcps` | Bot MCP 投影 |
| POST | `/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate` | 无 Membership 时创建 MCP Installation |
| POST | `/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate` | 无 Membership 时删除 MCP Installation |

不存在 MCP Installation Resource API。产品通过 SkillSet 管理 MCP；外部调用方可 Direct activate/deactivate。

### 9. 七桃负责且冻结的 OpenAPI

以下接口以七桃当前方案和已发布合同为准，本文不重构：

```text
GET    /openapi/v1/spaces
POST   /openapi/v1/spaces/create
POST   /openapi/v1/spaces/personal/initialize
GET    /openapi/v1/spaces/{space_id}/members
POST   /openapi/v1/spaces/{space_id}/members
PUT    /openapi/v1/spaces/{space_id}/members/{user_id}/role
DELETE /openapi/v1/spaces/{space_id}/members/{user_id}
POST   /openapi/v1/spaces/{space_id}/join-requests
POST   /openapi/v1/spaces/{space_id}/market-favorites
POST   /openapi/v1/spaces/{space_id}/market-favorites/cancel
POST   /openapi/v1/spaces/{space_id}/market-favorites/search
```

本期不支持 Space 删除，不调用 Skill Center close/disable/delete Team。

### 10. Phase 2：Space Skill Asset、Draft、Version

#### 10.1 创建与读取

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/spaces/{space_id}/skills` | 能力工坊 Skill 列表 |
| POST | `/openapi/v1/spaces/{space_id}/skills` | raw ZIP + 幂等键创建 Identity、V1 Draft、Binding、Owner |
| POST | `/openapi/v1/spaces/{space_id}/skills/import-from-git` | JSON Git source + 幂等键，映射同一创建命令 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}` | 创作详情：Draft、Version、Attempt、权限 |
| GET | `/openapi/v1/skills/{skill_id}` | 消费详情：只返回 latest Published |

创建事务包含 `ac_skill Identity + V1 Draft facts + ac_skill_space_binding + 唯一 Skill Owner`；SC RPC 不进入事务。Git 导入后与 ZIP 创建同模型，不转换 Legacy Repo Skill。

详情分别返回：

```text
latest_published_version
draft_target_version
draft_status
publication_status
```

SKILL.md 是名称和描述的唯一事实来源。

#### 10.2 Draft 与 Version

| Method | Path | 语义 |
| --- | --- | --- |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/upgrade` | 创建下一版本 Draft；要求幂等键 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft` | Draft 状态和 Git metadata |
| GET/PUT | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/files/{path}` | 读取/保存单文件 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/files` | Draft 文件树 |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/replace` | ZIP 原子替换 |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git` | 从原 Git 来源手动刷新 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/versions` | Published Version 列表 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/versions/{version}` | 精确业务版本详情 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/versions/{version}/files` | 精确版本文件树 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path}` | 精确版本文件内容 |

URL 中 `{version}` 是业务序号 `1/2/3`，不是 `ac_skill_version.id`。Published Version 不可修改、删除或单独下线。Git 刷新失败时 Draft 完全不变。

#### 10.3 Owner、Manager 与放弃 Draft

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/grants` | 返回唯一 Owner 与 Managers |
| PUT | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/managers/{user_id}` | Owner 幂等添加当前 Space Member 为 Manager |
| DELETE | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/managers/{user_id}` | Owner 幂等移除 Manager |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/owner-transfer` | 原子转移唯一 Owner |
| DELETE | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft` | 放弃当前 EDITING Draft |

- 当前 Owner 可以转移 Owner；Space Administrator 也可从详情页执行转移，但必须记录
  原因。接收人必须是当前 Space Member。
- Owner 转移与旧 Lease 失效在同一事务完成；原 Owner 默认降为 Manager，管理员代为
  转移时可按请求决定是否保留。
- 本期只有 Owner/Manager 两种 Skill Grant，不新增 Editor 或普通 Skill Member。
- 删除升级 Draft 只放弃本次升级；首次从未发布的 Draft 只有在没有 Attempt、Version、
  Binding、Artifact 或其他历史事实时才可连同 Identity 物理取消。
- FROZEN Draft 不能放弃，必须先由 Attempt 收敛到明确结果。

### 11. Phase 2：Edit Lease

独立数据库锁表，仅 Team Space Draft 需要；Personal 返回 `required=false`。

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/lease` | 查询 holder |
| PUT | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/lease` | 获取锁和新 fencing token |
| DELETE | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/lease` | holder 主动释放 |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover` | Owner/Manager 抢锁 |

本期不建设 TTL 或续租。关闭编辑抽屉主动释放；遗留锁由 Takeover。旧 fencing token 永久不能写入。

### 12. Phase 2：Publication Attempt 与退役

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/upgrade-impact` | 发布前查看受影响 Bot |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/publications` | 冻结 Draft、创建 Attempt 和 task；幂等；202 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/publications` | Attempt 历史 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/publications/{attempt_id}` | Attempt 详情 |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/versions/{version}/materialization-retry` | 只重试同一 Version 物化 |
| GET | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/retirement-impact` | 退役影响检查 |
| POST | `/openapi/v1/spaces/{space_id}/skills/{skill_id}/retirement` | 整体退役 Skill |

发布事务：

```text
Draft=EDITING
→ Draft=FROZEN
→ 创建 Publication Attempt
→ 写入 ac_task_queue
```

Attempt 状态：

```text
PREPARING → SC_SUBMITTING → WAITING_SC → MATERIALIZING
                                     ↘ SUCCEEDED / FAILED / RESULT_UNKNOWN
```

- 明确失败：Draft 恢复 EDITING，相同 target version 可修改后再次提交。
- SC Published：创建不可变 Version，记录 `versionId/versionNumber`，完成 Store 物化后 SUCCEEDED 并清 Draft。
- 物化失败：不再次 POST SC，只重试同一 Version。
- RESULT_UNKNOWN：Draft 保持 FROZEN，普通用户不能处理。
- 不建设长期 Snapshot URI/Hash，不提供 Attempt cancel。
- Runtime reconcile 不使用 `ac_task_queue`；SC 发布与物化继续使用持久任务。
- Bot Binding、Service Artifact 或进行中操作存在时阻断整体退役。

### 13. Skill Center 映射与精确版本物化

- Space 持久化 SC Team ID；每次 SC 调用沿 Skill Ownership 显式解析 Team，禁止使用
  全局默认 Team。本期不提供 Team close/disable/delete。
- 新 Space Skill 的 `skill_uuid` 直接作为全局唯一 SC `skillCode`；可变名称只写
  `skillName`，名称允许重复且不参与查找。
- Version 保存精确 `sc_version_number` 和可用的 SC version ID；不新增重复的
  `sc_skill_code` 字段。
- Skill Center Gateway 只负责 transport、鉴权、配置、请求/响应归一化和错误分类；
  不修改 Skill/Draft/Attempt/Version，也不决定重试和补偿。
- Local/Fake Gateway 与 Corp/Prod Gateway 必须通过同一 conformance tests；
  Community 实现明确返回 unavailable。
- 一个 Publication Attempt 对 SC publish POST 最多调用一次。SC 返回受理成功时已经
  获取临时 package URL 的内容，因此临时 OSS 只服务该次调用，不成为长期 Snapshot。
- 请求超时且无法判断结果时进入 RESULT_UNKNOWN，通过稳定 skillCode + version number
  查询或人工确认，禁止自动重发 POST。
- SC 确认外部精确版本后创建 MATERIALIZING Version，并用持久任务下载、校验、安全
  解压、扫描和物化同一精确版本；Runtime reconcile 不复用该任务机制。
- 文件型 Runtime 的唯一 Center corpus 是
  `skills-pool/skill-center/<skill_uuid>/<sc_version_number>/`。Center 对 Local/Repo
  layout 切流中立，但进入 mapping publish/verify/inventory 生命周期。
- Teclaw 沿用 Artifact v4，新增 `skill-center` OSS Store，SkillRef path 为
  `<skill_uuid>/<sc_version_number>`；Bucket/Base 来自服务端配置。
- 文件型 Store 与 Teclaw Store 全部就绪后 Version 才转 PUBLISHED。任一失败保持
  MATERIALIZING，只重试同一 Version，不再次发布 SC。
- 禁止 `current/latest`、版本覆盖和数量型 GC；历史 Service Artifact 可能继续引用
  任意旧版本。

### 14. Track Latest 与 Service Artifact

- Personal/Desktop 和 Service Bot 草稿态使用 Track Latest。
- Space Skill 发布并物化后，对 Personal/Desktop Bot执行 Best-Effort 全量 reconcile。
- 不维护逐 Bot target/actual resolution 表。
- Service Bot 发布时把 `skill_uuid + 精确 SC version` 固化到 Artifact。
- 已发布 Service Bot 重启、扩容、回滚只读历史 Artifact，不动态解析 latest。
- Skill 新版本只更新 Service Bot 草稿；下次发布 Service Bot 时才进入新 Artifact。
- Local/Repo/Space 路径解析集中在统一 Resolver/Engine Adapter。

### 15. Bot Type × Engine 兼容矩阵

| Bot Type | 本期完整支持的 Logical Engine | Space/Center 交付 |
| --- | --- | --- |
| Personal | OpenClaw、Claude Code、Hermes、Teclaw | Draft Track Latest；重启/下一次 mutation 全量自愈 |
| Desktop | OpenClaw、Hermes | Track Latest；重启/重新 ACTIVE 时全量自愈 |
| Service | OpenClaw、Claude Code、Teclaw | Draft Track Latest；Published Release 固化精确版本 |

- Claude Code 的产品能力与 OpenClaw 一致；新增 Bot 实际使用 AICoding image 的
  personalCoding/applicationCoding 变体。物理目录由 Runtime probe/Adapter 判定，
  不能只凭 active_engine 分流。
- 历史纯 Claude Code、历史 active_engine=aicoding、Desktop Claude Code 等未在矩阵
  中的对象只保留现有安全读取/删除能力，不新增 Center 支持。
- 不支持组合在创建、变更 Engine、Direct activate、SkillSet activate 和 Service
  publish 等所有写入口统一失败关闭。
- 文件型 Engine 的路径编码集中在 Engine Adapter；Backend 只传结构化 Local/Repo/
  Center dependency。Teclaw 只接收 Artifact Store/Path。

### 16. Principal、幂等、错误

- Bot Skill/SkillSet：`UserOrDelegatedApp`，始终校验 Bot ACL。
- Space Skill创作、编辑、发布：必须有明确 User 身份并校验 Owner/Manager。
- `tenant/env/user_id` 从认证上下文取得，业务 body 不得任意指定。
- Bot Principal 不能修改 Skill、SkillSet 或 Space。
- Create Space Skill、Create Draft、Publish、Materialization Retry 要求 `Idempotency-Key`。
- Membership PUT/DELETE 天然幂等。
- Local 同名上传继续原地替换。

| HTTP | 类别 |
| --- | --- |
| 400 | wire/request 格式错误 |
| 403 | ACL、Owner/Manager、MCP 权限失败 |
| 404 | Skill/Space/Version/Attempt 不存在 |
| 409 | 状态机、Membership、Bot ready、runtime name 冲突 |
| 422 | SKILL.md、ZIP、Git 内容或参数校验失败 |
| 502/503 | Skill Center 或 Runtime 不可用 |

继续使用已发布 Gateway Envelope，并提供稳定 `error_code`。

### 17. 向前兼容门禁

1. 已发布 Local API 不传 `type` 时始终 Local-only；新产品显式 `type=ALL`。
2. 旧 Local raw ZIP、response、status、同名替换、active 和 delete 行为不变。
3. Deprecated `/openapi/v1/bots/skills/**` 不删除。
4. Legacy Skill/SkillSet BFF 只做 Compatibility Adapter，不拥有领域逻辑。
5. 七桃负责的 Space/Member/Favorite wire 不变。
6. Local、Repo、Space 不自动转换；Bot-local 长期保持 Bot-local。
7. Legacy Local/Repo 不要求补 UUID 才能读取；公开身份仍是 `ac_skill.id`。
8. Resolver 覆盖 Local/Repo/Space、OpenClaw、Claude Code(AICoding image)、Hermes、Teclaw 及产品支持的 Bot Type。
9. Service Bot v4 旧 Artifact 继续可读；Center 精确依赖采用 additive contract/capability gate。

### 18. 交付、切流与回滚

Phase 1 是 Phase 2 的控制面基础，但允许 Phase 2 的纯内部模块在接口稳定后并行开发。
功能启用必须遵循以下 Gate：

1. **Phase 1 Schema/Consumer first**：部署 Additive DDL、Installation Repository、
   Runtime Resolver、兼容 Adapter 和 OpenAPI 兼容测试，不改变产品流量。
2. **Phase 1 Enable**：切换 Local active 的内部事实源，开放 Repo/SkillSet/MCP
   canonical OpenAPI，产品显式使用统一 Gateway；通过 Phase 1 全量门禁。
3. **Phase 2 Consumer first**：先部署 Mapping v3、pool_center、Teclaw skill-center
   Store 和 Service Artifact 精确版本读取能力。
4. **Phase 2 Enable**：再开放 Space Skill 创建、编辑、发布、Track Latest 和退役。

Phase 1 回滚可以恢复旧 Adapter/Resolver，但不得丢失已经写入的 Installation；
回滚版本必须继续理解新表或先停止新写并验证等价投影。Phase 2 一旦生成 Space Skill、
SC Version 或含 Center 的 Service Artifact，安全回滚下限是保留 Center Consumer 的
兼容基线：停止新写，继续收敛 Attempt/Materialization，保留全部新表、Store 内容和
历史 Artifact，不能回到完全不认识 Center 的二进制。

## Testing Decisions

测试只断言外部行为、持久领域事实和跨边界合同，不锁定私有函数或 SQL 调用顺序。
优先使用以下四个最高测试 seam：

1. **Public OpenAPI seam**：通过真实 Router、认证上下文和 Gateway Envelope 验证
   请求、响应、错误、幂等与已发布 wire 兼容；每次实现后生成 OpenAPI 并运行
   compatibility gate。
2. **Skill/MCP Control Plane seam**：通过统一 Service API 验证 Asset、Installation、
   Membership、SkillSet 原子命令、权限和数据库事务，不从 HTTP Adapter 直测 SQL。
3. **Runtime Projection seam**：给定完整 Desired State，断言 Resolver 输出的完整
   Local/Repo/Center/MCP/CLI 投影；所有 Engine Adapter 复用同一组 contract fixtures。
4. **Publication seam**：使用 Fake Skill Center Gateway 与 Fake/临时 Store 驱动
   Draft、Attempt、Version、Materialization 状态机；真实 SC 只承担契约联调。

Phase 1 必测：

- 已发布 Local canonical 与 deprecated API 的 request/response/status/side effect
  回归；不传 type 仍为 Local-only。
- Local upload/replace/delete 与 Direct activate/deactivate；Repo/Space Direct
  activate/deactivate。
- SkillSet create/update/delete、Skill/MCP membership、整体 activate/deactivate、
  active Set 增删成员和三类冲突错误。
- Repo list/search/tree/sync 以及同步互斥；Legacy market Adapter 等价。
- MCP Catalog/permission/config 兼容与新 Bot MCP Direct 语义。
- Runtime 明确失败的方案 A 补偿、进程崩溃窗口、下一次 mutation 和 Bot restart
  全量自愈。
- Legacy Local/Repo/Bot-local 不转换，System Default Skill/MCP/CLI 仍然生效。
- 所有支持 Bot Type × Engine 组合及未支持组合 fail-closed。

Phase 1 Backend 完成定义：Gateway OpenAPI gate 通过，存量 BFF 与 canonical
OpenAPI 的等价测试通过，全部产品能力均存在可供前端切流的 Gateway Backend 合同，
Legacy 全矩阵无回归。产品前端是否已经完成调用切换和页面 E2E 属于独立的产品切流
验收，不阻塞 Backend Phase 1 代码完成，但在产品正式切流前仍必须由前端团队验证。

Phase 1 对新产品 PRD 的可测边界：Local 上传、Repo Catalog、MCP Catalog/权限、
添加到 SkillSet、SkillSet 整体激活/停用、System Default 和 Runtime 恢复可做完整
Backend 验收。真实 Space Skill 的 ZIP/Git 创建、Draft 编辑、Owner/Manager/Edit
Lease、发布、版本升级、Skill Center 物化、Track Latest、Service Artifact 精确版本
和退役属于 Phase 2；Phase 1 只能通过兼容 Fixture 验证已有 Space wire 和消费边界，
不能把它计为新产品主流程 E2E。

Phase 2 必测：

- Identity/Ownership/Owner/Manager/Draft 原子创建；并发升级和并发发布唯一。
- Team Edit Lease acquire/release/takeover/fencing；Personal 不需要 Lease；无 TTL。
- Git 导入/刷新失败不改变 Draft；FROZEN 后所有写入口拒绝。
- SC 单次 POST、明确失败、超时 RESULT_UNKNOWN、迟到结果和人工恢复。
- MATERIALIZING 对消费者不可见；文件型/Teclaw 物化全部成功后才 PUBLISHED。
- Track Latest 不维护逐 Bot actual；下一次 mutation/restart 自愈。
- Service Release 固化 V1 后，V2 发布不影响 V1 的扩容、重启和回滚。
- Skill retirement 的 Binding、Artifact、Attempt、Materialization 前置阻断。
- Local/Repo/Center 并存、Mapping v2/v3、Teclaw v4 兼容和 Store 访问隔离。

Phase 2 完成定义：产品主流程 E2E、SC pre 联调、多引擎矩阵、Service Artifact
回滚和 Phase 1 全量回归全部通过。

## Out of Scope

- Space 删除。
- Local/Repo/Space 原地转换。
- 任意 Git URL 导入 Legacy Repo Catalog。
- 单个 Published Version 下线、删除或覆盖。
- Publication Attempt cancel。
- Runtime effective/reconcile 运维接口。
- Runtime 持久重试或逐 Bot observed resolution 查询。
- 普通用户处理 `RESULT_UNKNOWN`。

## Further Notes

### 团队 Review

涔涔重点 Review 以下合同；开发可以按 Spec/Ticket 先行，但任何 Review 结论必须先
更新本文、受影响 Ticket 和兼容测试，再进入对应 PR：

1. 已发布 Local、MCP、Space wire 是否全部兼容。
2. SkillSet 原子全选语义是否匹配现有产品。
3. Installation 作为当前有效物化清单是否保持唯一语义。
4. Repo canonical 路径与旧 Market Adapter 是否无遗漏。
5. Skill/MCP 全量 Resolver 是否覆盖重启和下一次 mutation 自愈。
6. Space Skill Draft/Version/Attempt 是否避免单一 status 混用。
7. Service Artifact 精确版本是否不会被 Track Latest 污染。
8. Phase 1 与 Phase 2 是否可以独立验收。

### Ticket 治理

- 本 Spec 发布后重新执行 to-tickets，按 Phase 1/Phase 2 建立 blocking edges。
- Phase 1 采用五个纵向实现 Ticket 加一个 Backend Gate，顺序如下：
  1. `P1-01 Installation 深模块与 Local 兼容` 无业务实现 blocker，先建立 Skill/MCP
     Installation 事实源、统一 Service 边界和已发布 Local wire 兼容。
  2. `P1-02 Repo Catalog 与 Direct API`、`P1-03 SkillSet Skill 原子语义` 均被
     P1-01 阻塞，P1-01 完成后可以并行。
  3. `P1-04 MCP Direct 与 SkillSet 语义` 被 P1-03 阻塞，复用同一套 SkillSet
     原子命令和 Installation 语义。
  4. `P1-05 统一 Resolver 与 Runtime 全量投影` 被 P1-02、P1-03、P1-04 阻塞。
  5. `P1-GATE Backend 全量兼容与矩阵验收` 被 P1-05 阻塞，并复用已经落地的
     Legacy 回归基线；同时从真实 Backend Router 生成并提交正式
     `src/gateway/configs/schemas/bots.openapi.json`，供涔涔通过 Swagger/Redoc Review。
- 不创建 Phase 1 前端实现 Ticket；前端切流只作为独立产品发布验收项，由前端团队
  依据生成 OpenAPI/Swagger 执行。
- 旧 #1165～#1187 必须逐条归类为“已完成并关闭”“修订复用”或“被新 Ticket
  取代并关闭”；禁止保留两套同时可领取的 ready-for-agent 工作。
- 每个实现 Ticket 使用独立上下文和 `implement` 流程，以小 PR 合入
  `dev_refactory_collaboration`；一个 PR 只关闭一个可独立验收的纵向行为。
- Phase 2 Ticket 可以在其真实 blockers 完成后并行，但 Phase 2 功能启用必须等待
  Phase 1 Gate。

### 前端接口文档

前端最终以 Gateway 生成的 OpenAPI JSON/Swagger 为机器可读权威。实现前附带的
Frontend API Review 文档只用于提前对齐路径、产品流程、字段扩展和状态映射；不得
代替生成 artifact。实现 PR 必须同步更新生成 OpenAPI，前端不从本文猜测未声明字段。
