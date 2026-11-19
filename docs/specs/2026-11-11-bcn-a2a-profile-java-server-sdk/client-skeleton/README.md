# A2A Agent Client 骨架（评审用）

按 AgentCard 调用任意标准 A2A Agent 的 Java 客户端，和服务端的 A2A Agent Adapter 是一对：Adapter 把业务 Agent 暴露成 A2A 服务，Client 去调用 A2A 服务。两边都把协议细节藏起来，payload 都按《BCN A2A Payload 规范》（同目录 `bcn-a2a-payload.md`）。

- 可以编译，测试通过（`mvn verify`，JDK 17+）：用 in-process gRPC 和 JDK HttpServer 模拟 Agent，覆盖三种绑定的主流程。
- 主流程已实现；公司内部细节和待定的规范细节以 `TODO` 标出。
- groupId 和包名 `a2a.agent.client` 是占位。

## 业务怎么用

```java
A2aAgentClient client = A2aAgentClient.builder()
        .standardTransports("127.0.0.1:50002")                        // 本地 MOSN 地址
        .callerContext(() -> CallerContext.ofUser(currentUserId()))   // 可选：生成 bcn_caller
        .build();                                                     // 一个应用一个

RemoteAgent agent = client.agent(cardJson);                           // 缓存起来复用

Reply reply = agent.send("订单 1001 发货了吗？");                       // 一次性
agent.stream("写一份杭州两日游", new StreamListener() {                 // 流式
    public void onText(String delta) { … }
    public void onTool(ToolRecord record) { … }
    public void onComplete(Reply reply) { … }
    public void onError(A2aCallException e) { … }
});
Conversation conv = agent.conversation();                             // 多轮，自动沿用 contextId
```

业务接触的类型只有：`A2aAgentClient`、`RemoteAgent`、`AgentRequest`、`Reply`、`StreamListener`、`A2aCallException`。需要完全自己控制时，`agent.raw()` 返回标准 5 个方法的低层接口（收发 protobuf）。

## 一次调用的流程

| 步骤 | 位置 | 说明 |
|---|---|---|
| ① 获取 Card | `card.AgentCards` / `card.CardLoader` | JSON 直接传入，或从 URL 拉取（带缓存）。本期不支持按 Agent 名从 BCS 查询 |
| ② 校验、选接口 | `card.InterfaceSelector` | required 扩展必须支持；按 Card 顺序选第一个“有传输、版本 1.0、地址通过校验”的接口；选定后不悄悄换接口。规则同 BCS |
| ③ 构造请求 | `internal.RequestFactory` | 直接构造 protobuf：messageId、文本 Part、tenant、contextId、metadata（业务字段 + `bcn_caller`）、扩展请求头 |
| ④ 发起调用 | `transport.*` | SOFA-GRPC（经本地 MOSN）、GRPC（直连）、HTTP+JSON、JSONRPC |
| ⑤ 处理响应 | `internal.ResponseProjector` | 首帧检查、按 append 规则拼文本、只认显式终态、解码工具记录。规则同 BCS |
| ⑥ 流的编排 | `internal.StreamCall` | 背压、整体时限、回调串行化、断流后用 GetTask 恢复一次 |
| ⑦ 错误分类 | `internal.ErrorClassifier` | 区分“确定没执行”（REJECTED、BUSY）和“结果未知”（TIMEOUT、UNKNOWN_OUTCOME）；发送消息不自动重试 |

## 性能设计

| 做法 | 位置 |
|---|---|
| 连接复用：SOFA-GRPC 整个应用只有一个连本地 MOSN 的 Channel；直连 GRPC 按地址缓存 Channel；HTTP 共用一个 HttpClient（HTTP/2 + 连接池） | `MeshGrpcTransport`、`DirectGrpcTransport`、`HttpClients` |
| `RemoteAgent` 线程安全、可复用：Card 解析、接口选择、路由头只在创建时算一次；从 URL 获取时 Card 不变就返回同一实例 | `RemoteAgent`、`A2aAgentClient.agent(URI)` |
| Card 缓存：命中不发请求；同一 URL 并发只拉一次；过期先返回旧值、后台刷新 | `CardLoader` |
| 全异步：底层全部非阻塞（gRPC 异步 stub、`HttpClient.sendAsync`），同步方法只是等待结果；不为调用创建线程 | `TransportEndpoint`、`sendAsync` |
| 少转换：请求直接构造 protobuf，响应直接在 protobuf 上处理；三种绑定解析成同一套 protobuf，共用一条处理路径；不转换成 a2a-java POJO | `RequestFactory`、`ResponseProjector`、`HttpSupport` |
| 流式背压：gRPC 手动流控、SSE 按需读取，处理完一个事件才读下一个，慢消费者不会让事件堆积 | `GrpcEndpoint`、`SseSubscriber`、`StreamCall` |
| 回调线程可配置：默认在 IO 线程回调；耗时处理可配置业务线程池，仍保证顺序 | `ClientOptions.callbackExecutor`、`SerialRunner` |
| 并发上限：可选的在途调用上限，满了直接以 BUSY 失败，不排队 | `CallLimiter` |
| messageId 用 ThreadLocalRandom 生成，避免 SecureRandom 的锁竞争 | `Ids` |
| 静态请求头（版本、路由）在打开端点时构造一次，每次调用只追加 deadline 和扩展头 | `GrpcEndpoint` |

## TODO 索引

| 标记 | 数量 | 内容 |
|---|---|---|
| `TODO(内部)` | 10 | 公司内部细节：MOSN 路由头确认、Channel 参数、地址和应用名白名单、HttpClient 代理和证书、依赖是否由基座提供、鉴权要求的处理 |
| `TODO(实现)` | 10 | 规则已定待补的代码：日志与计数、GetTask 退避重试、形态 C 断流后改用 SubscribeToTask、一元调用的真正取消、Card 边读边限大小、URL 路径编码 |
| `TODO(规范)` | 3 | 待和规范对齐：HTTP+JSON 错误体格式、JSON-RPC 错误中 ErrorInfo 的位置、SubscribeToTask 用 GET 还是 POST |
| `TODO(待定)` | 1 | HTTP 请求的 Content-Type 用 `application/json` 还是 `application/a2a+json` |
| `TODO(性能)` | 1 | JSON-RPC 的 result 多一次序列化，压测确认是热点后改为流式解析 |

## 和服务端骨架的对齐

- toolcall 扩展 URI 两边必须一致，目前都是占位的 `urn:bcn:a2a:extension:tools:v2`。
- 客户端按 Payload 规范使用 `bcn_caller`；服务端骨架（`sdk-skeleton`）还是旧的 `bcn.caller`，以后改服务端时要对齐。
