# BCN A2A Java SDK 骨架（评审用）

这是 SDK 的结构骨架，用来评审分层和接口是否合理，不是可上线的实现。

- 可以编译，示例测试通过（`mvn verify`，JDK 17+）。
- 主流程已经打通：按 tenant 分发、两种档位、流式事件顺序、错误映射、启动校验。
- 策略细节（完整校验规则、背压、超时、SOFA 代理路径）以 `TODO` 注释标出，写清楚了要做什么。
- groupId 和包名 `bcn.a2a`、扩展 URI 都是占位。

## 设计要点

1. **业务只写业务逻辑。** 最简接入是一个类、一个方法；协议细节全部由 SDK 负责。
2. **三档接入，逐档增加。**

   | 档位 | 业务实现 | 适用 |
   |---|---|---|
   | 一次性回复 | `onMessage` | 大多数 Agent |
   | 流式输出 | `onStream` | 大模型逐段输出，业务不存状态。Task 只存在于这条流内 |
   | 引擎模式 | `onStream` + `TaskEngine` | 需要查询、订阅、取消、续交 |

3. **一个应用只发布一个标准 A2A 服务**（`A2aService`），按请求 tenant 分发到各个 `@A2aAgent`。
4. **SDK 不做 SOFABoot 自动装配。** 服务发布、线程池、调用链、指标、配置都由业务按自己的方式完成；SDK 只提供必须统一的部分：适配对象、发布规范的固定值、（待补的）SOFA Triple 生成代码。
5. **AgentCard 由平台方注册**，SDK 不生成 Card。平台注册的 Card 中，接口项的 tenant 应等于 `@A2aAgent.id`。
6. **SDK 不保存任何 Task 状态。** 引擎模式下状态的唯一来源是引擎的运行记录。
7. **本期只有一个扩展：工具观察（toolcall）。** 只在流式档位支持，调用方没有激活时 `BcnStream.tool` 自动丢弃。调用上下文、人机交互（hitl）、业务扩展声明本期都不做；本期不允许进入 INPUT_REQUIRED。
8. **metadata 保留键用 `bcn_` 前缀。** `bcn`、`bcn_*` 归平台，含冒号的 URI 形式归外部标准扩展，其余归业务；`BcnCall.metadata()` 只给业务字段。

## 目录

```
bcn-a2a-sdk/                     SDK 本体。不依赖 Spring；grpc / protobuf 由基座提供（provided）
  spi/      业务接口
    BcnAgent            业务实现的 Agent：onMessage / onStream 二选一
    A2aAgent            注解：id（即 tenant）
    BcnCall             一次调用：文本、业务 metadata、激活的扩展、取消状态
    BcnStream           流式输出：text / data / tool / progress / complete / fail
    TaskEngine          引擎模式：getTask / subscribe / cancel
    A2aListener         日志、指标接入点
    BcnException        业务拒绝
    ToolEvent, Messages
  server/   标准服务与请求处理（业务不直接接触，除了 A2aService 和 A2aServiceOptions）
    A2aService          标准 A2A gRPC 服务，业务发布的对象；final，所有方法收口到流水线
    RequestPipeline     固定顺序的处理流水线（见下）
    AgentRegistry       启动校验 + 按 tenant 查找
    ExtensionNegotiator 扩展协商
    InboundValidator    入站校验
    OutboundGuard       第一档回复的出站检查
    TaskStreamWriter    流式输出的状态机（StreamBridge 发到网络，CollectingStream 汇总成最终 Task）
    ErrorMapper         异常 → gRPC 状态 + google.rpc.ErrorInfo
    CallHeaders         A2A-Extensions / A2A-Version 请求头与响应头
  ext/      保留键规则与扩展 URI（BcnKeys）、工具观察编码（ToolObservationCodec）
  sofa/     发布规范的固定值（SofaPublishSpec）；SOFA 胶水代码的待办见 package-info
bcn-a2a-test/                    测试运行器 A2aAgentTester（不启动端口，经过完整流水线）
bcn-a2a-demo/                    业务接入示例：OrderAgent（第一档）、WritingAgent（第二档）、装配代码、测试
```

## 业务接入

```java
@A2aAgent(id = "order-agent")
public class OrderAgent implements BcnAgent {
    @Override
    public Message onMessage(BcnCall call) {
        return Messages.agentText(orderService.answer(call.text()));
    }
}
```

装配和发布由业务自己写，SOFABoot 下的写法见 `bcn-a2a-demo/.../DemoWiring.java` 的注释。

## 请求处理流程

`RequestPipeline` 对每个请求按固定顺序处理：

1. **开始**：计算截止时间（调用方 deadline 与本地超时取较早者），挂上取消回调，通知监听器。
2. **准备**（在 RPC 线程上同步完成，失败直接返回错误）：大小 → 协议版本 → 转为 a2a-java 模型 → 按 tenant 分发 → 扩展协商 → 入站校验 → 续交检查 → contextId → 回写激活结果。
3. **派发**：在业务线程池中执行一次，不在池内再次提交；并发已满或线程池拒绝时返回 UNAVAILABLE。
4. **执行与出站检查**：第一档检查回复；流式档位由 `TaskStreamWriter` 保证首帧是 Task、终态显式发出、终态之后不再输出。
5. **结束**：错误映射、监听回调，每个调用恰好结束一次。

## TODO 索引

代码中的 `TODO` 分三类：

| 标记 | 含义 | 主要位置 |
|---|---|---|
| `TODO(实现)` | 规则已定，待写代码 | 入站/出站校验细则、背压、unary 超时、工具记录的大小与截断、日志 |
| `TODO(待定)` | 需要先在评审中决定 | 扩展 URI、错误域、协议小版本、正文的 lastChunk、第二档的 returnImmediately |
| `TODO(SOFA)` | 需要先在基座 5.14.1 上验证 | 生成代码、代理调用路径、请求头、异常解包、ErrorInfo 穿透 |

## 构建

```bash
mvn verify
```
