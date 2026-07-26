# Mock Model Server MVP 规格

状态：已确认，sender-based 修订已完成
日期：2026-07-24

## 1. 背景与目标

BCS E2E 需要验证以下真实链路：

```text
用户发送消息
  -> BCS 路由
  -> 真实 OpenClaw 接收
  -> OpenClaw 调用模型
  -> OpenClaw 返回回复
  -> BCS 接收并展示回复
```

直接依赖真实大模型会引入随机性、耗时、网络和密钥依赖。因此实现一个仅供本地测试和 CI
使用、非生产系统使用的 OpenAI-compatible Mock Model Server。

MVP 保持真实 BCS 和真实 OpenClaw，只替换模型服务。Mock Server 不调用外部模型、网络
服务或公司内部服务。

## 2. MVP 范围

### 2.1 必须支持

- 监听本机 loopback 地址，默认端口 `18080`。
- 提供 OpenAI-compatible `POST /v1/chat/completions`。
- 提供 Singlebox 生命周期健康检查 `GET /health`。
- 返回单个非流式 JSON `chat.completion`。
- 从 OpenClaw 最新一条 user 消息自带的 sender metadata 中提取 sender label。
- 使用固定句式回复 sender，并带 Server 当前 UTC 时间。
- 请求带 `"stream": true` 时仍返回普通 JSON，不返回 SSE。
- 无外部 Python 依赖。
- 只有 `SINGLEBOX_MODEL_CONFIG_MODE=mock` 时，Singlebox 才配置、启动和管理该进程。

### 2.2 明确不做

- 不模拟 OpenClaw。
- 不要求调用方准备、生成或传入 request ID/UUID。
- 不通过输入哈希关联请求。
- 不实现 SSE、tool calling、reasoning、图片、音频或故障注入。
- 不新增或修改 BCS 业务接口。
- 不把 Mock Server HTTP 测试等同于完整 BCS E2E 回复闭环。

## 3. 接口契约

### 3.1 健康检查

请求：

```http
GET /health
```

成功响应必须精确为：

```json
{
  "status": "ok",
  "service": "singlebox-mock-model"
}
```

该接口只属于 Singlebox 生命周期管理，不属于 OpenAI-compatible 模型能力。

### 3.2 OpenAI-compatible completion

当前真实 OpenClaw 会把 BCS sender 信息放入最新一条 user 消息文本。`label` 由
OpenClaw 生成，通常同时包含显示名和 actor ID：

````text
Sender (untrusted metadata):
```json
{
  "label": "Apple (human_001)",
  "id": "human_001"
}
```

@研发 hi
````

Mock Server 从该 metadata JSON 的 `label` 字段提取 sender label，并原样放入回复。它
不会读取 `id` 后自行拼接 actor ID。成功回复正文固定为：

```text
[from OpenAI-compatible Mock Model Server]: Hi, <sender label>, now time is <UTC time>
```

例如：

```text
[from OpenAI-compatible Mock Model Server]: Hi, Apple (human_001), now time is 2026-07-24T05:16:26Z
```

完整响应保持 OpenAI-compatible `chat.completion` 结构：

```json
{
  "id": "mock-sender-reply",
  "object": "chat.completion",
  "created": 1784870186,
  "model": "singlebox-mock",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "[from OpenAI-compatible Mock Model Server]: Hi, Apple (human_001), now time is 2026-07-24T05:16:26Z"
      },
      "finish_reason": "stop"
    }
  ]
}
```

时间使用 Server 生成回复时的 UTC ISO 8601 秒精度：
`YYYY-MM-DDTHH:mm:ssZ`。

## 4. Sender 提取规则

1. `messages` 必须是数组。
2. Mock Server 从后向前找到最新一条 `role=user` 消息；历史 user 消息全部忽略。
3. 同时支持 `content` 为字符串，以及数组中的 `type=text` 字符串文本块。
4. 图片及其他 content block 忽略。
5. 在最新 user 消息中查找第一个由 OpenClaw 注入的：

   ````text
   Sender (untrusted metadata):
   ```json
   {...}
   ```
   ````

6. JSON 必须是对象，`label` 必须是去除首尾空白后非空、长度不超过 128 且不含换行的字符串。
7. 不读取历史 assistant 回复，不读取顶层自定义字段，也不从自然语言猜测 sender。

只读取最新 user 消息可以支持同一会话连续请求，避免旧 sender 或旧回复干扰当前响应。

缺少或无法解析 sender 时返回 HTTP 400：

```json
{
  "error": "missing_sender",
  "timestamp": "2026-07-24T05:16:26Z"
}
```

`timestamp` 是错误响应生成时的 UTC ISO 8601 秒精度时间。

## 5. 非流式兼容行为

当前 OpenClaw 会发送 `"stream": true`。MVP 已确认：

- Server 仍返回 `Content-Type: application/json` 的完整 completion；
- Server 不返回 `text/event-stream`；
- 以当前受支持 OpenClaw 能接受该兼容响应为前提；
- 如果未来 OpenClaw 不再兼容，应显式报错并重新评估，不静默扩展 SSE。

## 6. Singlebox 接入

`SINGLEBOX_MODEL_CONFIG_MODE=mock` 时生成本地 provider：

```json
{
  "models": {
    "mode": "merge",
    "providers": {
      "singlebox-mock": {
        "baseUrl": "http://127.0.0.1:18080/v1",
        "apiKey": "singlebox-local",
        "api": "openai-completions",
        "models": [
          {
            "id": "singlebox-mock",
            "name": "singlebox-mock",
            "input": ["text"]
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "singlebox-mock/singlebox-mock"
      }
    }
  }
}
```

- 默认端口为 `18080`，`SINGLEBOX_MOCK_MODEL_PORT` 可覆盖。
- 不允许配置远程默认地址。
- `manual`、`home` 或其他模式不得启动或停止 Mock Server。
- `setup` 只生成配置。
- `start` 在 OpenClaw bot 前启动并等待精确健康响应。
- `restart` 确保 Mock Server 可用后再重启服务。
- `stop` 只停止 PID 与命令身份均匹配的 owned process。
- 已有精确健康服务时复用，但不取得进程所有权。
- 端口被其他服务占用时启动失败，不自动换端口。

## 7. E2E 使用方式

测试者或测试代码只发送正常业务消息：

```text
@研发 hi
```

OpenClaw BCN Plugin 把 BCS 的 `actor_name` 和 `actor_id` 映射为 OpenClaw sender
metadata。Mock Server 回复：

```text
[from OpenAI-compatible Mock Model Server]: Hi, Apple (human_001), now time is <UTC time>
```

后续 BCS E2E 应通过 BCS 对外契约读取回复并断言：

- 回复来自被点名的真实 OpenClaw Bot；
- 回复正文包含 OpenClaw 生成的 sender label；
- 回复符合固定前缀和 UTC 时间格式；
- 最终状态完成；
- 消息被正确持久化或广播。

Sender 名称不保证一次请求的全局唯一性。MVP 的目标是验证真实 OpenClaw 确实调用模型并
把回复送回 BCS，不再要求通过 UUID 精确关联每一次请求。如后续用例需要并发请求的强
关联，应在该 E2E 场景中另行设计关联机制，不恢复为所有调用方必须手工准备 UUID。

## 8. 验收标准

### AC-01 协议

- 合法 OpenClaw sender metadata 返回 HTTP 200。
- `choices[0].message.role` 为 `assistant`。
- `finish_reason` 为 `stop`。
- 回复精确符合：
  `[from OpenAI-compatible Mock Model Server]: Hi, <sender label>, now time is <UTC time>`。
- `stream=true` 仍返回普通 JSON。

### AC-02 Sender

- `label=Apple (human_001)` 时回复包含 `Hi, Apple (human_001)`。
- `label=研发 (bot_11b77a19)` 时回复包含 `Hi, 研发 (bot_11b77a19)`。
- 中英文 sender label 都原样回复。
- 只使用最新一条 user 消息。
- 历史 sender、assistant 回复、图片 block 不影响结果。
- 缺少、损坏或空 sender 返回 HTTP 400 `missing_sender` 和 UTC 时间戳。

### AC-03 Singlebox

- 只有 `SINGLEBOX_MODEL_CONFIG_MODE=mock` 管理 Mock Server。
- 默认和覆盖端口生成正确本地 provider。
- 重复 start 不产生第二个进程。
- stop 不误杀身份不匹配进程。

### AC-04 回归

- `python3 scripts/modules/test_mock_model_server.py` 通过。
- `bash scripts/test_singlebox_model_config.sh` 通过。
- 相关 shell 脚本语法检查通过。
- `git diff --check` 通过。

## 9. 决议变更

早期版本要求调用方在消息中放入 `request_id=req_<UUID v4>`。真实 UI 验证表明，该规则
使普通测试者必须准备 UUID，并且扫描整个历史会增加多轮测试复杂度。

已确认用以下新决议替代旧 request ID 决议：

- 不再要求或解析 request ID/UUID。
- 使用 OpenClaw 已自动注入的 sender metadata。
- 只读取最新 user 消息。
- 回复 sender 名称和当前 UTC 时间。
- 缺失 sender 返回 `missing_sender`。

## 10. 实现边界

Mock Server 位于 scripts/test bootstrap，不进入 BCS core。OpenClaw BCN Plugin 只把
BCS 已有的 `actor_name` 和 `actor_id` 映射到 OpenClaw sender metadata，不改变 BCS
Service API、Plugin API 或消息协议。

Human Actor 历史名称同步、BCS group-flow sender 语义等通用修复不属于本次提交。完整
BCS → OpenClaw → Mock Model → BCS E2E 仍是后续独立任务，Server HTTP 测试不能替代
该闭环。
