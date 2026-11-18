# BCN A2A Payload 规范

规定 BCN 中 A2A 消息的数据格式：标准 A2A 对象怎么用、metadata 的命名空间、唯一的平台扩展（工具观察 toolcall）。不接 BCN SDK、自己实现 A2A 服务的业务，按本文实现即可与 SDK 生成的 payload 和 BCS 的处理保持一致。

| 项 | 内容 |
|---|---|
| 状态 | 草案 v0.1，待评审 |
| 日期 | 2026-11-18 |
| 线协议 | A2A 1.0 |
| 适用 | BCN SDK、自行实现的 A2A Agent、BCS 的 A2A 适配 |

条款编号为 PL-xx。“必须”“不得”是硬性要求，违反时调用方可以拒绝或判本次调用失败；“应”是强烈建议；“可以”是允许但不要求。示例使用 ProtoJSON 表示，SOFA gRPC 线上为 protobuf，字段含义相同。

## 目录

1. [范围](#1-范围)
2. [基本约定](#2-基本约定)
3. [三层结构](#3-三层结构)
4. [字段语义](#4-字段语义)
5. [内容 Part](#5-内容-part)
6. [回复形态](#6-回复形态)
7. [metadata 命名空间](#7-metadata-命名空间)
8. [toolcall 扩展（工具观察）](#8-toolcall-扩展工具观察)
9. [错误](#9-错误)
10. [完整示例](#10-完整示例)
11. [自检清单](#11-自检清单)
12. [兼容与版本](#12-兼容与版本)
13. [BCS 当前实现情况](#13-bcs-当前实现情况)
14. [待定事项](#14-待定事项)
15. [参考](#15-参考)

## 1 范围

本文只规定 payload：请求和回复里放什么、放在哪里、怎么解释。服务怎么发布、怎么注册 Card、线程和超时怎么处理，不在本文范围内。

| 本版包含 | 本版不包含 |
|---|---|
| 标准 A2A 对象的用法：Message、Task、产出物、状态更新 | 人机交互（hitl）：本版不得进入 INPUT_REQUIRED |
| 三种回复形态及其帧序列 | 推送通知、ListTasks、扩展 Card |
| metadata 命名空间：平台键、扩展键、业务键 | 文件、图片等非文本内容 |
| 平台字段 `bcn_caller`（调用上下文） | 结构化产出物（DataPart 作为产出物） |
| 唯一的平台扩展：工具观察（toolcall） | 鉴权：内网调用不做应用层鉴权（D1） |

本文是 payload 的权威定义。《BCN A2A Profile 与 Java Server SDK 详细设计》（同目录 `design.html`）中与本文冲突的写法，例如点号键 `bcn.caller`、把调用上下文作为 URI 扩展，以本文为准，后续同步修改。

## 2 基本约定

| 绑定 | 寻址 | 线上格式 |
|---|---|---|
| GRPC | SOFA 寻址（Card 接口项绑定名 `SOFA-GRPC`），经 MOSN 调用 | protobuf，服务 `lf.a2a.v1.A2AService` |
| HTTP+JSON | Card 接口项中的 URL | ProtoJSON |
| JSONRPC | Card 接口项中的 URL | ProtoJSON，包在 JSON-RPC 2.0 中 |

三种绑定使用同一套数据模型。JSON 字段名为小驼峰，枚举用完整名称字符串，例如 `TASK_STATE_WORKING`、`ROLE_AGENT`。

| 请求头 | HTTP 写法 | gRPC metadata | 说明 |
|---|---|---|---|
| 协议版本 | `A2A-Version: 1.0` | `a2a-version: 1.0` | BCS 已在所有请求中发送 |
| 扩展 | `A2A-Extensions: <URI>,<URI>` | `a2a-extensions` | 请求：调用方希望启用的扩展。响应：本次实际启用的扩展 |

| 条款 | 内容 |
|---|---|
| PL-01 | 线协议版本为 A2A 1.0。服务端收到不支持的版本时返回 `VersionNotSupportedError`。 |
| PL-02 | 所有 ID（messageId、contextId、taskId、artifactId、toolCallId）都是不透明的非空字符串，不限制长度和字符集，接收方不得从 ID 推断含义。 |
| PL-03 | 文本为 UTF-8。HTML、Markdown 等标记按普通文本传递，接收方不得执行其中的脚本。 |
| PL-04 | 接收方必须忽略不认识的字段和 metadata 键：保留、透传、不报错。发送方不得依赖接收方理解本文未约定的字段。 |

## 3 三层结构

| 层 | 定义者 | 怎么扩展 |
|---|---|---|
| 标准骨架 | A2A 官方模型（protobuf） | 直接复用 Message、Task、Artifact、Part。不得在 protobuf 对象上增加自有字段 |
| 平台约定 | 本文 | 只有两处：metadata 中的平台键（本版只有 `bcn_caller`）；toolcall 扩展 |
| 业务数据 | 业务的生产方和消费方 | 正文写进文本；描述信息放进对应对象的 metadata，使用业务键（7.2） |

```text
SendMessageRequest
├─ tenant                         目标 Agent
├─ message
│  ├─ messageId / role / contextId / taskId / referenceTaskIds
│  ├─ parts[]                     本版只有 TextPart
│  ├─ metadata                    只描述这条消息
│  └─ extensions[]                这条消息涉及的扩展 URI（只是标记）
├─ configuration                  acceptedOutputModes 等调用配置
└─ metadata                       只描述这次调用：bcn_caller、业务的调用级字段

SendMessageResponse               二选一：message | task

StreamResponse（每一帧选一个分支）
├─ message                        完整 Message（只用于形态 A）
├─ task                           Task 快照（Task 流的第一帧）
├─ statusUpdate                   taskId / contextId / status{state, message} / metadata
└─ artifactUpdate                 taskId / contextId / artifact{artifactId, parts} / append / lastChunk
```

## 4 字段语义

| 字段 | 含义 | 不能当作 |
|---|---|---|
| `tenant` | 目标 Agent 的路由标识。一个服务承载多个 Agent 时必填，值等于 Card 接口项中的 tenant | 用户身份或授权凭据 |
| `messageId` | 一条消息的身份。请求消息和回复消息各自生成 | taskId；幂等键（本规范不提供提交去重） |
| `contextId` | 会话关联。首轮请求可以不带，由 Agent 生成；之后沿用。出现时不得为空字符串 | 任务状态或恢复凭证 |
| `taskId` / `Task.id` | Task 的身份。请求中只有续交一个已存在的 Task 时才能带 | 一个不存在的 ID；本版只有引擎模式支持续交 |
| `referenceTaskIds` | 对其他 Task 的引用，原样透传 | 执行依赖、查询授权或恢复能力 |
| `artifactId` | 同一 Task 内一个产出物的身份 | 可以互相覆盖的槽位 |
| `append` / `lastChunk` | 这一帧是追加还是首段 / 这个产出物已经发完 | Task 已完成。完成只看显式终态（PL-15） |
| `metadata` | 只描述它所在的那个对象 | 跨层级的全局上下文 |

## 5 内容 Part

| 条款 | 内容 |
|---|---|
| PL-05 | 请求消息的 role 必须是 `ROLE_USER`，至少有一个 Part。本版只接受 TextPart，其他类型返回 `ContentTypeNotSupportedError`。 |
| PL-06 | 回复内容（独立 Message 回复、产出物）本版只使用 TextPart。 |
| PL-07 | DataPart 本版只用于工具观察记录（第 8 节），不得出现在独立 Message 回复和产出物中。BCS 遇到非文本内容会判本次运行失败。 |
| PL-08 | 文件 Part（raw、url）本版不使用。 |
| PL-09 | BCS 请求的 `acceptedOutputModes` 为 `["text/plain"]`。Agent 的输出必须是 text/plain。 |

## 6 回复形态

Agent 选择一种形态，并在 Card 中如实声明 `capabilities.streaming`。

| | 形态 A：一次性回复 | 形态 B：流内 Task | 形态 C：引擎模式 |
|---|---|---|---|
| 适用 | 回答一次性给出 | 逐段输出，Agent 不保存状态 | 需要断线恢复、查询、取消、续交 |
| SendMessage 返回 | Message | 处理完成后的终态 Task（带完整正文） | Task |
| SendStreamingMessage 的流 | 恰好一条 Message，然后结束 | 首帧 Task → 更新 → 显式终态 | 同形态 B |
| GetTask / SubscribeToTask / CancelTask | `TASK_NOT_FOUND` | `TASK_NOT_FOUND`（Task 只存在于这条流内） | 由运行记录支撑 |
| Card 中的 streaming | true 或 false 都可以 | 必须为 true | 按实际能力 |
| 工具观察 | 不支持 | 支持 | 支持 |

### 6.1 Message 回复（形态 A）

| 条款 | 内容 |
|---|---|
| PL-10 | role 为 `ROLE_AGENT`；至少一个 TextPart；不带 taskId；contextId 不填，或等于本次调用的 contextId（请求没带时为 Agent 生成的值）。 |
| PL-11 | 流式请求下，形态 A 的流只能包含这一条 Message，之后结束流。 |

### 6.2 Task 流（形态 B、C）

| 条款 | 内容 |
|---|---|
| PL-12 | 第一帧必须是 Task，id 和 contextId 非空。之后所有事件的 taskId、contextId 必须与之相同。 |
| PL-13 | 状态只使用 SUBMITTED（仅首帧）、WORKING、COMPLETED、FAILED、CANCELED、REJECTED。本版不得使用 INPUT_REQUIRED、AUTH_REQUIRED、UNSPECIFIED。 |
| PL-14 | 更新事件中的 `status.state` 表示 Task 当前的真实状态。不得为了附带一条消息而改变状态。 |
| PL-15 | 终态必须用 statusUpdate 显式发出。关闭流、`lastChunk: true` 都不代表 Task 结束。终态之后不得再发出任何事件。 |
| PL-16 | Task 已经发出后出错：发出 FAILED 终态（状态消息可以带一句不含敏感信息的原因），再正常结束流，不用传输层错误中断流。Task 发出之前出错：返回第 9 节的错误。 |
| PL-17 | 状态更新中的 `status.message` 是过程信息（进度说明、工具记录），调用方可以不展示，也可能丢失。不得把回答正文放在状态消息里。 |

### 6.3 文本产出物

| 条款 | 内容 |
|---|---|
| PL-18 | 回答正文放在产出物中，Part 为 TextPart。一个回答通常对应一个产出物（一个 artifactId）。 |
| PL-19 | 同一产出物的第一帧 `append` 为 false 或不填，之后每帧 `append: true` 逐段追加。中途不得用 `append: false` 替换已经发出的内容。 |
| PL-20 | 可以在最后一帧用 `append: false` 加 `lastChunk: true` 发送完整文本，此时内容必须以已经发出的文本开头，调用方只取新增部分。`lastChunk: true` 之后这个产出物不得再更新。`lastChunk` 可以不发。 |
| PL-21 | Task 快照（首帧或 GetTask）中的 artifacts 是基线，调用方据此补齐缺失的部分。快照中的文本同样只能在已发出的内容之后增加。 |

### 6.4 非流式调用与不可查询的 Task

| 条款 | 内容 |
|---|---|
| PL-22 | 形态 B 收到 SendMessage 时，处理完成后返回终态 Task，artifacts 中带完整正文。不支持查询的 Agent 不得返回非终态 Task，否则调用方再也拿不到结果。 |
| PL-23 | 形态 A、B 对 GetTask、SubscribeToTask、CancelTask 一律返回 `TASK_NOT_FOUND`。调用方在流中断后按失败处理，不重连。 |
| PL-24 | 形态 C 断流后，调用方可以用 SubscribeToTask 或 GetTask 恢复。GetTask 返回的快照必须包含已经发出的全部产出物。 |

## 7 metadata 命名空间

### 7.1 位置

| 位置 | 描述的对象 | 本版用途 |
|---|---|---|
| `SendMessageRequest.metadata` | 这次调用 | `bcn_caller`；业务的调用级字段 |
| `Message.metadata` | 这条消息 | 业务字段 |
| `Part.metadata` | 这个 Part | 业务字段；工具记录的来源等附加信息 |
| `Artifact.metadata` | 这个产出物 | 业务字段 |
| `Task.metadata` | 这个 Task | 业务字段 |
| 事件的 metadata | 这个事件 | 业务字段 |

| 条款 | 内容 |
|---|---|
| PL-25 | 每一层的 metadata 只描述所在的对象。不得把内层数据提升到外层，也不得假设接收方会跨层合并。 |

### 7.2 键的三类

| 键 | 归属 | 规则 |
|---|---|---|
| `bcn`，以及以 `bcn_` 开头的键 | BCN 平台 | 只能由本文定义。业务不得写入 |
| 含冒号的 URI 形式，例如 `https://…`、`urn:…` | 外部标准扩展 | 由对应的扩展规范定义。没有实现该扩展不得写入 |
| 其余所有键 | 业务 | 平台不解释，原样透传 |

| 条款 | 内容 |
|---|---|
| PL-26 | 业务不得写入 `bcn` 和 `bcn_*` 键。接收方遇到本文未定义的 `bcn_*` 键时保留并忽略。 |
| PL-27 | 平台不解释业务键，不能用业务键驱动 BCS 的路由、协作和授权。 |
| PL-28 | 键名只用小写字母、数字和下划线，不用点号：带点号的键在 JSON 路径查询、日志检索和按路径读写的工具里会被当作层级。平台键因此用 `bcn_` 而不是 `bcn.`。 |
| PL-29 | 只在一对生产方和消费方之间约定的业务键可以用短名。需要经过多方传递的业务键，加上业务自己的前缀，例如 `order_channel`。 |

### 7.3 平台键 bcn_caller（调用上下文）

本版平台只定义这一个键。它是固定字段，不是 URI 扩展，不需要协商：请求里带了就解析。

```json
{
  "bcn_caller": {
    "userId": "u-10086",
    "source": {"platform": "bcn"}
  },
  "order_channel": "app"
}
```

上例是 `SendMessageRequest.metadata`，其中 `order_channel` 是业务键。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `userId` | string | 否 | 业务用户 ID。系统触发的调用可以没有 |
| `source.platform` | string | 否 | 调用来源平台 |
| 其他字段 | — | — | 保留给平台，接收方保留并忽略 |

| 条款 | 内容 |
|---|---|
| PL-30 | `bcn_caller` 只出现在 `SendMessageRequest.metadata`。 |
| PL-31 | 由 BCS（或直连时的调用方）根据自己已认证的上下文生成。调用方转发请求时，必须用自己生成的值覆盖，或删除上游带来的 `bcn_caller`，不得原样转发。 |
| PL-32 | Agent 不验签。字段是调用方的自述，可信度等同于“来自内网可信应用”；需要授权的操作由 Agent 自己判断。 |
| PL-33 | Agent 不得把 userId 写进回复、产出物、状态消息或工具记录。 |
| PL-34 | 形态 C 续交同一个 Task 时，userId 必须与创建 Task 时一致，否则拒绝。 |

### 7.4 值的类型

| 条款 | 内容 |
|---|---|
| PL-35 | metadata 和 `DataPart.data` 在线上是 protobuf Struct，数字都是双精度浮点数。超过 2^53 的整数（订单号、用户 ID）和金额必须用字符串。 |
| PL-36 | 键的顺序不保证。不要对 metadata 或 `DataPart.data` 签名，也不要按字节比较。 |
| PL-37 | 不得放凭据、令牌、密码等敏感信息。 |

### 7.5 转发与群协作

| 条款 | 内容 |
|---|---|
| PL-38 | BCS 在一次调用的路径上原样传递业务键，不把它们广播给群里的其他 Bot。需要其他 Bot 知道的信息必须写进文本。 |

### 7.6 从旧格式迁移

- 旧格式中嵌套在容器键（例如 `biz`、`metadata`）下的业务字段，平铺到同一对象的顶层。
- 同名同值保留一份。同名不同值：迁移期以顶层（新位置）为准并记录日志，迁移完成后视为错误。
- 旧的点号平台键改为下划线，例如 `bcn.caller` 改为 `bcn_caller`。

## 8 toolcall 扩展（工具观察）

本版唯一的平台扩展。Agent 执行过程中调用了哪些工具、结果如何，以过程信息的形式告诉调用方，供展示和记录。

### 8.1 标识与声明

AgentCard 的 `capabilities.extensions` 中声明：

```json
{"uri": "urn:bcn:a2a:extension:tools:v2", "required": false}
```

| 条款 | 内容 |
|---|---|
| PL-39 | URI 为 `urn:bcn:a2a:extension:tools:v2`（占位，待定）。只有会发出 Task 的 Agent（形态 B、C）可以声明。 |
| PL-40 | `required` 必须为 false。调用方不支持时 Agent 照常工作，只是不发工具记录。 |

### 8.2 协商

| 条款 | 内容 |
|---|---|
| PL-41 | 调用方在请求头 `A2A-Extensions` 中带上 URI，表示希望接收。Agent 支持时在响应头中回写，表示本次已启用。 |
| PL-42 | 只有本次调用已启用时，Agent 才能发出工具记录。未启用时一条都不发，正文和其他事件不受影响。 |
| PL-43 | 只在消息的 `extensions` 字段里写 URI 不算启用。 |
| PL-44 | SubscribeToTask 同样按本次请求头协商。 |

### 8.3 载体

| 条款 | 内容 |
|---|---|
| PL-45 | 一条工具记录就是一个 statusUpdate 事件：`status.state` 为 Task 当前的状态（通常是 WORKING）；`status.message` 的 role 为 `ROLE_AGENT`，taskId、contextId 与 Task 一致，`extensions` 恰好为 `["urn:bcn:a2a:extension:tools:v2"]`，parts 恰好一个 DataPart。 |
| PL-46 | 工具记录只出现在 Task 流中。形态 A 本版没有工具观察。 |
| PL-47 | 工具记录不进入产出物，也不要求保存在 Task.history 中；GetTask 快照中可以没有。 |

### 8.4 data 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `toolCallId` | string | 是 | 同一 Task 内唯一；同一次调用的 start 和 result 使用相同的值 |
| `name` | string | 是 | 工具名 |
| `phase` | string | 是 | `start`（开始调用）或 `result`（得到结果） |
| `args` | object | 否 | 调用参数，只在 start 中出现。没有就不写 |
| `result` | 任意 JSON | 否 | 调用结果，只在 result 中出现。没有就不写 |
| `isError` | boolean | 否 | 只在 result 中出现。工具失败不等于 Task 失败 |

| 条款 | 内容 |
|---|---|
| PL-48 | `data` 顶层只允许上表中的字段。消费方必须忽略不认识的字段；同一版本内只会增加可选字段。 |
| PL-49 | 来源、耗时等附加信息放在这个 DataPart 的 metadata 中，使用业务键，例如 `server_id`、`elapsed_ms`。平台不解释这些字段。 |
| PL-50 | `args`、`result` 中不得包含凭据和 userId。单个字段编码后的大小上限待定（建议 32 KiB），超出时由生产方截断。 |

### 8.5 语义

| 条款 | 内容 |
|---|---|
| PL-51 | 工具记录是尽力投递的过程信息，可能丢失。调用方不得据此判断 Task 的成败。 |
| PL-52 | 工具记录不表示审批，也不改变 Task 状态。 |
| PL-53 | 同一 toolCallId 的 start 应先于 result。调用方必须容忍只收到其中一条；相同的记录重复发送视为一条。 |
| PL-54 | 消费方遇到不合规的工具记录（缺少必填字段、phase 未知）时丢弃这一条，不得因此让整次调用失败。 |

### 8.6 示例

开始调用：

```json
{"statusUpdate": {
  "taskId": "task-001", "contextId": "ctx-001",
  "status": {
    "state": "TASK_STATE_WORKING",
    "message": {
      "messageId": "msg-tool-001", "role": "ROLE_AGENT",
      "taskId": "task-001", "contextId": "ctx-001",
      "extensions": ["urn:bcn:a2a:extension:tools:v2"],
      "parts": [{
        "data": {"toolCallId": "call-1", "name": "search_poi", "phase": "start",
                 "args": {"city": "杭州", "keyword": "西湖"}},
        "metadata": {"server_id": "poi-search"}
      }]
    }
  }
}}
```

得到结果：

```json
{"statusUpdate": {
  "taskId": "task-001", "contextId": "ctx-001",
  "status": {
    "state": "TASK_STATE_WORKING",
    "message": {
      "messageId": "msg-tool-002", "role": "ROLE_AGENT",
      "taskId": "task-001", "contextId": "ctx-001",
      "extensions": ["urn:bcn:a2a:extension:tools:v2"],
      "parts": [{
        "data": {"toolCallId": "call-1", "name": "search_poi", "phase": "result",
                 "result": {"hits": 3}, "isError": false}
      }]
    }
  }
}}
```

### 8.7 与 tools/v1 的差异

| | tools/v1（现有） | toolcall v2（本文） |
|---|---|---|
| 标识 | GitHub 文档 URL；消息 metadata 中以 URI 为键写 `{"version":"1"}` | URN；消息的 `extensions` 字段 |
| phase | `start` / `result` | 相同 |
| `args` | start 中必填，对象 | 可选 |
| `result` | 必填，`{"content":[{"type":"text","text":…}]}` | 可选，任意 JSON |
| `isError` | result 中必填 | 可选 |
| 不合规的记录 | BCS 判本次运行失败 | 丢弃这一条（PL-54） |

## 9 错误

| 情况 | A2A 错误 | gRPC | JSON-RPC | HTTP | ErrorInfo.reason |
|---|---|---|---|---|---|
| Task 不存在，或 Agent 不支持任务类方法 | `TaskNotFoundError` | `NOT_FOUND` | `-32001` | 404 | `TASK_NOT_FOUND` |
| Task 不能取消 | `TaskNotCancelableError` | `FAILED_PRECONDITION` | `-32002` | 400 | `TASK_NOT_CANCELABLE` |
| 推送配置相关方法 | `PushNotificationNotSupportedError` | `FAILED_PRECONDITION` | `-32003` | 400 | `PUSH_NOTIFICATION_NOT_SUPPORTED` |
| ListTasks；未声明 streaming 却收到流式请求 | `UnsupportedOperationError` | `FAILED_PRECONDITION` | `-32004` | 400 | `UNSUPPORTED_OPERATION` |
| 非文本输入；不接受 text/plain 输出 | `ContentTypeNotSupportedError` | `INVALID_ARGUMENT` | `-32005` | 400 | `CONTENT_TYPE_NOT_SUPPORTED` |
| 扩展 Card | `ExtendedAgentCardNotConfiguredError` | `FAILED_PRECONDITION` | `-32007` | 400 | `EXTENDED_AGENT_CARD_NOT_CONFIGURED` |
| 协议版本不支持 | `VersionNotSupportedError` | `FAILED_PRECONDITION` | `-32009` | 400 | `VERSION_NOT_SUPPORTED` |
| 请求格式不合法；业务拒绝 | `InvalidParamsError` | `INVALID_ARGUMENT` | `-32602` | 400 | 具体原因，例如 `INVALID_INPUT` |
| 业务暂时不可用（BCN 约定） | — | `UNAVAILABLE` | `-32603` | 503 | `UNAVAILABLE` |
| 内部错误 | `InternalError` | `INTERNAL` | `-32603` | 500 | `INTERNAL` |

| 条款 | 内容 |
|---|---|
| PL-55 | 错误原因放在 `google.rpc.ErrorInfo` 中：reason 用大写下划线；A2A 标准错误的 domain 为 `a2a-protocol.org`；BCN 自定义原因的 domain 待定。HTTP+JSON、JSON-RPC 按 A2A 1.0 各绑定的规定携带。 |
| PL-56 | 错误描述会被调用方看到，不得包含堆栈、内部地址、凭据和用户数据。 |
| PL-57 | 凡是“没有这个 Task”的情况都用 `TASK_NOT_FOUND`，包括形态 A、B 收到任务类请求，不用 `UNSUPPORTED_OPERATION`。这样调用方可以用同一种方式探测所有 Agent。 |
| PL-58 | Task 已经发出后的失败，用 FAILED 终态表达（PL-16），不用本节的错误。 |

## 10 完整示例

### 10.1 一次性回复（形态 A）

请求（SendMessage）：

```json
{
  "tenant": "order-agent",
  "message": {
    "messageId": "msg-req-001", "role": "ROLE_USER",
    "parts": [{"text": "订单 1001 发货了吗？"}]
  },
  "configuration": {"acceptedOutputModes": ["text/plain"]},
  "metadata": {
    "bcn_caller": {"userId": "u-10086", "source": {"platform": "bcn"}},
    "order_channel": "app"
  }
}
```

响应：

```json
{
  "message": {
    "messageId": "msg-resp-001", "role": "ROLE_AGENT", "contextId": "ctx-001",
    "parts": [{"text": "订单 1001 已于今天上午发货。"}]
  }
}
```

### 10.2 流式输出（形态 B，已启用 toolcall）

SendStreamingMessage 的流依次为以下帧。工具记录的完整写法见 8.6，这里省略了消息内的重复字段。

```text
1 {"task": {"id": "task-001", "contextId": "ctx-001", "status": {"state": "TASK_STATE_WORKING"}}}
2 {"statusUpdate": {... "status": {"state": "TASK_STATE_WORKING", "message": {工具记录 phase=start}}}}
3 {"statusUpdate": {... "status": {"state": "TASK_STATE_WORKING", "message": {工具记录 phase=result}}}}
4 {"artifactUpdate": {"taskId": "task-001", "contextId": "ctx-001",
     "artifact": {"artifactId": "answer", "parts": [{"text": "杭州两日游："}]}}}
5 {"artifactUpdate": {"taskId": "task-001", "contextId": "ctx-001", "append": true,
     "artifact": {"artifactId": "answer", "parts": [{"text": "第一天西湖，"}]}}}
6 {"artifactUpdate": {"taskId": "task-001", "contextId": "ctx-001", "append": true, "lastChunk": true,
     "artifact": {"artifactId": "answer", "parts": [{"text": "第二天灵隐寺。"}]}}}
7 {"statusUpdate": {"taskId": "task-001", "contextId": "ctx-001",
     "status": {"state": "TASK_STATE_COMPLETED"}}}
```

同一个 Agent 收到非流式的 SendMessage 时，返回第 7 帧时的终态 Task，artifacts 中 `answer` 的文本为三段拼接后的完整内容。

### 10.3 业务拒绝

```text
gRPC status: INVALID_ARGUMENT
message:     请告诉我要查询的订单号
details:     google.rpc.ErrorInfo { reason: "INVALID_INPUT", domain: "<BCN 错误域，待定>" }
```

## 11 自检清单

### 11.1 服务端（Agent）

| 检查项 | 条款 |
|---|---|
| 请求版本为 1.0；只接受文本输入，其他内容返回 CONTENT_TYPE_NOT_SUPPORTED | PL-01、PL-05 |
| 选定一种回复形态，Card 中的 streaming 与之相符 | 第 6 节 |
| 形态 A：回复 role 为 Agent、只有文本、不带 taskId、contextId 与本次一致；流中只有这一条 Message | PL-10、PL-11 |
| 形态 B、C：首帧是 Task；ID 一致；终态显式发出；终态之后不再发任何事件 | PL-12、PL-15 |
| 不使用 INPUT_REQUIRED、AUTH_REQUIRED、UNSPECIFIED | PL-13 |
| 正文在产出物里，按追加规则发送；不放在状态消息里 | PL-17 至 PL-20 |
| 出错时：Task 已发出用 FAILED 终态，否则返回标准错误 | PL-16、第 9 节 |
| 形态 A、B 对任务类方法返回 TASK_NOT_FOUND | PL-23、PL-57 |
| 不写入 bcn、bcn_* 键；业务键用小写下划线；大整数和金额用字符串 | PL-26、PL-28、PL-35 |
| 读取 bcn_caller 但不信任其为认证身份；不把 userId 写进任何输出 | PL-32、PL-33 |
| 只在调用方启用 toolcall 时发工具记录；载体和字段符合 8.3、8.4 | PL-42、PL-45、PL-48 |
| 不认识的字段和键保留并忽略 | PL-04 |

### 11.2 调用方

| 检查项 | 条款 |
|---|---|
| 发送 A2A-Version: 1.0；希望接收工具记录时在 A2A-Extensions 中请求 toolcall | PL-01、PL-41 |
| 生成 bcn_caller 时基于自己已认证的上下文；转发时覆盖或删除上游的值 | PL-31 |
| 以显式终态判断 Task 结束，不以流关闭或 lastChunk 判断 | PL-15 |
| 按 append 规则拼接正文，只取新增部分 | PL-19 至 PL-21 |
| 形态 A、B 断流后按失败处理，不重连 | PL-23 |
| 不合规的工具记录丢弃，不让整次调用失败；不据工具记录判断成败 | PL-51、PL-54 |
| 不认识的字段和键保留并忽略 | PL-04 |

## 12 兼容与版本

- 同一版本内只增加可选字段；接收方忽略不认识的字段（PL-04），因此新增字段不影响旧实现。
- 不兼容的修改：扩展换新的 URI（例如 tools 的 v3）；平台键换新的键名（例如 `bcn_caller_v2`）。同一个键、同一个 URI 下的数据格式不做不兼容修改。
- 本文的版本号随修订递增；每次修订列出变更的条款。

## 13 BCS 当前实现情况

| 项 | 本规范 | BCS 当前 | 后续 |
|---|---|---|---|
| 工具观察 | toolcall v2 | 识别 tools/v1 | BCS 适配 v2；过渡期 Agent 按调用方请求的 URI 输出对应版本 |
| 调用上下文 | `bcn_caller` | 尚未发送（待办 A2A-003） | BCS 按 7.3 实现 |
| 不合规的工具记录 | 丢弃这一条 | v1 判本次运行失败 | 适配 v2 时改为丢弃 |
| 非文本内容 | 本版不使用 | 遇到即判本次运行失败 | 一致 |
| 文本产出物的追加规则 | PL-19 至 PL-21 | 已按此实现 | 一致 |
| 形态 A、B 断流 | 按失败处理 | 用 GetTask 尝试恢复，收到 TASK_NOT_FOUND 后失败 | 一致 |
| 协议版本头 | `A2A-Version: 1.0` | 已发送 | 一致 |
| streaming:false 的 Agent | 只能返回 Message 或终态 Task | 发送 `returnImmediately=true` 并用 GetTask 轮询 | 形态 B 必须声明 streaming:true（第 6 节） |

## 14 待定事项

| 事项 | 当前建议 |
|---|---|
| toolcall 扩展的 URI | 与其他 BCN 扩展的命名方式统一后确定 |
| BCN 自定义错误原因的 ErrorInfo domain | 统一确定一个，所有 BCN 组件共用 |
| 工具记录 args、result 的大小上限和截断方式 | 建议单个字段 32 KiB |
| 请求和回复的整体大小上限 | 建议请求 4 MiB；回复按 BCS 的处理能力确定 |
| 形态 B 收到 `returnImmediately=true` 时怎么处理 | 建议忽略该参数，处理完成后返回终态 Task。这与 A2A 的字面要求有偏差，需要评审确认 |
| 形态 B 在 Profile 等级中的定位 | 在 Profile 中补充条款 |
| 群协作时 BCS 对业务键、bcn_* 键的转发细则 | 随 BCS 群协作方案确定 |
| 工具记录是否增加展示类字段（耗时、状态） | 有明确展示需求时，作为可选字段加入同一版本 |

## 15 参考

- A2A 规范 1.0：消息与 Task、流式响应在终态或中断态关闭的规定、错误映射表；A2A 扩展文档：扩展声明、协商与版本。
- BCS 的 A2A 适配：`apps/bcs/crates/external-clients/bcs-a2a-http/src/projection.rs`（回复投影）、`text.rs`（文本追加规则）、`tools.rs`（工具记录）、`delivery.rs`（请求配置）。
- `docs/specs/a2a/extensions/tools/v1.md`（tools/v1）。
- 同目录：`design.html`（详细设计）、`sdk-skeleton/`（SDK 骨架，按本文生成 payload）。
