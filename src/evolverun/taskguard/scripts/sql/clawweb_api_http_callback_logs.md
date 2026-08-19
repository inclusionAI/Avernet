# clawweb 需要实现的 http_callback_logs API 接口

ClawMind 在 `database.mode: "api"` 下通过以下 API 接口读写 `http_callback_logs` 表。

## 1. INSERT — 写入审计日志

**请求**: `POST /http-callback-logs`

**请求体** (JSON):
```json
{
  "flow_id": "9b6d80ab-d8dc-4461-8280-e8f0096e984c",
  "workflow_id": "social-appeal-review-flow",
  "config_id": "hcb_xxx",
  "config_name": "监控平台通知",
  "callback_url": "https://example.com/webhook",
  "notify_event": "node_succeeded",
  "node_id": "prepare-run-dir",
  "attempt": 0,
  "max_attempts": 3,
  "request_body": "{\"workflow_id\":\"...\",\"flow_id\":\"...\",\"status\":\"node_succeeded\",\"ext_info\":{...}}",
  "request_headers": "{\"Content-Type\":\"application/json\",\"X-Callback-Config-Id\":\"hcb_xxx\",\"X-Callback-Timestamp\":\"1784630962549\"}",
  "response_status_code": 200,
  "response_body": "{\"ok\":true}",
  "duration_ms": 150,
  "status": "delivered",
  "error_message": null
}
```

**响应**:
```json
{
  "insertId": 123
}
```

## 2. 查询 — 按 flowId

**请求**: `GET /reads/http-callback-logs/flow/:flowId?limit=100`

**响应**:
```json
[
  {
    "id": 123,
    "flow_id": "...",
    "workflow_id": "...",
    "config_id": "...",
    "config_name": "...",
    "callback_url": "...",
    "notify_event": "...",
    "node_id": "...",
    "attempt": 0,
    "max_attempts": 3,
    "request_body": "...",
    "request_headers": "...",
    "response_status_code": 200,
    "response_body": "...",
    "duration_ms": 150,
    "status": "delivered",
    "error_message": null,
    "gmt_create": 1784630962,
    "gmt_modified": 1784630962
  }
]
```

## 3. 查询 — 按 workflowId

**请求**: `GET /reads/http-callback-logs/workflow/:workflowId?limit=100`

**响应**: 同上，数组

## 4. 查询 — 按 status

**请求**: `GET /reads/http-callback-logs/status/:status?limit=100`

**响应**: 同上，数组

## 5. 清理 — 删除旧记录

**请求**: `DELETE /http-callback-logs/cleanup?olderThan=1784630962`

**响应**:
```json
{
  "deleted": 42
}
```

## 表结构 (MySQL DDL)

见 `scripts/sql/migrate_v28_mysql.sql`。

## 注意事项

- INSERT 是高频操作（每次 HTTP 回调每次 attempt 一条），需要确保 clawweb API 有合理的写入性能
- `request_body` 可能较大（含 ext_info），建议用 MEDIUMTEXT 存储
- 查询接口需支持 `limit` 参数，默认 100，最大 500
- `status` 字段值为 `delivered` / `failed` / `skipped`
