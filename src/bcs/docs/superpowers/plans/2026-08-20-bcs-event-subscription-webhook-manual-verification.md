# BCS Event Subscription / Webhook 人工验证测试计划

> 日期：2026-08-20
> 对应设计：`../specs/2026-08-18-bcs-event-subscription-webhook-design.md`
> 对应开发计划：`2026-08-19-bcs-event-subscription-webhook.md`

## 1. 目的

本文用于人工验收 BCS Event Subscription / Webhook MVP，验证从业务事实提交、Event 记录、Subscription 匹配、
Delivery 调度到接收方接收的完整链路，并重点覆盖自动重试、严格顺序、DLQ、replay/skip、消息持久化失败和出站安全。

人工验收用于确认真实进程、真实网络和真实存储下的集成行为，不替代仓库中的 Contract、Conformance、OpenAPI 和
Cargo 自动化测试。发布前两类测试都必须通过。

## 2. 验收范围

### 2.1 MVP 范围

- Subscription scope 仅为 `group`，自动包含该 Group 下的 Session、Task、Message 和 State Machine Run Event；
- sink 仅为 Webhook；
- payload 支持 `metadata_only` 和 `full`；
- ordering 固定为 `strict_per_stream`；
- 支持独立 Subscription API 和建群时内联 Subscription；
- 支持同步 test、自动 retry、DLQ、replay、skip 和 HTTP 410 自动禁用；
- 验证首版 16 个 Event type；
- SQLite 至少执行完整 P0；MySQL 在准生产环境执行存储、并发和多实例用例。

### 2.2 明确不在本次验收范围

- Group 之外的 Subscription scope；
- Webhook 之外的 sink；
- 跨 stream 的全局到达顺序；
- exactly-once 投递；
- 历史 Event 任意 backfill 或按 event type/time 的全局检索；
- 失败、取消、超时、skipped 等尚未进入 MVP Catalog 的业务 Event；
- ManagerWorker Task Ledger 的跨进程持久化；
- durable inbox、Bot callback 恢复队列和既有 callback 链路的可靠性重构；
- Subscription 级 HMAC、OAuth、Bearer、自定义 Header 或 URL query 鉴权；内部服务身份由部署基础设施统一处理。

上述能力不得因为本计划未覆盖而被视为已支持。

## 3. 通过标准与优先级

| 优先级 | 定义 | 发布要求 |
| --- | --- | --- |
| P0 | 核心功能、数据一致性、顺序、可靠性或安全 | 必须全部通过，不能带已知规避项发布 |
| P1 | 管理完备性、并发、恢复和可运维性 | 必须通过；环境不具备时需负责人书面接受风险 |
| P2 | 长时间运行、容量和辅助诊断 | 可独立安排，但不能用来替代 P0/P1 |

单个用例通过必须同时满足：

1. 业务 API 或 Workbench 操作结果符合预期；
2. Delivery 查询结果与接收方实际请求一致；
3. Event envelope、scope、stream 和 data 符合 Contract；
4. BCS 日志、错误和 API 响应没有完整 URL/path、完整请求/响应 body 或内部 endpoint 泄漏；
5. 没有与当前用例不相关的 Error、panic、worker 退出或数据残留。

## 4. 环境与角色

### 4.1 环境矩阵

| 环境 | 存储 | Webhook | 用途 |
| --- | --- | --- | --- |
| Local | SQLite | loopback HTTP，可编程响应 | 全量 P0、CRUD、Catalog、retry/DLQ、单实例恢复 |
| Staging | MySQL | 受控 HTTPS 域名 | P0 复验、生产 URL policy、并发、多实例和持久化恢复 |

不得在共享生产数据库执行故障注入、建触发器、缩短重试窗口或批量并发创建。

### 4.2 测试身份

准备以下 Gateway Principal；其生成方式由被测环境提供，证据中不得保存原始 token：

- `ADMIN_A`：测试 Group 的 creator/driver，具有 Subscription 管理权限；
- `MEMBER_B`：Group 普通成员，但不具有 Subscription 管理权限；
- `OUTSIDER_C`：不属于 Group；
- 至少一个可用 manager Bot、worker Bot 和状态机节点 Bot。

公共 V1 请求通过 `X-Avernet-Principal: <compact-jwt>` 传递身份，不使用 `Bearer` 前缀。JWT 内应包含环境要求的
Human 与 App Principal。

### 4.3 可编程 Webhook Receiver

仓库提供一个最小本地 Receiver：

```bash
python3 src/bcs/scripts/event_webhook_receiver.py
```

默认地址为 `http://127.0.0.1:28082/events`。它打印每个 Event 的结构化日志并固定返回 204，可用于基础事件和
payload 检查。以下可编程故障行为尚不包含在这个最小工具中；执行 retry、DLQ 和安全故障用例时，需扩展该工具
或使用外部可编程 Receiver。

Receiver 必须能按请求或 `event_type` 配置以下行为：

- 始终返回 204；
- 前 N 次返回指定状态，之后返回 204；
- 固定返回 400、410、429、500 或 503；
- 返回 `Retry-After`；
- 延迟响应；
- 返回 302 到另一个受控地址；
- 返回大于 4 KiB 的响应 body；
- 记录到达时间、连接并发数和原始 body bytes；
- 按 `event_id` 去重，同时保留重复 Attempt 的原始记录用于验收。

Receiver 的日志属于测试敏感数据。只使用合成业务内容，验收结束后删除原始 body 日志。

### 4.4 本地配置

在隔离配置目录中基于 `configs/bcs-config-local.toml` 启用以下差异；不要直接覆盖共享开发配置：

```toml
store_messages = true

[eventing]
enabled = true
dispatcher_enabled = true
fanout_poll_interval_ms = 100
delivery_poll_interval_ms = 100
worker_concurrency = 8
per_host_concurrency = 2
lease_ms = 3000
drain_timeout_ms = 10000
event_retention_days = 30

[eventing.retry]
base_delay_ms = 200
max_delay_ms = 1000
max_attempts = 3
max_elapsed_ms = 5000

[eventing.webhook]
connect_timeout_ms = 500
request_timeout_ms = 2000
max_request_timeout_ms = 30000
max_event_body_bytes = 262144
max_response_body_bytes = 4096
allow_http_loopback = true
allow_non_standard_ports = true
```

可以使用 `./scripts/singlebox.sh --local start bcs` 启动 BCS，健康检查地址默认为
`http://127.0.0.1:21000/health`。如果 singlebox 会生成运行时配置，应先确认最终生效文件确实包含上述差异。
仓库的 `bcs-config-local.toml` 默认启用 Event 记录和 dispatcher；MV-01 中的 disabled/record-only 模式需要
作为用例显式覆盖，测试结束后恢复本地默认值。

### 4.5 测试数据

至少准备：

- `G_CHAT_A`：自由聊天群；
- `G_MW_A`：ManagerWorker 群；
- `G_SM_A`：configured State Machine 群，定义中至少有两个顺序节点；
- `G_OTHER`：不属于目标 scope 的对照群；
- 一个可稳定成功的状态机定义；
- 一个可令首个节点失败一次后重试成功的状态机定义；
- 一个 one-shot 状态机请求；
- metadata-only 和 full 两个 Subscription；
- 两个独立 Receiver endpoint，用于验证 revision 切换和 Subscription 间隔离。

所有 ID 记录为 opaque string，测试不得依赖 ID 格式。

## 5. 通用接口模板

设：

```bash
BCS_URL="http://127.0.0.1:21000/openapi/v1/collaboration"
PRINCIPAL_JWT="<ADMIN_A compact JWT>"
GROUP_ID="<existing group id>"
HOOK_URL="http://127.0.0.1:<receiver-port>/bcs/events"
```

创建一个覆盖全部 MVP Event 的 Subscription：

```bash
curl -sS -X POST "$BCS_URL/event-subscriptions" \
  -H "X-Avernet-Principal: $PRINCIPAL_JWT" \
  -H 'Content-Type: application/json' \
  -d "{
    \"name\": \"manual-all-events\",
    \"scope\": {\"type\": \"group\", \"id\": \"$GROUP_ID\"},
    \"event_filters\": [
      \"group.*\", \"session.*\", \"task.*\",
      \"state_machine.*\", \"message.created\"
    ],
    \"payload\": {\"mode\": \"metadata_only\"},
    \"sink\": {
      \"type\": \"webhook\",
      \"url\": \"$HOOK_URL\",
      \"request_timeout_ms\": 2000
    }
  }"
```

常用管理请求：

```bash
curl -sS "$BCS_URL/event-subscriptions?scope_type=group&scope_id=$GROUP_ID&limit=20" \
  -H "X-Avernet-Principal: $PRINCIPAL_JWT"

curl -sS "$BCS_URL/event-subscriptions/$SUBSCRIPTION_ID" \
  -H "X-Avernet-Principal: $PRINCIPAL_JWT"

curl -sS "$BCS_URL/event-subscriptions/$SUBSCRIPTION_ID/deliveries?limit=100" \
  -H "X-Avernet-Principal: $PRINCIPAL_JWT"

curl -sS "$BCS_URL/event-deliveries/$DELIVERY_ID" \
  -H "X-Avernet-Principal: $PRINCIPAL_JWT"
```

接口成功响应均使用 V1 envelope，业务对象位于 `data`。证据中保存 HTTP status、`request_id`、脱敏后的 response 和
Receiver 记录；不得保存 `PRINCIPAL_JWT` 或完整 Webhook URL/path。

## 6. 执行顺序和用例总览

建议按以下顺序执行，避免前置 DLQ 或 disabled Subscription 污染后续结果：

| 阶段 | 用例 | 优先级 |
| --- | --- | --- |
| A | MV-01～MV-04 配置、启动、内联建群和独立创建 | P0 |
| B | MV-05～MV-09 CRUD、权限、过滤、配额和 test | P0/P1 |
| C | MV-10～MV-14 16 类 Producer 和 payload | P0 |
| D | MV-15～MV-22 顺序、retry、DLQ、replay/skip、410 | P0 |
| E | MV-23～MV-27 SSRF、脱敏和限制 | P0/P1 |
| F | MV-28～MV-33 原子性、重启、多实例和兼容性 | P0/P1 |

每个破坏性用例使用新的 Group 或 Subscription。不要复用一个已进入 DLQ、disabled 或 deleted 状态的 Subscription。

## 7. 详细用例

### MV-01 Eventing 开关和启动校验（P0）

步骤：

1. 以 `eventing.enabled=false`、`dispatcher_enabled=false` 启动；
2. 创建不含内联 Subscription 的普通 Group；
3. 调用独立 Subscription 创建 API，并尝试创建含内联 Subscription 的 Group；
4. 改为 `enabled=true`，启动并检查 health；
5. 分别加入一个未知 `[eventing]` key 和一个越界值，确认配置校验失败。

预期：

- Eventing 关闭时普通业务仍成功，Subscription 创建明确返回 `eventing_disabled`，不能静默接受；
- Eventing 启用且配置有效时正常启动，未知字段和越界值阻止启动。

### MV-02 独立 Subscription 创建与脱敏读取（P0）

步骤：使用第 5 节模板创建 Subscription，再执行 list/get。

预期：

- POST 返回 201，status 为 `active`，revision 为 1；
- `include_descendants=true`，`ordering.mode=strict_per_stream`；
- scope 固定为 `{type: group, id: GROUP_ID}`；
- list/get 不返回原始 URL 或 URL path；
- sink 只暴露 `scheme`、`host`、`path_hash` 和 timeout，不包含 `auth`。

### MV-03 建群内联 Subscription（P0）

步骤：

1. 在一个正常的 `POST /groups` 请求中加入：

   ```json
   {
     "event_subscriptions": [{
       "name": "inline-observer",
       "event_filters": ["group.*", "session.*"],
       "payload": {"mode": "metadata_only"},
       "sink": {
         "type": "webhook",
         "url": "<HOOK_URL>"
       }
     }]
   }
   ```

2. 等待 Receiver 收到事件；
3. 用返回的 Group ID 查询 Subscription；
4. 再以非法 URL 创建另一个内联 Subscription Group。

预期：

- 建群返回 201，并返回脱敏后的 active Subscription 摘要；客户端不能传 scope、descendants 或 ordering；
- Receiver 收到该 Group 的 `group.created` 和初始 `session.created`；
- 两个事件的 scope 正确。若两者存在显式 causation，`group.created` 必须先成功，随后才投递 `session.created`；
- 非法 Subscription 使整个 provisioning 失败，不留下可读 Group、Session 或 active/pending Subscription。

### MV-04 基础 Envelope（P0）

对 MV-03 的每个请求检查：

- Method 为 POST，Content-Type 为 `application/json; charset=utf-8`；
- `spec_version=1.0`、`schema_version=1.0`、`source=bcs`；
- `event_id`、`event_type`、`occurred_at`、`recorded_at`、subject、scope、stream、data 完整；
- `stream.sequence` 从正整数开始且同一 stream 单调递增；
- 除标准 JSON Content-Type 外不存在业务 Header，Event Envelope body 是唯一权威事件数据；
- raw body 与 Delivery 持久化的 body/hash 一致，自动重试不会重新投影或重新序列化。

### MV-05 PATCH、乐观锁和 revision 切换（P0）

步骤：

1. 用 body `revision=1` 修改 name；
2. 用 `If-Match: "2"` 修改 filter 或 endpoint；
3. 用旧 revision 再次 PATCH；
4. 同时提供不一致的 body revision 和 If-Match；
5. 提交 `null`、空 patch 和 unknown field；
6. 在旧 endpoint 制造 pending/retry Delivery，再修改 URL。

预期：

- 每次有效配置修改产生新的 immutable revision，返回 revision 单调增加；
- strong numeric If-Match 可用，`*`、weak ETag、多 revision 和 0 被拒绝；
- stale revision 返回 409 `event_subscription_revision_conflict`；
- 不一致 revision、null、空 patch 和 unknown field 返回 400；
- 新事件只使用新 revision；旧 revision 尚未开始的 target/pending/retry Delivery 被取消，不能发往新 endpoint；
- Receiver A 的旧 pending/retry Delivery 被取消，不会改投 Receiver B。

### MV-06 disable、enable 和 delete（P0）

步骤：依次 PATCH status 为 disabled、触发事件、PATCH 回 active、再次触发、最后带当前 revision DELETE。

预期：

- disabled 期间新事件不产生该 Subscription 的 Delivery，未开始的旧投递被取消；
- 恢复 active 创建新 revision，之后的新事件恢复投递，不自动回补 disabled 期间事件；
- DELETE 返回 200 和脱敏的 `deleted` 摘要；
- deleted Subscription 不能恢复，之后不再匹配事件；
- 缺少 revision 的 PATCH/DELETE 被拒绝。

### MV-07 权限和资源不可见性（P0）

步骤：分别用 `ADMIN_A`、`MEMBER_B`、`OUTSIDER_C` 对同一 Subscription 执行 create/list/get/PATCH/DELETE/test/
replay/skip，并用无效或缺失 Principal 调用。

预期：

- 只有现有 Group 管理身份可以执行写操作；
- 无读取权限的详情和 Delivery 查询返回 404 或 Contract 允许的不可见结果，不泄漏资源存在性；
- 有资源可见性但无管理权限的写操作返回 403；
- 缺失/无效 Principal 返回 401；
- full payload 还必须经过单独授权，不能仅凭 Group 可见性获得。

### MV-08 filter、scope 隔离和分页（P1）

步骤：

1. 分别创建 exact `message.created`、family `state_machine.*` 和全量 Subscription；
2. 提交未知 family、非法通配、重复 filter 和超过 64 个 filter；
3. 在 `G_CHAT_A` 与 `G_OTHER` 各触发事件；
4. 创建超过 20 条可查询记录后测试 cursor/limit，测试 limit 0 和 101；
5. deliveries 按 `status` 分页过滤。

预期：

- exact 只收到指定 type，family 只收到登记 family，未匹配事件不产生 Delivery；
- Group Subscription 收到其 descendant Event，但绝不收到其他 Group 的 Event；
- 非法 filter 返回 400 `invalid_event_filter`；
- scope type 只能是 group，scope_type/scope_id 必须成对出现；
- 默认 limit 20、最大 100，cursor 无重复或遗漏，非法 limit 返回 400。

### MV-09 配额并发与同步 test（P1）

步骤：

1. 对空 Group 并发发起 11 个不同名称、相同合法配置的创建请求；
2. 统计成功项和 `event_subscription_limit_reached`；
3. 对一个 active Subscription 调用 `POST /event-subscriptions/{id}:test`；
4. Receiver 对 test 返回一次 500，再改为 204 重试调用；
5. 比较调用前后的普通 Delivery 列表。

预期：

- 默认配额下最多 10 个 active Subscription；并发不能突破上限；
- test body 的 type 为 `event_subscription.test`，同步返回脱敏结果；
- 单次 test 失败返回 422 `event_subscription_test_failed`，不自动重试；
- test 不进入业务 Event Catalog、不匹配其他 Subscription、不占用正常 stream sequence，也不新增普通 Delivery 历史。

### MV-10 Group 与 Session 生命周期事件（P0）

按下表操作并逐条核对 Receiver 与 Delivery 查询：

| Event type | 人工触发 | 必查内容 |
| --- | --- | --- |
| `group.created` | 使用内联 Subscription 创建 Group | subject=group；scope.group_id；stream=`group:{group_id}` |
| `group.participant.added` | 向 Group 添加 Human/Bot | actor_id/type、role、mode、group_version |
| `group.participant.removed` | 移除该成员 | previous_role、reason、group_version；重复无变化操作不再发事件 |
| `session.created` | 创建新 Session | scope 含 group_id/session_id；stream=`session:{session_id}` |
| `session.participant.added` | 向 Session 添加 Bot | role、mode、visible_from_seq |
| `session.participant.removed` | 移除 Session 成员 | previous_role、reason；重复无变化操作不再发事件 |

每项业务 API 成功只产生一个对应 Event；业务失败、权限失败和幂等 no-op 不产生 Event。

### MV-11 ManagerWorker Task 与 Session 完成（P0）

步骤：

1. 在 `G_MW_A` 启动正常协作；
2. 由 manager 执行 assign task，记录 task ID；
3. worker 返回并被 manager 接收为 final result；
4. manager 执行 `bcs_task_complete` 完成协作 Session；
5. 重复提交已完成 task 的 terminal callback 或完成命令。

预期：

- assign 被接受后产生一个 `task.assigned`，stream 为 `task:{task_id}`；
- worker final result 被验证并接收后产生一个 `task.completed`，与 assigned 使用同一 task stream；
- manager 完成整个协作产生 `session.completed`，不额外伪造共享的 `task.completed`；
- task data 的 assignment/result 遵循 payload projection；
- 已识别的重复 terminal callback 不产生第二个 task/message Event；
- 不要求未持久化 Task Ledger 在进程重启后恢复，这是本次明确非目标。

### MV-12 configured State Machine 完整序列（P0）

在 `G_SM_A` 执行至少两个顺序节点的成功 run。

预期同一 `state-machine-run:{run_id}` stream 中严格按 sequence 到达：

1. `state_machine.run.created`，`run_mode=configured`；
2. `state_machine.run.started`；
3. 每个节点各一个 `state_machine.node.started`；
4. 每个成功节点各一个 `state_machine.node.completed`；
5. `state_machine.run.completed`。

所有 Event scope 都含 group_id、session_id、run_id；node Event subject 为 node。并行节点允许实际完成时间交错，但
对外 sequence 唯一，Receiver 对同一 run 的到达顺序必须与 sequence 一致。
逐个检查 `state_machine.node.started.data.predecessor_node_ids`：初始节点应为 `[]`，顺序节点应包含一个
直接前序 ID，fan-in 节点应包含全部直接前序 ID，且数组无重复并按 ID 升序排列。

### MV-13 retry 节点与 one-shot State Machine（P0）

步骤：

1. 执行一个首个节点失败一次、随后成功的 run；
2. 执行一个 one-shot 状态机；
3. 分别按 run_id 汇总事件。

预期：

- 失败 attempt 产生 `state_machine.node.retry_scheduled`，包含 attempt、next_attempt、max_attempts、retry_at 和稳定 reason；
- 下一 attempt 再产生 node.started，成功后 node.completed；
- one-shot 独立产生 run.created、run.started、node 事件和 run.completed；
- one-shot 的 run.created/run.started 是两个不同 sequence，`run_mode=one_shot`；
- 内部 ready/judge/thinking/tool/failed 细分状态不被直接映射为公共 Event。

### MV-14 Message 和 payload projection（P0）

步骤：

1. 对同一 Group 建立 metadata-only 与 full 两个 Subscription；
2. 在自由聊天群各发送一条 Human chat 和一条 Bot final chat；
3. 在 ManagerWorker 群触发一个有多个 owner 可见物理副本的逻辑消息；
4. 触发 streaming delta、thinking、tool start/end 和内部协调 echo；
5. 发送含 UTF-8 多字节字符和测试附件的最终可见消息。

预期：

- 每条成功持久化的外部可见逻辑 chat 只产生一个 `message.created`；
- stream 为 `session:{session_id}`，subject.id 等于 logical_message_id；
- 多个 owner 物理副本共享 logical ID，只有一个外部 Event；
- streaming delta、thinking、tool 事件和内部 echo 不产生 `message.created`；
- metadata-only 的 content `included=false`，不含 text/json、preview、sha256 或其他正文派生标识；
- full 可以包含最终可见正文和 sha256，但不含内部 prompt、thinking、原始 tool arguments、token、object handle、内部 URL
  或 share token；
- attachment 只含 file_id、name、media_type、size_bytes、status；
- 截断时 `truncated=true`，UTF-8 字符不能被截断成非法 bytes。

### MV-15 同一 stream 严格顺序（P0）

步骤：

1. Receiver 对某个 run 的第一个 `state_machine.node.completed` 首次返回 500，之后返回 204；
2. 让业务继续产生后续 node.started/node.completed；
3. 在首次 500 后、重试成功前查询 Delivery 并检查 Receiver；
4. 重试成功后再次检查。

预期：

- head Delivery 进入 `retry_wait`；同一 Subscription、同一 run stream 的更大 sequence 保持 pending 且未到达 Receiver；
- head 成功后，lane 自动继续并按 sequence 投递后续 Event；
- 其他 stream、其他 Subscription 不被这个 lane 阻塞。

### MV-16 自动重试身份和 body 稳定性（P0）

Receiver 前两次返回 500、第三次返回 204。比较三次请求：

- Event Envelope 中的 `event_id` 和 raw body bytes 完全相同；
- Delivery 详情中的 Attempt 编号为 1、2、3；
- Delivery 最终为 succeeded，attempt_count=3，详情中有三条 Attempt 摘要；
- BCS 未重新读取业务资源组装 body，revision 和 payload 保持首次 fanout 快照。

允许接收方在“已处理但响应丢失”的情况下看到重复请求；Receiver 必须按 event_id 幂等。不要把重复 Attempt 判为
exactly-once 缺陷。

### MV-17 Retry-After、timeout 和网络失败（P0）

分别执行：

- 429 + `Retry-After: 2`；
- 503 + HTTP-date Retry-After；
- Receiver 延迟超过 Subscription request_timeout；
- 暂时拒绝连接或中断响应。

预期：均进入 retry；429/503 尊重 Retry-After 且不超过配置 cap/总窗口；timeout 不无限占用 worker；恢复 Receiver 后
Delivery 成功。非 429/503 的 Retry-After 不影响分类。

### MV-18 terminal 4xx、redirect 和响应 body cap（P0）

步骤：Receiver 分别返回 400、大于 4 KiB body 的 400、302 到第二个受控 endpoint。

预期：

- 400 和 3xx 不自动重试，Delivery 进入 dead_lettered；
- BCS 不跟随 302，第二个 endpoint 零请求；
- 大响应不会造成内存无界增长，API/日志只保留脱敏错误分类，不透传完整 response body。

### MV-19 DLQ 阻塞与 replay 恢复（P0）

步骤：

1. 临时使用 `max_attempts=2`，令某个非末尾 Event 始终返回 500 直到 dead_lettered；
2. 触发同 stream 后续 Event，确认没有到达；
3. Receiver 改为 204；
4. 调用：

   ```bash
   curl -sS -X POST "$BCS_URL/event-deliveries/$DELIVERY_ID:replay" \
     -H "X-Avernet-Principal: $PRINCIPAL_JWT" \
     -H 'Content-Type: application/json' \
     -d "{\"replay_request_id\":\"manual-replay-001\",\"expected_subscription_revision\":$REVISION}"
   ```

预期：

- DLQ 是 strict lane blocker；后序保持 pending；
- replay 返回 202，replacement 使用当前 active revision；
- replacement 有新 delivery_id，但保留原 event_id、sequence 和 canonical body 语义；
- replacement 成功后原 Delivery 记录 resolved_by_delivery_id，lane 继续；
- 相同 replay_request_id 重试是幂等的；并发 replay 最多产生一个非终态 replacement；
- 对非 DLQ、已解决或过期 Delivery replay 返回 409。

### MV-20 skip 恢复与审计（P0）

创建新的 unresolved DLQ，再调用：

```bash
curl -sS -X POST "$BCS_URL/event-deliveries/$DELIVERY_ID:skip" \
  -H "X-Avernet-Principal: $PRINCIPAL_JWT" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"manual verification: receiver intentionally rejects this event"}'
```

预期：Delivery 变为 skipped，记录 actor/time/reason，lane 继续；空 reason、非 DLQ、已解决 Delivery 被拒绝。skip 是
显式数据丢失确认，Receiver 不应再收到被 skip 的 Event。

### MV-21 HTTP 410 自动禁用（P0）

步骤：让 Receiver 对 active Subscription 返回 410，并在此之前准备 pending/retry Delivery。

预期：当前 Attempt 终结，Subscription 自动 disabled；其未开始 target 和 pending/retry Delivery 被取消；之后新 Event
不再投递。已发送的请求不能撤回，不要求回滚业务事实。

### MV-22 跨 stream 和因果边界（P1）

步骤：并行执行两个 state-machine run、两个 task，并在 Session 中持续发消息；另建两个 Subscription，其中一个只
匹配 effect Event。

预期：

- 每个 stream 内按 sequence 到达；不同 run/task/session stream 可并行，不能断言它们的全局先后；
- 一个 lane 的 retry/DLQ 不阻塞其他 stream；
- 同时匹配 cause/effect 且 Event 显式携带 causation 时，effect 等待 cause；
- 只匹配 effect 的 Subscription 不因缺少 cause Delivery 永久阻塞；
- task.completed 与 message.created 若无显式 causation，不能按到达先后判断业务因果。

### MV-23 URL 静态校验（P0）

创建或 PATCH 以下 URL：HTTP 公网、ftp/file、自定义 scheme、userinfo、query、fragment、空 host、非标准端口、
loopback/private/link-local/metadata/组播/保留 IPv4 与 IPv6。

预期：

- production 只接受满足策略的 HTTPS；
- local 只有显式开启时才接受 loopback HTTP 和非标准端口；
- query、fragment、userinfo 始终拒绝；
- 拒绝返回 400 `invalid_webhook_url`，不向目标发请求；
- production 配置若试图弱化私网阻断、loopback 或端口策略，进程启动失败。

### MV-24 每次 Attempt 的 DNS/SSRF 校验（P0，Staging）

使用受控 DNS 测试以下场景：所有解析结果安全、结果集中混入私网地址、创建后 DNS 改指私网、redirect 指向私网、
IPv4/IPv6 rebinding。

预期：每次 Attempt 重新解析并校验全部候选；任一不安全候选都阻止连接；连接使用已校验并 pin 的地址；代理和
redirect 不能绕过 guard。安全阻断应产生脱敏日志/metric，目标私网服务零请求。

### MV-25 URL 和日志脱敏（P0）

使用唯一合成 marker 作为 URL path 和内部测试字段，执行 create/get/list/PATCH/test/retry/DLQ 后扫描：

- API responses；
- BCS 普通日志和 error 日志；
- metric labels；
- Delivery/Attempt 管理查询；
- Event body。

预期：完整 URL/path、完整 response body、内部 token/endpoint 不出现；URL 读取结果只含 scheme、host 和 path_hash。
日志只含允许的 ID、脱敏 host/hash、status、latency 和 error category。

### MV-26 payload/body 和 timeout 边界（P1）

步骤：测试 1 KiB 以下和超过 256 KiB Event body；1 秒以下/1～30 秒/30 秒以上 request timeout；full payload 权限
不足；另外在 create/PATCH 中加入旧 `auth` 字段。

预期：只接受部署允许的 1～30 秒 timeout；旧 `auth` 字段按 unknown field 返回 400 `invalid_request`；不可安全投影
的超限业务 mutation 在提交前失败，或按 Contract 产生明确 metadata-only/truncated 内容，不能静默伪装为完整；
权限不足不能开启 full。

### MV-27 Group 删除清理（P0）

对一个有 active Subscription、pending/retry Delivery 的隔离 Group 执行删除。

预期：Group 删除事务把 Subscription disabled，并取消尚未开始的 target 和 pending/retry Delivery；新事件不再投递。
已经 in-flight 或已被远端处理的 HTTP 请求无法撤回，不据此判失败。

### MV-28 message 写失败不再继续（P0，隔离 SQLite/MySQL）

在隔离数据库中使用可恢复的 failpoint，或临时建立只针对 `bcs_messages` INSERT 抛错的数据库 trigger：

1. 发送一条带唯一 marker 的用户消息；
2. 观察调用方、Bot/Frontend、Receiver 和数据库；
3. 移除 failpoint/trigger 后重试同一逻辑请求。

预期：

- 第一次请求向上返回 persistence error，不再伪装成功；
- Bot 不收到该消息，Receiver 不收到 message.created；
- 不存在只写 Event 未写 message 的半成品；
- 恢复后请求可以成功，并只产生一个逻辑 message/Event；
- 日志记录错误类别但不包含正文或数据库内部细节。

记录并清理 trigger 的完整名称。若环境无法定点注入，只能将本用例标记为 blocked，不能用“关闭整个数据库后接口失败”
替代原子性证明。

### MV-29 Event 写失败回滚业务 mutation（P0，隔离 SQLite/MySQL）

临时令 `bcs_events` INSERT 对一个唯一测试 operation 失败，执行 message append 或其他已声明为同事务的业务 mutation。

预期：业务写和 sequence 分配一起回滚；没有“业务成功但 Event 永久丢失”，也没有对外可见 sequence 空洞。移除
failpoint 后同一请求正常成功。不得在共享数据库执行。

### MV-30 dispatcher 暂停与恢复（P0，持久化 Store）

步骤：

1. 使用 SQLite/MySQL 持久化 Event Store，以 `enabled=true`、`dispatcher_enabled=false` 启动；
2. 触发多个事件并确认 Receiver 零请求，但 Event/target/Delivery 已记录；
3. 保持同一数据库和 env，改为 `dispatcher_enabled=true` 后重启。

预期：关闭 dispatcher 只暂停投递，不停止 Event 记录/fanout；恢复后积压继续发送，event_id、revision、sequence 不变，
且不需要重做业务操作。

### MV-31 进程重启和至少一次语义（P0）

分别在 pending、retry_wait，以及 Receiver 已收请求但尚未响应时终止 BCS，再用同一数据库和 env 启动。

预期：领取后立即存在 `completed_at/result` 为空的 Attempt；lease 到期后未完成 Delivery 恢复，不永久丢失或卡死，
上一条 Attempt 被补记为 `retryable`、`error_category=lease_expired`，错误摘要说明远端结果未知，并创建新的执行中
Attempt。最后一种场景允许重复请求，Receiver 通过 event_id 去重。已 succeeded 的 Delivery 不应因普通重启重新投递。

### MV-32 多实例 claim、lease heartbeat 和并发限制（P1，MySQL）

步骤：

1. 两个 BCS 实例连接同一 MySQL/env；
2. 使用延迟时间长于 lease_ms 的 Receiver，同时制造超过 worker_concurrency 的积压；
3. 记录同一 delivery_id 的并发连接数、总并发和单 host 并发；
4. 在一个实例持有 lease 时结束该实例，等待另一个恢复。

预期：

- lease heartbeat 有效时，同一 Delivery 不被另一实例并发领取；
- 总 in-flight 不超过 worker_concurrency，单 host 不超过 per_host_concurrency；
- 一个实例退出后 lease 到期可恢复；
- 不同 Subscription/stream 可以并行；
- 崩溃窗口仍是 at-least-once，不宣称 exactly-once。

### MV-33 兼容性和旧 callback 共存（P1）

步骤：分别在 Eventing disabled/enabled 下执行原有自由聊天、ManagerWorker 和状态机主流程；如 Group 同时配置旧
callback 与新 Subscription，再完成一次 Session。

预期：不配置 Subscription 时现有请求/响应兼容；Eventing 不改变原有业务成功路径；旧 callback 与新 Event 是两条
独立通知，可能都到达，不能把它们互相去重或自动改写；旧 callback 不能因本功能被移除。

## 8. 首版 Event Catalog 检查表

执行 MV-10～MV-14 后，按实际 Receiver 记录逐项勾选。缺少任何一项均为 P0 不通过。

| 完成 | Event type | 预期 stream |
| --- | --- | --- |
| [ ] | `group.created` | `group:{group_id}` |
| [ ] | `group.participant.added` | `group:{group_id}` |
| [ ] | `group.participant.removed` | `group:{group_id}` |
| [ ] | `session.created` | `session:{session_id}` |
| [ ] | `session.completed` | `session:{session_id}` |
| [ ] | `session.participant.added` | `session:{session_id}` |
| [ ] | `session.participant.removed` | `session:{session_id}` |
| [ ] | `task.assigned` | `task:{task_id}` |
| [ ] | `task.completed` | `task:{task_id}` |
| [ ] | `state_machine.run.created` | `state-machine-run:{run_id}` |
| [ ] | `state_machine.run.started` | `state-machine-run:{run_id}` |
| [ ] | `state_machine.node.started` | `state-machine-run:{run_id}` |
| [ ] | `state_machine.node.completed` | `state-machine-run:{run_id}` |
| [ ] | `state_machine.node.retry_scheduled` | `state-machine-run:{run_id}` |
| [ ] | `state_machine.run.completed` | `state-machine-run:{run_id}` |
| [ ] | `message.created` | `session:{session_id}` |

Envelope 和 data 可以对照以下权威文件复核：

- `api-contracts/events/v1/catalog.yaml`；
- `api-contracts/events/v1/event-envelope.schema.json`；
- `api-contracts/events/v1/content-projection.schema.json`；
- `api-contracts/events/v1/fixtures/`。

## 9. 可观测性检查（P1）

若构建启用 Prometheus metrics，至少确认实际增加的计数器：

- `bcs_event_produced_total`，按 family/result/error_category 区分；
- `bcs_event_fanout_failed_total`；
- `bcs_event_delivery_attempt_total`，按 family/result/status_class/error_category 区分；
- `bcs_event_webhook_guard_blocked_total`。

Metric label 不得包含 subscription_id、event_id、group_id 或完整 URL/path。若部署还有 pending、retry、DLQ、
oldest pending 和 blocked lane gauge，应同时验证它们随 MV-15～MV-21 正确变化；不要把 Spec 建议但当前二进制未暴露的
metric 当成已经实现。

日志至少能通过 subscription_id/event_id/delivery_id/request_id 关联一次操作，但不能依赖完整 body 排障。

## 10. 证据留存

每个用例建立独立目录或测试记录，最少包含：

```text
MV-XX/
  environment.md          # commit、构建、配置差异、DB 类型、实例数；不含凭据
  operation.md            # 操作时间、身份角色、Group/Session/Run/Task ID
  api-request.redacted    # 脱敏请求
  api-response.json       # HTTP status、request_id、脱敏响应
  receiver-summary.json   # event/delivery/attempt、到达时间、raw body hash
  delivery-detail.json    # 管理 API 查询结果
  log-check.md            # 日志扫描结论
  result.md               # PASS/FAIL/BLOCKED、实际结果、缺陷链接
```

默认不归档 raw JWT、完整 URL path、原始 Webhook body、完整外部响应 body或数据库文件。
确需保留 raw body 时只使用合成数据，存入受控位置并注明销毁时间。

单个用例记录模板：

```markdown
### MV-XX <名称>

- 结果：PASS / FAIL / BLOCKED
- 环境/commit：
- 执行人/时间：
- Group / Session / Run / Task：
- Subscription / revision：
- Event / Delivery / Attempt：
- 实际结果：
- 与预期差异：
- 证据位置：
- 缺陷链接与优先级：
- 清理结果：
```

## 11. 缺陷分级

| 级别 | 示例 |
| --- | --- |
| Blocker | 业务成功但 Event 永久丢失；凭据或完整 endpoint 泄漏；SSRF 可访问私网/metadata；同 stream 越序；无法启动或普遍不可用 |
| Critical | 重试/DLQ 无法恢复；错误 410 未禁用；同一逻辑消息稳定地产生多个 Event；跨 Group 泄漏 |
| Major | revision 冲突未拦截；权限错误；配额并发突破；重启后投递卡死；Catalog 字段不符合 schema |
| Minor | 脱敏但不影响安全的诊断字段缺失；提示文案或低风险管理体验问题 |

Blocker/Critical 未关闭不得验收。Major 是否允许延期必须明确说明影响、规避方式、负责人和截止时间，且不能违反 P0
通过标准。

## 12. 清理与回滚

验收结束后：

1. 删除测试创建的 Subscription 和 Group；
2. 移除数据库 failpoint/trigger，验证 schema 恢复；
3. 恢复正常 retry、lease、dispatcher 和 Webhook 安全配置；
4. 停止 Receiver，销毁 raw body 日志；
5. 保留脱敏后的 API/Delivery/metric 证据；
6. 检查没有 pending、retry_wait、in_flight 或 unresolved DLQ 测试数据；
7. Staging 回滚时先 `dispatcher_enabled=false` 停止新投递，再禁用测试 Subscription。

## 13. 最终签字检查表

- [ ] Local P0 全部通过；
- [ ] Staging HTTPS/SSRF P0 全部通过；
- [ ] 16 个 Event type 全部有 Receiver + Delivery 证据；
- [ ] 同 stream 顺序、retry、DLQ、replay、skip、410 全部通过；
- [ ] message/Event 写失败的原子性故障注入通过；
- [ ] URL/path、body、日志和 metric 脱敏检查通过；
- [ ] SQLite 验收通过；MySQL conformance 与人工恢复/并发验收通过；
- [ ] Eventing disabled 时原有业务兼容；
- [ ] 所有 Blocker/Critical 已关闭；Major 延期均有书面接受；
- [ ] 测试数据、trigger、Receiver 日志和临时配置已清理。
