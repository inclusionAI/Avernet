# Skill 前端接口 Review Guide

> 状态：设计阶段接口说明；与同目录正式 Spec 一起 Review。
>
> 适用对象：TeamClaw Web 前端、产品联调、接口评审。

## 1. 哪份文档是权威

实现前，以同目录正式 Spec 的路径、领域语义和兼容规则为权威；本文只把前端需要的
调用流程集中展示。

实现后，以 Gateway 实际发布的 OpenAPI JSON 与 Swagger 为机器可读权威。它由
Backend 真实路由自动生成并经过向前兼容检查，前端可据此查看完整 request/response、
生成类型或 Client。不得为了提前展示而手工修改生产 OpenAPI artifact。

## 2. 通用约定

- Base prefix：`/openapi/v1`。
- `skill_id` 是十进制字符串形式的 `ac_skill.id`。
- 有 `skill_id` 的 Item API 由服务端判断 LOCAL/REPO/SPACE，前端不传 type。
- Bot Skill 列表中，旧调用不传 type 仍只返回 LOCAL；新产品必须显式传
  `type=ALL`。
- 返回继续使用 Gateway Envelope：`code`、`message`、`data`、`request_id`。
- 已发布接口中已有的 `user_id` query 参数继续保留；服务端必须验证调用身份，
  不能把客户端传值直接当作授权事实。
- 创建 Space Skill、升级 Draft、发布和物化重试要求 `Idempotency-Key`。
- Membership PUT/DELETE、activate/deactivate 都是幂等操作。

统一错误：

| HTTP | 前端处理 |
| --- | --- |
| 400 | 请求格式错误 |
| 403 | 无权限；展示 ACL、Owner/Manager 或 MCP 权限提示 |
| 404 | 对象不存在或当前用户不可见 |
| 409 | 状态冲突；读取稳定 error_code 展示业务提示 |
| 422 | ZIP、SKILL.md、Git 内容或参数校验失败 |
| 502/503 | Skill Center 或 Runtime 暂时不可用 |

重点 `409 error_code`：

- `RESOURCE_DIRECT_ACTIVE`
- `RESOURCE_MANAGED_BY_SKILL_SET`
- `RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET`
- `BOT_NOT_READY`
- `SYNC_IN_PROGRESS`

## 3. 前端所见 Skill 状态

统一 Skill 列表的核心字段：

| 字段 | 含义 |
| --- | --- |
| `skill_id` | 稳定公开身份 |
| `name` / `description` | 当前投影；Space Skill 来自 SKILL.md |
| `type` | LOCAL / REPO / SPACE；新增 optional 字段 |
| `active` | 当前 Bot 是否存在有效 Installation |
| `managed_by` | DIRECT / SKILL_SET；新增 optional 字段 |
| `skill_set_id` | 由普通 SkillSet 管理时返回；否则为空 |
| `tags` | 保持既有 wire |

不要在前端推导 Direct active、Effective active 或 Runtime observed status。
`active=true` 只表示当前 Desired State 应当生效。

## 4. Phase 1：Bot Skill

### 4.1 Product Bot Skill 列表

`GET /openapi/v1/bots/{bot_id}/skills?type=ALL`

产品默认展示以下集合的并集：

1. Bot-owned Local Asset；
2. 普通 SkillSet Membership；
3. 当前有效 Installation。

过滤、按 skill_id 去重后再分页。旧调用不传 type 的行为不能改变。

### 4.2 Local 上传

`POST /openapi/v1/bots/{bot_id}/skills`

- Body 是 raw ZIP，Content-Type 为 `application/zip`。
- 新建 Local Skill 返回 inactive。
- 同 Bot 同名上传原地替换，保留 skill_id 和原 active。
- 产品随后把它加入 SkillSet；若该 Set 当前 active，添加成功后立即生效。

“添加本地文件夹”保持现有产品的 multipart 交互，不要求前端自行把文件夹压成 ZIP：

`POST /openapi/v1/bots/{bot_id}/skills/upload-folder`

- Content-Type：`multipart/form-data`。
- `files`：重复字段，包含选中文件夹中的全部文件。
- `file_paths`：可选 JSON 字符串数组，与 `files` 一一对应，保存相对目录结构；不传时退回每个文件名。
- 与 raw ZIP 共用同一包校验、唯一 `SKILL.md`、同名替换、新建 inactive、大小/文件数限制和响应 Envelope。
- 旧 `/api/skills/upload` 原样保留，本期不修改其实现；新接口沿用其 `files + file_paths` wire 语义。新 OpenAPI 的文件夹与 raw ZIP 入口在请求解码后共用同一个 `LocalSkillUploadService` 生命周期。

### 4.3 Item 与内容

| Method | Path | 前端用途 |
| --- | --- | --- |
| GET | `/bots/{bot_id}/skills/{skill_id}` | Skill 详情 |
| DELETE | `/bots/{bot_id}/skills/{skill_id}` | 仅删除无引用的 inactive Local Asset |
| GET | `/bots/{bot_id}/skills/{skill_id}/content` | 读取可展示 SKILL.md |
| GET | `/bots/{bot_id}/skills/{skill_id}/parameters` | 读取 Bot 级参数 |
| PUT | `/bots/{bot_id}/skills/{skill_id}/parameters` | 全量保存 Bot 级参数 |

### 4.4 Direct API

| Method | Path | 语义 |
| --- | --- | --- |
| POST | `/bots/{bot_id}/skills/{skill_id}/activate` | 直接生效 |
| POST | `/bots/{bot_id}/skills/{skill_id}/deactivate` | 直接停用 |

产品界面不直接使用 Direct；外部 OpenAPI 调用方可以使用。资源已属于普通 SkillSet
时，Direct 操作返回 `RESOURCE_MANAGED_BY_SKILL_SET`。

## 5. Phase 1：Repo Catalog

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/openapi/v1/bots/skills/repository` | keyword/path/sort/page/page_size |
| GET | `/openapi/v1/bots/skills/repository/tree` | aiworkbench 目录树 |
| GET | `/openapi/v1/bots/skills/repository/{skill_id}` | Repo 详情；Phase 2 additive 支持 Space |
| POST | `/openapi/v1/bots/skills/repository/sync` | 同步完成后返回 |

Sync 是同步接口。前端 await 返回后刷新列表，不轮询新的 sync-status。并发同步返回
`SYNC_IN_PROGRESS`。

### 5.1 添加 Skill 弹窗的三个来源

三个目录独立读取，不能把 Skill Center 团队搜索当作能力工坊搜索：

| 产品入口 | 前端接口 | 结果来源 |
| --- | --- | --- |
| 引用市场 Skill / TeamClaw | `GET /openapi/v1/bots/skills/repository` | aiworkbench 扫描后的 `git://` Repo Skill |
| 引用市场 Skill / Skill Center | `POST /openapi/v1/bots/market/skill-center/skills` | Skill Center PUBLIC 市场；服务端注入凭据，固定不传 `teamId` |
| 引用工坊 Skill | `GET /openapi/v1/bots/spaces/{space_id}/skills/consumable` | 当前 Space 中已发布且运行时物化完成的 Skill |

已发布兼容接口 `POST /openapi/v1/bots/market/skills` 同样读取 TeamClaw Git 市场；新产品页面优先使用 Repository Catalog。

Skill Center Public Skill 不做全量扫描入 `ac_skill`。前端选中后，目标合同为：

```text
POST /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references
GET  /openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}

Idempotency-Key: required
{
  "skill_code": "stable-skill-code"
}
```

POST 返回 `202` 和 `reference_id`；前端轮询 GET，状态为
`PENDING/RUNNING/SUCCEEDED/FAILED`。Backend 冻结精确 SC Version，完成 TeamClaw Canonical Store Ready
物化并使 Version=`PUBLISHED` 后才写普通 SkillSet Membership；前端不编排“物化后再
添加”两步。当前仅冻结该接口合同，未实现完整链路前不得加入 Gateway 正式 OpenAPI artifact，也不得返回“已引用成功”的假结果。

## 6. Phase 1：SkillSet

Prefix：`/openapi/v1/bots/{bot_id}/skill-sets`

| Method | Path | 说明 |
| --- | --- | --- |
| GET/POST | `/skill-sets` | 列表/创建 inactive Set |
| GET/PUT/DELETE | `/skill-sets/{set_id}` | 详情/改名描述/删除 inactive 普通 Set |
| GET | `/skill-sets/{set_id}/skills` | Skill Membership |
| PUT/DELETE | `/skill-sets/{set_id}/skills/{skill_id}` | 添加/移除 Skill |
| POST | `/skill-sets/{set_id}/activate` | 原子激活全部 Skill/MCP |
| POST | `/skill-sets/{set_id}/deactivate` | 原子停用全部 Skill/MCP |
| GET | `/skill-sets/resources` | 全部 Set 的 MCP/Default CLI 聚合 |
| GET | `/skill-sets/{set_id}/mcps` | MCP Membership |
| PUT/DELETE | `/skill-sets/{set_id}/mcps/{server_code}` | 添加/移除 MCP |
| GET | `/skill-sets/{set_id}/mcp-permissions` | 权限聚合 |
| POST | `/skill-sets/{set_id}/mcp-permission-requests` | 发起既有权限申请 |
| DELETE | `/skill-sets/{set_id}/clis/{resource_code}` | Default CLI 排除 |

产品流程：

1. Local 先上传；Repo/Space 已存在于共享 Catalog。
2. PUT Membership。
3. 如果 Set inactive，只保存 Membership，Skill 不生效。
4. 如果 Set active，Membership 与 Installation 同事务写入，Skill 立即生效。
5. Activate/Deactivate 是整个 Set 的原子动作，不存在半选。

System Default 在列表中以 `is_default` 表示，始终 active，不提供单独 default API。

## 7. Phase 1：MCP

已发布 Catalog/Permission/Config 保持不变：

- `GET /openapi/v1/bots/mcp/servers`
- `GET /openapi/v1/bots/mcp/tenants`
- `GET /openapi/v1/bots/mcp/servers/{server_code}`
- `GET /openapi/v1/bots/mcp/servers/{server_code}/permissions`
- `GET/PUT /openapi/v1/bots/mcp/servers/{server_code}/config`

新增 Bot Direct：

| Method | Path |
| --- | --- |
| GET | `/openapi/v1/bots/{bot_id}/mcps` |
| POST | `/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate` |
| POST | `/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate` |

产品仍通过 SkillSet 管理 MCP；权限不足时先展示/发起既有权限申请。

## 8. 已冻结的 Space/Member/Favorite

七桃负责的已发布接口保持当前 OpenAPI，不在本次重新命名或改 wire。本期不提供
Space 删除。

## 9. Phase 2：Space Skill 创作

Prefix：`/openapi/v1/bots/spaces/{space_id}/skills`

| Method | Path | 前端流程 |
| --- | --- | --- |
| GET/POST | `/skills` | 列表/multipart 本地文件夹创建 |
| POST | `/skills/import-from-git` | 从 Git 创建新 Identity |
| GET | `/skills/{skill_id}` | 创作详情 |
| POST | `/skills/{skill_id}/draft/upgrade` | 创建下一版本 Draft |
| GET/DELETE | `/skills/{skill_id}/draft` | 查询/放弃 EDITING Draft |
| GET | `/skills/{skill_id}/draft/files` | 文件树 |
| GET/PUT | `/skills/{skill_id}/draft/files/{path}` | 读写文件 |
| POST | `/skills/{skill_id}/draft/replace` | multipart 本地文件夹原子替换 |
| POST | `/skills/{skill_id}/draft/refresh-from-git` | 手动刷新 |
| GET | `/skills/{skill_id}/versions` | Published Version 列表 |
| GET | `/skills/{skill_id}/versions/{version}` | 精确业务版本详情 |
| GET | `/skills/{skill_id}/versions/{version}/files` | 版本文件树 |
| GET | `/skills/{skill_id}/versions/{version}/files/{path}` | 版本文件内容 |

创作详情必须分别展示：

- `latest_published_version`
- `draft_target_version`
- `draft_status`
- `publication_status`

不能用一个 status 同时表达 Skill 可用性和 Draft 发布进度。

### 9.1 Space Skill 列表领域摘要

`GET /openapi/v1/bots/spaces/{space_id}/skills` 返回统一 `SpaceSkillSummary`，用于前端一次绘制
列表并避免逐卡调用 Lease/Work Order/Publication 造成 N+1。返回的是领域摘要，
不是按钮 ViewModel。

当前代码中的旧 `SpaceSkillItem` 尚无真实调用方，已与前端确认可直接替换；新接口不保留
`status/draft_status/current_user_skill_role/can_edit/can_grant/can_apply_edit`，前端只按
以下 `SpaceSkillSummary` 合同开发。

```json
{
  "skill_id": "1001",
  "skill_uuid": "uuid",
  "name": "release-note",
  "description": "生成版本发布说明",
  "space_type": "TEAM",
  "owner": { "user_id": "u-owner", "display_name": "王五" },
  "lifecycle_status": "PUBLISHED",
  "latest_published_version": 1,
  "draft": { "target_version": 2, "status": "EDITING" },
  "active_publication": null,
  "actor": {
    "skill_role": "MANAGER",
    "permissions": {
      "edit_draft": true,
      "publish_draft": true,
      "delete_draft": true,
      "create_upgrade_draft": true,
      "retire_skill": true,
      "manage_grants": false,
      "transfer_owner": false,
      "request_edit_access": false,
      "takeover_lease": true
    },
    "pending_editor_request": null
  },
  "lease_summary": {
    "required": true,
    "state": "HELD_BY_OTHER",
    "holder_user_id": "u-li-si",
    "holder_display_name": "李四"
  },
  "gmt_modified": "2026-08-24T08:00:00Z"
}
```

- `lifecycle_status/latest_published_version/draft/active_publication` 是 Skill 当前状态事实。
- `actor.permissions` 只表示调用者基于 ACL/Grant 是否有资格发起命令，不保证
  命令在当前 Draft/Lease/Attempt 状态下一定成功。
- `pending_editor_request` 只是当前用户的 PENDING 工单摘要；详情走 Work Order。
- `lease_summary` 只用于列表锁图标和 holder 展示，不含 fencing token。进入编辑流程
  时必须重新调用 Lease 接口。
- 公共响应不包含 `AVAILABLE/BLOCKED/HIDDEN`、按钮文案或 Tooltip。
- 工坊列表保留已退役 Skill 供历史查看；市场/Consumable 列表排除已退役 Skill。
- Phase 2 最终 PRD 不包含 Skill 复制/Clone。

## 10. Phase 2：Owner、Manager、编辑申请与 Lease

| Method | Path |
| --- | --- |
| GET | `/bots/spaces/{space_id}/skills/{skill_id}/grants` |
| PUT/DELETE | `/bots/spaces/{space_id}/skills/{skill_id}/managers/{manager_user_id}` |
| POST | `/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer` |
| POST | `/bots/spaces/{space_id}/skills/{skill_id}/editor-requests` |
| GET/PUT/DELETE | `/bots/spaces/{space_id}/skills/{skill_id}/draft/lease` |
| POST | `/bots/spaces/{space_id}/skills/{skill_id}/draft/lease/takeover` |

权限管理分成两条不同的前端流程：

1. **Owner 直接授权**：Owner 在“权限管理”中选择当前 Space Member，调用
   `PUT .../managers/{manager_user_id}`。这是直接命令，不产生工单。撤销调用
   `DELETE .../managers/{manager_user_id}`。
2. **成员申请编辑权限**：当 `actor.permissions.request_edit_access=true` 且
   `actor.pending_editor_request=null` 时，成员调用
   `POST .../editor-requests`。服务端自行确定当前 Skill Owner，创建
   `biz_type=SKILL_COLLABORATOR` 的工单。Owner 通过通用 Work Order 接口
   审批；审批通过后服务端写入 `MANAGER` Grant，拒绝则不改变 Skill
   Grant。

申请 body：

```json
{ "reason": "需要共同维护该 Skill" }
```

申请人和 Owner 查询/审批复用已有 Work Order 接口：

```text
GET  /openapi/v1/bots/work-orders?query_type=INITIATED_BY_ME&item_type=APPROVAL&biz_type=SKILL_COLLABORATOR&biz_id={skill_id}
GET  /openapi/v1/bots/work-orders?query_type=PENDING_FOR_ME&item_type=APPROVAL&biz_type=SKILL_COLLABORATOR&biz_id={skill_id}
GET  /openapi/v1/bots/work-orders/{work_order_id}
POST /openapi/v1/bots/work-orders/{work_order_id}/approval
```

审批 body：

```json
{
  "decision": "APPROVED",
  "review_remark": "同意共同维护"
}
```

前端不能调用通用 `/work-orders/events` 自行指定审批人；Owner 必须由
Skill 权限模块从当前 Grant 中确定。同一申请人对同一 Skill 同时最多一个
`PENDING` 工单。已是 Owner/Manager、非 Active Space Member 或 Personal Space 不能
发起申请。

以下是后端权限语义，不规定页面按钮显隐。前端使用 `actor.skill_role`、
`actor.permissions`、Draft/Attempt/Lease 事实生成自己的 ViewModel：

| 身份 | 编辑/发布 | 添加/移除 Manager | 转移 Owner | 申请编辑 |
| --- | --- | --- | --- | --- |
| Skill Owner | 可 | 可 | 可 | 不显示 |
| Skill Manager | 可 | 不可 | 不可 | 不显示 |
| Space Administrator（无 Skill Grant） | 不可 | 不可 | 可，必须填原因 | 按普通成员规则 |
| Active Space Member（无 Skill Grant） | 不可 | 不可 | 不可 | Team Space 可 |
| 非 Space Member | 不可 | 不可 | 不可 | 不可 |

Space Administrator 不会因管理 Space 自动获得 Skill 编辑、发布或授权权限。
Owner 转移与旧 Lease 失效必须原子完成；原 Owner 的 Grant 被撤销，不自动保留为
Manager。

Team Skill 编辑需要 Lease；Personal 返回 `required=false`。本期没有 TTL/心跳续租。
关闭编辑抽屉主动释放；Owner/Manager 可 Takeover。每次新获取/抢占都会生成新的
fencing token，旧页面保存必须被拒绝。

Draft 文件的持久化方式、`DraftContentStore` Protocol、OSS 路径与 OSS/DB 补偿仍是
**TBD**。本轮 Grant、Editor Request 与 Lease 合同不承诺任何 Draft 物理存储布局；
前端也不得依赖临时 URI 或推测的 OSS 路径。

## 11. Phase 2：发布与退役

| Method | Path |
| --- | --- |
| GET | `/bots/spaces/{space_id}/skills/{skill_id}/upgrade-impact` |
| POST/GET | `/bots/spaces/{space_id}/skills/{skill_id}/publications` |
| GET | `/bots/spaces/{space_id}/skills/{skill_id}/publications/{attempt_id}` |
| POST | `/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/materialization-retry` |
| GET | `/bots/spaces/{space_id}/skills/{skill_id}/retirement-impact` |
| POST | `/bots/spaces/{space_id}/skills/{skill_id}/retirement` |

Publish 返回 `202` 和 Attempt。前端轮询 Attempt；状态为：

- PREPARING
- SC_SUBMITTING
- WAITING_SC
- MATERIALIZING
- SUCCEEDED
- FAILED
- RESULT_UNKNOWN

FAILED 后 Draft 恢复 EDITING；RESULT_UNKNOWN 时 Draft 保持 FROZEN，普通用户没有
重试/取消按钮。MATERIALIZING 失败只允许重试同一 Version，不再次发布 SC。

前端展示统一为：

- `PREPARING/SC_SUBMITTING/WAITING_SC`：发布中；
- `MATERIALIZING`：物化中；
- TeamClaw Canonical Store Ready 后，`SUCCEEDED` 且 Version=`PUBLISHED`：发布成功；
- `RESULT_UNKNOWN`：发布结果确认中，不提供普通重新发布按钮。

SC Published 本身不等于发布成功；本期支持消费者所需的 Canonical Store 未全部 Ready
时仍显示“物化中”。发布成功后触发的 Bot Track Latest 是 Best-Effort 异步刷新，
不影响当前 Skill 已发布成功的展示。

## 12. 前端验收重点

1. 旧 Local 页面和外部调用不因 type 扩展改变。
2. 产品 Bot Skill 列表显式 type=ALL。
3. SkillSet 永远整组 active/inactive，不出现半选 UI。
4. active Set 添加/移除成员后页面与运行时结果一致。
5. Direct 与 SkillSet 冲突使用稳定 error_code 提示用户。
6. Repo sync await 完成后刷新，不新增轮询。
7. Space Skill 同时存在 Published V1 与 Draft V2 时，两类状态分开展示。
8. Space Skill 列表返回领域摘要和 `actor.permissions`，不返回按钮的
   `AVAILABLE/BLOCKED/HIDDEN`；前端统一生成 ViewModel。
9. Owner 直接授权不产生工单；普通成员申请编辑必须产生
   `SKILL_COLLABORATOR` 工单，审批通过后再变成 Manager。
10. Service Bot 已发布 Release 不因 Skill latest 变化而显示为自动升级。
11. 能力工坊不显示 Skill 复制按钮。
12. 所有 Loading、空态、无权限、校验失败、外部失败和重复点击均可恢复。
