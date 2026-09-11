# Candidate Evidence Pack

Governance Agent 在写入 ClawWeb 前应先形成小型结构化证据包。不要保存完整 Session、完整配置或秘钥。

```json
{
  "candidateKey": "stable-key",
  "analysisDate": "YYYYMMDD",
  "decision": "CREATE_AUTO",
  "ownerUserId": "205357",
  "botId": "bot-id",
  "botPriority": {
    "botType": "service",
    "runtimeStage": "online",
    "highValue": true
  },
  "impact": {
    "failureTaskCount": 12,
    "failureSessionCount": 8,
    "activeDays": 4,
    "firstFailureAt": "...",
    "lastFailureAt": "...",
    "failureRate": 0.31,
    "recentTrend": "rising"
  },
  "rootCause": {
    "failureClass": "CONFIG_MISSING",
    "componentType": "skill",
    "componentName": "change-inspect",
    "canonicalRootCause": "Skill manifest missing from loaded directory",
    "crossBotHint": false
  },
  "currentState": {
    "defectStillPresent": true,
    "skillPresent": false,
    "mcpPresent": null,
    "latestPublishAfterFailure": false,
    "laterSuccessObserved": false
  },
  "evidence": [
    {
      "sessionId": "...",
      "taskIndex": 0,
      "occurredAt": "...",
      "failureSummary": "...",
      "toolErrorSummary": "..."
    }
  ],
  "repairPlan": {
    "actionType": "DIRECT_EVOLUTION",
    "target": "workspace/skills/.../SKILL.md",
    "changeSummary": "...",
    "risk": "low",
    "rollback": "restore previous file version"
  },
  "verificationPlan": {
    "minNewSessions": 1,
    "successCondition": "same root cause does not recur",
    "failureCondition": "same component/error recurs"
  }
}
```

映射到 ClawWeb 请求：

- `decision=CREATE_AUTO` → `actionType=DIRECT_EVOLUTION`
- `decision=CREATE_MANUAL` → `actionType=ASSIGN_OWNER`
- `canonicalRootCause` → `rootCauseSummary`
- Owner 可行动依据 → `assignmentReason`
- `repairPlan.changeSummary` → `suggestedAction`
- 代表 Evidence → `selectedTasks`

API 详见 [api.md](api.md)。
