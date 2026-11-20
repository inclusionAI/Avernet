# 让 StateMachine 群成为 A2A Agent：分布式执行讨论稿

状态：**已明确核心行为，分布式合同待评审**（2026-11-20）。仅讨论已配置工作流的 StateMachine 群；配套[实施计划](plan.md)列出执行门槛，不代表 D0 冻结、实现或上线批准。

## 目标与依据

调用方只认识 Group、`contextId` 和 `taskId`，无需知道 BCS 换了哪台 Worker。每轮有独立的新输入；复用同一 context 时，跨轮上下文暂定按工作流节点隔离：每个节点只继承自己在该 Session 之前轮次中的交互记录，没有执行过的节点从空历史开始。本文讨论这些对外承诺如何映射到分布式 Gateway、Worker、Connector 和存储；不包括主从群、自由聊天、文件输入、轮内追问、Push Notification 或旧会话迁移。

- 产品参照：[原始 Group A2A spec（#3979 固定版本）](https://github.com/inclusionAI/Avernet/blob/387cd5c32a4d7c391a298ce6dfc3164cf3c764ab/apps/bcs/specs/2026-11-05-group-as-agent/spec.md)。原案用 Session activation / Run 和 `bcs_events` 关联一轮，要求忙时拒绝、最终产物持久化和整轮停止确认。
- 设计参照：[#4190 spec](https://github.com/inclusionAI/Avernet/blob/97a873ccdd33d1dc8d4b804b39a69ea0e7483863/apps/bcs/specs/2026-11-18-distributed-group-as-agent/spec.md) 与[实施计划](https://github.com/inclusionAI/Avernet/blob/97a873ccdd33d1dc8d4b804b39a69ea0e7483863/apps/bcs/specs/2026-11-18-distributed-group-as-agent/plan.md)。沿用其单一分布式 Session、纯 Runner 和 Worker 提交结果的方向；D0～D06 中的类型与方法不是现行 API。
- 实现基线：[GitHub dev `f0540f9b6`](https://github.com/inclusionAI/Avernet/tree/f0540f9b6e2d232c7952cb0d00c04db979c925de/apps/bcs)。[Gateway #4132](https://github.com/inclusionAI/Avernet/pull/4132) 和 [Worker 协调 #4145](https://github.com/inclusionAI/Avernet/pull/4145) 已合入；Gateway router 未挂进 `bcs`，协调模块[不运行 reducer 或 outbox](../../crates/services/bcs-dist-worker/README.md)。[可恢复 Worker #4245](https://github.com/inclusionAI/Avernet/pull/4245)、[Connector #4133](https://github.com/inclusionAI/Avernet/pull/4133) 当日仍在审，[群 Runner #4237](https://github.com/inclusionAI/Avernet/pull/4237) 是在审设计。不能把这几项相加当作真实群已可调用。

## 已明确的产品规则：Session 可复用，每个 Task 启动一轮 FSM

`contextId` 标识可跨轮复用的 Session；一个新的 A2A Task 对应一次独立的状态机运行（Run）。一次性的是 Run，不是 context。新一轮从工作流入口使用本轮输入启动，不续跑、重置或覆盖上一轮 Run；上一轮的 Task 和产物仍按其自身标识读取。

| 同一 context 的场景 | 对外行为 |
| --- | --- |
| 空闲，收到新的独立请求 | 先到先得：最先原子占用该 Session 的请求获得本轮执行权；并发到不同 Gateway 时，不以客户端时间戳或某一实例收包时间裁决。 |
| 当前轮已受理或仍在执行，收到另一新请求 | 返回 `409 / CONTEXT_BUSY`；不创建第二个 Task、不排队、不自动补跑。 |
| 当前轮已提交 `Completed`，收到下一轮请求 | 同一 `contextId` 可以立即竞争启动新的 Task / Run，不等 SSE 送达或额外解锁；用上一轮 `taskId` 仍可查询它的 Artifact。 |
| 本轮失败或取消，但仍有并行节点未确认结束 | 仍在收尾，返回 `409 / CONTEXT_BUSY`，不创建新 Task。FSM 确认整轮已结束并提交 `Failed` 或 `Canceled` 后，同一 Session 可以受理下一轮。 |
| 原来**已受理**的请求因丢回包而重试 | 命中同请求去重，返回原 Task；这不是开启下一轮，也不应误报 BUSY。 |

这是 **FSM 群对外启动一轮**的准入规则，不是给整个群加锁，更不是禁用 Inbox。当前轮的节点回调、取消等输入仍可进入 Inbox；群消息投递仍可排队。已判定停止未知而进入隔离的 context 不是空闲态，见问题 3。

旧执行路径已有相近但不等价的能力：[服务 Session 再激活](../../crates/services/bcs-session/src/application/sessionmanagementserviceimpl_2.rs)在 Running 时拒绝 invoke，否则可用新输入再激活符合条件的 Session；[状态机启动](../../crates/services/bcs-collaboration-runtime/src/runtime/start.rs)生成新 Run ID，one-shot 路径的 idle 检查不等于所有分布式入口均已独占。分布式实现需要让多个 Gateway 和故障接管后的 Worker 对“当前是哪一轮”得出一致结论，而不是复用旧 SQL 行或在 HTTP 层先查后写。

只让通用 Worker 串行执行已经排队的 Task 不满足上述规则：第二个 Task 已被受理，而调用方应该收到 BUSY。可复用同一 Gateway application/core 和输入流，但不能用这个捷径作为产品验收；HTTP 绑定、持久化协议及故障恢复仍按下文讨论。

## 需要共同回答的问题

### 1. 受理：哪一次持久写入使请求有权执行？（D02、D03；Gateway / Worker / Store）

**反例：** Gateway A、B 同时向空闲 context 提交不同 messageId。当前[新 Task 准入](../../crates/services/bcs-dist-gateway/src/admission.rs)检查 Session 归属而不独占这一轮；[Inbox producer](../../crates/services/bcs-dist-store/src/inbox/producer.rs)允许在结果未知后补写，物理记录可能重复。Worker 串行处理两项，仍违反“第二项 409、不创建可执行 Task、不排队”。

**讨论选择：** 由 Worker 作为唯一受理方，在同一持久边界裁决占用与输入；还是沿用 #4190 的分步方案，先按代次占用，再写 Inbox，最后持久授予执行许可并保证重新调度？前者要证明写入能覆盖的原子范围，后者要证明任一步崩溃都能修复。建议与 #4245 对齐唯一受理方，再选提交协议；不依赖同进程锁或假设跨键事务。

**验收反例：** 分别在写 Inbox 前、写后但授权前、授权后但尚未再次唤醒时杀掉进程。胜者最多执行一轮且最终能被调度；败者即使留下部分记录也不能被执行，忙时被拒的 messageId 在 context 空闲后可重发。接管者须能证明未授权的 Start 不存在或已被丢弃，不能靠等一段时间解锁。

**一次受理不等于下游副作用恰好一次。** 若 Bot 已收到节点调用、Connector 却在保存远端 Task ID 前崩溃，框架不能从“没有回执”推出“没有送达”。建议有已验证的下游持久去重/查询能力时，才按原调用身份重试/追踪；否则停止自动重发，保留未知状态并核实。待讨论：不具备这种能力的 Bot 是拒绝发布，还是允许发布但明确可能因送达未知而暂停该 context 的新轮次？验收应在远端收件后丢回包，分别数逻辑轮次、调用尝试和下游实际副作用，不能用前者代替后两者。

### 2. 重试：同一条消息究竟由哪些字段标识？（D01、D02；调用方 / Gateway）

#3979 按 `(tenant, principal, group, messageId)` 去重，客户端提供的 context、Task、配置和已激活扩展都参与指纹；[当前 input ID](../../crates/application/bcs-app-dist-gateway/src/identity.rs)还含 `sid`，[当前新 Task 的 digest](../../crates/application/bcs-app-dist-gateway/src/submit.rs)主要取群与消息内容。首轮回包丢失，再向另一实例重发，或把 `includeProgress` 从 false 改为 true，两者不能都被当作新任务。

**讨论选择：** 新入口遵循原案的严格指纹，还是允许拿到 contextId 后补填、改变等待方式再重试？建议严格入口保存首次规范化依据和生效配置，不改现有 JSON-RPC 重试协议；同请求取回原 Task，改语义报冲突，结果实际清理后返回过期而不重新执行。需评审请求墓碑保存边界及成本。验收应覆盖两个 Gateway 竞争首轮、配置默认值部署后变化，以及同一 messageId 换 context。

### 3. 完成与取消：按 Run 状态放行，按 Task 查询产物（D04；Runner / Worker / Connector / Store）

**成功的唯一业务依据是本轮 FSM Run 的 `Completed`。** A2A Task 映射为 `COMPLETED`，其 Artifact 来自该工作流定义的最终输出节点（`final_output`），不取最后到达的分支回调，也不再让 Gateway 单独判断是否业务完成。[旧运行时](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs)优先取指定节点但还会回退到逆序找到的其他节点产出；新路径不采用这个回退。持久化是发布条件，不是第二个业务终态：归档写入失败不能对外返回不带应有 Artifact 的成功，接管后补发原结果，不重跑整轮。

**`Completed` 的提交边界：** 最终节点的输出按本轮 `taskId` 持久化，不覆盖 Session 的上一轮产物。权威 Run 状态一旦提交为 `Completed`，就同时满足两件事：有权限的调用方可用 `GetTask(taskId)` 读到该轮 Artifact；同一 Session 可原子受理下一轮。无需等待 SSE、额外的停止回执或第二个完成标记。这比 #4190 中“发布后再单独释放占用”的方案更严格：不能只因上轮的异步通知未结束就返回 BUSY。

**实现与验收：** [ResultStore](../../crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs)提出先保存最终记录。若归档和权威 Run 状态不在同一事务，可先幂等保存按 Task 标识的结果，再提交引用它的完成状态；两者之间宕机则补交同一结果，不重跑业务，不提前暴露成功。验收：A 完成后暂停 SSE 发布，此时 B 仍可在同一 context 启动；B 运行中及完成后查询 A 的 `taskId` 都得到 A 的最终产物，查询 B 不会覆盖 A（在结果保留期内）。

**异常收尾：** A、B 两节点并行，A 发生不可恢复的错误或整轮收到取消时，若 B 仍在执行，Session 继续占用。FSM 确认所有在途工作已结束，才提交整轮的 `Failed` / `Canceled`；取消与完成竞争只提交一个终态。长期失联不能靠超时推定 B 已停止，也不能因 Worker 重启而放行。超时阈值、停止证据和长期失联后的恢复操作仍待定义。

**跨轮历史的暂定规则：** 隔离边界是同一 Session 内的工作流节点，不是群，也不是 Bot。第二轮的研究节点只继承第一轮研究节点自己收到的输入和给出的回复；评审节点同理，即使两个节点绑定同一 Bot，也不共享交互记录。本轮的新请求和工作流显式绑定的上游结果仍按定义传递：评审节点可以收到研究节点本轮交付的结论，但不会因此得到研究节点的历史对话。失败或取消轮次中，节点已实际发生且已持久化的交互仍进入该节点自己的历史，标注所属轮次为失败或取消；不能伪装成成功 Artifact。

**验收场景（未执行）：** 第一轮在研究、评审两节点的各自交互中留下不同的标记，标记不进入传给下游的结果。复用 context 执行第二轮时，分别检查两个节点的实际输入：各有自己的标记、均无对方的标记；工作流绑定的本轮结果仍正常送达。换一个 context 后两者都没有旧标记。建议在受理新轮时固定历史读取边界，验证节点重试或 Worker 接管后不会多读、漏读或串读。对外可查的 Artifact 和节点历史分别定义保留策略：产物已清理时，按 `taskId` 查询须明确返回过期；历史被裁剪时，节点可区分“历史不完整”和“从未有过历史”。具体期限和裁剪方式待定，不改变节点隔离规则。

### 4. 协议与访问：哪些现有接口能直接复用？（D01、D02；Gateway / 身份与目录）

[当前 Card](../../crates/adapters/http/bcs-dist-gateway-http/src/card.rs)按路径生成 JSON-RPC、纯文本描述；[阻塞等待](../../crates/application/bcs-app-dist-gateway/src/wait.rs)到期返回当前快照；[列表](../../crates/application/bcs-app-dist-gateway/src/lib.rs)必须带 session，且 [wire 响应](../../crates/adapters/http/bcs-dist-gateway-http/src/rpc.rs)把当页数量当作 total。不能只换路由就宣称支持原案的 HTTP+JSON、等待超时 504、按群查询和游标分页。

**建议与问题：** 旧绑定行为保持不变。新 Card 只声明实际开放的群、输出模式、认证和流能力；可信 tenant/principal 来自认证链，不从 metadata 推断。群撤销发布后，是只拒绝新请求，还是也中断历史读取和已建立的订阅？冻结工作流用于执行，不应被误当作永久授权。需要明确查询、订阅、取消的权限，区分撤销与删除。断线重连建议以已授权快照为基线，不承诺逐事件无损重放（见[现有 Gateway 的流裁剪限制](../2026-11-17-gateway-a2a-inbound/plan.md)）。验收：跨群或跨主体不可发现资源；HTTP 504/断线不取消任务；热视图与归档分页不重计，禁止把内部节点消息直接投到公共流。

### 5. 能力：什么样的 StateMachine 可以发布？（C01、D04；群目录 / Runner）

“群类型是 FSM”不等于该部署能恢复执行其所有节点。建议首批仅发布具有明确最终输出节点的 BotTask 无环图，覆盖串行、扇出/汇合和有界重试；Judge、HumanInput、Loop 等不能用时在受理前拒绝，而不是接了再挂起。是否需要 Judge 才能满足首个实际调用场景，留给业务评审；若需要，必须单独定义可恢复的判定请求/结果，不能在[纯 reducer](../../crates/service-api/bcs-service-api/src/core/dist_runner.rs)里调用网络。

**Session 固定版本与绑定：** 创建 context 时固定工作流版本及节点到 Bot 的绑定。群改版或换绑后，旧 Session 的后续轮次和故障接管仍使用原版本、原绑定；使用新版或新成员须创建新 context，不自动迁移节点历史。固定执行版本不代替当前访问权限检查。验收：同名节点从 Bot X 换绑至 Y 后，新 context 里的 Y 不能读到旧 context 中 X 的历史。

## 开始实施和宣称可用是两道门槛

- **D0 评审先解决问题 1～3。** 对齐 #4245 的受理职责、#4133 能提供的停止事实、#4237 的 Runner 输入/结果。写出各故障点的持久记录、唯一写者、重试返回值。第 4～5 问中未定的能力不在 Card 宣告。旧计划中的类型名和 crate 版本不直接复制：当前 [Service API](../../crates/service-api/bcs-service-api/Cargo.toml) 已是 7.0.0，应按实际兼容性重新定版。
- **D05 / D06 不因只做 FSM 而消失。** 消费者合同测试覆盖抢占、丢回复、迟到事件和终态/释放间隙；Blob 清理要有无引用证明，checkpoint 压缩后仍能拒绝旧代次。#4190 提议公开响应/单个 SSE 帧共享 8 MiB 预算；#4245 则有意不对通用 Worker 施加正文大小配额。讨论点是由哪一层保证已成功的产物一定能读回，而不是把 HTTP 限额加给所有 Worker。建议在群结果发布前校验，在公开读取时限制实际 IO 和编码；超限不得静默截断。结果保留期、去重墓碑和 Worker 成员记录是不同生命周期，不能共用一个 TTL。
- **集成验收（未执行）。** 先用真实角色和可控下游跑两节点 FSM 的 send/get/stream/cancel，再分进程注入以上故障；核对只有一轮业务执行、Get/SSE 的最终 Artifact 一致、停止未知不释放。结果表述要区分库测试、真实 provider、跨进程接管和部署。本文只提出讨论和验证条件，不表示这些行为已经交付。
