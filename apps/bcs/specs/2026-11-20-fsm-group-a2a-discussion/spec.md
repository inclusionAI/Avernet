# 让 StateMachine 群成为 A2A Agent：分布式执行讨论稿

状态：**五项 D0 设计方向已选定，物理协议待验证**（2026-11-24）。仅讨论已配置工作流的 StateMachine 群。[D0 合同决策](contract-decisions.md)记录选定方案与冻结前的反例；[实施计划](plan.md)列出验证责任。这不代表 D0 已冻结、功能已实现或获准上线。

## 目标与依据

调用方只认识 Group、`contextId` 和 `taskId`，无需知道 BCS 换了哪台 Worker。每轮有独立的新输入；复用同一 context 时，跨轮上下文暂定按工作流节点隔离：每个节点只继承自己在该 Session 之前轮次中的交互记录，没有执行过的节点从空历史开始。本文讨论这些对外承诺如何映射到分布式 Gateway、Worker、Connector 和存储；不包括主从群、自由聊天、文件输入、轮内追问、Push Notification 或旧会话迁移。

- 产品参照：[原始 Group A2A spec（#3979 固定版本）](https://github.com/inclusionAI/Avernet/blob/387cd5c32a4d7c391a298ce6dfc3164cf3c764ab/apps/bcs/specs/2026-11-05-group-as-agent/spec.md)。原案用 Session activation / Run 和 `bcs_events` 关联一轮，要求忙时拒绝、最终产物持久化和整轮停止确认。
- 设计参照：[#4190 spec](https://github.com/inclusionAI/Avernet/blob/97a873ccdd33d1dc8d4b804b39a69ea0e7483863/apps/bcs/specs/2026-11-18-distributed-group-as-agent/spec.md) 与[实施计划](https://github.com/inclusionAI/Avernet/blob/97a873ccdd33d1dc8d4b804b39a69ea0e7483863/apps/bcs/specs/2026-11-18-distributed-group-as-agent/plan.md)。沿用其单一分布式 Session、纯 Runner 和 Worker 提交结果的方向；D0～D06 中的类型与方法不是现行 API。
- 实现基线（2026-11-24）：[GitHub dev `040a6267c`](https://github.com/inclusionAI/Avernet/tree/040a6267c40e866a09dcd79a9bea639503b6bca8/apps/bcs)（相对前次 `82a657e78`，BCS 代码无变化） 已合入 [Gateway #4132](https://github.com/inclusionAI/Avernet/pull/4132)、[Worker 协调 #4145](https://github.com/inclusionAI/Avernet/pull/4145) 和 [Connector #4133](https://github.com/inclusionAI/Avernet/pull/4133)。Connector 已有派发认领、每次调用的恢复/取消和出站 A2A 适配，但[尚未装配到 `bcs`，部分接管与未决上报仍是占位合同](../2026-11-17-connector-runtime/validation.md)。Gateway router 也未挂载；[Worker 协调](../../crates/services/bcs-dist-worker/README.md)不执行 reducer/outbox。[可恢复 Worker #4245](https://github.com/inclusionAI/Avernet/pull/4245) 仍在审，[群 Runner #4237](https://github.com/inclusionAI/Avernet/pull/4237) 仍是设计，不能把这些组件等同于可调用的分布式 FSM 群。

- 联合构建必须同时对齐 #4245 固定版本的 model、Service API、Connector report、Inbox/Store 和 Worker，不能只取 processor。dev Connector 的通用 `admit` 与新版 Inbox 不直接兼容；采用配套的 `ConnectorAdmission` / `admit_connector` 路径，保留 claim fence 和稳定输入身份，不伪造远端 event ID。以无远端事件 ID 的正常 GetTask 完成结果经过真实 Inbox 到达 Runner 为集成门槛，具体依赖见 plan。

## 已明确的产品规则：Session 可复用，每个 Task 启动一轮 FSM

`contextId` 标识可跨轮复用的 Session；一个新的 A2A Task 对应一次独立的状态机运行（Run）。一次性的是 Run，不是 context。新一轮从工作流入口使用本轮输入启动，不续跑、重置或覆盖上一轮 Run；上一轮的 Task 和产物仍按其自身标识读取。

| 同一 context 的场景 | 对外行为 |
| --- | --- |
| 空闲，收到新的独立请求 | 先到先得：最先原子占用该 Session 的请求获得本轮执行权；并发到不同 Gateway 时，不以客户端时间戳或某一实例收包时间裁决。 |
| 当前轮已受理或仍在执行，收到另一新请求 | 返回 `409 / CONTEXT_BUSY`；不创建第二个 Task、不排队、不自动补跑。 |
| 当前轮已提交 `Completed`，收到下一轮请求 | 同一 `contextId` 可以立即竞争启动新的 Task / Run，不等 SSE 送达或额外解锁；用上一轮 `taskId` 仍可查询它的 Artifact。 |
| 本轮失败或取消，仍在核实期限内且有并行节点未确认结束 | 仍在收尾，返回 `409 / CONTEXT_BUSY`，不创建新 Task。核实期限届满仍未知则隔离，见 §3；FSM 确认整轮结束并提交 `Failed` 或 `Canceled` 后才可开下一轮。 |
| 原来**已受理**的请求因丢回包而重试 | 命中同请求去重，返回原 Task；这不是开启下一轮，也不应误报 BUSY。 |

这是 **FSM 群对外启动一轮**的准入规则，不是给整个群加锁，更不是禁用 Inbox。当前轮的节点回调、取消等输入仍可进入 Inbox；群消息投递仍可排队。已判定停止未知而进入隔离的 context 不是空闲态，见问题 3。

旧执行路径已有相近但不等价的能力：[服务 Session 再激活](../../crates/services/bcs-session/src/application/sessionmanagementserviceimpl_2.rs)在 Running 时拒绝 invoke，否则可用新输入再激活符合条件的 Session；[状态机启动](../../crates/services/bcs-collaboration-runtime/src/runtime/start.rs)生成新 Run ID，one-shot 路径的 idle 检查不等于所有分布式入口均已独占。分布式实现需要让多个 Gateway 和故障接管后的 Worker 对“当前是哪一轮”得出一致结论，而不是复用旧 SQL 行或在 HTTP 层先查后写。

只让通用 Worker 串行执行已经排队的 Task 不满足上述规则：第二个 Task 已被受理，而调用方应该收到 BUSY。可复用同一 Gateway application/core 和输入流，但不能用这个捷径作为产品验收；HTTP 绑定、持久化协议及故障恢复仍按下文讨论。

## 需要共同回答的问题

### 1. 受理：哪一次持久写入使请求有权执行？（D02、D03；Gateway / Worker / Store）

**反例：** Gateway A、B 同时向空闲 context 提交不同 messageId。当前[新 Task 准入](../../crates/services/bcs-dist-gateway/src/admission.rs)检查 Session 归属而不独占这一轮；[Inbox producer](../../crates/services/bcs-dist-store/src/inbox/producer.rs)允许在结果未知后补写，物理记录可能重复。Worker 串行处理两项，仍违反“第二项 409、不创建可执行 Task、不排队”。

**以 `ckpt:<sid>` 的受保护 CAS 裁决 Session 占用，不另加锁。** 在审的 [#4245 单文档受理协议](https://github.com/inclusionAI/Avernet/blob/c7187edcc99354f017f2e1a9bfcb3d6e88c15b96/apps/bcs/crates/services/bcs-dist-store/src/inbox/README.md)能恢复同一 `(scope, input_id)`，不能裁决不同 input 的竞争。严格入口由 Worker 在 checkpoint 的同一次 CAS 中校验前轮状态、版本和 owner fence，安装新 generation、Tid、原请求引用、`Accepted` 及待调度义务；Inbox 和 `schedule:<sid>` 不是执行许可。被拒请求的准备记录不能在下一代自动升级。首次创建、无 owner 时如何受理并被发现、交接后如何拒绝旧写，须由 T1 原型验证。

**验收反例：** 分别在写 Inbox 前、写后但授权前、授权后但尚未再次唤醒时杀掉进程。胜者最多执行一轮且最终能被调度；败者即使留下部分记录也不能被执行，忙时被拒的 messageId 在 context 空闲后可重发。接管者须能证明未授权的 Start 不存在或已被丢弃，不能靠等一段时间解锁。

**入口与可见性边界。** 采用隔离而非改变旧调用语义：新建 Session 时持久冻结 `source=group_a2a`，通用 JSON-RPC 创建的 context 仍属于原入口。首轮 Sid 派生也区分来源。所有面向客户端的读、续聊、追加、取消和订阅都由 application/core 在副作用前校验持久来源、群与主体，不信任客户端自报字段。交叉使用 Sid/Tid（包括用 JSON-RPC 向 REST 的忙 Session 发送新消息）按不可访问资源处理，不写 Task、索引或 Inbox；旧格式只走原入口，不猜测为新来源。Worker 的节点回调仍走执行身份校验，不是新的公开受理入口。

执行许可也是公开可见边界：仅 `Accepted` 的请求能作为 Task 出现在 Get/List/计数/订阅中。受理前预写的快照、索引、Start，以及放弃的准备记录都不是可查询资源；不能用“稍后清理”代替读路径的共同过滤。并发败者、放弃后重启两种情形都不得出现幽灵 `SUBMITTED`。

**一次受理不等于下游副作用恰好一次。** 送达结论分三类：① 有可靠的未送达证明，或下游仍在协商的持久去重范围/期限内且可重用完全相同的调用身份和内容，才允许重发；② 只有查询能力时，只查原调用，暂时 `NotFound`、索引延迟、断连都不证明未送达或已停止；③ 既无可核实结果也无安全重发证据时保存 unresolved，不再自动发送，按 §3 监督。当前 A2A [出站适配](../../crates/external-clients/bcs-a2a-http/src/dist_agent/mod.rs)能用已知远端 Task ID 查任务，但不支持从 message ID 反查远端 Task；启动回包丢失时，不得仅凭 Bot 支持 GetTask 就宣告可找回原调用。重试派发仍须有当前授权。对不具备恢复能力的 Bot 是否进一步禁止发布由部署策略收紧，绝不放宽这三条规则。

### 下游调用的恢复与证据

- 首次发送前记录操作、稳定请求 ID、发送内容和有效配置的摘要、实际去重域及保证有效的截止时间。接管时重新解析目标并检查当前授权；即使 `credential_ref` 或 message ID 不变，域变了、期限已过或无法证明仍有效，都不能依赖幂等性重发。授权上下文来自受理及派发记录，至少区分 scope、群、轮次、节点、attempt、目标和操作；同一 binding 的研究节点无权发新数据，不应影响评审节点独立获准的 query/cancel。
- 首次受理回执须与原操作关联，固定目标域、远端 Task 和 context 引用。查询、流事件和取消响应都要与该引用及原 attempt、操作匹配；收到别的 Task 的终态不证明本调用已停，也不能把它的输出交给下游。没有可核实引用时保留未知。
- 首批只发布能在恢复期内按原远端 Task 取回**完整、有序最终产物**的 BotTask；用真实 GetTask 结果验证此能力，不接受仅有 `Completed` 或不可恢复的 Delta 流。正常不断流与断流接管遵守同一规则：`Completed` 状态帧只能提供经核验的停止事实，不能直接固定产物 `Final`。完整 GetTask 的清单、顺序、摘要及入站预算验证通过并持久固定后，才首次提交产物 Final；Inbox 的首次有效内容不能靠后续完整 Final 覆盖。恢复时不拼接部分流和快照。已确认停止但结果缺失时不宣告成功；按结果恢复期限有界重查，耗尽则以结果不完整失败收尾。运行中若返回 `INPUT_REQUIRED` / `AUTH_REQUIRED`，冻结“不支持轮内交互”的失败原因、停止新派发，经同一停止屏障收尾，不公开追问、代填或等用户到总 deadline。

### 2. 重试：同一条消息究竟由哪些字段标识？（D01、D02；调用方 / Gateway）

#3979 按 `(tenant, principal, group, messageId)` 去重，客户端提供的 context、Task、配置和已激活扩展都参与指纹；[当前 input ID](../../crates/application/bcs-app-dist-gateway/src/identity.rs)还含 `sid`，[当前新 Task 的 digest](../../crates/application/bcs-app-dist-gateway/src/submit.rs)主要取群与消息内容。首轮回包丢失，再向另一实例重发，或把 `includeProgress` 从 false 改为 true，两者不能都被当作新任务。

**严格入口的重试顺序：** 先按当前权限授权，再用作用域内 messageId 查原请求记录（含未决预留）。命中时以持久化的首次 schema、默认值、有效配置和扩展集合规范化与判等；相同则恢复原受理或返回原 Task，语义改变则冲突，结果已清理则报过期，不重新执行。只有确定未命中才按当前默认值和能力验证新请求；创建预留遇到并发赢家，重新读取赢家的依据。目录下线某能力不应让仍有读取权限的重试变成新请求，被撤权则不能凭旧记录取回任何结果。存储读失败不是未命中。

### 3. 完成与取消：按 Run 状态放行，按 Task 查询产物（D04；Runner / Worker / Connector / Store）

**成功的唯一业务依据是本轮 FSM Run 的 `Completed`。** A2A Task 映射为 `COMPLETED`，其 Artifact 来自该工作流定义的最终输出节点（`final_output`），不取最后到达的分支回调，也不再让 Gateway 单独判断是否业务完成。[旧运行时](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs)优先取指定节点但还会回退到逆序找到的其他节点产出；新路径不采用这个回退。持久化是发布条件，不是第二个业务终态：归档写入失败不能对外返回不带应有 Artifact 的成功，接管后补发原结果，不重跑整轮。

**`Completed` 的提交边界：** 最终节点的输出按本轮 `taskId` 持久化，不覆盖 Session 的上一轮产物。权威 Run 状态一旦提交为 `Completed`，就同时满足两件事：有权限的调用方可用 `GetTask(taskId)` 读到该轮 Artifact；同一 Session 可原子受理下一轮。无需等待 SSE、额外的停止回执或第二个完成标记。这比 #4190 中“发布后再单独释放占用”的方案更严格：不能只因上轮的异步通知未结束就返回 BUSY。

**提交与可读证明。** 顺序为：固定最终公开投影与预算版本 → 用与 adapter 相同的编码规则校验完整 GetTask（约定历史预算）及每个必需 SSE 帧 → [幂等保存归档](../../crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs) → 带 owner fence 提交终态、归档引用、可接新轮的事实和待发布义务。校验包括 Task 外壳、Part 属性、公开历史、JSON 转义与 Blob 解引用；若最终产物不能完整发布，按 `PUBLIC_RESULT_TOO_LARGE` 失败收尾，不提交 `Completed`，不静默截断。归档先写后宕机时不提前暴露结果，接管只补原提交。

按 `(scope, group, Tid)` 读取权威终态证明，核对 round generation、提交身份、终态、归档引用及 digest。证明先随权威提交保存在 checkpoint，迁往持久按 Task 索引的证明记录且读回匹配后才允许压缩原证据。热快照缺失时不能仅凭 SQL 归档或 generation 水位宣布终态；冷热证明均无从确认时报依赖错误。证明至少保留到对应结果过期，并与过期墓碑安全交接；B 推进或重启不能使 A 不可读。

**通知不阻塞下一轮。** 已提交的终态发布义务由可恢复的独立发布队列跟踪，保留 Task 内事件顺序、稳定事件 ID 和待确认位置；TaskFeed append 失败只重试通知，不在 A 的轮次退役或 B 的调度路径上同步等待。验收时让共享 feed 持续写失败，不只是暂停 SSE 客户端；要求 A 仍可查且 B 实际启动，恢复 feed 后原事件按序补发。发布积压须有界，容量不足可在受理前按服务容量拒绝，不能把已完成的 A 伪报 BUSY。

首次补发也须有权写。权威提交要为 `AppendEvents` 与 `PublishSnapshot` 成对留下可独立验证的许可，绑定 Sid、轮次、effect 身份、事件范围/内容摘要/Sid 前驱以及快照摘要/source_commit/snapshot_event_id。两种效果均异步执行；即使 A 从未成功 append 且 B 已替换 checkpoint，存储也能先发布 A 的事件、再发布对应快照。append 成功但 publish 前宕机仍可恢复，不因前者成功就清掉后者的许可或引用 pin。B 的业务执行不等 A，但 B 的 feed 不能越过 A。公共 Get 的权威终态证明不依赖 feed 快照发布；通知义务的退役/裁剪必须验证两步完成或同 Task 快照被合法更新覆盖。普通队列记录不构成许可，其他 effect 仍受当前 outbox 栅栏约束。

**接管和持续容量。** Connector 已 ACK 后死亡，不得寄望旧 hint 重现。Worker 的 shard Supervisor 根据权威 Dispatch 和 holder 存活性重新通知接任者，凭已保存的远端身份在原 deadline 内接回在途调用，不重启远端任务。Dispatch 目录须能持续接受同 shard 的成功流量；只有终结事实已持久、无人再需重放或监督且旧写仍可被拒绝，才可回收目录项。checkpoint 压缩、TTL 或删除未知调用均不能替代此证明。回收与 Supervisor 扫描共享稳定目录键和版本化游标，不沿用可收缩数组的下标；删除游标之前的终结项不得漏掉尚在执行的项或使重启扫描永久报错，当前扫描 work 的引用须受保护。

**异常收尾与永久未知。** 正常 Runner、用户取消、deadline、步数/输出/容量上限、独立 finalizer、隔离和恢复路径都不能绕过同一终态裁决。取消意图或超时不是停止证明。任一在途 attempt 未确认结束时先关闭新派发，收尾期内 context 继续 BUSY；达到核实期限仍未知则持久标记 context 为 `Quarantined`，保留每个未核实调用与监督义务。

- 短期查询耗尽后，在权威 Dispatch 中以受保护 CAS 登记核实模式、独立 claim epoch 和待调度义务，并使普通 claim 失效；确认交接后才释放普通槽。队列仅是可修复索引，不能另立所有权源；CAS 后未入队也须被 Supervisor 发现。普通 claim、旧 hint 和原实例 actor 重建均拒绝核实模式，核实者不要求原进程死亡即可按独立 epoch 领取，旧 holder 写被拒。按到期时间低频、有界重查；重启恢复，迟到通知或权限恢复可触发提前核实，只允许获准的 query/cancel 与报告，不授权重发。无查询能力或无控制权限时保留监督义务并告警，不向下游发送业务数据。隔离项有独立容量和并发预算，受理前预留；积压不得挤占其他 context 的普通调用槽或丢弃已承担的义务。
- 按请求找回任务是独立能力，不是标准 GetTask 的推论。当前真实 A2A adapter 的 `ByRequest` 为 `Unsupported`；需要启动回包丢失后恢复的部署须交付经协商、绑定原请求作用域的查询扩展与真实 adapter 验收。标准 profile 没有远端 Task ID 时继续安全隔离，不猜造引用或重发；扩展 profile 要验证索引迟到后主动查得原 Task 并核验停止，而非向假 port 注入结果。
- 隔离不是 Task 终态。A 的 Get 返回原 Tid、非终态 `WORKING` 和公开原因 `STOP_UNCONFIRMED`；订阅发送这份状态后关闭当前 SSE（不是终态帧），重连可重新获取；阻塞 send 仍受原等待上限约束。相同请求重试取回 A 和原状态，不创建新轮。
- 隔离时同 context 的新请求返回 `400 / FAILED_PRECONDITION`、`CONTEXT_UNAVAILABLE`，与正常 `409 / CONTEXT_BUSY` 区分。即使永远没有证据也维持此行为，重启、时间流逝、GC 或普通管理员解锁均不是证据。已证实全部停止后提交冻结裁决的 `Failed` 或 `Canceled`，再允许新轮。核实期限、告警路由及可审计人工证据格式留给部署评审。
- 这是对 #3979「可以先公开 FAILED、context 继续隔离」的明确取舍：本案遵守已确定的「整轮停稳才终结」，不向上游虚报失败已收尾。

**跨轮历史的暂定规则：** 隔离边界是同一 Session 内的工作流节点，不是群，也不是 Bot。第二轮的研究节点只继承第一轮研究节点自己收到的输入和给出的回复；评审节点同理，即使两个节点绑定同一 Bot，也不共享交互记录。本轮的新请求和工作流显式绑定的上游结果仍按定义传递：评审节点可以收到研究节点本轮交付的结论，但不会因此得到研究节点的历史对话。失败或取消轮次中，节点已实际发生且已持久化的交互仍进入该节点自己的历史，标注所属轮次为失败或取消；不能伪装成成功 Artifact。

**验收场景（未执行）：** 第一轮在研究、评审两节点的各自交互中留下不同的标记，标记不进入传给下游的结果。复用 context 执行第二轮时，分别检查两个节点的实际输入：各有自己的标记、均无对方的标记；工作流绑定的本轮结果仍正常送达。换一个 context 后两者都没有旧标记。受理本轮时固定 cut 并保护该边界的历史版本；派发前持久保存实际选中条目的有序不可变引用、摘要及 `truncated` 值，再将其绑定到节点输入。裁剪不能回收活动轮的历史 pin，接管重放使用同一份内容，不用相同 cut 重新选取已变化的数据；只有不再可能重放时才能解除保护。对外可查的 Artifact 和节点历史分别定义保留策略：产物已清理时，按 `taskId` 查询须明确返回过期；历史被裁剪时，节点可区分“历史不完整”和“从未有过历史”。具体期限和裁剪方式待定，不改变节点隔离规则。

### 4. 协议与访问：哪些现有接口能直接复用？（D01、D02；Gateway / 身份与目录）

[当前 Card](../../crates/adapters/http/bcs-dist-gateway-http/src/card.rs)按路径生成 JSON-RPC、纯文本描述；[阻塞等待](../../crates/application/bcs-app-dist-gateway/src/wait.rs)到期返回当前快照；[列表](../../crates/application/bcs-app-dist-gateway/src/lib.rs)必须带 session，且 [wire 响应](../../crates/adapters/http/bcs-dist-gateway-http/src/rpc.rs)把当页数量当作 total。不能只换路由就宣称支持原案的 HTTP+JSON、等待超时 504、按群查询和游标分页。

**发布与撤权分开处理。** 显式撤销发布后，不受理新 Task、不再发布可调用的 Card；已受理的调用不会因此自动取消。原 Task 的重试读取、Get、订阅和取消仍逐项检查当前访问/控制权限；权限已撤销则隐去资源并关闭既有订阅，不因冻结过工作流而放行。新 Card 只声明实际开放的输出模式、认证和流能力；可信 tenant/principal 来自认证链，不从 metadata 推断。断线重连以已授权快照为基线，不承诺逐事件无损重放（见[现有 Gateway 的流裁剪限制](../2026-11-17-gateway-a2a-inbound/plan.md)）。验收：跨群或跨主体不可发现资源；HTTP 504/断线不取消任务；禁止把内部节点消息直接投到公共流。

**公开列表选择快照分页。** 首次查询取得统一授权 Task 索引的快照版本；成员资格以 `Accepted` 和该版本内已提交的状态为准。同一 Tid 无论热读或归档只计一次，按 `(status_updated_at DESC, tid ASC)` 稳定排序；过滤后的 `total`、各页、状态及投影都取同一 cut，不分别计两个存储再相加。T1 必须给出该索引与受理/终态证明的因果边界：查询开始前已公开的提交不得因异步索引滞后而遗漏；无法取得可验证的 cut 时返回可重试依赖错误，不报一个貌似准确的 total。不可篡改游标绑定 scope/group、筛选、排序、cut、末键和有效期。版本在游标有效期内保留；版本失效或权限变化则拒绝续页并要求重新查询，不静默切到实时视图。验收：页间 A 热转冷、新增 C、另一个 Task 更新时间改变，后页既不多出 C，也不漏掉或重复 A。旧 JSON-RPC 游标不通用。

### 5. 能力：什么样的 StateMachine 可以发布？（C01、D04；群目录 / Runner）

“群类型是 FSM”不等于该部署能恢复执行其所有节点。建议首批仅发布具有明确最终输出节点的 BotTask 无环图，覆盖串行、扇出/汇合和有界重试；Judge、HumanInput、Loop 等不能用时在受理前拒绝，而不是接了再挂起。是否需要 Judge 才能满足首个实际调用场景，留给业务评审；若需要，必须单独定义可恢复的判定请求/结果，不能在[纯 reducer](../../crates/service-api/bcs-service-api/src/core/dist_runner.rs)里调用网络。

**冻结绑定，不冻结权限。** 发布时先不可变保存 workflow 修订、规范化内容摘要及编译版本，再更新当前发布指针；创建 context 时固定修订和节点绑定，引用仍有效时不得清理。普通换绑不把旧 context 自动切到新 Bot；旧 Run 接管必须按原 revision/digest 加载，缺失或摘要不符时报错，绝不回退最新版。使用新版须开新 context，不迁移节点历史。

每次新派发、重发或携带新数据的继续调用（包括已排队动作在发出前）都复核当前租户、群成员资格、节点委托和目标凭据权限；可解析的旧 credential_ref 不代表授权。被移除/撤权的 Bot 不得再接收本轮输入或历史，也不能自动换人。对已经发出的调用，仅可在仍有独立控制授权的前提下用原身份 query/cancel，不附新业务数据；无此权限则保持 unresolved，按 §3 核实。授权恢复不自动重发未知操作。

**真实记忆隔离是发布条件。** 不同 conversation ID 不是隔离证据。同一 Bot/凭据下不同节点、不同 Session 必须使用经实际下游验证的隔离记忆域，或明确关闭共享长期记忆并验证无状态调用。用不进入上游绑定的私有标记做跨节点/跨 Sid 负测；共享记忆无法禁用、配置变化后无法重新验证或测试串读时，拒绝发布或停止新派发。

## 开始实施和宣称可用是两道门槛

- **D0 先验证同一 Sid 上的受理与完成，再验证列表快照。** 用 A/B 两轮校验每个持久切面；列表必须能证明覆盖查询开始前已公开的提交。重试与下游证据样例可以并行。 对齐 #4245 的受理职责、#4133 能提供的停止事实、#4237 的 Runner 输入/结果。写出各故障点的持久记录、唯一写者、重试返回值。第 4～5 问中未定的能力不在 Card 宣告。旧计划中的类型名和 crate 版本不直接复制：当前 [Service API](../../crates/service-api/bcs-service-api/Cargo.toml) 为 7.1.0，应从该基线评估增量兼容性。
- **D05 / D06 不因只做 FSM 而消失。** 消费者合同测试覆盖抢占、丢回复、迟到事件和终态/释放间隙；Blob 清理要有无引用证明，checkpoint 压缩后仍能拒绝旧代次。#4190 提议公开响应/单个 SSE 帧共享 8 MiB 预算；#4245 固定版本另在 Worker `prepare.rs` 解引用下游事件时使用 `PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES`（8 MiB），并非无正文限制。公开预算、入站节点结果预算和内部 Feed 编码上限分别约束不同边界；小最终产物或小 Blob 引用不能豁免大中间结果。Connector 从 IO 起限长，在首次报告前校验完整事件大小；严格来源超限时保存独立可核验的停止事实并提交有界失败，不静默截断、不误报成功，也不把正常业务超限当作存储损坏隔离。未核实停止仍经统一屏障，不能靠读到超限推断已停。结果保留期、去重墓碑和 Worker 成员记录是不同生命周期，不能共用一个 TTL。
- D0 同时冻结公开和内部编码预算：#4245 固定版本 TaskFeed 单事件 128 KiB、单批 256 KiB，并非公开 8 MiB。允许以不可变、受保护的归档引用承载大产物，但提交前须证明内部事件/批能写且公开投影可完整还原。D0 同时冻结权限矩阵、撤权边界、分页版本与期限、非默认投影预算；群详情的版本一致性不构成发布目录或 Task 索引证明。
- 联合角色需共享支持精确版本 CAS、`PersistentJsonMerge`、`AtomicJson`、Stream 与 `StreamGroups`、KV、ZSet、membership 与 `RetainedLease` 的真实 provider；须证明不同更新方式在同一文档上互操作。普通 `StrictLease`/TTL 删键不等价：必须保留可区分 `Terminated/Dead` 与 `Absent/Unknown` 的证据，覆盖仍被引用的接管义务，并在 provider 重启后验证。新生产 Runner 除 FSM 用例外须通过自己的 R25 集中合同，validator 测试不代替它。
- **集成验收（未执行）。** 先用真实角色和可控下游跑两节点 FSM 的 send/get/stream/cancel，再分进程注入以上故障；核对只有一轮业务执行、Get/SSE 的最终 Artifact 一致、停止未知不释放。结果表述要区分库测试、真实 provider、跨进程接管和部署。本文只提出讨论和验证条件，不表示这些行为已经交付。
