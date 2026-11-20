# StateMachine Group as Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让已发布的 FSM 群在同一 A2A context 内执行多轮独立 Task；并发新请求先到先得，结果可按 Task 查询，节点历史隔离。

**Architecture:** 复用分布式 Gateway、Worker、Connector、TaskFeed。Gateway 负责协议和访问控制；Worker 是受理和持久提交的责任方；纯 Runner 决定节点动作与整轮结果；Connector 只报告下游事实。只新增群调用需要的合同，不让旧 SQL Session 或 HTTP handler 成为第二个执行权来源。

**Tech Stack:** BCS Rust workspace、Axum HTTP+JSON/SSE、`bcs-store-api` 的持久 CAS/stream、BCS DB plugin（SQLite/MySQL）、Cargo conformance、Singlebox。

**Spec:** [FSM 分布式讨论稿](spec.md)；[历史计划 #4190](https://github.com/inclusionAI/Avernet/blob/97a873ccdd33d1dc8d4b804b39a69ea0e7483863/apps/bcs/specs/2026-11-18-distributed-group-as-agent/plan.md)。本文是**供评审的实施计划**。已确认的语义以本地 spec 为准；下列新文件、签名、拆包和决策门槛是实施建议，不是现有 API 或已通过的测试。

## Global Constraints

- 同一 `contextId` 复用分布式 `Sid`；每轮新 `Tid`、新 Run，不续跑上一轮。新请求竞争以持久原子占用为准，输家 `409 / CONTEXT_BUSY`，无 Task、无自动排队，之后同一 messageId 可重发。已受理请求重试先复用原 Task。
- 最终节点 `final_output` 是唯一成功产物来源。提交权威 `Completed` 时 `GetTask(taskId)` 必须已可读且下一轮即可受理；**不能等待 SSE 或异步解锁**。不采用 #4190 的“先公开终态再释放”作为客户端语义。
- 失败/取消必须等所有在途工作停止；停止未知、超时、缺记录和 Worker 重启均不授权开下一轮。不能承诺不支持去重的 Bot 恰好执行一次。
- 历史按授权范围、Session、冻结工作流中的逻辑节点隔离，不按 Bot 聚合。失败/取消轮中真实且持久化的交互仍保留，并标注轮次结果；不是 Artifact。新版工作流或新绑定用新 context。Artifact 过期与节点历史裁剪分别表示。
- 对外执行仅 FSM。排除 ManagerWorker、自由聊天、文件输入、轮内追问和旧 Session 迁移。保留原 JSON-RPC 对旧资源的行为；新增严格来源禁止跨入口读写，包括不带 Sid 的首次身份派生。默认不挂载新服务；真实 provider 与跨进程验收通过才谈上线。

## Review Focus

- 两个 Gateway 的不同请求竞争，输家在前轮结束后用同一 ID 重发仍能成功（T2）。
- 两个逻辑节点绑定同一 Bot，即使该 Bot 自带对话记忆也不能串读（T3、T6、T9）。
- SQL 归档已写、权威完成未提交；或已提交、SSE 未发布。两种窗口不能呈现相同的可见性（T4、T7）。
- 分支失败时兄弟节点或超时重试的旧 attempt 还在远端执行；租约交接不能清除这项义务（T5、T7）。
- 旧 context 的成员换绑或发布被撤销，归档、游标和持续订阅均不能绕过作用域检查（T5、T8）。

---

## 基线、依赖和决策门槛

本地基线 `f0540f9b6`；2026-11-20 核对 GitHub `dev` 为 `224c1e814`，两者间只有 Backend #4283，`apps/bcs/` 无差异。已合入的是 Gateway 库、Inbox producer 和 Worker 协调，不是真实 FSM 执行链。[#4245](https://github.com/inclusionAI/Avernet/pull/4245)（Worker 受理/processor/归档）和 [#4133](https://github.com/inclusionAI/Avernet/pull/4133)（Connector）仍在审；[#4237](https://github.com/inclusionAI/Avernet/pull/4237) 是 Runner 设计而非依赖包。下文标 `[#4245]` 的路径来自其 `1599541c82`，不能假定已在 dev；合入或接口变更后先刷新对应任务。未接好依赖前，可以提交纯模型、Runner 和消费者合同，不声称端到端可用。

| 决策点 | 建议的工作基线（非用户已批准的默认值） | 最晚冻结点；不通过时怎么办 |
| --- | --- | --- |
| 权威写者、受理/完成的原子边界 | 沿 #4245 由 Worker 侧统一受理。确认 provider 能否单步裁决；否则用 #4190 的预留、独占、持久许可及恢复协议。两种方案必须满足相同反例。 | **T1，阻塞 T2/T7 并行实现共享写入。** 记录实际 key/表、CAS/事务范围、栅栏及丢响应读回证据；不能用内存锁或分布式多键事务假设代替。 |
| 重试等价性、线协议与查询预算 | 严格入口用 `(tenant, principal, group, messageId)`；完整规范化输入、显式 context、有效配置和扩展入指纹。建议采用 #3979 HTTP+JSON 与 #4190 的有界读取；不改变 JSON-RPC。 | T1 冻结重试样例，T8 冻结等待时长、分页、历史及响应预算、错误和扩展。未冻结不发布 Card。 |
| 首批工作流及 Bot 恢复能力 | 建议 BotTask 无环图、唯一最终节点，串行/扇出汇合；排除 Judge、Loop、HumanInput。送达未知时仅允许有持久去重保证且仍在其有效窗口内按原身份重发；只有查询能力不允许重发。 | T5/T6：决定拒绝发布该类 Bot 还是允许挂起并核实。Judge 如必需，应另立有恢复证据的扩展，不插进本批 reducer。 |
| 历史、Artifact、请求墓碑及 Blob 保留 | 分开保存期限；固定历史读边界、裁剪标识；在引用解除前不能回收。#4190 的 8 MiB 是候选的**公开响应/单帧**预算，不是所有 Worker 的正文配额。 | T3/T4 定裁剪与过期合同；T9 前定期限、容量、未知停止告警、核实与人工恢复依据。无依据不解锁；无保留政策不启用自动删除。 |
| 群开放、认证和撤销后的访问 | 只从可信认证链取 scope；显式发布；Card 只声明已实现能力。当前授权与冻结的工作流版本分别检查。 | T8 前确认 Card、send、历史读取、订阅和 cancel 的权限矩阵（含既有订阅撤权）。没有明确授权就不开放对应入口。 |

## 两项不能用测试替身代交的依赖

**P：跨进程持久协调 provider（外部交付，阻塞 T2 的 provider GREEN、T7 故障验收和 T9）。** 交付责任在部署侧存储 provider，不在 HTTP 或 SQL 归档实现。当前基线的 Document/Stream 实现是测试模型或包装器；SQLite/MySQL 能保存归档不证明其已满足协调合同。接入方须交付可由两个独立进程连接同一隔离 namespace 的具体插件和启动配置，列出版本、能力（document exact-version CAS、Stream append/读回、KV、ZSet、基线要求的 membership/lease）及保留策略。对该实现执行 `bcs-store-api` 对应合同、`bcs-dist-store` 受理/协调 harness；同一套实例验证旧 owner 提交被拒、丢响应按身份读回、持久服务重启不丢已确认状态。T9 的 `validation.md` 必须记录运行命令、provider 版本和故障结果；缺交付就标 blocked，不用每进程内存或仅 SQL 归档补位。

**W：不可变工作流修订目录（本计划交付，T5 校验后、T7/T9 真实重启前）。**

- [ ] 新增 `crates/services/bcs-dist-store/src/workflow_revision.rs`、双方言新迁移及 `tests/conformance_workflow_revision.rs`；通过 DB plugin 原子保存不可变 `(scope, group, revision, digest)` 的定义、编译版本、绑定清单和当前发布指针。同一 revision 异内容拒绝；先有可验证对象再让 `resolve` 返回它，不能原地覆盖。
- [ ] 实现 `DistGroupDirectoryPort::{resolve,load_workflow}` 及发布写侧，Session 固定引用、活动/可恢复 context 的修订不回收；加载校验 scope 和 digest，缺失、旧格式或损坏时明确报错。测试 `publish_v2_restart_loads_pinned_v1`：发布 v1 → 创建 S → 发布 v2 → 清空进程缓存重启；S 继续用 v1 原绑定，新 context 用 v2；删掉/篡改 v1 不得回退 v2。
- [ ] RED/GREEN：`cargo test -p bcs-dist-store --test conformance_workflow_revision`，按 R25 调真实目录 contract harness，并跑双方言迁移；W 未交付不装配 fixture 作为群目录。

## 文件与实施顺序

路径均相对 `apps/bcs/`；Cargo 命令在该目录执行。除标明的现有文件外，`新增` 文件和所列 `--test` 目标是**计划产物**。各任务先创建测试并运行得 RED，不能把“零项测试”当通过；每次 GREEN 后形成单独可审查提交，不混入全局格式化。

| 任务 | 文件职责 | 依赖 |
| --- | --- | --- |
| T1 共享合同与夹具 | `crates/contracts/bcs-dist-model/src/group_round.rs`、Service API 分布式 repo 与集中 conformance | 先做 |
| T2 受理、许可、调度 | `bcs-dist-store` 的群占用与 Inbox，`bcs-dist-worker` 的 canonical admission | T1 + #4245 接口对齐 |
| T3 节点历史 | 节点历史 repo、DB provider、冻结读取及裁剪 | T1 |
| T4 历史产物 | 扩展 #4245 archive，按 Task 保存/读取、过期、Blob 引用和 GC | T1 + 归档合同 |
| T5 工作流与纯执行 | 共享冻结能力校验、确定性 FSM reducer | T1；可用 fixture |
| T6 下游执行证据 | Connector 的节点会话隔离、送达未知、停止事实 | T1/T3/T5 + #4133 |
| T7 Worker 完成桥接 | processor、持久 outbox、按权威 cut 读取和接管 | T2～T6 + #4245 |
| T8 Gateway | application、Card、HTTP+JSON、查询及 SSE | T1 后可先用替身；真实读写依赖 T2/T4/T7 |
| T9 装配与故障验收 | bootstrap 配置、真实角色、Singlebox 跨进程 | T1～T8 + P/W 的交付证据 |

### T1：冻结跨进程合同，不先写 HTTP handler

**文件：** 新增 `crates/contracts/bcs-dist-model/src/group_round.rs`、`crates/service-api/bcs-service-api/src/port/repo/distributed/group_round.rs`、`crates/test-support/bcs-test-support/src/contract/distributed/group_round.rs`、`crates/contracts/bcs-dist-model/tests/group_round_wire.rs`；修改各模块导出、`src/core/dist_runner.rs`（Service API）、相应 Cargo.toml 和 `Cargo.lock`。

**接口草案：** `RoundKey { scope: TaskScope, group: GroupId, sid: Sid, tid: Tid, generation: u64 }`；`RoundInput { key, ordered_parts, workflow_ref, history_cut, execution_deadline }`；`Admission::{New, Replay, Busy, Conflict, Expired}`（New/Replay 带原身份和返回配置）；`TerminalProof` 绑定 RoundKey、受保护提交身份与结果/收尾证据。协议 DTO 不进模型；任何已有状态不能靠补默认 scope 或缺省 generation 自动升级。

- [ ] 写 `frozen_identity_survives_reload` 和 `old_shape_cannot_guess_owner`，断言 `assert_eq!(reload(&input)?, input)`，旧格式缺 scope/generation 必须显式拒读。集中 harness 命名按 `<trait_snake>_contract_tests`。
- [ ] RED：`cargo test -p bcs-dist-model --test group_round_wire`；预期缺类型或 wire 断言失败。
- [ ] 在同目录新增 `contract-decisions.md`，选定上表第一项的物理提交点和恢复顺序。列出首轮缺/有 context、重复相同/不同内容、BUSY 后重发、重试跨配置更新的向量；用两个竞争请求和一次丢回包走通。审批后同步模型、Service API、所有构造者和消费者；按实际基线决定版本，不重复使用历史计划中的 7.0.0 升级号。
- [ ] 冻结 Session `source`、分域的首次 Sid/Tid 派生及 T8 的跨入口拒绝。T1/T2 增加 `replay_before_current_capabilities`：当前授权 → 按稳定请求键查原记录 → 用首次保存的规范化依据判等/恢复；仅确定未命中才按当前目录验证新请求，预留竞争失败重读赢家。A 丢回包后改默认值或下线能力，重试仍取 A；撤销访问则不能读 A。`Accepted` 同时授权执行和公开读取，预写数据没有此资格。
- [ ] GREEN：重跑 RED 命令；`cargo check --workspace --all-targets`；`bash scripts/ci/check-public-api.sh` 并单独检查 `bcs-dist-model` 的版本/序列化兼容性；门禁缺工具或跳过不能记作通过。
- [ ] 提交 `feat(bcs): define recoverable FSM group round contracts`。

### T2：并发先到先得，BUSY 不产生可执行任务

**文件：** 新增 `crates/services/bcs-dist-store/src/group_admission.rs`、`crates/services/bcs-dist-store/tests/conformance_group_round.rs`；扩展 `crates/services/bcs-dist-store/src/inbox/producer.rs`、`crates/service-api/bcs-service-api/src/port/repo/distributed/inbox.rs` 与 `crates/services/bcs-dist-worker/src/admission.rs` [#4245]。**接口：** T1 的 `GroupRoundRepo::admit(&GroupStartRequest) -> Result<Admission, StoreError>`（异步）；恢复取得原 `RoundKey`，不重分配 Tid。Gateway 只提交可信 scope 与稳定意图。

- [ ] 写 `two_gateways_one_round`：`assert_eq!((new_count, busy_count, executable_starts), (1, 1, 1))`；原胜者重试取原 Tid；前轮完成后输家按原 messageId 新受理。跨群、跨主体和客户端指定未知 Sid 不创建 Session。
- [ ] RED：`cargo test -p bcs-dist-store --test conformance_group_round`，先观察并发或恢复断言失败。
- [ ] 实现 T1 批准的受理协议。若分步写，未获许可的 Inbox 项不得运行；许可和重新调度义务一起保存。覆盖 worker 已 settle 后才发布许可、abandon 与迟到 append、每一步写成功但丢响应。记录重复物理输入与唯一业务轮次，不要求流中只有一条记录。
- [ ] `prepared_snapshot_is_not_a_public_task`：在 producer 已写初始快照和列表索引、尚未授予许可处停机。T2 为 T8 提供按 accepted cut 的读过滤；猜中 Tid 的 Get/subscribe/cancel 与未受理资源一致，List/total 不含它。覆盖 abandon 后重启仍不可见、成功恢复只显现一次，以及 REST 忙 Session 遭旧 RPC 提交不产生任何物理写入。
- [ ] GREEN：重跑该目标及 `cargo test -p bcs-dist-store --test admission`；对实际 provider 执行同一 harness，验证逐次故障后不会留下永久忙或未通知的胜者。
- [ ] 提交 `feat(bcs): admit one recoverable FSM round per context`。

### T3：持久化节点自己的历史，而不是 Session 群聊历史

**文件：** 新增 `crates/service-api/bcs-service-api/src/port/repo/distributed/node_history.rs`、`crates/services/bcs-dist-store/src/node_history.rs`、`crates/services/bcs-dist-store/tests/conformance_node_history.rs`；在 `migrations/{sqlite,mysql}/` 各加下一可用编号的 `fsm_node_history.sql`，在 `crates/services/bcs-db-migrations/src/lib.rs` 对应现行机制登记。**接口：** `NodeHistoryKey(scope, group, sid, workflow_ref, node_id)`；`append_once(key, interaction_id, entry)`，`read_at(key, cut, budget) -> HistoryPage { entries, truncated }`；两个方法为 repo 异步读写，失败返回 `StoreError`。`cut` 取受理时已提交的历史边界，取 cut 与保护该历史版本必须同一一致性操作。新增 `freeze_node_input(round, node, cut, budget) -> PinnedHistory`，持久记录选中条目/内容的不可变引用、有序 digest 和裁剪标志，后续派发只加载已验证的冻结版本；物化完成前保留版本 pin，终态且无可重放派发后才释放输入 pin。

- [ ] 写 `same_bot_two_nodes_remain_private`：研究和评审都绑定 Bot X，分别写入私有标记；`assert!(!review_history.contains(research_marker))`；新 Sid 为空。另测失败/取消轮中真实交互带所属轮次状态，不伪装成功 Artifact；重试相同交互只记一次。
- [ ] RED：`cargo test -p bcs-dist-store --test conformance_node_history`，旧的共享历史或按 Bot 键实现必须失败。
- [ ] 保存实际发送给节点的输入和它的实际响应，区分未送达/未知；用稳定交互身份去重，轮次终态通过可验证引用或一致索引关联。显式上游绑定作为**本轮输入**，不开放跨节点历史遍历。按固定 cut 批量加载有界历史；裁剪后 `truncated=true` 而非新历史。接管后不得读入同轮重试刚写的记录。
- [ ] `trim_during_takeover_replays_exact_input`：选定两条历史，在派发提交前崩溃；并发执行 retention 和接管。比较字节、顺序、digest、`truncated` 均不变，后续新轮可见裁剪。冻结阶段异常不能退回用同一 cut 裸查。
- [ ] GREEN：运行该目标、`cargo test -p bcs-db-migrations` 及 SQLite/真实 MySQL 的迁移升级和 repo harness。归档/历史迁移在首次提交前协调编号，不修改历史文件。
- [ ] 提交 `feat(bcs): isolate persisted interaction history by FSM node`。

### T4：按 Task 归档最终产物，不让未提交的结果提前可见

**文件：** `crates/service-api/bcs-service-api/src/port/repo/distributed/result.rs`、`crates/services/bcs-dist-store/src/archive/sql.rs` [#4245]；新增 `crates/services/bcs-dist-store/tests/group_artifact_archive.rs`、两种方言的 `fsm_task_artifacts.sql` 增量迁移，复用原归档表/DB plugin。**接口：** 扩展 `ResultStorePort::save_final` 的版本化 `FinalTaskRecord`：完整公开历史、稳定 result digest、保序 Artifact/Parts 和 `RoundKey`；产物唯一键 `(tid, artifact_id)`。归档成功不自动获得公开资格。

- [ ] 写 `archive_all_parts_or_none`、`old_tid_not_overwritten`：第二个产物写失败后不出现半份归档；同内容重放得到相同 ID/顺序，异内容冲突；A、B 两轮的文本/JSON 按各自 Tid 查询。
- [ ] RED：`cargo test -p bcs-dist-store --test group_artifact_archive`；先验证无事务或按 Session 更新会失败。
- [ ] 用同一事务写最终记录、产物和不可变 Blob 引用；按 scope/group/Tid 读，列表先分页再批量取产物；准备中的归档只供恢复。实现独立的过期标记和幂等回收：pin 在写对象前登记，未决/隔离或仍被引用不删；对象读取从 IO 开始限长，不能先全量解码。区分 Artifact 已过期、未授权、历史裁剪。授权校验先于披露过期。
- [ ] 和 T8 冻结单一版本的公开投影编码/大小校验合同（wire 类型仍在 adapter，由宿主注入纯校验器）。T4/T7 在提交成功前限长编码完整默认 GetTask 及必需状态/Artifact SSE 帧，包含外壳、转义、history 和所有 Part；保存预算版本、投影摘要和已校验的大小。`blob_fits_but_wire_overflows`：裸 Blob 小于 B，最终 JSON 为 B−1/B/B+1，前两者可读；B+1 只能进入有界失败收尾，绝不公开 Completed。
- [ ] 给 T7 的 `TerminalProof` 加按 Task 的持久读/幂等转存：作用域与 `Tid/generation/commit_id/status/result_ref/digest` 都一致才确认。未提交归档无证明；压缩前须确认等价冷证明可读。`compacted_proof_survives_hot_expiry`：A 归档/提交，B 推进、压缩、清热快照并重启，仍可查 A；对照仅归档未提交的 A′ 始终不可见为成功。清理时先保证 expired 墓碑可读再删除证明和结果，不能出现可重新执行的间隙。
- [ ] GREEN：同一目标覆盖 Blob put 后、事务前宕机，删除回复丢失，活动引用保留；运行与 T3 相同的双方言升级验证。限额采用 T8 选定 profile，测试用显式小预算覆盖 `B-1/B/B+1`，不把测试值作为默认配置。
- [ ] 提交 `feat(bcs): archive immutable FSM results per task`。

### T5：共享能力校验与确定性 FSM Runner

**文件：** 新增 `crates/services/bcs-dist-runner-fsm/{Cargo.toml,src/lib.rs,src/validator.rs,src/reducer.rs,tests/fsm_round.rs}`；共享 validator 的 trait 属于 `crates/service-api/bcs-service-api/src/core/dist_runner.rs`，供群目录和 Runner 复用。参考 `crates/services/bcs-collaboration-runtime/src/definition.rs` 的最终节点规则，不嵌入旧 runtime 的网络/数据库调用。**接口：** `validate(frozen: &SessionSpec, workflow: &StateMachineDefinition, supported: &FsmCapabilities) -> Result<ValidatedWorkflow, RunnerError>`；复用 `DistRunnerCoreService::{init,reduce}`，T1 的 typed Start、固定 `now` 和已解析节点输入进入 `EvaluationContext`。

- [ ] 写 `fork_join_uses_designated_final`：交换两分支的回包顺序，最终产物仍取显式 `final_output`；相同状态和有序输入重放满足 `assert_eq!(decision1, decision2)`。缺失唯一输出、不支持节点拒绝发布且不创建 Task。旧 context 重放仍用原 workflow/binding，新 context 才用新版。
- [ ] RED：`cargo test -p bcs-dist-runner-fsm --test fsm_round`。先注册最小测试目标，再观察上述行为失败。
- [ ] 实现冻结校验、串行/扇出汇合/有界重试、稳定节点 attempt 身份与输出模式选择。纯归约只产出派发/取消/完成意图；并行 A 失败而 B 在途时不输出整轮终态，旧 attempt 未核实时不能借新 attempt 释放占用。
- [ ] GREEN：重跑目标，追加失败/取消竞态和含 Judge/HumanInput 的拒绝测试。将共享 trait 的真实实现接入 R25，而非在 Gateway 重写节点白名单。
- [ ] 提交 `feat(bcs): reduce frozen FSM workflows into recoverable actions`。

### T6：下游会话也按节点隔离，送达未知不盲发

**文件：** 扩展 `crates/service-api/bcs-service-api/src/port/dist_agent.rs`；对齐 [#4133] 的 `crates/external-clients/bcs-a2a-http/src/dist_agent/mod.rs` 和 `crates/services/bcs-dist-connector/src/lib.rs`；新增 `crates/services/bcs-dist-connector/tests/group_node_delivery.rs`。**接口：** 沿用 `DistAgentClientPort::{start,query,cancel}`；`StableSendRequest` 携带节点身份、原 attempt 及该节点的冻结历史输入。将可用停止事实映射到 Worker 输入，而非由 Connector 决定群是否完成。

- [ ] 写 `downstream_accept_then_drop_reply` 和 `same_bot_distinct_node_conversations`：可控 Bot 接单后丢回复；不可查且未协商持久去重时 `assert_eq!(send_attempts, 1)`，不假报停止；同 Bot 两节点不能复用导致串读的远端会话。明确 BCS 提供的历史和远端续聊谁负责上下文，禁止两份历史重复拼接。
- [ ] RED：在 Connector 接口到位后 `cargo test -p bcs-dist-connector --test group_node_delivery`，确认从简单重发或共享会话起步的失败。
- [ ] 用 `proven_unsent`、`dedup_valid`、`query_only`、`unresolved` 四种证据驱动 Connector。前两种在授权后按原身份重发；第三种只复查原调用（先 NotFound 后出现时只发送过一次）；最后一种持久监督，不能伪造停止或自动重启节点。注册真实 stop 证据与审计，缺 provider 时不启用能力。
- [ ] `revocation_between_enqueue_and_dispatch`：发出/重发/continue 前重新核实当前成员资格、委托、目标凭据权限；未获授权不发新输入/历史。区分普通换绑和撤权，查询/取消已派发任务仅在有单独控制权限时可用。`remote_global_memory_ignores_conversation_id`：让测试 Bot 在两个不同 conversation 下共享全局记忆，能力检查必须拒绝；仅经目标部署验证的隔离记忆域或禁用共享记忆的模式可发布。T5/T8 使用同一能力证据，按 adapter/配置版本失效；T9 在实际下游执行标记负测。
- [ ] GREEN：重跑该目标。分别记录逻辑轮次、发送尝试和下游实际执行次数，不用其中一项冒充另外两项。
- [ ] 提交 `feat(bcs): correlate FSM node delivery and stop evidence`。

### T7：Worker 提交权威终态并立即允许下一轮

**文件：** 接入 [#4245] 的 `crates/services/bcs-dist-worker/src/processor/decision.rs`、同目录的 `transitions.rs` 和 `effects.rs`；新增 `crates/services/bcs-dist-worker/tests/group_round_recovery.rs`；扩展 `crates/services/bcs-dist-gateway-store/src/task_reader.rs`。**接口：** `commit_terminal(round: &RoundKey, proof: TerminalProof) -> Result<CommitSeq, StoreError>` 接入现有受 owner/session fence 保护的提交与 outbox；公开 get/list/subscribe 必须按相同权威 cut 决定能否展示归档，不能仅凭归档行存在或旧 snapshot 仍为 Working 裁决。

- [ ] 写 `archive_visible_only_at_commit_and_next_round_not_waiting_for_sse`：先暂停在归档后、提交前，A 不可见为成功，B 忙；放行提交但暂停 SSE，`assert_eq!(get(a).artifacts, expected)` 且 B 立即获准；恢复旧 SSE 不改 B 的占用。两个窗口各杀一次 Worker，A 的 Run 不重跑。
- [ ] RED：`cargo test -p bcs-dist-worker --test group_round_recovery`，确认旧的异步释放或 archive fallback 不满足可见性。
- [ ] 从 T3 的 `PinnedHistory` 核验并装入本节点已冻结的输入，执行 T5 的决定、T6 的派发，用 T4 固定归档；在同一权威版本下提交 FSM 终态和可接下一轮的事实，迟到旧写须带精确代次、被栅栏拒绝。读路径能从提交证明找到原归档，即使热快照滞后。SSE 是跟随者，不是放行条件。失败/取消只接受所有未决 attempt 已停止的证明；丢失通知、租约到期不算证明。
- [ ] 把 Runner Finish 和 [prepare.rs](https://github.com/inclusionAI/Avernet/blob/1599541c8282500ea2d08278a6fa8478b6deb108/apps/bcs/crates/services/bcs-dist-worker/src/processor/prepare.rs) 的所有独立入口接到同一停止屏障。包括 `TaskDeadlineExceeded/TaskDeadlineOutOfRange`、`TaskStepLimitExceeded`、`CanceledByUser`、`OutputQuotaExceeded`、容量拒绝和风险/隔离；同时修改 `processor/lifecycle.rs` 的 `finalize_independently` 及恢复回放。未受理容量拒绝不可变成公开 Task；已受理而没有 dispatch 的分支也须从持久记录证明为空。其余先冻结意图、停止新派发、登记取消，再由统一提交检查全部 attempt 的停止证明；可疑 checkpoint 不得凭猜测生成空集合。`deadline_or_step_limit_with_live_sibling` 对 deadline/步数上限分别断言：未停无终态、无释放，停止确认后仅一个终态。取消与正常完成竞态同理。
- [ ] 修改 [drive.rs](https://github.com/inclusionAI/Avernet/blob/1599541c8282500ea2d08278a6fa8478b6deb108/apps/bcs/crates/services/bcs-dist-worker/src/processor/drive.rs) 和 `lifecycle.rs::retire_published` 对 FSM 来源的调度条件：从阻塞 session 的 outbox 中拆出持久 feed 发布义务，与权威提交同切面登记；独立有界 publisher 按稳定 ID 顺序重试，已有事项不能因 A 退役或 checkpoint 压缩被清掉。`feed_append_failure_does_not_starve_next_round`：共享 feed 连续拒写（非客户端断开），A 可查、B **实际派发首节点**；接管后恢复 feed，检查 A 产物先于终态，重放无重复公开事件。业务派发/停止证明/归档等必要效果不得一并跳过。
- [ ] 异常公开状态与 T8 联测：超过停止核实期限时保存独立 `Quarantined`，不越过终态门槛；A 的 Get/同请求重试为原 Task `WORKING/STOP_UNCONFIRMED`，新的或重连订阅发送此快照后关闭（非终态），活动订阅同样有界退出，不能无期限依赖失败的 feed；新请求 `400 CONTEXT_UNAVAILABLE`。重启与持续未知重复这些断言，再注入真实停止证明完成收尾。
- [ ] GREEN：重跑该目标，覆盖同 Sid 多轮、失败历史、Unknown/崩溃、旧 owner 迟到和归档冲突。关闭轮次后有界压缩 checkpoint，保留拒绝旧事件的 watermark、已验证的按 Tid 冷证明和所有未完成义务；连续多轮的 checkpoint 不随已归档历史线性增长。
- [ ] 提交 `feat(bcs): commit FSM terminal state independently of SSE delivery`。

### T8：新增群 HTTP+JSON、Card、查询和订阅，不改旧绑定

**文件：** `crates/application/bcs-app-dist-gateway/src/{lib.rs,submit.rs,wait.rs}`、`crates/adapters/http/bcs-dist-gateway-http/src/{lib.rs,card.rs}`；新增同一 adapter 的 `src/group_rest.rs`、`tests/group_rest.rs`。在 `crates/services/bcs-dist-gateway-store/src/{page.rs,task_reader.rs}` 实现按群查询的索引读，不把当前页长度当作总数。**接口：** 扩展 `DistGatewayService` 的群调用入口，显式传可信 caller、GroupId 和规范化请求；读取 Card 调独立只读 application 边界。协议和 SSE 帧不进入 Runner/store。

- [ ] 写 `rest_contract_preserves_rpc`：`POST /api/v1/collaboration/a2a/groups/{group_id}/message:send` 并发败者 HTTP 409；阻塞 send 等待超限 504 而 Task 继续；断开 stream 不取消。终态 get 含最终节点 Artifact。用假认证链证明跨群/主体和恶意自报 metadata 不能读取任务；已有 JSON-RPC 套件保持通过。
- [ ] RED：`cargo test -p bcs-dist-gateway-http --test group_rest`，预期缺路由或返回码不符。
- [ ] 实现 `agent-card.json`、`message:send`、`message:stream`、`GET tasks/{id}`、`GET tasks` 和 `/tasks/{id}:subscribe` / `:cancel`。按 #3979 在同一动态 POST 路径分派动作；先查群/主体归属再返回资源。Get/List/SSE 使用相同公开投影；保留有序 Part 属性。List 返回过滤后实际总数/有效页大小，批量加载可选产物；订阅恢复到已授权快照，不承诺逐事件无损。校验显式发布、权限撤销行为、输入/输出模式、扩展和等待预算，未获批能力不在 Card 宣告。
- [ ] `rpc_cannot_write_rest_context_and_vice_versa`：在 `submit.rs`、cancel 和共享读入口校验持久的创建来源，不以 caller/group 相同代替来源校验；隔离新来源不改变旧资源行为。测试无 context 同 messageId 从两个入口建会话、跨入口追加/取消/查询/订阅、REST 准备后宕机的记录不进公开列表或 total，验证 Inbox 无新增写。Task 终态、隔离状态和 Accepted 可见性均从权威记录恢复而非裸快照。
- [ ] `list_pages_share_cut_across_hot_to_archive`：T1/T4 共建仅含已受理资源的版本化公共索引，首次查询取得有界的一致读版本，不能为异步索引滞后伪造当前 cut；按 Tid 唯一。total、排序键 `(status_updated_at DESC, tid ASC)`、筛选与行投影都读同版本。新游标签名并绑定 scope/group、筛选、cut、末键、期限；后页重验权限，过期/版本不可读则明确拒绝，不回退到实时查询。页间热转冷、并发新增和状态更新后，跨页无重复/漏项、total 不变；游标篡改、跨主体、撤权、过期均不可继续读。保留投影版本/引用覆盖有效游标期或显式使游标失效；记录有界扫描、索引计数和页内批量 IO 成本。
- [ ] GREEN：重跑新增目标及 `cargo test -p bcs-dist-gateway-http --test a2a`；检查终态与订阅竞态、削权后的旧游标、过期读取、超大列表/单帧、取消响应丢失后的幂等恢复。未决定的权限不能以匿名回退通过测试。
- [ ] 提交 `feat(bcs): expose published FSM groups over A2A HTTP and SSE`。

### T9：装配、注入故障、记录可上线范围

**文件：** `crates/bootstrap/bcs/src/server/server_run.rs`、新增 `crates/bootstrap/bcs/src/server/group_a2a_wiring.rs`、`crates/bootstrap/bcs/tests/group_a2a_companion.rs`。在 `specs/2026-11-20-fsm-group-a2a-discussion/validation.md` 记录实际角色、provider、命令、失败/跳过项。**接口：** composition root 注入真实群目录、认证、Worker、Runner、Connector、结果与节点历史 provider；不向 handler 暴露这些 repo。

- [ ] 写 `published_two_node_group_survives_takeover`：研究和评审各跑两轮，重复请求、忙时重发、失败/取消仍占用、跨轮历史隔离、Artifact 按旧 Tid 查询；关闭入口时确认既有路由不受影响。测试下游可控，核心角色不允许 Script 替身。
- [ ] RED：`cargo test -p bcs --test group_a2a_companion`；未挂载/无真实角色时功能场景不得通过。
- [ ] 先做显式 opt-in 的同进程装配，再用 Singlebox companion 的独立进程及共享持久 provider 执行 T2/T6/T7 的崩溃矩阵，覆盖停 Gateway、Worker、Connector、provider 重启；不得将多个进程各自的内存模型当作共享存储。
- [ ] GREEN：重跑 companion、受影响 crate、双方言升级、`cargo check --workspace --all-targets` 和两项 API/R25 门禁。记录受理/结果未知路径最大存储调用数、分页与恢复扫描有界性、故障退避和 retention/GC 负担；生产 provider 缺失、停止证明未签字、权限矩阵不全时保留关闭状态，不以本地 GREEN 替代上线验收。
- [ ] 提交 `test(bcs): verify FSM group invocation across process recovery`。

## 执行边界

本文不批准 Runtime PR、协议默认值、数据迁移或部署。每个任务的命令和断言是**未来验收**；本次生成计划没有运行这些测试。T1 后 T3/T5 和 T8 的协议测试可并行；T2/T4 须先与在审分支协调，T7 和真实 E2E 不绕过依赖。
