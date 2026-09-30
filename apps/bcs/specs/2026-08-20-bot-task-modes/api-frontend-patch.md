# 前端对接文档 — Bot 任务模式开关

> 扩展接口:`PATCH /openapi/v1/collaboration/bots/{bot_id}`
> 新增两个**可选布尔字段**:`task_claim_mode`(任务领取开关)、`task_dream_mode`(任务 Dream 开关)。
> 前端两个开关按钮分别对应这两个字段。

---

## 1. 鉴权与调用约束

- 请求头:`Authorization: Bearer <token>`
- 仅**机器人创建者**(`created_by` == 当前登录人 staff_no)可修改;非创建者 → `403 forbidden`。
- 该接口为 Human 控制面操作,经网关调用 BCS。
- 路径参数:`bot_id` —— 机器人 ID(string)。

## 2. 请求体(Request Body)

JSON 对象。`additionalProperties: false`(禁止未知字段),`minProperties: 1`(至少传一个可改字段)。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 否 | 名称 |
| `visibility` | enum | 否 | `public` / `protected` / `private` |
| `status` | enum | 否 | `online` / `hidden` |
| `descriptor` | object | 否 | 描述符(仅 physical bot 可改) |
| **`task_claim_mode`** | **boolean** | 否 | **任务领取开关。省略=不变;`true`=开启,`false`=关闭** |
| **`task_dream_mode`** | **boolean** | 否 | **任务 Dream 开关。省略=不变;`true`=开启,`false`=关闭** |

字段语义:
- **省略某个开关 → 该开关保持原值不变**(部分更新)。
- 两个开关**互相独立**:只传一个不会影响另一个。
- **至少传一个可改字段**;空 body 或全是省略 → `400 invalid_request`。
- 传入值等于当前值时,视为幂等成功(no-op)。
- 开关仅对 **physical bot(`kind=bot`)** 有意义;对 Human 行(`kind=human`)设置开关 → `400 invalid_bot_kind`。
- 写库失败等内部错误会作为 `500` 返回,**不会静默返回成功**。

### 请求示例

示例 1 — 同时打开两个开关:
```json
{
  "task_claim_mode": true,
  "task_dream_mode": true
}
```

示例 2 — 只打开 claim:
```json
{ "task_claim_mode": true }
```

示例 3 — 只关闭 dream(显式传 `false`):
```json
{ "task_dream_mode": false }
```

示例 4 — 改名称同时打开 claim:
```json
{ "name": "新名称", "task_claim_mode": true }
```

---

## 3. 成功响应 `200`

统一信封:`{ code, message, data, request_id }`,成功 `code` 恒为 `20000`。
`data` 为更新后的完整 Bot。physical bot(`kind=bot`)的 `data` 现新增两个布尔字段 —— **始终返回**(未设置过时默认 `false`):

```json
{
  "code": 20000,
  "message": "OK",
  "request_id": "req-3f9a1b2c-...",
  "data": {
    "bot_id": "bot-1",
    "kind": "bot",
    "name": "新名称",
    "visibility": "public",
    "status": "online",
    "env": "default",
    "created_by": "staff-1",
    "descriptor": {
      "summary": "开发助手",
      "domains": ["development"],
      "skills": [{ "name": "code_review", "description": "代码评审" }],
      "scopes": ["production"]
    },
    "reachability": "reachable",
    "provider": { "provider_id": "p1", "name": "Provider One" },
    "agent_code": "agent-xyz",
    "task_claim_mode": true,
    "task_dream_mode": true,
    "created_at": 1710960000000,
    "updated_at": 1726400000000
  }
}
```

字段说明:
- `task_claim_mode` / `task_dream_mode`:booleans,physical bot **始终存在**,默认 `false`。
- `created_by` / `provider` / `agent_code`:无值时**省略**(nullable-but-omitted)。
- `created_at` / `updated_at`:Unix 毫秒。
- 若返回的是 Human 行(`kind=human`),`data` 不含 `descriptor`/`reachability`/`provider`/`agent_code`/`task_claim_mode`/`task_dream_mode`(这些是 physical bot 专属)。

---

## 4. 错误响应

统一错误信封:`{ code: integer, message: string, data: { error_code: string, details?: object }, request_id: string }`。
**前端以 HTTP 状态码 + `data.error_code` 作为判断依据**(`code` 为数字码,不同错误类型数值不同)。

| HTTP | `data.error_code` | 场景 |
| --- | --- | --- |
| 400 | `invalid_request` | 空 patch、字段非法、批量参数非法 |
| 400 | `invalid_bot_kind` | 对 Human 行设置 `descriptor` 或任务模式开关 |
| 401 | `unauthenticated` | 未带 / 无效 token |
| 403 | `forbidden` | 当前人不是该 bot 的 `created_by` |
| 404 | `bot_not_found` | bot 不存在 / 已删除 / 未上架 |
| 500 | `internal_error` | 写库失败等内部错误(不会静默成功) |

错误示例(空 body → 400):
```json
{
  "code": 40000,
  "message": "Bot patch must contain at least one mutable field",
  "data": { "error_code": "invalid_request" },
  "request_id": "req-3f9a1b2c-..."
}
```
错误示例(非创建者 → 403):
```json
{
  "code": 40300,
  "message": "Current Human does not own Bot 'bot-1'",
  "data": { "error_code": "forbidden" },
  "request_id": "req-3f9a1b2c-..."
}
```
> 上例中的数字 `code` 仅为占位示意,实际数值以 BCS `ErrorEnvelope` 为准;判断逻辑请用 HTTP 状态码 + `data.error_code`。

---

## 5. 前端使用要点

- 两个开关各自独立调用:`{ "task_claim_mode": true/false }`、`{ "task_dream_mode": true/false }`。
- 关闭时**必须显式传 `false`**(不要靠省略,省略=不变)。
- 每次成功后以响应 `data.task_claim_mode` / `data.task_dream_mode` 为准刷新 UI。
- 初始查询某 bot 当前开关状态:用 `GET /openapi/v1/collaboration/bots/{bot_id}`(同样返回这两个字段,未设置=`false`)。