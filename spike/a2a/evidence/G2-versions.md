# G2 版本锁定（2026-10-13）

a2a-sdk==1.2.2（声明基线 A2A 1.0.x；spec 抓取日 2026-10-13）

## Python 依赖锁定（requirements-spike.txt，7 项关键依赖命中）

- a2a-sdk==1.2.2
- fastapi / uvicorn / httpx / websockets / pytest / pytest-asyncio（版本见 freeze 文件）

## SDK client API 面备忘（normalize 实现依据，来自 1.2.2 源码）

类型体系 = protobuf 生成类（`a2a.types.a2a_pb2`），字段 snake_case 属性访问
（`status_update`、`last_chunk`、`context_id`、`supported_interfaces`）。

### 客户端构造（Card 驱动，禁手工端点）
```python
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
card = await A2ACardResolver(httpx_client=http, base_url=BASE).get_agent_card()
# card.supported_interfaces: repeated AgentInterface{url, protocol_binding, tenant, protocol_version}
factory = ClientFactory(ClientConfig(streaming=True, httpx_client=http))
client = factory.create(card)   # 由 card 选定 transport——自动选 JSONRPC 绑定
```

### 方法（Client，1.2.2 实际 API——注意与 v0.x 文档风格不同）
- `send_message(request: SendMessageRequest) -> AsyncIterator[StreamResponse]`——
  自动选择流式或非流式（非旧版 `send_streaming_message(payload=...)`）
- `subscribe(request: SubscribeToTaskRequest) -> AsyncIterator[StreamResponse]` —— 重订阅
- `get_task(request) -> Task`、`cancel_task(request) -> Task`

### 消息构造
```python
from a2a.helpers.proto_helpers import new_text_message
from a2a.client import SendMessageRequest
msg = new_text_message("hello", context_id="ctx-a2a")  # Part text + role=user
req = SendMessageRequest(message=msg)
```

### StreamResponse（OneOf 四选一，WhichOneof 判定）
- `task` → Task{id, context_id, status, artifacts, history, metadata}
- `message` → Message{message_id, context_id, task_id, role, parts, metadata}
- `status_update` → TaskStatusUpdateEvent{task_id, context_id, status, metadata}
  —— 无 `final` 字段（ protobuf 类型不带；SSE 语义以流关闭为终态信号；服务端
  侧我们另在 metadata 携带 seq/lastSeq —— 客户端从 metadata 读）
- `artifact_update` → TaskArtifactUpdateEvent{task_id, context_id, artifact, append, last_chunk, metadata}

### 判定方式
`response.WhichOneof("response")`（StreamResponse 的 OneOf 字段名——
需要打印实际 DESCRIPTOR 确认，实现时首先验证）。

## 判定: SDK 主路径可用
