# 前端 Handoff：Skill 终态下线与 Version Copy

> 适用基线：`REL20260901`，已合入 #1825。
>
> 本文只说明本次前端需要适配的 **终态下线（Offline）** 和 **从历史 Version 复制新 Skill**。精确字段、必填项和 HTTP 状态以部署环境 Gateway 的 `/openapi.json` 为准。

## 1. 产品语义变更

旧交互是“下线 Vn 后，原 Skill 自动产生 Vn+1 Draft，未来还能在原 Skill 上重新发布”。

现在改为：

```text
Published Skill
  └─ 下线成功
       └─ 原 Skill = OFFLINE（保留所有已发布历史 Version）
            ├─ 不创建 Vn+1 Draft
            ├─ 不可编辑 / 升级 / 发布 / 再次消费
            └─ 可从任一 Published Vn 复制
                 └─ 新 skill_id + 新 skill_uuid + 独立 V1 Draft
                      └─ 后续发布时创建独立的 Skill Center Skill
```

前端不要把 Offline 理解为“退回草稿”或“暂时不可用”。它是 TeamClaw 本地的终态：不会调用 Skill Center 下线，Skill Center 外部页面可能仍可见。

## 2. 列表与详情的状态渲染

使用 `lifecycle_status` 作为页面主状态，不能由是否存在 Draft 推断。

| Detail/List 事实 | 产品展示和可用动作 |
| --- | --- |
| `PUBLISHED`，`draft=null` | 已发布；可进入升级、下线检查。 |
| `PUBLISHED`，`draft.status=EDITING` | 已发布 · 有待发布修改；继续编辑/发布/删除本次 Draft。 |
| `PUBLISHED`，`draft.status=FROZEN` 或有 `active_publication` | 发布中/物化中；按 Publication 状态展示并轮询。 |
| `OFFLINE`，`draft=null` | 已下线；仅展示历史 Version、详情和“复制为新 Skill”。不要展示编辑、升级、发布、删除 Draft、Lease 获取或 Lease 轮询。 |

Offline Detail 的关键形态：

```json
{
  "skill_id": "1123989",
  "skill_uuid": "old-skill-uuid",
  "lifecycle_status": "OFFLINE",
  "latest_published_version": {"version": 2, "sc_version_number": "2.0.0"},
  "draft": null,
  "active_publication": null,
  "lease_summary": null,
  "offline_at": "2026-09-02T12:00:00Z",
  "offline_by": "168944",
  "actor": {
    "permissions": {"copy_offline_skill": true}
  }
}
```

`actor.permissions.copy_offline_skill` 只表示当前调用者具备 Owner/Manager ACL 资格；仍必须同时判断 `lifecycle_status=OFFLINE` 和用户选择的 Version 存在。后端会再次校验。

## 3. 下线流程

### 3.1 用户点击“下线”

先查询影响面：

```http
GET /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline-impact?page=1&page_size=20&user_id={actor_id}
```

响应中的 `blocked=true` 时，展示 `items` 与 `counts`，禁用确认下线：

| `items[].kind` | 用户要处理的事实 |
| --- | --- |
| `DRAFT` | 先删除或完成现有 Draft。 |
| `PUBLICATION` | 等待或处理进行中的/结果未知的发布。 |
| `MEMBERSHIP` | 从普通或 Default SkillSet 移除。 |
| `INSTALLATION` | 移除 Bot 的有效安装。 |
| `SERVICE_ARTIFACT` | 删除/退役仍可 restart、scale、rollback 的 Service Bot 版本。 |

`warnings[].kind=UNKNOWN_ARTIFACT` 是“历史 Artifact 无法读取”的诊断信息，**不阻断**下线。可以提示用户，但不能据此禁用确认按钮。

`blocked=false` 后调用：

```http
POST /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/offline?user_id={actor_id}
```

无 request body、无 `Idempotency-Key`、无 `fencing_token`。成功示例：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "changed": true,
    "lifecycle_status": "OFFLINE",
    "offline_at": "2026-09-02T12:00:00Z"
  },
  "request_id": "trace-id"
}
```

若已是 Offline，仍返回 200，但 `changed=false`。POST 在事务内会重新检查影响面，因此即便预检为 `blocked=false`，仍可能返回 409。

### 3.2 下线成功后的前端收敛

1. 停止该 Skill 的 Draft Lease 轮询；不要再请求 `/draft/lease`。该路由在无 Draft 时会正常返回 404。
2. 清空当前编辑器内存态、Draft Revision、fencing token、Publication 轮询状态。
3. 重新 GET Detail 和当前列表页；以服务端返回的 `OFFLINE` 为准。
4. 跳转/保留在“历史 Version”视图，提供“复制为新 Skill”而不是“继续编辑/重新发布”。

## 4. Version Copy 流程

复制只允许 Offline 原 Skill 的 **精确 Published Version**。产品应先请求历史版本列表，让用户选 Vn；不能默认使用名称或 latest 指针代替 Version。

```http
POST /openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/copy?user_id={actor_id}
Idempotency-Key: {uuid-for-this-copy-intent}
```

- 不传 body。
- `version` 是业务版本号，例如 `1`、`2`，不是 `sc_version_number`。
- 同一次点击、网络重试、页面恢复必须复用相同 `Idempotency-Key`；再次点击“复制”才生成新 Key。
- 成功返回 HTTP `201` 和完整 `SpaceSkillDetail`。新 Detail 必须被视为一个新资产：新 `skill_id`、新 `skill_uuid`、`lifecycle_status=DRAFT_ONLY`、`draft.target_version=1`、`draft.status=EDITING`、`draft.source_kind=PUBLISHED_VERSION`、`source=COPY`。

成功后：跳转到返回的新 `skill_id` 的编辑页。Team Space 需要先获取 Draft Lease，才可保存文件：

```text
GET/PUT .../draft/lease
→ 获得 fencing_token
→ PUT Draft 文件（expected_revision_id + fencing_token）
```

复制不复用原 Skill 的 `skill_uuid` 或任何 Skill Center 身份；前端无需生成或传递 UUID。新副本后续发布时，Backend 使用它的新 UUID 创建独立的 Skill Center Skill。

## 5. 必须处理的错误

响应统一为 Envelope。请记录 HTTP status、顶层 `code` 和 `request_id`；后端错误符号只用于本文可读性，不是 response 新字段。

| 场景 | HTTP / `code` | 前端处理 |
| --- | --- | --- |
| 下线时出现新 blocker | `409 / 409313` `SKILL_OFFLINE_BLOCKED`；`data` 是最新 OfflineImpact | 用本次响应的 `data` 刷新阻断弹窗，不继续下线。 |
| 原 Skill 已 Offline，却调用升级/编辑/发布/新增消费 | `409 / 409312` `SKILL_OFFLINE` | 刷新 Detail，展示终态 Offline；引导到 Version Copy。 |
| 在线 Skill 点击 Copy | `409 / 409316` `SKILL_NOT_OFFLINE` | 刷新 Detail；提示“需先完成下线才能复制”。不要误展示为“Skill 已下线”。 |
| Version 不存在或不是 Published | `404 / 404204` `DRAFT_NOT_FOUND` | 刷新版本列表，提示用户选择有效的已发布 Version。 |
| 非 Owner/Manager 或不是 Space Member | `403` | 隐藏/禁用操作，并以服务端结果为准。 |
| Copy 幂等键被用于另一份 Skill/Version | `409 / 409305` `IDEMPOTENCY_KEY_REUSED` | 不自动换 Key 重试；视为客户端关联错误。 |

## 6. 前端改造清单

- [ ] API model 增加 `actor.permissions.copy_offline_skill`。
- [ ] `SpaceSkillDetail.source` 接受新增枚举 `COPY`。
- [ ] Draft `source_kind` 接受 `PUBLISHED_VERSION`。
- [ ] Offline 页面不假设存在 Vn+1 Draft；允许 `draft=null`、`lease_summary=null`。
- [ ] 下线成功后停止 Lease/编辑/发布轮询，并刷新 Detail/List。
- [ ] 历史 Version 行增加“复制为新 Skill”；调用精确 Version Copy endpoint 并使用 Idempotency-Key。
- [ ] Copy 201 后以响应中的新 Skill Detail 建立页面状态并跳转，不覆盖原 Offline Skill。
- [ ] `UNKNOWN_ARTIFACT` 仅为 warning，不阻断下线。
- [ ] 按本文错误码展示准确文案，特别区分 `SKILL_OFFLINE` 与 `SKILL_NOT_OFFLINE`。

## 7. 最小联调用例

1. Published V1、无引用：impact `blocked=false` → Offline 200 → Detail 为 `OFFLINE + draft=null`。
2. Offline 后刷新页面：不再调用 Draft/Lease 接口，历史 Version 仍可读。
3. Offline V1 Copy：201 返回不同 `skill_id/skill_uuid` 的 `DRAFT_ONLY V1`；原 Skill 仍为 Offline。
4. 在线 Skill Copy：409 `409316`，不是 `409312`。
5. 有 Membership/Installation/Service Artifact 任一 blocker：Offline 409 `409313`，列表展示最新 blocker。
6. 有 `UNKNOWN_ARTIFACT` warning 且无明确 blocker：Offline 仍 200。
