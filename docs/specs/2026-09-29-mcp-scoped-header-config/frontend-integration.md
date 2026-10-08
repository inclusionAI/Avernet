# MCP 详情页参数配置：新老产品联调接口

新老产品页分别使用自己的 HTTP 入口，但读写的是**同一份** user/Bot MCP 配置，后端共用同一个 core 操作。两套接口的 Header 组语义相同；差别只有鉴权、方法、`server_code` 位置和响应格式。

| 产品页 | 读取 | 保存 |
| --- | --- | --- |
| 老产品 | `GET /api/mcp/config-groups?server_code={server_code}` | `POST /api/mcp/config-groups` |
| 新产品 | `GET /openapi/v1/bots/mcp/servers/{server_code}/config-groups?user_id={user_id}` | `PUT /openapi/v1/bots/mcp/servers/{server_code}/config-groups?user_id={user_id}` |

老产品沿用当前站点的登录 Cookie、`ctoken`、Referer 请求封装；内部接口从登录态取得用户工号，不在请求体中传 `user_id`。新产品沿用 OpenAPI 鉴权封装，GET/PUT 均须带 `user_id` query；真人调用时该值必须与已验证的登录用户一致。OpenAPI 的 `server_code` 是路径参数，构造 URL 时应编码。Cookie、`ctoken`、Referer 都不是下文的 JSON 字段。

## 页面初始化

1. 获取 MCP Center 元信息，展示可用的环境和传输协议：老产品可用 `GET /api/mcp/market/detail?server_code={server_code}`；新产品可用 `GET /openapi/v1/bots/mcp/servers/{server_code}`。
2. 获取本人拥有的 Bot，作为“指定 Bot”候选项。现有 `GET /api/bots/by-owner` 可使用，但当前只取前 100 个；超过 100 个需要另补分页。不要直接把 `/api/bots/by-owner-or-collaborator` 返回的协作者 Bot 提交给配置接口。
3. 调用本产品页对应的配置组 GET 回显表单；保存时调用对应的 POST 或 PUT，并提交**完整表单快照**。

## 读取配置组

```http
GET /api/mcp/config-groups?server_code=mcp.example.server
GET /openapi/v1/bots/mcp/servers/mcp.example.server/config-groups?user_id=<current_user_id>
```

两套接口返回相同的 `data` 内容，只是外层格式不同。老产品响应：

```json
{
  "success": true,
  "data": {
    "server_code": "mcp.example.server",
    "endpoint_env": "PROD",
    "transport_protocol": "SSE",
    "params": [
      {"key": "X-Region", "value": "default", "bots": []},
      {"key": "X-Region", "value": "east", "bots": ["bot-x"]}
    ],
    "sync_results": null,
    "sync_summary": null
  }
}
```

新产品响应的 `data` 完全相同，外层改为 OpenAPI Envelope：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "server_code": "mcp.example.server",
    "endpoint_env": "PROD",
    "transport_protocol": "SSE",
    "params": [
      {"key": "X-Region", "value": "default", "bots": []},
      {"key": "X-Region", "value": "east", "bots": ["bot-x"]}
    ],
    "sync_results": null,
    "sync_summary": null
  },
  "request_id": "<trace_id>"
}
```

`params` 只回显**显式保存的配置组**，不会把继承的 user Header 展开为 Bot 行。没有保存过配置时，返回 `endpoint_env: "PROD"`、`transport_protocol: null`、`params: []`。`value` 当前按一期约定原值回显，不做掩码；前端请勿写入控制台或埋点日志。GET 直接读控制面数据，不要求 MCP Center 当时可用。

## 保存配置组

老产品：

```http
POST /api/mcp/config-groups
Content-Type: application/json
```

```json
{
  "server_code": "mcp.example.server",
  "endpoint_env": "PROD",
  "transport_protocol": "SSE",
  "params": [
    {"key": "X-Region", "value": "default", "bots": []},
    {"key": "X-Region", "value": "east", "bots": ["bot-x"]}
  ]
}
```

新产品：

```http
PUT /openapi/v1/bots/mcp/servers/mcp.example.server/config-groups?user_id=<current_user_id>
Content-Type: application/json
```

请求体与上例相同，**但不含 `server_code`**；它已在路径中给出。两边都要提供以下字段：

| 字段 | 要求 | 含义 |
| --- | --- | --- |
| `endpoint_env` | 必填，`PROD` / `PRE` | 用户默认的端点环境。 |
| `transport_protocol` | 必填，可为 `SSE` / `STREAMABLE_HTTP` / `null` | 用户默认协议；`null` 清除协议偏好，使用 Center 默认选择。 |
| `params` | 必填数组 | 保存后的**全部**显式 Header 组；`[]` 清空全部显式 Header。 |
| `params[].key` | 必填字符串 | HTTP Header 名称，比较时不区分大小写。 |
| `params[].value` | 必填字符串 | Header 值；空字符串也是显式值。 |
| `params[].bots` | 字符串数组，建议始终显式传递 | `[]` 表示 user 默认；非空表示仅为列出的本人 Bot 显式配置。 |

允许同名 Header 同时存在 user 默认组和指定 Bot 组；Bot 的值覆盖同名默认值，其余 Header 继续继承。两个指定 Bot 组不能以同名 Header 命中同一个 Bot（名称比较不区分大小写）。指定 Bot 不要求此时已安装 MCP；未消费该 MCP 时只保存配置，不触发设备投影。

保存是**完整快照替换**，不是逐行 PATCH：前端每次必须提交所有要保留的组，删除行即从 `params` 移除。`params: []` 清空 user/Bot 显式 Header，但本次提交的 `endpoint_env`、`transport_protocol` 仍会保存；API key、Bot 自定义 URL 和 Bot 自身环境/协议不被此接口改动。配置组接口也不负责安装 MCP。

两套接口保存成功后都在 `data` 中返回持久化后的配置及 `sync_results`、`sync_summary`。老产品成功标志为 HTTP 200 / `success: true`；新产品为 HTTP 200 / `code: 200000`。这**只表示控制面保存成功**，不保证每个 Bot 投递成功。离线或单 Bot 投递失败不会回滚配置，页面应显示“已保存”并展示投递警告。例如：

```json
{
  "sync_results": [
    {"bot_id": "bot-x", "synced": false, "reason": "设备离线"}
  ],
  "sync_summary": {
    "affected_bot_count": 1,
    "synced_count": 0,
    "offline_count": 1,
    "runtime_drift_count": 0,
    "failed_count": 0
  }
}
```

`sync_results` 只包含实际消费该 MCP 的投影目标；未安装该 MCP 的预存配置 Bot 不出现在本次结果中。多页面同时编辑按最后一次完整快照保存为准，保存后应采用响应或重新 GET 刷新表单。

## 校验与错误

| HTTP 状态 | 常见原因 |
| --- | --- |
| 401 / 403 | OpenAPI 无有效调用者，或 `user_id` 与已验证的真人调用者不一致。 |
| 422 | 缺少必填字段、字段类型/枚举错误；OpenAPI 缺少 `user_id` 也返回 422。 |
| 400 | Header 名称/值不合法、指定 Bot 不归本人、同名作用域重叠、Center 环境/协议组合不可用等。 |
| 404 | MCP Center 不存在该 `server_code`。 |
| 502 | MCP Center 不可用，写入校验无法完成。 |

老产品内部接口的业务错误通常为 `{"detail": "..."}`；新产品 OpenAPI 错误使用 `{"code": ..., "message": "...", "data": null, "request_id": "..."}`。新产品可保留 `request_id` 供排查；不要假设两套外层错误格式相同。显式环境/协议组合不可用时，后端拒绝写入，不静默回退。普通 Center 端点按名称逐项合并 user 与 Bot Header；Bot 自定义 URL 仅使用 Bot 显式 Header，不继承 user/default Header。

## 与原有 user-only 接口的关系

现有 `/api/mcp/user/config` 和 `/openapi/v1/bots/mcp/servers/{server_code}/config` 保持原合同，仍用于 user-only 配置，不是这张“生效 Bot”表单的接口。老产品页若原本调用 `/config`，需在此表单迁到内部 `/api/mcp/config-groups`；新产品页使用 OpenAPI `/config-groups`。这两套配置组接口共用同一份控制面数据，任一页面保存后，另一页面重新读取会看到更新后的显式规则。
