# Phase 2 Skill 前端开发与联调手册

> 适用对象：TeamClaw Web 前端、产品、联调测试。
>
> 本文按产品 User Story 说明接口组合、字段渲染、轮询、错误和恢复。精确单接口 wire 请查
> `phase2-openapi-contract.md`；实现后字段类型以 Gateway Swagger/OpenAPI 为机器可读权威。

## 1. 使用边界

### 1.1 当前阶段

当前 `dev` 已包含 Space Skill 创建/读取、Draft、Grant、Lease、Publication、Version、Offline 和
Artifact 主链。SC Public Reference、周期同步和 Track Latest 由 PR #1707 交付；只有部署版本的
Gateway `/openapi.json` 已出现 `skill-center-references` 路由后，前端才可从 Mock 切换真实联调。

Group 6 是 Runtime/Artifact/Gateway 的跨链路集成验收，不再新增一套前端业务 API。本文中的
单接口字段、required、状态码始终以目标部署环境的 Gateway OpenAPI 为准，不能仅凭 PR 已合入
判断环境已经可调用。

不得通过以下方式绕过：

- 直连 Backend 私有 `/api/*`；
- 调用未注册的候选路径并假设返回 501；
- 从页面按钮反推权限，忽略服务端 403/409；
- 使用 SC `skillCode` 调普通 Membership；
- 把 Draft、Published Version、Publication Attempt 压成一个 status。

### 1.2 Base URL 与公共格式

所有本文路径均以 `/openapi/v1/bots` 开始。响应统一：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {},
  "request_id": "trace-id"
}
```

前端必须同时记录 HTTP status、稳定六位 `code` 和 `request_id`；反馈问题时携带 request_id。
合同文档里的大写错误符号是 `code` 的可读名称，不是 Envelope 顶层新增字段。只有 Publication
Attempt/Reference 这种持久异步资源会在 `data` 内返回自己的 `error_code`。

### 1.3 身份、缓存与重复提交

- 无需 `user_id` query 的 SC/Repo 市场读取仍要求已认证的 `UserOrDelegatedApp` Principal；其余
  本文 Space、Repo 和 Bot 接口按生成 OpenAPI 要求传 `?user_id={actor_id}`。后续示例为突出业务路径
  会省略该公共 query。
- `user_id` 表示当前调用者，不是 Bot owner；Gateway/Backend 会校验认证上下文。
- Bot-scoped 接口还接受可选 `owner_id`。操作自己的 Bot 时省略即可；操作协作 Bot 时必须传
  `owner_id={bot_owner_id}`，不能用当前调用者代替 Owner。建议在统一 API client 层注入这两个参数。
- Space Skill 写操作只允许明确用户身份；不要使用 App-only Principal。
- 需要 `Idempotency-Key` 的请求，前端应为一次用户意图生成 UUID，并在网络重试、页面恢复时
  复用同一个 Key；用户重新发起一次新动作时生成新 Key。
- GET 可以按页面生命周期缓存；执行任意 mutation 后按本文指出的刷新范围重新读取。

当前 Space Skill DTO 的 `skill_id`、Publication DTO 的 `attempt_id` 是十进制字符串，而部分 Path
参数在生成客户端中仍是 integer。前端状态中保留原始字符串；调用 Path 参数前通过统一
`parseSafeDecimalId()` 校验并转换，禁止在页面组件里散落 `Number(id)`。这是 Backend 合同统一前的
兼容规则。

## 2. 前端状态模型

### 2.1 三类事实必须分开

| 事实 | 字段 | 用途 |
| --- | --- | --- |
| Skill 生命周期 | `lifecycle_status` | 卡片是仅草稿、在线还是本地下线 |
| 当前 Draft | `draft.status` | 是否可编辑、目标版本、当前 Revision |
| 当前发布任务 | `active_publication.status` | 发布中、物化中或结果确认中 |

示例：Published V1 正在编辑 V2：

```json
{
  "lifecycle_status": "PUBLISHED",
  "latest_published_version": {"version": 1},
  "draft": {"target_version": 2, "status": "EDITING"},
  "active_publication": null
}
```

不能因为存在 Draft V2 就把 Published V1 从市场或 Bot Runtime 移除。

### 2.2 页面文案映射

| 领域事实 | 建议展示 |
| --- | --- |
| `DRAFT_ONLY + EDITING` | 草稿 |
| `PUBLISHED + draft=null` | 已发布 |
| `PUBLISHED + EDITING Draft` | 已发布 · 有待发布修改 |
| Attempt `PREPARING/SC_SUBMITTING/WAITING_SC` | 发布中 |
| Attempt `MATERIALIZING` | 物化中 |
| Attempt `RESULT_UNKNOWN` | 发布结果确认中 |
| `OFFLINE` | 已下线 · 保留历史版本，可复制为新 Skill |

`SUCCEEDED/FAILED` 是历史 Attempt 终态，详情页可展示；列表 `active_publication` 只内联非终态。

### 2.3 Permission 不是最终按钮状态

`actor.permissions` 只表示当前用户基于 ACL/Grant 有资格发起命令。例如：

```text
permissions.publish_draft=true
+ Draft.status=EDITING
+ 没有进行中 Attempt
+ Team Lease 不是 HELD_BY_OTHER
→ 发布按钮可用
```

前端可以据此预判按钮，但点击后仍必须处理服务端对最新状态的 403/409。

## 3. 页面—接口总览

| 产品页面/动作 | Operation |
| --- | --- |
| 能力工坊卡片列表 | P2-SKL-001 |
| 本地文件夹创建 | P2-SKL-002 |
| Git 导入创建 | P2-SKL-003 |
| Skill 创作详情 | P2-SKL-004 |
| 创建升级 Draft | P2-DRF-001 |
| 文件树、打开、保存、Git 更新 | P2-DRF-002..005 |
| 删除草稿/未发布 Skill | P2-DRF-006 |
| 历史版本 | P2-VER-001..004 |
| 权限管理 | P2-GRT-001..005 |
| 编辑锁 | P2-LSE-001..004 |
| 发布影响、发布和轮询 | P2-PUB-001..005 |
| 下线影响和下线 | P2-OFF-001..002 |
| 市场搜索 | P2-MKT-001..003 |
| 引用 SC Public Skill | P2-REF-001..003 |

## 4. 能力工坊列表与详情

### 4.1 打开能力工坊

```http
GET /openapi/v1/bots/spaces/{space_id}/skills?keyword=&page=1&page_size=20
```

产品行为：

1. 首次进入请求第 1 页；搜索输入防抖后重新从第 1 页请求；
2. Backend 对 name/description 做不区分大小写过滤后分页；
3. Offline Skill 仍在工坊列表中，供 Owner/Manager 继续修改；
4. 不在列表额外请求全部 Grants、Versions、文件树或影响面。

卡片字段：

| 字段 | UI |
| --- | --- |
| `name/description` | 标题与摘要，来自 SKILL.md |
| `lifecycle_status` | 主状态标签 |
| `latest_published_version.version` | `V1/V2` 标签 |
| `draft.target_version/status` | 待发布版本与草稿状态 |
| `active_publication` | 发布进度 |
| `owner` | Owner 展示 |
| `actor.skill_role` | 当前用户角色 |
| `lease_summary` | 锁图标和 holder；不保存 token |

列表返回 `actor.pending_editor_request` 时，普通成员展示“申请中”，不能再次申请。该字段不是
`SpaceSkillSummary` 的顶层字段。

### 4.2 打开 Skill 详情

```http
GET /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}
```

详情响应是页面初始化事实，包含当前 Draft、latest Published、Attempt、Actor 与 Lease 摘要。
进入编辑器前仍要调用实时 Lease API；不要使用列表中的 lease_summary 直接保存。

### 4.3 空态与失败

| 情况 | 处理 |
| --- | --- |
| 200 total=0 | 展示空态或无搜索结果 |
| 403 | 用户不再是 Space 成员，返回 Space 列表 |
| 404 | Skill 被删除或不可见，刷新工坊列表 |
| 409 | 详情已变化，重新 GET detail |

## 5. 创建 Space Skill

本节发生在“能力工坊 → 新建 Skill”，创建的是 Space-owned 创作资产。它不同于能力集弹窗里的
“添加本地文件夹”：后者创建 Bot-owned Local Skill，见 11.1。

### 5.1 本地文件夹

产品入口：“新建 Skill → 添加本地文件夹”。

```http
POST /openapi/v1/bots/spaces/{space_id}/skills
Content-Type: multipart/form-data
Idempotency-Key: <uuid>
```

Form：

```text
files=<file1>
files=<file2>
file_paths=["SKILL.md","references/example.md"]
```

- 浏览器直接提交 File 集合和 webkitRelativePath；不生成 ZIP。
- `file_paths` 与 files 数量、顺序必须一致，并保留目录结构。
- 单次网络重试复用同一 Idempotency-Key。
- 201 后直接进入 V1 Draft 详情；这时还没有发布、Membership 或 Runtime 生效。

校验失败：

| 合同符号名（映射到 Envelope code） | UI |
| --- | --- |
| `SKILL_PACKAGE_INVALID` | 展示包级错误 |
| `SKILL_MANIFEST_MISSING` | 提示缺少 SKILL.md |
| `SKILL_MANIFEST_MULTIPLE` | 提示只能存在一个目标 SKILL.md |
| `SKILL_PATH_INVALID` | 展示非法相对路径 |

Skill Identity 使用 UUID；不同 Space 或同一市场已有同名 Skill 不构成复用或冲突依据。

### 5.2 Git 导入

```http
POST /openapi/v1/bots/spaces/{space_id}/skills/import-from-git
Idempotency-Key: <uuid>

{
  "git_url": "https://example.com/team/skills.git",
  "branch": "main",
  "subdir": null
}
```

仓库中有多个 Skill：

- 用户指定 subdir：只解析该目录；
- 未指定：根 SKILL.md 优先，否则选择规范化父目录字节序第一项；
- 选择必须确定性，不能随机，也不能第一个非法后继续碰运气；
- 201 响应在 `data.draft.source_subdir/source_commit_sha` 返回最终选择结果。

Git 导入是 Snapshot，不会加入 aiworkbench Repo Catalog，也不会自动跟随 Git 更新。

## 6. Draft 编辑与 Lease

### 6.1 Personal Space

Personal Draft 不需要 Lease：

```json
{"required": false, "state": "NOT_REQUIRED", "fencing_token": null}
```

仍必须使用 `expected_revision_id` 做并发 CAS。

### 6.2 Team Space 打开编辑器

```text
GET detail
→ GET draft/lease
→ FREE：PUT draft/lease 获取锁
→ HELD_BY_ME：直接使用返回 token
→ HELD_BY_OTHER：只读展示，Owner/Manager 可选择 takeover
→ GET draft/files
```

前端只在编辑页面内存保存 `fencing_token`，不得写入列表缓存或长期 localStorage。

### 6.3 Takeover

```http
POST .../draft/lease/takeover
```

只有 Owner/Manager 且 `permissions.takeover_lease=true` 展示。成功生成新 token；旧页面保存收到
`LEASE_FENCING_TOKEN_STALE`，必须退出编辑并重新读取。

### 6.4 文件树和保存

```http
GET .../draft/files
GET .../draft/files/{path}
PUT .../draft/files/{path}
```

`{path}` 是 URL 编码后的完整 POSIX 相对路径，不允许 `..`。保存示例：

```json
{
  "content": "# Updated content",
  "expected_revision_id": "rev-2",
  "fencing_token": 7
}
```

成功后用新 revision_id 替换本地 revision。多个 Tab 编辑时，旧 revision 返回
`DRAFT_REVISION_CONFLICT`；提供“刷新最新内容”，不要自动覆盖。

SKILL.md 的 `name` 创建后不可修改；description 可在 Draft 中修改，发布成功后才更新展示。

### 6.5 Git 手动刷新

只对 Git Draft 展示：

```http
POST .../draft/refresh-from-git

{"expected_revision_id":"rev-2","fencing_token":7}
```

刷新失败时 Draft 完全不变；成功后重新读取文件树。

### 6.6 关闭编辑器

Team holder 应调用：

```http
DELETE .../draft/lease?fencing_token=7
```

网络失败不会让锁自动过期；本期 Lease 无 TTL。遗留锁由 Owner/Manager takeover。

## 7. 升级、删除 Draft 与历史版本

### 7.1 点击“升级”

```http
POST .../draft/upgrade
Idempotency-Key: <uuid>
```

升级只创建 Vn+1 EDITING Draft，不查询影响面、不发布、不推 Bot。内容从 TeamClaw Canonical
exact Version 复制；必要时服务端从 SC exact version 修复。

### 7.2 删除按钮

```http
DELETE .../draft?expected_revision_id=rev-2&fencing_token=7
```

- `deleted_scope=DRAFT`：仅放弃本次升级；Published Vn 仍在线；
- `deleted_scope=SKILL`：首次从未发布，且除终态 FAILED Attempt 外无外部事实，整个 Skill
  及其失败 Attempt 被删除。

FROZEN 时不显示删除按钮。Offline Skill 不再允许升级；产品显示“复制”，以最后 Published Vn
创建独立新 Skill 的 V1 Draft。
删除 Team Draft 会同时使当前 Lease/fencing token 失效，前端必须停止 Lease 轮询并退出编辑态。

### 7.3 查看历史版本

```http
GET .../versions?page=1&page_size=20
GET .../versions/{version}
GET .../versions/{version}/files
GET .../versions/{version}/files/{path}
```

不要提供编辑、删除、覆盖或单版本下线按钮。历史 Runtime/Artifact 可能仍引用精确版本。

## 8. Owner、Manager 与编辑权申请

### 8.1 权限管理页

```http
GET .../grants
PUT .../managers/{manager_user_id}
DELETE .../managers/{manager_user_id}
POST .../owner-transfer
```

- 恰好一个 Owner；Manager 必须是当前 Active Space Member；
- 只有 Owner 直接增删 Manager；
- Space Admin 转移 Owner 时 `reason` 必填；
- 移除 Lease holder 或转移 Owner 会使旧 fencing token 失效；
- 原 Owner 是否保留 Manager 由 `retain_previous_owner_as_manager` 决定，默认 false。

### 8.2 普通成员申请编辑

```http
POST .../editor-requests

{"reason":"需要共同维护该 Skill"}
```

成功进入既有 Work Order。前端不得指定 reviewer；Backend 使用当前唯一 Skill Owner。Owner 转移后
旧 Owner 不能继续审批。列表 `pending_editor_request` 用于显示“申请中”。

## 9. 发布完整流程

### 9.1 发布前影响提示

用户点击“发布”时先调用：

```http
GET .../publication-impact?page=1&page_size=20
```

影响列表只是提示：有影响也允许发布；用户点击“已知悉并发布”后才 POST；不传 acknowledgement
token；Backend 成功后重新计算实际 Track Latest 候选。

### 9.2 创建 Publication

```http
POST .../publications
Idempotency-Key: <uuid>
```

不传 fencing token。Team 服务端检查 Lease：HELD_BY_OTHER 拒绝；FREE/HELD_BY_ME 冻结服务端
最新 Revision。成功返回 202 Attempt，页面立即轮询。

如果返回 503 或请求超时，前端必须复用同一个 Idempotency-Key 重放。Backend 返回原 Attempt 并
重新确保后台任务；生成新 Key 会被解释为新的发布意图，不能用于网络层重试。

### 9.3 轮询策略

```http
GET .../publications/{attempt_id}
```

建议：前 30 秒每 2 秒，之后每 5 秒；页面隐藏时 15 秒；SUCCEEDED/FAILED 终止；
RESULT_UNKNOWN 低频查询但不重新 POST。页面刷新后从 detail 或 publications collection 恢复。

### 9.4 状态与展示

| status | 页面 | 继续轮询 |
| --- | --- | --- |
| PREPARING / SC_SUBMITTING / WAITING_SC | 发布中 | 是 |
| MATERIALIZING | 物化中 | 是 |
| SUCCEEDED | 发布成功 | 否，刷新 detail/list |
| FAILED | 发布失败，Draft 恢复可编辑 | 否 |
| RESULT_UNKNOWN | 发布结果确认中 | 低频；禁止重新发布 |

只有 SUCCEEDED 才表示 SC exact Version、Canonical Store、metadata/MCP dependency 全部 Ready。
Track Latest 此后异步触发，不阻塞“发布成功”。

### 9.5 重试按钮

只在 `attempt.recovery.state=AVAILABLE` 展示：

```http
POST .../publications/{attempt_id}/retry
```

前端不判断重试 publish、SC status 还是 materialization；Backend 根据 recovery.kind 恢复同一
Attempt。普通 FAILED 需要修改 Draft 后新建 Attempt。RESULT_UNKNOWN 且 NOT_AVAILABLE 不显示按钮。

- HTTP 202：恢复任务已重新入队，继续轮询同一 Attempt；
- HTTP 200：Attempt 已经成功，无需创建任务，直接刷新 detail/list。

## 10. 下线与重新发布

先调用：

```http
GET .../offline-impact?page=1&page_size=20
```

与 publication-impact 不同，下线对已明确证明的引用是硬门禁：存在 Membership、Installation、
Draft/Attempt 或可重放 Service Artifact 时不能继续。无法读取、解析或完整扫描 Artifact
只作为诊断 warning 返回，不阻塞下线；因此历史 offloaded Artifact 的偶发不可读不会让所有
Skill 永久无法下线。

`data.items[].kind` 的稳定枚举及前端建议文案：

| kind | 含义 |
| --- | --- |
| `DRAFT` | 已存在可编辑或冻结 Draft |
| `PUBLICATION` | Publication 仍进行中或结果未知 |
| `MEMBERSHIP` | 普通或 Default SkillSet 仍引用该 Skill |
| `INSTALLATION` | Bot Effective Installation 仍存在 |
| `SERVICE_ARTIFACT` | 可重放的 Service Artifact 仍冻结该精确 Version |
| `UNKNOWN_ARTIFACT` | Artifact 血缘无法完整证明；只在 `data.warnings` 返回，不阻断 |

`blocked=false` 才启用：

```http
POST .../offline
```

Backend 会重新检查，所以仍可能返回 `SKILL_OFFLINE_BLOCKED`。成功后：

- 历史 Published Vn 保持不可变；
- TeamClaw 市场和 consumable 隐藏；
- 不调用 SC 外部下线；
- 原 Skill 不可继续编辑、升级或发布；
- 产品可调用 Version Copy 创建独立新 Skill 的 V1 Draft。

`data.warnings` 是不计入 `total`、`counts` 或 `items` 的诊断信息；产品可以提示“部分历史
Artifact 无法读取，未发现明确引用，已继续下线”，但不得把 warning 当成 blocker。

409 `code=409313` 的 `data` 会带回最新 OfflineImpact/counts，前端直接用它刷新阻断弹窗；这是
普通错误 `data=null` 的 route-specific 例外。

产品文案不能写成“Vn 变回草稿”；准确语义是“Skill Offline，保留历史 Vn；复制后产生独立新 Skill”。

## 11. 添加 Skill 弹窗的四个产品来源

前端分别查询，不建设 Backend 混合分页；不同来源的确认命令也不同。

### 11.1 添加本地文件夹（Bot-owned Local）

```http
POST /openapi/v1/bots/{bot_id}/skills/upload-folder?user_id={actor_id}&owner_id={bot_owner_id}
Content-Type: multipart/form-data

files=<file1>
files=<file2>
file_paths=["SKILL.md","references/example.md"]
```

上传成功可能是“新建”或“同名替换”。新建项默认 inactive；替换必须保留原 active、Membership
和 skill_id。上传后刷新 Bot Skill detail/list，再按最终控制来源处理：

```http
GET /openapi/v1/bots/{bot_id}/skills?source=LOCAL&page=1&page_size=20&user_id={actor_id}&owner_id={bot_owner_id}
```

`source=LOCAL` 只返回该 Bot 上传的 `local://` Skill（包含 active 与 inactive）。省略 `source`
仍保持“Bot 全部可达 Skill”的现有列表语义，不能在前端用名称或 active 状态猜 Local 来源。

| 上传后状态 | 后续动作 |
| --- | --- |
| inactive 且无 Membership | 调用下面的 Membership PUT |
| 已属于目标 SkillSet | 不重复改变控制来源；刷新目标 Set 即可 |
| 已属于其他普通 SkillSet | 先由用户从原 Set 移除，再添加到目标 Set |
| Direct active | 先调用 `POST /openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate`，再添加到 SkillSet |

需要加入目标 Set 时调用：

```http
PUT /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}?user_id={actor_id}&owner_id={bot_owner_id}
```

`owner_id` 仅在协作 Bot 场景必传；当前调用者就是 Owner 时可省略。

目标 Set 默认 active 时，添加成功后立即写 Installation 并投影 Runtime。这里不能误调 Space Skill
创建接口；Local 资产属于 Bot，Space Skill 属于 Space。

### 11.2 TeamClaw 市场

```http
GET /openapi/v1/bots/skills/repository?keyword=&page=1&page_size=20&user_id={actor_id}
PUT /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}?user_id={actor_id}&owner_id={bot_owner_id}
```

返回 Repo Skill，已有 TeamClaw `skill_id`，直接调用普通 Membership。
Phase 2 弹窗统一使用该 canonical Repo Catalog；不要切换到仍存在的通用
`POST /openapi/v1/bots/market/skills`，避免两套分页和字段合同并存。

### 11.3 能力工坊

```http
GET /openapi/v1/bots/spaces/{space_id}/skills/consumable?keyword=&page=1&page_size=20&user_id={actor_id}
PUT /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}?user_id={actor_id}&owner_id={bot_owner_id}
```

只展示 Published、物化完成、非 Offline 的 Space Skill；同样已有 `skill_id`。

### 11.4 SkillCenter Public 市场

```http
POST /openapi/v1/bots/market/skill-center/skills

{
  "keyword": "search",
  "pageNum": 1,
  "pageSize": 20,
  "sortBy": "latest",
  "tagList": [],
  "creatorName": null,
  "creatorWorkNo": null,
  "belongTo": null,
  "isOfficial": null,
  "isRecommended": null
}
```

卡片只能消费生成 OpenAPI 的 `SkillCenterMarketItem` 已声明字段：
`skillCode/skillName/description/tagList`（以及可选的归属和创建者字段）。版本标签、图标和
iframe 详情入口在对应字段被显式加入 DTO、重新生成并发布 Gateway OpenAPI 前均为**未定义**，
前端不得从 `additionalProperties` 或 Router 透传字段推断其语义。确认引用时只有外部 `skillCode`，
不能调用普通 Membership，必须发起异步 Reference。

选择项和去重键必须使用 `skillCode`，不能使用名称。若异常上游记录没有 skillCode，该卡片只可
查看、不可勾选；不能由前端生成临时代码。

## 12. SC Public 批量异步引用

### 12.1 提交

用户可勾选多个 Skill，前端去重后最多 20 个：

```http
POST /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references?user_id={actor_id}&owner_id={bot_owner_id}
Idempotency-Key: <uuid>

{"skill_codes":["public-a","public-b"]}
```

202 只表示 Operation 已持久化，不表示已经加入 SkillSet。
响应中 Envelope 顶层 `request_id` 是 trace；`data.request_id` 才是后续 collection 过滤使用的
Reference 批次身份，前端不要混用。

```json
{"request_id":"req-01","reference_ids":["ref-a","ref-b"]}
```

202 不直接返回卡片详情；前端随后按 `data.request_id` 查询 collection。

如果 POST 超时或返回 503，复用同一 `Idempotency-Key` 重放；Batch 可能已经持久化，不能生成新 Key。
协作 Bot 未持有编辑锁时可能返回 HTTP 423，前端应进入既有 Bot edit-lock 获取流程后再用同一 Key
重放。前端必须先按 `skillCode` 去重，并限制为 1–20 项；Backend 会再次校验。

### 12.2 卡片状态

| Backend status | 前端阶段 |
| --- | --- |
| QUEUED / RESOLVING_VERSION | 准备中 |
| MATERIALIZING | 正在同步 Skill |
| ADDING_TO_SKILL_SET | 正在添加到能力集 |
| PROJECTING_RUNTIME | 正在使能力生效 |
| COMPLETED | 添加成功 |
| FAILED | 添加失败 |

COMPLETED 前前端不得把卡片视为“已添加”或“已激活”。内部在 `PROJECTING_RUNTIME` 阶段可能已进入
事务性 Membership/Installation/Runtime 处理，但只有 COMPLETED 才是前端可提交的成功事实。

### 12.3 轮询与恢复

```http
GET .../skill-center-references?request_id=req-01&page=1&page_size=20&user_id={actor_id}&owner_id={bot_owner_id}
GET .../skill-center-references/{reference_id}?user_id={actor_id}&owner_id={bot_owner_id}
```

建议每 2 秒轮询，全部终态后停止。关闭弹窗不会取消任务；重新打开、页面刷新或目标 Set 删除后，
仍可按 Bot ACL 查询历史结果。

### 12.4 部分成功

3 项中 2 成功、1 失败时：成功项保留，失败项展示 error；不回滚成功项；本期没有原地 retry，
用户重新选择失败项并使用新 Key 提交。物化已完成但 Membership 失败时，共享资产继续保留。

“没有原地 retry”仅指没有用户可调用的 Reference retry API。Backend 对可恢复的上游/物化失败会
自动做有限重试，前端只轮询持久状态。FAILED 常见稳定 `error_code`：

| error_code | 前端处理 |
| --- | --- |
| `SC_SKILL_NOT_FOUND` | 提示市场项不可用，刷新搜索结果 |
| `SC_MARKET_UNAVAILABLE` | 提示 SC 暂时不可用，可用新请求重试失败项 |
| `MATERIALIZATION_FAILED` | 提示 Skill 同步失败 |
| `SKILL_OFFLINE` | 提示该 TeamClaw 资产已下线 |
| `SKILL_SET_NOT_FOUND` | 目标能力集已删除，刷新能力集 |
| `SKILL_SET_FORBIDDEN` | 当前用户已无目标能力集权限 |
| `RUNTIME_PROJECTION_FAILED` | 添加/运行时投影失败，刷新最终能力集状态 |
| `SKILL_SET_UPDATE_FAILED` | 通用能力集更新失败 |

## 13. SkillSet 与生效语义

新建普通 SkillSet 默认 `active=true`。空 Set 不触发 Runtime，之后添加成员立即生效。

### 13.1 Desired State 与 Runtime Projection

所有新的 Bot Skill / SkillSet / MCP 激活、停用和成员变更 OpenAPI response 都同时返回：

```json
{
  "desired_state": {"changed": true, "status": "COMMITTED"},
  "runtime_projection": {
    "status": "CONVERGED | PENDING | DEGRADED | SKIPPED",
    "components": {"skills": "CONVERGED", "mcp": "CONVERGED"},
    "pending_count": 0,
    "degraded_count": 0,
    "issues": []
  }
}
```

- `desired_state.status=COMMITTED` 表示 Installation 已写入，是“Bot 应当生效哪些能力”的权威事实；
  `UNCHANGED` 表示幂等请求没有改变该事实。
- `CONVERGED`：当前 Runtime 已完成投影；`SKIPPED`：本次命令无需投影或兼容服务未提供观察结果。
- `PENDING`：Runtime 暂不可达或受源文件缺失影响，前端保留已选中的能力并提示“状态已保存，稍后自动同步”。
- `DEGRADED`：Bot 生效目录有同名实体目录或外部软链，平台不会覆盖/删除它；前端保留已选中的能力，并按
  `issues` 展示受影响 Skill 名称、原因及 `suggested_action`。
- 不要把 `PENDING` / `DEGRADED` 当作 mutation 失败，也不要把开关回滚。后续 Bot 生命周期事件或下一次能力变更会再次尝试投影。

存量 BFF `/api/skills/skillset/activate|deactivate` 保持原成功 wire；只有 `PENDING` / `DEGRADED`
时额外返回 `data.runtime_projection` 并使用对应中文提示文案。因此旧页面不读取该字段时仍按成功处理。

| 操作 | inactive Set | active Set |
| --- | --- | --- |
| 添加已物化 Skill | 只写 Membership | Membership + Installation + Runtime |
| 删除 Skill | 删除 Membership | 删除 Membership + Installation + Runtime |
| SC Public Reference 完成 | 只写 Membership | Membership + Installation + Runtime |

SkillSet 只有全部 active 或全部 inactive，不显示半选。Installation 是 Bot Effective Skill 的
读取事实，前端不自行计算来源 Union。

Skill 的 MCP dependency 权限沿用现有 SkillSet 添加流程，不因为来源是 Space/SC Public 增加一套
前置规则。最终添加时权限不足就按既有 MCP 权限申请交互处理；已成功物化的共享 Skill Asset 不回滚。

Center Skill 的技术合同支持所有实际存在的 Bot Type × Engine 组合。前端可以按 PRD 隐藏尚未开放
的产品入口，但不能根据静态矩阵把 Backend 409 当成“技术不支持”，Backend 也不定义
`SKILL_RUNTIME_NOT_SUPPORTED`。

## 14. Track Latest 的产品边界

新 Version PUBLISHED 后，Personal/Desktop Bot 与 Service Draft 异步收敛 latest：

- 发布页不等待全部 Bot 更新；
- 本期不展示逐 Bot actual version；
- Runtime 失败不回滚 Version；
- Published Service Release 冻结 exact Version，restart/scale/rollback 不跟随 latest；
- 下一次 Service 发布才使用最新版本。

前端不能在发布成功弹窗中承诺“全部 Bot 已完成升级”。

## 15. 全局异常与恢复

| 场景 | 前端行为 |
| --- | --- |
| 重复点击创建/发布 | 禁用按钮；超时重放同一 Idempotency-Key |
| 请求超时且结果未知 | 使用同一 Key 重放，禁止生成新 Key |
| Draft revision 冲突 | 拉取最新 detail/file，提示重新编辑 |
| Lease 被 Takeover | 退出编辑，丢弃旧 token，重新 GET lease |
| SC 市场不可用 | 保留搜索条件，允许刷新；不创建假资产 |
| Publication RESULT_UNKNOWN | 显示“确认中”；不重新发布 |
| recovery AVAILABLE | 显示统一重试按钮，POST 同 Attempt retry |
| Reference 部分失败 | 保留成功项；失败项新请求重试 |
| Bot mutation 返回 423 | 获取/刷新 Bot edit lock 后重放原请求 |
| Runtime projection 失败 | 展示失败并刷新最终 SkillSet/Reference 状态 |
| Offline blocker 变化 | 重新 GET offline-impact |
| 页面刷新 | 从 detail、Attempt collection 或 Reference collection 恢复 |

## 16. 联调清单

### 16.1 Mock/合同阶段

- 按 Operation ID 建 API client，不自行改路径。
- 覆盖未知 additive 字段、200/201/202、403、404、409、422、423、503。
- 模拟 Published V1 + Draft V2、Offline + Draft、RESULT_UNKNOWN、Reference 部分成功。
- 不把候选 API 当成已发布 Gateway 路由。

### 16.2 Backend 路由落地后

- 以 Gateway `/openapi.json` 重新生成类型，所有请求经 Gateway。
- 验证 Envelope、分页、Idempotency-Key 和 request_id。
- 文件夹上传覆盖嵌套目录、中文路径、重复文件名和非法路径。
- Team 编辑覆盖 acquire、takeover、旧 token 保存失败。
- 发布覆盖影响提示、轮询、恢复和页面刷新。
- Reference 覆盖 20 项、部分成功、Set 删除和 inactive/active Set。
- Offline 覆盖每类 blocker 与重新发布恢复。

### 16.3 正式切流前

- Swagger 中每个标记已实现的 Operation 都真实可调用，无 501/stub。
- Phase 1 Local/Repo/SkillSet/MCP 回归通过。
- 前端不依赖旧 `status/draft_status/can_edit/retire_skill`。
- 产品文案使用“发布前影响”“Offline 后创建下一版 Draft”“新建 SkillSet 默认 active”。
