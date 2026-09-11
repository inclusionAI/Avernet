# Governance candidate API

## 1. 创建候选

```text
POST {CLAWWEB_URL}/api/insight/v1/internal/governance/actions
```

不需要认证头。必须提供 1～128 字符的 `Idempotency-Key`。

```json
{
  "ownerUserId": "205357",
  "sourceOwnerUserId": "205357",
  "botId": "bot-id-from-evidence",
  "title": "候选改进项标题",
  "userGuidance": "可选补充说明",
  "sourceRuleId": "rule.id",
  "actionType": "DIRECT_EVOLUTION",
  "assignmentReason": "当前 Bot 的具体配置事实说明",
  "rootCauseSummary": "由 Evidence 与 NAS 当前状态支持的规范化根因",
  "suggestedAction": "明确的修复目标、方案和验收方式",
  "selectedTasks": [
    {"sessionId": "existing-session-id", "taskIndex": 0}
  ]
}
```

服务端控制：

```text
status=PENDING_ADMIN
adminReviewStatus=PENDING
sourceType=ADMIN_RULE_DIRECT_EVOLUTION|ADMIN_RULE_ASSIGN_OWNER
createdBy=governance-agent
version=1
```

客户端不得发送状态、审批、创建者、版本或 Evolve 执行字段。首次创建返回 `201`，幂等重放返回 `200`。

## 2. 去重查询（提交前必查）

提交候选前，必须查询同一 ownerUserId + botId 在过去 15 天内是否已有被驳回或已存在的改进项，避免重复打扰。

```text
GET {CLAWWEB_URL}/api/insight/v1/internal/governance/actions?ownerUserId={uid}&botId={bid}&fields=id,title,rootCauseSummary,actionType,status,adminReviewStatus,adminReviewReason,createdAt,updatedAt
```

查询参数：

| 参数 | 说明 |
|------|------|
| `ownerUserId` | 必填，目标 Owner |
| `botId` | 必填，目标 Bot |
| `status` | 可选过滤，如 `REJECTED` |
| `adminReviewStatus` | 可选过滤，如 `REJECTED`、`APPROVED` |
| `since` | 可选，ISO 时间，默认 15 天前 |
| `fields` | 可选，返回字段列表 |
| `limit` | 可选，默认 50 |

去重判断逻辑：

1. 查询同一 ownerUserId + botId 的最近 15 天记录
2. 如果存在 `adminReviewStatus=REJECTED` 且 rootCauseSummary 高度相似（同 failure_class + 同 component）→ **DROP**，不重复提交
3. 如果存在 `status=PENDING_ADMIN` 的同根因项 → **DROP**，等待审批结果
4. 如果存在 `status=IN_PROGRESS` 的同根因项 → **DROP**，已有在处理中
5. 如果仅存在已 RESOLVED 的同根因项，但 ODPS 显示近期仍有同类失败 → 可提交，但需在 assignmentReason 中注明历史项已关闭但问题复现

相似度判断：优先比较 `canonical_root_cause` 中的 `failure_class + component_type + component_name`，而非整段文本。

## 3. 标记已处理 — Evolve 完成后推进验证

接口契约已在 ClawWeb 后端实现；具体 PRE/PROD 是否可用仍以目标环境实际部署版本为准。

DIRECT_EVOLUTION 类型的改进项被 Admin 审批通过后，ClawEvolve 自动应用修复。修复成功后需调用此接口设置 `handledAt`，使其进入 Verification Agent 的待验收列表。

```text
POST {CLAWWEB_URL}/api/insight/v1/internal/governance/actions/{improvementId}/mark-handled
```

无需鉴权。

```json
{
  "handledAt": "2026-08-19T15:30:00+08:00",
  "appliedEvolveTaskId": "evolve-task-uuid"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `handledAt` | 是 | ISO-8601 时间，修复完成的时间 |
| `appliedEvolveTaskId` | 否 | Evolve 任务 ID，用于追溯关联 |

**状态转换**：仅对 `adminReviewStatus=APPROVED` 且 `actionType=DIRECT_EVOLUTION` 且 `handledAt` 为空的项有效。调用后 `handledAt` 被设置，项进入 `verification-candidates` 列表。其他状态返回 409。
