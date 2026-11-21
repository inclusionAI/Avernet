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
| 本轮失败或取消，仍在核实期限内且有并行节点未确认结束 | 仍在收尾，返回 `409 / CONTEXT_BUSY`，不创建新 Task。核实期限届满仍未知则隔离，见 §3；FSM 确认整轮结束并提交 `Failed` 或 `Canceled` 后才可开下一轮。 |
| 原来**已受理**的请求因丢回包而重试 | 命中同请求去重，返回原 Task；这不是开启下一轮，也不应误报 BUSY。 |

这是 **FSM 群对外启动一轮**的准入规则，不是给整个群加锁，更不是禁用 Inbox。当前轮的节点回调、取消等输入仍可进入 Inbox；群消息投递仍可排队。已判定停止未知而进入隔离的 context 不是空闲态，见问题 3。

旧执行路径已有相近但不等价的能力：[服务 Session 再激活](../../crates/services/bcs-session/src/application/sessionmanagementserviceimpl_2.rs)在 Running 时拒绝 invoke，否则可用新输入再激活符合条件的 Session；[状态机启动](../../crates/services/bcs-collaboration-runtime/src/runtime/start.rs)生成新 Run ID，one-shot 路径的 idle 检查不等于所有分布式入口均已独占。分布式实现需要让多个 Gateway 和故障接管后的 Worker 对“当前是哪一轮”得出一致结论，而不是复用旧 SQL 行或在 HTTP 层先查后写。

只让通用 Worker 串行执行已经排队的 Task 不满足上述规则：第二个 Task 已被受理，而调用方应该收到 BUSY。可复用同一 Gateway application/core 和输入流，但不能用这个捷径作为产品验收；HTTP 绑定、持久化协议及故障恢复仍按下文讨论。

## 需要共同回答的问题

### 1. 受理：哪一次持久写入使请求有权执行？（D02、D03；Gateway / Worker / Store）

**反例：** Gateway A、B 同时向空闲 context 提交不同 messageId。当前[新 Task 准入](../../crates/services/bcs-dist-gateway/src/admission.rs)检查 Session 归属而不独占这一轮；[Inbox producer](../../crates/services/bcs-dist-store/src/inbox/producer.rs)允许在结果未知后补写，物理记录可能重复。Worker 串行处理两项，仍违反“第二项 409、不创建可执行 Task、不排队”。

**讨论选择：** 由 Worker 作为唯一受理方，在同一持久边界裁决占用与输入；还是沿用 #4190 的分步方案，先按代次占用，再写 Inbox，最后持久授予执行许可并保证重新调度？前者要证明写入能覆盖的原子范围，后者要证明任一步崩溃都能修复。建议与 #4245 对齐唯一受理方，再选提交协议；不依赖同进程锁或假设跨键事务。

**验收反例：** 分别在写 Inbox 前、写后但授权前、授权后但尚未再次唤醒时杀掉进程。胜者最多执行一轮且最终能被调度；败者即使留下部分记录也不能被执行，忙时被拒的 messageId 在 context 空闲后可重发。接管者须能证明未授权的 Start 不存在或已被丢弃，不能靠等一段时间解锁。

**入口与可见性边界。** 采用隔离而非改变旧调用语义：新建 Session 时持久冻结 `source=group_a2a`，通用 JSON-RPC 创建的 context 仍属于原入口。首轮 Sid 派生也区分来源。所有面向客户端的读、续聊、追加、取消和订阅都由 application/core 在副作用前校验持久来源、群与主体，不信任客户端自报字段。交叉使用 Sid/Tid（包括用 JSON-RPC 向 REST 的忙 Session 发送新消息）按不可访问资源处理，不写 Task、索引或 Inbox；旧格式只走原入口，不猜测为新来源。Worker 的节点回调仍走执行身份校验，不是新的公开受理入口。

执行许可也是公开可见边界：仅 `Accepted` 的请求能作为 Task 出现在 Get/List/计数/订阅中。受理前预写的快照、索引、Start，以及放弃的准备记录都不是可查询资源；不能用“稍后清理”代替读路径的共同过滤。并发败者、放弃后重启两种情形都不得出现幽灵 `SUBMITTED`。

**一次受理不等于下游副作用恰好一次。** 送达结论分三类：① 有可靠的未送达证明，或下游仍在协商的持久去重范围/期限内且可重用完全相同的调用身份和内容，才允许重发；② 只有查询能力时，只查原调用，暂时 `NotFound`、索引延迟、断连都不证明未送达或已停止；③ 既无可核实结果也无安全重发证据时保存 unresolved，不再自动发送，按 §3 监督。重试派发仍须有当前授权。对不具备恢复能力的 Bot 是否进一步禁止发布由部署策略收紧，绝不放宽这三条规则。

### 2. 重试：同一条消息究竟由哪些字段标识？（D01、D02；调用方 / Gateway）

#3979 按 `(tenant, principal, group, messageId)` 去重，客户端提供的 context、Task、配置和已激活扩展都参与指纹；[当前 input ID](../../crates/application/bcs-app-dist-gateway/src/identity.rs)还含 `sid`，[当前新 Task 的 digest](../../crates/application/bcs-app-dist-gateway/src/submit.rs)主要取群与消息内容。首轮回包丢失，再向另一实例重发，或把 `includeProgress` 从 false 改为 true，两者不能都被当作新任务。

**严格入口的重试顺序：** 先按当前权限授权，再用作用域内 messageId 查原请求记录（含未决预留）。命中时以持久化的首次 schema、默认值、有效配置和扩展集合规范化与判等；相同则恢复原受理或返回原 Task，语义改变则冲突，结果已清理则报过期，不重新执行。只有确定未命中才按当前默认值和能力验证新请求；创建预留遇到并发赢家，重新读取赢家的依据。目录下线某能力不应让仍有读取权限的重试变成新请求，被撤权则不能凭旧记录取回任何结果。存储读失败不是未命中。

### 3. 完成与取消：按 Run 状态放行，按 Task 查询产物（D04；Runner / Worker / Connector / Store）

**成功的唯一业务依据是本轮 FSM Run 的 `Completed`。** A2A Task 映射为 `COMPLETED`，其 Artifact 来自该工作流定义的最终输出节点（`final_output`），不取最后到达的分支回调，也不再让 Gateway 单独判断是否业务完成。[旧运行时](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs)优先取指定节点但还会回退到逆序找到的其他节点产出；新路径不采用这个回退。持久化是发布条件，不是第二个业务终态：归档写入失败不能对外返回不带应有 Artifact 的成功，接管后补发原结果，不重跑整轮。

**`Completed` 的提交边界：** 最终节点的输出按本轮 `taskId` 持久化，不覆盖 Session 的上一轮产物。权威 Run 状态一旦提交为 `Completed`，就同时满足两件事：有权限的调用方可用 `GetTask(taskId)` 读到该轮 Artifact；同一 Session 可原子受理下一轮。无需等待 SSE、额外的停止回执或第二个完成标记。这比 #4190 中“发布后再单独释放占用”的方案更严格：不能只因上轮的异步通知未结束就返回 BUSY。

**提交与可读证明。** 顺序为：固定最终公开投影与预算版本 → 用与 adapter 相同的编码规则校验完整 GetTask（约定历史预算）及每个必需 SSE 帧 → [幂等保存归档](../../crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs) → 带 owner fence 提交终态、归档引用、可接新轮的事实和待发布义务。校验包括 Task 外壳、Part 属性、公开历史、JSON 转义与 Blob 解引用；若最终产物不能完整发布，按 `PUBLIC_RESULT_TOO_LARGE` 失败收尾，不提交 `Completed`，不静默截断。归档先写后宕机时不提前暴露结果，接管只补原提交。

按 `(scope, group, Tid)` 读取权威终态证明，核对 round generation、提交身份、终态、归档引用及 digest。证明先随权威提交保存在 checkpoint，迁往持久按 Task 索引的证明记录且读回匹配后才允许压缩原证据。热快照缺失时不能仅凭 SQL 归档或 generation 水位宣布终态；冷热证明均无从确认时报依赖错误。证明至少保留到对应结果过期，并与过期墓碑安全交接；B 推进或重启不能使 A 不可读。

**通知不阻塞下一轮。** 已提交的终态发布义务由可恢复的独立发布队列跟踪，保留 Task 内事件顺序、稳定事件 ID 和待确认位置；TaskFeed append 失败只重试通知，不在 A 的轮次退役或 B 的调度路径上同步等待。验收时让共享 feed 持续写失败，不只是暂停 SSE 客户端；要求 A 仍可查且 B 实际启动，恢复 feed 后原事件按序补发。发布积压须有界，容量不足可在受理前按服务容量拒绝，不能把已完成的 A 伪报 BUSY。

**异常收尾与永久未知。** 正常 Runner、用户取消、deadline、步数/输出/容量上限、独立 finalizer、隔离和恢复路径都不能绕过同一终态裁决。取消意图或超时不是停止证明。任一在途 attempt 未确认结束时先关闭新派发，收尾期内 context 继续 BUSY；达到核实期限仍未知则持久标记 context 为 `Quarantined`，保留每个未核实调用与监督义务。

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

- **D0 评审先解决问题 1～3。** 对齐 #4245 的受理职责、#4133 能提供的停止事实、#4237 的 Runner 输入/结果。写出各故障点的持久记录、唯一写者、重试返回值。第 4～5 问中未定的能力不在 Card 宣告。旧计划中的类型名和 crate 版本不直接复制：当前 [Service API](../../crates/service-api/bcs-service-api/Cargo.toml) 已是 7.0.0，应按实际兼容性重新定版。
- **D05 / D06 不因只做 FSM 而消失。** 消费者合同测试覆盖抢占、丢回复、迟到事件和终态/释放间隙；Blob 清理要有无引用证明，checkpoint 压缩后仍能拒绝旧代次。#4190 提议公开响应/单个 SSE 帧共享 8 MiB 预算；#4245 则有意不对通用 Worker 施加正文大小配额。讨论点是由哪一层保证已成功的产物一定能读回，而不是把 HTTP 限额加给所有 Worker。建议在群结果发布前校验，在公开读取时限制实际 IO 和编码；超限不得静默截断。结果保留期、去重墓碑和 Worker 成员记录是不同生命周期，不能共用一个 TTL。
- **集成验收（未执行）。** 先用真实角色和可控下游跑两节点 FSM 的 send/get/stream/cancel，再分进程注入以上故障；核对只有一轮业务执行、Get/SSE 的最终 Artifact 一致、停止未知不释放。结果表述要区分库测试、真实 provider、跨进程接管和部署。本文只提出讨论和验证条件，不表示这些行为已经交付。
