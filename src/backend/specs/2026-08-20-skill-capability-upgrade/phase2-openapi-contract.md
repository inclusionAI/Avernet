# Phase 2 Skill OpenAPI Contract Matrix

> 状态：实现前候选 HTTP 合同，依赖正式领域 Spec PR #1686。
>
> 适用对象：Backend、Gateway、接口 Reviewer、测试与 Ticket 拆分。
>
> 重要边界：本文不会生成或提前发布未实现接口。真实路由实现后，
> `src/gateway/configs/schemas/bots.openapi.json` 才是机器可读权威。

## 1. 文档职责与权威顺序

本文把正式 Spec 中已经定稿的领域语义翻译为逐 Operation 的 HTTP 合同。它负责冻结路径、
Principal、输入、输出、状态码、幂等和稳定业务错误，不负责描述数据库表或私有函数。

实现前的权威顺序：

1. 正式领域 Spec：状态机、领域不变量、Runtime/Artifact 约束；
2. 本文：候选 HTTP wire；
3. `frontend-api.md`：产品如何组合调用这些 wire。

实现后的权威顺序：

1. Backend `openapi_v1` Router 与 Pydantic DTO；
2. 自动生成并通过兼容 Gate 的 `bots.openapi.json`；
3. 本文保留设计理由、兼容要求和 Operation ID。

禁止手工把本文转换成生产 OpenAPI JSON；禁止用 `501`、stub 或未注册 Router 冒充完成。

## 2. 通用合同

### 2.1 Prefix、身份与标识符

- 所有路径使用 `/openapi/v1/bots` 命名空间。
- `skill_id`：十进制字符串形式的 `ac_skill.id`，是 TeamClaw 公开 API 身份。
- `skill_uuid`：Center Store、SkillCenter 发布和 Runtime 使用的内部稳定 UUID。
- `{version}`：业务版本序号，例如 `1`、`2`，不是数据库主键，也不是 SC version number。
- `sc_version_number`：SkillCenter 的精确版本号字符串，例如 `1.0.0`。
- `user_id`：当前调用者；从认证上下文验证。客户端 query 只承担已发布兼容 wire，不是授权事实。
- Space Skill 创作命令要求明确 User Principal；纯 App/Bot Principal 不允许写 Draft、Grant 或 Publication。

| Operation 范围 | Principal 与授权 |
| --- | --- |
| SC/Repo 市场读取 | 已认证 `UserOrDelegatedApp` |
| Space/Skill 列表与只读详情 | `UserOrDelegatedApp`；必须解析明确 actor 并通过 Space ACL |
| Space Skill 创建、Draft、Grant、Lease、Publication、Offline | 明确 User；按 Space ACL + Owner/Manager/Member 规则校验 |
| SC Public Reference | `UserOrDelegatedApp`；受理与最终写入都校验 Bot ACL，最终阶段再校验 actor、Owner、SkillSet 最新状态 |

P2-OFF-001/002 仅 Owner/Manager 可调用。P2-REF-002/003 即使目标 SkillSet 后来被删除，仍按
Bot ACL 与 Operation 中冻结的 `skill_set_id` 查询历史，不要求当前 Set 存在。

### 2.2 Envelope 与分页

所有成功和失败均使用 Gateway Envelope：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {},
  "request_id": "b0a6d2f4e8c94b1a9f3d5e7c60218a4d"
}
```

分页统一使用：

```json
{
  "total": 1,
  "items": []
}
```

- `page` 或既有接口的 `page_no` 从 1 开始；新 Phase 2 接口统一使用 `page`。
- `page_size` 默认 20，最小 1，最大 100。
- 时间统一为 UTC RFC 3339，例如 `2026-08-30T08:00:00Z`。

### 2.3 幂等

| 类型 | 规则 |
| --- | --- |
| 创建 Skill、Git 导入、创建升级 Draft、Publication、SC Public Reference | 必须传 `Idempotency-Key` |
| Membership PUT/DELETE、Manager PUT/DELETE、Lease acquire/release、Offline | 领域幂等 |
| Publication Retry | 使用 `attempt_id`，不要求新 Key |
| Reference Operation 失败重试 | 前端重新 POST，并使用新的 Key；本期不提供原 Operation retry |

同一 `Idempotency-Key` 重放必须返回原资源；Key 被用于不同 Space、不同 Skill 或不同请求内容时
返回 `409 IDEMPOTENCY_KEY_REUSED`。

### 2.4 错误类别

| HTTP | 用途 | 前端默认处理 |
| --- | --- | --- |
| 400 | wire 或 query 格式错误 | 修正请求，不自动重试 |
| 401 | 未认证 | 重新登录或刷新凭据 |
| 403 | ACL、Owner/Manager、Space Member 或 MCP 权限失败 | 禁止操作并刷新权限 |
| 404 | Space、Skill、Draft、Version、Attempt、Reference 不存在或不可见 | 返回列表或空态 |
| 409 | 状态机、Lease、幂等、Offline blocker、Membership 冲突 | 使用 Envelope `code` 显示业务提示并刷新详情 |
| 422 | SKILL.md、文件、Git、路径或参数校验失败 | 展示字段/文件级错误 |
| 502 | SC/Runtime 上游调用明确失败 | 可按接口 recovery 语义处理 |
| 503 | SC、OSS、Task enqueue 或 Runtime 暂时不可用 | 保留页面状态，允许安全重试 |

普通错误响应的 `data` 保持 `null`。P2-OFF-002 的 `SKILL_OFFLINE_BLOCKED` 是明确例外：
409 的 `data` 返回服务端在命令事务内重新计算的最新 `OfflineImpact`，供产品刷新 blocker。
Envelope 顶层 `code` 是稳定六位业务错误码；本文的
大写符号名用于评审和代码枚举命名，不是额外的顶层 JSON 字段。需要结构化 blocker、validation
或 recovery 信息的接口，通过成功的 impact/detail DTO 返回。Publication Attempt 和 Reference
Item 自己的 `error_code` 是持久资源字段，和 Envelope `code` 不混用。

## 3. Operation 总表

状态含义：`DONE` 已符合目标合同；`ADJUST` 已有 Router 但 wire/语义需调整；`NEW` 尚未实现；
`FROZEN` 是 Phase 1 或其他 Owner 的既有合同，本期只调用。

### 3.1 市场与工坊目录

| ID | Method | Path | 产品用途 | 成功 | 幂等 | dev 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P2-MKT-001 | POST | `/openapi/v1/bots/market/skill-center/skills` | 查询 SC Public 市场 | 200 | 读取 | DONE |
| P2-MKT-002 | GET | `/openapi/v1/bots/market/skill-center/tags` | 初始化 SC 标签筛选 | 200 | 读取 | DONE |
| P2-MKT-003 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/consumable` | 查询当前 Space 可引用工坊 Skill | 200 | 读取 | NEW |
| P2-MKT-004 | POST | `/openapi/v1/bots/market/skill-center/sync` | 手动同步已懒物化 SC Public Skill | 200 | 服务端互斥 | NEW |

TeamClaw Repo 市场继续使用 Phase 1 `GET /openapi/v1/bots/skills/repository`，普通工坊 Skill
加入 SkillSet 继续使用 Phase 1 Membership PUT；两者在本文标记为 `FROZEN`，不重新定义 wire。

### 3.2 Space Skill、Draft 与 Version

| ID | Method | Path | 产品用途 | 成功 | 幂等 | dev 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P2-SKL-001 | GET | `/openapi/v1/bots/spaces/{space_id}/skills` | 能力工坊卡片列表 | 200 | 读取 | ADJUST |
| P2-SKL-002 | POST | `/openapi/v1/bots/spaces/{space_id}/skills` | 上传本地文件夹创建 V1 Draft | 201 | Header Key | NEW |
| P2-SKL-003 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/import-from-git` | Git Snapshot 创建 V1 Draft | 201 | Header Key | NEW |
| P2-SKL-004 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}` | 创作详情 | 200 | 读取 | NEW |
| P2-DRF-001 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/upgrade` | 创建 Vn+1 Draft | 201 | Header Key | NEW |
| P2-DRF-002 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files` | Draft 文件树 | 200 | 读取 | NEW |
| P2-DRF-003 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path}` | 读取 Draft 文件 | 200 | 读取 | NEW |
| P2-DRF-004 | PUT | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path}` | 保存单个 Draft 文件 | 200 | Revision CAS | NEW |
| P2-DRF-005 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/refresh-from-git` | 从冻结 Git 来源刷新 | 200 | Revision CAS | NEW |
| P2-DRF-006 | DELETE | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft` | 放弃 Draft 或删除首次未发布 Skill | 200 | Revision CAS | NEW |
| P2-VER-001 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions` | Published Version 列表 | 200 | 读取 | NEW |
| P2-VER-002 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}` | 精确版本详情 | 200 | 读取 | NEW |
| P2-VER-003 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files` | 精确版本文件树 | 200 | 读取 | NEW |
| P2-VER-004 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path}` | 精确版本文件 | 200 | 读取 | NEW |

没有独立 `GET .../draft`；P2-SKL-004 已返回完整 Draft。没有公开 `draft/replace`，产品没有
整包覆盖入口。Published Version 不允许 PUT/DELETE。

### 3.3 Grant、编辑申请与 Lease

| ID | Method | Path | 产品用途 | 成功 | 幂等 | dev 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P2-GRT-001 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants` | Owner/Manager 列表 | 200 | 读取 | ADJUST |
| P2-GRT-002 | PUT | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/managers/{user_id}` | 添加 Manager | 200 | 是 | ADJUST |
| P2-GRT-003 | DELETE | 同上 | 移除 Manager | 200 | 是 | ADJUST |
| P2-GRT-004 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/owner-transfer` | 转移唯一 Owner | 200 | 结果幂等 | ADJUST |
| P2-GRT-005 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/editor-requests` | 普通成员申请编辑权 | 201 | PENDING 唯一 | ADJUST |
| P2-LSE-001 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/lease` | 获取实时锁状态 | 200 | 读取 | ADJUST |
| P2-LSE-002 | PUT | 同上 | 获取永久编辑锁 | 200 | holder 重放 | ADJUST |
| P2-LSE-003 | DELETE | 同上，query `fencing_token` | 主动释放 | 200 | 是 | ADJUST |
| P2-LSE-004 | POST | `.../draft/lease/takeover` | Owner/Manager 抢锁 | 200 | 结果幂等 | ADJUST |

Work Order 的列表、详情和审批复用既有 `/openapi/v1/bots/work-orders` 合同，不在 Phase 2
复制审批 Router。

### 3.4 Publication 与 Offline

| ID | Method | Path | 产品用途 | 成功 | 幂等 | dev 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P2-PUB-001 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publication-impact` | 发布确认前影响提示 | 200 | 读取 | NEW |
| P2-PUB-002 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/publications` | 冻结 Draft 并发起发布 | 202 | Header Key | NEW |
| P2-PUB-003 | GET | 同上 | Attempt 历史 | 200 | 读取 | NEW |
| P2-PUB-004 | GET | `.../publications/{attempt_id}` | 轮询/恢复 Attempt | 200 | 读取 | NEW |
| P2-PUB-005 | POST | `.../publications/{attempt_id}/retry` | 恢复同一 Attempt | 202/200 | attempt_id | NEW |
| P2-OFF-001 | GET | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline-impact` | 下线前阻断检查 | 200 | 读取 | NEW |
| P2-OFF-002 | POST | `/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline` | 本地 Offline 并创建 Vn+1 Draft | 200 | 是 | NEW |

### 3.5 SC Public 异步 Reference

| ID | Method | Path | 产品用途 | 成功 | 幂等 | dev 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P2-REF-001 | POST | `/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references` | 批量引用最多 20 个 SC Skill | 202 | Header Key | NEW |
| P2-REF-002 | GET | 同上 | 恢复批次/项目进度 | 200 | 读取 | NEW |
| P2-REF-003 | GET | `.../skill-center-references/{reference_id}` | 单项详情 | 200 | 读取 | NEW |

## 4. 共享 DTO 字典

### 4.1 SpaceSkillSummary

```json
{
  "skill_id": "1123211",
  "skill_uuid": "0d10a2e5-9de0-46c4-bd25-d29c5619088a",
  "name": "risk-calculator",
  "description": "计算风险指标",
  "space_type": "TEAM",
  "lifecycle_status": "PUBLISHED",
  "owner": {"user_id": "168944", "display_name": "卷瓜"},
  "latest_published_version": {
    "version": 1,
    "sc_version_number": "1.0.0",
    "published_at": "2026-08-30T08:00:00Z"
  },
  "draft": {"target_version": 2, "status": "EDITING", "revision_id": "rev-2"},
  "active_publication": null,
  "actor": {
    "skill_role": "OWNER",
    "permissions": {
      "edit_draft": true,
      "publish_draft": true,
      "delete_draft": true,
      "create_upgrade_draft": true,
      "offline_skill": true,
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
  "gmt_created": "2026-08-29T08:00:00Z",
  "gmt_modified": "2026-08-30T08:00:00Z"
}
```

字段规则：

- `lifecycle_status`：`DRAFT_ONLY | PUBLISHED | OFFLINE`。
- `latest_published_version`：从未发布时为 `null`；Offline 仍保留最后 Published Version。
- `draft`：无当前 Draft 时为 `null`；Published V1 与 Draft V2 可以同时存在。
- `active_publication`：仅当前非终态 Attempt；历史不内联。
- `permissions` 只表示 ACL/Grant 资格，不保证当前状态下一定成功；命令仍可能返回 409。
- `lease_summary` 无 Draft 时为 `null`；不返回 fencing token。

### 4.2 SpaceSkillDetail

Detail 包含 `SpaceSkillSummary` 的全部字段，并展开：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft` | `DraftDetail|null` | 当前 Draft 的完整来源、Revision 与 metadata |
| `latest_published_version` | `SkillVersionSummary|null` | 最新 Ready Version |
| `active_publication` | `PublicationAttemptSummary|null` | 当前发布进度 |
| `source` | `FOLDER|GIT` | Identity 首次创建来源 |
| `offline_at/offline_by` | `string|null` | TeamClaw 本地下线事实 |

`DraftDetail`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `target_version` | integer | 下一业务版本 |
| `status` | `EDITING|FROZEN` | 内容是否可改 |
| `revision_id` | string | 当前 immutable Revision，用于 CAS |
| `name/description` | string / nullable string | 当前 Draft SKILL.md metadata |
| `source_kind` | `FOLDER|GIT|PUBLISHED_VERSION` | 本次 Draft 内容来源 |
| `source_repo_url` | string|null | Git 来源；非 Git 为 null |
| `source_branch` | string|null | 实际解析分支 |
| `source_commit_sha` | string|null | 已冻结 commit |
| `source_subdir` | string|null | 选中的 Skill 子目录 |

### 4.3 文件树与文件内容

文件树返回当前 Revision 与扁平文件清单，前端按 `/` 自行构造目录树：

```json
{
  "revision_id": "rev-2",
  "files": [
    {"path": "SKILL.md", "size": 256},
    {"path": "references/example.md", "size": 128}
  ]
}
```

- `path` 使用规范化 POSIX 相对路径，不以 `/` 开头，不允许 `..`。
- 单文件 GET 返回 `{path, content, revision_id}`；`content` 是 UTF-8 文本。
- 当前产品编辑器不定义二进制文件预览/原地编辑 wire；二进制资产仍保留在 immutable package。

保存文件请求：

```json
{
  "content": "# Updated content",
  "expected_revision_id": "rev-2",
  "fencing_token": 7
}
```

Personal Space 的 `fencing_token` 为 `null`；Team Space 必须传当前 holder token。成功返回新的
`DraftDetail`，前端必须替换本地 `revision_id`。

### 4.4 Version

```json
{
  "version": 2,
  "sc_version_number": "2.0.0",
  "name": "risk-calculator",
  "description": "新版风险指标",
  "mcp_dependencies": ["mcp.a"],
  "published_at": "2026-08-30T08:00:00Z"
}
```

只返回 `PUBLISHED` Version；`MATERIALIZING` 由 Publication Attempt 表达，不进入消费列表。

### 4.5 PublicationAttempt

```json
{
  "attempt_id": "pa-01",
  "target_version": 2,
  "status": "MATERIALIZING",
  "sc_version_number": "2.0.0",
  "recovery": {"state": "AUTO_RETRYING", "kind": "MATERIALIZATION"},
  "error_code": null,
  "error_message": null,
  "gmt_created": "2026-08-30T08:00:00Z",
  "gmt_modified": "2026-08-30T08:01:00Z"
}
```

状态：`PREPARING | SC_SUBMITTING | WAITING_SC | MATERIALIZING | SUCCEEDED | FAILED |
RESULT_UNKNOWN`。

服务端持久 Attempt 同时保存创建事务内冻结的 `frozen_draft_locator`，但该内部 immutable
Revision locator 不进入公共 `PublicationAttempt` DTO。Worker 只消费该 snapshot；FAILED 后
只有当前 Draft locator 已变化才允许用新 Key 创建 Attempt。`package_url` 只是 active Attempt
调用 SC 的临时 signed URL，终态清理且不承担 Revision marker 语义。

恢复：

- `recovery.state`：`AUTO_RETRYING | AVAILABLE | NOT_AVAILABLE`。
- `recovery.kind`：`PREPARATION | SC_STATUS_CHECK | MATERIALIZATION | null`。
- 只有 `AVAILABLE` 展示“重试发布”；统一调用 P2-PUB-005。

### 4.6 SC Public Reference

```json
{
  "reference_id": "ref-01",
  "request_id": "req-01",
  "skill_set_id": "1115804",
  "skill_code": "my-skill",
  "sc_version_number": "1.0.0",
  "status": "MATERIALIZING",
  "skill_id": null,
  "error_code": null,
  "error_message": null,
  "gmt_created": "2026-08-30T08:00:00Z",
  "gmt_modified": "2026-08-30T08:00:30Z"
}
```

状态：`QUEUED | RESOLVING_VERSION | MATERIALIZING | ADDING_TO_SKILL_SET |
PROJECTING_RUNTIME | COMPLETED | FAILED`。终态永久保留，本期无 cancel/delete/retry。

## 5. 逐 Operation 输入输出

### 5.1 市场与 Consumable

#### P2-MKT-001 搜索 SC Public Skill

Request body 沿用已实现 camelCase 兼容合同：

```json
{
  "keyword": "risk",
  "pageNum": 1,
  "pageSize": 20,
  "isOfficial": null,
  "isRecommended": null,
  "tagList": [],
  "sortBy": "latest",
  "creatorName": null,
  "creatorWorkNo": null,
  "belongTo": null
}
```

结果 Item 至少包含 `skillId/skillCode/skillName/description/accessLevel/homepageUrl/
latestVersionNumber/tagList`；未知上游展示字段 additive 保留。`homepageUrl` 用于 iframe 详情，
查询和查看详情均不创建 TeamClaw Asset。

#### P2-MKT-003 查询工坊可消费 Skill

Query：`keyword?、page=1、page_size=20`。仅返回当前 Space 中 `PUBLISHED`、Canonical Store
Ready 且非 Offline 的 Skill。响应为 `Page[ConsumableSpaceSkill]`：

```json
{
  "skill_id": "1123211",
  "name": "risk-calculator",
  "description": "计算风险指标",
  "latest_published_version": {
    "version": 2,
    "sc_version_number": "2.0.0",
    "published_at": "2026-08-30T08:00:00Z"
  }
}
```

该目录不混入 Bot Membership；前端从目标 SkillSet 的成员列表判断是否已经添加。

#### P2-MKT-004 手动同步已物化 SC Public Skill

无 body。同步等待本轮发现和 exact materialization 结束，返回：

```json
{
  "scanned": 12,
  "updated": 2,
  "unchanged": 9,
  "failed": 1,
  "failures": [{"skill_id": "123", "skill_code": "x", "error_code": "SC_MARKET_UNAVAILABLE"}]
}
```

单项失败保留旧 Published Version并继续。并发同步返回 `409 SYNC_IN_PROGRESS`；该接口同步返回，
不提供 sync-status 轮询。

### 5.2 创建、详情与 Draft

#### P2-SKL-002 本地文件夹创建

`Content-Type: multipart/form-data`，Header `Idempotency-Key` 必填：

| Part | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `files` | repeated binary | 是 | 文件夹内全部文件 |
| `file_paths` | JSON string array | 是 | 与 files 一一对应的相对路径 |

成功 201 返回 `SpaceSkillDetail`，其 `lifecycle_status=DRAFT_ONLY`、`draft.target_version=1`、
`draft.status=EDITING`。创建不调用 SC、不加入 SkillSet、不写 Installation、不推 Runtime。

#### P2-SKL-003 Git 导入创建

```json
{
  "git_url": "https://example.com/team/skills.git",
  "branch": "main",
  "subdir": null
}
```

`branch/subdir` 可省略。出现多个 SKILL.md 时根目录优先，否则选择规范化父目录字节序第一项；
响应返回实际冻结的 branch、commit SHA 与 source_subdir。

#### P2-DRF-001 创建升级 Draft

无 body，Header `Idempotency-Key` 必填。后端从 latest Published exact Version 创建 Vn+1；成功
201 返回完整 `DraftDetail`。已存在 Draft 时，相同 Key 返回原 Draft，不同 Key 返回
`409 DRAFT_ALREADY_EXISTS`。

#### P2-DRF-005 Git Refresh

```json
{
  "expected_revision_id": "rev-2",
  "fencing_token": 7
}
```

只允许 `source_kind=GIT`，使用持久化 repo/branch/subdir；成功后返回新 DraftDetail。克隆、校验或
CAS 失败时当前 Draft 不变。

#### P2-DRF-006 删除 Draft

Query：`expected_revision_id` 必填，Team 再传 `fencing_token`。响应：

```json
{"changed": true, "deleted_scope": "DRAFT"}
```

`deleted_scope=SKILL` 表示首次未发布且无任何外部事实，整个 Skill 聚合被删除；否则仅删除 Draft。
FROZEN 返回 `409 DRAFT_FROZEN`。

### 5.3 Grant 与 Lease

现有 DTO 保持：

- Grant Item：`{user_id, role=OWNER|MANAGER}`。
- Owner Transfer body：`new_owner_user_id`、`reason?`、`retain_previous_owner_as_manager=false`。
- Editor Request body：`{reason}`，返回 `{work_order_id,work_order_no,status=PENDING}`。
- Lease：`{required,state,holder_user_id,fencing_token}`；只有当前 holder 获得 token。

最终列表/详情的 permission 字段使用 `offline_skill`。当前 dev DTO 中旧 `retire_skill` 属于
P2-SKL-001 的 ADJUST 内容，不作为最终前端合同。

### 5.4 Publication

#### P2-PUB-001 发布影响

Query：`page=1&page_size=20`。返回可能受 Track Latest 影响的当前 Bot：

```json
{
  "total": 1,
  "items": [{"owner_id": "168944", "bot_id": "bot-a", "bot_name": "分析 Bot"}]
}
```

它是提示，不签发 ack token；列表为空也必须由用户点击发布后调用 P2-PUB-002。

#### P2-PUB-002 创建 Publication

无 body，Header `Idempotency-Key` 必填。Team 不传 fencing token：服务端读取最新 Revision，
若 Lease 为 `HELD_BY_OTHER` 则拒绝；FREE/HELD_BY_ME 均可冻结。返回 202 PublicationAttempt。
业务事务提交后 Task enqueue 失败时返回 503，但 Attempt 不丢失；前端以同一个 Key 重放，Backend
必须返回原 Attempt 并重新确保 live Task，不能创建新 Attempt/Version，也不能再次调用 SC publish。

#### P2-PUB-005 恢复 Attempt

无 body。`AVAILABLE` 时确保同一任务继续：

- PREPARATION：继续首次准备/提交；
- SC_STATUS_CHECK：只查 SC，禁止重新 publish；
- MATERIALIZATION：只物化同一 Version。

任务被确保时返回 202；Attempt 已 SUCCEEDED 时幂等返回 200；FAILED 且 Draft 已恢复 EDITING
返回 `409 PUBLICATION_REQUIRES_NEW_ATTEMPT`。

### 5.5 Offline

#### P2-OFF-001 影响检查

Query：`page=1&page_size=20`。响应：

```json
{
  "blocked": true,
  "total": 1,
  "counts": {"MEMBERSHIP": 1, "INSTALLATION": 0, "SERVICE_ARTIFACT": 0},
  "items": [{"kind": "MEMBERSHIP", "resource_id": "1115804", "display_name": "基础能力集"}]
}
```

Blocker kind：`DRAFT | PUBLICATION | MEMBERSHIP | INSTALLATION | SERVICE_ARTIFACT |
UNKNOWN_ARTIFACT`。UNKNOWN 必须 fail closed。

#### P2-OFF-002 执行 Offline

无 body。后端锁 Skill 并重查 blocker；成功返回：

```json
{
  "changed": true,
  "lifecycle_status": "OFFLINE",
  "draft": {"target_version": 3, "status": "EDITING", "revision_id": "rev-3"}
}
```

已有 Offline+Draft 时 `changed=false`。没有 force/管理员绕过；不调用 SC 下线。新 Version 发布成功
后自动恢复 PUBLISHED。

命令事务内重查发现 blocker 时返回 HTTP 409、`code=409313`，且 `data` 使用与 P2-OFF-001
相同的最新 `OfflineImpact`；前端不得继续使用调用 POST 前缓存的 counts。

### 5.6 SC Public Reference

#### P2-REF-001 创建批量引用

Header `Idempotency-Key` 必填。响应同时有两个不同层级的 `request_id`：Envelope 顶层
`request_id` 是调用 trace；`data.request_id` 是持久 Reference 批次身份，前端必须从 data 读取：

```json
{"skill_codes": ["public-a", "public-b"]}
```

去重后 1..20 项。返回 202：

```json
{
  "request_id": "req-01",
  "reference_ids": ["ref-a", "ref-b"]
}
```

相同 Key 返回同一 request/reference_ids 并重新确保 live Task。前端收到 202 后用 request_id
调用 P2-REF-002 获取卡片详情。物化完成前不创建 Membership；成功项在最终批量 add 时进入
SkillSet，失败项不影响其他项。

`skill_code` 是唯一外部选择键。相同 code 已物化时按 `center://<skill_code>` 复用同一个 Center
Asset；不得按 name 与 Local/Repo/其他 Space Skill 复用。搜索异常记录缺少 skill_code 时拒绝受理。

#### P2-REF-002 Collection

Query：`request_id?、status?、page=1、page_size=20`；这里的 request_id 是批次身份。默认
`gmt_created DESC,
reference_id DESC`。目标 Set 已被删除时，历史 Operation 仍可按 Bot ACL 查询。

#### P2-REF-003 Detail

返回完整 Reference DTO。前端以 `COMPLETED/FAILED` 为终态；FAILED 项需要用户重新选择/提交，
本期没有原地 retry。

## 6. 稳定业务错误字典

| 符号名 | Envelope `code` | HTTP | 主要 Operation | 含义 |
| --- | --- | --- | --- | --- |
| `IDEMPOTENCY_KEY_REUSED` | `409305` | 409 | 创建、升级、发布、Reference | 同一 Key 被用于不同请求 |
| `SKILL_NAME_CHANGED` | `422203` | 422 | Draft save/refresh/publish | SKILL.md name 与 Identity 不一致 |
| `SKILL_PACKAGE_INVALID` | `422202` | 422 | 创建、refresh、materialize | 包结构或 SKILL.md 非法 |
| `SKILL_MANIFEST_MISSING` | `422205` | 422 | folder/Git 创建 | 缺少目标 SKILL.md |
| `SKILL_MANIFEST_MULTIPLE` | `422206` | 422 | folder 创建 | 上传包包含多个候选 SKILL.md |
| `SKILL_PATH_INVALID` | `422207` | 422 | folder/Draft file | 相对路径非法或越界 |
| `DRAFT_SOURCE_NOT_REFRESHABLE` | `422208` | 422 | Git refresh | 当前 Draft 不是 Git snapshot 来源 |
| `DRAFT_NOT_FOUND` | `404204` | 404 | Draft | 没有当前 Draft |
| `DRAFT_ALREADY_EXISTS` | `409306` | 409 | upgrade | 已存在 Draft |
| `DRAFT_FROZEN` | `409307` | 409 | save/delete/refresh | 发布中的 Draft 不可修改 |
| `DRAFT_REVISION_CONFLICT` | `409308` | 409 | save/delete/refresh | expected revision 已过期 |
| `LEASE_HELD_BY_OTHER` | `409303` | 409 | save/publish | 复用既有 Draft Lease conflict |
| `LEASE_FENCING_TOKEN_STALE` | `409304` | 409 | save/release | 复用既有 token rejected |
| `PUBLICATION_IN_PROGRESS` | `409309` | 409 | publish | 已有非终态 Attempt |
| `PUBLICATION_RESULT_UNKNOWN` | `409310` | 409 | publish | SC 结果未确认，禁止重发 |
| `PUBLICATION_RECOVERY_NOT_AVAILABLE` | `409311` | 409 | retry | 当前状态不允许恢复 |
| `PUBLICATION_REQUIRES_NEW_ATTEMPT` | `409315` | 409 | retry | FAILED Draft 修改后必须新建 Attempt |
| `SC_MARKET_UNAVAILABLE` | `502000` | 502 | SC market/sync 请求级失败 | 沿用已发布 SC market 上游失败合同 |
| `SKILL_SET_NOT_FOUND` | `404206` | 404 | Reference POST | 受理时目标 Set 不存在 |
| `REFERENCE_BATCH_TOO_LARGE` | `422204` | 422 | Reference | 去重后超过 20 |
| `SKILL_OFFLINE` | `409312` | 409 | 新增引用/Membership | Skill 当前 Offline |
| `SKILL_OFFLINE_BLOCKED` | `409313` | 409 | Offline | 最新血缘检查存在 blocker |
| `SYNC_IN_PROGRESS` | `409314` | 409 | SC sync | 已有同步运行 |

异步资源内部失败不再改变已经返回的 HTTP 202，而由 P2-PUB-004/P2-REF-002/003 的资源字段表达：

| `data.error_code` | 资源 | 含义 |
| --- | --- | --- |
| `SC_SKILL_NOT_FOUND` | Reference | 外部 code 不存在或不可消费 |
| `SC_MARKET_UNAVAILABLE` | Reference/Publication | Worker 调用 SC 暂时失败且重试耗尽 |
| `SKILL_SET_NOT_FOUND` | Reference | 受理后目标 Set 被删除 |
| `RUNTIME_PROJECTION_FAILED` | Reference | active Set 最终 Runtime 投影失败并已补偿 |
| `SC_PUBLISH_REJECTED` | Publication | SC 明确拒绝，本次 Attempt FAILED |
| `MATERIALIZATION_FAILED` | Publication/Reference | exact Version 物化重试耗尽 |

实现时可增加更细的 additive error code，但不能把上表中的不同状态压成一个通用 500。

## 7. 发布门禁

每个 NEW/ADJUST Operation 完成时必须同时具备：

1. 显式 `openapi_v1` Router 与 Pydantic DTO；
2. `PublicAPIRoute`；
3. Authorization 与 Admission inventory；
4. Service/Repository/Task 的真实实现，不返回 stub；
5. Router contract、domain、幂等和错误测试；
6. `dump_openapi.py` 生成候选；
7. `gate_and_publish_openapi.py` 向前兼容检查；
8. 随实现提交更新后的 `bots.openapi.json`。

本文本身不满足上述门禁，也不会改变 Gateway 已发布接口。
