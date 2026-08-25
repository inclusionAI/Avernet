# 前端页面联调文档 —— 能力工坊（Phase 2）详细出入参

> 适用模块：能力工坊 Space Skill 创建、Draft 编辑、协作治理、发布与退役
>
> 文档版本：v1.1；状态：**Phase 2 目标契约**。本手册为能力工坊页面的
> 前端开发、Mock 与测试提供参考。它不代表 PR #1342 的 contract-only `501`
> 实现；该 Mock 实现已废弃，前端应以本文档的目标请求/响应进行 Mock。
>
> 后端真实实现完成后，最终机器可读权威是 Gateway 从 Backend 真实路由生成的
> `configs/schemas/bots.openapi.json`。在正式实现合入之前，不应根据当前环境是否
> 存在占位路由判断业务能力是否可用。

## 1. 使用范围与通用约定

本手册对应 PRD 中的能力工坊：创建/导入 Skill、Draft 编辑、权限治理、编辑 Lease、
版本浏览、发布与退役。它补充 [Skill 前端接口 Review Guide](frontend-api.md) 的
第 9～12 节。本文冻结前端 Mock 使用的目标路径和 schema；后端实现时
必须用显式 FastAPI handler/schema 将同一契约生成到 Gateway OpenAPI。

### 1.1 基础地址与身份

所有接口前缀为：

```text
/openapi/v1/bots/spaces/{space_id}/skills
```

- `space_id`：正整数 Space 标识。
- `skill_id`：平台内稳定 Skill 标识；不要把 `skill_uuid` 当作 path 参数。
- 所有新增 Space Skill 接口都必须带 query `user_id=<当前用户>`。已有 Work
  Order 接口例外，它们直接从认证 principal 取当前用户。
- Manager 资源参数是 `{manager_user_id}`，与调用者 `user_id` 不同。
- 当前只允许带真实用户身份的调用；app-only caller 被拒绝。

### 1.2 Envelope、状态和 Mock 规则

成功目标响应统一使用：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {},
  "request_id": "trace-id"
}
```

创建类目标成功使用 `201000`/`Created`，异步命令使用 `202000`/`Accepted`。

前端 Mock 建议按下列目标 HTTP 状态产生页面状态：

| HTTP | Mock 含义 | 页面建议 |
| --- | --- | --- |
| 200 | 查询/同步命令完成 | 刷新当前卡片、列表或抽屉 |
| 201 | 创建 Skill 或 Upgrade Draft | 跳转/打开新 Draft |
| 202 | 发布、物化重试、退役已受理 | 轮询 Attempt 或展示处理中 |
| 403 | 非 Owner/Manager 或 Space Member | 展示权限页/禁用命令 |
| 404 | Space、Skill、Version 或 Attempt 不可见 | 返回列表或空态 |
| 409 | Draft/Lease/状态机冲突 | 刷新详情后提示冲突 |
| 422 | 本地文件夹、SKILL.md、Git、路径或字段校验失败 | 定位到上传/表单字段 |
| 502/503 | Skill Center 或运行时依赖不可用 | 可重试的外部失败态 |

### 1.3 特殊请求约束

- 本地文件夹创建和 Draft 文件夹替换使用 `multipart/form-data`：每个文件使用重复的
  `files` 字段；`file_paths` 是可选 JSON 字符串数组，与每个文件一一对应，用来保留相对
  目录结构。前端不需要、也不应自行压缩为 ZIP。
- 下列命令必须带非空 `Idempotency-Key` header：本地文件夹创建、Git 导入、创建升级 Draft、
  创建发布、物化重试。
- `PUT/DELETE manager` 是幂等命令；不要以“已经存在/不存在”作为错误提示。
- `{version}` 是业务版本序号（`1`、`2`……），不是数据库主键。

## 2. 页面与接口映射

### 2.1 工坊列表页与创建入口

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data | Mock 页面状态 |
| --- | --- | --- | --- | --- |
| Space Skill 列表 | `GET /openapi/v1/bots/spaces/{space_id}/skills` | `user_id`，可选 `keyword`、`page_no`、`page_size` | `Page<SpaceSkillSummary>` | loading、空态、卡片列表、无权限 |
| 本地文件夹创建 | `POST /openapi/v1/bots/spaces/{space_id}/skills` | `files` + `file_paths` multipart；`Idempotency-Key`；`user_id` | `SpaceSkillDetail`（201） | 上传中、校验失败、创建后打开详情 |
| Git 导入 | `POST /openapi/v1/bots/spaces/{space_id}/skills/import-from-git` | `GitImportRequest`；`Idempotency-Key`；`user_id` | `SpaceSkillDetail`（201） | 导入中、内容错误、创建后打开详情 |
| 创作详情 | `GET /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}` | `user_id` | `SpaceSkillDetail` | Draft/Published 双状态、当前调用者领域权限 |

#### 2.1.1 获取 Space Skills 列表

- **URL**：`GET /openapi/v1/bots/spaces/{space_id}/skills`
- **状态**：Phase 2 目标契约；前端可按本节直接建立 Mock。
- **说明**：能力工坊首页/Space 下的 Skills Tab 使用此接口。它只返回当前调用用户在该
  Space 可见的 Skill 卡片；前端不应以 Bot 的 `/openapi/v1/bots/{bot_id}/skills` 列表替代。

| 字段名 | 位置 | 类型 | 必填 | 默认值 | 描述 |
| --- | --- | --- | --- | --- | --- |
| `space_id` | path | integer | 是 | - | Space 主键，最小值 1 |
| `user_id` | query | string | 是 | - | 当前调用用户；必须与已认证身份一致 |
| `keyword` | query | string | 否 | - | 按 Skill 名称或描述搜索，最长 128 字符 |
| `page_no` | query | integer | 否 | `1` | 从 1 开始的页码，最小值 1 |
| `page_size` | query | integer | 否 | `20` | 每页最多 100 条，范围 1～100 |

**请求示例**：

```http
GET /openapi/v1/bots/spaces/42/skills?user_id=u-owner&keyword=release&page_no=1&page_size=20
```

**成功响应**：`Envelope<Page<SpaceSkillSummary>>`

```json
{
  "code": 200000,
  "message": "OK",
  "request_id": "mock-list-1",
  "data": {
    "total": 1,
    "items": [
      {
        "skill_id": "1001",
        "skill_uuid": "9d41d2fa-7ef8-4b87-9f56-123456789abc",
        "name": "release-note",
        "description": "生成版本发布说明",
        "lifecycle_status": "DRAFT_ONLY",
        "latest_published_version": null,
        "space_type": "TEAM",
        "owner": {
          "user_id": "u-owner",
          "display_name": "张三"
        },
        "draft": {
          "target_version": 1,
          "status": "EDITING"
        },
        "active_publication": null,
        "actor": {
          "skill_role": "OWNER",
          "permissions": {
            "edit_draft": true,
            "publish_draft": true,
            "delete_draft": true,
            "create_upgrade_draft": true,
            "retire_skill": true,
            "manage_grants": true,
            "transfer_owner": true,
            "request_edit_access": false,
            "takeover_lease": true
          },
          "pending_editor_request": null
        },
        "lease_summary": {
          "required": true,
          "state": "FREE",
          "holder_user_id": null,
          "holder_display_name": null
        },
        "gmt_created": "2026-08-22T10:00:00Z",
        "gmt_modified": "2026-08-22T10:30:00Z"
      }
    ]
  }
}
```

`items` 即使没有结果也始终为数组（`[]`），`total` 为 `0`；列表页应将其渲染为“空工坊”
而不是错误页。无当前 Space 访问权限时后端返回 `403`；Space 不存在或对调用者不可见时
返回 `404`。完整卡片字段释义见 [5.3 列表与详情响应字段](#53-列表与详情响应字段)。

`SpaceSkillSummary` 是列表卡片，至少使用：

```text
skill_id, skill_uuid, name, description, lifecycle_status,
latest_published_version, draft, active_publication, space_type, owner,
actor.skill_role, actor.permissions, actor.pending_editor_request,
lease_summary, gmt_created, gmt_modified
```

`SpaceSkillDetail` 是详情头部，必须分开展示以下字段，不能合并为一个泛化 status：

```text
skill_id, skill_uuid, name, description,
latest_published_version, draft, active_publication,
owner, actor, lease_summary
```

列表返回领域摘要，不返回页面按钮状态。`actor.permissions` 表达当前调用者是否有
资格发起领域命令，不保证命令在当前 Draft/Lease/Attempt 状态下一定成功。
`lease_summary` 只服务列表显示，不含 fencing token；进入编辑流程后必须调用 Lease 资源
重新确认。列表保留已退役 Skill，但市场/Consumable 列表不返回已退役 Skill。

Phase 2 最终 PRD 不包含 Skill 复制/Clone；Mock 和正式页面都不展示复制按钮。

#### 本地文件夹创建与 Git 导入 request

本地文件夹创建不使用 JSON body 或 ZIP。浏览器选择文件夹后，提交以下 multipart 表单：

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `files` | `File[]` | 是 | 重复字段；包含所选文件夹内的所有文件 |
| `file_paths` | string | 否 | JSON 字符串数组，和 `files` 一一对应，例如 `["SKILL.md","scripts/check.py"]`；不传时使用文件名 |

```javascript
const form = new FormData();
for (const file of selectedFiles) form.append('files', file);
form.append('file_paths', JSON.stringify(selectedFiles.map((file) => file.webkitRelativePath)));

await fetch('/openapi/v1/bots/spaces/42/skills?user_id=u-owner', {
  method: 'POST',
  headers: { 'Idempotency-Key': crypto.randomUUID() },
  body: form,
});
```

```json
// POST .../import-from-git
{
  "repository_url": "https://example.com/acme/skill.git",
  "branch": "main",
  "subdir": "skills/release-note"
}
```

`branch` 与 `subdir` 可省略。文件夹中必须有且仅有一个 `SKILL.md`，它是名称和描述的
事实来源。

#### 2.1.2 公共市场/已发布 Skill 的 README

公共市场查看 Skill 详情时没有 `bot_id`，使用独立的 Skill Asset 接口：

```http
GET /openapi/v1/bots/skills/{skill_id}/readme?user_id=u-member
```

目标响应：`Envelope<SkillReadme>`

```json
{
  "code": 200000,
  "message": "OK",
  "request_id": "mock-readme-1",
  "data": {
    "content": "# release-note\n\n生成版本发布说明"
  }
}
```

- `skill_id` 是唯一的资源定位参数；`user_id` 仅用于身份校验。
- 前端不传 `bot_id`、`entity_id`、`entity_type` 或 `engine_type`。
- 只读取共享 Repo Skill 或已发布 Space Skill。Local Skill、Draft 和未发布
  Space Skill 不从此接口返回。
- Bot 已安装 Skill 的内容仍使用
  `GET /openapi/v1/bots/{bot_id}/skills/{skill_id}/content`，不要在公共市场页面调用。

公共市场和 Space Skill Asset 详情页也不调用 Bot Runtime 参数值接口。
`GET/PUT /openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters` 的资源是“某个 Bot 上
某个 Skill 的实际配置值”，只在已明确选定目标 Bot 的配置页面使用。旧 BFF 虽然 path
中无 `bot_id`，但强制接收 `bot_id/entity_id/engine_type` query 并写入目标 Bot
容器，本期不迁移为 Botless OpenAPI。参数定义/schema 仍从 `SKILL.md config`
或对应 Draft/Version 内容读取。

### 2.2 Draft 编辑抽屉

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| 创建下一 Draft | `POST /{skill_id}/draft/upgrade` | `user_id`、`Idempotency-Key` | `DraftDetail`（201） |
| 读取 Draft | `GET /{skill_id}/draft` | `user_id` | `DraftDetail` |
| 放弃 Draft | `DELETE /{skill_id}/draft` | `user_id` | `DraftDetail` |
| Draft 文件树 | `GET /{skill_id}/draft/files` | `user_id` | `FileTreeItem[]` |
| 读取文件 | `GET /{skill_id}/draft/files/{path}` | `user_id` | `FileContent` |
| 保存文件 | `PUT /{skill_id}/draft/files/{path}` | `WriteDraftFileRequest`、`user_id` | `FileContent` |
| 本地文件夹原子替换 | `POST /{skill_id}/draft/replace` | `files` + `file_paths` multipart、`user_id` | `DraftDetail` |
| 从 Git 刷新 | `POST /{skill_id}/draft/refresh-from-git` | `RefreshDraftFromGitRequest`、`user_id` | `DraftDetail` |

`DraftDetail`：

```text
target_version, status, source_type, repository_url, branch, subdir, gmt_modified
```

`FileTreeItem`：`path`、`is_directory`、`size_bytes`；`FileContent`：`path`、`content`。

保存文件 body：

```json
{
  "content": "---\nname: release-note\n---\n",
  "fencing_token": 7
}
```

`fencing_token` 仅 Team Space 在持有 Lease 时使用；Personal Space 可不传。Draft 文件夹
替换沿用上节同一个 `files + file_paths` multipart wire，成功时必须原子替换全量文件，不能
部分更新。Git 刷新：

```json
{ "confirm_overwrite": true }
```

Mock 必须覆盖 `EDITING`、`FROZEN`：`FROZEN` 时禁用文件写入、replace、refresh 和
abandon；不允许把 Git 刷新失败模拟成部分文件更新。

### 2.3 Owner、Manager、编辑权限申请与 Lease

这里有两条不同的产品链路，前端不能混用：

- **直接权限管理**：Skill Owner 在详情页直接添加/移除 Manager，不生成工单。
- **编辑权限申请**：无 Skill Grant 的 Team Space 成员申请编辑，生成
  `SKILL_COLLABORATOR` 工单；Skill Owner 审批通过后，申请人成为 Manager。

#### 2.3.1 接口总表

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| 权限列表 | `GET /{skill_id}/grants` | `user_id` | `SkillGrants` |
| Owner 直接授予 Manager | `PUT /{skill_id}/managers/{manager_user_id}` | `user_id`；无 body | `SkillGrant` |
| Owner 直接移除 Manager | `DELETE /{skill_id}/managers/{manager_user_id}` | `user_id`；无 body | `SkillGrant` |
| 转移 Owner | `POST /{skill_id}/owner-transfer` | `OwnerTransferRequest`、`user_id` | `SkillGrants` |
| 申请 Skill 编辑权限 | `POST /{skill_id}/editor-requests` | `CreateSkillEditorRequest`、`user_id` | `SkillEditorRequestCreated`（201） |
| 查询 Lease | `GET /{skill_id}/draft/lease` | `user_id` | `DraftLease` |
| 获取 Lease | `PUT /{skill_id}/draft/lease` | `user_id` | `DraftLease` |
| 释放 Lease | `DELETE /{skill_id}/draft/lease` | `user_id` | `DraftLease` |
| Takeover Lease | `POST /{skill_id}/draft/lease/takeover` | `user_id` | `DraftLease` |

表内缩写路径都基于：

```text
/openapi/v1/bots/spaces/{space_id}/skills
```

#### 2.3.2 权限列表和直接授权

`GET /{skill_id}/grants` 返回 `SkillGrants`：

```json
{
  "owner": { "user_id": "u-owner", "role": "OWNER" },
  "managers": [{ "user_id": "u-manager", "role": "MANAGER" }]
}
```

Owner 在权限管理抽屉点击“添加”时，直接调用：

```http
PUT /openapi/v1/bots/spaces/42/skills/1001/managers/u-new-manager?user_id=u-owner
```

该命令要求目标用户是当前 Active Space Member。它是幂等直接授权，不产生
Work Order。移除 Manager 调用同路径的 `DELETE`；如果被移除者正持有 Draft
Lease，后端必须在同一事务内使该 Lease/fencing token 失效。

Owner 转移 body：

```json
{
  "target_user_id": "u-manager",
  "reason": "项目交接"
}
```

当前 Skill Owner 或 Space Administrator 可转移 Owner，接收人必须是 Active Space
Member。Space Administrator 必须填写审计原因，且不会因此获得 Skill 的
编辑、发布或授权权限。Owner 转移会原子使旧 Lease 失效；已有的
`PENDING` Skill 编辑申请也必须改由新 Owner 审批，旧 Owner 不得继续审批。

#### 2.3.3 成员申请 Skill 编辑权限

当 `actor.permissions.request_edit_access=true` 且 `actor.pending_editor_request=null` 时，
调用以下接口：

```http
POST /openapi/v1/bots/spaces/42/skills/1001/editor-requests?user_id=u-applicant
Content-Type: application/json

{
  "reason": "需要共同维护发布说明 Skill"
}
```

`reason` 必填，长度 1～512 字符。目标成功响应：

```json
{
  "code": 201000,
  "message": "Created",
  "request_id": "mock-editor-request-1",
  "data": {
    "work_order_id": 8102,
    "work_order_no": "WO202608240001",
    "status": "PENDING"
  }
}
```

后端为该工单写入：

```text
biz_type = SKILL_COLLABORATOR
biz_id = {skill_id}
applicant_user_id = 当前认证用户
reviewer = 当前 Skill Owner（服务端解析）
status = PENDING
```

前端不能调用 `/openapi/v1/bots/work-orders/events` 并自己指定 Owner/审批人。
服务端在创建前必须确认：这是 Team Space Skill，申请人是当前 Active
Space Member，申请人尚不是 Owner/Manager，且不存在同一申请人+同一 Skill
的 `PENDING` 工单。重复申请返回 `409 ALREADY_PENDING`。

| HTTP / Envelope `code` / 语义 | 含义 | 前端处理 |
| --- | --- | --- |
| `403` / `403201` / `ACCESS_DENIED` | 申请人不是 Active Space Member | 隐藏申请按钮并刷新 Space 身份 |
| `404` / `404201` / `NOT_FOUND` | Space/Skill 不存在或不可见 | 返回列表或展示空态 |
| `409` / `409201` / `ALREADY_PENDING` | 同一 Skill 已有本人待审申请 | 展示“申请中”并查询已有工单 |
| `409` / `409206` / `APPLICANT_ALREADY_EDITOR` | 申请人已是 Owner/Manager | 刷新 Skill 详情，进入可编辑态 |
| `409` / `409204` / `NO_REVIEWER` | Skill 没有可用的当前 Owner | 禁止重复提交，提示联系 Space Administrator |

#### 2.3.4 申请人查进度、Owner 审批

Work Order 接口直接从认证 principal 确定当前用户，不额外接收
Space Skill 接口的 `user_id` query。

申请人查询自己对该 Skill 的申请：

```http
GET /openapi/v1/bots/work-orders?query_type=INITIATED_BY_ME&item_type=APPROVAL&biz_type=SKILL_COLLABORATOR&biz_id=1001&page_no=1&page_size=20
```

Owner 查询待审：

```http
GET /openapi/v1/bots/work-orders?query_type=PENDING_FOR_ME&item_type=APPROVAL&biz_type=SKILL_COLLABORATOR&biz_id=1001&page_no=1&page_size=20
```

打开工单详情：

```http
GET /openapi/v1/bots/work-orders/8102
```

审批或拒绝：

```http
POST /openapi/v1/bots/work-orders/8102/approval
Content-Type: application/json

{
  "decision": "APPROVED",
  "review_remark": "同意共同维护"
}
```

`decision` 只能是 `APPROVED` 或 `REJECTED`；拒绝时 `review_remark` 必填。
目标审批响应：

```json
{
  "code": 200000,
  "message": "OK",
  "request_id": "mock-approval-1",
  "data": {
    "work_order_id": 8102,
    "status": "APPROVED",
    "decision": "APPROVED",
    "reviewer_user_id": "u-owner",
    "review_remark": "同意共同维护",
    "reviewed_at": "2026-08-24T08:30:00Z"
  }
}
```

审批通过的领域结果不是只把工单改成 `APPROVED`。后端必须在一个
事务中锁定工单、重新确认审批人仍是当前 Skill Owner、重新确认申请人
仍是 Active Space Member，幂等写入 `ac_skill_grant(role=MANAGER, status=ACTIVE)`，
再完成 Work Order 并生成结果通知。拒绝只关闭工单，不写 Skill Grant。

审批成功后，前端重新请求 `GET /{skill_id}/grants` 和 Skill 详情，不在
本地直接伪造 Manager 状态。

#### 2.3.5 前端权限矩阵

下表只描述领域授权结果，不规定页面按钮显隐。后端将它投影为
`actor.skill_role + actor.permissions`，前端再结合 Draft/Attempt/Lease 领域事实生成 ViewModel：

| 身份 | 查看 Grants | 编辑/发布 | 添加/移除 Manager | 转移 Owner | 申请编辑 |
| --- | --- | --- | --- | --- | --- |
| Skill Owner | 可 | 可 | 可 | 可 | 不显示 |
| Skill Manager | 可 | 可 | 不可 | 不可 | 不显示 |
| Space Administrator（无 Skill Grant） | 可 | 不可 | 不可 | 可，必须填原因 | 按普通成员规则 |
| Active Space Member（无 Skill Grant） | 可 | 不可 | 不可 | 不可 | Team Space 可 |
| 非 Space Member | 不可 | 不可 | 不可 | 不可 | 不可 |

#### 2.3.6 编辑 Lease

`DraftLease`：

```json
{
  "required": true,
  "holder_user_id": "u-manager",
  "fencing_token": 8
}
```

Personal Space Mock 返回 `required=false`，`holder_user_id`/`fencing_token` 为 `null`。
Team Space 必须 Mock 同一 Draft 被他人占用、主动释放、Owner/Manager takeover 及旧 token
保存返回 409 的场景。Manager 权限被撤销或 Owner 发生转移时，旧 token 也必须
失效。

### 2.4 Published Version 浏览

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| Published Version 列表 | `GET /{skill_id}/versions` | `user_id` | `PublishedVersion[]` |
| 精确 Version 详情 | `GET /{skill_id}/versions/{version}` | `user_id` | `PublishedVersion` |
| Version 文件树 | `GET /{skill_id}/versions/{version}/files` | `user_id` | `FileTreeItem[]` |
| Version 文件内容 | `GET /{skill_id}/versions/{version}/files/{path}` | `user_id` | `FileContent` |

`PublishedVersion`：`version`、`status`、`sc_version_number`、
`publication_attempt_id`、`published_at`。其中 `publication_attempt_id` 可空：
TeamClaw 工坊发布的 Version 指向对应 Attempt；SC Public 懒加载 Version 为 `null`，
前端不得假定每个 Published Version 都有 TeamClaw Publication Attempt。
该页面只读；前端不能提供 Version 删除、覆盖、单独下线入口。

### 2.5 发布与物化

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| 升级影响 | `GET /{skill_id}/upgrade-impact` | `user_id` | `UpgradeImpact` |
| 创建发布 | `POST /{skill_id}/publications` | `CreatePublicationRequest`、`Idempotency-Key`、`user_id` | `PublicationAttempt`（202） |
| 发布历史 | `GET /{skill_id}/publications` | `user_id` | `PublicationAttempt[]` |
| Attempt 详情/轮询 | `GET /{skill_id}/publications/{attempt_id}` | `user_id` | `PublicationAttempt` |
| 同 Version 物化重试 | `POST /{skill_id}/versions/{version}/materialization-retry` | `Idempotency-Key`、`user_id` | `PublicationAttempt`（202） |

`UpgradeImpact`：`affected_bot_count`、`affected_bots`。

发布 body 可为空；若 Team Draft 要求 Lease，则传：

```json
{ "fencing_token": 8 }
```

`PublicationAttempt`：

```text
attempt_id, target_version, status, created_at, error_code
```

前端 Mock 轮询状态流：

```text
PREPARING → SC_SUBMITTING → WAITING_SC → MATERIALIZING → SUCCEEDED
                                            ↘ FAILED / RESULT_UNKNOWN
```

SC Published 只表示外部版本已经形成；前端仍保持 `MATERIALIZING`，直到 TeamClaw
Canonical Store Ready。只有 Version=`PUBLISHED` 且 Attempt=`SUCCEEDED` 才显示发布成功。
随后触发的 Track Latest 是 Best-Effort 异步刷新，不参与发布成功门槛。

- `FAILED`：Mock Draft 回到 `EDITING`，允许重新提交。
- `RESULT_UNKNOWN`：Mock Draft 保持 `FROZEN`，普通用户没有 cancel/retry publish 按钮。
- `MATERIALIZING` 失败：只展示同 Version 的 materialization retry；不能再次创建 publish。

### 2.6 整体退役

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| 退役影响 | `GET /{skill_id}/retirement-impact` | `user_id` | `RetirementImpact` |
| 退役 Skill | `POST /{skill_id}/retirement` | `RetireSkillRequest`、`user_id` | `RetirementImpact`（202） |

`RetirementImpact`：

```json
{
  "can_retire": false,
  "blocking_reasons": ["active_bot_binding", "publication_in_progress"]
}
```

退役 body：

```json
{ "reason": "能力已由新 Skill 替代" }
```

Mock 仅在 `can_retire=true` 时允许受理；有 binding、Service Artifact、Attempt 或物化进行中时
展示阻断原因。没有单 Version retirement/delete API。

## 3. 前端 Mock 数据建议

建议至少维护以下 fixture 组合，以覆盖 PRD 页面能力：

1. **空工坊**：列表为空，可选择本地文件夹 / Git 导入。
2. **首次 Draft**：没有 Published Version，Draft=`EDITING`，Owner 可编辑。
3. **双轨详情**：Published V1 + Draft V2；页头同时展示 `latest_published_version=1` 与
   `draft_target_version=2`。
4. **多人 Team 编辑**：Manager 持有 lease，Owner 看到 takeover；旧 fencing token 保存冲突。
5. **发布中**：Attempt=`WAITING_SC` / `MATERIALIZING`，Draft=`FROZEN`。
6. **发布未知**：Attempt=`RESULT_UNKNOWN`，不显示普通重试或取消。
7. **物化失败**：Version 已存在但未 Published，只有 materialization retry。
8. **退役阻断**：`can_retire=false` 与具体 `blocking_reasons`。
9. **编辑权限申请**：普通 Team Space Member 发起后显示 `PENDING`，Owner 审批通过
   后用户变为 Manager，拒绝后仍无 Skill Grant。
10. **重复与身份变化**：重复申请 `409 ALREADY_PENDING`；Owner 转移后旧 Owner
    不能审批原工单；Manager 被撤权后旧 Lease token 保存返回 409。
11. **领域状态组合**：至少覆盖普通成员可申请、申请中、他人持锁、
    Published V1 + Draft V2、只有 Published 可升级、已退役仅查看。
12. **无复制功能**：任何 fixture 都不包含 Skill 复制/Clone 数据或调用流程。
13. **权限与外部失败**：403、404、409、422、502/503 各一份可恢复的页面 fixture。

## 4. 开发切换规则

开发期由前端 mock 层按本手册的路径、request body、response schema 和错误状态
提供页面能力。PR #1342 的 `501` 占位响应不是联调契约，不应对它编写业务
分支。

当后端正式实现任一路由并生成新的 Gateway OpenAPI 后，前端先比对生成 schema
与本文，确认一致后再移除该路由的 mock override。

## 5. 详细字段字典

以下字段表按接口模型汇总；接口表中写到某模型时，直接复用该模型字段。除特别标注外，
所有 JSON body 均为 `application/json`，所有 Space Skill 接口都有 query `user_id`。
Work Order 列表、详情和审批接口从认证 principal 确定当前用户，不传 `user_id`。

### 5.1 地址参数与公共 Header

| 字段名 | 位置 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- | --- |
| `space_id` | path | integer | 是 | Space 数字主键，最小值 1 |
| `skill_id` | path | string | 是 | 平台 Skill 稳定身份 |
| `manager_user_id` | path | string | Manager 接口是 | 被授予或移除 Manager 的 Space 成员；不是当前调用者 |
| `version` | path | integer | Version 接口是 | 业务版本序号，最小值 1 |
| `attempt_id` | path | string | Attempt 接口是 | Publication Attempt 标识 |
| `path` | path | string | 文件接口是 | 相对 Skill 根目录的路径，可带子目录 |
| `user_id` | query | string | 是 | 当前调用用户；后端将与认证身份核验 |
| `Idempotency-Key` | header | string | 创建/导入/升级/发布/物化重试 | 1～128 字符的重试去重键 |

### 5.2 创建、Draft 与权限请求字段

#### `GitImportRequest` — Git 导入

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `repository_url` | string | 是 | Git 仓库 URL，最长 2048 字符 |
| `branch` | string | 否 | 分支或 ref，最长 256 字符 |
| `subdir` | string | 否 | Skill 在仓库内的相对目录，最长 1024 字符 |

#### `WriteDraftFileRequest` — 文件保存

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `content` | string | 是 | UTF-8 文件完整内容 |
| `fencing_token` | integer | Team Space 条件必填 | 当前 Lease token，最小值 1；Personal Space 可省略 |

#### `RefreshDraftFromGitRequest` — Git 覆盖确认

| 字段名 | 类型 | 必填 | 默认值 | 描述 |
| --- | --- | --- | --- | --- |
| `confirm_overwrite` | boolean | 否 | `false` | 是否确认以原 Git source 替换当前 Draft 文件 |

#### `OwnerTransferRequest` — Owner 转移

| 字段名 | 类型 | 必填 | 默认值 | 描述 |
| --- | --- | --- | --- | --- |
| `target_user_id` | string | 是 | - | 必须是当前 Space Member 的接收人 |
| `reason` | string | 是 | - | 审计原因，1～1000 字符 |

Owner 转移后原 Owner 的 Grant 被撤销，不自动保留为 Manager。

#### `CreateSkillEditorRequest` — 申请 Skill 编辑权限

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `reason` | string | 是 | 申请理由，1～512 字符 |

#### `WorkOrderApprovalRequest` — Work Order 审批

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `decision` | `APPROVED`/`REJECTED` | 是 | 审批决定 |
| `review_remark` | string/null | 拒绝时必填 | 审批意见，最长 512 字符 |

#### `CreatePublicationRequest` — 发布命令

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `fencing_token` | integer | Team Space 条件必填 | 持有 Team Draft Lease 时的 token，最小值 1 |

#### `RetireSkillRequest` — 退役命令

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `reason` | string | 是 | 整体退役原因，1～1000 字符 |

### 5.3 列表与详情响应字段

#### `SpaceSkillSummary` — 工坊列表卡片

| 字段名 | 类型 | 描述 |
| --- | --- | --- |
| `skill_id` | string | 平台 Skill 身份 |
| `skill_uuid` | string | Skill Center / Runtime 稳定代码 |
| `name` | string | 从 `SKILL.md` 解析的名称 |
| `description` | string/null | 从 `SKILL.md` 解析的描述 |
| `space_type` | `PERSONAL`/`TEAM` | 所属 Space 类型 |
| `owner` | `SkillOwnerSummary` | 当前唯一 Owner 摘要 |
| `lifecycle_status` | `DRAFT_ONLY`/`PUBLISHED`/`RETIRED` | Skill 资产可用性状态 |
| `latest_published_version` | integer/null | 最近可消费的 Published 业务版本 |
| `draft` | `DraftSummary`/null | 当前 Draft 摘要，与 Published 可同时存在 |
| `active_publication` | `ActivePublicationSummary`/null | 当前进行中 Attempt 摘要；不包含历史 |
| `actor` | `SpaceSkillActorSummary` | 当前调用者的 Grant、permissions 和 PENDING 编辑权工单 |
| `lease_summary` | `DraftLeaseSummary`/null | Draft 列表锁摘要；不含 fencing token；无 Draft 时为 null |
| `gmt_created` / `gmt_modified` | datetime | 创建/修改时间 |

#### `SpaceSkillDetail` — 详情头部

| 字段名 | 类型 | 描述 |
| --- | --- | --- |
| `skill_id` | string | 平台 Skill 稳定身份 |
| `skill_uuid` | string | SC/Runtime 稳定代码，仅展示，不作为 API path id |
| `name` | string | `SKILL.md` 名称 |
| `description` | string/null | `SKILL.md` 描述 |
| `owner` | `SkillOwnerSummary` | 当前唯一 Owner 摘要 |
| `lifecycle_status` | `DRAFT_ONLY`/`PUBLISHED`/`RETIRED` | Skill 资产可用性状态 |
| `latest_published_version` | integer/null | 最近已发布的不可变 Version |
| `draft` | `DraftSummary`/null | 当前 Draft 摘要 |
| `active_publication` | `ActivePublicationSummary`/null | 当前进行中 Attempt 摘要 |
| `actor` | `SpaceSkillActorSummary` | 当前调用者领域授权摘要 |
| `lease_summary` | `DraftLeaseSummary`/null | Draft 锁摘要；不含 fencing token |

#### `SpaceSkillSummary` 子模型

| 模型 | 字段 | 类型 | 描述 |
| --- | --- | --- | --- |
| `SkillOwnerSummary` | `user_id` | string | 当前 Owner ID |
|  | `display_name` | string | Owner 展示名 |
| `DraftSummary` | `target_version` | integer | 当前 Draft 目标业务版本 |
|  | `status` | `EDITING`/`FROZEN` | Draft 状态 |
| `ActivePublicationSummary` | `attempt_id` | string | 当前进行中 Attempt ID |
|  | `target_version` | integer | 正在发布的 Draft 版本 |
|  | `status` | string | PREPARING/WAITING_SC/MATERIALIZING/RESULT_UNKNOWN 等 |
| `SpaceSkillActorSummary` | `skill_role` | `OWNER`/`MANAGER`/null | 当前调用者 Skill Grant |
|  | `permissions` | `SpaceSkillPermissions` | 基于 ACL/Grant 的领域命令资格；不代表当前状态必然允许 |
|  | `pending_editor_request` | `PendingEditorRequest`/null | 当前调用者的待审编辑权工单 |
| `PendingEditorRequest` | `work_order_id` | integer | 待审工单 ID |
|  | `work_order_no` | string | 可展示工单号 |
|  | `status` | `PENDING` | 列表只投影未完成申请 |
| `DraftLeaseSummary` | `required` | boolean | Personal Space 为 false |
|  | `state` | `NOT_REQUIRED`/`FREE`/`HELD_BY_ME`/`HELD_BY_OTHER` | 当前用户视角的锁状态 |
|  | `holder_user_id` | string/null | 当前 holder |
|  | `holder_display_name` | string/null | 锁图标 Tooltip 展示名 |

`SpaceSkillPermissions` 固定 boolean 字段：

```text
edit_draft
publish_draft
delete_draft
create_upgrade_draft
retire_skill
manage_grants
transfer_owner
request_edit_access
takeover_lease
```

Permissions 只表达调用者的领域资格。例如 `publish_draft=true` 时，若 Draft 已 FROZEN
或 Lease 已被他人持有，发布命令仍会被后端拒绝。页面的显示/禁用逻辑不属于本响应。

#### `SkillReadme` — 公共/已发布 Skill 的展示内容

| 字段名 | 类型 | 描述 |
| --- | --- | --- |
| `content` | string | UTF-8 的完整 `SKILL.md` 展示内容 |

#### `DraftDetail`、文件与 Version

| 模型 | 字段 | 类型 | 描述 |
| --- | --- | --- | --- |
| `DraftDetail` | `target_version` | integer | 正在编辑的业务版本 |
|  | `status` | string | Draft 状态 |
|  | `source_type` | string/null | `FOLDER` 或 `GIT` 等来源投影 |
|  | `repository_url` / `branch` / `subdir` | string/null | Git-backed Draft 原始来源 |
|  | `gmt_modified` | datetime/null | 最近修改时间 |
| `FileTreeItem` | `path` | string | 相对 Skill 根目录路径 |
|  | `is_directory` | boolean | 是否目录 |
|  | `size_bytes` | integer/null | 文件大小；目录为 null |
| `FileContent` | `path` | string | 相对文件路径 |
|  | `content` | string | UTF-8 全量文本 |
| `PublishedVersion` | `version` | integer | 业务版本序号 |
|  | `status` | string | Version 生命周期状态 |
|  | `sc_version_number` | string/null | 精确 SC Version 标识 |
|  | `publication_attempt_id` | string/null | TeamClaw 工坊 Attempt；SC Public 懒加载 Version 为 null |
|  | `published_at` | datetime/null | 发布完成时间 |

### 5.4 治理、发布和退役响应字段

| 模型 | 字段 | 类型 | 描述 |
| --- | --- | --- | --- |
| `SkillGrant` | `user_id` | string | 被授予成员 |
|  | `role` | string | `OWNER` 或 `MANAGER` |
| `SkillGrants` | `owner` | `SkillGrant` | 唯一 Owner |
|  | `managers` | `SkillGrant[]` | Manager 列表 |
| `SkillEditorRequestCreated` | `work_order_id` | integer | 创建的工单标识 |
|  | `work_order_no` | string | 可展示的工单号 |
|  | `status` | `PENDING` | 创建后固定为待审 |
| `WorkOrderReviewResponse` | `work_order_id` | integer | 已审批的工单标识 |
|  | `status` | `APPROVED`/`REJECTED` | 工单终态 |
|  | `decision` | `APPROVED`/`REJECTED` | 本次审批决定 |
|  | `reviewer_user_id` | string | 审批人，必须是审批时的 Skill Owner |
|  | `review_remark` | string/null | 审批意见 |
|  | `reviewed_at` | datetime | UTC 审批时间 |
| `WorkOrderListItem` | `work_order_id` | integer/null | 关联工单标识 |
|  | `work_order_no` | string | 可展示工单号 |
|  | `biz_type` | string | 本链路固定为 `SKILL_COLLABORATOR` |
|  | `biz_id` | string/integer | Skill 的 `skill_id` |
|  | `applicant_user_id` | string/null | 申请人 |
|  | `apply_reason` | string/null | 申请理由 |
|  | `status` | `PENDING`/`APPROVED`/`REJECTED` | 工单状态 |
|  | `can_approve` | boolean | 当前用户是否可审批 |
|  | `gmt_created` / `gmt_modified` | datetime | UTC 创建/更新时间 |
| `DraftLease` | `required` | boolean | Personal Space 为 false |
|  | `holder_user_id` | string/null | 当前 Team Lease 持有人 |
|  | `fencing_token` | integer/null | 当前有效 token |
| `UpgradeImpact` | `affected_bot_count` | integer | 受升级影响的 Bot 数量 |
|  | `affected_bots` | string[] | 受影响 Bot 标识 |
| `PublicationAttempt` | `attempt_id` | string | Attempt 标识，用于轮询 |
|  | `target_version` | integer | 对应业务版本 |
|  | `status` | string | 发布状态机状态 |
|  | `created_at` | datetime/null | 创建时间 |
|  | `error_code` | string/null | 失败时稳定错误码 |
| `RetirementImpact` | `can_retire` | boolean | 是否可整体退役 |
|  | `blocking_reasons` | string[] | 绑定、制品、Attempt 等阻断理由 |

## 6. 可直接使用的页面联调样例

### 6.1 列表页 Mock

```json
{
  "code": 200000,
  "message": "OK",
  "request_id": "mock-list-1",
  "data": {
    "total": 1,
    "items": [
      {
        "skill_id": "1001",
        "skill_uuid": "9d41d2fa-7ef8-4b87-9f56-123456789abc",
        "name": "release-note",
        "description": "生成版本发布说明",
        "lifecycle_status": "DRAFT_ONLY",
        "latest_published_version": null,
        "space_type": "TEAM",
        "owner": {
          "user_id": "u-owner",
          "display_name": "张三"
        },
        "draft": {
          "target_version": 1,
          "status": "EDITING"
        },
        "active_publication": null,
        "actor": {
          "skill_role": "OWNER",
          "permissions": {
            "edit_draft": true,
            "publish_draft": true,
            "delete_draft": true,
            "create_upgrade_draft": true,
            "retire_skill": true,
            "manage_grants": true,
            "transfer_owner": true,
            "request_edit_access": false,
            "takeover_lease": true
          },
          "pending_editor_request": null
        },
        "lease_summary": {
          "required": true,
          "state": "FREE",
          "holder_user_id": null,
          "holder_display_name": null
        },
        "gmt_created": "2026-08-22T10:00:00Z",
        "gmt_modified": "2026-08-22T10:30:00Z"
      }
    ]
  }
}
```

### 6.2 Draft + Published V1/V2 详情 Mock

```json
{
  "code": 200000,
  "message": "OK",
  "request_id": "mock-detail-1",
  "data": {
    "skill_id": "1001",
    "skill_uuid": "9d41d2fa-7ef8-4b87-9f56-123456789abc",
    "name": "release-note",
    "description": "生成版本发布说明",
    "lifecycle_status": "PUBLISHED",
    "latest_published_version": 1,
    "owner": {
      "user_id": "u-owner",
      "display_name": "张三"
    },
    "draft": {
      "target_version": 2,
      "status": "EDITING"
    },
    "active_publication": null,
    "actor": {
      "skill_role": "OWNER",
      "permissions": {
        "edit_draft": true,
        "publish_draft": true,
        "delete_draft": true,
        "create_upgrade_draft": true,
        "retire_skill": true,
        "manage_grants": true,
        "transfer_owner": true,
        "request_edit_access": false,
        "takeover_lease": true
      },
      "pending_editor_request": null
    },
    "lease_summary": {
      "required": true,
      "state": "HELD_BY_ME",
      "holder_user_id": "u-owner",
      "holder_display_name": "张三"
    }
  }
}
```

### 6.3 普通成员“申请编辑/申请中” Mock

可申请：

```json
{
  "skill_role": null,
  "permissions": {
    "edit_draft": false,
    "publish_draft": false,
    "delete_draft": false,
    "create_upgrade_draft": false,
    "retire_skill": false,
    "manage_grants": false,
    "transfer_owner": false,
    "request_edit_access": true,
    "takeover_lease": false
  },
  "pending_editor_request": null
}
```

已申请：

```json
{
  "skill_role": null,
  "permissions": {
    "edit_draft": false,
    "publish_draft": false,
    "delete_draft": false,
    "create_upgrade_draft": false,
    "retire_skill": false,
    "manage_grants": false,
    "transfer_owner": false,
    "request_edit_access": true,
    "takeover_lease": false
  },
  "pending_editor_request": {
    "work_order_id": 8102,
    "work_order_no": "WO202608240001",
    "status": "PENDING"
  }
}
```

### 6.4 他人持锁的领域摘要 Mock

```json
{
  "draft": {
    "target_version": 2,
    "status": "EDITING"
  },
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
  }
}
```

### 6.5 发布已受理 Mock

```json
{
  "code": 202000,
  "message": "Accepted",
  "request_id": "mock-publish-1",
  "data": {
    "attempt_id": "attempt-20260822-001",
    "target_version": 2,
    "status": "WAITING_SC",
    "created_at": "2026-08-22T10:40:00Z",
    "error_code": null
  }
}
```
