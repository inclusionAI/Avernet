# 全局约定（所有分组通用）

> **适用所有分组**。鉴权、响应信封、错误码、租户隔离等跨分组约定在此统一定义。
> 错误码清单见 [error-codes.md](./error-codes.md)，版本化见 [versioning.md](./versioning.md)。

## 认证

secbaas 支持三种认证方式，按端点类型分类：

### 1. API Key 认证（开放 API）

**适用端点**：`/openapi/v1/*`、`/api/v1/bot-health-checker/*`、`/api/v1/sandbox-device/*`

通过 `Authorization: Bearer <api-key>` 请求头携带 API 密钥。密钥通过管理后台 API
（`/api/v1/admin/api-keys`）创建和管理。

密钥状态：
- `ACTIVE`（活跃）— 可用于认证
- `INACTIVE`（非活跃）— 暂停使用
- `REVOKED`（已吊销）— 永久失效

**app_type 限定**：某些端点要求 API Key 的 `app_type` 为特定值：
- **Health Checker**（`/api/v1/bot-health-checker/*`、`/api/v1/sandbox-device/*`）要求
  `app_type` 为 `"health-checker"`。app_type 不匹配时返回 HTTP 403（`code:403000`）。

### 2. Buservice Cookie 认证（管理 API）

**适用端点**：`/api/v1/bots/*`、`/api/v1/publishes/*`、`/api/v1/devices/*`、`/api/v1/tenants/*` 等

基于蚂蚁集团统一认证服务（Buservice）的用户会话。通过 `Cookie: bcs_session=<session>`
传递用户会话，服务端解析当前登录用户。认证失败返回 HTTP 302 重定向到登录页。

部分敏感操作需额外权限码（如 `agentclaw_system_config_admin`），通过
Antbuservice 权限平台配合 HMAC-SHA256 签名验证。

### 3. MOSN 网格认证（内部服务）

**适用端点**：`/internal/*`

内部服务间调用，安全依赖于 MOSN 服务网格的网络隔离，不要求在应用层传递认证凭据。

## 统一响应信封

所有响应使用统一的 `Envelope` 结构：

```json
{
  "code": 200000,
  "message": "OK",
  "data": { ... },
  "request_id": "487ec32cf90b424195f6786651ac1ba5"
}
```

- `code`：6 位整数 — 前 3 位为 HTTP 状态码，后 3 位为业务子码
- `message`：可读消息，**始终为英文**
- `data`：接口负载（成功时），失败时为 `null`
- `request_id`：请求追踪 ID，用于问题定位

## 租户隔离

secbaas 支持多租户。大多数管理 API 需要显式传递 `tenant` 查询参数来指定操作租户：

```
GET /api/v1/bots?tenant=my_tenant
```

- 租户通过 `/api/v1/tenants` API 创建和管理
- 所有数据按租户隔离
- 开放 API（`/openapi/v1/*`）的租户由 API Key 绑定的租户推导，不显式传递

## HTTP 方法语义

| 方法 | 语义 |
|------|------|
| `GET` | 查询资源（集合或单个） |
| `POST` | 创建资源或触发操作 |
| `PUT` | 全量更新资源 |
| `PATCH` | 部分更新资源 |
| `DELETE` | 删除资源 |

## 传输方式

### SSE（Server-Sent Events）

部分端点支持 SSE 流式响应：

- **开放 API**：`POST /openapi/v1/messages/stream` 返回 `text/event-stream`，事件类型
  包括 `ready`、`data`、`error`。
- **BCN 下行链路**：`POST /bcn/downlink` 可通过 `X-BCN-TRANSPORT: sse` 请求头启用
  SSE 模式，用于 `chat.send` 方法。默认传输方式为 JSON（`X-BCN-TRANSPORT: json`）。

## 分页

列表接口统一支持分页参数：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码（从 1 开始） |
| `page_size` | int | 20 | 每页条数（最大 100） |

响应格式：

```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

## 错误处理

所有错误响应使用同一 `Envelope` 结构，`data` 为 `null`，`code` 反映错误类型。
详见 [error-codes.md](./error-codes.md) 的完整清单。