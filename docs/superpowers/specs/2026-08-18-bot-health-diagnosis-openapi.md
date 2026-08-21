# Bot 健康诊断 OpenAPI 契约

- 日期：2026-08-18
- 状态：已实现，待 Gateway 发布与联调
- 适用范围：云端 `openclaw` Bot（个人 Bot、服务 Bot 草稿工作区）
- 授权：Bot Owner 或具备 Member 及以上权限的协作者

## 1. 获取健康状态

```http
GET /openapi/v1/bots/{bot_id}/diagnostics/health
```

Query：

| 参数 | 必填 | 说明 |
|---|---|---|
| `user_id` | 是 | 当前请求代表的用户 |
| `owner_id` | 否 | Bot Owner；访问协作 Bot 时填写 |
| `scan_id` | 否 | `health-check` 返回的任务 ID；不传时返回最近一次完成结果 |

没有历史结果时：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "found": false,
    "bot_id": "bot-001",
    "scan_id": null,
    "status": "not_run",
    "health_score": null,
    "grade": null,
    "summary": {},
    "check_items": [],
    "findings": [],
    "failed_reason": null,
    "duration_ms": null,
    "created_at": null
  },
  "request_id": "trace-id"
}
```

完成结果：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "found": true,
    "bot_id": "bot-001",
    "scan_id": 1024,
    "status": "completed",
    "health_score": 86,
    "grade": "good",
    "summary": {
      "pass": 4,
      "warning": 1,
      "fail": 0,
      "error": 0
    },
    "check_items": [
      {
        "name": "AGENTS.md",
        "status": "completed",
        "result": "warning",
        "score": 86,
        "duration_ms": 1200
      }
    ],
    "findings": [
      {
        "check_item": "AGENTS.md",
        "findings": [
          {
            "rule_id": "D-AGENTS-001",
            "name": "角色定义诊断",
            "message": "角色定义不完整",
            "risk_level": "warning",
            "result": "warning",
            "score": 86
          }
        ]
      }
    ],
    "failed_reason": null,
    "duration_ms": 1200,
    "created_at": "2026-08-18T10:00:00"
  },
  "request_id": "trace-id"
}
```

## 2. 触发健康检查

```http
POST /openapi/v1/bots/{bot_id}/diagnostics/health-check
```

Query：

| 参数 | 必填 | 说明 |
|---|---|---|
| `user_id` | 是 | 当前请求代表的用户 |
| `owner_id` | 否 | Bot Owner；访问协作 Bot 时填写 |

不需要 Request Body。成功后返回 `202 Accepted`：

```json
{
  "code": 202000,
  "message": "Accepted",
  "data": {
    "bot_id": "bot-001",
    "scan_id": 1024,
    "status": "scanning"
  },
  "request_id": "trace-id"
}
```

前端使用返回的 `scan_id` 调用：

```http
GET /openapi/v1/bots/{bot_id}/diagnostics/health?scan_id=1024
```

直到 `status` 为 `completed` 或 `failed`。

## 3. 状态与错误

任务状态：

- `not_run`：没有完成过健康检查。
- `scanning`：诊断中。
- `scan_completed`：扫描完成，正在处理后续结果。
- `patching`：正在生成修复建议。
- `completed`：诊断完成，可读取健康分和详细问题。
- `failed`：诊断失败，读取 `failed_reason`。

主要错误：

| HTTP | 场景 |
|---:|---|
| `403` | `user_id` 与认证用户不一致 |
| `404` | Bot、诊断任务不存在，或者当前用户无权访问 |
| `409` | 5 分钟内已有进行中的诊断任务 |
| `409` | Bot 不是受支持的云端 OpenClaw Bot，或者已有检查正在进行 |
| `502` | 诊断持久化服务暂时不可用 |

`scan_id` 不是授权凭证。后端会在返回记录前再次校验该任务所属的 `bot_id`、Owner 和运行环境。
