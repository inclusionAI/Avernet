# Verification API

## 1. List — 待验收项

```text
GET {CLAWWEB_URL}/api/insight/v1/internal/governance/verification-candidates?limit=100
```

不需要认证。只返回 `IN_PROGRESS` 且存在 `handledAt` 的待验收项。

## 2. List Open — 用户可能已经修复但没有回传状态的开放项

```text
GET {CLAWWEB_URL}/api/insight/v1/internal/governance/verification-candidates/open?limit=100
```

不需要认证。返回仍处于 `ACTIVE`，或处于 `IN_PROGRESS` 但尚未记录 `handledAt` 的 Owner 可见改进项。结果会附带 `gmtModified`、`latestEvolveTaskId` 和 `latestEvolveTaskStatus`，供 Verification Agent 判断修复边界和是否仍有 Evolve Task 在运行。

该接口只负责发现候选，不代表项目已经被用户修复。Verification Agent 必须先检查 NAS/ODPS 的真实证据；如果 `latestEvolveTaskStatus` 表示任务仍在运行，应等待，不得直接验收。

```bash
python3 scripts/verification.py list-open --limit 100 --json
```

## 3. List Resolved — 已标记完成的改进项（主动验收）

```text
GET {CLAWWEB_URL}/api/insight/v1/internal/governance/actions?status=RESOLVED&limit=100
```

查询已标记为完成（RESOLVED / VERIFIED / AUTO_VERIFIED）的改进项，用于主动验收：通过 NAS 聊天记录和 ODPS full_message 确认问题是否真的消失。

查询参数：

| 参数 | 说明 |
|------|------|
| `ownerUserId` | 可选，按 Owner 过滤 |
| `botId` | 可选，按 Bot 过滤 |
| `status` | 可选，如 `RESOLVED,VERIFIED,AUTO_VERIFIED`（逗号分隔） |
| `since` | 可选，ISO 时间，默认 30 天前 |
| `limit` | 可选，默认 100 |

命令行：

```bash
python3 scripts/verification.py list-resolved --limit 100 --since "2026-07-20T00:00:00+08:00"
```

## 4. Submit — 已进入验收队列的项目回写结果

```text
POST {CLAWWEB_URL}/api/insight/v1/internal/governance/verification-results
```

```json
{
  "improvementId": 43,
  "version": 3,
  "outcome": "DISAPPEARED",
  "newSessionCount": 2,
  "lastRecurrenceAt": null
}
```

只允许字段：

```text
improvementId
version
outcome
newSessionCount
lastRecurrenceAt（可选）
overrideActionType（可选，仅 outcome=STILL_PRESENT 时有效）
```

| 字段 | 说明 |
|------|------|
| `overrideActionType` | 当原 actionType 为 DIRECT_EVOLUTION 且验收结论为 STILL_PRESENT 时，可设置为 `ASSIGN_OWNER` 将修复方式从自动切换为手动。UI 将展示"自动修复未生效，请手动处理"。不传则保持原 actionType 不变。 |

结果：

- `DISAPPEARED`：`RESOLVED/VERIFIED/AUTO_VERIFIED`；至少一个新 Session，且 `handledAt` 至少经过 2 天。提前提交返回 `409 VERIFICATION_TOO_EARLY`。
- `STILL_PRESENT`：保持 `IN_PROGRESS`，记录复现，验收未通过。可选携带 `overrideActionType=ASSIGN_OWNER` 切换修复方式。
- `STILL_PRESENT` + 目标为 `RESOLVED` → **重新打开为 ACTIVE**（上周已实现），支持已完成项被发现假修复后回退。
- `INSUFFICIENT_DATA`：保持 `IN_PROGRESS`，等待更多证据。

`version` 用于乐观锁；过期或重复写回返回 `409`。

## 5. Submit Open — 未回传状态的开放项目回写结果

```text
POST {CLAWWEB_URL}/api/insight/v1/internal/governance/verification-results/open
```

请求字段与普通结果接口一致：

```json
{
  "improvementId": 43,
  "version": 3,
  "outcome": "DISAPPEARED",
  "newSessionCount": 2,
  "lastRecurrenceAt": null
}
```

该接口只接受来自 `verification-candidates/open` 的 `ACTIVE` 或未回传 `handledAt` 的 `IN_PROGRESS` 项。`DISAPPEARED` 会将项目推进到 `RESOLVED/VERIFIED/AUTO_VERIFIED`；`STILL_PRESENT` 或 `INSUFFICIENT_DATA` 保持项目打开。接口仍要求最新 `version`，并且 `DISAPPEARED` 至少需要一个真实的新 Session，同时修复观察边界至少经过 7 天。提前提交返回 `409 OPEN_VERIFICATION_TOO_EARLY`。

Verification Agent 不得传入 `status`，也不能借此修改 Owner、审批状态或 Bot 配置。确认开放项已经修复后使用：

```bash
python3 scripts/verification.py submit-open \
  --improvement-id 43 \
  --version 3 \
  --outcome DISAPPEARED \
  --new-session-count 2
```

## 6. 验收成功 → 生成规则进化候选

当本次`DISAPPEARED` 使某条 `DIRECT_EVOLUTION` 规则达到 ClawWeb 配置的成功验收门槛时，Verification 结果响应可能包含：

```json
{
  "ruleEvolutionProposal": {
    "proposalId": 7,
    "sourceRuleId": "tool.utoo-proxy.unsupported",
    "fromRuleVersion": 1,
    "proposedRuleVersion": 2,
    "status": "PENDING",
    "successCount": 3
  }
}
```

这只是 Admin 的规则进化候选，不代表规则已经生效。Verification Agent 不得批准或发布它。Admin 需要通过以下接口审核：

```text
GET  /api/insight/v1/admin/governance/rule-evolution?status=PENDING
POST /api/insight/v1/admin/governance/rule-evolution/{proposalId}/review
```

Admin 批准后，ClawWeb 才会把当前规则 JSON 发布为新版本，并将 `adminPolicy.mode` 提升为 `TRUSTED`；规则版本变化会使旧的持续授权范围重新校验。

## 7. 验收失败 → 回退手动修复

接口已支持 `overrideActionType`；脚本通过 `--override-action-type ASSIGN_OWNER` 发送。具体 PRE/PROD 是否可用仍以目标环境实际部署版本为准。

当 Verification Agent 发现 DIRECT_EVOLUTION 修复未生效时，除标记 STILL_PRESENT 外，可携带 `overrideActionType=ASSIGN_OWNER` 告知 ClawWeb 将此项的修复方式改为手动。这样用户在 Admin UI 看到的是"需要手动处理"而非"自动修复中"。

```json
{
  "improvementId": 46,
  "version": 3,
  "outcome": "STILL_PRESENT",
  "newSessionCount": 2,
  "lastRecurrenceAt": "2026-08-20T10:00:00+08:00",
  "overrideActionType": "ASSIGN_OWNER"
}
```

注意：`overrideActionType` 只能是 `ASSIGN_OWNER`（不能将手动改为自动），且仅在原 actionType 为 `DIRECT_EVOLUTION` 时生效。
