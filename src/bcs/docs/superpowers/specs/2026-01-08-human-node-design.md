# State Machine HumanInput 节点设计

**状态**：第一版设计已锁定
**最后更新**：2026-07-22
**范围**：DAG-only、一次性 Human 输入、Workbench + IM Channel

## 1. 为什么需要 HumanInput

当前 State Machine 的节点输出都来自 Bot。但很多流程在继续执行前，需要一个真实的人阅读上下文并
给出意见，例如：

- 发布前确认；
- 风险判断；
- 补充 Bot 无法获得的信息；
- 在几个合法方向中做选择。

第一版要解决的是“流程运行到这里，等待一个 session 内的人输入，然后继续”，而不是在现有 DAG
runtime 中提前塞入一套不完整的循环系统。

## 2. 最重要的设计决定

HumanInput 的输入在语义上就是 Bot 节点的输出。

```text
Bot 节点：Bot final text -> optional Judge -> outcome -> DAG progression
Human 节点：Human text   -> optional Judge -> outcome -> DAG progression
```

两者只有“输出由谁产生”不同：

- BotTask 由配置的 Bot 产生文本；
- HumanInput 由 session 中有权限的 Human 产生文本。

Judge、outcome、artifact、节点完成、分支选择和 run completion 都复用同一套逻辑。这个决定是
第一版保持简单的核心。

## 3. 产品语义

### 3.1 节点激活

HumanInput 被 DAG scheduler 激活后进入 `Running`。此时：

- 不投递 Bot task；
- Workbench 显示待输入表单；
- 如果 run session 绑定了 IM Channel，系统向对应 conversation 推送 Human-ready 提示；
- 节点持续等待，直到合法响应到达或 `node_timeout_ms` 到期。

### 3.2 谁可以响应

第一版不在节点上配置具体审批人 id。任何满足以下条件的人都可以响应：

1. 身份来自可信 HTTP authentication 或 Channel participant mapping；
2. 是该 run session 的 Human participant；
3. participant mode 是 `Present`。

节点的 `assignee` 仍只表示 Bot assignee。因此 HumanInput 的 domain assignee 为 `None`，而不是
填写 Human id。

### 3.3 响应内容

Human 输入自然语言，不要求手写 `approved` 或 `rejected`。

如果节点配置 Judge，Judge 根据自然语言和 criteria 选择声明过的 outcome；如果没有 Judge，系统
直接使用 `complete`。Human 的原始文本保存为该节点的 `artifact_text`，供 downstream 节点使用。

例如：

```text
Human 输入：这个方案可以发布
artifact_text：这个方案可以发布
Judge outcome：approved
responded_by：human_1001
```

### 3.4 Judge 行为

HumanInput 使用与 BotTask 相同的 Judge contract：

- Judge 成功并返回合法 outcome：完成节点；
- Judge provider 失败、超时或返回非法 outcome：节点进入通用 failure 路径；
- Judge 的 `confidence` 不产生 Human 专属 clarification 状态；
- `retry_instruction` 不会让同一 Human 节点重新等待输入。

这意味着第一版没有“人输入一次，Judge 觉得不清楚，再让人补充”的内嵌交互循环。

## 4. YAML 设计

```yaml
api_version: bcs.collaboration/v1
id: release_review
version: 1
name: 发布审核

runtime:
  kind: state_machine
  graph_mode: acyclic
  nodes:
    draft:
      kind: bot_task
      assignee:
        type: bot_binding
        binding: writer
      instruction: 生成发布方案
      transitions:
        complete:
          targets: [review]

    review:
      kind: human_input
      display_name: 人工审核
      instruction: 请审核发布方案，并说明是否允许发布
      node_timeout_ms: 86400000
      judge:
        type: llm
        criteria:
          - 是否明确允许发布
        outcomes: [approved, rejected]
      transitions:
        approved:
          targets: [publish]
        rejected:
          targets: []

    publish:
      kind: bot_task
      assignee:
        type: bot_binding
        binding: publisher
      instruction: 根据已审核方案执行发布
      transitions:
        complete:
          targets: []
```

HumanInput 约束：

- 不配置 Bot assignee；
- `node_timeout_ms` 必填且大于 0；
- `max_attempts` 实际值固定为 1；
- 可以配置 Judge，也可以不配置；
- Judge outcomes 必须与 transitions 对齐；
- graph 仍必须是 acyclic，任何回边都被 validator 拒绝。
- YAML 可以包含多个 HumanInput，但它们必须通过 DAG 依赖明确串行；任何两个可能同时 pending 的
  HumanInput 都在 definition validation 阶段被拒绝。

## 5. 为什么第一版不支持驳回循环

类似下面的结构属于真正的 cyclic graph：

```text
Bot draft -> Human review -> rejected -> Bot revise -> Human review
```

静态 YAML 能描述这条边，但当前 runtime 的 node run 只有一份固定记录，无法正确表达 review 和
revise 的第 1、2、3 次执行。如果只允许 Human 节点重新打开，会把问题推给 artifact、join、timeout、
cancel、history 和 recovery，最终形成一套 Human-only 的循环特例。

因此第一版的 `rejected` 可以终止当前分支或进入一个不回头的后续节点，但不能回到已经执行过的
节点。完整 loop 以后作为 State Machine 通用能力实现。

## 6. 状态变化

### 6.1 正常完成

```text
Pending/Ready
  -> Running（等待 Human）
  -> Judge（如果配置）
  -> Completed(outcome, artifact_text, responded_by)
  -> DAG progression
```

### 6.2 超时

```text
Running
  -> node timeout scanner
  -> Failed
  -> run failure
```

HumanInput 没有 Bot retry。`max_attempts = 1`，因此 timeout 后不会重新激活节点。

### 6.3 并发响应

```text
Human A ---- Judge ---- complete CAS -- winner
Human B ---- Judge ---- complete CAS -- 409 Conflict
```

第一版不增加持久化 response claim。并发时可能产生重复 Judge 调用，但节点完成仍由通用 CAS 保证
只有一个 winner。考虑到本期不追求重分布式协调，这是可接受的成本。

## 7. Workbench 交互

第一版不改造 Workbench 前端，也不增加 HumanInput 专用表单。Human 继续在现有群聊输入框中输入
自然语言，沿用当前 WebSocket `chat.send` 协议。后端完成 Workbench 身份校验后，在普通 Bot
message flow 之前处理这条消息：

- 普通 Chat group 仍按原逻辑发送给 Bot；
- State Machine session 恰好有一个 pending HumanInput 时，整条消息成为该节点输出，不发送给 Bot；
- 没有 pending HumanInput 时拒绝消息，不能降级成普通群聊；
- 多个 pending HumanInput 属于第一版不支持的异常状态，拒绝消息，不猜测目标。

刷新 history 后，HumanInput 输出继续使用现有 user message 形态展示，不要求前端识别新的消息类型。

## 8. IM Channel 交互

第一版直接复用现有 Channel 机制：

1. 一个钉钉等 IM 机器人通过 Channel binding 绑定到目标 group；
2. inbound 首消息启动或关联 State Machine session；
3. HumanInput 激活时，Runtime 通过 `SessionChannelOutboundPort` 向该 conversation 推送提示；
4. Human 在同一 conversation 回复；
5. Channel 根据 conversation-session mapping 找到 run，并将可信 Human identity 与文本交给
   Runtime；
6. Runtime 完成鉴权、Judge 和状态推进；
7. Channel 只返回接收结果，不自行判断 approve/reject。

IM 输入同样只接受“恰好一个 pending HumanInput”的情况。没有 pending 节点时直接拒绝；多个
pending 节点视为 definition/runtime invariant 被破坏并拒绝，不提供 `response_ref` 选节点机制。

Human-ready 通知是 best-effort。发送失败不回滚节点，第一版也不增加通知状态列和后台重投 scanner。

## 9. Timeout 设计

`node_timeout_ms` 表示 HumanInput 从进入 `Running` 起最多等待多久。deadline 持久化在 node run 的
`timeout_deadline_ms`，由现有 scanner 查询并处理，不是进程内为每个节点建立一个 timer。

配置 Judge 时，当前实现仍复用 Bot Judge timeout 计算；它不是一个额外的 Human claim deadline。

## 10. 数据与持久化

Domain：

```rust
pub assignee_bot_id: Option<String>,
pub outcome: Option<String>,
pub responded_by: Option<String>,
pub artifact_text: Option<String>,
```

数据库兼容：

- `assignee_bot_id` 暂时保持 `NOT NULL`；
- HumanInput 写入空字符串 sentinel；
- Store boundary 读取时转回 `None`；
- Runtime 永远不能将 sentinel 当作 Bot delivery target。

Migration 只给 `bcs_state_machine_node_runs` 增加：

```text
outcome       varchar(128) NULL
responded_by  varchar(256) NULL
```

不增加 Human 专属表、claim/lease 字段、event idempotency 字段或 recovery index。

## 11. API 语义

```text
GET  /state-machine-runs/{run_id}/pending-human-nodes
POST /state-machine-runs/{run_id}/nodes/{node_id}/respond
```

`POST` body：

```json
{ "content": "这个方案可以发布" }
```

成功返回 `200`，内容是完成后的 node 与当前 run。已经完成、超时或输掉 CAS 返回 `409`。身份与
权限分别按 `401/403` 处理。输入非法返回 `400`，Judge 不可用返回 `503`。

## 12. 安全边界

- Human identity 必须来自可信认证或 Channel mapping；
- 只允许 session 中 `Present` Human 读取和响应 pending 节点；
- 不信任请求体中的 actor id；
- Human response 限制为 64 KiB；
- 日志不记录完整 Human 内容、IM 原始 body、cookie 或 token；
- SQL 使用参数绑定；
- Channel adapter 不拥有 workflow policy。

## 13. 已知取舍

第一版接受以下限制：

- 服务在 Human-ready 发送失败后不会自动重投；
- 两个并发响应可能都调用 Judge，但只有一个能完成节点；
- 节点完成后、progression 前进程异常没有 Human 专属 recovery；
- 没有 same-request idempotent replay；客户端重试完成后的响应会得到 `409`；
- Judge 失败直接走节点失败，不回到输入框请求澄清。

这些取舍与“第一版轻量实现”的目标一致。若实际故障数据证明需要增强，再优先做通用 runtime
能力，而不是恢复 Human-only claim/lease 模型。

## 14. 后续 Loop 能力

通用 cyclic runtime 至少要同时定义：

- node execution id 与迭代次数；
- 每轮 artifact/Judge output 的版本；
- join 和 branch 在多轮执行中的语义；
- max iterations 与 termination guard；
- retry、timeout、cancel 和 recovery；
- API、事件与 history 的多次执行表达。

在这些问题解决前，YAML 继续保持 DAG-only。
