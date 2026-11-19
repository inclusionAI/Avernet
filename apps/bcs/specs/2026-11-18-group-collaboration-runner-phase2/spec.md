---
status: proposed
date: 2026-11-18
updated: 2026-11-19
owner: BCS collaboration Runner
---

# Avernet / BCS 第二批：真正的群协作 Runner SPEC v1

## 1. 目标、状态与非目标

本稿定义可用于生产装配的群协作 Runner，使 Worker 能执行现有结构化群工作流，处理节点结果、分支、循环、人工交互和业务结束。交付状态为 `proposed`；本次只有源码调研、设计和文档，不修改 Rust、配置或数据库。下文的类型变化、实施切片和测试均是后续实现要求，不是已完成记录；文档送审不代表实现或部署。

三批边界：第一批负责通用 Worker 的受理、串行计算、checkpoint/outbox、dispatch 管理、投影、归档和恢复；本批负责真实群协作业务；第三批负责长期保留、GC、容量治理和调优。本批保留必要的步数、循环、状态大小和无进展保护，不开展长期治理。

Runner 的模型为：

```text
冻结 SessionSpec + 精确版本的工作流与编译产物
+ 可序列化 RunnerState + 有序规范输入 + 显式 now
→ Decision(next_state, action intents, next_deadlines, input dispositions)
```

`input dispositions` 是本稿建议的最小补充，用于区别业务接受、陈旧忽略与拒绝；当前 `Decision` 没有此字段。Runner 不访问存储、网络、系统时钟或随机源，不依赖 owner/holder 变化决定业务。相同上述输入必须得到相同决策。

不包含：完整 Gateway/Connector、全部远端协议、普通 Chat/ManagerWorker 的重新实现、任意图语言、动态改图、任意循环、任意并行汇合、存量运行中会话在线迁移、自动补偿、长期资源治理、生产吞吐承诺。对现有群入口、通知和结果投递的兼容以第 4、10 节的共存边界为准。

## 2. 调研基线与现状证据

### 2.1 版本与材料状态

调研日期为 2026-11-18，公共工作树 HEAD 为 `1d5c65d87866597c31b7c290d1d54577d68d4a0d`，调研开始和写稿前工作树均干净。其前两个提交只修改第一批文档，第一批记录的代码基线为 `7358ad11c314c7c221f1f66630f0aaec89e92997`。本稿以指定工作树为源码依据，不把其他 checkout、默认分支或运行实例视为同版本。

2026-11-19评审复核基线为本稿首次送审提交 `c774253bd776ce52f9f5fe9901c68a4889d1b204`，其父提交为GitHub `dev` 的 `4b17dacc62eef0ddd34310cce5eb5eac166c3da6`。下表补记已在该基线中的协调实现，E16/E17为本次补充源码证据；保留上述原调研记录，不把源码实现、提案和生产验收合并成一种状态。

| 依据 | 核对结果与使用方式 |
| --- | --- |
| [第一批 Worker SPEC（原调研版本）](https://github.com/inclusionAI/Avernet/blob/1d5c65d87866597c31b7c290d1d54577d68d4a0d/apps/bcs/specs/2026-11-18-worker-runtime-phase1/spec.md) | `proposed`；包括 artifact Upsert/AppendText、两阶段终态归档、隔离保留已冻结结果等拟议契约，均不是已实现保证。2026-11-19送审核对 [PR #4144](https://github.com/inclusionAI/Avernet/pull/4144) 仍未合入；[最新固定修订](https://github.com/inclusionAI/Avernet/blob/8811efbc9751004747451d00c5ea1e6a30a6b224/apps/bcs/specs/2026-11-18-worker-runtime-phase1/spec.md) 新增的checkpoint/output预算与任务独立终结约束按§5.3、§9.3衔接 |
| [分布式契约 SPEC](../2026-11-15-distributed-contracts/spec.md) | additive declarations；接口和模型已有声明，不代表 Worker、Connector 或真实 Runner 已装配 |
| [PR #4126](https://github.com/inclusionAI/Avernet/pull/4126) | 首次读取时 open、非 draft；本轮于 2026-11-18 10:50:33（Asia/Shanghai）合入，merge 为 `ca4ab19c213a57997f465cbb60f25f730786324d`，head 为 `220c99949bfa56eb76221ffe7078f352d84d1e8b`；[合入版本的协调 SPEC](https://github.com/inclusionAI/Avernet/blob/ca4ab19c213a57997f465cbb60f25f730786324d/apps/bcs/specs/2026-11-17-worker-coordination/spec.md) 仍为 proposed。该 PR 仅修改文档；合入不代表运行能力已实现，指定工作树未切换到 merge |
| [PR #4145](https://github.com/inclusionAI/Avernet/pull/4145) | 2026-11-18 23:00:22（Asia/Shanghai）合入，merge `3a925fd4742520abb2377a015bb23a1a73a6e71f` 已是本稿送审基线的祖先；提供 DocumentCas/StrictLease 能力契约、可复用验收套件、测试内存模型和公共 Worker 协调运行时，见[实现记录](../2026-11-17-worker-coordination/implementation.md)及E16/E18。生产provider须单独实现并通过对应套件；完整业务processor仍依赖第一批后续实现，不等于生产存储就绪、#4144闭环或生产Runner已完成 |
| [分布式业务端口](../../crates/service-api/bcs-service-api/src/port/repo/distributed/README.md) | 声明持久受理、checkpoint/outbox、稳定身份、远端 Unknown 与取消确认的边界；本批复用，不另建基础设施 |
| 架构与知识规范 | 遵守 [架构约束](../../../../docs/arch/arch.rules.md)、[CI 规范](../../../../docs/arch/ci.enforce.md)、[知识标准](../../../../docs/agents/knowledge-standard.md)、[BCS 规则](../../AGENTS.md)与 [ADR 导航](../../docs/adr/README.md)；不以本提案替换已接受契约 |

### 2.2 结论对应的实现与测试

下表是已阅读的源码与测试证据，不表示本次运行过这些测试。符号和测试名用于固定结论，路径均相对于本 SPEC。

| 编号 | 结论 | 源码 / 测试证据 |
| --- | --- | --- |
| E01 | 分布式 Runner 是同步纯计算 trait；只有 Start、HumanReply、Cancel、Agent、Timeout；尚无群实现 | [dist_runner.rs](../../crates/service-api/bcs-service-api/src/core/dist_runner.rs)：`DistRunnerCoreService`、`EvaluationContext`、`Decision`；检索其 impl 仅发现 [test-support 的 Script](../../crates/test-support/bcs-test-support/tests/distributed_harness/services.rs) |
| E02 | 冻结 spec 有 revision/digest、逐节点 Agent binding 和基础 limits；RunnerState 只有版本与 JSON | [session.rs](../../crates/contracts/bcs-dist-model/src/session.rs)、[runner.rs](../../crates/contracts/bcs-dist-model/src/runner.rs)；[模型上下文](../../crates/contracts/bcs-dist-model/CONTEXT.md)明确尚非部署格式 |
| E03 | 枚举比真实执行能力宽；v1 实际只接受 acyclic、bot_task/human_input、唯一入口和终点，人工节点必须存在依赖顺序 | [definition.rs](../../crates/services/bcs-collaboration-runtime/src/definition.rs)：`validate_definition`、`ensure_human_inputs_are_ordered`；[definition_validation.rs](../../crates/services/bcs-collaboration-runtime/tests/definition_validation.rs) |
| E04 | complete 可 fan-out，汇合等待所有上游 Completed/Skipped；Judge 只选已声明 outcome，共享汇合不能随未选分支被跳过 | [runtime/progression.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/progression.rs)、[progression.rs](../../crates/services/bcs-collaboration-runtime/src/progression.rs)：`derive`；[graph_and_judge.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/graph_and_judge.rs)：`complete_transitions_support_fan_out_and_implicit_all_join`、`judged_node_keeps_shared_merge_reachable_from_selected_branch` |
| E05 | v2 hierarchical 固定循环预展开为 v1 DAG；单层/嵌套分别使用 compiler/v1、compiler/v2，ID 与完整循环路径确定相关 | [fixed_loop.rs](../../crates/services/bcs-collaboration-runtime/src/fixed_loop.rs)：`compile_definition`、`execution_node_id`；[fixed_loop_compiler.rs](../../crates/services/bcs-collaboration-runtime/tests/fixed_loop_compiler.rs)：`compiler_is_byte_stable_and_snapshot_round_trip_preserves_metadata`、`parent_child_and_parallel_sibling_loops_with_repeated_local_ids_are_distinct` |
| E06 | continue 边为 ControlOnly；break/exhausted 投影选中的结果；previous_result 来自同父实例的上一轮 result_node | [loop_context.rs](../../crates/services/bcs-collaboration-runtime/src/loop_context.rs)：`build_loop_context`、`projects_upstream_artifact`；[循环运行测试](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/fixed_loop.rs)：`parallel_body_join_and_next_iteration_use_the_designated_result`、`parallel_nested_child_loops_join_only_after_both_selected_exits` |
| E07 | 恢复使用已保存计划，校验 hash、编译器版本和映射；不会拿当前限制重编译旧计划 | [snapshot.rs](../../crates/services/bcs-collaboration-runtime/src/snapshot.rs)：`load_state_machine_snapshot`；[fixed_loop_snapshot.rs](../../crates/services/bcs-collaboration-runtime/tests/fixed_loop_snapshot.rs)、[循环运行测试](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/fixed_loop.rs)：`nested_rerun_reuses_compiler_v2_snapshot_despite_tighter_current_limits` |
| E08 | Final 不等于任意输出；空可见最终文本走失败/重试，旧 run/node/attempt/delivery/assignee 不能覆盖当前尝试 | [runtime/terminal_event.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/terminal_event.rs)：`handle_bot_terminal_event_impl`、`extract_text`；[failure_recovery.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/failure_recovery.rs)、[循环运行测试](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/fixed_loop.rs)：`retry_keeps_iteration_and_context_and_stale_completion_cannot_overwrite_previous_result` |
| E09 | Judge 是独立外部求值，Agent/Human 文本先固定，再求 outcome；不是解析文本为控制命令 | [runtime_judge.rs](../../crates/services/bcs-collaboration-runtime/src/runtime_judge.rs)、[runtime/node_execution.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/node_execution.rs)：`evaluate_node_outcome`；[JudgeEvaluatorPort](../../crates/service-api/bcs-service-api/src/port/judge.rs)；[judge_recovery.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/judge_recovery.rs) |
| E10 | human_input 不发 Bot dispatch，使用自然语言；有固定 deadline，过期/重复拒绝，可继续 Judge；人工超时不受全局重试次数影响 | [runtime/human_input.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/human_input.rs)；[human_input_response.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/human_input_response.rs)、[timeouts.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/timeouts.rs) |
| E11 | Bot 超时可直接重试同轮次的新 attempt；当前路径不等待远端取消确认；确定派发拒绝直接 FailRun | [runtime/recovery.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/recovery.rs)：`process_expired_node_timeout_candidate`；[runtime/progression.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/progression.rs)：`resume_failed_node`；[循环运行测试](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/fixed_loop.rs)：`timeout_retries_same_bot_iteration_and_fails_human_without_advancing`；[failure_recovery.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/failure_recovery.rs)：`rejected_dispatch_stays_fatal_even_with_remaining_attempts` |
| E12 | 全图 Completed/Skipped 才完成；优先 final_output，无 final_output 时按 node_id 降序找首个已有 artifact，并非一定取 terminal Human 的回复 | [runtime/completion.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs)：`complete_run_if_done`、`final_output_text`；[memory_runs.rs](../../crates/services/bcs-collaboration-store/src/memory_runs.rs)：`list_node_runs` 和 [sql_node_state.rs](../../crates/services/bcs-collaboration-store/src/sql_node_state.rs)：`sql_list_node_runs` 均按 node_id 升序返回；[completion.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/completion.rs) |
| E13 | 普通群 routing、权限和 producer facts 有自己的冻结边界，不能全部搬进 Runner | [group_flow/routing.rs](../../crates/services/bcs-message-flow/src/group_flow/routing.rs)、[output_facts.rs](../../crates/services/bcs-message-flow/src/group_flow/output_facts.rs)、[message-flow 上下文](../../crates/services/bcs-message-flow/CONTEXT.md) |
| E14 | one-shot 最终群消息有稳定 publication 身份、StateMachine/Public 分类及原始时间；混合投递歧义会失败，不能绕过准入发布 | [group_flow/state_machine_result.rs](../../crates/services/bcs-message-flow/src/group_flow/state_machine_result.rs)：`ResultMessage::validate_admission`、`publish`；[result_publication.rs](../../crates/services/bcs-collaboration-runtime/tests/runtime_progression/result_publication.rs) |
| E15 | 中间节点历史并非全部 Public：Bot 为 FullOnly，Human 为 Directed；projection 字段存在不等于已实现通用可见性语言 | [runtime_message_history.rs](../../crates/services/bcs-collaboration-runtime/src/runtime_message_history.rs)：`output_payload` 中的 audience 构造；[runtime/events.rs](../../crates/services/bcs-collaboration-runtime/src/runtime/events.rs) |
| E16 | 已有协调运行时管理ready/owner、恢复、轮次和调度生命周期；宿主processor须确认恢复、checkpoint和effects，不负责工作流业务算法 | [Worker上下文](../../crates/services/bcs-dist-worker/CONTEXT.md)、[runtime.rs](../../crates/services/bcs-dist-worker/src/runtime.rs)：`acquired_sessions`、`recovery_completed`、`begin_round`、`checkpoint_confirmed`、`effects_completed`、`round_unknown`、`scheduling_started`、`scheduling_completed` |
| E17 | 现有协同控制不是“任意结构化Tool”：校验活跃run、有效期、Bot/群/Session和tool-call身份，claim已存intent后使用其参数；claim后再次校验run | [coordination.rs](../../crates/services/bcs-message-flow/src/bot_event/coordination.rs)：`claim_and_dispatch`；[intent端口](../../crates/service-api/bcs-service-api/src/port/coordination_intent.rs)；[Tool身份测试](../../crates/services/bcs-message-flow/tests/cases/contract_coordination_tool_identity.rs)、[chat-final测试](../../crates/services/bcs-message-flow/tests/cases/contract_coordination_chat_final.rs)。无name/toolName的BaaS结果fixture仍须兼容 |
| E18 | 协调存储构造强制要求DocumentCas、StrictLease、ZSet；StrictLease过期/退休后保留Terminated，迟到注册不得复活；测试模型通过不等于生产provider就绪 | [coordination/mod.rs](../../crates/services/bcs-dist-store/src/coordination/mod.rs)：`CoordinationStore::with_config`；[lease.rs](../../crates/plugin-api/bcs-store-api/src/lease.rs)：`LeaseState`、`LeaseStorePlugin`；验收套件：[document](../../crates/test-support/bcs-test-support/src/contract/plugin/store/document.rs)、[lease](../../crates/test-support/bcs-test-support/src/contract/plugin/store/lease.rs)、[zset](../../crates/test-support/bcs-test-support/src/contract/plugin/store/zset.rs) |

现有 `CollaborationRuntime` 同时做持久化、时钟读取、Judge 和投递；不能把它直接包成 reducer 并宣称纯计算。复用的是验证、编译、映射和业务算法，经纯化后进入生产 Runner。其仓储恢复机制不复制进 RunnerState。

## 3. 职责、设计选择与最小上游补充

### 3.1 分工及选定方案

| 组件 | 本批责任 | 不能承担 |
| --- | --- | --- |
| Runner | 节点激活、outcome 选择验证、汇合、循环结果传递、重试策略、交互关联、业务终止、动作与业务 deadline | IO、投递确认、owner fencing、outbox 回执、远端传输恢复 |
| Worker | 规范输入和顺序、引用解析、注入 now、原子 checkpoint/outbox、动作身份固定和重放、投影与归档、超时输入与远端义务监督 | 根据工作流私自选择 retry/skip/continue，或把 Unknown 改成失败 |
| Connector / 执行侧适配 | Agent 与 Judge 请求执行、协议归一化、远端确定事实、能力约束内恢复、取消/继续输入 | 选择工作流下一节点，改变节点重试策略 |
| admission / application facade | 冻结精确工作流和上下文、权限校验、旧入口与新会话路由、业务回复结果查询 | 读取最新定义替换活跃会话；把外部未验证 actor/控制文本直接交给 Runner |

选择“复用现有编译产物，新增纯群 Runner，与旧路径隔离共存”。直接调用旧运行时会把 IO 和两套持久权威引入 reducer；重写工作流语言会扩大兼容风险，均不采用。新 Runner 拟放在独立服务 crate `bcs-group-runner`，实现现有 Core trait；共享纯编译/快照校验从原 crate 提取到 `bcs-collaboration-plan`，由 composition root 或测试装配，两边不跨服务调用具体实现。

**超时重试由结构化协作自行消化。** 本批兼容 E11：Bot 节点超时且有剩余次数，Runner 可以明确创建新 attempt/did，同时请求取消旧 did，不以旧取消确认为前置条件。旧远端可能继续执行；隔离旧结果不等于撤销其外部副作用。Worker 不为这种新业务尝试增加全局等待策略，也不能在同一 did 发送结果未知时擅自重发。是否以后提供“确认结束后再重试”等业务策略属于另行产品决策，本稿不新增该配置。

### 3.2 接口补充清单

以下均为**本批拟议**，第一批已提议的输出扩展单独注明。实现前在 owning contract 增加类型、兼容说明和 conformance；本次不改声明。

| 边界 / 当前缺口 | 最小补充与归属 | 兼容性与消费者 |
| --- | --- | --- |
| `load_workflow` / `EvaluationContext.workflow` 只有 `StateMachineDefinition`，SessionSpec未冻结Runner选择 | 通用层冻结模式kind、Runner语义版本、配置schema/version/digest及有界配置值/不可变Ref。FSM配置包含 `ResolvedWorkflow`：原定义身份、authoring definition、完整可选 `StateMachineExecutionPlan`、compiler_version、计划hash和envelope digest | Service API / dist-model / directory / composition root；按§3.3选择和解释配置，Worker不解释图。原具体workflow字段迁移到FSM配置，不在通用接口强制所有模式提供StateMachineDefinition |
| SessionSpec 的节点binding与通用执行授权混合，缺少Judge配置 | 通用授权集合保留显式Agent binding、pool/capabilities和Judge executor profile；FSM配置另存节点到授权目标的映射、非敏感群上下文和业务limits。凭据继续仅用引用 | dist-model / directory；Worker验证Agent属于冻结participant binding、Judge属于冻结executor profile，不能将Judge当Bot；Runner不查询live Group/Provider，活跃权限撤销由外层显式控制输入处理 |
| `DispatchSpec.target` 只能是 Agent，关联键耦合NodeId；Judge若放进reducer会做IO | 目标扩为显式 `Agent` / `Judge` 变体；通用关联为有作用域、长度上限的稳定模式关联键及带版本payload，FSM在模式内解释NodeExecutionKey和work/judge目的 | dist-model / Connector；执行侧保留目标/能力/schema校验，不能全部变成无约束字符串。复用DispatchBoard/outbox，通过应用层适配JudgeEvaluatorPort；FSM的NodeTask/NodeJudge不是所有模式必须有的阶段 |
| `AgentEvent::Final/Error` 未规定完整内容、失败分类、取消及Judge结果 | 规定带版本payload和确定事实Completed/Failed/Canceled/Rejected；FSM完成载荷为artifact或JudgeDecision。NotSent/Unknown不自动变成业务失败。共享边界允许显式注册、经身份/授权校验的模式控制事件，经durable admission回流 | Connector→Worker→Runner；事实与模式策略分离，见§6.1。FSM的普通Tool/文本仅展示，不接受协同控制命令；本批不实现ManagerWorker协议 |
| `Interaction` 强制did；HumanReply缺受信actor | 目标为 `ModeLocal(scoped correlation)` / `Dispatch(did, remote interaction)`；携带prompt、deadline、generation和受信actor/作用域授权事实。FSM将ModeLocal解释为原生Human节点实例 | dist-model / facade / Runner / TaskFeed；不创建假dispatch。Worker验证通用身份/授权，Runner核对当前业务关联、受答者和时效 |
| Deadline无发生身份、模式局部关联 | 增加稳定occurrence ID，支持Task、Dispatch及ModeLocal关联；FSM将后者解释为NodeAttempt/Interaction及work/judge/wait阶段。`next_deadlines`仍是完整集合；过早Timeout返回可重驱动的InputNotDue | Runner决定时限；Worker只登记/去重/到期触发，不解释节点阶段或决定retry；详见§6.2 |
| RunnerInput 未保留规范业务身份，无法明确反馈陈旧人工回复 | 有序输入加规范身份包装；durable 输入保留 `input_id`，中间输入使用既有 `(did, holder_epoch, seq)`。`Decision.input_dispositions` 按输入顺序给 Applied / IgnoredStale / Rejected(code) | Service API；不携带 InboxPos。Worker将durable处理结果转成既有outbox中的必要effect，与state/consumed原子提交；中间事件不逐条持久回执 |
| 现有 AdmissionReceipt 仅证明受理，无法查询业务回复是否生效 | 为原canonical admission记录增加一次性处理结果及查询/幂等写入端口；Worker新增 `RecordInputDisposition` effect，携带原身份、business_digest、commit证明和处理结果 | Inbox port / Worker；相同身份与内容重复成功、冲突拒绝。effect确认前不替换outbox。复用原受理记录，不新增Runner队列或去重库；尚未写入时返回Processing，绝不提前答Applied。AdmissionReceipt不承诺业务生效；Applied不承诺远端收到ContinueDispatch，见§6.3第7条及§9.1 |
| Show / OutputBody 无 artifact 身份与操作 | 依赖第一批拟议的 `(node, artifact_id, class)`、Upsert/AppendText、公共纯投影 | **第一批前置补充，当前源码未有**；本批不独立实现另一套投影/快照 |
| 第一批通用数量预算尚未明确未结束dispatch计数、跨Task启动及耗尽收尾 | 补齐Decision动作数、每Task累计dispatch/交互数、每Session未确认结束dispatch数；冻结模式validator计算的dispatch需求上界及capacity_wait_timeout_millis；Worker持久化启动预留、等待截止/发生身份及处理结果。已运行任务正常配额耗尽复用独立终结器 | **第一批最小必要补充，仍为提案**；Worker拥有计数/预留与Queued生命周期，见§9.3–9.4。未启动容量等待到期为Rejected(StartCapacityTimeout)，可解释的执行额度耗尽为Failed(ExecutionQuotaExceeded)；均不在Runner复制账本或另建投递系统 |

上述变更采用新的分布式模型 schema 版本与独立 namespace；当前未部署声明的 Rust struct literals、穷尽 enum match、测试与消费者须一起调整。不能声称增加字段就是源兼容，也不能悄悄把旧 `Show` 解释为 AppendText。旧运行时模型和已冻结 compiler/v1、compiler/v2 计划不改写。

### 3.3 模式选择与通用契约边界

composition root静态注册 `(kind, runner_semantic_version)` 对应的配置decoder/validator和Core工厂，Worker只持有选中的 `DistRunnerCoreService`。本批仅注册FSM；现有 `SessionKind::FsmGroup` 映射到该注册项。未注册模式、版本或配置schema在受理时拒绝；不增加动态插件加载、持久注册中心或测试Script fallback。

SessionSpec在受理时冻结模式kind、Runner语义版本、配置schema/version/digest；这些字段参与spec digest。语义版本描述决策行为，`RunnerState.format_version`描述存储格式，工作流revision/compiler描述定义与编译产物，三者独立；不冻结二进制构建号。模式配置经对应validator校验，解析后注入纯Runner。原 `EvaluationContext.workflow`、`SessionSpec.workflow`/逐节点binding中的FSM内容迁入该配置；通用授权集合保留在Worker可验证的外壳中，不能仅给新增字段改名而留下旧必填图依赖。

Worker把dispatch/deadline/interaction及展示的模式关联键视为不透明的有界身份，校验scope、tid、身份/内容一致性、schema、授权集合和预算；只有FSM解释execution_node_id、attempt、loop_path、work/judge等业务含义。当前 `ActionIntent::Show.node: NodeId` 及相关事件/快照字段也须随新schema迁为模式关联键，第一批拟议的稳定artifact身份和Upsert/AppendText语义保持；FSM仍以execution_node_id赋值。Agent/Judge目标类型、作用域、payload schema和权限保持显式，不因关联键不透明而跳过校验。

未来模式必须声明Session、多轮输入、业务子任务与Worker Task的映射，并验证第一批串行启用、排队、归档和恢复契约；不满足则单独评审上游变化。本稿只规定FSM的Start→FinishTask，并行子工作不等于并行顶层Task；不预设Chat每轮消息就是一个Task，也不承诺任何未来能力都无需演进公共契约。

## 4. 工作流语义与兼容性矩阵

| 能力 | 当前真实行为 | 本批分类与决定 |
| --- | --- | --- |
| v1 acyclic | 一个零入度入口、一个终点，全节点在入口至终点路径上 | **必须兼容**，复用现有校验；不是任意 DAG 的承诺 |
| BotTask | 单一解析 assignee，prompt 带输入、上游结果与 instruction | **必须兼容**，逐执行节点冻结目标；不临时择员 |
| HumanInput | 无 Bot dispatch，自然语言回复；人工节点之间显式依赖；可有 Judge | **必须兼容**，前端/TaskFeed 交互闭环属于本批；原生并行人工节点仍拒绝 |
| 分支及并行 | complete.targets 可扇出；Judge outcome 选一组 targets；隐式 all-join | **必须兼容**；不增加 any/quorum/race 汇合、动态 fan-out |
| 固定与嵌套循环 | v2 hierarchical 经版本化编译预展开；continue/break/exhausted；可串联及并行子循环 | **必须兼容**，直接执行保存的图和 loop_path，禁止另一套运行期栈解释器 |
| 节点重试 | attempt 从 0 开始；Bot max_attempts 至少为 1；Human 固定一次；错误/空结果/Judge 错误可消耗重试 | **必须兼容**；恢复重放同一次 dispatch 不消耗业务重试 |
| 节点超时 | Bot 可直接重试；Human 失败；明确派发拒绝直接整体失败 | **必须兼容**；增加对旧 dispatch 的取消意图及独立监督，不能宣称已取消 |
| 分支跳过 | 未被选择且不再可达的节点 Skipped，不能跳过仍被其他分支需要的 join | **必须兼容**；Skipped 不是“失败后继续”策略 |
| 上游结果 | v1读取全部结构上游已有artifact；带循环计划时按选中Artifact边过滤 | **必须兼容**两条实际路径，不用同一过滤规则静默改变prompt/Judge输入，见 §4.1 |
| 群结果 | 优先唯一 final_output；v1 terminal human_input 图使用E12的确定性回退 | **必须兼容**，终点未完成不成功；保留node_id降序回退，改为始终取Human终点回复须另作业务决策 |
| Agent InputRequired/AuthRequired、流式事件 | 分布式已有类型，尚未形成生产群 Runner 闭环 | **本批补齐**，可有多个不同 dispatch 交互；不等于开放并行原生 HumanInput 图 |
| projection/visibility 字段 | 类型中存在，但上述运行时没有通用表达式解释器；历史 audience 有具体规则 | **保持实际输出边界**，不由字段名推导公开权限；新 TaskFeed 采用受权 task scope，群 Public 仅用于最终结果桥接 |
| 普通群 @mention、driver、ManagerWorker 路由 | message-flow 拥有成员/角色/权限与 Send/观察上下文 | **保留旧路径**；FSM 节点按冻结图派发，Agent 正文中的 @mention 不修改图 |
| one-shot、rerun、开场消息、IM 通知、Caller 凭据接力 | 旧应用服务有各自持久化、权限和投递协议 | **共存**；本批不迁移这些完整入口。新任务重跑用新 tid 与冻结定义；不能承诺旧 run_id API 可直接读取新 RunnerState |
| GroupChat、ToolAction、SubStateMachine 节点；cyclic/event_driven；guard、variables/events、input_schema/output_contract/action | 模型可能声明，但当前 validator 拒绝执行 | **明确不支持**，按现有错误提前拒绝；Tool 事件可展示不等于支持 ToolAction 节点 |
| 改图、换成员影响在途节点、失败跳过/补偿、等待取消后重试、并行原生人工输入 | 无当前兼容契约 | **尚需产品决策，非本批需求**；新版本评审前保持不支持，不留实现时自行选择的 TODO |

### 4.1 推进与汇合

采用 E04 的可达性规则：未完成节点保留所有潜在路径，已完成节点只保留其选定 outcome 的边；从唯一入口计算仍可能到达的集合。只能将集合外的未启动节点标为 Skipped，不能改写 Running、Judging、等待交互或已完成实例。

一个非入口节点须至少有一条已选择的入边，且全部结构上游均为 Completed/Skipped 才 Ready；入口只在 Start 激活一次。上游结果按 execution_node_id 排序、去重后传递，但保留当前两种过滤规则：v1无execution plan时读取所有结构上游已有artifact；v2带plan时仅投影Completed且已选择的Artifact边，ControlOnly只通过LoopContext传递。这一规则同时用于Bot prompt、JudgeRequest和人工交互的上游上下文。

例如v1的 `a(yes→b, no→final), b→final` 选择yes后，final仍收到A+B；不能因a→final未选中而删去A。v2则按冻结metadata选择结果。分支到达次序可不同，不改变各自规则下join的输入集合和排列；实际完成时间可以不同，不要求不同输入轨迹的状态字节完全一致。

### 4.2 循环与结果传递

保留编译器现有的物化节点身份，运行时不解析 `ln-` ID 来猜轮次。完整外到内 `loop_path` 来自校验后的 metadata；compiler/v1 单层 metadata 只在读视图中推导路径，不改旧计划字节和 hash。

continue 且未到上限只激活同父实例的下一轮入口；到上限走 exhausted 的外部 targets，但保留结果节点原始 outcome。break 跳到声明的退出 targets，跳过该循环实例未来不再可达的子树，不影响另一并行循环。循环退出后外层 join 只消费被选中出口的结果。

第一轮 `previous_result=null`；后续轮必须来自该实例的上一轮指定 result_node，包含结果、原始 outcome、completed_at 和执行身份。continue 边为 ControlOnly，上一轮结果仅通过 LoopContext 传一次；不是重复拼入普通 upstream。嵌套 inner 的第一轮不能继承上一 outer 实例中的 inner 结果。重试保留当前 loop_path 与同一份前序结果。

## 5. 冻结模型、身份与 RunnerState

### 5.1 冻结定义

新会话在 admission 冻结定义身份、revision、完整 envelope digest、原定义、编译计划及 hash、节点 binding、Judge profile、prompt 所需的非敏感群上下文和 limits。authoring 中省略的 UUID 等默认值只允许在冻结前补齐，不能在 init/reduce 反序列化时生成。

上述为FSM模式配置的内容；外层同时冻结§3.3的模式选择与配置版本。Worker先校验外壳和授权集合，再由注册的FSM validator验证工作流。改变最新注册默认值不能改变已冻结选择。

`WorkflowRef.revision` 是不可变版本，不等同节点语言 version；`workflow.digest` 覆盖整个工作流 envelope，既有 plan hash 仍按现有 `execution_plan_content_hash` 校验。新 envelope 采用带编码版本的规范 JSON：对象键递归排序，数组保留定义顺序，UTF-8、无空白；digest 使用 SHA-256。SessionSpec.digest 同样排除自身摘要字段后计算，覆盖冻结上下文和 binding。目录返回错误 revision、未知 compiler、缺映射或 digest 不匹配时不调用 reducer。

恢复读精确 revision，不重新 resolve 当前群、不用新编译器重编译、不用缩紧后的 admission limits 改变已受理图。Worker先解析、校验不可变 Payload::Ref，再把可用值注入；Runner 不读取 Blob。v1 定义继续走相同结构验证，v2 必须同时具备 authoring 与计划；仅保存展开后的 StateMachineDefinition 不足以恢复。

### 5.2 身份

下表是FSM的确定性身份规则；共享契约只规定稳定性、作用域隔离、长度上限和同身份内容一致性，不要求其他模式使用节点或此哈希元组。

| 身份 | 规则 |
| --- | --- |
| 逻辑节点 | 定义中的 node 路径；不能单独用作结果关联键 |
| 执行节点 | 精确计划中的 execution_node_id；循环不同轮次、不同父路径已是不同节点 |
| NodeExecutionKey | `(sid, tid, workflow_digest, execution_node_id, attempt)`；attempt 为 0-based，每次业务重试加 1 |
| did | `SHA-256(versioned canonical tuple(NodeExecutionKey, purpose))`，purpose 为 work 或 judge；采用完整摘要，不含 owner、holder、时钟或随机值 |
| interaction_id | 由 NodeExecutionKey 加来源、交互 generation 确定；远端来源还带原 did 与 remote interaction ID。重试/下一轮不得复用旧交互 |
| deadline_id | 由任务或 NodeExecutionKey 加阶段和已登记 occurrence 确定；同一发生身份及 at 重放不变 |
| artifact_id | 由 tid、execution_node_id、展示逻辑 slot 和编码版本确定；**不含 attempt**。不同循环执行节点隔离，重试替换同一展示单元 |

Runner 决定 did 的业务关联；Worker继续使用 `EffectId(sid, source_commit, action_index)` 和输出 EventId，固定原 commit、digest 和顺序。Worker 对已有 did 校验完全相同的 DispatchSpec，不能重编号；同 ID 不同请求为冲突。checkpoint 中现有 `dispatch_seq` 是中间输入的 holder/seq 去重状态，不是 Runner 的 dispatch ID 分配器。

### 5.3 可序列化状态

沿用 `RunnerState { format_version, value }` 外壳，首版明确为 `format_version=1`、`value.kind="bcs.group-fsm"`、`value.runner_semantic_version=1`，与SessionSpec冻结选择一致；拒绝不同kind的测试Runner状态。此前草稿的复合kind尚未形成部署格式，不能将此次拆分称为已部署状态迁移。value为有类型、可校验的对象，逻辑结构如下，实际Rust字段在契约切片按此固定：

| 状态 | 持久业务内容 |
| --- | --- |
| 根 | kind、runner_semantic_version、sid、spec_digest、workflow_digest、兼容reader/compiler标识；`active_task: None 或 TaskBusinessState` |
| TaskBusinessState | tid、Running/Finishing、原始任务输入、started_at、task_deadline、steps_used、nodes、interactions、可选已冻结终止原因 |
| nodes | 按 execution_node_id 排序；Pending/Running/WaitingInput/Judging/Completed/Skipped/Failed；当前 attempt、阶段开始时间与业务 deadline、当前 work/judge did、接受的最终文本、选定 outcome、完成时间与失败分类 |
| interactions | 稳定 id、来源节点/dispatch、generation、受答者约束、prompt、deadline、pending/answered 状态；保留当前任务内已关闭交互的关联以拒绝旧回复 |
| 输出关联 | 已使用的有限逻辑 artifact slots、当前尝试是否已为该 slot 发首个 Upsert；不保存完整展示快照 |

loop_path 是冻结 metadata 的事实，活动循环实例可由活动节点路径得到；join 进度由节点状态和选定边导出。不再保存一份可独立漂移的循环计数栈或入边完成计数。如果为计算缓存这些索引，必须可从上述权威值重建，不能写成第二份持久权威。

节点结果是后续 prompt、Judge 和群结果的业务输入，允许保存；完整公开展示、任务队列、归档引用、outbox、effect 回执、owner/holder、远端 task/cursor/发送状态和投递 ACK 都由 Worker/Connector拥有，不进入 RunnerState。Running/Finishing 是业务是否还可推进的标识，不复制 Worker 的 AwaitingResult/Done 归档阶段。

Worker保证同一 Session 只将一个已启用任务交给 Runner，后续排队 Start 留在 Worker。此前任务归档及终态输出完成，并通过§9.4启动容量检查后才投递下一Start；等待期间任务保持Queued，Runner不接收该Start，也不替它建立业务deadline。Runner可用新tid替换Finishing的active_task。旧tid事件一律不作用于新任务，Worker仍可用它们收敛旧dispatch义务。

第一批独立终结器处理输出额度耗尽等任务级故障时，可以丢弃该任务未提交候选并自行收尾；其终态发布后，为下一任务重新init空RunnerState。不能要求已故障的Runner先置Finishing，也不能把旧任务失败状态带给新任务。这是Worker生命周期控制，不是Runner新增持久权威。

## 6. 输入语义、顺序和状态转移

### 6.1 规范化与防陈旧

Worker负责 InputId 内容冲突、物理去重和 Session 输入顺序；Runner按给定切片从左到右逐项处理，每项后推进到稳定业务边界。同一 now 用于本轮产生的时间，仍在重试的同一 commit 候选不得换 now；宕机丢失未提交候选后的重新计算见 §9.1。业务层不比较 InboxPos，不按 remote_event_id 字典序重排输入。

关键事件：确定终结、InputRequired/AuthRequired、Judge 结果和业务控制；必须走 durable admission，跨 holder 重放保持业务身份。Delta/Thinking/Tool/展示 Snapshot 是中间事件，沿用 Worker 的 `(did, holder_epoch, seq)` 去重。旧 holder 的**有效关键结果**不能只因 holder 旧被丢弃。

Runner只接受当前 tid、当前 NodeExecutionKey/purpose 对应 did 且状态允许的事件。一个有效 did 已唯一绑定作用域、目标和 attempt，归一化执行侧另须核对远端身份，不能让正文自报身份越权。旧轮次、旧 attempt、已接受终结后的重复关键事件、迟到 delta 均不推进、不覆盖节点结果、不再展示。

以下事件处理、重试和FailRun规则属于本批FSM策略，不能作为所有未来模式的统一失败策略。通用归一化层报告确定事实及带schema的failure category/code，不能把“是否可重试”从远端文本或连接故障中推导出来。模式特有的有界失败详情须由该模式注册的版本化schema解释；未注册事件/schema在边界明确拒绝并保留输入失败证据，不映射成任意已知业务失败。共享终结事实的扩展仍需契约评审，不预留无约束终态变体来承诺未来永不改公共契约。

failure category集合随schema版本封闭，新增category必须升级schema；已知schema出现未知category属于schema违规，在边界拒绝并保留证据。code是该schema内允许有界扩展的附带详情，不是新的终结事实或重试指令：schema、终结事实和category均已知且合法时，即使code未识别，也接收终结事实、保留原code及未识别标记，不因code新增而等待节点超时。Runner仅按冻结的category/终结事实策略决定：普通Failed/Error可消耗剩余次数重试，永久配置失败或明确派发Rejected仍FailRun。known/unknown code不得改变该策略；身份、必填结构、正文大小等检查不因此放宽。

FSM事件规则：

| 输入 | 业务含义 |
| --- | --- |
| Thinking | 可输出临时 Thinking；不进入节点结果/最终快照，不改变进度、分支、超时或重试预算 |
| Delta / 展示 Snapshot | 只更新允许展示的 slot；不表示节点完成；Snapshot 不是 checkpoint |
| Tool | 仅结构化展示；任意 stdout、@mention、工具名不能隐式变成下一节点、完成或取消 |
| Final / 确定 Completed | 必须提供完整规范最终 artifact，不能仅依赖可能缺失的流式 delta；去空白后空文本走现有失败/重试规则 |
| Error / 确定 Failed | 根据明确 failure category 处理；网络断流、状态 Unknown 不准伪造为业务 Error |
| 确定 Canceled / Rejected | 当前attempt未经本地任务取消而远端Aborted/Canceled，沿用旧Error/Aborted路径作为节点失败，可重试；不是整个任务的Cancel。明确派发拒绝/配置错误按FailRun；本地终止后的取消确认仅清理，不解析自然语言猜分类 |
| InputRequired / AuthRequired | 当前远端执行暂停等待，生成可恢复交互；不是完成，也不启动新节点 |
| Judge 结果 | 只接受当前 judge did 的已声明 outcome；置信度/说明是结果内容，不新增隐式阈值策略 |

普通Tool/文本不能授权控制。共享模式控制输入必须显式注册、带版本、校验身份/作用域/授权后经durable admission；具体schema和允许动作由模式声明。FSM对此类协同命令明确拒绝，普通Tool仍仅展示。E17的旧受信intent路径继续从已存intent claim取得参数、保留run/Bot/群/Session/tool-call及有效期校验，不能从事件正文替换参数；本批不改该路径，也不把其存储协议规定成所有未来模式的唯一协议。

外部凭证/intent的授权与受理有效性由受理侧校验并冻结为可重放事实；若模式采用intent引用，受理/Worker引用解析须提供经校验的不可变参数及内容摘要，Runner不能拿引用临时查询可变缓存。Runner仍按自身状态检查当前tid/执行实例、交互是否关闭和业务时限；“不读取系统时钟”不等于不能使用注入now。受理时合法不保证处理时仍业务有效，人工回复按§6.3/§9.1处理。未来控制模式须明确其受理期限与业务期限的区分，本批不替ManagerWorker决定该协议。

远端 Unknown 不需要进入 RunnerState 的传输状态。无确定终态时节点保持业务等待，已有 deadline 继续有效。Connector只把确定事实交回 Runner，Worker可独立记录/展示运维状态，不制造节点失败。

### 6.2 转移表

`advance` 指第 4 节的可达性、跳过和汇合闭包；`end(S)` 指关闭业务交互/业务 deadline、为当前未完成 dispatch 生成 CancelDispatch、置 Finishing 并发一次 FinishTask(S)。根Finishing使所有节点不可再推进，无须重写每个未完成节点为Abandoned。以前已发出的取消义务仍由Worker保留。

| 输入 / 触发 | 前置条件 | next_state | actions | next_deadlines 变化 |
| --- | --- | --- | --- | --- |
| init | spec/工作流校验通过 | 空 active_task，绑定 digest | 无 | 空 |
| Start | 无活跃任务，或 Worker已放行下一任务 | Running，初始化物化节点，入口激活 | Working；Bot StartDispatch 或原生交互 SetStatus | task deadline；入口 work/interaction deadline |
| 重复 Start | 相同 tid 已启用 | 不变 | 无，IgnoredStale | 不变 |
| 另一 Start | 现任务仍 Running | 不变 | Rejected(task_busy)；Worker本不应投递 | 不变 |
| 中间事件 | 当前 work did、未结束，符合阶段 | 仅必要输出关联改变 | Show(Upsert/AppendText/Thinking) | 不续期 |
| work Final，无 Judge | 当前 Running/WaitingInput，完整非空 artifact | 关闭该 dispatch 交互，节点 Completed/outcome=complete；advance | 最终文本 Upsert；新 Ready 节点动作 | 删除该阶段/交互；增加新节点 deadline |
| work Final，有 Judge | 同上 | 固定 artifact，Judging | StartDispatch(purpose=NodeJudge)；更新状态 | work deadline 换为 judge deadline |
| judge Final | 当前 Judging，结果格式及 outcome 合法 | 节点 Completed，保存 outcome；advance | 新 Ready 节点动作 | 删除 judge deadline；登记新节点 |
| Error、远端Aborted/Canceled、空 Final、无效 Judge、work/judge Timeout | 当前实例/阶段匹配；Timeout到期 | Bot还有次数：关闭当前 attempt，attempt+1；否则 end(Failed)。Human失败直接 end(Failed) | 超时先 CancelDispatch(old did)，再新 StartDispatch；耗尽则 FinishTask(Failed) | 旧发生身份撤销，新 attempt新 deadline；不改变 task deadline |
| 明确派发拒绝 / 永久配置不匹配 | 当前 work/judge did | end(Failed)，不消耗剩余次数继续派发 | 取消同任务其他未完成 dispatch，FinishTask(Failed) | 清空业务 deadline |
| 原生 HumanReply | 当前 pending id，actor匹配，now < deadline，非空且限长 | 固定回复；无 Judge则 Completed并advance，有 Judge则Judging | Applied；关闭交互，必要时Judge StartDispatch | 关闭交互 deadline，登记Judge/下游 |
| 远端 HumanReply | 当前 did+interaction+generation，actor已授权，未过期 | 当前交互 answered，dispatch业务状态继续运行或仍有其他待答项 | ContinueDispatch；确定的 PendingInput id/seq；刷新非终态状态 | 回复本身不续期 |
| InputRequired/AuthRequired | 当前 work did，交互身份未被处理 | WaitingInput，登记交互 | SetStatus及prompt展示 | 使用现有 work/task 截止，不因重复请求延长 |
| 同交互重复请求 | id、generation、内容相同 | 不变 | IgnoredStale | 不变 |
| 旧/重复/过期 HumanReply | 交互不存在、关闭、作用域错或now >= deadline | 不变 | Rejected(stale_interaction/expired_interaction/invalid_reply) | 不变；到期事件仍需消费 |
| Cancel | 当前任务尚未 Finishing | end(Canceled) | 取消所有当前未完成 dispatch；FinishTask(Canceled) | 清空业务 deadline |
| Task Timeout | 同 occurrence，now >= at，任务Running | end(Failed)，reason=task_timeout | 取消未完成 dispatch；FinishTask(Failed) | 清空业务 deadline |
| 原生人工等待 Timeout | 同 interaction occurrence 且仍待答 | end(Failed)，不重试 | FinishTask(Failed)及其他dispatch取消 | 清空业务 deadline |
| 全图收敛 | 每节点Completed/Skipped，唯一终点Completed | Finishing | FinishTask(Completed, terminal_result)一次 | 清空业务 deadline |
| 陈旧 Agent/Timeout | 旧 tid/did/阶段/occurrence，或已 Finishing | 不变 | IgnoredStale，无业务动作 | 不变 |
| 无进展 / 步数耗尽 | §9规则成立，状态可解释 | end(Failed)，稳定原因码 | FinishTask(Failed)及取消 | 清空业务 deadline |

`next_deadlines` 为每次决策后的完整集合，按 `(at, deadline_id)` 稳定排序，不是增量patch。Worker只对持久登记且实际到期的occurrence生成内部规范Timeout，外部调用者不能伪造它；身份沿用第一批统一去重规则。恢复已合法生成的Timeout时，注入now不得小于该输入已证明发生的deadline.at，即使系统时钟回拨也不把它变为过早输入。

未知/旧occurrence为IgnoredStale。匹配当前登记但`now < at`的直接Runner调用返回`InputNotDue`错误，无部分Decision、无disposition，也不推进consumed；绝不能提交Rejected后期待同InputId再次生效。Worker正常路径在持久受理前完成到期检查并重新登记未来唤醒，故不会用未来Timeout阻塞后续Cancel。若内部违约已形成这样的输入，则保留同一输入到期重驱动，可先提交其前的有序前缀，不越过它提交后续输入，不消耗业务重试或隔离失败预算。

### 6.3 竞争与人工交互细则

1. 完成、取消、超时以 Worker给出的规范输入顺序仲裁，第一条**有效且已冻结终止决定**胜出。同批逐项执行亦如此，不把 Cancel 自动提到队首。Final只是中间节点完成时，之后的 Cancel仍可结束任务；此前顺序内生成的 StartDispatch及随后 CancelDispatch保持顺序，不宣称那些远端从未启动。
2. Bot/Judge 的 deadline 由有效 Timeout输入落实；仅观察到 `now` 已过期不抢先推翻排在 Timeout前的有效 Final。人工回复沿用现有严格时间检查：`now >= deadline` 就拒绝，即使 Timeout尚未消费。task 总超时一旦生效，不再重试节点。
3. 并行节点可交错完成，但每个节点只接受当前实例一次结果。某分支失败耗尽即整体失败，其他分支结果随后只清理，不“继续凑齐”成功。
4. 原生 HumanInput为当前任务固定一次；前端节点允许已获外层授权的在场 Human，指定 runtime_actor 的节点还须匹配冻结 assignee。正文里的 actor不算身份。回复 trim 后非空，沿用 64 KiB UTF-8上限，并受Session输入上限约束。
5. 远端交互以 scoped remote interaction ID 生成本地ID。已关闭的同远端ID重复出现不能重新打开；新的问题必须有新的远端交互身份。多 pending 项确定性排序，存在 Authorization时TaskStatus为AuthRequired，其次InputRequired，否则Working；状态包含完整pending集合，不靠一个全局“当前问题”覆盖其他dispatch。
6. ContinueDispatch使用当前did内独立递增的pending-input序号；输入ID由interaction及规范回复InputId确定，内容摘要固定。再次传输是Worker/Connector重放，不能再增加业务序号。回复后远端仍Unknown时不假造已经继续成功。
7. Finishing 后不再发Show、重开交互或改写结果。旧任务的关键回流可让Worker/Connector收敛取消/终结义务，不能再次reduce为新业务。相同InputId的重试返回既有受理/处理结果；不同InputId提交已关闭交互得到业务拒绝。Applied只证明回复已在checkpoint成为业务决定，不证明远端已收到ContinueDispatch；AdmissionReceipt则连业务生效也不承诺。

规范事件payload固定为带schema的变体：展示事件含逻辑slot及文本/结构化内容；work完成含完整artifact文本；Judge完成含现有JudgeDecision；失败含schema封闭的category及有界原始code，允许按§6.1标记code未识别；交互含remote interaction ID、kind、prompt和受信答复约束。purpose从冻结DispatchSpec核对，不接受正文覆盖。不同关键InputId若指向已关闭实例，返回IgnoredStale；相同活跃interaction身份但内容冲突返回Rejected(interaction_conflict)，保留原问题。业务可解释的非法回复/内容返回Rejected；身份越权应在admission拒绝，不让正文影响图控制。

## 7. 动作、输出与群最终结果

| ActionIntent | 生成条件与参数 | 消费者与约束 |
| --- | --- | --- |
| Show | 当前实例有效输出；tid、execution_node_id、稳定artifact_id、class、operation、content | Worker公共投影与TaskFeed；Upsert替换完整值并保留位置；AppendText仅文本，不做JSON merge |
| SetStatus | Working/InputRequired/AuthRequired等非终态变化；完整pending interactions | Worker任务草稿和输出；禁止Completed/Failed/Canceled/Rejected；无变化不重复发 |
| StartDispatch | Ready Bot或已固定artifact的Judge；固定did、purpose、attempt、target/profile、pool、capabilities、request和deadline | Worker创建/重放DispatchBoard；Connector执行；不等整个远端执行完才算必要CreateDispatch effect完成 |
| CancelDispatch | Cancel、整体失败/总超时、旧attempt超时或放弃；原did、稳定request_id、原因码 | Worker保存取消意图并监督；Connector取消；RequestAccepted不等于ConfirmedCanceled |
| ContinueDispatch | 当前远端交互接受一条回复；did、PendingInput(seq,input_id,digest,payload) | Worker推送继续输入，Connector执行与恢复；人工节点本身不用这个动作 |
| FinishTask | 唯一成功终点且全图收敛，或业务失败/取消/拒绝；tid、TerminalStatus、完整稳定result | Worker两阶段归档和终态输出；Runner不写ResultStore、不发最终群消息、不自行开始下一排队任务 |

### 7.1 输出内容与稳定身份

Connector归一化输出为有限逻辑slot（主文本、具名artifact、工具展示），未知/不稳定slot不允许无限扩张。每个新attempt对已存在slot首次写入先Upsert，之后才AppendText；重新播放同attempt不重置slot。主最终文本使用主slot完整Upsert，保证中间事件丢失不影响最终业务结果。工具过程展示不当作节点最终结果；旧attempt事件不能追加到新attempt的slot。

Runner只保留生成动作所需的slot关联；完整展示单元、首次出现顺序、折叠与snapshot由第一批公共投影负责。Thinking临时显示但不进snapshot/归档，不能通过“最新thinking”选择结果、续期或判定进展。非法AppendText/超大动作须在Worker验证整个Decision时拒绝，不能半批生效。

TaskFeed仅在授权TaskScope内读；不得把所有节点artifact投递为群Public消息。现有StateMachine历史的FullOnly/Directed和producer admission facts需由外围桥接保留，不能在首次callback重新查询当前角色后扩大可见性。`projection.visibility`目前不是可任意改写受众的语言，本批不扩展它。

### 7.2 结果形成与结束

成功result为版本化业务对象，包含终点身份、实际结果来源节点及最终文本，供Worker归档；失败/取消result包含稳定原因码、失败节点/attempt（如有）和有界进度摘要，不重复复制全部节点正文，不含未确认远端取消的成功声明。已接受节点结果留在既有状态与展示中。所有完成时间使用计算时注入且已提交保存的now，不在归档时重算。

v1保留E12：有final_output时用其文本；只有terminal HumanInput而无final_output时，按node_id降序找首个已有artifact。即使该文本来自上游Bot，也不能未经决策改为Human回复；版本化result须明确实际来源。这是兼容现有实现的规则，并非建议的新结果选择能力。Human带Judge时必须等合法outcome完成。v2要求唯一外层final_output。禁止按事件到达先后、非确定存储顺序或拼接全部中间输出选结果。终点完成而全图尚未Completed/Skipped时不能FinishTask(Completed)。

一次FinishTask后Runner保持Finishing。Worker按第一批先固定AwaitingResult和SaveResult，再确认归档并提交终态及输出；归档不可用则停留等待。隔离不能把已冻结Completed/Canceled改写为Failed。若外围需要发布one-shot群结果，必须在已确认终态后，由应用桥接复用既有publication身份和message-flow准入，不能由Runner调用`publish_state_machine_result`。

## 8. 失败、重试、远端未知与 deadline

| 情况 | Runner业务策略 | Worker/Connector义务 |
| --- | --- | --- |
| Bot确定执行失败、非本地取消引起的远端Aborted/Canceled、完整Final为空、Judge求值失败/无效outcome | `attempt+1 < effective_max_attempts`则重试同执行节点；否则Failed。effective次数沿用`max(1)`；Judge失败重试整个Bot节点，不能悄悄改为无限重判 | 重放与业务新尝试分离；新did，旧结果隔离 |
| Bot/Judge阶段Timeout | 同上；有活跃旧did则先发取消意图，再新尝试 | 保留旧未知义务；不能因新attempt出现删除旧dispatch |
| Human等待或其Judge失败 | Human effective_max_attempts=1，Failed | 保留已接受的人工输入作为业务证据，不重新询问冒充未答 |
| 确定派发拒绝、目标配置不兼容 | 整体Failed，即使还有次数 | 不伪装NotSent暂态重试成功 |
| 仅连接断开、发送/取消未知 | 无业务终结事实；现有节点deadline继续有效 | 按能力查询/恢复；不盲目重发同did，不把Unknown映射Failed |
| 明确NotSent且仍可发送 | 保持当前attempt，不消耗业务次数 | Connector可在确认从未提交的前提下重试同一稳定请求；确定拒绝或配置不兼容才归一化为Rejected。不把NotFound或Unknown推断为NotSent |
| Cancel / 总任务Timeout | 分别Canceled / Failed；关闭所有业务推进，对未完成did发取消 | 本地业务结束后继续未知执行和取消监督，不阻塞结果归档，也不宣称远端停止 |
| Runner格式/冻结定义/必需证据不可解释 | 返回明确RunnerError，无部分Decision | 由第一批隔离规则决定Blocked/安全收尾；不能把损坏状态当空状态重跑 |

节点work timeout优先用显式node_timeout_ms，其次definition.defaults；Bot非正值沿用旧语义视为没有单独节点时限，但始终受task总deadline约束。Judge阶段使用节点/default timeout或90秒缺省并取至少1毫秒，再被task总deadline截断；原生Human必须有正timeout。有效deadline=min(阶段开始时间+预算, task_deadline)，溢出作为无效冻结配置拒绝，不回绕。

远端InputRequired/AuthRequired沿用当前work deadline，不暂停、刷新或延长节点预算；新的独立“执行时间暂停计费”不是当前分布式契约，本批不移植另一套run-budget体系。所有等待由task总deadline兜底。业务事件到期产生的新重试不会延长task截止。

Judge是非确定的外部计算，但**已接纳的Judge结果是确定的Runner输入**。Judge dispatch使用既有持久完成/关键回流协议；有已保存结果则恢复它，不重新调用LLM决定分支。当前JudgeEvaluatorPort本身未保证幂等调用；执行侧不能虚报该能力。调用已发生而结果未知时保持Unknown，由业务deadline处理，不能为同did重新采样后覆盖原决定。本批不承诺LLM恰好调用一次。

## 9. 确定性、恢复、状态升级与资源底线

### 9.1 确定性约束

- BTreeMap/排序视图提供稳定遍历；Ready集合、上游artifact、actions、interactions、deadlines有固定排序。按输入顺序依次产生动作，同一输入内先撤销旧义务、再按节点ID派发、最后发布汇总状态/FinishTask。
- prompt与JudgeRequest只依赖冻结定义、任务输入、已接受的节点结果和校验后的LoopContext；不调用当前成员查询、动态显示名或随机默认值。JSON渲染版本固定。
- init/reduce对错误不修改传入状态。检查整图、状态关联、大小和数值范围后才能返回完整Decision；Worker再验证动作与checkpoint的一致性。
- 同一事务候选重试保留同输入、now、动作顺序和内容；owner更换、holder更换、连接类型不进入业务身份。不同真实输入顺序可以合法导致不同终态，但每个顺序都须可重放。

确定性承诺包含now这个参数，不把“相同输入、不同恢复时间”称为相同计算。提交结果未知时必须先解决原提交是否成功，不能以新now覆盖一个可能已提交的决定。若宕机丢失候选且已确认未提交，允许以恢复时注入的now重新计算；提交前没有任何action执行。这不会新增业务attempt，但可能改变deadline、完成时间或临界点的人工回复判定。例如D−1计算接受、未提交即宕机、D+1重算时回复将被拒绝；若D−1已提交则必须保留接受。要保证“受理时间早于D就一定接受”需要另行改变业务时限语义并冻结可信受理时间，本批不引入该要求，也不在Runner另存候选日志。

### 9.2 恢复和升级

恢复从Worker已提交的RunnerState和精确工作流开始；先处理旧必要effects，再reduce新输入。不调用旧CollaborationRunRepo读取第二套节点状态，不根据远端查询结果直接修补RunnerState。

format/kind/Runner语义版本/digest、节点集合、当前attempt/did、结果/outcome、交互和deadline关联均必须验证。旧format仅通过明确登记的纯迁移函数读取；首版没有已部署的旧生产群Runner格式，不能把测试Runner JSON或legacy run rows当作v0自动转换。未知format返回UnsupportedFormat，未注册冻结语义版本返回拟议UnsupportedRunnerVersion，损坏关联返回CorruptState，无效新图返回InvalidWorkflow。

若以后有状态迁移，保留tid/did、interaction、deadline occurrence和已冻结结果；由Worker在正常fenced checkpoint提交中持久化，禁止就地无版本改写。没有安全迁移时Block并等待适配或显式新会话。回滚版本必须仍能读取已产生格式，否则仅停止新会话接纳并由兼容reader排空，不能接回旧引擎继续写。

能读取状态不等于具备相同决策语义。只要仍有该版本的在途会话，部署必须保留对应语义的reducer；所有可能接管该会话的Worker必须支持冻结版本，或在执行前Block并保留状态/义务。没有版本感知路由的部署须在可接管该会话的二进制中保留对应reducer；本批不新增路由平台，也不要求每个无关二进制包含全部历史版本。停止新会话准入不能替代已有会话的兼容执行能力。

在相同冻结定义、状态、有序输入和now下，任何改变next_state、actions、deadlines或dispositions的修复都采用新的Runner语义版本，新会话才可默认选择；旧会话继续旧语义。只改变实现而不改变这些结果可保留语义版本。纯状态格式迁移不得暗中切换语义；旧版本必须停用且没有安全继续方案时按第一批Block处理，不能用“bug修复”名义静默换版本或伪造任务失败。

### 9.3 有界推进

复用`FixedLoopLimits`的当前缺省：深度4、每循环32轮、每body64节点、展开2048节点、计划4 MiB（[配置证据](../../crates/service-api/bcs-config-api/src/fixed_loop.rs)）；实际受理使用已校验配置并冻结，不把缺省等同生产容量。v1也必须受展开后节点/边及计划大小上限约束；本批提议边数缺省上限8192，作为可配置且冻结的基础保护，尚无生产容量验证。

`SessionLimits.max_task_steps`计每次Bot/Human节点激活及业务retry一次；同attempt的Judge阶段、Thinking、重复事件、Worker重放和consumed-only提交不加步数。达到上限后不创建下一dispatch，结束为Failed(step_limit_exceeded)。节点重试仍受max_attempts约束，数值耗尽不回绕。

纯闭包每次推进必须新增一个明确的节点状态变化，单次闭包最多`2 × 节点数 + 1`次扫描；达到边界而未稳定返回结构错误，不能自旋。稳定时若仍有未完成节点，但没有Ready、Running、Judging或pending交互等可解除阻塞的节点义务，结束Failed(no_progress)；孤立的task总deadline不算可推进义务，不能靠等待它掩盖断裂的汇合。正常远端等待、人工等待和Worker提交/归档不可用不算业务无进展。

本批提议Runner业务状态的申请上限为规范编码16 MiB、每任务artifact slot最多4096；这些是待验证的保护值，不是已有配置或性能结论。最终有效Runner字节预算在admission按组合profile取较小值：申请上限与checkpoint扣除通用记录、outbox、provider封装及收尾预留后的可用空间，并随SessionSpec冻结。第一批最新提案的checkpoint总上限为1 MiB、outbox为256 KiB，因此不能直接启用16 MiB的内联Runner状态，也不能由本批默默提高第一批上限。

每个输入/输出仍受第一批字节预算。admission检查有效预算能容纳物化图，另外预留终止原因、有界错误result、当前图最大活跃dispatch的Cancel intents和本批disposition effects所需空间；不能计算出正的有效预算、无法容纳所选图或最大合法动作批次时拒绝该配置/图，先收紧图和批次准入。实现验收须覆盖第一批1 MiB profile，不以正文Blob外置推导RunnerState已获得额外存储空间。

数量预算复用并补齐第一批通用约束，计数归Worker；模式图规模和steps只作进一步收紧：

| 预算 | 计量及恢复规则 |
| --- | --- |
| 每次Decision动作数 | 计所有Start/Cancel/Continue/Show/SetStatus/FinishTask，连同disposition转成effect后的数量校验批次；N个节点同批超时可产生N个Cancel加N个Start，不能以活跃节点数作为动作数上界。合法最坏输入批次须在配置受理时能容纳，不能截断Decision |
| 每Task累计dispatch/交互数 | 每个新did、规范interaction身份各计一次；work/judge、重试均计入，重放不重复计数；关闭不返还累计额度 |
| 每Session未确认结束dispatch数 | 新Start的已提交占额及尚未收敛的旧attempt、已本地终态Task的远端义务均计入；仅已核验确定结束或确定未启动的证据可释放，CancelIntent、Unknown、本地归档均不释放。启用下一Task还须扣除§9.4未使用的启动预留，不重复计作已创建dispatch |

这些上限随SessionSpec/profile冻结，Worker在既有checkpoint/dispatch义务元数据中维护有界占额及释放证明，不在RunnerState复制权威计数。新did占额与其outbox动作同一fenced提交，重放按稳定did核对；释放也须有持久、幂等且可恢复的事实。未完成dispatch预算是Session边界，不用全局瞬时连接/线程槽位改变业务结果；执行侧全局暂态容量继续走第一批有界背压。收尾Cancel、既有关键回流和归档使用独立预留，不能被新启动额度耗尽阻断。

本批选定的最小上游补充分开处理启动等待与执行超额：受理前即可确定无法容纳的配置/图明确拒绝；前一Task遗留义务导致下一Task启动容量不足时按§9.4保持Queued，不先交Runner再判失败。已Running且状态/预算可解释的任务，若触及正常执行额度（例如累计交互数），Worker拒绝整个未提交候选，以拟议稳定原因 `ExecutionQuotaExceeded` 通过第一批独立终结器将**当前Task**收尾为Failed，保留已确认内容并持久登记其未结束dispatch的取消义务。与TaskPhase/consumed共同提交处理结论，包含其他任务输入的批次拆分，不确认未处理输入；下一Task获准启动时重新init。额度不自动释放，也不重复reduce同一必然失败候选。该路径沿用第一批§3.3输出超额终结机制，但执行数量预算及启动背压仍属本稿提议，不能声称第一批已规定或实现。

无需为上述失败路径向EvaluationContext注入实时“未结束数量”，也不新增Runner的配额等待/重试策略。若未来需要业务感知容量，须另行定义不可变资源观察输入、候选/恢复一致性、占额与唤醒协议，只读快照本身不构成额度预留。预算数据/收尾状态不可解释、违反不变量或无法安全提交时仍按第一批故障隔离处理，不能把损坏状态当正常额度耗尽；已冻结的终态不能被配额失败覆盖。

正文超过预算不截断为可用业务结果：回滚当前输入未提交的候选增量，以此前可解释状态加有界失败摘要结束，该输入返回Rejected(resource_limit)；已经处理的有序前缀仍按原顺序提交。任何新增正文/slot都不得侵占终止预留，不把无限累积藏进JSON字段。若现存状态连预留不变量都不成立，返回CorruptState交第一批隔离，不能产生无法提交的FinishTask或截断取消列表。对展示slot、关闭交互记录和继续输入序号也需检查总状态预算及数值溢出。

Runner每次reduce为零DB/网络往返；朴素可达性/汇合为有界O(N+E)扫描，闭包有上述界限。一次节点dispatch通常新增一条既有dispatch与输出effects；Judge增加一条dispatch，人工节点不增加假dispatch。每条durable输入的disposition另需一次对原admission记录的幂等条件更新，可有界批写但不能以批写冒充单行代价；中间流事件不增加这类写入。最坏批量按上述动作总数及第一批输入/字节/effect上限计算；旧dispatch收尾可能跨批，由Worker有界扫描和保存进度，不能截断取消列表。预算校验使用前述上游补充维护的有界占额元数据，不逐节点查DB或每轮扫描全部历史Task来计数；释放证明处理与第一批dispatch监督衔接。实施验收须断言正常、最大图、额度耗尽及effect重放路径的读写次数与批大小，不能在远端请求期间持有DB事务。状态/checkpoint大小随物化节点及保留业务结果增长；不能以纯单测推导存储吞吐。基础诊断只报tid/node/attempt、错误码、步骤数、等待阶段和大小，不记录正文、Judge原始响应或凭据。

### 9.4 跨Task启动容量与有界等待

FSM validator在受理时按完整物化图计算每Task最多新建的dispatch需求上界 `B`，与计算规则版本、模式配置digest一起冻结为Worker可读的通用数值。首版采用可证明但不求最紧的总量界：对每个物化Bot节点计 `max_attempts × (1 + has_judge)`，对每个物化Human节点计 `has_judge`，再安全求和；has_judge为0或1，Human固定一次，无假work dispatch。节点的缺省次数在受理时解析并冻结，循环按展开后的节点计，互斥分支也保守相加；Continue/Cancel重放不产生新did，不进入B。整数溢出拒绝配置，不截断。Worker只验证/使用该数值，不读取图计算业务策略。

不能用“最大并行宽度×重试次数”替代B：串行A→B各两次尝试，A0超时Unknown、A1完成后B0再超时，启动B1时A0/B0/B1共占3个，超过宽度1×次数2。步骤/总时限等其他保护可能提前结束Task，但首版不据此削减保守界。

设冻结的Session未结束dispatch上限为C。受理必须验证B不超过C及每Task累计dispatch上限，并校验§9.3的动作/字节/收尾预算；否则以 `InsufficientExecutionCapacity` 拒绝该配置/新会话。B是所有可能创建did的总量界，不是实际远端并发承诺，也不是把B条空dispatch预先写入存储。

2A必须逐个统计现有有效[运行fixture](../../crates/services/bcs-collaboration-runtime/tests/fixtures)、[API fixture](../../tests/fixtures)及[群工作流模板](../../seeds/collaboration-templates)，包括测试内构造的串行、分支、Judge、固定/嵌套及并行循环图；非法/非工作流fixture列出排除原因。交付记录固定源码/模板revision、参数profile、展开节点数、B、实际编码预算及准入/拒绝结果，再据此确定可配置默认上限和容量等待时长。2048个Bot节点各3次且含Judge的保守界可达12288；这是计算示例，不是已验证模板需求或默认额度。不得为让模板全部准入而直接抬高C或第一批1 MiB checkpoint上限；必须同时验证旧义务占额元数据、RunnerState、outbox和收尾预留。若保守界使应支持模板无法准入，2A须给出兼容性取舍；要缩紧界则先提供安全证明、版本化规则和回归，不能删除fixture掩盖拒绝。未完成该报告或未选择经验证的profile前保持新模式关闭；本稿不拍定未经测算的通用默认值，fixture结果也不证明生产容量。

Worker用既有fenced checkpoint/Task队列元数据维护实际未结束占额O及当前Task未使用的预留R，保证 `O + R <= C`。同一Session只有一个启用Task：确认前一Task终态输出完成后，按既有队列顺序选择下一Task；可用额度至少B时，在批准该Start的同一提交中预留B、推进TaskPhase及提交Runner决定。每个新did将一单位R转换为O，与其outbox原子提交；同did重放不重复转换。确定终结证明仅释放对应O，不补回已经消耗的Task累计次数。Task冻结终态后释放未使用R，已创建的旧did仍计O；提交未知先确认原结果，不能重置预留或重复启动。该计数属于Worker，不进入RunnerState，不另设持久资源服务。符合冻结B的任务不会因前一Task旧义务而在启动后耗尽已预留的dispatch容量；超出已验证B或损坏计数属于不变量违反，按第一批故障分类，不伪装成普通容量等待。

容量不足时，下一Task保持Queued，不向Runner投递Start，不产生该Task的StartDispatch或业务deadline。Worker在首次确认进入容量等待时固定绝对 `capacity_wait_deadline = wait_started_at + capacity_wait_timeout_millis`，连同稳定发生身份 `(sid, tid, capacity-wait/v1)` 持久提交；时长须为profile内显式、正且有界的配置，不能缺省成无限等待。重复唤醒/恢复/接管不续期，未知提交先确认；配置更新不改变既有会话的B/C、预留规则或已固定截止。容量释放证明持久生效后重新调度；同时用既有持久调度登记截止唤醒和有界复查，不能只靠一次通知。等待不是Runner业务no_progress，也不复用只有Start后才建立的task deadline。

下表是**拟议Worker任务生命周期补充**，不是Runner转移；等待字段、容量内部到期事件和预留均由第一批实现。处理规范控制输入与启动判定仍在同一Session串行推进，不能跳过更早的有效Cancel来启用任务。

| 触发 / 前置条件 | Worker状态、动作与deadline |
| --- | --- |
| 下一Queued Task，O+B<=C，尚无容量等待或本轮now早于其截止 | 原子批准Start并占额，清除该容量等待；只在这一刻允许生产Runner处理Start并建立业务deadline；此前必要effects及前任务归档仍须完成 |
| 下一Queued Task，O+B>C | 保持Queued，首次登记固定等待截止及发生身份；以后只复查，不重新reduce该Start，不消费其业务重试预算 |
| 确定释放证明 / 有界复查 | 幂等更新O；容量仍不足继续等待，足够且未过期才启用。now>=等待截止时不因刚释放容量而抢先启动，保持Queued并处理规范的内部到期事件 |
| 匹配的容量到期事件，仍Queued且now>=截止 | 由Worker独立终结器冻结Rejected，稳定原因 `StartCapacityTimeout`；复用两阶段归档/终态发布，不伪造Runner FinishTask，不先改成Running；不取消或释放前Task的义务 |
| 等待期间有效Cancel | 仍未启用时由Worker冻结Canceled并清除等待，走既有收尾；与容量到期按规范输入顺序及第一次已冻结终态仲裁。已经启用则按正常Runner Cancel处理 |
| 已启用/终结后的旧容量唤醒，或重复释放证明 | 不重复Start、不改终态、不重复返还额度；只收敛调度/去重责任 |

Queued期间的Cancel、旧Task关键回流、独立归档/取消义务继续有预算推进；启动背压不能堵住释放容量所依赖的输入。到期Rejected/Canceled须终态发布后才依队列顺序处理后继Task。启动检查只读取有界聚合元数据，不扫描历史Task计数；预留、等待登记/清除与生命周期写入合并，截止和释放触发沿用Worker调度，不增加无界轮询。

## 10. 迁移与共存

1. 保持opt-in，生产装配按§3.3注册`bcs-group-runner`。新会话准入由宿主facade在创建SessionSpec前检查，最小粒度为`(scope, group, mode kind)`的显式允许集合及所选语义版本；总开关关闭优先于允许集合。默认关闭、未命中则明确拒绝新模式请求；测试Script永不作为fallback。第一批Worker、本文契约补充、经2A验证的容量profile及Connector能力未齐全时即使命中允许集合也拒绝。协调存储必须由实际生产provider提供DocumentCas、StrictLease、ZSet并通过对应契约套件；声明supports或测试内存模型通过均不等于生产就绪，具体套件见2D。此为最小静态配置边界，不要求百分比灰度平台。
2. 会话在创建时选定引擎及namespace，生命周期不切换。旧会话继续旧CollaborationRuntime；新会话只写新checkpoint/DispatchBoard，不双写legacy节点表，不让两套引擎消费同一业务输入。
3. 保留现有authoring API/模板与fixture，验证、编译及快照reader提纯后由两套入口复用。开关和允许集合只影响新会话；一旦受理，engine/namespace/模式及语义版本按冻结选择路由，重试同一会话创建不能重新抽样或改选。已有会话不再按当前允许集合改选引擎，已冻结计划由兼容reader及reducer恢复。
4. 本批最小应用桥接负责冻结FSM、提交Start/HumanReply/Cancel、返回业务input disposition及读TaskFeed。权限、actor真实性、作用域在桥接检查，不由Runner发起查询。现有普通群routing、Caller凭据接力、IM渠道及one-shot结果发布仍走原服务；未适配的入口明确留旧引擎，不静默降级新会话。
5. 全量UI/历史API同形、IM通知重发与旧run原位迁移不作为本批退出条件。新TaskFeed原生人工交互必须可用；已有业务仍从旧入口运行。后续接入群结果消息时复用E14协议并增加独立集成验收。
6. 回退先关闭总开关或移除目标scope/group/mode允许项，停止对应新会话接纳；已有会话继续原版本，保留兼容Worker/Runner排空及监督远端义务。不得把新模式请求静默转旧引擎，也不删除checkpoint/冻结定义/远端义务。若旧二进制不支持已冻结语义或状态格式，不能替换所有兼容执行实例；按§9.2保留兼容部署或Block。实现、部署、流量迁移分别记录，本稿没有迁移任何会话。

## 11. 实施切片

| 切片 | 交付及依赖 | 退出条件 |
| --- | --- | --- |
| 2A 共享计划与契约 | 提纯现有编译/reader；冻结模式选择/envelope；定义静态注册工厂与兼容检查；拆分通用关联/显式执行目标和FSM配置；补齐失败schema、事件/交互/deadline/disposition、容量上界和Worker启动等待/收尾契约 | fixture编译字节/hash不漂移；逐fixture/模板容量报告与profile取舍满足§9.4/R29；conformance覆盖旧字段迁移、未知schema/category拒绝和新code兼容；此片仅契约/纯计划，2D才启用生产注册和准入 |
| 2B 生产纯Runner | 实现状态编解码、串行/并行汇合、分支skip、loop metadata/previous_result、prompt与群结果 | 真正`DistRunnerCoreService`实现通过业务测试，不用Script代替 |
| 2C 交互和业务控制 | Agent流/终结、原生/远端交互、Judge dispatch、节点重试、取消、timeout竞争、资源与无进展 | §12纯Runner矩阵通过；无IO/时钟/随机依赖；每条事件有确定处理结果 |
| 2D Worker集成与入口 | 复用#4145已有协调钩子；依赖第一批业务processor及本稿启动容量/等待等上游补充，装配生产Runner、可控Connector和通过契约套件的实际生产provider；facade落实准入与旧入口隔离 | 下述provider门槛及生命周期衔接、checkpoint/outbox恢复、启动背压/配额终结、历史语义保留和终态后监督通过；声明未覆盖的真实协议 |

2D启用前，实际装配的DocumentCas provider必须通过 `document_store_contract_tests`，StrictLease provider通过 `lease_store_contract_tests`、`lease_expiry_contract_tests`，ZSet provider通过 `z_set_store_plugin_contract_tests`（E18）。StrictLease的过期/退休终结证据不可逆、稳定返回Terminated，迟到注册/更新不得复活；普通TTL键不是兼容替代。缺少任一能力会被协调存储构造拒绝，不能仅标为故障接管降级。这只是协调层必要条件，第一批其他业务存储/Inbox/调度能力和宿主集成仍须分别验收；本批文档不实现provider，也不把测试内存模型结果当生产验收。

2D的processor从 `acquired_sessions` 取得Recovering会话，核对冻结选择并补做持久effects后调用 `recovery_completed`；可推进时通过 `begin_round` 进入本轮，再读取有序输入、固定now、调用纯Runner、校验预算并提交checkpoint。只有提交结果确定才调用 `checkpoint_confirmed`；有pending effects进入Effects阶段，必要effects全部完成后调用 `effects_completed`，零effects则由前一钩子直接返回Idle。提交未知调用 `round_unknown` 并先确认原提交。调度settle按 `scheduling_started` / `scheduling_completed` 成对管理，未知时保留pending。上述钩子属于宿主processor，Runner不调用协调/IO接口；协调代码存在不等于processor、投影或归档闭环已验收。

各片段是评审/验证边界，不声称单独上线即完成本批。变更归属为公共domain/service-api、共享纯计划服务、生产Runner、Worker动作消费者、Connector执行侧及composition root；企业适配与运行证据由宿主保管，不进入此公共文档。

## 12. 验收矩阵与测试边界

所有“新Runner”用例必须实例化生产`bcs-group-runner`，输入使用现有真实authoring fixture/编译产物或同结构最小图。测试工具可提供有序输入和注入时间，不能实现另一套工作流状态机来替代被测对象。下表为待实现的Given/When/Then测试定义，U为纯Runner或注明的共享FSM validator测试，I为Worker集成；validator的容量报告不能替代生产Runner与Worker的执行验证。

| ID / 层 | Given | When | Then |
| --- | --- | --- | --- |
| R01 U/I 串行 | `a → b(final)`，绑定与输入冻结 | Start；a Final("A")；b Final("B") | 依次仅一个新节点派发；b请求包含A；result=B；仅一次FinishTask；I中归档确认前无Completed |
| R02 U/I fan-out/join | `start → [left,right] → final` | 两分支以两个相反顺序完成 | 首个分支不激活join；第二个后恰好激活一次；上游内容按ID排序相同 |
| R03 U Judge分支 | 现有judge_shared_merge图；另有v1 `a(yes→b,no→final), b→final` | 节点Final后先无Judge结果，再返回合法outcome | 没结果不选边；共享merge保留；普通控制词文本不驱动边；v1选择yes后final的Bot/Judge上下文含A+B，v2按选中Artifact边过滤 |
| R04 U/I 固定循环 | [fixed_loop.yaml](../../crates/services/bcs-collaboration-runtime/tests/fixtures/fixed_loop.yaml) | 连续continue到上限，另轨迹提前break | 分别走exhausted/相应break；原outcome不篡改；上一轮指定result只传一次，未来无效轮次Skipped |
| R05 U/I 嵌套循环 | [nested_fixed_loop.yaml](../../crates/services/bcs-collaboration-runtime/tests/fixtures/nested_fixed_loop.yaml)及并行child fixture | inner continue/break，再outer continue；交换并行child完成顺序 | 完整父路径隔离；新outer的inner第一轮previous=null；父join等两条选中出口 |
| R06 U/I 失败与重试 | Bot max_attempts=2，同一loop执行节点 | attempt0确定Error，attempt1再Error；另测空Final/无效Judge/未经本地Cancel的远端Aborted | 只产生attempt1一个新work did，loop_path和前序结果不变；耗尽Failed；远端Aborted沿节点失败路径，明确派发Rejected首轮即Failed |
| R07 U/I 原生人工 | 带timeout的human_input，后接Bot/或带Judge | 合法受信回复；同InputId重放；不同InputId重复；旧轮次回复；越权/空/过期回复 | 首次固定一次，正确继续；其余不覆盖、不派发；返回既有结果或明确拒绝；无假Agent dispatch |
| R08 U/I 远端交互 | 两个并行work dispatch分别InputRequired/AuthRequired | 回复其中一个，恢复checkpoint，再重复旧请求与回复 | 保留另一个等待，status优先AuthRequired；ContinueDispatch同identity不重复；pending序号不重置 |
| R09 U/I 完成竞争 | 最后节点及有效task/node deadline | 分别输入[Final,Cancel]、[Cancel,Final]、[Final,Timeout]、[Timeout,Final] | 首条有效终结决定固定；timeout可重试时旧Final不能完成新attempt；同批和跨提交均符合§6 |
| R10 U/I 超时策略 | Bot max_attempts=2，旧远端仍Unknown | 当前attempt Timeout | Runner发旧did Cancel及新did Start，不等待取消确认；Worker不重发旧did；旧义务仍存在；task总超时后不再重试 |
| R11 U/I 陈旧Agent | 当前第2轮/attempt1，Worker已完成物理去重 | 旧轮次Final、旧attemptError、重复Final、迟到Delta、旧holder但当前did的有效关键Final | 前四者无业务动作；最后一项不因holder旧而丢弃；Thinking不推进/续期 |
| R12 U 决策重放 | 同spec/plan/state、有序inputs和固定now | 序列化/反序列化后重复reduce，改变map插入顺序 | next_state、actions及顺序、deadlines、dispositions相同；不读时钟/网络/随机 |
| R13 I checkpoint恢复 | 生产Runner+第一批Worker+持久provider+可控Connector；固定输入与时间轨迹 | 在提交前、提交未知、已提交未执行effect、半批effect、归档两次提交之间分别重启/换owner | 同now重算得到相同决定/业务结果；仅恢复不新增业务attempt/did；已提交effects及disposition原身份；归档内容不变。另测D−1回复计算后宕机：未提交且D+1重算拒绝，已提交则保持接受；不承诺跨不同now或不同commit分批的字节等价 |
| R14 U/I 输出 | 主文本Upsert+AppendText、Tool、Thinking，且有重试 | 丢部分中间事件后发送完整Final；重放outbox；恢复TaskFeed | 最终文本正确、Thinking不归档、AppendText不重复、重试首包替换同slot；JSON隐式merge/非文本append拒绝整批 |
| R15 U/I 非法定义/状态 | 未支持kind、guard、任意cycle、并行原生Human、错误hash/compiler、测试Runner状态 | admission/init/恢复 | 无dispatch；明确InvalidWorkflow/UnsupportedFormat/CorruptState；恢复按第一批隔离，不能伪造业务成功或清空重跑 |
| R16 U/I 上限与无进展 | steps恰好到界；循环展开/字节/slot到界与超界；可解释但无可推进待办状态 | 再次激活或追加超量结果，运行闭包 | 边界内可执行，超界无新dispatch且有稳定失败码；不自旋；正常等待不被误判no_progress |
| R17 I 终态后监督 | Canceled或Failed已归档，旧dispatch取消Unknown | 新owner收到ConfirmedCanceled/AlreadyFinished及旧业务输出 | 只收敛dispatch义务，任务与归档不改写，不再Show/FinishTask |
| R18 U/I 冻结与共存 | 新引擎会话进行到循环中途；旧引擎另有会话 | 修改最新定义/binding/admission limits；恢复新会话；旧入口继续运行 | 新会话仍用旧冻结envelope；旧会话不被新Worker消费；不能热切换引擎 |
| R19 U/I Timeout发生身份 | 当前deadline=D、固定occurrence及规范InputId | D−1触发调度；直接向Runner注入过早Timeout；D后正常触发并恢复重放 | 调度不提前admit；纯Runner返回InputNotDue且无Decision；到期仍以同occurrence处理一次；已合法受理的Timeout在时钟回拨后不丢失 |
| R20 U 终点兼容 | v1 `z_bot → a_human`，无final_output；以及显式final_output图 | 完成所有节点和必要Judge | 前者完成后按旧降序回退得到z_bot文本并记录来源，后者用指定final_output；不依赖最后到达事件 |
| R21 U/I 模式与语义冻结 | 静态注册生产FSM语义v1，会话冻结v1；v2及其他模式未注册 | 新会话请求v2/其他模式；恢复旧会话；另移除v1 reducer而只保留兼容reader | 未注册选择在受理时拒绝；旧会话仍用生产v1并与原决策一致；缺v1时Block保留状态/义务，不换语义、不执行动作；不为通过此用例实现本批范围外的v2 Runner |
| R22 U/I 受信输入边界 | FSM收到普通Tool和结构化协同命令；另有已受理合法HumanReply但处理时已过期 | 归一化/受理、reduce及checkpoint恢复 | Tool仅展示，协同命令不派发；未授权/未注册控制事件在边界拒绝；受理合法不绕过Runner当前实例/期限校验，过期回复拒绝；相同冻结事实/state/now重放一致 |
| R23 U/I 数量边界 | N个并行Bot均可重试，旧远端Unknown，冻结预算足以接纳合法动作批次 | 同批N条Timeout，再重放该提交；跨Task继续监督旧dispatch | Runner产生N个Cancel及N个新Start，另计状态/输出等动作；Worker新did各占额一次，旧N个继续占额；Task本地终态/Cancel不释放，只有确定结束证明释放一次 |
| R24 I 执行配额耗尽 | Running Task的checkpoint可解释，已预留dispatch容量，累计交互达到冻结上限且有收尾预留 | 生产Runner产生超额交互候选；在独立收尾提交未知/半批取消effects时重启 | 整个超额候选不执行；仅当前Task以ExecutionQuotaExceeded收尾，旧确认内容保留，取消义务完整且可分批恢复；不重复reduce失败候选、不隔离整个Session、不把计数注入Runner；下一Task经R27容量准入后重新init，旧义务仍占额；超出已验证B或计数损坏走不变量故障路径 |
| R25 I 准入与回退 | 允许集合仅包含scopeA/groupA/FSM v1，另有旧引擎会话及已受理新引擎会话 | scopeB请求新模式；关闭总开关；重试已成功的会话创建；恢复已有会话 | 新模式未准入者明确拒绝；创建重试返回原冻结选择；已有新会话继续v1、旧会话仍用旧引擎；不双写/静默fallback，不丢远端义务；兼容reducer缺失按R21处理 |
| R26 U/I 事实与FSM失败策略 | 当前attempt可重试，已注册失败schema及封闭category集合；code可有界扩展 | 分别输入普通Failed+新code、permanent/Rejected+新code、未经本地Cancel的远端Canceled；另输入未知schema、已知schema内未知category、非法payload及Unknown执行状态 | 合法新code保留原码及unrecognized标记，普通Failed按剩余次数重试，permanent/Rejected直接FailRun，Canceled按既定节点失败策略；不从code猜策略；未知schema/category及非法payload拒绝并保留有界证据，Unknown不制造业务Error；相同规范事实重放结果一致 |
| R27 I 跨Task启动背压 | 前Task已归档但旧did仍占O，下一Task已受理且B<=C，O+B>C | 调度下一Task；释放部分旧义务仍不足；在等待截止前释放至足够；重复释放并重启 | 不足时始终Queued，无Runner Start/业务deadline；足够时原子预留B并仅Start一次，新did按R转O；旧义务不被抹除，证明仅释放一次；新Task超时仍可直接Cancel旧attempt并Start新attempt，无需等待取消确认 |
| R28 I 容量等待恢复与竞争 | Queued Task已固定等待截止D及发生身份，旧Task清理仍在推进 | 等待登记提交未知后接管；重复唤醒；D时释放容量；分别按序输入Cancel/内部到期及反序；另在D前已Start后收到旧到期 | 先确认提交，截止不续期且不受新profile影响；D时不能抢先Start；匹配到期由Worker冻结Rejected(StartCapacityTimeout)，Cancel先有效则Canceled，已冻结终态不改；无Runner FinishTask；已Start的旧到期不影响业务，旧义务回流/取消/归档不中断 |
| R29 U/I 容量上界与profile | 生产FSM validator、现有有效fixture/模板及1 MiB checkpoint profile；另有A→B均为无Judge的Bot且各2次、带Judge的Bot/Human及循环/分支图 | 生成§9.4报告，校验边界/溢出/排除原因；通过生产Runner与可控Connector记录合法执行的新did | A→B的B=4，覆盖实际同时3个义务的轨迹；Judge、Human无work、循环物化及互斥分支保守求和正确；每Task新did数不超其B；溢出或B>C/累计上限在受理拒绝；报告包含实际编码及收尾预留，拒绝和兼容性取舍有据，未验证profile不启用 |
| R30 I 生产provider门槛 | 生产组合选定DocumentCas/StrictLease/ZSet provider；另有各缺一种能力的配置 | 构造协调存储并执行2D列出的实际provider契约套件；覆盖lease过期/退休后迟到注册或更新 | 缺任一能力构造失败，不能降级开启新模式；实际provider全部通过才满足协调层启用条件，内存模型通过不可替代；Terminated证据不可逆，迟到请求不复活；第一批其余存储与宿主验收仍独立成立 |

纯Runner测试负责语义、身份、顺序、状态格式、输出意图和确定性；共享FSM validator负责静态容量计算及profile准入，不通过mock repository宣称持久性。Worker集成测试负责输入规范化、fencing、提交未知、outbox、真实公共投影、不可变归档、dispatch监督及启动预留/等待，凡执行群工作流必须调用生产Runner；未获准Start的场景断言Runner未被调用。可控Connector负责录制请求、按脚本产生确定事实/Unknown和可恢复交互，不要求接入全部真实远端协议。provider契约套件验证真实实现，不能用可控Connector或内存模型的结果代替。

实现时既有回归入口（在仓库的`apps/bcs`目录执行，命令本次未运行）：

```bash
cargo test --locked -p bcs-collaboration-runtime --test definition_validation
cargo test --locked -p bcs-collaboration-runtime --test fixed_loop_compiler
cargo test --locked -p bcs-collaboration-runtime --test fixed_loop_snapshot
cargo test --locked -p bcs-collaboration-runtime --test fixed_loop_context
cargo test --locked -p bcs-collaboration-runtime --test runtime_progression
```

新crate建立后新增`cargo test --locked -p bcs-group-runner`与生产组合conformance入口，把R01–R30映射到具体测试；不存在的crate/入口不能在文档交付时宣称执行通过。legacy差分测试比较支持范围、下游输入、outcome、结果和尝试次数，显式排除旧时钟/UUID及旧存储布局差异。E17既有受信intent/无name结果的兼容测试继续保留，不因共享模式控制边界而改写旧路径。

## 13. 文档交付与未验证边界

2026-11-18实际检查：本SPEC与导航共94个本地链接目标存在；15条证据中的具名源码/测试符号已核对，5个既有Cargo测试入口存在；Markdown围栏配对、尾随空白及`git diff --check`通过；公开内容人工检查及私有地址/凭据常见字面量检查未发现越界内容。两份依据的状态与PR合入后的proposed状态已核对。只读独立审阅提出的v1上游输出差异、未提交候选的时间语义和过早Timeout去重问题已修订并复核。改动仅本SPEC及导航，源码HEAD保持§2.1基线。

上述检查只证明文档和可追溯性；当前R01–R30是待实现的验收要求，不能作为生产能力已通过的证据。

未执行：Cargo构建/测试、生产Runner运行、真实provider故障实验、远端Agent/Judge联调、数据库迁移、部署或存量会话迁移。已有协调实现见§2.1/E16；第一批完整业务Worker与本批新增契约是否落地，须在实施时重新核对版本与验证，不能仅凭本稿启动生产流量。

本稿仅新增proposed设计与[SPEC导航](../README.md)，不把拟议行为提升为现有domain契约或已接受ADR。实现交付时更新实际owning contracts、共享编译器/Runner的Context Boundary与app导航，分别记录实现、合入、集成和部署。公共文档不含私有地址、凭据、企业运行态或未脱敏材料。

2026-11-19送审基线为GitHub `dev` 的 `4b17dacc62eef0ddd34310cce5eb5eac166c3da6`，保留§2.1的历史调研基线。第一批SPEC尚在独立PR，因此使用固定提交链接，不把其提交并入本PR。送审准备另核对其最新预算及独立终结约束，补充有效Runner状态预算和下一任务重新init的衔接；编译/推进等源码结论仍按原证据版本解释，实施前须重核实际集成版本。本次按请求使用`--no-verify`提交与推送，未运行Git hooks；不将跳过的检查记为通过。

2026-11-19评审修订：在[PR #4237](https://github.com/inclusionAI/Avernet/pull/4237)的原8项讨论达成处理共识后，补充模式选择/历史语义兼容、通用与FSM契约拆分、数量预算及依赖接入，保持直接超时重试策略。新增建议采用本稿选定的最小边界：受理授权事实不代替业务时效检查，正常执行额度耗尽由Worker独立收尾，不增加实时资源观察或Runner等待策略；新增R21–R26验收要求。准入与失败分类新评论正文不全，本次只明确可从标题及既有契约确认的最小边界，具体后续扩展仍待Reviewer补充，不据此增加灰度平台或其他模式实现。本次仍只修订proposed文档，未实现上述能力。

本轮文档检查：本SPEC与导航的100个本地链接目标存在，E01–E17及R01–R26编号连续且唯一，5个既有Cargo测试入口存在；新增E16/E17符号与源码对应，Markdown围栏/尾随空白/冲突标记、公开内容边界及`git diff --check`通过。仅SPEC内容变更，导航仍指向同一路径；未运行Cargo或运行态测试。沿用已授权的`--no-verify`提交/推送，Git hooks不计为通过。

2026-11-19后续评审修订：以上轮文档提交 `58d89d1fa676c416a40a5d04bddf136b9b2e5ee8` 为基线，完整读取新增评论并按三项已确认共识修订：schema内category封闭、code允许有界扩展；跨Task遗留义务由Worker在Start前预留/等待，容量不足不转为Runner业务失败，业务超时仍直接重试；#4145能力契约与生产provider就绪分开记录。新增§9.4、E18及R27–R30，细化R24/R26和2A/2D。2A须交付现有fixture/模板容量及实际编码报告后确定profile，本次未生成该实现期报告，也未给出未经验证的容量默认值。

本次文档检查：本SPEC与导航的108处本地链接均可解析到现有目标，E01–E18、R01–R30连续且唯一；新增E18构造能力检查、StrictLease语义及四个具名契约套件与源码对应，5个既有Cargo测试入口存在；Markdown围栏、尾随空白、冲突标记、公开内容边界及`git diff --check`通过。本次只修订SPEC并保持proposed；没有实现Runner/Worker/provider，也未运行Cargo或生产验证。提交和推送继续按请求使用`--no-verify`，不声称Git hooks通过。
