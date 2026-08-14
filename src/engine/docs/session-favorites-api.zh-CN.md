# 会话收藏接口

## 适用范围

接口由 Engine Adapter 统一提供，和 OpenClaw、Hermes、Claude Code 等具体引擎无关。前端仍通过当前 Bot 对应的 Engine Proxy 调用，不需要直连具体引擎。

收藏仅保存会话键和用户标识，不复制标题、消息或其他会话内容。查询时由 Adapter 读取当前引擎的真实会话数据，因此响应与 `/api/sessions` 的会话结构一致。

## 作用域

- 收藏按 `user_id` 隔离。
- `user_id` 为必填 query 参数，前端传入当前登录用户的 ID。
- 同一用户收藏同一会话是幂等操作。
- 取消一个不存在的收藏也是幂等操作，仍返回成功。
- 删除会话成功后，Adapter 会同步清理该会话的全部收藏记录。

## 会话键编码

收藏和取消收藏使用 path 参数 `session_id`。前端应复用现有会话接口的 URL-safe Base64 编码方式：

```ts
const encodedSessionId = encodeToUrlSafeBase64(sessionId);
```

例如原始会话键：

```text
agent:main:session:2d20edc1-2f84-4524-8486-15bbd7078d42:user:165137
```

不要手工截断、拼接或按引擎类型转换会话键；直接使用会话列表返回的 `id`。

## 接口

### 查询已收藏会话

```http
GET /api/session-favorites?user_id={user_id}
```

响应：

```json
{
  "success": true,
  "data": [
    {
      "id": "agent:main:session:xxx:user:165137",
      "title": "会话标题",
      "user_id": "165137",
      "agent_id": "main",
      "model": "qwen-plus",
      "permission_mode": "default",
      "cwd": "/home/admin/.openclaw/workspace",
      "gmt_created": "2026-07-21T10:00:00",
      "gmt_modified": "2026-07-21T10:30:00",
      "message_count": 12,
      "runtime": "openclaw",
      "last_message": {
        "id": "message-001",
        "session_id": "agent:main:session:xxx:user:165137",
        "role": "assistant",
        "content": "这是最后一条回复。",
        "metadata": {
          "model": "qwen-plus"
        },
        "gmt_created": "2026-07-21T10:30:00",
        "history_meta": {
          "summary": "会话摘要"
        }
      },
      "ext_info": {
        "source": "web"
      }
    }
  ],
  "total": null,
  "message": null,
  "warning": null
}
```

`data` 为完整会话对象，字段与 `GET /api/sessions` 返回的单条会话一致。`last_message` 存在时，其字段为 `id`、`session_id`、`role`、`content`、`metadata`、`gmt_created`，并可能包含 `history_meta`。

`runtime`、`last_message`、`ext_info` 仅在引擎返回对应数据时出现；其他字段始终存在，但部分字段值可能为 `null` 或空字符串。接口只返回仍存在的会话；已删除或已不存在的收藏不会出现在结果中。

支持与会话列表相同的分页和 Agent 筛选参数：

```http
GET /api/session-favorites?user_id={user_id}&agent_id={agent_id}&limit=20&offset=0
```

分页针对“已收藏”会话集合生效。前端直接用 `data` 渲染“已收藏”列表；不要以该接口的返回顺序影响“全部”列表排序。

### 收藏会话

```http
PUT /api/session-favorites/{encoded_session_id}?user_id={user_id}
```

响应：

```json
{
  "success": true,
  "data": null,
  "message": "Session favorited",
  "warning": null,
  "total": null
}
```

重复调用不会重复创建收藏记录，仍返回 `200`。

### 取消收藏

```http
DELETE /api/session-favorites/{encoded_session_id}?user_id={user_id}
```

响应：

```json
{
  "success": true,
  "data": null,
  "message": "Session unfavorited",
  "warning": null,
  "total": null
}
```

即使会话原本未被当前用户收藏，也返回 `200`。

## 错误处理

`user_id` 缺失或为空时，三个接口均返回：

```http
422 Unprocessable Entity
```

SQLite 读写异常时返回：

```http
500 Internal Server Error
```

前端可沿用现有 Engine API 的统一错误处理逻辑。

## 前端接入建议

1. 获取“全部”会话时，维持既有 `/api/sessions` 或 AICoding 会话列表调用和排序逻辑。
2. 同时调用收藏查询接口，得到收藏 session key 集合。
3. 用该集合过滤原始会话列表，渲染“已收藏”列表；不要改变“全部”列表顺序。
4. 用户点击收藏或取消收藏后，调用写接口并更新本地收藏集合，或重新拉取收藏列表。
