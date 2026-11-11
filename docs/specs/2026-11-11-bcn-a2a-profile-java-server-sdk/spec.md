# BCN A2A Profile 与 Java Server SDK

状态：草案，供评审。更新日期：2026-11-11。

本文定义两件事：公司内 Agent 统一遵循的 **BCN A2A Profile**（A2A 1.0 的子集、行为约束和扩展目录），以及帮助 SOFABoot（JDK 17）平台按该 Profile 提供 A2A Server 的 **Java Server SDK**。

本文是目标合同，不代表已实现。BCS 现有下行行为以 [A2A 接口参考](../../bcn-a2a-api-reference.zh-CN.md) 为准；两者不一致之处在第 9 节列出。

## 1. 背景与目标

BCS 已作为公司级 A2A 注册中心和 A2A Client：Agent 注册 AgentCard，BCS 按 Card 调用。问题在于很多 Agent 没有提供标准 A2A 接口，各平台自行适配的结果互不一致。

目标：

1. 发布一份可检验的 Profile，说明"接入 BCN 的 Agent 必须实现什么、行为必须是什么"。
2. 提供 Java Server SDK，平台只实现少量 SPI 即可得到合规的 A2A Server。
3. 提供一致性测试套件（TCK），Profile 的解释以它为准。

## 2. 范围

本期包含：

- Profile 文本、一致性等级、扩展目录。
- Java Server SDK：协议处理、SPI、SOFABoot starter、跨实例存储 SPI 及参考实现。
- TCK。

本期不包含：

- BCS 对外作为 A2A Server 的能力。
- Java Client SDK。
- 跨实例事件转发、执行迁移、宕机后自动重跑。
- 应用层鉴权（Bearer、OAuth、mTLS、签名断言）。
- A2A 0.3 兼容、Push Notification、ListTasks、文件与多模态输入输出。

## 3. 已确认的决定

| 编号 | 决定 | 后果 |
| --- | --- | --- |
| D1 | 内部调用全部走 RPC，协议不定义额外鉴权 | 信任边界是内部 RPC 网络；Card 固定 `securityRequirements: []`；调用上下文的可信度等同于 RPC 调用方 |
| D2 | 多实例是一等约束 | SDK 的默认行为必须在多实例下正确，不能以单实例为前提 |
| D3 | 多实例只提供跨实例存储的 SPI 和参考实现 | 任务始终在接收它的实例上执行；其他实例只通过共享存储协作，见第 6 节 |
| D4 | 接受 D3 带来的三项降级 | 跨实例订阅为轮询合成；跨实例取消有秒级延迟；审批续交要求引擎状态可从存储恢复 |

## 4. Profile

### 4.1 定位

Profile 不是新协议。合规 Agent 是一个标准 A2A 1.0 Server，任何标准 A2A Client 都能调用它。Profile 只做三件事：选定子集、收紧行为、登记扩展。

BCS 继续兼容不使用 SDK、但符合 Profile 的第三方 Server。

### 4.2 基线

- A2A 版本：`protocolVersion` 为 `"1.0"`，细节按 A2A 1.0.1 规范。
- 必选绑定：`GRPC`（服务 `lf.a2a.v1.A2AService`，经 SOFARPC Triple 和 MOSN）。
- 可选绑定：`HTTP+JSON`、`JSONRPC`，用于本地调试和非 SOFA 技术栈。三种绑定语义一致。
- 方法：`SendMessage`、`SendStreamingMessage`、`GetTask`、`SubscribeToTask`、`CancelTask`。其他标准方法不属于 Profile。
- 输入输出：`text/plain`。

### 4.3 一致性等级

等级逐级包含。Card 通过第 4.6 节的 profile 扩展声明自己达到的等级。

| 等级 | 能力 | 必须实现 |
| --- | --- | --- |
| L0 | 非流式文本 | `SendMessage`（支持 `returnImmediately`）、`GetTask`、`CancelTask` |
| L1 | 流式文本与断流恢复 | L0，加 `SendStreamingMessage`、`SubscribeToTask`，Card 声明 `streaming: true` |
| L2 | 调用上下文 | L1，加调用上下文扩展（第 4.7 节） |
| L3 | 工具观察、exec 审批、提交去重 | L2，加第 4.6 节中声明的可选扩展；三项可分别声明 |

### 4.4 AgentCard 要求

沿用 [Provider AgentCard 管理接口 v0.1](../../bcn-provider-agent-card-api-v0.1.zh-CN.md) 的字段要求，并补充：

- `securityRequirements` 必须为 `[]`，不得声明 `securitySchemes`。
- `supportedInterfaces` 至少包含一个 `GRPC` 接口。
- `defaultInputModes`、`defaultOutputModes` 必须包含 `text/plain`。
- 一个进程承载多个 Agent 时，每个 Agent 使用可区分的接口地址或 `tenant`。

### 4.5 行为约束

以下约束比 A2A 标准更严，是 BCS 现有下行行为的前提。

1. **先返回 Task**。新任务的首个响应是含有效 `id` 和 `contextId` 的 Task；后续事件使用相同标识。L0 的简单问答允许直接返回 Message，但这样的回复不可查询、不可取消。
2. **文本只追加**。同一 `artifactId` 首次使用 `append:false`，之后使用 `append:true`。不得改写已输出的文本；最终快照必须以已输出文本为前缀。
3. **终态必须显式**。`lastChunk` 和关闭流都不表示任务结束；必须发出终态的状态更新。
4. **`GetTask` 返回完整快照**。Artifact 是累计全文，不是最后一段增量。
5. **断开不等于取消**。客户端断开流不得终止任务。
6. **`CancelTask` 如实返回**。只有任务确实进入 `CANCELED` 才能返回该状态；已终结的任务返回 `TASK_NOT_CANCELABLE`。
7. **扩展须协商**。只有请求的 `A2A-Extensions` 包含该 URI 时，才能发出对应扩展的数据。
8. **隔离**。不同 Agent、不同 `tenant` 的 Task 和 Context 互不可见。

### 4.6 扩展目录

| 扩展 | 等级 | 状态 | 规范 |
| --- | --- | --- | --- |
| profile 声明 | 全部 | 新增，待定义 | 本文第 4.6.1 节 |
| 调用上下文 | L2 | 新增，待定义 | 本文第 4.7 节 |
| 工具观察 | L3 | 已有 | [tools/v1](../a2a/extensions/tools/v1.md) |
| exec 审批 | L3 | 已有 | [structured-input-exec/v1](../a2a/structured-input-exec/v1.md) |
| 提交去重 | L3 | 已有 | [message-dedup/v1](../a2a/extensions/message-dedup/v1.md) |
| 来源分类 | 可选 | 已有 | [source-agent/v2](../a2a/extensions/source-agent/v2.md) |

#### 4.6.1 profile 声明扩展

Data-only 扩展，`required:false`。`params` 包含：

- `profileVersion`：字符串，本文对应 `"1"`。
- `level`：`"L0"` 到 `"L3"`。

它只是自声明，不授予权限。BCS 是否据此展示或筛选，另行确定。

### 4.7 调用上下文扩展

实现 [A2A-003](../a2a/todo.zh-CN.md)。在 `SendMessageRequest.metadata` 中，以扩展 URI 为键传递一个对象。

首版字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userId` | string，必填 | 发起调用的业务用户 |

信任模型（来自 D1）：

- BCS 从已认证的调用上下文生成字段，不采信用户消息或外部传入的 metadata。
- Agent 侧不验签。字段的可信度等同于"这次 RPC 的调用方是可信的内部应用"。
- 因此任何能发起 RPC 的内部应用都能构造 `userId`。处理敏感数据的 Agent 应结合 RPC 调用方应用名做限制；SDK 负责把调用方应用名提供给 Agent（第 5.3 节）。
- `GetTask`、`SubscribeToTask`、`CancelTask` 没有 metadata，按已建立的 Task 关联处理，不重新传递上下文。
- Card 以 `required:true` 声明本扩展时，未协商或缺少 `userId` 的请求必须拒绝，不得回退为默认用户。

## 5. Java Server SDK

### 5.1 原则

- 复用官方 [a2a-java](https://github.com/a2aproject/a2a-java) 的协议模型和请求处理，不重写。现有 Harness adapter 使用的是 `1.4.0.Final`。
- Profile 的行为约束由 SDK 默认保证，平台代码不需要了解。
- 平台差异全部通过 SPI 注入，不在 SDK 内判断部署环境。

### 5.2 模块

| 模块 | 内容 | 依赖 |
| --- | --- | --- |
| `bcn-a2a-spec` | 扩展 URI 常量、扩展数据模型、Card 的 Profile 校验 | a2a-java 模型 |
| `bcn-a2a-server-core` | 请求处理、Task 生命周期、行为约束、SPI 定义、内存存储 | `bcn-a2a-spec` |
| `bcn-a2a-server-store-jdbc` | 存储 SPI 的 JDBC 参考实现 | `bcn-a2a-server-core` |
| `bcn-a2a-server-sofaboot-starter` | 自动装配、GRPC（Triple）端点、可选 HTTP 端点、Card 发布 | `bcn-a2a-server-core`、SOFABoot |
| `bcn-a2a-tck` | 一致性测试套件 | `bcn-a2a-spec` |

### 5.3 SPI

下列签名用于说明职责划分，不是最终 API。

**简易层**：覆盖 L0 到 L2。平台只实现一个接口。

```java
public interface BcnAgent {
    AgentCard card();
    void execute(AgentRequest request, AgentResponseSink sink);
    default void cancel(String taskId) {}
}

public interface AgentRequest {
    String taskId();
    String contextId();
    String text();
    CallerContext caller();      // userId、RPC 调用方应用名
}

public interface AgentResponseSink {
    void append(String text);    // 追加文本
    void complete();
    void fail(String reason);
}
```

SDK 负责：创建并持久化 Task、首帧返回 Task、把 `append` 转成符合第 4.5 节的 Artifact 更新、节流写入存储、发出终态、响应取消。

**完整层**：按能力拆分，L3 或有特殊需要的平台按需实现。

| SPI | 职责 | 默认实现 |
| --- | --- | --- |
| `AgentResolver` | 一个进程多个 Agent 时，按接口路由或 `tenant` 选择 `BcnAgent` | 单 Agent |
| `TaskStore` | 跨实例存储，见第 6.2 节 | 内存；JDBC 参考实现 |
| `CallerContextProvider` | 从 RPC 上下文取得调用方应用名 | SOFARPC 实现在 starter 中 |
| `ToolEventEmitter` | 发出工具观察记录 | 由 SDK 提供，Agent 调用 |
| `ApprovalHandler` | 发起 exec 审批、接收决定、恢复执行 | 无；不实现则不声明该扩展 |
| `SubmissionDedupStore` | 提交去重的原子占位 | 基于 `TaskStore` |
| `AgentRegistrar` | 启动时向 BCS 注册或更新 Card | 关闭 |

### 5.4 starter 的装配

- 发现容器内的 `BcnAgent` Bean，生成并校验 Card；不合规时启动失败。
- 暴露 GRPC 端点；HTTP+JSON 和 JSONRPC 端点通过配置开启。
- 存储实现通过配置选择，不在代码中判断环境。
- `AgentRegistrar` 默认关闭。开启时使用 [Provider 管理接口](../../bcn-provider-agent-card-api-v0.1.zh-CN.md)，以 `provider_bot_ref` 保证重启不重复注册。

## 6. 多实例语义

### 6.1 模型

- 接收 `SendMessage` 或 `SendStreamingMessage` 的实例是该 Task 的**执行实例**，执行权不迁移。
- 执行实例把 Task 快照写入共享存储，并持有一个租约。
- 其他实例只读写共享存储，不与执行实例直接通信。

### 6.2 存储 SPI

最小原语：

```java
public interface TaskStore {
    Optional<TaskRecord> get(String agentKey, String taskId);
    boolean create(TaskRecord record);                    // 不存在才写入
    boolean compareAndSet(TaskRecord next, long expectedVersion);
    boolean reserveSubmission(String agentKey, String messageId,
                              String requestDigest, String taskId);
}
```

`TaskRecord` 包含：

- 标识：`agentKey`（Agent 与 tenant）、`taskId`、`contextId`。
- 内容：状态、累计 Artifact、状态消息、Task metadata。
- 协作字段：`version`、`ownerInstance`、`leaseExpiresAtMs`、`cancelRequested`。

约束：

- 不需要发布订阅，不需要跨实例锁。
- 写入失败必须作为错误返回；不得吞掉失败后继续对外报告成功。
- `reserveSubmission` 的记录保留期不短于 Task 记录。

### 6.3 各操作的行为

| 操作 | 落在执行实例 | 落在其他实例 |
| --- | --- | --- |
| `GetTask` | 读存储 | 读存储 |
| `SendStreamingMessage` | 直接推送事件 | 不适用，接收者即执行实例 |
| `SubscribeToTask` | 直接推送事件 | 轮询存储，把快照差异合成为流事件 |
| `CancelTask` | 直接取消 | 写入 `cancelRequested`，有限等待终态后返回 |
| 审批续交 | 直接恢复 | 通过 CAS 接管执行权后恢复；引擎状态不可从存储恢复时返回错误 |

细则：

- **写入节奏**。状态变化立即写。文本按节流写，间隔上限 1 秒，与 BCS 的 1 秒轮询对齐。租约续期与文本写入合并，空闲时单独续期。
- **轮询合成订阅**。由于文本只追加，快照差异就是新增后缀。粒度等于写入间隔。工具观察记录不保证补齐，这与现有 `GetTask` 的约定一致。
- **跨实例取消**。执行实例在下一次写入时读到 `cancelRequested` 并取消。接到请求的实例等待终态，等待上限须小于 BCS 的取消超时（当前清理路径为 15 秒）。超时则返回当前的非终态 Task；BCS 会把它视为取消未确认。
- **宕机**。任一实例读到租约过期的非终态 Task 时，通过 CAS 把它置为 `FAILED`。不重跑。
- **提交去重**。声明该扩展时，先 `reserveSubmission` 再启动引擎；重复提交返回已有 Task。

### 6.4 访问成本

以一次 30 秒的流式任务为例，单 Task 的存储访问量：

| 阶段 | 写 | 读 |
| --- | --- | --- |
| 创建 | 1 次插入（声明去重时再加 1 次占位） | 0 |
| 执行中 | 每秒最多 1 次 CAS 更新，约 30 次 | 0（取消标记随 CAS 结果带回） |
| 终态 | 1 次 CAS 更新 | 0 |
| 跨实例订阅 | 0 | 每秒 1 次按主键读取 |
| 跨实例取消 | 1 次 CAS 更新 | 等待期间每秒 1 次按主键读取 |

每次更新写入累计全文，行大小随输出增长。SDK 对单 Task 文本设置上限，超过时任务失败，不截断后报告成功。不逐 token 写入。

### 6.5 JDBC 参考实现

- 面向 MySQL 和 OceanBase。一张 Task 表，主键 `(agent_key, task_id)`，`version` 列用于 CAS；一张提交占位表，主键 `(agent_key, message_id)`。
- 所有访问按主键，不做范围扫描。
- 提供建表语句，不自动建表。
- 过期清理由宿主负责；参考实现提供按时间分批删除的语句。

## 7. TCK

- 一组语言无关的用例，描述请求序列和期望的响应约束，按一致性等级分组。
- 两个执行方向：
  - 针对 Server：扮演 Client 调用被测 Agent。平台用它自测，SDK 用它做回归。
  - 针对 Client：提供可编排的桩 Server。BCS 下行 adapter 的合同测试逐步对齐到同一组用例。
- 多实例用例：两个实例共用一个存储，覆盖第 6.3 节的每一格，以及执行实例被终止后的租约过期。
- Profile 文本与 TCK 冲突时，先修正文本或用例，不允许两者长期不一致。

## 8. 分期

| 阶段 | 交付 | 验收 |
| --- | --- | --- |
| P1 | Profile 文本；`bcn-a2a-spec`；`server-core` 的 L0/L1；内存存储；starter 的 GRPC 端点 | 单实例下 BCS 到示例 Agent 的流式、非流式、取消闭环；TCK 的 L0/L1 通过 |
| P2 | `TaskStore` 多实例语义；JDBC 参考实现 | 两实例下第 6.3 节全部行为通过 TCK |
| P3 | 调用上下文扩展；BCS 下行侧生成 `userId` | L2 用例通过；未协商时拒绝 |
| P4 | 工具观察、exec 审批、提交去重的 SPI | L3 用例通过；审批续交在两实例下验证 |

## 9. 与现状的差异

需要随本 spec 一并处理或明确保留的不一致：

1. 现有指南把"无应用层凭据"描述为本期限制；Profile 把它定为固定要求。兼容入口保留的 Bearer 链路不属于 Profile。
2. 现有指南中三种绑定并列；Profile 把 `GRPC` 定为必选。
3. A2A-003 目前写的是"待定义字段与信任规则"，并提到 SDK 提供验证扩展点；按 D1，首版不提供验签。
4. 现有扩展 URI 使用仓库 `dev` 分支的文档地址。作为公司标准的标识不够稳定，见第 10 节。

## 10. 待确认

| 编号 | 问题 | 当前倾向 |
| --- | --- | --- |
| Q1 | 代码位置 | Profile 和 TCK 用例放本仓库 `docs/specs/a2a/`；SDK 开源核心放本仓库或独立仓库；MOSN、注册中心相关实现放内部仓库，通过 SPI 接入 |
| Q2 | 扩展 URI 的命名空间、版本和废弃策略 | 新扩展启用稳定命名空间；已有扩展 URI 保持不变 |
| Q3 | 调用上下文除 `userId` 外还需要哪些字段 | 首版只有 `userId` |
| Q4 | 现有 Harness adapter 的去向 | 作为 `server-core` 的起点演进 |
| Q5 | a2a-java server 模块与 SOFABoot 4.x 的装配兼容性 | P1 开始前实测；Harness adapter 已在该组合下运行过 |
| Q6 | 存量 Agent 的主要形态（同步接口、流式、SOFARPC 服务） | 决定简易层 SPI 是否需要同步变体 |
| Q7 | Task 记录的保留期和单 Task 文本上限的默认值 | 待定 |
