# Skill 能力全量 OpenAPI Gateway 化与 Space Skill 升级 Spec

> 状态：**正式 Spec / 开发与团队 Review 共同基线**
> 初版日期：2026-08-20
> 最后修订：2026-08-30
> 目标分支：`dev`
> 本文是 Phase 1、Phase 2 开发、测试、Ticket 拆分和团队 Review 的唯一方案基线。
> 本次修订基于 `github/dev@b68ec64f1` 与产品 PRD Mock
> `Teamclaw_PRD_new origin/master@72de5d1c`；原型只作为产品 User Story 证据，未定义行为
> 以本文最终领域决策为准。

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
的资产类型；Bot 当前有效的 Skill/MCP 只从 Installation 读取。所有会改变有效能力的
命令实时维护 Installation；迁移期所有 Effective Read 先经统一 Reader 幂等 flush
SkillSet、System Default 与 exclusion 语义，再只读 Installation。所有 Runtime 更新和
Bot 重启都经过同一个 Resolver/Projector 生成完整投影。

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
15. 作为用户，我希望同一资源最多属于一个 SkillSet（System Default 包含在内），避免来源冲突。
16. 作为用户，我希望 Direct active 资源加入任何 SkillSet 前被要求先停用 Direct 来源。
17. 作为用户，我希望任何 SkillSet 管理的资源不能被单独 activate/deactivate；Default 成员通过 exclusion 控制。
18. 作为平台管理员，我希望 System Default 始终生效且不可删除或停用。
19. 作为 Repo 市场用户，我希望搜索、列表、目录树和同步都从 aiworkbench master 得到一致结果。
20. 作为 Repo 市场用户，我希望同步接口同步完成后才返回，并在并发同步时收到稳定冲突。
21. 作为 MCP 用户，我希望 MCP 与 Skill 使用一致的 Direct/SkillSet 激活语义。
22. 作为 MCP 用户，我希望安装、加入 SkillSet 或激活前完成权限校验。
23. 作为 Bot 用户，我希望 Bot 重启时根据数据库 Desired State 重建全部 Skill/MCP 投影。
24. 作为 Bot 用户，我希望一次 Runtime 明确失败不会留下新的半套 Desired State。
25. 作为运维人员，我希望再次 activate/deactivate 或修改 active SkillSet 能全量修复上次崩溃窗口。
26. 作为 Space 用户，我希望从本地文件夹或 Git 创建全新的 Space Skill Identity，而不转换 Legacy Repo。
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
43. 作为产品负责人，我希望仍被 SkillSet、Artifact 或进行中操作引用的 Skill 不能下线。
44. 作为前端开发者，我希望实现后直接使用 Gateway OpenAPI/Swagger 获取准确请求、响应和错误合同。
45. 作为发布负责人，我希望 Phase 1 和 Phase 2 各有独立验收门禁，并能定位安全回滚下限。
46. 作为 Team Space 普通成员，我希望可以向当前 Skill Owner 申请编辑权限，并在工单中查询结果。
47. 作为 Skill Owner，我希望批准编辑申请后申请人原子获得 Manager Grant，拒绝时不改变 Skill Grant。
48. 作为前端开发者，我希望列表返回稳定的 Skill/Draft/Attempt/Lease 领域摘要与当前调用者权限，而不把页面按钮 ViewModel 写入公共合同。
49. 作为 Bot 工坊用户，我希望“引用市场 Skill”同时展示 TeamClaw Repo 与 SkillCenter Public 结果，并由前端按来源调用不同的添加命令。
50. 作为 SkillCenter Public 用户，我希望一次选择多个 Skill 后获得可跨刷新恢复的异步引用进度，且只有物化完成后才加入 SkillSet。
51. 作为发布者，我希望发布前看到可能受 Track Latest 影响的 Bot，但该预览不阻断发布，发布成功后由后端重新计算真实候选。
52. 作为发布者，我希望自动重试耗尽后可以针对同一个 Attempt 恢复准备、查询 SC 结果或重试物化，而不会重复发布。
53. 作为 Skill Owner/Manager，我希望下线前看到完整血缘；无引用时保留历史 Published Vn 并下线该资产，必要时复制 Vn 为独立新 Skill。

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
- Skill Center 发布、精确版本物化、升级传播和可恢复下线。
- 七桃负责的 Space、Member、Join Request、Favorite 接口保持现有合同，不在本文重构。
- 当前 `GET /openapi/v1/bots/spaces/{space_id}/skills` 的旧 `SpaceSkillItem` 尚无真实
  调用方，且已与前端确认不作为兼容合同；Phase 2 直接以本节的
  `SpaceSkillSummary` 替换，不保留 `status/draft_status/current_user_skill_role/
  can_edit/can_grant/can_apply_edit` 双轨字段。

### 3. 最终领域模型

#### 3.1 Skill Asset

| 类型 | 资产归属 | 内容来源 | Bot 删除语义 |
| --- | --- | --- | --- |
| `LOCAL` | Bot-owned | Bot 上传 ZIP | 删除 Local 资产 |
| `REPO` | 环境共享 | `security_release/aiworkbench` master 扫描 | 不能删除共享资产 |
| `SPACE` | Space-owned 或 SC Public 懒物化的环境共享只读资产 | Draft 发布到 Skill Center，或 SC Public exact download | 不能通过 Bot API 删除 |

公开 `skill_id` 统一使用十进制字符串形式的 `ac_skill.id`。Item API 根据 `skill_id` 在服务端解析类型，调用方不传 `type`。

`SPACE` 是现有 OpenAPI 中 Center-backed Runtime asset 的统一类型，不新增 `CENTER` wire enum。
TeamClaw 工坊 Skill 通过 `ac_skill_space_binding + Grant` 表达 Space ownership；SC Public 懒物化
资产没有 Space Binding/Grant，是环境共享只读 Asset。两者运行时都使用内部 UUID + exact
Version 的 Center corpus，但创作/授权能力不同。

#### 3.2 Bot 当前有效 Skill

`ac_bot_skill_installation` 是 **Bot 当前有效 Skill 的唯一读取事实**：

```text
UNIQUE(tenant, env, owner_id, bot_id, skill_id)
```

- 行存在即 active，不保存 inactive 行。
- 不保存 `source_type`、`skill_set_id`、`direct_active` 或 Runtime observed status。
- `Installation + 任意 reaching SkillSet Membership` 表示由 SkillSet 管理。
- `Installation + 无任何 reaching Membership` 表示 Direct 管理。
- Active 普通 SkillSet、未 exclusion 的 System Default 成员与 Direct Activate 最终都
  物化到同一张表；Resolver 禁止再从 SkillSet/Default/exclusion 内存合并一套 Effective Skill。

| 场景 | Asset | Membership | Installation |
| --- | --- | --- | --- |
| Local 已上传未激活 | 有 | 无 | 无 |
| Repo/Space 在 inactive SkillSet | 有 | 有 | 无 |
| Direct active | 有 | 无 | 有 |
| Active 普通 SkillSet 成员 | 有 | 有 | 有 |
| System Default 未 exclusion 成员 | 有 | Default | 有 |
| System Default excluded 成员 | 有 | Default | 无 |

切流不要求先完成全库 backfill。`BotCapabilityStateReader` 是回答“Bot 当前有效能力”的唯一
DB Read seam：每次 Effective Read 先调用统一 `flush_installations()`，把普通 SkillSet、
System Default 与 Default exclusion 的当前规则幂等物化到 Installation，再只读 Installation。
冷 Bot/Legacy drift 因此会在首次读取时修复；稳态读取走 read-only fast path。

Flush 是纯 DB→DB 收敛：active ordinary Set 与未 exclusion Default 成员补行；inactive
ordinary Set 与 excluded Default 成员删行；无任何 Set 能解释的 Direct Installation 永不触碰。
它不访问设备、不触发 Runtime Projection。所有写命令仍在同一 UoW 中 eager 维护
Installation，flush 只承担迁移期兼容和漂移修复。未来完成一次全量 DB flush 后可以移除
读前保护，但在此之前所有 Effective Read 必须通过 Reader。

不存在公开的 Installation Resource API：

```text
PUT/GET/DELETE /bots/{bot_id}/skills/{skill_id}/installation
```

#### 3.3 SkillSet 原子语义

- 一个普通 SkillSet 只有 active 或 inactive，不存在半选。
- 同一 Bot 下，同一 Skill/MCP 最多属于一个 SkillSet，System Default 包含在内；excluded
  仍然属于 Default。
- Direct active 资源不能加入任何 SkillSet；必须先 Direct deactivate。
- Installation 行存在本身不证明 Direct：若该行可由 active ordinary SkillSet 或未 exclusion
  的 System Default Membership 解释，则资源是 Set-managed，加入第二个 Set 时返回
  `RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET`，而不是 `RESOURCE_DIRECT_ACTIVE`。
- 已属于任何 reaching SkillSet 的资源不能单独 activate/deactivate；Default exclusion 只能
  通过 Default Set-scoped add/remove 命令切换。
- 激活/停用 SkillSet 原子批量增删成员对应的 Installation 行。
- 向 active SkillSet 添加成员时，同事务创建 Installation。
- 从 active SkillSet 移除成员时，同事务删除 Installation。
- System Default 始终 active、不能删除/停用/编辑共享 Membership；其 per-Bot exclusion 与
  Installation delta 必须同事务写入。

稳定错误码：

```text
RESOURCE_DIRECT_ACTIVE
RESOURCE_MANAGED_BY_SKILL_SET
RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET
```

#### 3.4 MCP 对齐

`ac_bot_mcp_installation` 与 Skill 使用相同定义：它是 Bot 显式有效 MCP 的唯一读取事实，不保存 inactive 行。

- Direct activate/deactivate 增删 Installation。
- Active ordinary SkillSet 与未 exclusion Default MCP 成员原子增删 Installation。
- Skill 声明的 MCP dependency 是版本化 Runtime 派生，不写 MCP Installation。
- 安装、加入 SkillSet、激活前执行服务端权限校验。
- MCP Catalog、用户配置、调用身份仍是独立事实。

Engine/Template Default MCP Policy 是唯一明确例外：它来自代码配置而非 Set Membership，
不进入 Installation flush。Runtime Effective MCP 只能在统一 Collector 中计算：

```text
Engine/Template Default MCP Policy（应用其 exclusion）
∪ BotCapabilityStateReader.active_mcp_server_codes
∪ Active exact Skill Version 的 mcp_dependencies
```

Asset、Draft、Grant、Lease、Publication、Version 和 Artifact 实现不得复制另一套 MCP Union。

#### 3.5 持久化合同

Phase 1 新增两张 Desired State 表：

| 表 | 唯一键 | 行语义 |
| --- | --- | --- |
| `ac_bot_skill_installation` | `tenant + env + owner_id + bot_id + skill_id` | Bot 当前有效 Skill |
| `ac_bot_mcp_installation` | `tenant + env + owner_id + bot_id + server_code` | Bot 当前显式有效 MCP |

两表都不保存 inactive 行、来源类型、SkillSet ID 或 Runtime observed status。
普通 SkillSet Membership 继续复用既有关系表；同一 Bot 的同一资源最多属于一个 Set，
Default Set 包含在内，excluded 仍算 Membership。System Default 保留平台默认
Skill/MCP/CLI 配置；exclusion 是 Default Set 自身的 per-Bot deactivate，不把资源交回 Direct
控制。Local Direct 不再借用 Default exclusion 表达。

Phase 2 复用一条 `ac_skill` 作为跨版本稳定 Identity：

- Legacy Local/Repo 可以没有 `skill_uuid`；公开 API 身份仍是 `ac_skill.id`。
- 新 Space Skill 必须由服务端生成唯一 UUID，并直接作为 SC `skillCode`。
- `ac_skill` 只增加活动 Draft 目标版本、Draft 状态、Git 来源和 TeamClaw-local Offline 事实；
  不新增 `ac_skill_draft`，也不把 Version 再写成一条 `ac_skill`。
- Offline 使用 `offline_at/offline_by`（或实现 Review 时按仓库命名确认的等价字段）；当前尚未
  被业务使用的 `retired_at/retired_by` 旧骨架不作为最终合同，必须在实现前替换/废弃，禁止用
  永久退役字段承载可恢复语义。
- `ac_skill_space_binding` 是一个 Skill 在一个环境下唯一的 Space Ownership。
- `ac_skill_grant` 表达恰好一个 Owner 和多个 Manager，不读取 Legacy 权限表。
- `ac_skill_draft_edit_lease` 是永久协作锁事实，保存 holder 与单调递增 fencing
  token；本期没有 TTL、expires、renewal 或自动释放语义。
- Draft 内容使用单 immutable ZIP Revision Store，不建设多对象 folder 或 READY marker。
  `DraftContentStore` 只暴露 `write_revision/read_revision/delete_revision`；Active Draft
  无 TTL。持久 locator 为 `draft://<skill_uuid>/v<target_version>/<revision_id>`，业务层不
  解析物理路径。默认 OSS object key 为
  `aidesktop/aidesktop_<env>/bolt_shared/skills-upload/space-drafts/<tenant>/<env>/`
  `<skill_uuid>/v<target_version>/revisions/<revision_id>.zip`。Draft 数据库命令与 CAS/补偿
  仍由后续 P2-01/P2-03 application service 实现，不属于 Store。同一 exact revision key
  必须使用 object-store create-if-absent 原子创建；已存在同字节为幂等，不同字节冲突失败。
  Object read 必须区分 FOUND/NOT_FOUND/FAILED，禁止把存储故障误判为不存在后覆盖。
- `SkillPackageValidator` 是 Local、Draft 和精确版本物化共用的纯 package 边界：负责
  安全相对路径、压缩/展开大小与文件数、唯一 `SKILL.md`、frontmatter 的
  name/description/config、wrapper、平台 metadata 忽略和 deterministic canonical ZIP，
  返回稳定 `ValidatedSkillPackage`；默认入口严格要求 frontmatter。只有既有 Local 上传
  生命周期可调用显式 legacy-compatible 入口兼容无 frontmatter 历史包，Draft、Git import
  和精确版本物化不得继承该 fallback。Validator 不拥有 Bot 授权、容器/Pool 写入、数据库
  或 Runtime。
- `ac_skill_version` 保存不可变 version ordinal、SC version number/version id、
  冻结元信息和 MATERIALIZING/PUBLISHED 状态，不保存长期 Snapshot URI/Hash。
  `publication_attempt_id` 必须允许为空：TeamClaw 工坊发布产生的 Version 指向
  Publication Attempt；SC Public 懒加载 Version 没有 TeamClaw 发布动作，值为 `null`，
  不得伪造 Publication Attempt。
- `ac_skill_publication_attempt` 保存幂等键、目标版本、冻结的
  `frozen_draft_locator`、外部提交阶段、失败原因和 RESULT_UNKNOWN；一个 Skill 同时最多一个
  进行中 Attempt。`frozen_draft_locator` 是 Attempt 创建事务内从当前 `ac_skill.zip_url` 获取的
  immutable Revision snapshot；迁移列为 nullable 仅用于兼容存量，所有新 Attempt 必须非空，
  active 存量缺失时 fail closed。
- `ac_skill_center_reference_operation` 保存一次把 SC Public 外部 `skill_code` 懒物化并加入
  目标 Bot SkillSet 的持久业务过程；每个 code 一行、同一批共享 `request_id`。它保存冻结
  `sc_version_number`、最终 `resolved_skill_id`、状态和错误，不替代 Asset、Version、Membership
  或 Installation。终态永久保留，本期不提供 cancel/delete/retry 或 TTL 清理。
- SC Public 外部身份不复用同名 Local/Repo/Space：`ac_skill.git_path=center://<external_skill_code>`
  保存外部定位，TeamClaw 为该资产生成自己的 UUIDv4 `skill_uuid`；Canonical OSS、Runtime、
  Artifact 始终使用内部 `skill_uuid + exact sc_version_number`。不新增 `origin_kind/corpus` 持久列；
  `LOCAL/REPO/CENTER` corpus 由 locator 派生，并在使用点复用一个全局枚举。
- 已有 `zip_url` 保存持久 Draft locator，`package_url` 仅保存当前 Publication 调 SC 的临时
  signed URL；线上字段可复用，不新增同义列。
- 所有新增表的 `env` 非空，所有查询和唯一键同时包含 tenant/env。

当前目标分支已存在部分 Additive Schema。实现必须以本节覆盖其中仍残留的 TTL、
Space 删除或旧状态定义；已合入代码不因“已经存在”而自动成为新合同。

### 4. Runtime 一致性

```text
SkillSet/Default/exclusion rules
        ↓ flush（DB→DB，无设备 side effect）
ac_bot_skill_installation + ac_bot_mcp_installation
        ↓ BotCapabilityStateReader
RuntimeProjectionResolver（纯计算）
        ↓
BotRuntimeProjector（DB→Engine）
        ↓
skills-local / skills-repo / skill-center / MCP/CLI projection
```

1. 所有回答或驱动“Bot 当前真正能用什么”的 use case 必须从
   `BotCapabilityStateReader` 读取；Asset、Draft、Version 和历史 Artifact 读取不触发 flush。
2. Mutation 前校验 ACL、Bot ready、Ownership Policy、MCP 权限和 runtime name 冲突。
3. Runtime name 统一使用 `ac_skill.name`，由单一 `RuntimeNamePolicy` 规范化。
4. Command Service 使用共享 `mutate → project` 流程。Installation 提交后 Runtime 投影采用
   best-effort：设备不可达、源文件缺失或不可安全覆盖的容器侧实体返回 `PENDING` / `DEGRADED`，
   不补偿已经提交的 Desired State；DB/ACL/Ownership/Offline/路径安全等写前校验仍 fail closed。
   Projector 不拥有业务事务。
5. Projection 默认按受影响 Domain 选择 Scope；启动/重新 ACTIVE、Service Draft build 和需要
   全量自愈时使用 `ProjectionScope.everything()`。Teclaw 仍一次 compose/deliver whole Artifact。
6. activate/deactivate 即使 `changed=false` 仍允许投影，作为显式自愈入口。
7. 不建设无限期 Runtime observed-state 对账和逐 Bot actual-version 表。Track Latest 与首次
   Device ACTIVE 失败允许 deadline 有界的 `ac_task_queue` 持久重试；普通命令同步投影并返回
   可重试的 `PENDING` 或需人工处理的 `DEGRADED`。
8. Device ACTIVE 首次完整投影失败时入队唯一
   `BOT_RUNTIME_BOOTSTRAP_RETRY(binding_id)`；执行前必须确认 binding 仍是当前 Draft binding，
   替换/释放或 Published Service binding 均 no-op。
9. Installation 是 Effective Desired State，不是 Runtime observed state。Bot offline/not-ready 的
   变更沿现有 Command 合同失败关闭，不留下新的半套 Desired State。
10. Center 总是使用 `pool_center` exact-version corpus，不参与 Local/Repo `legacy|pool` 选择；
    禁止 `legacy_center`、按名称寻址和 Backend 硬编码 Engine home 路径。

### 5. Phase 1：Bot Skill OpenAPI

#### 5.1 已发布 Local 合同

| Method | Path | 用途 | 兼容要求 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/skills` | Local Skill 列表 | 不传 `type` 时严格保持 Local-only |
| POST | `/openapi/v1/bots/{bot_id}/skills` | raw ZIP 上传或同名替换 Local | 新建 inactive；替换保留 `skill_id` 和 active |
| POST | `/openapi/v1/bots/{bot_id}/skills/upload-folder` | multipart 文件夹上传 | additive；沿用旧 `/api/skills/upload` 的 `files + file_paths` wire 语义，并与新 raw ZIP OpenAPI 共用 `LocalSkillUploadService` |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | Local 详情 | 旧 wire 不变 |
| DELETE | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | 删除 inactive Local 资产 | Repo/Space 不能复用为资产删除 |
| POST | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate` | 激活 Skill | 旧 Local 不变；additive 支持 Repo/Space |
| POST | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate` | 停用 Skill | 旧 Local 不变；additive 支持 Repo/Space |

已发布 `/openapi/v1/bots/skills/**` deprecated shim 全部保留。

#### 5.2 Additive 统一接口

| Method | Path | Request | 语义 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/skills` | optional `source=LOCAL` | 省略时列出 Bot 全部可达 Skill；`LOCAL` 只列出该 Bot 上传的 Local Skill |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}` | path | 服务端解析类型和 Bot 关系 |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/content` | path | 返回可消费 `SKILL.md` |
| GET | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` | path | Bot 级参数 |
| PUT | `/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` | 全量参数对象 | 按 `SKILL.md config` 校验；保存失败必须传播 |
| GET | `/openapi/v1/bots/skills/{skill_id}/readme` | path | Botless 共享资产详情：Repo 或已发布 Space/Center 的可展示 `SKILL.md` |

统一列表集合：

```text
Bot-owned Local Asset
∪ 普通 SkillSet Membership
∪ 当前 Installation
```

旧 `active` 字段表示当前是否存在 Installation。只增加 optional `source=LOCAL`，不增加 `direct_active/effective_active/sources`。

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

Botless `readme` 只凭全局 `skill_id` 定位共享 Repo 或已发布 Space/Center Asset；`user_id` 仅表达
调用者。它不接受 bot/entity/engine 参数，不读取 Bot-owned Local、Space Draft 或未发布 Skill。
查看某个 Bot 已安装 Skill 的内容继续使用 Bot-scoped `/content`。

旧 `/api/skills/upload` 的实现本期原样冻结，等待产品链路下线；不将其纳入新上传模块改造。新 OpenAPI 的 raw ZIP 与 multipart 文件夹入口必须在 HTTP 解码后汇入同一个 `LocalSkillUploadService.upload_local_skill()` 生命周期；两种输入先由共享 `SkillPackageValidator` 规范化为同一 package contract，再共用同名替换、存储、DB 更新和失败补偿。

#### 5.3 Skill 添加来源与 Skill Center 懒物化

添加弹窗读取三个独立目录。Bot 工坊“引用市场 Skill”由前端分别查询 TeamClaw 与
SkillCenter Public 后聚合展示；Backend 不建设混合分页接口。用户确认后前端按来源拆分命令：

| 入口 | 接口 | 权威来源 |
| --- | --- | --- |
| TeamClaw 市场 | `GET /openapi/v1/bots/skills/repository` | aiworkbench 扫描得到的 `git://` Asset |
| Skill Center 市场 | `POST /openapi/v1/bots/market/skill-center/skills` | Skill Center PUBLIC 市场；Gateway 固定 `team_id=None`、`access_level=PUBLIC` |
| 工坊 Skill | `GET /openapi/v1/bots/spaces/{space_id}/skills/consumable` | 当前 Space 的 Published 且已物化 Version |

旧 `POST /openapi/v1/bots/market/skills` 保留为 TeamClaw 市场兼容入口。Skill Center 的团队搜索不得作为工坊目录；TC 才是 Space、Grant、Draft 和消费状态的权威。

未物化 SC Public Skill 的“查看详情”复用现有 search response 的 `homepageUrl`，由前端 iframe
嵌入 SkillCenter 页面；查看行为不创建 `ac_skill`、不下载 Package、不触发物化。Botless
`readme` 只服务已经成为 TeamClaw 共享资产的 Repo/Published Space/已懒物化 Center Skill。

- TeamClaw/工坊结果已经存在 `ac_skill.id`，调用普通
  `PUT /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}`。
- SkillCenter Public 结果只有外部 `skill_code`，调用以下专用异步 Reference；不使用
  `market_source + identifier` 通用 Router。

Skill Center 市场有万级且持续增长的外部记录，禁止全量扫描/写入 `ac_skill`。用户确认引用时才按需物化。公开 Interface 使用 Bot/SkillSet-scoped 的专用异步 Reference，不复用通用 Membership 的 `skill_id`，也不传 `market_source`：

```text
POST /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references
GET  /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references
GET  /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}

Idempotency-Key: required
{
  "skill_codes": ["public-skill-a", "public-skill-b"]
}
```

POST 单次最多 20 个 code，返回 `202 + request_id + reference_id[]`；相同 Key 返回原
Operations 并幂等确保 live Task。每个 code 独立持久状态：

```text
QUEUED → RESOLVING_VERSION → MATERIALIZING
       → ADDING_TO_SKILL_SET → PROJECTING_RUNTIME
       → COMPLETED | FAILED
```

collection 使用标准 `page/page_size<=100`，默认倒序并支持 `request_id/status` 过滤；终态永久
保留，本期无 cancel/delete/retry。前端关闭弹窗后仍可恢复进度；失败项由用户重新提交。
POST 受理时要求目标 SkillSet live；若执行期间 Set 被删除，item 以 `SKILL_SET_NOT_FOUND`
失败。为保证失败结果仍可恢复，collection/detail GET 以 Bot ACL + Operation 中冻结的 exact
`skill_set_id` 授权和过滤，不要求该 Set 当前仍存在。

Worker 以受控并发（初始 4）解析/物化，冻结外部 `skill_code → exact version`，收集本批成功
`skill_id` 后只调用一次 `SkillSetManagementService.add_skills()`，active Set 只投影一次。
物化成功前禁止写 Membership；物化成功但 Membership/Projection 失败时保留共享 Asset/Version，
补偿目标 Bot 的 Membership/Installation。最终写入前必须重验最新 Bot、Owner、Actor 权限和
SkillSet 状态；inactive Set 只写 Membership，active Set 同时维护 Installation/Runtime。

`ac_task_queue` key 为 `skill-center-reference:<request_id>`，只承担执行。Operation 事务提交后
再 enqueue；失败时 POST 返回 503，相同 Idempotency-Key 重放原 Operations 并重新确保 Task。
瞬时 SC/OSS 错误有限自动 Retry，永久错误或 Retry 耗尽使对应 item `FAILED`。当前阶段只冻结
合同；没有真实 Backend 实现前不得加入 Gateway 正式 OpenAPI artifact。

### 6. Phase 1：Repo Catalog

Repo 唯一内容源是 `security_release/aiworkbench` master。用户不能通过此接口导入任意 Git URL。

| Method | Path | Request | 语义 |
| --- | --- | --- | --- |
| GET | `/openapi/v1/bots/skills/repository` | `keyword?,path?,sort=latest\|hottest,page,page_size` | 全局 Repo 列表与搜索；真实分页 |
| GET | `/openapi/v1/bots/skills/repository/tree` | 无 Bot 参数 | aiworkbench 目录树 |
| GET | `/openapi/v1/bots/skills/repository/{skill_id}` | path | Repo 资产详情；Phase 2 additive 支持 Consumable Space Skill |
| POST | `/openapi/v1/bots/skills/repository/sync` | 无 Bot/Entity/Engine/Git URL | 同步 aiworkbench master |

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
| POST | `/skill-sets` | 创建 active SkillSet；要求 `Idempotency-Key`；空集合不触发 Runtime |
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

Bot 工坊中每个 active MCP 的 Owner/Caller 身份配置复用已经发布的 Caller Identity 合同，不在
SkillSet Membership 中重复保存：

```text
GET   /openapi/v1/bots/{bot_id}/caller-context
PATCH /openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type
       {"call_type":"owner|caller"}
```

inactive Membership 尚未进入 Runtime，不创建独立 call-type；成为 active 后通过 Caller Identity
模块配置。Skill/MCP Control Plane 只负责 Effective MCP，Caller Identity 模块负责执行身份。

### 9. 七桃负责且冻结的 OpenAPI

以下接口以七桃当前方案和已发布合同为准，本文不重构：

```text
GET    /openapi/v1/bots/spaces
POST   /openapi/v1/bots/spaces/create
POST   /openapi/v1/bots/spaces/personal/initialize
GET    /openapi/v1/bots/spaces/{space_id}/members
POST   /openapi/v1/bots/spaces/{space_id}/members
PUT    /openapi/v1/bots/spaces/{space_id}/members/{user_id}/role
DELETE /openapi/v1/bots/spaces/{space_id}/members/{user_id}
POST   /openapi/v1/bots/spaces/{space_id}/join-requests
POST   /openapi/v1/bots/spaces/{space_id}/market-favorites
POST   /openapi/v1/bots/spaces/{space_id}/market-favorites/cancel
POST   /openapi/v1/bots/spaces/{space_id}/market-favorites/search
POST   /openapi/v1/bots/spaces/{space_id}/market-favorites/status
```

本期不支持 Space 删除，不调用 Skill Center close/disable/delete Team。

### 10. Phase 2：Space Skill Asset、Draft、Version

#### 10.1 创建与读取

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills` | 能力工坊 Skill 列表；`keyword?,page,page_size`，返回 `Page[SpaceSkillSummary]` |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills` | multipart 本地文件夹 + 幂等键创建 Identity、V1 Draft、Binding、Owner |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/import-from-git` | JSON Git source + 幂等键，映射同一创建命令 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}` | 创作详情：Draft、Version、Attempt、权限 |
| GET | `/openapi/v1/bots/skills/repository/{skill_id}` | 消费详情：只返回 latest Published |

两个入口均要求 `Idempotency-Key` 并汇入同一个 Draft Application Service。创建顺序为：
鉴权和共享 `SkillPackageValidator` 完整校验 → 生成 `skill_uuid/revision_id` → 写 immutable Draft
ZIP → DB 事务创建 `ac_skill Identity + V1 EDITING Draft + Space Binding + 唯一 Owner`。SC、
Installation、SkillSet、Runtime 和 Skills Pool 不进入创建事务。DB 失败时 best-effort 清理新 ZIP。

本地文件夹上传使用重复 `files` 与 JSON `file_paths` 保留相对目录，不要求浏览器生成 ZIP；
Backend 解码后与现有 Local folder upload 复用同一 package 校验模块，但不复用包含 Bot/Pool/
Runtime side effect 的整个 Local upload 生命周期。

Git 导入是 snapshot，不转换 Legacy Repo。仓库包含多个 `SKILL.md` 时，根目录优先；否则按
规范化 POSIX 父目录 bytewise lexicographical ascending 选择第一项，禁止依赖 walk 顺序或
非法后跳到下一项。持久化实际 `source_subdir + resolved branch + commit_sha`；后续 refresh
只读取同一 subdir。用户需要其他 Skill 必须显式提供更精确的 `subdir`。

`creation_request_id` 唯一范围 `(tenant,env,request_id)`；同 Key 同 Space 返回原 Skill，跨
Space 返回 `409 IDEMPOTENCY_KEY_REUSED`。V1 保存完成后立即存在持久 Draft，不依赖页面内存。

详情分别返回：

```text
latest_published_version
draft_target_version
draft_status
publication_status
```

SKILL.md 是名称和描述的唯一事实来源。`name` 创建后不可变；所有后续保存/refresh/publish
必须保持同名。`description` 随 Version 可变：Draft detail 返回 Draft description，
`ac_skill.description` 在新 Version PUBLISHED 后才更新为 latest Published description。
Published `SKILL.md` 不可原地修改；只允许在 EDITING Draft 中通过文件接口编辑并发布新
Version。产品本期不提供“展示名称/描述/图标即时生效”的独立 mutation：展示名称不得脱离
`SKILL.md.name`，描述不得绕过 Version，图标使用既有默认展示，未来有明确需求再 additive
增加独立 presentation metadata。

##### 10.1.1 工坊列表领域摘要

`GET /openapi/v1/bots/spaces/{space_id}/skills` 是能力工坊的集合读取接口。
它返回稳定领域摘要，而不是数据库表行或页面按钮 ViewModel。工坊列表包含 Offline Skill
供继续编辑和重新发布；消费型 Repository/Consumable 列表排除 Offline Skill。
`keyword` 由 Backend 对名称和描述做不区分大小写过滤，过滤后再分页；默认按
`gmt_modified DESC, skill_id DESC` 稳定排序。`page` 从 1 开始，`page_size` 默认 20、最大 100。

当前代码中的旧 `SpaceSkillItem` 从未被产品或外部调用方使用，不承担向前兼容；实现时
直接删除旧 `status/draft_status/current_user_skill_role/can_edit/can_grant/
can_apply_edit` 投影，以本节 `SpaceSkillSummary` 作为唯一响应模型。

每个 `SpaceSkillSummary` 至少返回：

```text
skill_id / skill_uuid / name / description / space_type / owner
lifecycle_status = DRAFT_ONLY | PUBLISHED | OFFLINE
latest_published_version
draft = { target_version, status = EDITING | FROZEN } | null
active_publication = { attempt_id, target_version, status } | null
actor = {
  skill_role = OWNER | MANAGER | null,
  permissions,
  pending_editor_request
}
lease_summary
gmt_created / gmt_modified
```

Published V1 与 Draft V2 可同时存在。`active_publication` 只返回当前进行中 Attempt
的摘要；历史仍通过 Publications 资源查询。`pending_editor_request` 只投影当前
调用者对该 Skill 的 PENDING `SKILL_COLLABORATOR` 工单，无待审申请时为 `null`。

`actor.permissions` 只表达当前调用者基于 ACL/Grant 是否有资格发起领域命令：

```text
edit_draft
publish_draft
delete_draft
create_upgrade_draft
offline_skill
manage_grants
transfer_owner
request_edit_access
takeover_lease
```

Permission 不表示命令在当前 Draft/Lease/Attempt 状态下一定成功。公共接口不返回
`AVAILABLE/BLOCKED/HIDDEN`、按钮文案或 Tooltip；前端根据领域事实和 permissions 生成页面。
所有命令接口仍必须在执行时重新校验权限和当前状态。

`lease_summary` 仅服务于列表锁图标和 holder 展示：

```text
required
state = NOT_REQUIRED | FREE | HELD_BY_ME | HELD_BY_OTHER
holder_user_id
holder_display_name
```

无 Draft 时 `lease_summary=null`；Personal Draft 返回 `required=false,state=NOT_REQUIRED`。列表不返回
fencing token；点击编辑后通过 Lease 资源重新查询/变更实时状态。列表也不内联全量
Grants、Versions、Publication 历史、文件树或发布/下线影响列表，这些由对应资源接口提供。

#### 10.2 Draft 与 Version

| Method | Path | 语义 |
| --- | --- | --- |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/upgrade` | 创建下一版本 Draft；要求幂等键 |
| GET/PUT | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path}` | 读取/保存单文件 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files` | Draft 文件树 |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git` | 从原 Git 来源手动刷新 |
| DELETE | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft` | 放弃 EDITING Draft；未发布且无外部事实时删除整个 Skill |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions` | Published Version 列表 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}` | 精确业务版本详情 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files` | 精确版本文件树 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path}` | 精确版本文件内容 |

创作详情已经返回完整 Draft summary（target/status/revision/metadata/source），不再提供独立
`GET .../draft`。产品没有“重新上传整个文件夹覆盖 Draft”入口，因此不公开
`POST .../draft/replace`；package replacement 保持 Draft 模块内部能力。文件树与单文件
GET/PUT 分别服务编辑器目录、内容读取和保存。

每次内容 mutation 使用：完整校验 → 写新 immutable ZIP → DB CAS
`EDITING + expected revision + Team fencing_token` → commit → best-effort 删除旧 ZIP。Personal
同样使用 expected revision CAS；FROZEN 拒绝全部内容写入。Git refresh 失败时 Draft 完全不变。

升级 Draft 从 TC Canonical Store 精确复制 latest Published Vn；Store 缺失或校验失败时从 SC
exact version 下载并修复 Store，再创建 Vn+1 Draft。禁止使用 current/latest、旧 Draft 或历史
package URL。发布成功后不保留 Draft；只有再次点击升级才创建下一版 Draft。

DELETE 要求 `expected_revision_id`，Team Lease 必须 FREE/HELD_BY_ME；无 Published Version，且
除终态 FAILED Attempt 外不存在 Version/Installation/Membership/Artifact 等外部事实时，删除
整个从未发布的 Skill 聚合及其 FAILED Attempt，否则只放弃升级 Draft。FROZEN 永不允许删除。
DB 事实先提交，OSS 做 best-effort 清理；只放弃 Draft 时同步失效当前 Lease/fencing token。

URL 中 `{version}` 是业务序号 `1/2/3`，不是 `ac_skill_version.id`。Published Version 不可修改、删除或单独下线。

所有回答“Bot 当前真正能使用哪些 Skill”的消费者必须经
`BotCapabilityStateReader.active_skill_assets()` 读取。Reader 在 Installation flush 和
`Installation JOIN ac_skill` 后，通过唯一 `SkillVersionResolver` 补齐 Center 资产的精确版本：

```text
Local / Repo → 保持 ac_skill 内容，不查询 Version
Center       → 单次批量查询各 skill_id 的最高 version_ordinal PUBLISHED Version
             → 返回 exact sc_version_number 与版本级 mcp_dependencies
```

禁止使用 `ac_skill.version/status`、SC latest/current、字符串版本排序或 MATERIALIZING Version。
Center 缺 `skill_uuid`、PUBLISHED Version、`sc_version_number` 或合法 dependency metadata 时，
属于 Runtime 计划解析错误：命令保留已提交 Desired State 并返回 `PENDING`，不能用不完整资产
投影半套运行时。一次 Runtime projection 只能消费 Reader/Resolver 返回的同一份不可变 assets
tuple，避免 Skill mapping 与 MCP dependency 跨版本。

单 Skill add/remove、Direct activate/deactivate 和 Default exclusion/un-exclusion 在声明本次
`ProjectionScope` 时必须遵循同一依赖来源：Local/Repo 读取 `ac_skill.mcp_dependencies`，Center
读取最高 ordinal 的 PUBLISHED `ac_skill_version.metadata_json.mcp_dependencies`。不得因为
Center 的资产级依赖列为空而把一次实际包含 MCP 变化的命令误报成 Skill-only projection。

Space 发布产生的 `ac_skill_version.publication_attempt_id` 非空；SC Public 懒物化没有
TeamClaw Publication Attempt，因此该列必须允许 NULL，不能伪造 Attempt。Published Service
历史实例不解析 latest，只读取冻结 Artifact 中的 exact Version。

#### 10.3 Owner、Manager、编辑权申请与放弃 Draft

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants` | 返回唯一 Owner 与 Managers |
| PUT | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{user_id}` | Owner 幂等添加当前 Space Member 为 Manager |
| DELETE | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{user_id}` | Owner 幂等移除 Manager |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer` | 原子转移唯一 Owner |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests` | Team Space 普通成员申请 Manager 编辑权；创建 `SKILL_COLLABORATOR` Work Order |
| DELETE | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft` | 放弃当前 EDITING Draft |

- 当前 Owner 可以转移 Owner；Space Administrator 也可从详情页执行转移，但必须记录
  原因。接收人必须是当前 Space Member。
- Owner 转移与旧 Lease 失效在同一事务完成；原 Owner 不自动保留 Manager 权限。
- 本期只有 Owner/Manager 两种 Skill Grant，不新增 Editor 或普通 Skill Member。
- Owner 直接 `PUT/DELETE managers` 不生成 Work Order。移除当前 Lease holder 的
  Manager Grant 时，必须在同一事务内使该 Lease/fencing token 失效。
- 删除升级 Draft 只放弃本次升级；首次从未发布的 Draft 在除终态 FAILED Attempt 外没有
  Version、Installation、SkillSet Membership、Artifact 或其他外部历史事实时，可以在同一
  事务中删除 FAILED Attempt、Draft 事实、Lease、Owner/Manager Grant、该 Skill 自己的 Space
  Binding 和 Skill Identity。
- FROZEN Draft 不能放弃，必须先由 Attempt 收敛到明确结果。

编辑权申请 body：

```json
{ "reason": "需要共同维护该 Skill" }
```

申请人必须是当前 Active Team Space Member，且尚不是 Owner/Manager。同一
`tenant + env + skill_id + applicant_user_id` 同时最多一个 PENDING 工单。前端不得调用
`/openapi/v1/bots/work-orders/events` 自行指定审批人；Skill 模块必须从当前唯一
Owner Grant 解析 reviewer。

申请成功返回 `work_order_id/work_order_no/status=PENDING`。申请人和 Owner 复用：

```text
GET  /openapi/v1/bots/work-orders?query_type=INITIATED_BY_ME&item_type=APPROVAL&biz_type=SKILL_COLLABORATOR&biz_id={skill_id}
GET  /openapi/v1/bots/work-orders?query_type=PENDING_FOR_ME&item_type=APPROVAL&biz_type=SKILL_COLLABORATOR&biz_id={skill_id}
GET  /openapi/v1/bots/work-orders/{work_order_id}
POST /openapi/v1/bots/work-orders/{work_order_id}/approval
```

审批通过必须在同一事务内锁定 Work Order、确认仍为 PENDING、重新确认
reviewer 仍是当前 Skill Owner、申请人仍是 Active Space Member、幂等写入
`ac_skill_grant(role=MANAGER,status=ACTIVE)`，再将 Work Order 收敛为 APPROVED 并写结果通知。
拒绝只关闭工单，不写 Skill Grant。Owner 转移后，待审 Skill 工单必须改由新
Owner 审批，旧 Owner 不得继续审批。

### 11. Phase 2：Edit Lease

独立数据库锁表，仅 Team Space Draft 需要；Personal 返回 `required=false`。

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease` | 查询 holder |
| PUT | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease` | 获取锁和新 fencing token |
| DELETE | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease` | holder 主动释放 |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover` | Owner/Manager 抢锁 |

本期不建设 TTL 或续租。关闭编辑抽屉主动释放；遗留锁由 Takeover。旧 fencing token 永久不能写入。

### 12. Phase 2：Publication Attempt 与可恢复下线

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publication-impact` | 发布前查看可能受 Track Latest 影响的 Bot；信息提示，不是门禁 |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications` | 冻结 Draft、创建 Attempt 和 task；幂等；202 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications` | Attempt 历史 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications/{attempt_id}` | Attempt 详情 |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications/{attempt_id}/retry` | 恢复同一 Attempt；按最新阶段分流，不要求新幂等键 |
| GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline-impact` | 下线影响检查 |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline` | 本地隐藏 Published Skill，并创建下一版 Draft |
| POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/copy` | 复制已下线 Skill 的精确 Vn 为独立新 Skill V1 Draft |

发布事务：

```text
Draft=EDITING
→ Draft=FROZEN
→ 创建 Publication Attempt
→ commit
→ 调用现有 TaskQueueService.enqueue()
```

本期明确接受业务事务与 Task enqueue 的有限非事务窗口，不采用 Transactional Pending Writer。
enqueue 失败时同一 Publication POST Idempotency-Key 或 Attempt Retry 必须返回原 Attempt 并重建
Task，不能创建新 Attempt/Version，也不能重复调用 SC publish。

Attempt 状态：

```text
PREPARING → SC_SUBMITTING → WAITING_SC → MATERIALIZING
                                     ↘ SUCCEEDED / FAILED / RESULT_UNKNOWN
```

- 明确失败：Draft 恢复 EDITING，相同 target version 可修改后再次提交。
- SC Published：创建不可变 Version，记录 `versionId/versionNumber`；只有全部 Canonical Store
  `verify_version` 通过后才将 Version 置为 PUBLISHED、Attempt 置为 SUCCEEDED 并清 Draft。
- 物化失败：不再次 POST SC，只重试同一 Version。
- RESULT_UNKNOWN：Draft 保持 FROZEN，普通用户不能处理。
- 不建设长期 Snapshot URI/Hash，不提供 Attempt cancel。
- Publication POST 不要求 `fencing_token`：Personal 直接发布；Team 重新校验权限和 Lease，
  `HELD_BY_OTHER` 拒绝，FREE/HELD_BY_ME 冻结服务端最新已提交 Revision。并发保存通过
  `EDITING + fencing_token` CAS 与发布冻结互斥。
- Worker 只读 Attempt 的 `frozen_draft_locator`，不得重新读取可能已变化的 `Skill.zip_url` 作为
  冻结输入。FAILED 后新建 Attempt 时，只有当前 `Skill.zip_url` 已不同于最近 FAILED Attempt
  的 locator 才能受理；`package_url` 仅保存 active Attempt 调 SC 的临时 signed URL，并在
  FAILED/SUCCEEDED 终态清理。
- Publication `request_id` 唯一范围为 `(tenant,env,request_id)`；相同 Key 只重放原 Attempt，
  用于不同 Space 或 Skill 时返回 `IDEMPOTENCY_KEY_REUSED`，不得创建第二个 Attempt。
- `PREPARING + sc_post_started_at=NULL` 的 Retry 可继续首次 submit；`SC_SUBMITTING/
  WAITING_SC/RESULT_UNKNOWN` 只能查询状态，禁止再次 publish；`MATERIALIZING` 只重试同一
  `skill_version_id`；FAILED 必须修改 Draft 后新建 Attempt；SUCCEEDED 幂等成功。
- 自动 Task Retry 有 deadline；耗尽后 Attempt detail/summary 返回：
  `recovery.state=AUTO_RETRYING|AVAILABLE|NOT_AVAILABLE` 与
  `recovery.kind=PREPARATION|SC_STATUS_CHECK|MATERIALIZATION|null`。前端三个恢复按钮统一调用
  Attempt Retry；普通 FAILED/SUCCEEDED 无恢复按钮。

前端阶段投影固定为：`PREPARING/SC_SUBMITTING/WAITING_SC` 显示“发布中”；SC 已形成
精确 Version 且处于 `MATERIALIZING` 时显示“物化中”；只有
`ac_skill_version.status=PUBLISHED` 且 Attempt=`SUCCEEDED` 才显示“发布成功”。
`RESULT_UNKNOWN` 单独显示“发布结果确认中”，不得提供普通重新发布按钮。Bot Track
Latest 是发布成功后的 Best-Effort 异步刷新，不参与发布成功门禁。

`upgrade` 只创建 Vn+1 EDITING Draft，不查询影响面。产品在发布确认时先 GET
`publication-impact` 展示 Bot 列表，用户点击“已知悉并发布”后调用 Publication POST；不签发
acknowledgement token。预览到 PUBLISHED 之间事实可能变化，Track Latest 必须在发布成功后按
最新 Installation/迁移期候选重新发现并由 Reader 复核，禁止信任前端预览列表。

#### 12.1 Offline、重新发布与血缘

产品“下线”是 TeamClaw-local 的终态 Offline，不是永久 Retirement，也不把历史 Published
Version 改回 Draft。无 blocker 时，Offline 仅写 `offline_at/offline_by`，保留不可变 Published
Vn；原 Skill 不再创建 Draft、升级或重新发布。用户需要继续创作时，复制精确 Vn 为新 UUID、
独立新 Skill 的 V1 Draft；发布新副本会在同一 SC Team 创建独立 SC Skill，绝不复用原 SC 身份。

Offline 期间从 TeamClaw 市场和 consumable 列表隐藏，禁止新的 Direct activation、Membership
和其他 Bot 消费；Owner/Manager 仍可查看历史、编辑 Draft 和发布。它不调用 Skill Center
删除/关闭/下线，因此 SC 外部页面可能持续可见。永久 Retirement、SC 全局下线和单 Version
offline 均不在本期范围。

GET impact 供产品预览，POST 必须在事务内锁定 Skill 并重新检查全部 blocker；没有 force、
管理员绕过或“已知悉后继续”。Owner 与 Manager 均可查看和执行，普通 Member 无权限。

| blocker | 下线是否拒绝 |
| --- | --- |
| Active/Frozen Draft | 是；先放弃或完成当前 Draft/Attempt |
| 进行中或 RESULT_UNKNOWN Attempt | 是 |
| 任意普通/Default Membership，含 inactive/excluded | 是 |
| Bot Skill Installation | 是 |
| 存活 Service Bot 仍可 restart/scale/rollback 的 exact Artifact ref | 是 |
| 无法读取/解析/完整扫描的 Artifact | 否；作为 `UNKNOWN_ARTIFACT` diagnostic warning 返回，不计入 blocker |
| Published Version、Canonical Store、Favorite、Grant、Space Binding | 否 |
| Source Service Bot 已彻底删除，只剩审计 Artifact | 否 |

Service Artifact 血缘不建新索引表、不 backfill。唯一 `ServiceArtifactLineageReader` 通过现有
`BotPublishRepository` 读取 inline 或透明 re-inline 的 offloaded `ext`：文件型解析
`skills_manifest.center_skills[]`，Teclaw 解析 `config_artifact.skills[]` 中
`store=skill-center,path=<skill_uuid>/<version>`。任何 OSS/JSON/分页未知都 fail closed。
仅把存活 Service Bot 的 SUCCESS/UPGRADED/RELEASED/VALIDATING 等仍可重放记录作为 blocker；
Service Bot 仅下线仍可能重新上线，因此仍阻断，必须彻底删除/退役该 Service Bot 才释放血缘。

Offline Application Service 先做只读 impact 预检，再在一个 DB 事务中锁 Skill、重查 blocker、
仅写 `offline_at/offline_by`。POST 已 Offline 时幂等 `changed=false`。存在 blocker 返回
`409 SKILL_OFFLINE_BLOCKED` 与最新 counts。Offline 与新增引用共享 Skill row lock 和
`offline_at IS NULL` 不变量；Offline 期间新引用和原 Skill 的 `draft/upgrade` 返回 `SKILL_OFFLINE`。

### 13. Skill Center 映射与精确版本物化

- Space 持久化 SC Team ID；每次 SC 调用沿 Skill Ownership 显式解析 Team，禁止使用
  全局默认 Team。本期不提供 Team close/disable/delete。
- 新 Space Skill 的 `skill_uuid` 直接作为全局唯一 SC `skillCode`；可变名称只写
  `skillName`，名称允许重复且不参与查找。
- SC Public 已有 `skillCode/userProvidedSkillId` 不要求 UUID。懒物化时按
  `git_path=center://<external_skill_code>` 幂等定位 TeamClaw 资产，并为它生成内部 UUIDv4；
  不能因为外部 code 与已有 Local/Repo/Space 同名就复用后者。
- Version 保存精确 `sc_version_number` 和可用的 SC version ID；不新增重复的
  `sc_skill_code` 字段。
- Skill Center Gateway 只负责 transport、鉴权、配置、请求/响应归一化和错误分类；
  不修改 Skill/Draft/Attempt/Version，也不决定重试和补偿。
- PUBLIC 市场查询保留已发布 OpenAPI 的 `belongTo` 筛选；SC Team 按外部引用查询在
  `success=true,data=null` 时归一化为稳定 `TEAM_NOT_FOUND`，不得与上游拒绝、协议错误
  或不可用混为一类。
- 发布状态查询按 SC 开放接口只传全局唯一 `skillCode`，响应保留 SC 当前 `version`；
  Publication Application Service 使用持久化 Attempt 对返回版本做匹配，Gateway 不要求
  调用方补造 `team_id/version_number`，也不为状态查询执行 Team 列表预检。
- SC 发布状态中的 `standardCheckResult`、`securityCheckReport` 在稳定归一化字段之外
  必须无损保留原始 JSON，供既有 OpenAPI 展示字段继续兼容。
- Version 列表和精确下载的 `PUBLIC/TEAM` scope 及 Team ID 是 Consumer 的信任域预检
  上下文，不是 SC 开放接口的额外 wire 参数；Adapter 预检后仍按全局唯一
  `skillCode`（下载再加精确 `version`）调用 SC。
- SC 当前没有 Team Skill 精确详情接口；若 Adapter 从分页 Team Skill 列表实现
  `get_team_skill`，必须完整翻页或使用等价权威能力后才能判定不存在，禁止只查首页。
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
  layout 切流中立：无论 Bot 当前 Local/Repo 是 Legacy 还是 Pool，Center 都使用这一
  canonical root，不创建 `legacy_center`，但进入 mapping publish/verify/inventory 生命周期。
- Backend 的 `CanonicalCenterVersionStore` 只接受显式 `skill_uuid + sc_version_number`
  exact identity，Interface 只暴露 `write_version/read_version/verify_version`；禁止
  `latest/current`、覆盖和按名称寻址。默认 OSS 物理 key 根为
  `aidesktop/aidesktop_<env>/bolt_shared/skills-center/<skill_uuid>/`
  `<sc_version_number>/`，其下保存经过上游 Materializer 规范化的安全文件树，且必须有
  根级 `SKILL.md`，不得混入 intent、manifest 或 Ready marker。Store 在不会被 Runtime
  mount/rsync 的 sibling 控制根 `skills-center-control/<skill_uuid>/<sc_version_number>/`
  保存 write-once `write-intent.json` 与 `content-manifest.json`；两者只承担并发冲突和完整性
  校验，不是第二个领域状态。Store 逐文件原子 create-if-absent，完整校验后最后发布 integrity
  manifest；manifest 缺失时的部分写入不向 Consumer 暴露，同内容重试按 manifest 幂等补齐
  缺失文件并继续，内容不同则冲突失败。为避免删除并发同内容重试已依赖的 immutable file，
  Store 不物理回滚已完成的 partial；这类对象只属于该尚未完整的 exact identity。
  integrity manifest 缺失、文件缺失、hash/manifest 冲突或对象存储不可用时均不可读、不可向
  Runtime/Artifact 暴露。重复写入同一 exact identity 仅允许同内容幂等验证，任何内容冲突
  fail closed。该 Store 不负责 SC 下载、Scanner、MCP dependency、Version 状态、Runtime
  mapping、Teclaw StoreRef 或 Artifact。
- 真实 bucket 与可选 base override 由服务端环境配置提供；上面的默认 key 合同不包含
  bucket。文件型 Engine Adapter 将它暴露为容器内 `skills-pool/skill-center/` 视图。
- Teclaw 沿用 Artifact v4，新增 `skill-center` OSS Store，SkillRef path 为
  `<skill_uuid>/<sc_version_number>`；Bucket/Base 来自服务端配置。
- `ac_skill_version.status=PUBLISHED` 是唯一领域 Ready/发布成功事实：Materializer 只有在
  文件型 Store `verify_version`、Teclaw Store 和精确 metadata/MCP dependency 全部就绪后
  才能转 PUBLISHED。SC 已发布、单个 Store 完成或仅存在 integrity manifest 都不算 TeamClaw
  发布成功；任一失败保持 MATERIALIZING，只重试同一 Version，不再次发布 SC。
- 禁止 `current/latest`、版本覆盖和数量型 GC；历史 Service Artifact 可能继续引用
  任意旧版本。

`SkillVersionResolver` 是唯一 exact-version Read seam：

```text
BotCapabilityStateReader.active_skill_assets()
→ flush Installation
→ SkillVersionResolver.resolve_latest_runtime_assets(env, assets)
   ├─ Local/Repo：保持 ac_skill 当前内容
   └─ Center：一次批量查询各 skill_id 最高 ordinal 的 PUBLISHED Version
→ Runtime-ready RegisteredSkillAsset tuple
```

Resolver 只读 DB，不下载 SC、不物化、不写 Installation、不推 Runtime；禁止 N+1、字符串版本
排序、MATERIALIZING、SC 现场 latest 或 current symlink。Runtime、Teclaw Composer 与新 Service
Artifact build 都消费同一已解析值对象；Published Service 历史实例不调用 Resolver。

所有 Skill 都有 `mcp_dependencies`；Local/Repo 从当前兼容投影读取，Center 的权威是 exact
`ac_skill_version.metadata_json.mcp_dependencies`（或等价版本级列/子表）。Materializer 只有在
依赖解析、metadata 和所有 Canonical Store 完整后才能 PUBLISHED。所有由 SkillCenter exact
download 进入的版本（Public 懒物化/同步和 TeamClaw Space/Team 发布）都以返回的
`mcpServices` 为版本依赖权威：`null`、缺失或空数组都表示无依赖，非空数组按 `serverCode`
规范化到 `mcp_dependencies[].code`。SkillCenter 的公共市场或 Team 发布流程已完成安全检查，
TeamClaw 的 exact Version Materializer 不再重复调用 Scanner，`risk_tags` 固定为空；但仍继续
执行 SHA256、ZIP 安全、`SKILL.md`、Team name 一致性和 Canonical Store 完整性校验。Local/Repo
的既有 Scanner 链路不在本规则范围内，保持不变。

SC Public 不接 Webhook。周期巡检与手动同步复用一个新的 exact-version
`SkillCenterSyncService.sync()` 深模块：

```http
POST /openapi/v1/bots/market/skill-center/sync
```

只扫描已经懒物化的 `center://` Public assets，不扫描 SC 全量市场，也不巡检 TeamClaw 自己
发布的 Space Skills。同步查询 SC latest，对比 latest PUBLISHED Version，新版本交给同一
Materializer；接口等待发现和物化完成后返回摘要，Track Latest 异步触发。旧 Sync 仅复用调度/
DI 壳子，废弃 name 映射、latest/current、NAS 中转、保留 N 版本、旧完成日志和数量型 GC。
单项失败保留旧 PUBLISHED 并继续其他资产。

### 14. Track Latest 与 Service Artifact

#### 14.1 Track Latest

Space Skill 或已懒物化 SC Public Skill 的新 Version 只有在 `PUBLISHED` 后才触发 Track Latest；
失败不回滚 Version。候选发现使用 Installation 与迁移期 active ordinary Membership，随后每个
Bot 必须通过 Reader flush-then-read 再确认当前是否 active。只由 Default Set 解释的引用不主动
扇出，在重启/重新 ACTIVE/下一次 Skill mutation 时自愈。

```text
PUBLISHED
→ TRACK_LATEST_FANOUT(skill_id)
→ BOT_TRACK_LATEST_RECONCILE(owner_id,bot_id,skill_id)
→ Reader + SkillVersionResolver 重新解析执行时 latest
→ BotRuntimeProjector
```

Task 使用 `ac_task_queue` 有限 Retry，不持久化逐 Bot target/actual version。V2 Task 执行时 V3
已经 PUBLISHED，则直接收敛到 V3。投影前后比较完整 mapping snapshot；发生漂移时 Reschedule，
不把 snapshot 作为新领域状态。

文件型 Runtime 的 Track Latest Scope 包含 Skill 与 MCP dependency delta：当前 Version 新增的
MCP 为 claimed，旧 Version 不再需要且无其他来源的 MCP 为 released，同时全量刷新 allow-list；
无关 MCP 不重推配置。Teclaw 每次本来就 compose whole Artifact，因此一次 Track Latest 只做
一次 whole delivery。

Personal/Desktop 与 Service Draft binding 参与 Track Latest。Published Service online binding
永远不参与；Skill 新 Version 只更新其 Draft，下一次 Service 发布才冻结新版本。

#### 14.2 文件型 Service Artifact

本期采用简单方案 A，不新增 `ServiceCapabilitySnapshot`：Service build pre-stage 先经 Reader
执行一次 `BotRuntimeProjector.project(ProjectionScope.everything())`，随后按 Artifact Producer
能力决定是否读取当前 Runtime layout。ARCA/BaaS 文件型 Producer 在每次 build 执行一次 fresh
`RuntimeLayoutProbeServiceProtocol.probe_bot()`，将 transport evidence 归一化为 typed
`ServiceArtifactLayoutObservation`，并通过不可变 `ArtifactBuildRequest` 传给 Producer；Teclaw
Config Artifact 不做文件系统 Probe。Artifact build 禁止读取
`ac_bot_skill_layout_state.last_probe_evidence` 作为当前物理路径证据，该字段仅属于 Pool
Reconcile/Recovery。Local/Repo 仍接受既有 build 并发窗口；Center exact capability 在 finalize
前重新通过 Reader 读取并与 capture 完全比较，发生漂移时本次 build 失败、由既有发布 Retry 重做。

Artifact 只复制 Bot 实际 active Slice：Local 内容随 Artifact 复制；完整 `skills-repo` 和
`skill-center` corpus 必须排除，只保留实际 active Skill 的精确 symlink。Repo/Center 物理
OSS mount 的唯一 Owner 是 OCB image entrypoint；Avernet 的 Managed BaaS deploy composer
不得把 `shared_corpora` 或默认 Repo 再转换为 `mount_points`。`shared_corpora` 只承担 Snapshot
exclusion、exact-link validation 与历史 Artifact 声明，不是运行时 mount 指令。logical
Claude Code + coding template 必须由统一 layout resolver 落到 physical AICoding 路径。

文件型 build 的顺序固定为：

```text
Runtime Project (best effort)
→ resolve Artifact Producer
→ ARCA/BaaS fresh Probe once
→ ArtifactBuildRequest(bot, version, typed observation)
→ capture exact Center refs + layout generation
→ rsync Snapshot
→ validate corpus exclusion + exact Center links
→ re-read exact Center refs + layout generation
→ finalize skills_manifest
```

Probe 总状态与 Center mount 瞬时状态分离：Pool 或包含 Center 的 Legacy build 需要总体
`READY` 路径证据；无 Center 的 Legacy build 在 `NOT_CAPABLE/TRANSIENT_ERROR/INVALID` 时继续
使用历史静态 BuildPlan。Center 需要 Engine 声明支持 mapping v3，无 Center 的 Pool 至少支持
mapping v2。`center_mount=NOT_READY/UNAVAILABLE` 不单独阻止 Artifact，因为 OCB 会在每次启动
重新 mount；但 Canonical Store exact Version、Snapshot exact links 和 exclusion 仍然 fail closed。

现有 `skills_manifest schema_version=1` 采用 additive optional `center_skills` 与
`shared_corpora`，不升 v2。兼容矩阵为：Legacy 无 Center 不写 shared；Legacy 有 Center 只写
`[center]`；新 Pool Artifact 无论是否有 Center 都写有序 `[repo, center]`。历史无
`skills_manifest`、Legacy 无 shared、以及 Pool 无 Center/无 shared Artifact 保持可读；有
Center 却无 exact Center delivery、Legacy 声明 Repo delivery 或其它组合均 fail closed。

Legacy + Center 示例：

```json
{
  "schema_version": 1,
  "engine": "openclaw",
  "active_layout": "legacy",
  "layout_contract_version": null,
  "center_skills": [
    {
      "runtime_name": "pdf",
      "skill_uuid": "uuid",
      "sc_version_number": "1.0.0",
      "mcp_dependencies": [{"code": "mcp.a"}]
    }
  ],
  "shared_corpora": [
    {
      "corpus": "center",
      "runtime_path": "/engine/workspace/skills-pool/skill-center",
      "store_prefix": "aidesktop/aidesktop_<env>/bolt_shared/skills-center",
      "layout_contract_version": "skills-pool-p3-v1",
      "permission": "read_only",
      "snapshot_policy": "exclude"
    }
  ]
}
```

按 `runtime_name + skill_uuid + sc_version_number` 稳定排序。Manifest 只承担 exact-version 审计、
物理 Artifact 校验、Offline 血缘和问题定位，不在 restart 时重新解析 latest 或重建软链。旧 Artifact
没有 `center_skills` 时按无 Center 的原合同读取。Validator 必须保证：不携带完整共享 Corpus；
Center link 与 manifest 一一对应、使用 canonical absolute target 并指向 exact version；共享 Store
exact Version 可读；不存在未声明/版本漂移/越界 link；Probe 返回的 active/local/repo/center roots
为当前 Engine、同一 contract 下的 canonical 绝对路径。

构建失败沿用现有 `FAILED + source_status=building` 与 Retry 状态机，并在内部 ext 记录稳定错误码：
`SERVICE_ARTIFACT_LAYOUT_EVIDENCE_UNAVAILABLE`、
`SERVICE_ARTIFACT_LAYOUT_EVIDENCE_INVALID`、
`SERVICE_ARTIFACT_CENTER_STORE_NOT_READY`、
`SERVICE_ARTIFACT_SNAPSHOT_INVALID`、
`SERVICE_ARTIFACT_CAPABILITY_CHANGED`。公开 OpenAPI Service Publication facade 继续返回脱敏失败提示；
存量 BFF 保留已有的详细 `error_message` 兼容合同，Probe 原始 evidence 与异常栈仍只进入内部日志。
成功重试必须清理旧的 build error 字段。

#### 14.3 Teclaw v4

Teclaw 继续使用 Artifact v4，不新增 v5。Composition Root 增加通用 Store：

```text
store_id = skill-center
type     = oss
bucket   = bot_oss.bucket_name
base     = aidesktop/aidesktop_<env>/bolt_shared/skills-center
```

Center SkillRef 为 `name=ac_skill.name, scope=shared, store=skill-center,
path=<skill_uuid>/<sc_version_number>`；只有被 Ref 实际使用时才写 Store。V1→V2 只替换 Ref path，
停用移除 Ref，最后一个 Center Ref 移除后可省略 Store。缺 Store、缺 exact Version、权限或下载失败
时整次 apply fail closed。v4 JSON Schema 只做 additive 扩展，旧 Artifact、offload/re-inline 与历史
restart/rollback 必须继续可读。

#### 14.4 历史重放

Service Bot 发布成功时 Artifact 冻结 exact Center Version 和最终 Effective MCP；restart、scale、
rollback 只读对应历史 Artifact，禁止调用 Reader/Resolver、SC latest 或 Track Latest。文件型
Scale 必须复用原发布 Artifact，而不是从当前 Draft Runtime 重打包。不存在历史 Center ref 的
Artifact 不需要迁移。

### 15. Bot Type × Engine 产品入口与技术合同

Center Skill 的技术链路对所有实际存在的 Bot Type × Engine 组合使用同一 exact-version
Resolver/Projector/Artifact 合同。Backend 不得因为产品当前未开放某个创建入口而编码
`SKILL_RUNTIME_NOT_SUPPORTED` 或静态矩阵拒绝；若目标 Bot 已存在，就不能仅凭
`bot_type/engine_type` 拒绝 Membership、activation 或 Artifact build。

产品当前可达组合仍按 PRD 决定 E2E 范围；未开放组合通过共享 contract/unit fixtures 保证无
Center 专属分支。Claude Code 的 logical identity 与 physical runtime layout 分离：
personalCoding/applicationCoding 使用 AICoding physical paths，纯 Claude Code 使用自身 paths，
统一 resolver 必须基于 Bot facts 判定，不能只读 `active_engine`。

文件型 Engine 的 home/path 编码集中在 Engine Adapter；Backend 只传结构化
Local/Repo/Center dependency。Teclaw 只接收 Artifact Store/Path。Mapping v3 未广告、真实
Consumer 未识别 Center Store、OSS/Mount 不可用属于发布门禁；Device 不可用属于真实
Projection 的 `PENDING` / `DEGRADED` 观察结果，不转化为产品“不支持”错误，也不补偿已提交
Desired State。

### 16. Principal、幂等、错误

- Bot Skill/SkillSet：`UserOrDelegatedApp`，始终校验 Bot ACL。
- Space Skill创作、编辑、发布：必须有明确 User 身份并校验 Owner/Manager。
- `tenant/env/user_id` 从认证上下文取得，业务 body 不得任意指定。
- Bot Principal 不能修改 Skill、SkillSet 或 Space。
- Create Space Skill、Create Upgrade Draft、Publication、SC Public Reference 要求
  `Idempotency-Key`；Attempt Retry 以 `attempt_id` 作为稳定恢复身份，不要求新 Key。
- Membership PUT/DELETE 天然幂等。
- Local 同名上传继续原地替换。

| HTTP | 类别 |
| --- | --- |
| 400 | wire/request 格式错误 |
| 403 | ACL、Owner/Manager、MCP 权限失败 |
| 404 | Skill/Space/Version/Attempt/Reference 不存在 |
| 409 | 状态机、Membership、Bot ready、runtime name、Offline blocker 冲突 |
| 422 | SKILL.md、本地文件夹、Git 内容或参数校验失败 |
| 502/503 | Skill Center 或 Runtime 不可用 |

继续使用已发布 Gateway Envelope，并提供稳定 `error_code`。

### 17. 向前兼容门禁

1. 已发布 Local API 不传 `type` 时始终 Local-only；新产品显式 `type=ALL`。
2. 旧 Local raw ZIP、response、status、同名替换、active 和 delete 行为不变。
3. Deprecated `/openapi/v1/bots/skills/**` 不删除。
4. Legacy Skill/SkillSet BFF 只做 Compatibility Adapter，不拥有领域逻辑。
5. 七桃负责的 Space/Member/Favorite wire 不变。
6. 当前尚无真实调用方的 Space Skill 旧列表响应不属于兼容 wire；直接由
   `SpaceSkillSummary` 替换，不保留旧按钮 ViewModel 字段。
7. Local、Repo、Space 不自动转换；Bot-local 长期保持 Bot-local。
8. Legacy Local/Repo 不要求补 UUID 才能读取；公开身份仍是 `ac_skill.id`。
9. 所有 Effective Read 通过 Reader flush-then-read Installation；Legacy Set/Default/exclusion
   语义仍能惰性物化，不能保留第二套 in-memory merge。
10. Resolver 覆盖 Local/Repo/Center、OpenClaw、Claude Code(AICoding image)、Hermes、Teclaw；
    不因产品未开放入口而静态拒绝已经存在的 Bot/Engine 组合。
11. 文件型 `skills_manifest schema_version=1` additive optional `center_skills`，Teclaw 继续 v4
    additive `skill-center` Store；旧 Artifact 不升版、不切流并继续可读。
12. SC Public 外部 code 与 Local/Repo/Space 同名不复用资产；`center://` locator 与内部 UUID
    映射必须稳定。

### 18. 交付、切流与回滚

Phase 1 是 Phase 2 的控制面基础，但允许 Phase 2 的纯内部模块在接口稳定后并行开发。
功能启用必须遵循以下 Gate：

1. **Phase 1 Schema/Consumer first**：部署 Additive DDL、Installation Repository、
   按 Bot 懒物化、Runtime Resolver、兼容 Adapter 和 OpenAPI 兼容测试，不改变产品流量。
2. **Phase 1 Enable**：切换 Local active 的内部事实源，开放 Repo/SkillSet/MCP
   canonical OpenAPI，产品显式使用统一 Gateway；通过 Phase 1 全量门禁。
3. **Phase 2 Consumer first**：先部署并以 CI/Singlebox/预发真实 Consumer 验证 Canonical
   Materializer、Mapping v3、pool_center、文件型 exact Center mapping、Teclaw v4
   `skill-center` Store 和 Service Artifact 精确版本读取/重放能力。
4. **Phase 2 Producer**：再部署 Backend Publication、SC Public Reference、Track Latest、
   Artifact Producer；协议不建设长期 feature flag、双版本或 Bot Type 静态支持矩阵。
5. **Phase 2 Enable**：最后发布新增 Gateway OpenAPI 和产品入口。软件协议兼容由发布门禁证明；
   资产/DDL/权限写前校验 fail closed，已提交能力的 Device Projection 返回 `PENDING` /
   `DEGRADED` 而不补偿。

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
- Runtime `PENDING` / `DEGRADED` 不改变 Installation、进程崩溃窗口、下一次 mutation 和
  Bot restart 的全量自愈。
- Reader 对普通/Default Skill/MCP membership 与 exclusion 做一次幂等 flush，随后全部消费者只读
  Installation；Engine/Template Policy MCP 仍由统一 Effective MCP Collector 合并。
- Legacy Local/Repo/Bot-local 不转换，System Default Skill/MCP/CLI 仍然生效。

Phase 1 Backend 完成定义：Gateway OpenAPI gate 通过，存量 BFF 与 canonical
OpenAPI 的等价测试通过，全部产品能力均存在可供前端切流的 Gateway Backend 合同，
Legacy 全矩阵无回归。产品前端是否已经完成调用切换和页面 E2E 属于独立的产品切流
验收，不阻塞 Backend Phase 1 代码完成，但在产品正式切流前仍必须由前端团队验证。

Phase 1 对新产品 PRD 的可测边界：Local 上传、Repo Catalog、MCP Catalog/权限、
添加到 SkillSet、SkillSet 整体激活/停用、System Default 和 Runtime 恢复可做完整
Backend 验收。真实 Space Skill 的本地文件夹/Git 创建、Draft 编辑、Owner/Manager/Edit
Lease、发布、版本升级、Skill Center 物化、Track Latest、Service Artifact 精确版本
和可恢复下线属于 Phase 2；Phase 1 只能通过兼容 Fixture 验证已有 Space wire 和消费边界，
不能把它计为新产品主流程 E2E。

Phase 2 必测：

- folder/Git Identity/Ownership/Owner/V1 Draft 原子创建；Git 多 SKILL.md 确定性第一项与
  source_subdir 固化；请求重放不重复 Identity。
- Team Edit Lease acquire/release/takeover/fencing；Personal 不需要 Lease；无 TTL。
- immutable Draft Revision、单文件保存 CAS、Git 刷新失败不改变 Draft；FROZEN 后全部写入口拒绝。
- SC Publication 单次 POST、明确失败、RESULT_UNKNOWN、迟到结果，以及同 Attempt 的
  PREPARATION/SC_STATUS_CHECK/MATERIALIZATION 恢复。
- publication-impact 只提示；发布成功后按最新候选重算，不信任预览。
- MATERIALIZING 对消费者不可见；文件型/Teclaw 物化全部成功后才 PUBLISHED。
- SC Public 一次 20 code 的持久 Reference：Idempotency replay、跨刷新轮询、部分成功、终态
  永久保留、最终最新权限/Set 状态复验、单次批量 Membership/Runtime Projection。
- SC Public 周期/手动 Sync 只巡检已物化 center:// 资产，单项失败保留旧 PUBLISHED。
- Track Latest 不维护逐 Bot actual；Direct/ordinary 候选、Default-only 延迟自愈、Version/MCP
  delta、Task 合并和 Device ACTIVE bootstrap retry 全部覆盖。
- Service Release 固化 V1 后，V2 发布不影响 V1 的扩容、重启和回滚。
- 文件型 manifest v1 additive center_skills、共享 Center mount、排除完整 Corpus、exact symlink
  校验；Teclaw v4 additive Store 与真实 Consumer/offload/re-inline。
- Offline 覆盖 Draft/Attempt、inactive/default Membership、Installation、inline/offloaded
  replayable Artifact；无 force、无 SC 下线；成功保留原 Vn 为 OFFLINE，精确 Version Copy 创建独立 V1 Draft。
- Local/Repo/Center 并存、Mapping v2/v3、所有实际 Bot/Engine 走共享合同而无静态拒绝分支。

Phase 2 完成定义：产品主流程 E2E、SC pre 联调、多引擎矩阵、Service Artifact
回滚和 Phase 1 全量回归全部通过。

## Out of Scope

- Space 删除。
- Local/Repo/Space 原地转换。
- 任意 Git URL 导入 Legacy Repo Catalog。
- 单个 Published Version 下线、删除或覆盖。
- Skill 复制/Clone；最终 PRD 已移除该功能，本期不定义对应接口。
- Publication Attempt cancel。
- Runtime effective/reconcile 运维接口。
- 无限期 Runtime 对账、逐 Bot observed actual-version 查询或 `ServiceCapabilitySnapshot`。
- Transactional TaskQueue Pending Writer；本期接受业务事务后 enqueue 的有限窗口。
- SC Webhook、全量 SC Public 扫描或 SC 全局下线。
- Reference Operation cancel/delete/原地 retry 与终态 TTL 清理。
- Offline force/admin bypass、永久 Retirement 和新的 Artifact lineage 索引表/backfill。
- 长期 feature flag、Center 双协议版本和按 Bot Type × Engine 的静态支持表。

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

- 旧 Phase 2 Ticket 正文基于早期 Draft/Artifact/Reference 语义，不能继续作为实现权威。
- 本次先完成正式 Spec 与产品 PRD 对照；Frontend Guide/OpenAPI 清单对齐后再统一重新拆 Ticket。
- 旧 Ticket 必须逐条归类为“已完成关闭”“按最终 Spec 修订复用”或“被新 Ticket 取代关闭”，
  禁止保留两套同时可领取的 ready-for-agent 工作。
- 新 Ticket 按可独立验收的纵向闭环拆分，每个实现 PR 合入 `dev`；Consumer-first 门禁、
  Backend Producer 和 Gateway/Product Enable 必须有明确 blocking edges。

### 前端接口文档

前端最终以 Gateway 生成的 OpenAPI JSON/Swagger 为机器可读权威。实现前附带的
Frontend API Review 文档只用于提前对齐路径、产品流程、字段扩展和状态映射；不得
代替生成 artifact。实现 PR 必须同步更新生成 OpenAPI，前端不从本文猜测未声明字段。
