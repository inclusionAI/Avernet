# Pure FSM Group Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用纯 DistRunnerCoreService 执行首期 BotTask 无环协作图，保留稳定调用身份、多轮输入、最终结果及失败/取消语义。

**Architecture:** compile 校验冻结定义，reduce 只读取给定状态/输入/时间并产生 ActionIntent；IO、提交、Judge 和取消 RPC 由 Worker/执行器完成。复用旧定义的可支持规则，不调用旧 CollaborationRuntime。

**Tech Stack:** bcs-domain::StateMachineDefinition、bcs-dist-model、bcs-service-api、serde；无 Tokio/HTTP/DB 生产依赖。

---

前置 D01～D06 及 C01 完成；R01 与 G01 共同消费同一 `GroupWorkflowValidatorCoreService` 结果，不各自实现能力白名单。可与 Gateway、存储并行。R01～R03 是首期必需；R04 是明确可选扩展，不交付时发布能力拒绝 Judge。

## R01：冻结工作流编译及版本化 RunnerState

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/{Cargo.toml,CONTEXT.md,src/lib.rs,src/compile.rs,src/state.rs,src/input.rs}`。
- Consume：D0 已定义 `RunnerInput::Start { input: GroupRoundInput }`、`ActionIntent::FinishTask { tid, status, result: PublicTaskResult }`；R01 不再改公共 trait。丢弃证明由 Worker gate 消费，不传入 Runner。
- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/tests/{validation,serialization,conformance_dist_runner}.rs`、`tests/common/mod.rs`；conformance 调用 `dist_runner_core_service_contract_tests`。
- Modify：`apps/bcs/Cargo.toml`。
- Read：`apps/bcs/crates/contracts/bcs-domain/src/collaboration.rs`、`apps/bcs/crates/services/bcs-collaboration-runtime/src/{definition,progression}.rs`。

- [ ] **1. 定义测试 fixture `two_nodes()`、`fanout_join()`、`node_input()`、`context_at(ms)`。** 使用真实 StateMachineDefinition 字段创建、固定参与者 binding、节点 a/b/final、final_output=true；测试不支持节点、缺失/多个最终节点、不可达最终节点、环、悬空 binding 均拒绝。

```rust
#[test]
fn rejects_human_input_before_execution() {
    let mut workflow = common::two_nodes();
    workflow.nodes.get_mut("a").unwrap().kind = bcs_domain::StateMachineNodeKind::HumanInput;
    assert!(common::shared_workflow_validator().validate(&workflow).is_err());
}
```

- [ ] **2. RED：** `cargo test -p bcs-dist-runner-fsm --test validation --test serialization`。
- [ ] **3. compile 仅消费 C01 返回的 ValidatedGroupWorkflow；拓扑、前驱、分支可达集和最终节点索引由同一 shared validator 生成，Runner 校验 digest/compiler version，不再各自推导节点支持列表。** 支持 BotTask 无环图；不复制旧 validator 中与本期不兼容的隐藏 default。只有一个显式最终输出节点，所有允许成功路径都能给出它的结果；不支持 guards/action/expression 时明确 CapabilityUnsupported，不能忽略。

状态包含 tid/execution identity、各节点 Pending/Running/Completed/Skipped/RetryScheduled、attempt、固定 deadline、已冻结完成决定及引用。节点内容放不可变 payload 引用或受限 typed 输入，不将全量文本流放 RunnerState。

```rust
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum NodePhase {
    Pending,
    Running { attempt: u32, did: bcs_dist_model::Did },
    RetryScheduled { attempt: u32, due: bcs_dist_model::TimestampMillis },
    Completed { result: bcs_dist_model::Payload },
    Skipped,
}
```

- [ ] **4. GREEN：** 序列化重载后状态相等，未知 format_version 拒绝；首轮固定 workflow digest，下一轮不能被当前目录覆盖。提交 `feat(bcs): compile frozen group workflows for pure execution`。

## R02：纯归约、稳定 dispatch、分支和汇合

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/src/{reduce,dispatch,progression}.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/tests/{determinism,progression,multi_round}.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-runner-fsm/tests/common/mod.rs`，完整创建 D0 SessionSpec/Context/Start fixture。

- [ ] **1. 测试 Start 两次归约同输入得到完全相同 Decision；重复 Final 不启动第二次后继，汇合在所有所需上游完成后只派发一次。** 改变 map 插入顺序不改变动作顺序；选择分支后其余路径 Skipped，不错误跳过已有 Running 节点。

```rust
#[test]
fn same_input_produces_same_decision() {
    use bcs_service_api::core::dist_runner::DistRunnerCoreService;
    let fixture = common::runner_fixture();
    let runner = bcs_dist_runner_fsm::FsmRunner::default();
    let state = runner.init(&fixture.context).unwrap();
    let inputs = vec![fixture.start_input()];
    assert_eq!(
        runner.reduce(&fixture.context, &state, &inputs).unwrap(),
        runner.reduce(&fixture.context, &state, &inputs).unwrap(),
    );
}
```

- [ ] **2. RED：** `cargo test -p bcs-dist-runner-fsm --test determinism --test progression --test multi_round`。
- [ ] **3. 实现 init/reduce，固定 node 顺序和 dispatch ID 算法。** did 从冻结 tid/node/attempt 组成受版本控制的编码，不使用 UUID/time。最终文字必须来自 explicit final node；中间 delta/thinking 不改变决策。节点 deadline=min(GroupRoundInput.execution_deadline,首次持久化 node_started_at+timeout)，RunnerState 保存 node/retry 的绝对时间；Start 等待和接管时不以 EvaluationContext.now 重算整轮期限。FinishTask 直接携带 D01 PublicTaskResult，Artifact ID 由固定 tid/最终节点/position 的版本化算法生成，Worker 不补 ID/顺序/模式。

```text
Start: validate typed GroupRoundInput, frozen workflow/capability digest and absolute deadline -> mark ready roots -> emit StartDispatch
Final: validate did/node/attempt -> store result -> select outcome -> mark skipped branch
       -> emit only newly ready successors
Duplicate/stale final: retain state, no second dispatch
No remaining work + final result valid: emit FinishTask with fixed typed PublicTaskResult
```

多轮输入 typed 保存 session.initial_goal/current Parts+data/previous_success_result；用节点 input fixture 断言真实可消费，不只在元数据挂引用。输入已由 Gateway 在 claim 时固定匹配 generation 的背景，Runner 不用当前目录重选它；测试以 T1 为上一成功轮时不能退回 T0。相同 Session 新 Task 新执行，接管保留原 execution。单纯 `opening_message` 不替代 input。

- [ ] **4. GREEN 与提交：** 所有 target 非零通过，运行 `cargo test -p bcs-dist-runner-fsm`；提交 `feat(bcs): reduce group workflow inputs deterministically`。

## R03：超时、重试、取消与公共投影

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/src/{failure,cancel,projection,finish}.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/tests/{failures,cancellation,public_output}.rs`。

- [ ] **1. 使用固定 clock 构造 deadline 前后输入。** 有界 max_attempts、旧 attempt 迟到不复活、意外 InputRequired/AuthRequired 失败收尾；取消后不产生新 StartDispatch，成功已冻结的取消不能改写结果。
- [ ] **2. RED：** `cargo test -p bcs-dist-runner-fsm --test failures --test cancellation --test public_output`。
- [ ] **3. reduce 只产生持久控制动作，等真实停止证据后完成取消。** 未知远端继续作为 obligation；超时失败决定与 context 隔离由 Worker 桥接记录。公共投影在写 Feed 前生成，只有固定阶段模板与最终节点允许字段。

```text
Cancel + Running:
  close new dispatch admission
  emit RequestCancel for every unsettled did
  wait for definitive dispatch terminal facts
  if all stopped -> fixed Canceled finish
Timeout/unsupported wait:
  freeze Failed closing reason, request stop
Thinking/Tool:
  no public ActionIntent::Show and no decision dependency
```

不将任意 Agent 错误正文/堆栈放公开结果。includeProgress=false 只抑制阶段消息，状态/最终 Artifact 仍产生；JSON 结果保持原类型，不能先转字符串再当 data。每个 PublicPart 的 filename/media_type/metadata 独立于 Artifact 顶层属性；公开策略允许的值按原 Part 保留，私有 metadata 不透传，固定结果 digest 包含这些属性。public_output 测试同时断言允许属性未丢失与禁止属性未泄漏。

- [ ] **4. GREEN：** 在三种输入顺序（Final→Cancel、Cancel→Final、Unknown→Deadline）断言一个终态决定，超时不假造远端取消；提交 `feat(bcs): bound group failures and filter public output`。

## R04（可选）：Judge 动作化执行

**Files**

- Create：`apps/bcs/crates/services/bcs-dist-runner-fsm/src/judge.rs`、`tests/judge.rs`。
- Create：`apps/bcs/crates/services/bcs-dist-worker/src/group_call/judge_effect.rs`、`tests/group_judge.rs`。
- Modify：`apps/bcs/crates/services/bcs-dist-runner-fsm/src/{reduce,state}.rs`。
- Contracts：使用 D04 的 EvaluateJudge/JudgeResult，不能自行添加第二套形状。

- [ ] **1. 写 reducer 测试：节点结果到达只产 EvaluateJudge，不调用 evaluator。** Worker 测试真实 recording JudgeEvaluatorPort 只被同 attempt 的固定请求调用；响应丢失时读取持久结果而非改变输入重新判断。
- [ ] **2. RED：** `cargo test -p bcs-dist-runner-fsm --test judge`；Worker crate 由基础依赖创建后运行 `cargo test -p bcs-dist-worker --test group_judge`。
- [ ] **3. 执行器保存 intent、结果和回流身份。** EvaluateJudge 使用 effect identity、固定 prompt/digest、deadline；outcome 回流只能推进同 Task/node/attempt。真实评估结果未知时按原能力/存储协议恢复，不能宣称外部 LLM exactly-once。

```text
checkpoint(Judging, fixed effect)
  -> execute evaluator outside transaction
  -> persist result with same attempt/content identity
  -> durable JudgeResult input
  -> reduce selects branch
cancel -> stop scheduling new judge; retain unsettled judge obligation until resolved
```

- [ ] **4. GREEN 与能力登记：** 单测和 Worker failure replay 测试通过后才在 FrozenGroupCapabilities 开放 Judge；不交付本任务时 G01/R01 必须拒绝含 Judge 群。提交 `feat(bcs): execute group judges through durable effects`。

本子计划 R01～R03 依赖 D0+C01；R04 另依赖真实 Worker。`cargo test -p bcs-dist-runner-fsm` 通过证明纯执行语义，不证明 Worker 所有权、IO 或持久化恢复。
