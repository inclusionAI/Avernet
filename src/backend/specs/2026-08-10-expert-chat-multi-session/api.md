# 我的互动多会话前端接入说明

## 1. 功能范围

本次改造支持同一个登录用户与同一个公开 Bot 创建和管理多个会话。

- Backend 新增多会话列表、新建、连接和删除接口。
- 搜索只支持按完整 `session_key` 精确查询。
- “全部”和“已收藏”列表均由新的 Backend 会话列表接口提供。
- 收藏和取消收藏继续复用现有 Engine Proxy 接口。
- 消息历史加载、WebSocket 连接和消息发送继续沿用现有聊天链路。
- OpenClaw、Hermes、AI Coding、Claude Code 在本次支持范围内。
- Teclaw 不在本次需求范围内。

Backend 接口统一前缀：

```text
/api/v1/expert-chats/{bot_id}/{owner_id}
```

路径参数：

| 参数 | 说明 |
| --- | --- |
| `bot_id` | 当前 Bot ID |
| `owner_id` | Bot 所有者工号，不一定是当前登录用户 |

当前用户身份由 Backend 从登录态获取，多会话 Backend 接口不接受前端传入
`user_id` 覆盖当前用户身份。

## 2. 发布前置

1. 执行同目录下的 `ddl.sql`，创建 `ac_expert_chat_owned_sessions` 表。
2. 发布 Backend。
3. Adapter 无需随本需求升级，本实现复用现有 `/api/sessions` 和
   `/api/session-favorites` 接口。

旧版前端继续调用单数 `/session` 接口时，行为保持不变。Backend 会将旧接口
当前使用的 session key 自动登记到新表，因此新旧前端可以并行使用。

## 3. 查询会话列表

```http
GET /api/v1/expert-chats/{bot_id}/{owner_id}/sessions
```

### 3.1 查询参数

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `session_key` | 否 | - | 按完整 session key 精确查询，长度不超过 255 |
| `favorite_only` | 否 | `false` | `true` 时只返回已收藏会话 |
| `limit` | 否 | `20` | 每页数量，范围 1～100 |
| `offset` | 否 | `0` | 分页偏移量，必须大于或等于 0 |

查询全部会话：

```http
GET /api/v1/expert-chats/20260807_ks811fu2/165137/sessions?limit=20&offset=0
```

按 session key 精确搜索：

```http
GET /api/v1/expert-chats/20260807_ks811fu2/165137/sessions?session_key=agent%3Amain%3Asession%3Axxx%3Auser%3A165137
```

查询已收藏会话：

```http
GET /api/v1/expert-chats/20260807_ks811fu2/165137/sessions?favorite_only=true&limit=20&offset=0
```

`session_key` 作为 query 参数时，应交给请求库进行 URL 编码，不要修改、截断或
按引擎类型转换原始值。

### 3.2 成功响应

```json
{
  "success": true,
  "message": "获取成功",
  "error_code": 0,
  "data": {
    "total": 2,
    "items": [
      {
        "id": "agent:main:session:2d20edc1:user:165137",
        "title": "代码审计与优化",
        "user_id": "165137",
        "agent_id": "20260807_ks811fu2",
        "model": "claude-sonnet-4-5",
        "permission_mode": "default",
        "cwd": "/home/admin/workspace",
        "gmt_created": "2026-08-10T10:00:00Z",
        "gmt_modified": "2026-08-10T10:30:00Z",
        "message_count": 8,
        "runtime": "openclaw",
        "last_message": {
          "id": "message-001",
          "session_id": "agent:main:session:2d20edc1:user:165137",
          "role": "assistant",
          "content": "审计完成，发现两个可优化点。",
          "metadata": {
            "model": "claude-sonnet-4-5"
          },
          "gmt_created": "2026-08-10T10:30:00Z",
          "history_meta": {
            "summary": "会话摘要"
          }
        },
        "ext_info": {
          "source": "web"
        }
      }
    ]
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `data.total` | 当前筛选条件下的会话总数，不是当前页数量 |
| `data.items[].id` | 原始 session key，后续连接、删除和收藏操作均以此为准 |
| `title` | 会话标题 |
| `user_id` | 会话所属用户 |
| `agent_id` | 会话所属 Bot/Agent |
| `model` | 会话模型，可能为 `null` |
| `permission_mode` | 权限模式，可能为 `null` |
| `cwd` | 会话工作目录，可能为 `null` |
| `gmt_created` | 会话创建时间 |
| `gmt_modified` | 会话最近更新时间，用于列表倒序展示 |
| `message_count` | 消息数量 |
| `runtime` | 引擎类型，仅在引擎返回时存在 |
| `last_message` | 最后一条消息，无消息或引擎不可用时为 `null` |
| `ext_info` | 引擎扩展信息，仅在引擎返回时存在 |

不同引擎返回的扩展字段可能不同。前端应依赖上述公共字段进行列表渲染，并允许
`runtime`、`ext_info`、`last_message` 的扩展字段缺失。

### 3.3 容器不可用时的占位数据

当 Backend 中仍存在合法会话归属，但容器正在启动或 Adapter 暂不可用时，
会话不会从列表中消失，而是返回字段完整的占位数据：

```json
{
  "id": "agent:main:session:xxx:user:165137",
  "title": "新会话",
  "user_id": "165137",
  "agent_id": "20260807_ks811fu2",
  "model": null,
  "permission_mode": null,
  "cwd": null,
  "gmt_created": "2026-08-10T10:00:00Z",
  "gmt_modified": "2026-08-10T10:00:00Z",
  "message_count": 0,
  "last_message": null
}
```

前端不要因为收到占位数据而删除本地会话。后续重新查询时，Backend 会继续尝试
从 Adapter 补齐会话元数据。

当 Caller 独立容器仍在启动时，列表响应的 `data` 中还可能包含：

```json
{
  "total": 2,
  "items": [],
  "need_poll": true
}
```

此时前端应沿用现有 Bot 启动轮询逻辑。

## 4. 新建会话

```http
POST /api/v1/expert-chats/{bot_id}/{owner_id}/sessions
```

无需请求体。每次调用都会创建一个全新的会话，不会删除或覆盖之前的会话；新会话
同时会成为旧版单会话接口的默认会话。

### 4.1 成功响应

```json
{
  "success": true,
  "message": "创建成功",
  "error_code": 0,
  "data": {
    "session_key": "agent:main:session:xxx:user:165137",
    "is_new": true,
    "connection": {
      "type": "websocket",
      "target": "127.0.0.1:20003",
      "token": null,
      "engine_type": "openclaw",
      "url": "wss://example.com/ws",
      "headers": null,
      "use_proxy": false,
      "sandbox_id": "ARCA-SANDBOX-xxx"
    }
  }
}
```

`connection` 的具体字段随现有 Bot 连接类型变化，前端继续使用当前聊天页面已有的
连接逻辑处理该对象。

### 4.2 容器启动中

```json
{
  "success": true,
  "message": "创建成功",
  "error_code": 0,
  "data": {
    "session_key": null,
    "is_new": true,
    "connection": {},
    "need_poll": true
  }
}
```

收到 `need_poll=true` 时，尚未创建 session。前端应等待容器就绪后重新调用新建
接口，不能将 `session_key=null` 加入会话列表。

## 5. 进入已有会话

```http
POST /api/v1/expert-chats/{bot_id}/{owner_id}/sessions/connect
Content-Type: application/json
```

请求体：

```json
{
  "session_key": "agent:main:session:xxx:user:165137"
}
```

成功响应：

```json
{
  "success": true,
  "message": "获取成功",
  "error_code": 0,
  "data": {
    "session_key": "agent:main:session:xxx:user:165137",
    "is_new": false,
    "connection": {
      "type": "websocket",
      "target": "127.0.0.1:20003",
      "token": null,
      "engine_type": "openclaw",
      "url": "wss://example.com/ws",
      "headers": null,
      "use_proxy": false,
      "sandbox_id": "ARCA-SANDBOX-xxx"
    }
  }
}
```

Backend 会先校验该 session key 是否属于当前登录用户，再返回连接信息。前端选择
已有会话时，应调用本接口获取连接，不能只根据列表中的 session key 直接建立连接。

Caller 独立容器启动中时，响应可能包含 `need_poll=true`。此时前端应完成现有
轮询流程后，再次调用本接口。

## 6. 删除指定会话

```http
DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/sessions?session_key={session_key}
```

成功响应：

```json
{
  "success": true,
  "message": "Session 已删除",
  "error_code": 0,
  "data": null
}
```

删除会同步清理：

- Adapter 中的真实会话；
- Adapter 中的收藏记录；
- Backend 中当前用户的会话归属记录；
- 旧版默认会话指针。

如果删除的是旧版默认会话，Backend 会将默认指针切换到剩余会话；如果没有剩余
会话，则清空默认指针。

前端应等待接口返回成功后再从列表移除该项。删除失败时不要只在前端隐藏会话，应
保留该项并允许用户重试。

## 7. 收藏与取消收藏

收藏写操作继续通过当前 Bot 的 Engine Proxy 调用现有接口：

### 7.1 收藏

```http
PUT /api/session-favorites/{encoded_session_id}?user_id={current_user_id}
```

成功响应：

```json
{
  "success": true,
  "data": null,
  "message": "Session favorited",
  "warning": null,
  "total": null
}
```

### 7.2 取消收藏

```http
DELETE /api/session-favorites/{encoded_session_id}?user_id={current_user_id}
```

成功响应：

```json
{
  "success": true,
  "data": null,
  "message": "Session unfavorited",
  "warning": null,
  "total": null
}
```

`encoded_session_id` 必须使用当前项目已有的 URL-safe Base64 编码方法生成，原始值
直接取会话列表返回的 `item.id`：

```ts
const encodedSessionId = encodeToUrlSafeBase64(session.id);
```

不要手工截断、拼接或按引擎类型转换 session key。收藏和取消收藏均为幂等操作。

“已收藏”标签页不要直接使用 Adapter 收藏列表接口渲染，而是调用新的 Backend
列表接口：

```http
GET /api/v1/expert-chats/{bot_id}/{owner_id}/sessions?favorite_only=true
```

这样 Backend 会再次按照当前登录用户、Bot、Bot Owner 和 session key 校验会话归属。

## 8. 统一错误响应

Backend 多会话接口使用统一响应结构。业务错误通常也通过该结构返回，前端不能只
判断 HTTP 状态码，还必须判断 `success` 和 `error_code`：

```json
{
  "success": false,
  "message": "Session不存在或不属于当前用户",
  "error_code": 404,
  "data": null
}
```

常见错误码：

| `error_code` | 说明 |
| --- | --- |
| `0` | 成功 |
| `400` | Bot 状态不可用或请求不符合要求 |
| `4001` | Bot 尚未发布 |
| `403` | 当前用户没有访问权限 |
| `404` | Bot/session 不存在，或 session 不属于当前用户 |
| `5001` | Bot 连接不可用或仍在启动 |
| `5003` | 创建 session 失败 |
| `5999` | Backend 未预期异常 |

身份认证失败仍可能由统一认证中间件直接返回 HTTP `401` 或 `403`。

## 9. 推荐前端调用流程

1. 用户选择左侧 Bot，调用会话列表接口加载“全部”会话。
2. 用户切换“已收藏”，调用同一列表接口并传 `favorite_only=true`。
3. 用户输入完整 session key，调用同一列表接口并传 `session_key`。
4. 用户点击“新建会话”，调用新建接口。
5. 新建成功后，使用响应中的 `session_key` 和 `connection` 进入聊天。
6. 用户点击已有会话，调用 `sessions/connect` 获取连接信息后进入聊天。
7. 消息历史加载、WebSocket 建连和消息发送沿用当前聊天实现。
8. 用户收藏或取消收藏时，调用现有 Engine Proxy 收藏写接口。
9. 用户删除会话时，调用 Backend 指定会话删除接口；成功后再刷新列表。

## 10. 兼容性说明

- 原有 `POST /api/v1/expert-chats/{bot_id}/{owner_id}/session` 保留，旧前端不修改
  时行为不变。
- 原有 `DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session` 保留。
- 新旧前端并行期间，旧接口当前使用的 session key 会自动登记到多会话表。
- 新建或进入会话时，Backend 会更新旧版默认会话指针，避免回到旧页面后打开错误
  会话。
- 前端不需要修改 Adapter 的调用协议，也不需要为不同引擎实现不同的多会话逻辑。
