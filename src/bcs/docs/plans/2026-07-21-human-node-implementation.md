# HumanInput 节点实现说明

> 本文描述第一版 `human_input` 的实际实现边界。产品语义见
> [Human node 设计](../superpowers/specs/2026-01-08-human-node-design.md)。

**状态**：已实现，待本地联调
**最后更新**：2026-07-22

## 1. 实现目标

第一版在现有 DAG State Machine 中增加一次性的 `human_input` 节点：

1. 节点被激活后进入 `Running`，等待 session 中的 Human 输入自然语言；
2. Human 的原始文本等价于 Bot 节点的输出文本；
3. 有 Judge 时复用 Bot 节点的 Judge；没有 Judge 时 outcome 为 `complete`；
4. outcome 确定后复用现有节点完成 CAS 和 DAG progression；
5. Workbench HTTP 与 IM Channel 都调用同一个 application service；
6. 节点超时复用现有 node timeout scanner。

这里刻意不为 HumanInput 建立第二套执行引擎。

## 2. 第一版明确不做的内容

- 不支持 cyclic graph，也不支持 Human 驳回后回到上游 Bot 的循环；
- 不支持重新打开已经完成的 Human 节点；
- 不增加 Human response claim、Judge lease、heartbeat 或 recovery scanner；
- 不增加 Human 专属 clarification / low-confidence loop；
- 不保证 Human-ready IM 通知的持久化重投；
- 不为 Channel 新建表，也不扩展 Channel binding 表；
- 不实现跨实例 exactly-once start 或跨实例响应协调。

完整 YAML loop 将作为 State Machine 的通用能力单独设计，不能只给 Human 节点做局部特例。

## 3. 核心语义

### 3.1 Human 输入就是节点输出

Human 提交：

```text
这个方案可以发布
```

节点完成后：

```text
artifact_text = "这个方案可以发布"
responded_by = "human_1001"
```

Runtime 不再生成带 `[human_input]`、outcome 或 responder header 的包装文本。这样 downstream
节点看到的 artifact 与 Bot 输出保持同一种数据形态。

### 3.2 Judge 与 Bot 节点一致

- 没有 Judge：`outcome = complete`；
- 配置 Judge：调用现有 `evaluate_node_outcome`；
- Judge 返回已声明 outcome：完成节点并按 outcome 选择 transition；
- Judge provider error、timeout 或非法 outcome：走现有 node failure 路径；
- `confidence` 和 `retry_instruction` 不触发 Human 专属重试语义。

因此 HumanInput 不再有 `Evaluating` 或 `NeedsClarification` API 状态。

### 3.3 并发边界

Runtime 不提前 claim Human 输入。两个合法 Human 并发提交时，可能都已开始 Judge，但最终只有一个
请求能通过现有：

```text
run = Running
node = Running
attempt = expected
```

条件完成节点。失败方收到 `409 Conflict`。这是第一版接受的简化边界。

## 4. Definition 与领域模型

YAML 示例：

```yaml
runtime:
  kind: state_machine
  graph_mode: acyclic
  nodes:
    review:
      kind: human_input
      display_name: 人工审核
      instruction: 请审核上游结果，并说明是否允许发布
      node_timeout_ms: 86400000
      judge:
        type: llm
        criteria:
          - 是否明确允许发布
        outcomes:
          - approved
          - rejected
      transitions:
        approved:
          targets: [publish]
        rejected:
          targets: []
```

约束：

- `human_input` 不填写 Bot assignee；
- Domain 的 `assignee_bot_id` 为 `Option<String>`；
- 数据库旧列仍是 `NOT NULL`，Store 写入 Human 节点时使用空字符串 sentinel；
- Store 读取空字符串时还原为 `None`；
- Runtime 不得把空字符串当作 delivery target；
- HumanInput 的 `max_attempts` 固定为 1，不能进入 Bot retry；
- `node_timeout_ms` 必填且必须大于 0；
- topology validator 继续拒绝所有回边。
- 多个 HumanInput 可以出现在同一份 YAML 中，但任意两个 HumanInput 之间必须存在明确的前后依赖路径；
  validator 拒绝可能同时进入 `Running` 的 HumanInput。

节点运行态只新增公开结果字段：

```rust
pub assignee_bot_id: Option<String>,
pub outcome: Option<String>,
pub responded_by: Option<String>,
pub artifact_text: Option<String>,
```

## 5. Runtime 流程

### 5.1 激活

1. DAG scheduler 选择 HumanInput 节点；
2. Store CAS 将节点从 `Pending/Ready` 改为 `Running`；
3. 写入 `started_at` 和 `timeout_deadline_ms`；
4. Workbench 可通过 pending API 查询节点；
5. 如果 session 有 Channel binding，则 best-effort 推送 Human-ready 消息。

Human-ready 发送失败不回滚节点，也不写额外 retry 状态。用户仍可以从 Workbench 查询并响应。

### 5.2 响应

`respond_human_node` 的顺序：

1. trim 文本，拒绝空输入和超过 64 KiB 的输入；
2. 加载 run、definition 与 node；
3. 校验调用者是 run session 中 `Present` 的 Human participant；
4. 校验 run/node 都是 `Running`，且 deadline 未到；
5. 将 Human 文本直接传入 `evaluate_node_outcome`；
6. outcome 成功时调用通用 `complete_node_attempt`：
   - `outcome` 为 Judge 结果或 `complete`；
   - `artifact_text` 为 Human 原始文本；
   - `responded_by` 为可信身份中的 Human actor id；
7. CAS 成功后复用 `skip_unselected_targets`、`dispatch_ready_targets` 和
   `complete_run_if_done`；
8. CAS 失败返回 `409 Conflict`。

### 5.3 Judge 失败

Judge 失败时调用通用 `fail_node_attempt`，随后调用 `fail_node_or_schedule_retry`。由于 HumanInput
的 `max_attempts = 1`，最终表现为 node/run 失败，不会重新等待 Human 输入。

### 5.4 超时

HumanInput 使用 `timeout_deadline_ms` 和现有 `process_expired_node_timeouts`：

1. scanner 找出已过 deadline 的 `Running` node；
2. CAS `Running -> Failed`；
3. 进入通用 failure progression；
4. HumanInput 因 `max_attempts = 1` 不会重试。

超时判断基于数据库 deadline，不依赖单进程内存 timer。

## 6. HTTP 与 Channel

### 6.1 HTTP

Workbench 创建 State Machine 的 `ServiceInvocation` session 时，`created_by` 继续保持为
driver Bot；BCS 同时根据服务端认证结果，把当前 Human 以 `Observer + Present` 加入该
session。这样既不改变 Bot creator 语义，也能满足 HumanInput 的权限要求。Human actor id
不能由请求体指定，普通 Chat session 不执行这项自动加入逻辑。创建协作群时自动生成的首个
`ServiceInvocation` session 与后续手工新建的 session 使用相同规则。

```text
GET  /state-machine-runs/{run_id}/pending-human-nodes
POST /state-machine-runs/{run_id}/nodes/{node_id}/respond
```

请求：

```json
{ "content": "这个方案可以发布" }
```

成功统一返回 `200`，响应包含完成后的 node 与当前 run。主要错误：

| 状态码 | 含义 |
| --- | --- |
| 400 | 空输入、超长输入或节点类型错误 |
| 401 | 没有可信 Human 身份 |
| 403 | 调用者不是 session 中 Present Human |
| 404 | run 或 node 不存在 |
| 409 | 节点已完成、已超时或输掉完成 CAS |
| 503 | Judge 暂时不可用；节点按通用失败路径处理 |

响应接口不再读取 `Idempotency-Key`，也不返回 `202` 或 `422`。

Workbench 不增加 HumanInput 专用表单，继续使用现有群聊输入框和 WebSocket `chat.send`。后端在
Workbench 鉴权后、进入普通 Bot message flow 前，按 session 处理：

- 普通 Chat group：保持原有群聊行为；
- State Machine session 恰好有一个 pending HumanInput：把整条自然语言消息作为该节点输出，
  不再投递给 Bot；
- State Machine session 没有 pending HumanInput：返回 `409 Conflict`，消息不进入普通群聊；
- 出现多个 pending HumanInput：视为 definition/runtime invariant 被破坏并拒绝消息，不猜测目标。

Human 身份只取自认证后的 WebSocket connection 或 HTTP caller，不读取 `chat.send` 中可伪造的
`bot_id`、`bot_uuid`、`from`。

### 6.2 IM Channel

- 复用已有 Channel binding、conversation-session mapping 和 participant mapping；
- 启动 State Machine 后，Human-ready 消息发送到该 session 已绑定的 IM conversation；
- 恰好一个 pending HumanInput 时，inbound 自然语言消息直接调用同一个 `respond_human_node`；
- 成功回复“回复已接收，流程继续执行”；
- 没有 pending HumanInput 时直接拒绝消息，不回退到 Bot 群聊；
- 多个 pending HumanInput 属于不支持的异常状态，直接拒绝，不支持 `response_ref` 选节点；
- Channel 不自行解释 approve/reject，也不绕过 Runtime 权限与 Judge。

## 7. 数据库变更

Migration 003 只增加：

```sql
ALTER TABLE bcs_state_machine_node_runs
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(128) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS responded_by VARCHAR(256) DEFAULT NULL;
```

不修改 `bcs_collaboration_events`，不增加 Human 专属索引或表。

Bot 节点也写入 `outcome`，`responded_by` 保持 `NULL`；Human 节点同时写两者。

## 8. 安全与隐私

- Human actor id 只来自认证结果或可信 Channel participant mapping；
- 不接受请求体自报 actor id；
- response 最大 64 KiB；
- Runtime event 与普通日志不记录完整 Human 文本；
- Store SQL 全部使用参数绑定；
- 数据库 sentinel 只用于兼容旧 schema，不能作为 Bot delivery target。

## 9. 验证清单

- Domain：Human YAML、可空 assignee、outcome/responded-by 序列化；
- Store：空字符串 sentinel、Human activation CAS、通用 completion CAS；
- Runtime：无 Judge 完成、Judge outcome、并发 CAS、timeout、无 Bot retry；
- Definition：允许显式串行的多个 HumanInput，拒绝可能并行 pending 的 HumanInput；
- Session：driver Bot 创建 State Machine session 时，认证 Human 自动成为 Present participant；
- Workbench/HTTP：普通 Chat 保持原行为；单 pending 消费输入；零 pending 和多 pending 拒绝；
- Channel：Human-ready outbound、单 pending continuation、零 pending 和多 pending 拒绝；
- Migration：MySQL 文件检查、SQLite fresh/idempotent migration；
- 前端：不新增协议或 HumanInput 专用表单，继续使用现有群聊输入框。

## 10. 后续通用 loop

后续若支持 YAML loop，需要在 State Machine 层统一解决：

- 同一 node 的多次 execution identity；
- 每轮 artifact 与 Judge 结果版本；
- join 在多轮执行中的语义；
- 最大迭代次数和终止条件；
- retry、timeout、cancel 与 recovery；
- API 和 history 如何表达同一节点的多次执行。

这些能力不应通过重新打开当前 Human node 或增加 Human-only revision loop 来实现。
