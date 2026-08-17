# 服务 Bot OpenAPI 契约

本文记录 Bot 工坊第一期服务 Bot 接口。路径遵循最新 dev 的 bot-first 规范，
统一前缀为 `/openapi/v1/bots`。

## 通用约定

- 所有接口均要求认证。
- `user_id`：必填 Query，表示实际操作人。
- `owner_id`：可选 Query，省略时为当前用户；访问协作 Bot 时传 Bot Owner。
- 成功和失败统一使用 OpenAPI v1 Envelope。
- 生命周期动作不接收 `publication_id`。后端按 `bot_id`、当前环境和目标状态选择最新可操作版本，避免调用方传错历史版本。

## 接口清单

| Method | URL | 用途 |
| --- | --- | --- |
| GET | `/all` | 统一 Bot 卡片列表；服务 Bot 最多展开两张版本卡片 |
| POST | `/{bot_id}/lifecycle/upgrade` | 将支持的云端个人 Bot 转为服务 Bot |
| GET | `/{bot_id}/lifecycle` | 查询最多两张可见生命周期卡片 |
| DELETE | `/{bot_id}/lifecycle` | 删除从未正式发布的初始草稿 Bot |
| GET | `/{bot_id}/lifecycle/approval` | 查询审批开关 |
| PUT | `/{bot_id}/lifecycle/approval` | Owner 修改审批开关 |
| POST | `/{bot_id}/lifecycle/advance` | 推进到预发或线上 |
| POST | `/{bot_id}/lifecycle/restart` | 重启预发或线上运行时 |
| POST | `/{bot_id}/lifecycle/cancel-staging` | 销毁预发并退回草稿 |
| POST | `/{bot_id}/lifecycle/offline` | 下线线上版本 |
| POST | `/{bot_id}/lifecycle/retry` | 重试最新失败发布 |
| GET | `/{bot_id}/edit-lock` | 查询编辑锁 |
| POST | `/{bot_id}/edit-lock` | 获取编辑锁 |
| DELETE | `/{bot_id}/edit-lock` | 释放自己的编辑锁 |
| POST | `/{bot_id}/edit-lock/steal` | Owner/Admin 抢占编辑锁 |

完整示例：

```text
GET /openapi/v1/bots/20260817_abcd1234/lifecycle?user_id=165137&owner_id=168944
```

## 统一列表服务卡片

`GET /openapi/v1/bots/all` 中，个人/本地 Bot 仍各占一张卡；服务 Bot 按发布版本展开，
并增加以下字段：

```json
{
  "bot_id": "20260817_abcd1234",
  "card_id": "service:20260817_abcd1234:102",
  "kind": "service",
  "display_state": "service_staging",
  "status": "validating",
  "publication_id": 102,
  "publication_version": 3,
  "live_version": 2,
  "actions": ["view", "chat", "edit", "publish_online", "restart", "cancel_staging"]
}
```

规则：

1. 最多返回两个版本卡片，按版本号和发布记录 ID 倒序。
2. `upgraded` 不展示。
3. 多个 `released` 只保留最新一个参与排序。
4. publication 采用一次批量查询，不按服务 Bot 逐个查询。
5. 非服务 Bot 的 `publication_id/publication_version/live_version` 为 `null`，`card_id` 等于 `bot_id`。

## 生命周期查询

`GET /openapi/v1/bots/{bot_id}/lifecycle` 返回更完整的版本信息：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "bot_id": "20260817_abcd1234",
    "items": [
      {
        "bot_id": "20260817_abcd1234",
        "publication_id": 102,
        "card_id": "service:20260817_abcd1234:102",
        "version": 3,
        "status": "staging",
        "internal_status": "validating",
        "live_version": 2,
        "deployment": null,
        "approval": null,
        "available_actions": ["publish_online", "restart_publish", "cancel_staging"],
        "created_at": "2026-08-17T12:00:00",
        "updated_at": "2026-08-17T12:10:00"
      }
    ]
  },
  "request_id": "trace-id"
}
```

稳定产品状态为 `draft/deploying/staging/running/offline`。前端使用 `status` 和
`available_actions` 渲染，不根据 `internal_status` 自行推断动作。

## 推进和重启请求

推进请求：

```http
POST /openapi/v1/bots/{bot_id}/lifecycle/advance?user_id=165137
Content-Type: application/json

{"stage":"staging"}
```

`stage` 只能是 `staging` 或 `online`。重启使用同样的 stage 枚举：

```http
POST /openapi/v1/bots/{bot_id}/lifecycle/restart?user_id=165137
Content-Type: application/json

{"stage":"online"}
```

动作成功返回 HTTP 202：

```json
{
  "code": 202000,
  "message": "Accepted",
  "data": {
    "bot_id": "20260817_abcd1234",
    "publication_id": 102,
    "action": "publish_online",
    "accepted": true,
    "operation_status": "waiting_approval",
    "approval": {
      "required": true,
      "status": "PROCESSING",
      "approval_id": "approval-id",
      "approval_url": "https://example.invalid/approval"
    }
  },
  "request_id": "trace-id"
}
```

`pending` 表示已入队；`waiting_approval` 表示等待已有审批流处理。

## 审批与编辑锁

- Owner 可修改 `should_approval`。
- 开启审批后，非 Owner 的上线和下线需要审批。
- 草稿进入预发时，如果 Bot 存在协作者，操作者必须持有编辑锁。
- Member 可获取和释放自己的锁；只有 Owner/Admin 可以抢锁。
- 未持锁推进预发返回 423。

## 删除语义

只有 Owner 可以删除，且必须是从未正式发布的初始草稿。只要同一 Bot 曾存在
`success/upgraded/released` 任一历史状态，即永久禁止删除整个服务 Bot。

## 主要错误

| HTTP | 含义 |
| --- | --- |
| 400 | 参数非法 |
| 401 | 未认证 |
| 403 | `user_id` 与认证身份不一致 |
| 404 | Bot 不存在或调用方无权访问（统一掩蔽） |
| 409 | 状态或 Bot/引擎组合不支持该动作 |
| 422 | 请求模型校验失败 |
| 423 | 协作草稿推进预发时未持锁 |
| 500 | 内部错误 |
