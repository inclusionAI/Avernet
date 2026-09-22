# StateMachine 聊天记录持久化到 bcs_messages

- 日期：2026-09-21
- 状态：已按最小修改范围重新完成 T01–T03，T05 已实现并通过本轮读取验收；按用户决定移除 T04 历史回填与对账。本轮验证见 [tasks.md](./tasks.md)。
- 所属模块：BCS
- 目标：先补齐 StateMachine 消息双写，运行一段时间后通过配置切换为仅从 `bcs_messages` 读取，不回填旧消息。
- 已确认范围：不增加实时工具事件、流式过程的历史持久化能力；切读后不再展示仅存在于运行来源、未写入 messages 的旧状态机消息。
- 实施范围：`src/bcs/`；本文件不授权顺带修改其他模块或仓库根目录的 CI、脚本与配置。

## 1. 依据与术语

架构依据：

- [仓库领域文档规范](../../../../docs/agents/domain.md)
- [架构约束](../../../../docs/arch/arch.rules.md)
- [CI 约束](../../../../docs/arch/ci.enforce.md)
- [BCS 本地规则](../../AGENTS.md)及[分层约束](../../CLAUDE.md)
- [Service API 边界](../../crates/service-api/bcs-service-api/CONTEXT.md)
- [消息存储边界](../../crates/services/bcs-message-store/CONTEXT.md)
- [Session 应用边界](../../crates/application/v1/bcs-app-session/CONTEXT.md)
- [现有历史接口兼容设计](../../docs/plans/2026-08-13-openapi-v1-session-history-legacy-message-compatibility-design.md)

当前系统级 ADR 中没有直接规定 StateMachine 聊天记录存储方式的条目。本提案延续 BCS 的运行状态、消息存储和应用接口边界，不变更其他模块对 Bot 原生聊天历史的所有权。

| 术语 | 本文含义 |
| --- | --- |
| 定义 V1 / V2 | `runtime.state_machine.version`，与 HTTP API 版本、定义修订号 `definition.version` 无关 |
| 定义 V1 | 普通无环状态机定义 |
| 定义 V2 | 支持层级定义及固定次数 Loop，编译为无环执行计划并保存执行节点映射 |
| 执行节点 | 一次 Run 中实际运行的节点；Loop 各轮使用各自的 execution node ID |
| attempt | 同一执行节点的尝试次数；与 Loop 轮次分别标识 |
| 逻辑消息 | 一次开场、一次 HumanInput 提示、一次被接受的节点回复，或一次最终结果发布 |
| 消息投影 | 将已接受的业务事实转换为可独立读取的持久聊天记录 |
| 双写 | 运行状态仍保存到原表，同时可靠写入消息投影；不表示两次网络发送 |
| 运行来源读取 | 当前通过 Run、Node、定义快照及已存消息组合历史的方式 |

文中的“必须”是验收要求；发布步骤和默认批大小是本提案选择，不表示已实现能力。

## 2. 目标与非目标

### 2.1 目标

1. 新产生的范围内消息，在进程重启、节点重试及 Run 结束后仍可恢复。
2. 同一逻辑消息在重复回调、补偿、并发写入和双来源读取中只出现一次。
3. 切读后，StateMachine 聊天记录的正文、身份、时间、角色及可见性不依赖 Run、Node、定义快照、投递 checkpoint 或 Bot 原生历史接口。
4. 覆盖 StateMachine 群，以及 Chat / ManagerWorker 会话内的一次性 StateMachine Run。
5. 保留原有会话访问控制、Human 视角隔离、Bot owner 过滤和已有最终结果发布语义。
6. 允许双写与切读分开发版；双写观察期结束、新查询验证通过后切换，不要求完成旧消息迁移。

“只读 messages”限定为 StateMachine 消息内容的重建。访问接口仍需要 Session、Group 和身份数据进行鉴权；不要求这些数据也迁移到 messages。混合会话中既有普通聊天的原生历史兼容策略不在本次改造范围。

完整持久化保证从目标环境连续启用双写后新接受的消息开始。messages 中已经存在的旧记录继续读取；未落库的旧状态机消息在切读后不展示，不增加运行来源 fallback，也不因等待一段时间而自动补齐。双写启用时间记入发布记录，不新增按时间截断查询的配置或数据字段。

### 2.2 非目标

- 不持久化工具调用起止、工具结果、token delta、thinking 或其他实时过程事件。
- 不新增 Bot 任务下发全文、内部 prompt、judge 日志、重试通知或失败通知到聊天时间线。
- 不改变状态机调度、judge 结果、节点转移、网络重发或取消规则。
- 不以 messages 代替运行数据库恢复工作流，也不删除原运行表和定义快照。
- 不使流程面板的图、当前状态或操作能力脱离运行 API；仅持久化面板入口消息。
- 不开发或执行旧消息批量回填、全量历史对账及对应 `check` / `backfill` 管理命令，也不在查询时迁移旧消息。不提供窗口初始化；已接受事实的故障补写仍在范围内。
- 不承诺恢复历史上从未持久化、已经清除的内容。
- 不增加 StateMachine 对外事件订阅类型，也不因历史补写产生新的 `message.created` 或 IM 通知。
- 不在本次升级公共 HTTP 历史接口的游标协议，见第 9 节。

## 3. 当前实现与缺口

| 内容 | 现状 | 本次处理 |
| --- | --- | --- |
| 开场 / 面板入口 | `state_machine_panel` 已持久化，有原始 opening checkpoint | 复用原正文与稳定身份，纳入统一读与故障恢复 |
| V1 节点输出 | 主要由 Node 的 `artifact_text` 动态生成 | 补齐新接受回复的持久化，不批量迁移旧输出 |
| V2 节点输出 | Completed 后保存 `state_machine_output` | 保留已有数据；扩展到实际接受回复的时点 |
| HumanInput 提示 | 主要根据定义、节点和参与者动态生成 | 激活时冻结正文、受众及关联信息并持久化 |
| HumanInput 回复 | V1 依赖 Node，V2 完成后作为 output 保存 | HTTP、Channel 等入口统一使用接受回复的持久事实 |
| Chat 会话最终结果 | 发布前保存 Public `chat` 消息 | 保持原发布 checkpoint、消息 ID 和网络投递规则 |
| 工具及流式过程 | 可实时推送，但不属于完整的持久历史 | 不纳入本次范围 |

当前历史读取按 Session 加载所有 Run，再逐个加载 Node、快照及输出；messages 有记录仍不意味着读取已与运行数据解耦。Human Participant 和一次性 Run 还有额外的合并路径。

当前消息身份混用 `message_id`、`client_msg_id` 和动态生成 ID。特别是长 output ID 入库后使用哈希，而某些合并路径仍使用原始 client key，存在重复展示风险。

当前 Node 行保存当前 attempt 的状态；重试会清除或替换 artifact。仅在 Completed 时生成消息会遗漏已接受、正在判定或随后判定失败的回复。

## 4. 总体方案

```text
业务入口授权、校验 Run / Node / attempt
                |
                v
一次本地事务：状态变更 + 不可变历史消息 checkpoint
                |
                v
幂等写入 bcs_messages + 确认 checkpoint
                |
                v
继续既有推进 / 返回成功 / 发布既有通知

有界恢复扫描 ------> 重试未确认的消息投影（包括终态 Run）

过渡期读取：运行来源 + 已存消息 -> 按逻辑身份选一份 -> 权限、排序、分页
最终读取：鉴权 -> bcs_messages -> 公共 GroupMessage 投影
```

本提案选择复用现有 `bcs_collaboration_delivery_checkpoints` 保存未完成消息投影的可靠事实，新增稳定的 `operation_kind = history_message`。现有 `run_opening` 已有本地历史写入的先例。

这样可以让“状态已接受、消息尚未写入”的窗口具备可恢复证据，不要求 Runtime 在两个 Repo 调用之间伪装原子事务，也不引入新的通用任务队列或网络投递状态机。代价是新消息增加一份 checkpoint payload 和确认写；成本与扫描上限见第 12 节。

已有 opening / Chat result checkpoint 继续承担各自职责；不再为同一消息复制第三种 checkpoint。它们必须提供与本提案相同的补写、身份校验及终态覆盖保证。

## 5. 消息集合及数据契约

### 5.1 纳入的逻辑消息

| 逻辑种类 | 新记录 `message_type` | 生产时点 | 默认受众 |
| --- | --- | --- | --- |
| panel | `state_machine_panel` | 原始开场内容确定并保存后 | Public |
| human_input_prompt | `state_machine_human_input_prompt` | HumanInput attempt 成功激活时 | 激活时有权处理该节点的 Human 集合 |
| output：Bot | `state_machine_output` | 当前有效 attempt 的回复被接受时 | FullOnly |
| output：Human | `state_machine_output` | 有权 Human 的回复被接受时 | 实际回复者 Directed |
| published_result | `chat` | 既有 Chat 最终结果发布 checkpoint 确定后 | Public |

Human 回复统一属于 output，不再同时新增一条 `state_machine_human_input_response`。已有这种类型的记录仍可读取，只有元数据证明对应同一逻辑 output 时才能归并。

同一节点产物和“由发起 Bot 发布到普通聊天的最终结果”是两种业务消息，即使正文相同也不能按正文去重。是否显示某种消息由当前会话和视角的历史投影规则决定。

### 5.2 存储字段与关联

| 字段 | 约束 |
| --- | --- |
| `message_id` | 稳定物理身份；新增记录长度必须适配 MySQL 的 64 字节约束 |
| `group_id` / `session_id` / `env` | 原业务归属，禁止根据“最新 Session”推断 |
| `session_seq` | 由消息仓储原子分配的物理序号；幂等重试不额外占号，参与者加入仍记录此物理位置 |
| `run_id` | 存储层的 StateMachine Run ID；不能误填 Provider / Bot delivery run ID，也不能直接复制为公共 DTO 的对话轮次 |
| `sender_id` / `sender_type` | 实际回复者或既有 BCS 开场发送者 |
| `message_type` | 上表定义的稳定消息类型 |
| `content` | 展示正文及完整消息元数据快照 |
| `client_msg_id` | 复用已有 producer key；不作为数据库并发唯一约束 |
| `created_at` | 业务事实首次产生的毫秒时间，不使用重试或恢复时的当前时间 |
| `visibility_domain` | `state_machine`，与外层 Group strategy 独立 |
| `audience` | Public / Directed / FullOnly，由生产者显式声明 |
| `owner_bot_id` | 保持现有消息归属规则；不能用它代替 Human audience |

Run 关联使用环境范围内已有的 `run_id`；节点及 attempt 关联使用 JSON 元数据，不新增关系列、外键或关联表。节点行中的单个 `message_id` 无法表示多种消息和多次 attempt，不增加此类反向指针。普通 Chat 的窗口在查询侧排除新增投影，不新增列、计数器或索引，见第 8.2 节。

### 5.3 content 结构

新增 prompt / output 使用现有对象结构，保存格式标识 `history_schema_version: 1`；已有 panel / opening / published_result 保留原写入结构。该标识同时表示接受事实产生的补充历史投影，与状态机定义 V1 / V2 无关：

```json
{
  "text": "已接受的回复正文",
  "bot_name": "处理助手",
  "metadata": {
    "state_machine": {
      "history_schema_version": 1,
      "run_id": "run-id",
      "definition_id": "definition-id",
      "definition_version": 3,
      "node_id": "execution-node-id",
      "attempt": 0,
      "event": "output",
      "status": "running"
    }
  }
}
```

- `node_id`、`attempt` 对节点消息必填；panel 不填写虚构节点。
- Loop 节点保留现有 `execution` 对象，包括逻辑节点、Loop 和轮次；从原始执行计划取得，不解析 ID 猜测。
- prompt 保留 `assignee_actor_ids`、`response_ref`、`timeout_deadline_ms`；正文使用历史界面原本展示的 instruction，不复制 IM 通知全文或隐藏上游上下文。
- output 保留已知 `assignee_bot_id`、`delivery_request_id`、`bot_delivery_run_id`；这些字段用于关联，不能影响消息主键。
- `status` 是记录当时的状态快照，不代表节点当前状态。judge 完成后不修改原消息，也不新增第二份 output。
- 新记录有可用显示名称时保存 `bot_name`；没有可靠名称时允许按 sender ID 显示，不能依赖读取 Bot / Run 才取得正文。旧数据无法证明当时名称时不伪造历史名称。
- published_result 保持已有 `chat` 字符串正文，不为本次迁移改变其网络发布契约；逻辑种类由该生产者的已验证稳定 key 识别。
- 缺少 `history_schema_version` 的已存消息按现有格式读取。不得为了统一格式重写其主键、正文或创建时间。

### 5.4 时间与历史保留语义

1. panel 使用 Run 的原开场时间，prompt 使用该 attempt 的激活时间。
2. 新 output 使用回复被本地状态机接受的时间；正在 judge 的回复也必须保存。
3. 已持久化旧 output 保留原 `created_at`，即使它使用的是完成时间。
4. 恢复及启用时的事实固化使用可信的原接受时间；缺少该信息时，已完成节点可使用原完成时间。仍无法确定时报告缺失，不能使用持续变化的 `run.updated_at` 伪造精确接受时间；不为旧消息另做批量回填。
5. 已保存 output 不因 judge 失败、重试、取消或 Run 终态而删除；新的 attempt 产生新的逻辑身份。
6. 这意味着新记录能够保留原来可能被节点覆盖的已接受回复。它是持久化后的明确行为变化，不属于新增工具过程或通知类型。

## 6. 稳定身份与去重

### 6.1 逻辑身份

身份的比较键是结构化元组，不能以正文、时间戳、显示名称或可变状态去重：

```text
panel            = (env, session_id, run_id, panel)
human prompt     = (env, session_id, run_id, execution_node_id, attempt, human_input_prompt)
node output      = (env, session_id, run_id, execution_node_id, attempt, output)
published result = (env, session_id, run_id, published_result)
```

Loop 轮次由 execution node ID 区分，重试由 attempt 区分；rerun 使用新的 run ID。多位 Human 竞争同一节点回复时，只有成功接受的那一位可以产生 output。

已有 producer key 算法保留：panel 为 `{run}:000-panel`，output 为 `{run}:{node}:{attempt}:1-output`，prompt 为 `{run}:{node}:{attempt}:human-input-prompt`，published result 为 `state-machine-result:{run}`。沿用现有短 ID，过长时使用完整 SHA-256 十六进制值，禁止截断导致碰撞。

新身份构造集中到一处；正常写入、恢复及读取归并必须复用。字符串 key 只作为现有物理身份约定，不能通过任意分隔符拆分旧 ID 反推业务字段。若 key 无法无歧义映射到结构化身份或出现内容冲突，返回冲突并阻止切读。

### 6.2 写入幂等

- 所有新写入使用 caller-owned 稳定 `message_id`，由主键处理并发冲突。
- `client_msg_id` 查重只能发现历史记录，不能替代主键唯一性。
- 已存在且身份、正文、发送者、归属、受众一致时，复用原记录及物理序号；不覆盖旧值。存量行不得补上新增投影标记，重复写入不推进 `current_msg_seq`。
- 重复调用中的当前时间、当前节点状态不得用于重建原 payload。正常重放读取原 checkpoint；旧格式适配先验证逻辑身份和内容，再使用原已存 payload。
- 同一逻辑身份出现不同正文、发送者、Session 或 audience 时为冲突，不采用最后写入覆盖，也不生成新 ID 绕过冲突。
- 兼容已有随机 message ID、长 ID、client key 和旧 Human response 类型，仅限仓库中确有消费者或存量记录的格式；不新增无依据的别名。

### 6.3 双来源读取去重

双写上线必须同时修改现有合并逻辑，不能只增加 INSERT：

1. 从明确的 producer 类型及 metadata 取得逻辑身份，校验归属。
2. 对同一身份优先使用持久化记录；缺失时才使用运行来源生成的记录。
3. 选定持久记录后，以其 audience 决定可见性；不可见时不能回退到较宽松的动态记录。
4. 两个来源复用同一个公开 ID 投影：panel、prompt、旧 Human response 沿用现有 `client_msg_id` 优先的公开 ID；output 使用实际已存 `message_id`；published_result 沿用既有 chat ID。未落库记录按同一生产者规则计算其公开 ID。不得在不同读取路径对同一 output 分别返回哈希和 client key。
5. 去重及权限过滤先于最终排序和 limit；两个独立页面直接拼接再截断不能作为正确实现。
6. 不合并同正文的不同 node、attempt、Run，或 output 与 published_result。

逻辑去重键与公开 ID 是两个职责：前者识别业务消息，后者保留已有客户端身份。只有已经存在的存量别名需要兼容；建立映射后，重复刷新、翻页、前端历史合并应使用相同公开 ID。已有实时最终回复与历史加载的关联不得退化；不要求工具事件获得新的身份协议。

## 7. 写入时机、一致性与故障处理

### 7.1 可靠历史事实

对新 prompt / output，扩展现有业务 Repo 命令，使以下操作处于同一个本地数据库事务：

- 校验当前 env、Session、Run、执行节点、attempt 及原有 CAS / lease fencing；
- 接受回复或激活 HumanInput 节点；
- 保存不可变 `history_message` checkpoint；
- 保持该状态转移原来要求原子保存的事件、judge 输入等记录。

checkpoint 的逻辑 payload 包含确定的 `message_id`、`NewMessage` 内容和业务身份，复用现有 `payload_json`。具体封装为 `schema_version: 1`、`message_id`、`message` 三个字段，未知版本明确报错，不能丢弃后重建。新增投影的 `message.content.metadata.state_machine.history_schema_version` 必须为 1；实际物理序号仍由消息仓储分配。operation key 为 `history-message:` 加结构化逻辑身份规范序列化后的 SHA-256；tuple 字段顺序固定，数值与字符串不得混用，环境在 key 与存储条件中都须校验。不得在此保存 transport 凭证、Cookie、转发 Header 或临时附件 URL。

`history_message` 只使用 Pending / Delivered 两种完成状态。Delivered 表示目标消息存在且校验通过，不表示前端、Bot 或 IM 收到消息。它没有网络发送动作，没有“超时后放弃正文”的 deadline。确认更新必须校验 env、operation key 和不可变 payload；并发 worker 允许重复尝试本地投影，由消息主键保证唯一，不能因为没有网络租约而省略身份校验。

如果节点 CAS 失败，事务不得留下新 checkpoint。禁止先插消息再检查节点是否仍有效，否则迟到回调会变成伪聊天记录。

### 7.2 前台投影

1. 状态及历史事实提交后，立即按 checkpoint 调用幂等消息写入。
2. 校验返回记录后，再将 checkpoint 标记为 Delivered。
3. 历史写入或确认失败应传播错误，不能把写失败转成成功或把存储错误判定成 Bot 业务失败。
4. 本条消息的投影完成前，不以当前请求继续依赖它的后继推进或返回接受成功；恢复入口使用相同屏障。
5. 判断消息是否已保存以消息主键和 payload 校验为准，不能只看内存标记。

T03 统一在接受 Bot / Human 回复时提交 artifact 与 checkpoint；带 judge 的路径同时建立原有 judging 输入。不带 judge 的路径先完成消息投影，再完成节点；崩溃恢复使用已接受 artifact 推进，不重发 Bot。Human 回复不等待后续流程全部结束才保存。

Completed 后的原 `persist_node_output` 改为确认或补齐同一逻辑消息，不能按完成时间重新构造另一份正文并与接受时间发生冲突。

### 7.3 恢复与终态

使用 BCS 现有后台扫描的生命周期管理，增加独立的待投影 checkpoint 分页。该分页不以 Run 为 Pending / Running 为前提，必须处理 Completed、Failed、Aborted Run 的已接受消息。

- 状态事务失败：无接受事实、无消息；允许按原业务规则重试。
- 状态事务提交后崩溃：Pending checkpoint 恢复补写。
- 消息提交后、checkpoint 确认前崩溃：重复写返回原行，确认后结束；不分配第二个 sequence。
- 存储持续不可用：保持 Pending，限速、退避并报告积压；不删除 payload。
- Run 随后取消或结束：已接受消息仍补写；不恢复 Bot 投递、不重新调用 judge、不重新发布最终结果。
- 迟到或错误 attempt 回调：由原 CAS 拒绝，不能产生新的历史事实。
- 重放校验发现已确认记录被删除、篡改或与 checkpoint 不符：明确报错；不能静默确认成功。本次不增加全量巡检。

保证范围是“持久业务事实不会因消息投影失败而丢失，成功投影幂等”，不是跨网络端到端 exactly-once。进程在接受事实后崩溃的短窗口，messages 读取可能暂未看到该条，恢复后收敛；不能宣称两张表始终同步可见。

首次启用时，覆盖、重试、judge 和完成之前均经过已有事实固化屏障。旧版本已接受但尚未完成的 artifact 没有可靠的接受时间，不能伪造时间补写：应在 persistence 关闭的兼容版本上排空这些 Running / Judging 输入后再开启。若遗漏，运行时返回 `missing_source_evidence` 并保留原 artifact，不允许覆盖；新版本已有 checkpoint 的输入不受此限制。

### 7.4 已有 opening / published result

沿用原 checkpoint 和稳定消息 ID。单独补消息不调用 `handle_web_send`、Bot delivery 或 Channel outbound，也不更新原网络投递的成功状态。

即使 Run 已终态或消息下游发送失败，既有恢复流程遇到已经确定且按原规则应出现在历史中的 opening / published result，仍应校验并补齐本地消息。不存在可信原 payload 时报告缺失，不能用今天的定义或配置重新渲染；本次不为此额外遍历全部旧 Run。

终态清理每页批量检查修复完成标记，只对未确认的 Run 执行上述本地修复。成功后在现有 checkpoint 表记录 `operation_kind=history_repair`，仅保存 Run 级完成标记，不复制消息 payload、不确认网络投递或节点 Pending。写入或确认失败时仍可重试；成功标记跨重启保留，后续扫描不再读取该 Run 的原 payload 或探测消息，也不承担已确认后被外部删除消息的巡检。

## 8. 可见性与展示一致性

### 8.1 权限与消息展示

1. 所有对外入口先执行现有 Session / View Actor 鉴权，不能因为 messages 存在记录就允许访问。
2. StateMachine 生产者显式写 `visibility_domain = state_machine`，即使 Run 位于 Chat 群。
3. Human Participant 仅看到 Public 和 Directed 包含自身的消息；Bot output 的 FullOnly 保持不变。
4. HumanInput prompt 的 audience 在激活时冻结。加入新参与者、配置改绑或名称变化不自动扩大旧消息受众；当前访问权限撤销仍立即生效。
5. 保留历史界面现有的展示选择：StateMachine 专用历史中的 Human prompt 只在相应 Human Participant 投影出现，Full / Bot 视角不因新落库自动新增这些提示。展示筛选与安全 audience 分别表达，在分页前执行。
6. Chat / ManagerWorker 会话继续保留原 owner、View Actor 及参与者历史窗口规则。不能把所有 `owner_bot_id = NULL` 的状态机消息视为所有角色可读。
7. 新增历史投影及故障补写会分配物理 `session_seq`，该值不能伪装成原发送顺序并绕过参与者加入窗口。对原本没有 sequence 的状态机动态记录，沿用其原有 source-specific 可见性规则；不能以补写时的新 sequence 替代原可见性依据或扩大受众。
8. Legacy 无分类消息在作用域受限的状态机视角中继续 fail closed，不为了补齐数量设为 Public。

实现前应将现有 Group strategy × Bot/Human × Full/Participant 的输出建立为契约样例。持久化的目的不构成对额外角色开放内部节点产物的授权。

### 8.2 普通 Chat 的查询侧窗口补偿

只保留 `session_seq` / `current_msg_seq`。参与者加入的 `participant_join_seq` 仍为物理位置，不改已有字典值。普通 Chat 有 join_seq 时以它为固定锚点；缺失时读取消息仓储当前物理序号。纯 StateMachine 和 ManagerWorker 不新增参与者窗口。

按原 `N = new_participant_visible_limit` 向前计算窗口，但跳过本次接受事实产生的新增投影：必须同时满足 StateMachine visibility、状态机历史消息类型、`content.metadata.state_machine.history_schema_version = 1`。标记随消息事实冻结，不从当前配置或 Run 状态推断。不存储 JSON 计数器。

- 旧 V2 output / prompt、普通 chat、opening、published_result 继续占原来的物理位置。重复写命中旧行时，不覆盖其 metadata / ID / sequence / 时间。
- 删除普通消息留下的物理空号仍消耗窗口，不能按现存行 COUNT 紧缩窗口、暴露更早内容。
- 查询只读取锚点及以前的序号和投影标记；锚点之后新增的消息不改变加入前窗口。得到物理下界后，普通行按 `session_seq >= 下界` 过滤；新增投影独立沿用原动态历史的 owner / audience 规则，不因跳过窗口而获得可见权限。
- 补充投影在 Session 保留期内须保留序号和标记。硬删除投影会使它无法与普通消息空号区分；当前没有生产消息硬删除路径，本任务不增加此能力。将来清理正文时必须保留位置及标记，或单独调整历史窗口契约。
- SQLite / MySQL 每批最多返回 512 个序号与布尔标记，沿既有 `(session_id, session_seq)` 索引倒序扫描；不向应用返回正文，但数据库仍需读取并解析候选行的 JSON。单次允许跳过最多 16,384 个补充投影，超过预算或查询失败则明确报错，不用未经补偿的边界返回成功。常见短窗口一批完成；高密度投影会增加读取成本，见第 12 节。

关闭 persistence 或切回 runtime 仍使用同一窗口补偿。Session 创建 / 加入、投递鉴权和正常追加保留现有物理序号契约，不引入第二套计数器或初始化流程。

## 9. 读取接口、排序和公共兼容

### 9.1 统一应用入口

以下入口必须共用历史策略选择和消息投影：

- `GET /sessions/{session_id}/messages`
- `GET /openapi/v1/collaboration/sessions/{session_id}/messages`
- StateMachine 群的 Runtime 历史入口及 Human view 入口
- Chat / ManagerWorker 中一次性 Run 的历史合并入口

HTTP 路由只负责协议和鉴权上下文转换；不能在两个 adapter 中各自实现去重或补写。

切读后，StateMachine 记录从 MessageRepo 取得，移除运行来源 fallback。已接受事实的投影失败、格式损坏或存储失败必须可观测，不能悄悄查询 Run / Node 让验收看似通过。切读前未落库的旧消息按第 10 节明确不展示，读取本身不承诺检测不存在的旧记录。切读回滚通过显式配置完成。

### 9.2 返回数据

- 保持 legacy `GroupMessage[]` 与 OpenAPI `Envelope<GroupMessage[]>`，不增加第二种消息 DTO。
- 保持 `message_type = bot` 的现有展示形态；Human 回复使用 `role = user`，Bot output 使用 `role = assistant`。
- StateMachine 关联保留在数据库 `run_id` 和 `metadata.state_machine.run_id`，不替换为下游执行 run ID。公共 DTO 的顶层 `run_id` 具有现有对话分组消费者，不能直接复制存储列。
- panel、prompt、output 及已归一化的旧 Human response 统一沿用状态机合成历史的逐消息投影：顶层 `run_id` 保持空值并按既有序列化规则省略，`historyMeta` 不注入 Run 级 `conversationRoundId`。两个来源、两套 API 和各视角共用该投影；已存记录也适用，不修改数据库原始 run ID。
- published_result 与其他普通聊天继续沿用原顶层 `run_id` / `historyMeta`，保留已有对话聚合及实时回复关联。此规则按业务消息种类判断，不能仅凭 `run_id` 非空或 `visibility_domain = state_machine` 清空所有消息的分组信息。
- 新格式标识、接受时状态快照，以及通用持久消息投影对状态机顶层字段的上述归一化，必须更新契约和消费者回归样例；不能将补齐可选字段视为天然兼容。
- `include_pending` 的普通聊天既有行为保持不变；本提案不为 StateMachine 新增工具或内存 delta 历史。已被接受并存入 messages 的回复属于持久记录。

当前 Workbench 的 `transformMessageData` 会将顶层 `run_id` 用作 `conversationRoundId`，随后 `transformGroupMessagesToChatMessages` 对同轮用户回复只取第一条，并合并同一 Bot 的输出。因此本提案不向状态机逐消息历史引入 Run 级分组。验收必须使用现有消费者转换链，验证同一 Run 的多个 Human 节点、同 Bot 多节点、Loop 各轮及多 attempt 均保留独立消息 ID、正文和 metadata；仅比较后端返回条数不足以证明兼容。

### 9.3 排序和游标边界

消息仓储及全量读取验收使用已有的 `(created_at, session_seq)` 复合游标，按倒序取最新一页；同时间戳消息依靠 sequence 稳定排序。

现有公开 HTTP 协议只有排他的毫秒 `before`，没有 `session_seq` 游标，也不返回 next cursor。本次遵守现有接口设计，不顺带增加字段或修改返回 envelope。其同毫秒跨页可能漏读的既有限制必须保留在接口说明和发布说明中；不能声称公开分页已实现无遗漏遍历。

当前状态机升序后 `take(limit)` 会返回最早一批，切读会改变结果。双写发布时应先将两种来源对齐为“返回最新 N 条、按倒序排列”，再验证已落库消息的排序；同页排序适配与 legacy / OpenAPI 一起验证。这是本次必要且显式的历史行为修正。

对本次保证范围内消息的全量读取验收使用复合游标的仓储读取，不以公开时间戳接口替代，也不声称已迁移双写前的全部旧历史。公共复合游标升级是独立需求；若实施时要求该 API 也完整遍历，需另行扩展本节及所有消费者契约，不能暗中改变 `before` 含义。

## 10. 无回填切读的历史边界与验证

### 10.1 历史展示边界

不实施 T04，不新增旧消息 `check` / `backfill` 命令或全量迁移报告。双写期间保持 runtime 查询，运行一段时间后通过配置直接切到 messages：

1. messages 中已有的 panel、V2 output、旧 Human response、published_result 等记录继续按统一身份和权限读取，不因早于双写启用时间而过滤。
2. 双写启用前仅存在于 Run / Node / 定义中的旧消息不作批量迁移，切读后不展示。旧 Run 的历史可能只剩部分已落库记录，这是本次接受的行为边界，不要求为了切读补齐。
3. 连续双写期间新接受的消息仍须完整持久化并可恢复；不能把新写入失败或长期 Pending 当作允许丢弃的旧历史。
4. T03 的 Pending 恢复、既有 opening / result 本地修复和启用时事实固化保持不变；它们不承诺遍历并恢复全部旧消息。没有窗口初始化或旧消息正文回填。
5. 双写观察时长由发布安排决定；本次不新增时间开关、自动切读任务或查询时回填。发布记录写明实际启用和切读时间，以及旧消息展示边界。

### 10.2 双写观察与读取验证

- 观察新消息写入、幂等冲突、投影延迟、持续失败及 Pending 恢复，修复新双写路径上的问题，不以等待时间代替故障处理。
- 在回归场景中验证新消息的正文、发送者、role、时间、Run / Node / attempt、Loop metadata、受众和顺序；消息投影成功确认前仍校验原不可变 payload。
- 验证同一会话含已落库旧消息、未落库旧消息和双写新增消息时，messages 模式只展示有权访问的已存记录，没有运行来源 fallback，也不补写缺失的旧消息。
- 两套历史接口、Workbench / CLI 消费和 H22 内容来源隔离测试通过；原参与者窗口不受新增投影及恢复影响。

上述验证不要求全环境旧历史完整对账，也不宣告旧历史已全部迁移。等待双写不会使未落库的旧消息自动进入 messages。

## 11. 配置、发布与回滚

通过现有 BCS 配置加载和 bootstrap 注入以下稳定配置，不在业务层直接读环境变量：

| 配置 | 默认值 | 语义 |
| --- | --- | --- |
| `state_machine_history.persistence_enabled` | `false` | 启用本提案补齐的可靠消息事实及投影；关闭时仍保留原已有 panel、V2 output、published result 写入 |
| `state_machine_history.read_source` | `runtime` | `runtime` 使用过渡期统一归并，`messages` 使用消息仓储 |

`read_source = messages` 要求 `persistence_enabled = true`，非法组合启动失败。T03 版本尚未实现 messages 读取，因此无论 persistence 配置如何，选择 messages 均明确拒绝启动。配置沿用现有加载机制，修改后须重启兼容版本实例；不新增切读 API 或热更新设施。配置不是成熟度名称；不引入 `phase1`、`v2_history` 等临时契约。

### 11.1 发布顺序

1. 部署身份归一化、旧格式适配、两入口排序修正、查询侧窗口补偿及 checkpoint 恢复能力；不执行 DDL 或数据初始化。开启新写入前确认所有读取实例支持新增投影标记，维持默认配置。
2. 确认所有相关读写实例为兼容版本，按第 7.3 节排空缺少原接受时间的旧 Running / Judging 输入，再开启 persistence。门槛不只覆盖状态机 writer；旧读取实例也会误用物理窗口。旧实例还可能按 Completed 元数据校验新接受态消息，因此禁止旧二进制与新写入模式混跑。
3. 记录环境的双写启用时间，保持 runtime 读取并连续双写一段时间，按第 10.2 节观察新增消息和故障恢复。旧消息不回填，切读也不依赖全量历史对账报告。
4. T05 完成、观察期结束且满足下列门槛后，按部署环境将 `read_source` 改为 `messages`，保持 persistence 开启并重启兼容实例。发布说明明确未落库旧消息将不再展示。
5. 切读稳定、读取回滚期结束后独立清理 runtime 历史来源，不以旧消息已全部迁移为清理前提；不删除工作流调度或流程面板所需运行数据。

### 11.2 切读门槛

- 目标环境没有使用旧窗口算法的读取实例，所有入口都写同一消息身份。
- 双写运行正常，没有未处理的持续写入失败、身份冲突或长期 Pending；故障后的已接受事实能够补齐。允许持续流量中的短暂 Pending，不要求建立全量旧历史验收水位。
- 已记录双写启用时间及第 10.1 节旧消息展示边界；双写前未落库旧消息不阻止切读，也不能据此宣称旧历史已全部迁移。
- 活跃、终态、Loop、重试、Human、混合会话及重启恢复测试通过。
- 已验证真实 MySQL 上的幂等并发、迁移链和查询计划；SQLite 结果不能替代。
- messages-only 依赖隔离测试通过，两套 HTTP 入口契约一致。
- Workbench 消费者逐消息展示及无回填边界用例通过；历史投影追加前后加入、缺少 join_seq、并发普通写入及回滚场景的窗口用例通过。

### 11.3 回滚

优先只将 `read_source` 切回 runtime，保留新写入与恢复，避免扩大数据缺口。回滚后的 runtime 归并必须保留已持久化的旧 attempt 消息。

关闭 persistence 前，先回滚读取并处理 Pending checkpoint；关闭后也不能停止已接受事实的补写。若关闭期间产生新的未落库状态机消息，再次切读时这些消息同样不展示；重新开启不会自动回填，须在发布记录中明确中断时段，并重新观察双写及恢复是否正常。

不将直接降级旧二进制作为常规回滚：旧版本不理解新历史事实、接受态 payload 和补充投影标记。虽然物理序号及 join_seq 的含义未改变，旧读取算法会将新增投影计入窗口、挤占普通消息。回滚应通过同一兼容版本切回 runtime，不删除消息或改写迁移记录。

## 12. 数据库变更与访问成本

### 12.1 复用原有表结构

逻辑关联复用已有 `env` / `session_id` / `run_id` / `message_id` 和 JSON metadata。可靠写入的 checkpoint 复用已有表、operation kind 和 payload 列。消息幂等性使用已有主键及 Session / sequence 唯一约束，不新增表、列、索引或反向关联字段。

新投影直接使用已有 `append_message_with_id`；正常追加、Event 追加及 delivery admission 保留原有物理序号事务。Pending checkpoint 使用既有 `(env, operation_kind, status, aggregate_id)` 索引。

旧 client key 查找每 200 个 key 分为主键查询和 client key 查询，结果每次至多 401 行。主键仍能精确查找；client key 只能依赖原有 Session / type 索引缩小扫描范围，返回行数上限不代表扫描成本上限。代表性执行计划需记录，若真实负载证明不足，另行评审针对性索引，不作为本次上线前置 DDL。

### 12.2 草稿迁移撤除与保留数据库

本次明确撤除未提交的 `029_state_machine_message_history.sql` / `030_state_machine_message_history.sql`、两列、三个索引及 `initialize-window` 命令，无消息窗口初始化或回填步骤。最初验证基于 MySQL 028 / SQLite 029；rebase 后上游链包含 MySQL 029 / SQLite 030 的 `bot_provider_storage` 迁移，这是独立的 Provider 功能，按原样保留，本需求仍不新增 DDL。

本地保留 SQLite 已记录实验迁移 030。用户已明确授权仅从源码撤下这两份迁移及注册，数据库、迁移记录和现有消息保持原样；不修改已提交迁移或 checksum，不启停当前服务。保留数据库额外列 / 索引暂时存在，但新代码不再读写它们。撤除前只读确认本地 `history_message` checkpoint 为 0，窗口计数器未分叉。

上游 `030_bot_provider_storage` 已与该保留库的实验 030 同号。复用该库运行新版本前，必须单独处理本次实验版本，不能覆盖记录或静默忽略 checksum 冲突。原始代码备份及用户授权范围在本地执行记录中保存；这不构成修改业务数据库的授权。

### 12.3 正常写入

当前带 client key 的稳定追加通常需要一次查重，以及事务内 sequence 更新、sequence 读取和消息 INSERT，即约四条 SQL，另计事务控制；命中旧 client key 时通常读取原行返回。

本方案每条新增 prompt / output 的额外成本预算为：

- 原状态转移事务增加一条 checkpoint INSERT；
- 一次消息幂等追加，正常路径约四条 SQL；
- 一条 checkpoint 确认 UPDATE；
- 消息正文在 checkpoint 与 messages 各保存一份，Node 的原 artifact 存储仍保留。

普通消息和新增投影都只推进 `current_msg_seq`；重试命中已有行或事务回滚时不前进。不改 Session 加入及投递权限路径，不新增窗口位置查询。

T03 的 SQLite DB Plugin 计数测试记录：无事件的接受命令为一个三语句事务（Run 锁 / Node CAS / checkpoint INSERT），成功后直接返回已提交 payload；新投影为一次查重、一个三语句事务和一次确认 UPDATE。运行时接受前另有一次已有 checkpoint 查询。原业务事件若存在，沿用原事件事务步骤。消息已提交而确认响应丢失的重复投影为两次消息查询、一次确认 UPDATE 及确认已完成时的一次 checkpoint 读取，物理序号不增长。

上述是 SQL / 插件调用计数，不等于每条语句都有独立网络往返。无新增 DDL 版本已重新验证数据库并发、事务回滚及代表性查询成本，结果见 tasks 文末；未沿用双序号方案的验收结论。

### 12.4 恢复扫描和读取

- 恢复扫描默认每批最多 100 个 checkpoint，每轮至多处理 100 条；使用可续传游标，失败行不阻塞其他 Run。
- 默认扫描间隔 5 秒；持续失败复用现有带抖动退避，上限 30 秒，单条失败不形成紧密循环。进程重启后可以重新扫描，但不能绕过并发唯一性。
- ID 精确读取复用现有最多 200-ID 批读；不按每条消息查询一次定义或参与者。
- messages 读取一次页面最多查询 `limit + 1` 条可见记录，不能先加载全部 Run / Node 再分页；权限、展示条件和窗口下界在 limit 前生效。普通 Chat 的窗口定位额外读取至多 N 个普通位置与 16,384 个补充投影，批末最多多取 511 行；查询次数受同一预算限制，预算耗尽明确报错。
- 过渡期运行来源仍有原来的扫描成本；messages 查询不增加旧消息迁移、影子对账或运行来源补缺。
- 不在数据库事务或 Session 锁内进行 Bot / IM / judge 等外部网络 IO。
- 新追加对 `current_msg_seq` 的锁竞争、普通聊天共享连接池和持续失败时的恢复负载，必须进行代表性查询计数及并发验证。

T03 Pending 扫描每页一条索引查询，最多返回 100 份 payload；串行投影，不为每条 checkpoint 回查 Run / Node / 定义。原有 opening / Delivered publication 的本地核对接入既有活跃恢复与终态清理页。终态页最多 32 个 Run，仅增加一次基于现有 checkpoint 主键的批量标记查询；已确认 Run 不再读取 Run、原 checkpoint payload 或 messages。未确认 Run 首次修复或失败重试才读取来源并幂等补写，成功后每个 Run 写入一条完成标记；并发重复确认至多多一次批量主键复核。原有终态清理成本保持不变，不持锁调用网络。取消 T04 不移除这些故障恢复能力，也不将其扩展为全部旧消息的回填扫描。

终态修复验证：Runtime / Store 测试覆盖部分消息写入失败、标记写入失败、并发确认、跨重启跳过及节点 Pending 独立恢复。SQLite 最大 32 个 Run 的查询计数为一条查询、至多 32 行；真实 MySQL Text / Prepared 契约通过。隔离 MySQL 8.4 中放入 5,000 条标记后，1 / 32 个 key 的 EXPLAIN 均使用现有 PRIMARY，预计读取 1 / 32 行；此验证不代表共享生产负载下的吞吐结论。

T05 的状态机正文页只执行一次 MessageRepo SELECT，按环境 / Group / Session、消息类型和受众在 LIMIT 前过滤。内部最多 1,000 条加一条 lookahead，OpenAPI 保持原 100 条上限；Legacy 未指定 limit 时最多返回 1,000 条。查询从 Session 获取 group_id 后直接读取 messages，不批查 Run、不按消息回查 Bot 名称。内部复合游标仍只在 HTTP 暴露原有排他毫秒 before；同毫秒分页边界限制未改变。

混合会话沿用普通聊天查询，Participant 另有一页状态机消息查询并按共享身份归并。旧会话的普通 Bot 原生历史 fallback 保留，但其中状态机历史项被排除，由持久消息补齐；此分支在原 panel / opening 查询之外增加 output 和 Human response 两个有界类型查询，无逐 Run 扫描。隔离 HTTP 测试计数包括鉴权和 Session / Group 元数据，Legacy / OpenAPI 的 Full 请求分别为 5 / 3 次 SQL，Participant 混合请求为 7 / 5 次，测试上限为 8 次；这些数字不代表其他鉴权配置的全部开销。

真实 MySQL 8.4 的 5,000 条混合消息样本命中现有 `idx_session_type_created`，估计检查 502 行并使用 filesort。该查询使用原有索引；分页限制的是返回行数，不能据此声称数据库只扫描 limit + 1 行。尚未进行生产规模吞吐、连接池占用或 P99 锁等待压测。

已 Delivered checkpoint 的保留与清理由既有协作数据生命周期管理；Pending 不能被终态清理删掉。任何清理策略变更需独立定义，不能以清理 checkpoint 为由删除 messages 历史。

## 13. 分层与实施影响

| 位置 | 变更职责 |
| --- | --- |
| `bcs-domain` | 历史 payload / 身份、message metadata 和新增投影标记 |
| `bcs-service-api` | 扩展状态转移的历史事实契约；增加有界读取、投影确认、查询侧窗口补偿及幂等校验接口 |
| `bcs-collaboration-runtime` | 构造正文、身份、时间及受众；在接受事实时保存；前台投影与恢复复用同一能力 |
| `bcs-collaboration-store` | 同事务保存状态和历史事实；checkpoint CAS、终态覆盖、有界扫描 |
| `bcs-message-store` | 复用物理序号追加，批读、视角过滤、复合游标及查询侧窗口补偿；不决定业务 audience |
| `bcs-session-store` | 保留现有创建 / 加入及物理 join_seq 契约，本任务不修改 |
| `bcs-message` / 历史应用服务 | 统一 GroupMessage 逐消息投影、窗口基准、运行来源归并、messages 读取 |
| `bcs-app-session`、两套 HTTP adapter | 复用同一应用策略，保留各自鉴权与 envelope |
| `bcs-config-api` / bootstrap | 配置校验、同环境存储装配及后台恢复生命周期 |
| `bcs-admin` | 不新增管理命令；仅在既有 MySQL 测试中验证原迁移链和无 DDL 查询 |
| `src/bcs/api-contracts`、测试、CONTEXT | 更新可选 metadata、行为差异、Service API 与兼容测试 |

SQL、表结构及 checkpoint 映射只在 store 中；Runtime 只依赖业务 Repo / Service 契约。Memory、SQLite、MySQL 必须实现相同接受事实和幂等规则，不能用两份不一致的 Memory map 绕过原子性测试。

本提案通过第 9.2 节的后端投影保持现有 Workbench 逐消息展示，不要求前端增加消息类型或修改聚合逻辑。实施时需使用 Workbench 现有转换链验证多节点、多 attempt 和普通聊天聚合，并验证 CLI 的 ID、排序、role、run_id 和 metadata 消费；若仍发现必须修改 `src/frontend/`，先补充传播范围，不能用省略消费者验证代替兼容证明。

## 14. 可观测性

T03 使用结构化日志提供等价观测：当前页 Pending 数量及最老记录年龄、接受 / 投影完成 / 重复确认 / 冲突 / 写入失败分类、扫描处理量及投影延迟。页内 Pending 和年龄明确不是全环境积压总量，避免每个扫描周期全表 COUNT；本次不提供旧消息全量完整性报告。日志的投影完成分类也不作为新增物理行数量的精确计数，提交后确认丢失可能重复记录该结果。

日志允许携带排障所需的 env / Session / Run / node / attempt / message ID 及固定错误分类；不记录正文、完整 payload、Header 或私有上下文。指标标签不能使用 Run、node、message ID。

保留投影和恢复中的冲突、写入失败及 `missing_source_evidence` 分类，不因取消旧消息回填而吞掉新写入错误。messages-only 请求本身不能从“不存在的行”判断历史缺失，不能为探测缺失而给每次历史读取增加运行表访问；本次只承诺第 10 节的历史范围，不宣告未回填旧记录的完整性。

## 15. 验收矩阵

| 编号 | 场景 | 必须满足 |
| --- | --- | --- |
| H01 | V1 Bot 节点正常完成 | output 只写一份，messages 可还原 |
| H02 | V2 普通节点及多轮 Loop | execution 信息正确；轮次、attempt 不相互合并 |
| H03 | Bot 回复后正在 judge | 回复已持久化，重启可见；完成后不产生第二份 |
| H04 | judge 失败并重试 | 已接受的旧 attempt 回复保留，新 attempt 独立 |
| H05 | HTTP / Channel Human 回复 | 同一逻辑身份及 role；两个 Human 竞争只接受一个 |
| H06 | HumanInput 激活 | prompt 与激活事实同事务；受众冻结，Full 与 Participant 展示规则正确 |
| H07 | 同一 final 重复 / 并发 / 迟到 | 一份消息、一个 sequence；错误 attempt 不落消息 |
| H08 | 状态与 checkpoint 事务注入失败 | 二者同时回滚，不留伪消息 |
| H09 | 状态提交后、messages 写前重启 | Pending 恢复补齐；不重发 Bot、不重跑已完成 judge |
| H10 | messages 提交后、确认前重启 | 返回原消息，序号不增加 |
| H11 | Run 已 Completed / Failed / Aborted | 未确认历史仍恢复；终态清理不丢 Pending |
| H12 | 消息数据库持续失败 | 错误传播、有限批量与退避；普通消息池不被无界占用 |
| H13 | 双写 + 动态记录 + 已存记录 | 同一逻辑消息每个视角仅一条 |
| H14 | 长 Loop ID 哈希 / 原 client key / 旧随机 ID | 统一身份，无跨页或重复刷新重复 |
| H15 | 正文相同的不同节点、attempt、Run | 均保留；与公开最终结果分别识别 |
| H16 | 两套历史 API | 相同授权视角得到同一 GroupMessage 数组语义 |
| H17 | Chat / ManagerWorker 内一次性 Run | 普通聊天策略不变；状态机消息不扩权、不重复 |
| H18 | 参与者变化、新增历史投影 sequence | 当前鉴权生效，旧 audience 不扩大，加入窗口不被绕过 |
| H19 | 同会话存在未落库旧消息及双写新增消息后切读 | 仅展示有权访问的已存消息；不回填旧消息、不访问运行来源补缺，新消息不重复 |
| H20 | 双写启用前已经落库的旧消息 | 保留原身份、正文、时间和可见性；不按双写启用时间截断查询，不要求旧 Run / Node 存在 |
| H21 | 同毫秒大量消息 | 仓储复合游标全量遍历不重不漏；公开 before 限制有明确测试 |
| H22 | 消息读取独立性 | 保留鉴权数据，将状态机源 Repo 全部替换为调用即失败的实现，聊天历史仍正确 |
| H23 | opening / published result 补写 | 不产生 Bot / IM 二次投递，不改变网络 checkpoint 状态 |
| H24 | 读取回滚及重新开启 persistence | 已存记录不丢、不重复；关闭期间未落库消息不自动回填，恢复双写及再次切读的边界明确 |
| H25 | Memory / SQLite / MySQL | 相同契约；真实 MySQL 并发、长度限制、完整迁移链通过 |
| H26 | 最大 Loop 规模与跨 Session 并发 | 查询条数、批量、锁时长和连接池占用满足第 12 节边界 |
| H27 | 兼容版本部署和开关校验 | 非法配置拒绝启动，旧实例未退出时不启用新写入模式 |
| H28 | 开启时已有 Running / Judging 节点 | 覆盖前固化已有事实；缺少原接受时间的旧输入阻止推进、保留原值，并要求启用前排空，不能伪造时间 |
| H29 | 双写观察期后持续写入下切读 | 新接受事实正常投影、短暂 Pending 可恢复、长期积压须处理；不把双写前旧消息完整迁移作为切读条件 |
| H30 | 同 Run 多 Human 节点、同 Bot 多节点 / Loop / attempt | 经现有 Workbench 两级转换后仍逐条展示，ID、正文和 metadata 不丢失；顶层分组字段未引入 Run 级合并，普通 chat / published_result 聚合不变 |
| H31 | 100 条普通消息后新增 100 条投影，窗口为 100 | 物理序号为 200，查询下界仍为 1；加入前后及缺少 join_seq 的视角可见相同普通消息集合，audience 独立过滤 |
| H32 | 后续普通追加、历史补写及重复恢复 | 固定物理 join anchor 不移动；仅新增普通消息推进缺少 join_seq 时的窗口，重复追加不占第二个序号 |
| H33 | 旧 V2 行、删除空号及高密度投影 | 旧行不补标记，普通空号不压缩；512 行有界读取及 16,384 补充投影预算生效，失败明确报错 |
| H34 | 双写后回滚读取、关闭 / 重开 persistence | runtime / messages 共用窗口补偿，不重写 join_seq、历史标记或消息数据 |
| H35 | 新增投影及原有写入路径事务失败 | 新 prompt / output 不挤占窗口，旧行及 opening / result 保持原语义；失败回滚唯一物理计数器 |

H22 是本需求的核心验收：不仅要查询结果一致，还要证明读取没有调用 Run、Node、definition snapshot、checkpoint 或 Bot 原生历史。流程面板 API 不受此隔离测试约束。

建议运行受影响的 runtime、collaboration-store、message-store、session-store、message、app-session、config 和 HTTP 契约测试，并用现有 Workbench 转换链完成 H30；架构边界及对应 Singlebox BCS 场景按仓库规则执行；保留已有文件布局，不因行数上限擅自拆分文件，既有长度问题如实记录。具体命令与结果在实现 PR 中记录，不将此列表当成已通过证据。

## 16. 实施切片与完成定义

1. 固化现有消息与视角样例，统一身份、公开 ID、逐消息内容投影和两个读取入口的去重；验证 Workbench 消费者，修正必要的倒序选择。
2. 实现 prompt / output 的持久事实、前台投影和终态恢复，扩展 V1 及接受态回复；保持已有 opening / result 生产者。
3. 实现普通 Chat 的查询侧窗口补偿，复用现有索引并验证访问成本、旧格式和删除空号；不修改表结构。
4. 实现配置控制的 messages-only 读取和隔离测试，更新接口、CONTEXT 与发布说明。
5. 部署全部兼容实例，再开启双写；持续运行一段时间并满足门槛后直接通过配置切读，明确不回填旧消息，记录真实数据库和运行环境验证。

完成条件：上述范围内的新消息具备可靠持久事实和幂等消息投影；两套历史接口在 messages 模式不依赖状态机运行数据；权限、身份、顺序、回滚和故障矩阵通过；发布说明明确未落库旧消息切读后不展示，未验证项如实列出，不用兼容 fallback 掩盖，也不要求全量旧历史完成迁移。

## 17. 本文验证证据与限制

本 spec 基于本地代码、接口及契约的静态阅读。主要证据：

- [定义 V1 / V2 编译分支](../../crates/services/bcs-collaboration-runtime/src/fixed_loop.rs)
- [现有 V2 output 写入、哈希 ID 和读投影](../../crates/services/bcs-collaboration-runtime/src/runtime_metadata.rs)
- [历史拼装、HumanInput 接受、开场与节点事件](../../crates/services/bcs-collaboration-runtime/src/runtime.rs)
- [judge 接受后的推进](../../crates/services/bcs-collaboration-runtime/src/runtime_judge.rs)
- [opening checkpoint](../../crates/services/bcs-collaboration-store/src/opening.rs)
- [checkpoint 表及现有索引](../../migrations/mysql/028_fixed_loop_runtime.sql)
- [稳定消息 ID Repo 契约](../../crates/service-api/bcs-service-api/src/port/repo/message.rs)
- [消息 SQL 与可见性过滤](../../crates/services/bcs-message-store/src/mysql.rs)
- [统一消息展示转换](../../crates/services/bcs-message/src/projection.rs)
- [参与者窗口基准计算](../../crates/services/bcs-message/src/lib.rs)
- [参与者加入序号记录](../../crates/services/bcs-session-store/src/mysql.rs)
- [Workbench 对话轮次转换](../../../frontend/src/pages/GroupChat/utils/transformMessageData.ts)
- [Workbench 同轮消息聚合](../../../frontend/src/pages/GroupChat/hooks/useGroupChat.ts)
- [OpenAPI Session 历史契约](../../api-contracts/v1/openapi/sessions.yaml)
- [最终结果消息发布](../../crates/bootstrap/bcs/src/server.rs)

T01–T03 已实现共享身份与投影、runtime 兼容读取、查询侧窗口补偿、接受事实与 checkpoint 的原子提交以及可靠双写和恢复，实际验证与发布限制见 tasks。T04 已移除；T05 已实现 messages-only 查询、配置校验及无回填切读边界。目标环境仍须启用双写、完成观察和发布门禁后再切读，开发验收不替代实际环境的双写观察。公开时间戳分页的同毫秒边界和未落库旧消息的展示范围仍按本 spec 的限制处理。
