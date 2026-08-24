# 前端页面联调文档 —— 能力工坊（Phase 2）详细出入参

> 适用模块：能力工坊 Space Skill 创建、Draft 编辑、协作治理、发布与退役
>
> 文档版本：v1.0；状态：**contract-only**。本手册为能力工坊页面的前端开发、Mock 与测试提供参考；
> 最终机器可读权威始终是 Gateway 生成的 `configs/schemas/bots.openapi.json`。
>
> 除下文明确标为“已实现”的列表接口外，当前后端对本手册中的新增接口统一返回 HTTP
> `501`、标准 Envelope，且响应头带 `x-contract-status: contract-only`。前端应据本手册
> Mock 目标成功/错误态，**不能把当前 501 当成业务失败状态设计的唯一来源**。

## 1. 使用范围与通用约定

本手册对应 PRD 中的能力工坊：创建/导入 Skill、Draft 编辑、权限治理、编辑 Lease、
版本浏览、发布与退役。它补充 [Skill 前端接口 Review Guide](frontend-api.md) 的
第 9～12 节，以真实已生成 OpenAPI 的路径和 schema 名称为准。

### 1.1 基础地址与身份

所有接口前缀为：

```text
/openapi/v1/bots/spaces/{space_id}/skills
```

- `space_id`：正整数 Space 标识。
- `skill_id`：平台内稳定 Skill 标识；不要把 `skill_uuid` 当作 path 参数。
- 所有新增接口都必须带 query `user_id=<当前用户>`。
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

创建类目标成功使用 `201000`/`Created`，异步命令使用 `202000`/`Accepted`。当前
contract-only 后端实际响应为：

```json
{
  "code": 501000,
  "message": "Phase 2 endpoint is contract-only",
  "data": null,
  "request_id": "trace-id"
}
```

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
| Space Skill 列表（已实现） | `GET /openapi/v1/bots/spaces/{space_id}/skills` | `user_id`，可选 `keyword`、`page_no`、`page_size` | `Page<SpaceSkillItem>` | loading、空态、卡片列表、无权限 |
| 本地文件夹创建 | `POST /openapi/v1/bots/spaces/{space_id}/skills` | `files` + `file_paths` multipart；`Idempotency-Key`；`user_id` | `SpaceSkillDetail`（201） | 上传中、校验失败、创建后打开详情 |
| Git 导入 | `POST /openapi/v1/bots/spaces/{space_id}/skills/import-from-git` | `GitImportRequest`；`Idempotency-Key`；`user_id` | `SpaceSkillDetail`（201） | 导入中、内容错误、创建后打开详情 |
| 创作详情 | `GET /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}` | `user_id` | `SpaceSkillDetail` | Draft/Published 双状态、权限按钮 |

#### 2.1.1 获取 Space Skills 列表（已实现，可直接联调）

- **URL**：`GET /openapi/v1/bots/spaces/{space_id}/skills`
- **状态**：已实现，实际成功响应为 HTTP `200`；**不带** `x-contract-status: contract-only`。
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

**成功响应**：`Envelope<Page<SpaceSkillItem>>`

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
        "status": "DRAFT",
        "draft_status": "EDITING",
        "space_type": "TEAM",
        "current_user_skill_role": "OWNER",
        "can_edit": true,
        "can_grant": true,
        "can_apply_edit": false,
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

`SpaceSkillItem` 是列表卡片，至少使用：

```text
skill_id, skill_uuid, name, description, status, draft_status,
space_type, current_user_skill_role, can_edit, can_grant,
can_apply_edit, gmt_created, gmt_modified
```

`SpaceSkillDetail` 是详情头部，必须分开展示以下字段，不能合并为一个泛化 status：

```text
skill_id, skill_uuid, name, description,
latest_published_version, draft_target_version, draft_status,
publication_status, current_user_skill_role
```

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

### 2.3 Owner、Manager 与编辑 Lease

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| 权限列表 | `GET /{skill_id}/grants` | `user_id` | `SkillGrants` |
| 授予 Manager | `PUT /{skill_id}/managers/{manager_user_id}` | `user_id` | `SkillGrant` |
| 移除 Manager | `DELETE /{skill_id}/managers/{manager_user_id}` | `user_id` | `SkillGrant` |
| 转移 Owner | `POST /{skill_id}/owner-transfer` | `OwnerTransferRequest`、`user_id` | `SkillGrants` |
| 查询 Lease | `GET /{skill_id}/draft/lease` | `user_id` | `DraftLease` |
| 获取 Lease | `PUT /{skill_id}/draft/lease` | `user_id` | `DraftLease` |
| 释放 Lease | `DELETE /{skill_id}/draft/lease` | `user_id` | `DraftLease` |
| Takeover Lease | `POST /{skill_id}/draft/lease/takeover` | `user_id` | `DraftLease` |

`SkillGrants`：

```json
{
  "owner": { "user_id": "u-owner", "role": "OWNER" },
  "managers": [{ "user_id": "u-manager", "role": "MANAGER" }]
}
```

Owner 转移 body：

```json
{
  "target_user_id": "u-manager",
  "reason": "项目交接",
  "retain_previous_owner_as_manager": true
}
```

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
保存返回 409 的场景。

### 2.4 Published Version 浏览

| PRD 功能点 | 方法与路径 | 入参 | 目标响应 data |
| --- | --- | --- | --- |
| Published Version 列表 | `GET /{skill_id}/versions` | `user_id` | `PublishedVersion[]` |
| 精确 Version 详情 | `GET /{skill_id}/versions/{version}` | `user_id` | `PublishedVersion` |
| Version 文件树 | `GET /{skill_id}/versions/{version}/files` | `user_id` | `FileTreeItem[]` |
| Version 文件内容 | `GET /{skill_id}/versions/{version}/files/{path}` | `user_id` | `FileContent` |

`PublishedVersion`：`version`、`status`、`sc_version_number`、`published_at`。
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
9. **权限与外部失败**：403、404、409、422、502/503 各一份可恢复的页面 fixture。

## 4. 开发切换规则

开发期可由前端 mock 层按本手册的“目标响应 data”返回成功态；联调环境中如果收到
`x-contract-status: contract-only`，应明确显示“后端能力待实现”，不要将其解析为
`FAILED`、`RESULT_UNKNOWN` 或业务权限错误。

当后端后续实现任一路由时，前端只需保留路径、request body、response schema 与状态模型；
移除该路由的 mock override，开始消费真实 Gateway OpenAPI 即可。

## 5. 详细字段字典

以下字段表按接口模型汇总；接口表中写到某模型时，直接复用该模型字段。除特别标注外，
所有 JSON body 均为 `application/json`，所有接口都有 query `user_id`。

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

### 5.2 创建与 Draft 请求字段

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
| `retain_previous_owner_as_manager` | boolean | 否 | `true` | 原 Owner 是否保留为 Manager |

#### `CreatePublicationRequest` — 发布命令

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `fencing_token` | integer | Team Space 条件必填 | 持有 Team Draft Lease 时的 token，最小值 1 |

#### `RetireSkillRequest` — 退役命令

| 字段名 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| `reason` | string | 是 | 整体退役原因，1～1000 字符 |

### 5.3 列表与详情响应字段

#### `SpaceSkillItem` — 工坊列表卡片

| 字段名 | 类型 | 描述 |
| --- | --- | --- |
| `skill_id` | string | 平台 Skill 身份 |
| `skill_uuid` | string | Skill Center / Runtime 稳定代码 |
| `name` | string | 从 `SKILL.md` 解析的名称 |
| `description` | string/null | 从 `SKILL.md` 解析的描述 |
| `status` | string/null | Skill 生命周期投影 |
| `draft_status` | string/null | Draft 状态投影 |
| `space_type` | `PERSONAL`/`TEAM` | 所属 Space 类型 |
| `current_user_skill_role` | `OWNER`/`MANAGER`/null | 当前用户 Skill Grant |
| `can_edit` | boolean | 是否可编辑 Draft |
| `can_grant` | boolean | 是否可管理 Manager |
| `can_apply_edit` | boolean | 是否可申请编辑资格 |
| `gmt_created` / `gmt_modified` | datetime | 创建/修改时间 |

#### `SpaceSkillDetail` — 详情头部

| 字段名 | 类型 | 描述 |
| --- | --- | --- |
| `skill_id` | string | 平台 Skill 稳定身份 |
| `skill_uuid` | string | SC/Runtime 稳定代码，仅展示，不作为 API path id |
| `name` | string | `SKILL.md` 名称 |
| `description` | string/null | `SKILL.md` 描述 |
| `latest_published_version` | integer/null | 最近已发布的不可变 Version |
| `draft_target_version` | integer/null | 当前正在编辑的业务版本 |
| `draft_status` | string/null | 例如 `EDITING`、`FROZEN` |
| `publication_status` | string/null | 当前或最近 Attempt 状态 |
| `current_user_skill_role` | string/null | `OWNER`、`MANAGER` 或无 Grant |

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
|  | `published_at` | datetime/null | 发布完成时间 |

### 5.4 治理、发布和退役响应字段

| 模型 | 字段 | 类型 | 描述 |
| --- | --- | --- | --- |
| `SkillGrant` | `user_id` | string | 被授予成员 |
|  | `role` | string | `OWNER` 或 `MANAGER` |
| `SkillGrants` | `owner` | `SkillGrant` | 唯一 Owner |
|  | `managers` | `SkillGrant[]` | Manager 列表 |
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

## 6. 可复制的页面联调样例

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
        "status": "DRAFT",
        "draft_status": "EDITING",
        "space_type": "TEAM",
        "current_user_skill_role": "OWNER",
        "can_edit": true,
        "can_grant": true,
        "can_apply_edit": false,
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
    "latest_published_version": 1,
    "draft_target_version": 2,
    "draft_status": "EDITING",
    "publication_status": null,
    "current_user_skill_role": "OWNER"
  }
}
```

### 6.3 发布已受理 Mock

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
