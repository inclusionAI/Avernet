# BCS State Machine 固定 Loop 与上一轮结果传递设计

- 日期：2026-09-02
- 状态：实施中；S1 已完成，S2 snapshot/Store 与历史 plan 读取已实现、验收中，v2 可通过显式实验开关试运行，生产发布验收未完成
- 开发追踪：[固定 Loop tasks](2026-09-02-bcs-fixed-loop-state-machine-tasks.md)
- 2026-09-14 修订：事务边界按现有 State Machine 分步提交实现收敛；break/exhausted targets 必须非空。
- 2026-09-14 补充：continue 集合非空、break 集合可空；补齐 HumanInput LoopContext 和编译边/Graph 的 loop_route 合同。
- 范围：`src/bcs` 自定义协作 State Machine Definition、编译、执行、持久化、事件与运行图
- 相关文档：
  - `docs/arch/arch.rules.md`
  - `docs/arch/ci.enforce.md`
  - `docs/arch/protocol-contract-tests.md`
  - `src/bcs/docs/superpowers/specs/2026-08-19-bcs-collaboration-state-persistence-fo-rerun-design.md`
  - `src/bcs/docs/superpowers/specs/2026-08-22-v1-collaboration-definition-validate-design.md`
  - `src/bcs/docs/plans/2026-07-27-human-input-dingtalk-im.md`
  - `src/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md`

## 1. 摘要

本文为 BCS 自定义协作 State Machine 增加第一期固定 Loop 能力。Definition 作者可以声明一个有最大
迭代次数、固定 body、唯一入口和唯一结果节点的结构化 Loop。每轮执行相同的 body，根据结果节点的 outcome
决定继续下一轮、退出 Loop，或在达到最大迭代次数后进入显式 exhausted 分支。

第一期只解决下一轮入口节点感知上一轮结果的问题，不建设通用可变 State：

- 上一轮结果的 source of truth 是上一轮 `result_node` 对应 Node Run 已持久化的 `outcome`、
  `artifact_text` 和 `completed_at`；
- Runtime 以只读 `LoopContext` 将该结果注入下一轮入口节点；
- 不新增 roster、scoreboard、global variables 或任意 JSON state mutation；
- 不允许 Bot 动态生成节点、修改 Definition 或改变参与者；
- 不直接启用通用 `graph_mode: cyclic`，也不允许任意 `goto`；
- Authoring Definition 使用结构化 Loop，Runtime 将它确定性编译成普通 acyclic execution plan 后执行。

该设计保留当前 State Machine 的关键语义：一个 execution node 在一个 Run 中只有一条 Node Run；
`attempt` 继续仅表示同一 execution node 的失败重试；delivery correlation、CAS、分支 skip、超时、取消、
Judge 和 FO 均使用展开后的 execution node identity。

## 2. 背景与当前约束

当前合同已经枚举 `acyclic`、`cyclic`、`event_driven` 和 `hierarchical` graph mode，但运行时只接受
`acyclic`。当前实现还依赖以下 DAG 不变量：

1. Definition 只有一个零入度入口和一个 terminal node；
2. 每个 Definition node 在 Run 创建时物化一条 Node Run；
3. Node Run 的持久化唯一键是 `(env, run_id, node_id)`；
4. `attempt` 是该 Node Run 的外部执行重试编号；
5. target 只有在其静态 upstream 全部为 `Completed` 或 `Skipped` 后才可派发；
6. Run 在所有 Node Run 均为 `Completed` 或 `Skipped` 后完成；
7. 当前 Definition preview 和 Run graph 都按 DAG 布局；
8. 事件、delivery correlation、Judge output 和消息 metadata 都使用 `node_id + attempt` 关联执行。

仅解除 cycle 校验或允许 transition 回到一个已完成 node，会立即破坏上述不变量：第二轮会覆盖第一轮状态，
旧 attempt 的迟到事件可能污染新一轮，静态 upstream 无法表达“同一轮”的 join，FO 也无法判断应恢复哪一次
node activation。

本文选择 compile-to-DAG：Loop 是 Authoring Contract；展开后的 acyclic execution plan 是 Runtime
Contract。第一期不把当前运行时升级为通用 token/activation engine。

## 3. 目标

### 3.1 功能目标

1. Definition 可以声明固定 body 的有界 Loop。
2. Loop 必须有一个或多个 continue outcome，可以有零个或多个 break outcome，以及一个系统生成的 exhausted outcome。
3. 下一轮入口节点可以稳定读取上一轮结果节点的 output 和 outcome。
4. 第一轮入口节点能够明确知道自己没有上一轮结果。
5. 每一轮 body node 都有独立 Node Run、attempt、deadline、artifact 和 delivery correlation。
6. Loop 内继续支持现有 BotTask、HumanInput、Judge、并行 fan-out/join 和分支选择；具体支持范围仍受当前节点能力限制。
7. Definition validation、运行图、事件和消息历史可以区分 logical body node、iteration 和 execution node。
8. Run snapshot 和 rerun 使用同一份不可变 execution plan，进程重启或版本升级不能改变历史 Run 的展开结果。
9. 提前 break 后未执行的后续 iteration 必须确定性进入 `Skipped`，使现有 Run completion 规则保持成立。
10. 达到最大 iteration 后必须走显式 exhausted 分支，不能静默成功、无限执行或猜测业务结果。

### 3.2 可靠性目标

1. 同一 execution node attempt 最多产生一次有效 completion transition。
2. 旧 iteration 或旧 attempt 的迟到 terminal event 不能推进当前 iteration。
3. FO 恢复只依赖持久化 execution plan、Node Run、definition snapshot 和既有 checkpoint/phase，不依赖进程内 Loop 计数。
4. 同一入口节点的 retry 必须看到完全相同的 `LoopContext`。
5. result node completion 与后续 progression 使用现有单节点 CAS 和分步提交路径；不要求 completion、全部 skip 和下游 frontier 同事务提交。FO 从持久化结果补做未完成推进，不为 Loop 建立第二套恢复路径。

## 4. 非目标

第一期明确不支持：

1. 任意 backward edge 或 `goto`；
2. 通过 `loopable: true` 一类目标节点标记隐式声明 Loop；
3. 无最大次数的 `while`；
4. guard expression、脚本、用户代码或任意条件求值器；
5. 嵌套 Loop、重叠 Loop、交叉 Loop 或从外部跳入 body 中部；
6. 动态增加、删除或替换 body node；
7. 动态参与者、动态 assignee、`for_each` 或运行时 fan-out；
8. roster、计分、预算、阶段等通用 mutable Run State；
9. Bot 输出 state patch、plan proposal 或新的 Workflow topology；
10. 将全部历史 iteration output 自动拼入下一轮 prompt；
11. 从某一 iteration 局部 rerun 或复用历史 artifact；
12. 原生 cyclic graph 的 token、epoch、activation 或动态 join 语义；
13. 以 `attempt` 表示 iteration；
14. 依赖 Definition 当前版本重新编译历史 Run；
15. continue 集合为空的 break-only Loop；只执行一次并退出的需求使用普通节点。

通用 State、动态集合执行和受控动态 Workflow 必须另立设计，不能通过 `extensions` 或隐式 prompt 协议塞入本期 Loop。

## 5. 术语

### 5.1 Authoring Definition

用户提交并版本化保存的 Collaboration Definition。它可以包含结构化 `loop` node 及其内部 body；body 只包含普通可执行节点，不允许再次声明 Loop。

### 5.2 Execution Plan

由固定版本编译器从 Authoring Definition 生成的扁平 acyclic graph。Runtime、Repo、delivery、event 和
FO 只执行该计划。Execution Plan 是 Run snapshot 的组成部分。

### 5.3 Loop Node

Authoring Definition 中的结构化控制节点。它不绑定 Bot，不产生外部 delivery，也不直接拥有 Node Run。
编译器用展开后的 body nodes 替换它。

### 5.4 Body Node

Loop 内每轮重复执行的 logical node。`body_node_id` 只在所属 Loop 内唯一。

### 5.5 Iteration

Loop body 的一次完整执行。对外展示和 Definition 语义使用从 1 开始的编号。

### 5.6 Execution Node

Execution Plan 中实际物化 Node Run 的节点。Loop body node 在每个 iteration 对应不同的
`execution_node_id`。

### 5.7 Result Node

Loop body 的唯一终点。它的最终 `artifact_text` 和 outcome 构成本轮 `PreviousLoopResult`，其 outcome
决定 continue 或 break。

### 5.8 PreviousLoopResult

上一轮 result node 的不可变持久化投影。它不是通用 State，也不允许当前 node 修改。

### 5.9 Attempt

同一 execution node 的外部执行尝试编号，继续从 0 开始。Iteration 和 attempt 是正交维度。

## 6. 核心不变量

1. 一个 Loop 必须有固定、非空且 acyclic 的 body。
2. 一个 Loop 必须有唯一 entry node 和唯一 result node。
3. 一个 Loop 必须有显式、有限的 `max_iterations`。
4. 一个 body result outcome 必须且只能归入 continue 或 break 集合之一。
5. `exhausted_outcome` 由 BCS 生成，不得同时是 result node 的 Judge outcome。
6. Body node 不能直接引用 Loop 外节点；外部节点不能直接引用 body node。
7. 一个 execution node 只属于一个 iteration；一个 Node Run 只属于一个 execution node。
8. `attempt` 不随 Loop 继续而递增；下一轮使用新的 execution node，attempt 重新从 0 开始。
9. 下一轮 `PreviousLoopResult` 必须来自 execution plan 指定的上一轮 result execution node，不能通过“最近完成节点”猜测。
10. PreviousLoopResult 的 output、outcome 和 completion time 均来自持久化事实。
11. Run 创建后 Authoring Definition、Execution Plan、participant binding 和 Loop iteration count 均不可变。
12. 提前 break 后尚未执行的 iteration 节点必须全部为 `Skipped`。
13. Compile 后的 graph 必须继续满足当前 acyclic runtime 的 entry、reachability、terminal 和 completion 不变量。
14. 恢复历史 Run 时 execution plan snapshot 是唯一权威，不得 fallback 到使用当前 compiler 重新编译。
15. 每个 break outcome 和 exhausted outcome 的外层 transition 必须至少有一个合法 target；不允许用空 targets 表示退出或耗尽。
16. `continue_outcomes` 必须非空，`break_outcomes` 可以为空；不通过编译后裁剪不可达 iteration 来支持 break-only Loop。

## 7. Authoring Contract

### 7.1 版本与能力

包含 Loop 的 Definition 使用：

```yaml
runtime:
  kind: state_machine
  state_machine:
    version: 2
    graph_mode: hierarchical
```

持久化 Domain Definition 的 `api_version` 继续是 `bcs.collaboration/v1`；现有 authoring YAML 仍省略由
BCS 管理的顶层 `api_version`、`id` 和 `version`。`state_machine.version: 2` 表示 Definition node
contract 增加结构化 control node。
`graph_mode: hierarchical` 表示 Authoring Definition 包含 Loop 内部的 body 层级，不表示支持 Loop 嵌套。编译后的 Execution Plan 固定为
`acyclic`。

Runtime 必须推导并校验以下 server features：

```text
state_machine.version.2
state_machine.graph_mode.hierarchical
state_machine.node.kind.loop
state_machine.loop.mode.fixed
state_machine.loop.previous_result
```

已有 `version: 1 + graph_mode: acyclic` Definition 行为完全不变。

### 7.2 Loop schema

`StateMachineNodeKind` 增加 `loop`。只有 `kind: loop` 可以携带 `loop` 字段；普通 node 不得携带该字段。

```yaml
rounds:
  kind: loop
  display_name: 多轮讨论
  loop:
    mode: fixed
    max_iterations: 5
    entry_node: discussion
    result_node: decision
    continue_outcomes: [continue]
    break_outcomes: [completed]
    exhausted_outcome: exhausted
    nodes:
      discussion: {}
      decision: {}
  transitions:
    completed:
      targets: [publish]
    exhausted:
      targets: [manual_finish]
```

建议新增 Domain Contract：

```rust
pub enum StateMachineNodeKind {
    BotTask,
    GroupChat,
    HumanInput,
    ToolAction,
    SubStateMachine,
    Loop,
}

pub struct FixedLoopDefinition {
    pub mode: FixedLoopMode,
    pub max_iterations: u32,
    pub entry_node: String,
    pub result_node: String,
    pub continue_outcomes: Vec<String>,
    pub break_outcomes: Vec<String>,
    pub exhausted_outcome: String,
    pub nodes: BTreeMap<String, StateMachineNodeDefinition>,
}

pub enum FixedLoopMode {
    Fixed,
}
```

`StateMachineNodeDefinition` 增加：

```rust
pub loop_definition: Option<FixedLoopDefinition>
```

字段的 wire name 为 `loop`。Loop node：

- 必须有非空 `display_name`；
- 不允许 `assignee`、`instruction`、`notification`、`judge`、`action`、`output_contract`、
  `node_timeout_ms`、`max_attempts`、`visibility` 或 `final_output`；
- 必须有 `loop` 和外层 `transitions`；
- 外层 transition 只处理 break outcomes 和 exhausted outcome；continue outcomes 是 Loop 内部控制流。

`continue_outcomes` 和 `break_outcomes` 均须显式声明。前者至少包含一个 outcome，后者允许 `[]`；break 为空
时外层只声明 exhausted transition。这样每个 iteration 都有静态 continue 路径可达，不需要为 break-only
定义增加特殊展开或不可达节点裁剪规则。

### 7.3 完整示例

```yaml
name: Bounded multi-round collaboration
participants:
  coordinator:
    display_name: Coordinator
    required: true
  speaker:
    display_name: Speaker
    required: true

runtime:
  kind: state_machine
  state_machine:
    version: 2
    graph_mode: hierarchical
    projection:
      default_visibility: private
    defaults:
      node_timeout_ms: 60000
      max_attempts: 2
    nodes:
      prepare:
        kind: bot_task
        display_name: Prepare
        assignee:
          type: bot_binding
          binding: coordinator
        instruction: Prepare the collaboration input.
        transitions:
          complete:
            targets: [rounds]

      rounds:
        kind: loop
        display_name: Multi-round discussion
        loop:
          mode: fixed
          max_iterations: 5
          entry_node: discussion
          result_node: decision
          continue_outcomes: [continue]
          break_outcomes: [completed]
          exhausted_outcome: exhausted
          nodes:
            discussion:
              kind: bot_task
              display_name: Discuss current round
              assignee:
                type: bot_binding
                binding: speaker
              instruction: Use the Loop Context and produce this round's contribution.
              transitions:
                complete:
                  targets: [decision]

            decision:
              kind: bot_task
              display_name: Decide current round
              assignee:
                type: bot_binding
                binding: coordinator
              instruction: Summarize this round and decide whether another round is required.
              judge:
                type: llm
                criteria:
                  - Whether the collaboration has reached its terminal condition
                outcomes: [continue, completed]

        transitions:
          completed:
            targets: [publish]
          exhausted:
            targets: [manual_finish]

      manual_finish:
        kind: human_input
        display_name: Resolve exhausted loop
        instruction: The loop reached its maximum iterations. Provide the final decision.
        node_timeout_ms: 600000
        transitions:
          complete:
            targets: [publish]

      publish:
        kind: bot_task
        display_name: Publish result
        assignee:
          type: bot_binding
          binding: coordinator
        instruction: Publish the final result.
        final_output: true
```

### 7.4 Result node 语义

Result node 是 body 内的特殊 terminal：

- 它不定义 body 内 `transitions`；
- 如果配置 Judge，Judge 的所有 outcomes 必须被 Loop 的 continue/break 集合完整分区；
- 如果没有 Judge，它只有现有 `complete` outcome；必须配置 `continue_outcomes: [complete]` 和 `break_outcomes: []`；
- Result node 不能是 `final_output`；Run 的唯一 final output node 必须位于 Loop 外；
- Result node 失败、超时或 Judge 失败时，沿用普通 node retry/failure，不能被解释为 continue、break 或 exhausted。

无 Judge 的 result 固定执行到最大次数，再走显式 exhausted 分支。需要按结果提前退出时配置 Judge；只执行一次
并退出时使用普通节点。`max_iterations: 1` 仍合法，其 continue outcome 直接走 exhausted，不代表允许空 continue 集合。

### 7.5 无 Judge 的固定次数示例

以下片段可替换 §7.3 的 `rounds` 节点，沿用该例的 `prepare`、`manual_finish`、`publish` 和 participant 定义：

```yaml
rounds:
  kind: loop
  display_name: Fixed three rounds
  loop:
    mode: fixed
    max_iterations: 3
    entry_node: discussion
    result_node: discussion
    continue_outcomes: [complete]
    break_outcomes: []
    exhausted_outcome: exhausted
    nodes:
      discussion:
        kind: bot_task
        display_name: Discuss current round
        assignee:
          type: bot_binding
          binding: speaker
        instruction: Use the Loop Context and produce this round's contribution.
  transitions:
    exhausted:
      targets: [manual_finish]
```

前两轮 `complete` 进入下一轮；第三轮仍保存真实 `complete` outcome，并通过 exhausted route 进入人工收尾。

## 8. Definition 校验

Validation 分为 Authoring validation、Loop structural validation、compile validation 和 deployment capability validation。

### 8.1 Authoring validation

1. 包含 Loop 的 Definition，其 `state_machine.version` 必须为 2；
2. 包含 Loop 的 Definition，其 `graph_mode` 必须为 `hierarchical`；
3. `loop.mode` 第一期只能为 `fixed`；
4. `max_iterations` 必须大于 0；
5. `entry_node`、`result_node`、outcomes 和 body node IDs 必须非空；
6. `continue_outcomes` 必须是非空列表，`break_outcomes` 必须是列表但可以为空；列表内部不得重复；
7. continue 与 break 集合不得相交；
8. exhausted outcome 不得出现在 continue 或 break 集合；
9. Loop body 不得为空；
10. Loop node 的外层 transitions 不得包含 continue outcome；
11. 外层 transition keys 必须恰好为 break 集合与 exhausted outcome 的并集，每个 `targets` 必须是非空列表；
12. Loop node 不得携带普通 executable node 字段。

continue 集合缺失或为空时返回 `INVALID_DEFINITION`，path 指向该 Loop 的 `loop.continue_outcomes`；
即使 `max_iterations: 1` 也不例外。break 空列表是合法配置，不是缺失 outcome 的错误。

### 8.2 Body structural validation

1. `entry_node` 和 `result_node` 必须引用 body 中存在的 node；
2. Body node kind 第一期只允许当前 Runtime 已实现的 executable kinds；
3. Body 中不得出现 Loop、SubStateMachine 或其他未实现 control node；
4. Body 必须 acyclic；
5. Body 必须只有一个零入度 node，且等于 `entry_node`；
6. `result_node` 必须是 body 内唯一 terminal；
7. 除 result node 外的所有 body node 必须定义现有合法 transition targets；
8. 所有 body node 必须从 entry 可达且能够到达 result；
9. Body transition target 必须位于同一 body；
10. Body node 不得 `final_output: true`；
11. Body 内 HumanInput ordering、participant assignment、Judge 和 timeout 继续遵守现有规则。

### 8.3 Outcome validation

1. Result node 有 Judge 时，Judge outcomes 必须等于 continue outcomes 与 break outcomes 的并集；
2. Result node 无 Judge 时，continue 集合必须恰好为 `[complete]`，break 集合必须为空；将 `complete` 归入 break 必须拒绝；
3. 非 result body node 的 Judge outcome 和 transition 继续一一对应；
4. exhausted 是 Runtime outcome，不能由 Result node Judge 返回；
5. 外层 break/exhausted transition 的 `targets` 必须非空，每个 target 必须是 outer graph 中存在的 node；缺失或空列表返回 `INVALID_DEFINITION`，diagnostic path 指向对应 `transitions.<outcome>.targets`。即使当前 DAG validator 接受空 targets，Loop authoring validator 也必须拒绝。

### 8.4 Resource validation

Runtime 配置至少提供：

```text
max_fixed_loop_iterations
max_fixed_loop_body_nodes
max_compiled_state_machine_nodes
max_compiled_state_machine_bytes
```

Definition validation 必须在落库和启动前检查：

```text
compiled_nodes = outer_executable_nodes
               + Σ(loop.body.nodes × loop.max_iterations)
```

超过任一限制时返回稳定的 `INVALID_DEFINITION` diagnostic，不能在 Run 创建到一半后失败。具体默认值属于配置，
不写入 wire contract。

### 8.5 Compile validation

编译完成后，必须对 Execution Plan 再运行现有 DAG invariants：

- exactly one entry；
- exactly one final output terminal；
- acyclic；
- all nodes reachable from entry；
- all nodes can reach final；
- participant bindings valid；
- generated execution node IDs unique and within persistence/API length limits；
- no reserved ID collision。

只有 Authoring 和 Execution Plan 两层都合法时，Definition validation 才返回 `valid: true`。

## 9. Execution Plan 编译

### 9.1 编译器版本

编译器必须有稳定版本，例如：

```text
bcs.fixed-loop.compiler/v1
```

版本是 snapshot 数据的一部分。改变展开 topology、generated ID、metadata 或 artifact projection 均需要新 compiler
version；不能在原版本下静默改变历史语义。

2026-09-14 S1 实现记录：compiler v1 使用 `ln-` + SHA-256 前 128 bits 的十六进制编码（共 35 ASCII 字节）。
哈希输入为固定 namespace `bcs.fixed-loop.compiler/v1/execution-node`、一个 NUL 分隔字节和
`(definition_id, definition_version, loop_node_id, iteration, body_node_id)` 的紧凑 JSON tuple。
执行节点/metadata 使用有序映射，edge metadata 按 source/outcome/target 排序；plan content hash 使用完整
SHA-256。该选择由 compiler golden 固定，运行时与客户端不得反向解析 ID。

### 9.2 Execution node identity

普通 outer node 的 execution node ID 与 logical node ID 相同。Loop body node 的 execution node ID 由以下 tuple
确定性生成：

```text
(definition_id, definition_version, loop_node_id, iteration, body_node_id)
```

具体生成算法应使用固定 namespace 的 UUIDv5 或等价的稳定哈希，并增加 `ln-` 前缀，使 ID 长度固定且不与 authoring
node ID 冲突。Runtime 不得通过解析 execution node ID 恢复 Loop metadata；完整映射必须保存在 execution plan。

```rust
pub struct CompiledNodeMetadata {
    pub execution_node_id: String,
    pub definition_node_id: String,
    pub loop_id: Option<String>,
    pub iteration: Option<u32>,
    pub max_iterations: Option<u32>,
    pub is_loop_entry: bool,
    pub is_loop_result: bool,
    pub previous_result_node_id: Option<String>,
}
```

Execution Plan 同时为每条展开后的 edge 固化 artifact projection role 和可选 Loop 路由说明：

```rust
pub enum CompiledArtifactProjection {
    Artifact,
    ControlOnly,
}

pub enum StateMachineLoopRouteKind {
    Continue,
    Break,
    Exhausted,
}

pub struct StateMachineLoopRoute {
    pub kind: StateMachineLoopRouteKind,
    pub logical_outcome: String,
}

pub struct CompiledEdgeMetadata {
    pub source_execution_node_id: String,
    pub outcome: String,
    pub target_execution_node_id: String,
    pub artifact_projection: CompiledArtifactProjection,
    pub loop_route: Option<StateMachineLoopRoute>,
}
```

`loop_route.kind` 的 wire values 为 `continue`、`break`、`exhausted`。只有 result execution node 的 Loop
控制出口 edge 携带该对象；body 内 edge、outer graph 指向第一轮 entry 的 edge、普通 outer edge 均省略。
每条控制出口 edge 都必须在编译时固化 `kind` 和 `logical_outcome`，不能让 API/前端事后推断。

| 控制出口 | edge.outcome | loop_route.kind | loop_route.logical_outcome |
| --- | --- | --- | --- |
| result(i) continue -> entry(i + 1)，i < N | result 的原 continue outcome | `continue` | 同一个 continue outcome |
| result(i) break -> outer targets | result 的原 break outcome | `break` | 同一个 break outcome |
| result(N) continue -> exhausted targets | result 的原 continue outcome | `exhausted` | Definition 的 `exhausted_outcome` |

`logical_outcome` 是 authoring 层路由名称；跨轮 continue 没有外层 transition，因此沿用原 continue outcome。
多个 continue outcomes 在最后一轮可以有不同 `edge.outcome`，同时映射到相同 exhausted logical outcome。
`edge.outcome` 始终用于与 Node Run 的真实 outcome 匹配；`loop_route` 仅用于说明和展示该编译结果，不参与
第二次选路，不改写 Node Run outcome，也不引入额外持久化行或逐 edge 运行状态。

`ControlOnly` 不是另一种 progression edge。它仍参与 outcome 选择、upstream barrier、skip propagation、
`predecessor_node_ids` 和恢复计算，仅禁止把 source artifact 放进 target 的通用 `[Upstream Outputs]`。

### 9.3 展开规则

对一个 `max_iterations = N` 的 Loop：

1. 为每个 body node 创建 N 个 execution node；
2. Body 内 edge 在每个 iteration 内原样复制；
3. Outer graph 指向 Loop node 的 edge 改为指向 iteration 1 的 entry execution node；
4. 对 `i < N`，result(i) 的 continue outcome 指向 entry(i + 1)，标记为 `ControlOnly`，并设置 `loop_route.kind=continue`、`logical_outcome=原 continue outcome`；
5. 对任意 i，result(i) 的 break outcome 指向 Loop 外层对应 transition targets，设置 `loop_route.kind=break`、`logical_outcome=原 break outcome`；break 集合为空时不生成这种 edge；
6. 对 `i = N`，result(N) 的 continue outcome 指向 exhausted transition targets，设置 `loop_route.kind=exhausted`、`logical_outcome=exhausted_outcome`，edge 的真实 outcome 保持不变；
7. Result node 的 output/outcome 保留在 result(i) Node Run；
8. Loop control node 本身不进入 Execution Plan，不创建 Node Run；
9. 提前 break 时，现有 branch skip 逻辑负责将未选择的 continue subtree，即所有未来 iterations，标记为 Skipped；
10. Outer successor 仍通过所有静态 upstream Completed/Skipped barrier 满足当前 join 规则。

例如：

```text
prepare
   |
   v
discussion(1) -> decision(1)
                    | completed -------------------------> publish
                    | continue
                    v
discussion(2) -> decision(2)
                    | completed -------------------------> publish
                    | continue
                    v
                  ...
                    |
discussion(N) -> decision(N)
                    | completed -------------------------> publish
                    | continue
                    v
                manual_finish --complete---------------> publish
```

该图只包含普通 executable nodes 和普通 outcome transitions，`graph_mode` 固定为 `acyclic`。

### 9.4 Artifact projection

编译器必须在 plan 中记录每个 execution node 的 logical metadata 和每条 edge 的 artifact projection role：

- Body 内 edge、outer graph 指向第一轮 entry 的 edge，以及 break/exhausted 指向 Loop 外 successor 的 edge 标记为
  `Artifact`；
- result(i) continue -> entry(i + 1) 的跨轮 edge 标记为 `ControlOnly`；
- Runtime 计算 readiness、selected transition、skip 和 predecessor correlation 时不区分两种 role；
- Runtime 构造通用 `[Upstream Outputs]` 时排除 `ControlOnly` edge。下一轮 entry 只能通过 `LoopContext` 的
  `[Previous Iteration Result]` 读取上一轮 result，不能再从通用 upstream projection 重复读取；
- HumanInput 的 `upstream_artifacts` 使用相同的 `Artifact`/selected-edge 过滤规则；上一轮 result 通过独立
  `loop_context` 返回，不能绕过 `ControlOnly` 再塞入通用人工上游列表；
- 对于 Loop 外 successor，Runtime 只投影“其实际 selected transition 指向当前 node”的 completed upstream
  artifacts。上一轮 continue 的 result output 不能因为它也是静态 upstream 而被误当成最终 break result。

为避免改变 v1 历史行为，该 selected-edge artifact projection 只对 compiler v1 生成的 Loop plan 强制启用；普通
v1 acyclic Definition 保持现有 upstream projection，除非另立兼容性设计统一修正。

## 10. LoopContext 与上一轮结果

### 10.1 数据契约

```rust
pub struct LoopContext {
    pub loop_id: String,
    pub iteration: u32,
    pub max_iterations: u32,
    pub previous_result: Option<PreviousLoopResult>,
}

pub struct PreviousLoopResult {
    pub iteration: u32,
    pub result_node_id: String,
    pub execution_node_id: String,
    pub outcome: String,
    pub output: String,
    pub completed_at: u64,
}
```

`PreviousLoopResult` 不是新的持久化实体。Runtime 使用 execution plan 的 `previous_result_node_id` 读取上一轮
Node Run 并构造该投影。

### 10.2 注入范围

- 每个 iteration 的 entry node 都注入 `LoopContext`；BotTask 使用 prompt block，HumanInput 使用结构化 `loop_context`；
- iteration 1 的 `previous_result` 为 `None`；
- iteration > 1 必须有且只有一个 previous result；缺失表示持久化或 execution plan 损坏，不能降级为 None；
- entry 的通用 upstream projection 必须排除 `previous_result_node_id` 对应的 `ControlOnly` edge，上一轮结果只能出现
  在 LoopContext 中一次；
- 非 entry body node 不自动注入 previous result，继续使用直接 upstream artifacts；
- Loop 外 node 不注入 LoopContext；
- retry 同一个 entry execution node 时重新从持久化事实构造完全相同的 context。

### 10.3 Prompt contract

BotTask entry 在现有 `[Input]` 与 `[Upstream Outputs]` 之间增加 server-owned block：

```text
[Loop Context]
loop_id: rounds
iteration: 2
max_iterations: 5

[Previous Iteration Result]
iteration: 1
result_node_id: decision
outcome: continue
completed_at: 1788320000000
output:
<persisted artifact_text>
```

第一轮固定为：

```text
[Loop Context]
loop_id: rounds
iteration: 1
max_iterations: 5

[Previous Iteration Result]
(none - this is the first iteration)
```

该 block 的 header、字段顺序、空值表现和换行均属于 bot runtime delivery contract，必须有 golden tests。Runtime
只在该 block 注入上一轮，不在 `[Upstream Outputs]` 重复投影，也不自动累计全部历史，因此 prompt 不随 iteration
次数线性累加历史文本。Result artifact 继续遵守现有 Node output 的存储和投影边界，本期不增加 Loop 专用摘要器
或截断器。

### 10.4 HumanInput context contract

HumanInput 不经过 BotTask prompt 构造路径。`PendingHumanNodeView` 和
`SessionChannelOutboundPort` 的 `HumanInputReadyEvent` 均增加以下字段，复用 §10.1 的 `LoopContext`：

```rust
#[serde(default, skip_serializing_if = "Option::is_none")]
pub loop_context: Option<LoopContext>,
```

- v2 Loop 的 HumanInput entry 必须返回/携带 `loop_context`；普通 v1、非 entry body node 和 Loop 外节点省略
  整个字段。该字段在兼容 schema 中为 optional，不代表合法 v2 人工入口可以缺失。
- 首轮 `loop_context.previous_result` 显式序列化为 `null`；后续轮次为完整 `PreviousLoopResult`。`None`
  只表示第一轮，不用于表达“结果缺失”或“显示层隐藏内容”。
- Runtime 使用同一个 context 构造器，从当前 Run 的 plan 和指定上一轮 result Node Run 读取数据，分别供
  Bot prompt、人工查询和 HumanInputReadyEvent 使用。通知端不自行解析 execution ID、不读取当前 Definition，
  也不通过“最近完成节点”补算结果。
- `pending_human_node_view` 按 §9.4 过滤 `upstream_artifacts`。人工入口只在 `loop_context.previous_result`
  中获取上一轮结果；非 entry 人工节点继续接收直接 upstream artifacts。
- 人工界面在 instruction 与通用上游结果之间显示独立的 Loop Context 区，包含轮次、最大轮数和上一轮
  outcome/output/completed_at；首轮明确显示“本轮没有上一轮结果”。
- `direct_assignee` IM 通知同样单独展示该上下文。`fixed_group` 沿用既有共享/脱敏上下文规则，不向共享群
  自动发送完整私密 result；不能展示的上一轮内容提示到人工界面查看。可见性过滤只影响通知文本，不能把
  结构化 `previous_result` 伪装成首轮的 `null`。
- `bcs-channel` 将选定通知模式下的文本写入已有 `HumanInputRequest.notification_text`，排队、重试和通知
  恢复复用该文本。不新增 Loop 专用通知表、事务或恢复 worker，也不依赖重新执行 Bot prompt 来恢复人工通知。
- 相同 ready event 的重试和通用 scanner 先读取已有 request，并保留原文本、目的地、deadline 和 interaction stream key。
  NotificationPending 表示明确未发送，外部 IO 前原子转为 Notifying，只有条件更新的赢家能发送；Notifying
  表示发送中或结果未知，包括旧版 attempts=0 的行，不自动重发。Queued 使用既有槽位/队列提升，Active 或已结束
  请求不重复发送；DeliveryFailed 沿用 Workbench 处理策略。发送前和 ACK 前检查原节点、Session activation 与 deadline，
  过期/已失效节点释放 scope，等待者也可清理属于终态 Run 的失效队首。只有缺请求才从原 snapshot 构造同一 ready event。
  持久化错误必须返回；文件 Store 保存成功后再发布内存状态。沿用字符串 status，不新增 migration。
  预检不是跨 Store 事务，之后仍可能与取消/外部 IO 竞争，已开始的发送无法撤回；不宣称外部恰好一次。
  新旧 Channel 写入方不混跑，完整产品 FO 与发布门禁仍按 §13、§18、§22 验收。
- 人工查询、通知准备缺少匹配 plan 或后续轮次 previous result 时返回错误；不能发送一个缺上下文的 v2 人工
  入口请求。回复继续使用 execution node ID/response ref，客户端不能提交或修改可信 LoopContext。

人工查询示例中的新增字段如下；当前 `node_id` 和 `response_ref` 仍由现有 response 字段提供：

```json
{
  "loop_context": {
    "loop_id": "rounds",
    "iteration": 2,
    "max_iterations": 5,
    "previous_result": {
      "iteration": 1,
      "result_node_id": "decision",
      "execution_node_id": "ln-previous-result",
      "outcome": "continue",
      "output": "Persisted first-round result",
      "completed_at": 1788320000000
    }
  }
}
```

Bot prompt、人工界面和 IM 通知的内容投影分别提供 golden/fixture tests；它们的呈现样式可以不同，但上一轮
数据必须来自同一不可变投影，且不得重复加入通用 upstream 输出。

## 11. Runtime 状态推进

### 11.1 Run 创建

Run 创建使用已验证的 Authoring Definition 和 Execution Plan：

1. 编译并验证 Definition/Plan，确定 compiler version/hash 和 resolved participant bindings 作为本次创建输入；
2. 为 Execution Plan 中每个 executable node 物化一条 Pending Node Run；
3. 普通 node 保持现有初始化规则；
4. 只有 compiled entry frontier 可以进入首次 dispatch；
5. Loop control node 不创建 Node Run；
6. 沿用当前先创建 Pending Run/Node Runs、再保存 snapshot、再启动 Run 和派发 initial frontier 的顺序。保留现有 Run/Node 创建事务，不要求把 snapshot、opening、audit、checkpoint 和 frontier 扩成一个事务；
7. Authoring Definition、Plan、compiler version/hash 和 resolved bindings 作为一次完整 snapshot 写入。snapshot 与 opening 历史持久化成功前不得派发节点；写入失败返回错误，并沿用现有启动失败处理。进程退出可留下部分启动状态，恢复只能使用已保存的原始事实，不能读取当前 Definition 重新补编 plan。

### 11.2 Continue

Result execution node 以 continue outcome 完成且 iteration 小于 max：

1. completion CAS 成功；
2. artifact 和 outcome 成为 durable PreviousLoopResult source；
3. 未选择的 break branches 不得被错误地永久 skip，因为它们可由未来 iteration 到达；
4. 下一轮 entry 在其全部 compiled upstream 完成后进入 Ready/dispatch；
5. 下一轮 entry 从上一轮 result Node Run 构造 LoopContext，供 Bot prompt 或 HumanInput 查询/通知使用；
6. 当前 result node 不再执行。

completion CAS 同时固化该节点的 outcome、artifact 和 completed_at；随后 skip、readiness 检查和 dispatch
按现有路径分别提交，不持有覆盖全部展开节点的事务。任一步写入失败必须返回错误，已完成的 result 不回滚，
也不增加 attempt；FO 以该 result 的不可变结果继续推进。

### 11.3 Break

Result execution node 以 break outcome 完成：

1. 选择对应 outer targets；
2. 当前 iteration 后所有 execution nodes 通过现有 branch skip 进入 Skipped；
3. Loop 外 successor 只有在所有相关 upstream Completed/Skipped 后派发；
4. successor 的 selected-edge projection 只暴露触发 break 的 result artifact；
5. Run 按普通 DAG completion 继续推进。

### 11.4 Exhausted

最后一轮 Result execution node 仍返回 continue outcome：

1. Runtime 不创建第 N+1 轮；
2. Runtime 选择 Loop node 的 `exhausted_outcome` targets；
3. 审计和 graph view 显示 result(N) 的真实 outcome 仍为 continue；
4. 对应 edge 的 `loop_route.kind=exhausted`、`logical_outcome=exhausted_outcome`；Node Run 和 edge 的真实 outcome 仍是原 continue outcome；
5. 如果 exhausted target 最终失败，Run 按普通规则失败。

### 11.5 Node retry 与失败

- 同一 iteration 的 retry 只增加 Node Run attempt；
- retry 不增加 iteration，不重新执行之前完成的 body nodes；
- entry retry 看到相同 LoopContext；
- Bot terminal error/aborted、空可见输出、Judge 或 timeout 失败按现有 max attempts 处理；派发被拒绝或派发调用失败沿用当前直接 FailRun 的行为，不因 Loop 改为重试；
- attempts 耗尽后 Run Failed，不把技术失败解释为 exhausted；
- 用户 rerun 创建新 Run，继续沿用 source snapshot 中相同的 N 轮 execution plan。

### 11.6 HumanInput

Body 内 HumanInput 使用生成的 execution node ID 形成唯一 response ref。每个 iteration 的 HumanInput 是不同
Node Run；上一轮迟到的人类回复必须因 node status/attempt/response ref 不匹配而被拒绝或记录为 stale。固定参与者
和 HumanInput channel snapshot 在整个 Run 内保持不变。

当 HumanInput 是 entry 时，激活、pending-node query 和 IM 通知都必须使用 §10.4 的结构化 LoopContext；
非 entry HumanInput 沿用直接 upstream 语义。现有 HumanInput ordering 与回复授权规则继续适用。

## 12. 持久化

### 12.1 Node Run 与 delivery correlation

第一期继续复用现有 Node Run 唯一键：

```text
(env, run_id, node_id)
```

Loop 展开后每轮使用不同 execution node ID，因此无需把 iteration 塞入 `attempt`，也无需立即引入通用
`activation_id`。Delivery request ID 沿用现有实现的格式，不因 Loop 改变已有去重键：

```text
smnode-{run_id}-{execution_node_id}-{attempt}
```

Delivery correlation、Judge outputs、events、message IDs 和 timeout scanner 全部使用 execution node ID。

Node attempt 进入 Failed 时，在同一单节点 CAS 中保存 `failure_action=retry|fail_run`、error 和 completed_at。
该字段是 Repo 内部恢复事实，不增加公开 Node status。RetryScheduled CAS 清除上一次失败决策；它只增加 attempt，
不增加 iteration。历史记录允许该字段为 NULL，不根据错误文本、当前配置或当前 Definition 回填。

### 12.2 Definition snapshot 扩展

`bcs_state_machine_definition_snapshots` 当前保存 Authoring Definition。新增 nullable 字段：

```text
execution_plan_json
execution_plan_content_hash
execution_plan_compiler_version
```

MySQL 使用 JSON/CHAR/VARCHAR；SQLite 测试实现使用 TEXT。Legacy v1 snapshot 允许这些字段为空。V2 Loop Run
创建时三者必须非空。

保存 snapshot 的 repository command 必须同时接受 Authoring Definition、Compiled Execution Plan 和 resolved
bindings，并在一次 snapshot 写入中保存完整内容。Plan 必须在创建 Run 前编译和验证完成；允许按现有实现先创建
Pending Run/Node Runs，再同步保存 snapshot，不要求它们同事务提交。禁止先启动执行再异步生成或补写 plan。

### 12.3 Snapshot load

- v1 Run：沿用当前 Authoring Definition snapshot 路径；
- v2 Loop Run：必须读取持久化 Execution Plan；
- v2 snapshot 缺 plan、hash 不匹配或 compiler version 为空：返回持久化损坏错误并停止推进；
- 禁止在读取失败时调用当前 compiler 重新生成；
- rerun 原样复制 source Execution Plan snapshot 和 compiler metadata。

### 12.4 不新增通用 State

本期不创建 `bcs_state_machine_run_states`。Previous result 完全来自上一轮 Result Node Run：

```text
outcome + artifact_text + completed_at
```

Loop 计数来自 execution plan metadata 和当前 execution node，不单独维护可漂移的 `current_iteration` 行字段。
Graph view 的 current iteration 是 Node Run 状态的只读投影。

## 13. 幂等、并发与 FO

### 13.1 事件隔离

不同 iteration 拥有不同 execution node ID：

```text
iteration 1 decision -> node_id = ln-...
iteration 2 decision -> node_id = ln-...
```

旧 iteration 的 terminal event 只能命中旧 correlation。即使其 attempt 与当前 iteration 都为 0，也不能完成当前
Node Run。

### 13.2 CAS

以下转换继续要求 CAS 或等价事务条件：

- Pending/Ready/RetryScheduled -> Running；
- Running(attempt) -> Completed；
- Running(attempt) -> Failed（同时保存 failure_action）；
- Failed(attempt, retry) -> RetryScheduled(attempt + 1)；
- Pending/Ready/RetryScheduled -> Skipped；
- Running Run -> terminal Run status。

同一 result completion 只能选择一次 continue/break route。重复 terminal event 记录为 ignored/stale，不能再次推进
或覆盖 PreviousLoopResult。

### 13.3 Progression recovery

固定 Loop 遵守 `2026-08-19-bcs-collaboration-state-persistence-fo-rerun-design.md` §6.3 的事务边界。
Completed 表示本节点结果已经提交，不表示全部下游副作用已经完成；不要求增加 `Completed + progressing`
持久化状态或每条展开 edge 的 checkpoint。正常路径继续同步推进，FO 使用同一组 CAS/dispatch 原语补做：

- result 已 Completed、下一轮尚未 Ready/dispatch 时，reconciler 从 Execution Plan 重新计算 selected transition；
- 未来 iterations 的 skip 可以重复执行且必须幂等；恢复遍历不能因为中途节点已 Skipped 就停止，必须继续检查其尚未处理的后代；
- 下一轮 dispatch 使用稳定 execution node ID 和 delivery request ID；
- 已 Failed 但尚未创建 RetryScheduled 或终结 Run 的 attempt，恢复必须读取失败 CAS 保存的 `failure_action`：`retry` 只为同一 execution node 创建下一 attempt，`fail_run` 使用原 error 结束 Run。保存的 FailRun 不得改判为 retry；缺失/未知 action 或必要失败事实时明确报错，不猜测历史策略。该恢复不重新调用 Judge，也不修改之前 Completed 节点；
- finalization 遵循 FO spec 的 Run checkpoint 边界；当前代码尚未实现完整 checkpoint，缺少载荷或发送确认时不得盲目恢复外部结果发布；
- ServiceInvocation 的终态 Run 与 Session 完成可独立提交；正常路径和恢复均以 Run 保存的 `session_activation_count` CAS 完成同一 activation。恢复扫描仍 Running 的 Service Session，并使用 terminal Run 的原 snapshot/output/error；缺少 activation 事实时不得读取当前轮次代替。Run 状态 CAS 未命中不触发该请求的 Session 完成或通知，Session 写失败须返回错误。callback 只使用完成 CAS 返回的 Session；Chat 发布和终态 IM checkpoint 仍按 FO spec 独立验收；
- Chat 最终结果复用 FO spec §9.3 的独立 publication checkpoint：保存原目标/文本后同步发布，Delivered 后完成 Run。消息历史幂等不代表 Bot 路由幂等，Delivering 不重发，原 deadline 后以“可能已送达”的明确错误置 Run Failed；已确认 publication 的恢复只补 Run 收尾。沿用 pre-FO drain/default-off 边界，不增加 Loop 专用恢复或新 migration；
- Service Completed/Failed Run 在完成 Session 前保存终态 IM 的原收件人/文本/activation/期限；Session 完成后沿用同步发送，独立 checkpoint page 补恢复。逐收件人记录进度，预检失败只重试尚未发送的目标；Sending/结果未知不重发，部分失败不被其他收件人的成功覆盖，IM 失败不改变终态 Run。复用 FO spec §9.3 的 30 秒 lease、原 90 秒期限和旧 activation fencing，不增加跨 Store 事务；
- 缺 snapshot/opening 的准备请求保留原 Run 创建时间起 90 秒宽限期，到期通过“仍 active 且事实仍缺失”的 CAS 失败；前台明确写失败可立即收敛。Run 与类型化 startup_failure 事实只在失败路径局部提交，Session 独立恢复。缺 dispatch 使用原节点 deadline/重试策略；无 deadline 时按原 started_at 加 90 秒 FailRun，历史缺 started_at 才退到 Run 创建时间。准备期限不证明创建者已死，不重造原请求、不重发未知 attempt、不改变已确认派发的 timeout 关闭语义。详见 FO spec §10.4.1；
- Loop 不增加内存计数器、独立 scheduler 或专用恢复线程。

Reconciler 对 active Run 有界扫描并批量读取 plan/Node 状态，避免逐 edge 增加持久化进度写入。补做每个动作前
仍检查 Run 是否 active、依赖是否满足，并保留单节点 CAS。重复 completion event 不能改写结果；是否需要补做
progression 由持久化状态决定，不以“本次 completion CAS 未命中”作为永久放弃恢复的依据。

如果完整 progression recovery 尚未发布，固定 Loop 不得被宣称为 FO-safe；不能因为正常同步路径测试通过而绕过
该门禁。

### 13.4 Cancel

取消 Run 时所有未完成 execution nodes 按现有 cancel 语义停止推进。已完成 iteration 保留审计；未开始的未来
iterations 不得被派发。Provider cancel capability 仍不是本设计目标。

## 14. Service API 与 wire projection

### 14.1 统一 execution metadata 与 Run view

所有公开投影复用同一个 additive 对象，字段不得在不同 API 中改名或依赖解析 generated ID：

```rust
pub struct StateMachineNodeExecutionMetadata {
    pub definition_node_id: String,
    pub loop_id: String,
    pub iteration: u32,
    pub max_iterations: u32,
}
```

普通 v1 node 省略整个 `execution` 对象。Loop body node 的 `definition_node_id` 是 body logical ID，`loop_id` 是
outer Loop node ID，iteration 从 1 开始。

现有 start、get 和 rerun 都返回 `StateMachineRunView`，而其中的 `nodes` 是裸 `StateMachineNodeRun`，不能只扩展
单节点 view。Run view 必须增加以 execution node ID 为 key 的可选映射：

```rust
pub struct StateMachineRunView {
    pub run: StateMachineRun,
    pub nodes: Vec<StateMachineNodeRun>,
    pub judge_outputs: Vec<StateMachineJudgeOutputView>,
    pub node_execution_metadata:
        Option<BTreeMap<String, StateMachineNodeExecutionMetadata>>,
}
```

v2 Loop Run 的映射必须包含每个 Loop body execution node；普通 outer node 可以不出现在映射中。v1 response 省略
该字段，保持 wire compatibility。OpenAPI 将字段声明为 optional map，但 contract tests 必须强制 v2 Loop Run 在
start、get 和 rerun 三类 response 中完整返回。

单节点 query 使用同一个对象：

```rust
pub struct StateMachineNodeRunView {
    pub node: StateMachineNodeRun,
    pub execution: Option<StateMachineNodeExecutionMetadata>,
    pub sub_status: Option<StateMachineNodeSubStatus>,
    pub judge_outputs: Vec<StateMachineJudgeOutputView>,
}
```

其中 `node.node_id` 始终是 execution node ID。`execution` 必须通过 Run snapshot 中的 Execution Plan 映射获得，
不得解析 `ln-...`。

上述查询与 Graph、PendingHuman 都是只读投影，不受 v2 新建/推进开关或当前编译资源上限控制；
只要 reader 支持保存的 compiler version，即可读取历史 plan。此行为不放开 start/rerun/恢复推进门禁。
已保存的 v2 snapshot 若缺失 plan、hash 不匹配或 compiler 不支持，查询返回错误，不静默省略 metadata。
无 snapshot 的旧 Run/Node 保留原有裸记录查询行为，不根据 ID 形状猜测其执行版本或补算 metadata。

### 14.2 Graph view

第一期 Run graph 返回展开后的 acyclic nodes/edges，并在每个 Loop body `StateMachineGraphNodeView.execution` 上
携带 `StateMachineNodeExecutionMetadata`。这样现有布局算法不需要处理 back edge。Definition validation preview
也在 flattened preview node 的 `execution` 字段返回相同 shape，并按 `loop_id + iteration` 分组；preview
execution IDs 只用于该 response，不是未来 Run identity。

`StateMachineGraphDefinitionView` 保留现有 `graph_mode` 字段作为 Authoring mode；v2 Loop Run 返回
`hierarchical`。同时增加：

```text
execution_graph_mode = acyclic
execution_plan_compiler_version
```

Definition validation graph preview 同样增加可选 `execution_graph_mode`。Frontend 使用 execution mode 选择布局，
但不得把 Authoring `graph_mode` 覆盖成 acyclic 后丢失 Loop 语义。

Run graph 的 `StateMachineGraphEdgeView` 和 Definition preview 的 edge DTO 复用 §9.2 的
`StateMachineLoopRoute`，增加可选 `loop_route` 字段。Run graph edge shape 为：

```rust
pub struct StateMachineGraphEdgeView {
    pub source: String,
    pub outcome: String,
    pub target: String,
    pub guard: Option<String>,
    pub loop_route: Option<StateMachineLoopRoute>,
}
```

Graph node 增加可选 `outcome`，返回已保存的真实 Node outcome；消费者比较 source.outcome 与 edge.outcome，不能仅凭目标节点 Running/Completed 判断选中分支。旧 response 可省略该字段。

普通 edge 省略整个 `loop_route`；v2 Loop 的每个 result 控制出口 edge 必须带完整对象。API 从持久化 plan
直接投影，preview 从本次编译结果投影；禁止用 node 名称、iteration 或 outcome 字符串猜测 route kind。
`outcome` 始终保留用于选路的真实 outcome，`logical_outcome` 用于 authoring 展示。例如最后一轮：

```json
{
  "source": "ln-last-result",
  "outcome": "continue",
  "target": "manual_finish",
  "loop_route": {
    "kind": "exhausted",
    "logical_outcome": "exhausted"
  }
}
```

前端按 `kind` 区分继续、提前退出和次数耗尽，以 `logical_outcome` 显示 authoring 路由名称，同时允许查看真实
`outcome`。判定某条 edge 是否实际被选中仍比较 source Node Run 的真实 outcome 与 edge.outcome；
`loop_route` 是静态说明，不是“这条 edge 已执行”的标记。

### 14.3 Node query path

运行时 `GET .../nodes/{node_id}` 的 `node_id` 继续是 execution node ID。Frontend 和 CLI 必须使用 Run/Graph response
返回的 ID，不得自行拼接 logical ID、loop ID 和 iteration。

### 14.4 Message history metadata

Runtime 创建 state-machine output message 时，必须从该 Run 的 snapshot Execution Plan 查询 metadata，并把相同
`execution` 对象持久化在现有 `metadata.state_machine` 中：

```json
{
  "metadata": {
    "state_machine": {
      "run_id": "run-...",
      "node_id": "ln-...",
      "attempt": 0,
      "execution": {
        "definition_node_id": "discussion",
        "loop_id": "rounds",
        "iteration": 2,
        "max_iterations": 5
      }
    }
  }
}
```

普通 v1 node 省略 `execution`。消息历史读取原样返回该持久化 metadata；不得通过解析 `node_id` 补算。v2 Run 在
消息写入前无法加载匹配的 snapshot plan 或找不到 execution node 映射时，按损坏的 v2 snapshot 失败，不能写入
只有 generated ID、无法定位 logical node/iteration 的消息。

v2 Node 输出使用现有 MessageRepo 的确定性主键写入：逻辑键为 `{run_id}:{node_id}:{attempt}:1-output`，
超过 64 字节时主键取该逻辑键的 SHA-256 小写十六进制摘要，以适配 MySQL `message_id VARCHAR(64)`；
`client_msg_id` 保留原逻辑键，恢复时复用旧版 SQLite 已保存的消息。历史批量读取同时兼容摘要主键和原逻辑键主键。
消息在 Completed CAS 后、后继推进前保存；
失败向调用方返回错误，通用 active Run 恢复从 Completed Node 补写。此消息不等同于最终 Chat result publication，
不新增表、跨 Run/Message 大事务或公共 Loop 事件。Bot 输出保持 FullOnly，Human 输出定向原 responder。
历史优先批量读取已保存消息并原样返回 metadata；v1 和升级前未持久化输出的旧记录保留原 snapshot 历史投影，
从可信 plan 获取 metadata，不解析 ID。终态缺失历史的恢复仍属于 FO 剩余边界。

### 14.5 HumanInput query 与通知 port

OpenAPI 的 `PendingHumanNodeView.loop_context` 引用共享 `LoopContext`/`PreviousLoopResult` schema。
`loop_context` 对普通 node 可省略；对象一旦存在，`loop_id`、`iteration`、`max_iterations` 和
`previous_result` 都是必需字段，只有首轮的 `previous_result` 可以为 null。legacy/V1 adapter 和人工面板的
TypeScript DTO 同步添加该字段；v2 人工入口完整性由 contract tests 强制验证。

`HumanInputReadyEvent.loop_context` 属于 `SessionChannelOutboundPort` 的已有内部通知合同，不是新的公共
Webhook event type。Runtime、`bcs-channel`、通知 fixtures 和相关 port conformance tests 同步更新，遵守
§10.4 的模式可见性和通知文本持久化要求。

## 15. Event Contract

现有 `state_machine.node.started`、`state_machine.node.completed` 和
`state_machine.node.retry_scheduled` data 增加可选的统一 `execution` 对象：

```json
{
  "node_id": "ln-...",
  "execution": {
    "definition_node_id": "discussion",
    "loop_id": "rounds",
    "iteration": 2,
    "max_iterations": 5
  },
  "attempt": 0
}
```

对象对普通 node 可省略。`node_id` 继续是业务 correlation identity，新增对象只用于逻辑展示和聚合。
`predecessor_node_ids` 继续返回 execution node IDs。

第一期不新增 Loop 专用公共事件。订阅方可以用 node event metadata 推导 iteration。若后续出现独立订阅需求，再
通过单独 Event Contract 设计增加：

```text
state_machine.loop.iteration.started
state_machine.loop.iteration.completed
state_machine.loop.completed
```

不得先在实现中发出未进入 catalog/schema 的私有 Loop events。

## 16. Frontend 与可观测性

### 16.1 Definition preview

2026-09-17 逻辑 Loop 展示修订：用户已确认方案并要求实施。

- 默认使用循环视图，每个 Loop 使用有标题的容器，循环体仅展示一次；保留内部并行/汇合。
- continue 用 result 到 entry 的虚线回边表示，标签只写 `continue`；max_iterations=1 不画不可执行回边。
- break/exhausted 标签只展示逻辑 outcome 原文（例如 `approved`、`exhausted`），不附加中文解释；后续统一考虑 i18n。
  上限仅在折叠信息/悬浮说明中使用 `max_iterations` 展示；节点标题不附加“第 N 轮”或序号，外框标识 Loop。
- Definition preview 没有执行历史，只展示单份循环体，不按上限展开未来 execution nodes。
  实际执行仍是不可变 DAG，不改变 Runtime、编译器或持久化。
- 示例模板由主编评审、Judge 选择 `approved` 进入独立 `polisher` 角色的润色节点，或持续 `revise` 后经 `exhausted` 进入 writer 的重写节点；两条路径最终汇入总结。
- 点击逻辑节点使用第一轮 preview 节点定位定义/角色绑定，不产生按轮独立编辑。
- 预览和 Run graph 增加可选 `loops` map，key 为逻辑 Loop ID。value 包含 `display_name`、
  `max_iterations`、`entry_node_id`、`result_node_id`、`body_node_ids`、`continue_outcomes`、
  `break_outcomes`、`exhausted_outcome`。节点 ID 均指 body 内逻辑 ID；不解析生成的 execution ID。
- descriptor 来自验证后的 authoring；Run graph 来自该 Run 的保存 snapshot。既有展开 nodes/edges 保留；
  普通 v1 省略 loops。旧服务未返回完整 descriptors 时显示展开图，不猜测条件。
- validation error 指向 authoring path，超限在提交前展示。

### 16.2 Run graph

- 默认循环视图，外框标识 Loop 与当前/所选执行序号（`#N`），节点只显示名称。退出状态直接使用实际逻辑 outcome。
- 查看执行下拉框与展开执行图仅包含实际进入的 iteration：任一 body 节点 ready/running/retry_scheduled、存在 started_at、completed/failed 或 attempt>0。
  仅因未来跳过而写入 completed_at 的 skipped 节点不算进入。取消前已启动的执行仍保留。
- 默认跟随当前执行；无活动节点时显示最后实际进入的执行；完全未启动的 Loop 只保留一份待执行结构，没有可选历史。
- 用户主动选择历史执行后不被刷新覆盖，提供“回到当前执行”。各 Loop 的选择互相独立；过期选择回退到当前执行，不展示未来记录。
- 节点状态、详情、产物、Judge、retry attempt 和 HumanInput response 均对应所选轮次的真实 execution ID。
  合成的展示容器/回边不得作为请求目标。等待人工的当前任务仍可从独立人工入口直接处理。
- break 后未进入的未来 iteration 不出现在选择器或展开图中；exhausted 显示正常出口，不能作为失败。
  Run 在 Loop 外失败不能把已成功退出的 Loop 改成失败；取消保留真实节点状态并显示 Run 已取消。
- 保留“展开执行图”用于已进入的 execution node 排障，按 Loop/iteration 外框分组，两种视图共享当前 Run 数据和节点操作。未进入的循环外分支也不列入执行历史，避免隐藏未来 iteration 后出现悬空节点；完整分支结构保留在循环视图。
- 验收覆盖单轮、空 break、continue/exhausted/break、多 Loop、并行 body、retry/cancel、历史轮次固定、
  当前轮跟随、人工回复精确目标、v1/旧服务回退，以及窄屏下容器/回边/出口不裁切。

### 16.3 日志与指标

结构化日志增加：

```text
loop_id
loop_iteration
loop_max_iterations
definition_node_id
execution_node_id
attempt
selected_outcome
```

至少增加：

```text
state_machine_loop_iterations_started_total
state_machine_loop_iterations_completed_total
state_machine_loop_break_total{outcome}
state_machine_loop_exhausted_total
state_machine_loop_compile_rejected_total{reason}
```

指标不得使用 run ID、node ID 或 Bot ID 作为高基数 label。

计数复用现有观测 hook 与 Prometheus 装配点，在本地状态转换提交成功后同步更新进程指标：

- started：Loop entry 节点 attempt 0 首次进入 Running；HumanInput 和 BotTask 使用同一规则。同轮 retry 不新增轮次。
- completed：Loop result 节点首次提交 Completed；同时根据已保存的 route 计 break 或 exhausted。中间 body 节点、普通节点及出口 fan-out 不增加轮数。
- compile rejected：typed v2 compiler 每次拒绝调用计一次，包括 Definition validation。YAML shape 错误、compiler 之前的执行能力门禁和历史 snapshot 读取不属于该计数点。reason 限制为 invalid_definition/resource_limit，资源原因由编译器错误类型提供，不解析文案。
- break 的 outcome label 仅保留 complete/done/approved/rejected，其他 author-defined 值统一为 other；实际 outcome 保留在结构化日志和运行记录中。沿用现有 env label，不添加 Loop ID、iteration 或 attempt label。

重复 terminal、已提交结果的 progression/recovery 不重计；恢复首次完成原先未提交的 Judge 或 entry 转换时正常计数。指标是进程级观测，状态提交后、记录指标前若崩溃可能少计一次；不为指标引入数据库列、事务、去重表或恢复补账。持久化 Run/Node/Event 是审计依据。实现细节与验收见 [Loop 观测合同](../../observability/fixed-loop.md)。

## 17. 安全与资源边界

1. `max_iterations` 必填，不能由 Bot 在运行时扩大；
2. 服务端 hard limit 优先于 Definition；
3. 编译前后都限制 node count 和 serialized plan bytes；
4. generated execution node ID 由服务端生成，调用方不能注入；
5. LoopContext 从持久化 Node Run 构造，不能信任 Bot 回传的 iteration/outcome metadata；
6. Bot output 只作为 artifact 和 Judge input，不拥有 transition policy；
7. continue/break outcome mapping 来自不可变 snapshot；
8. participant binding 在 Run 创建时固化，Loop 不改变 Bot/Human 权限；
9. Prompt 只注入上一轮，避免重复拼接全部历史造成无界 token growth；
10. Definition validation 不执行用户脚本或表达式；
11. exhausted 必须显式路由，防止达到上限后静默产出未经确认的结果。

## 18. 兼容性与迁移

### 18.1 Definition compatibility

- v1 acyclic Definition byte/wire 行为不变；
- v2 Loop Definition 只能发送给声明对应 server features 的 BCS；
- 旧 BCS 对 version 2 返回稳定 unsupported-version diagnostic；
- 不修改现有 `cyclic` 的 unsupported 状态；
- 不允许 v1 Definition 使用 `kind: loop`；
- 不允许通过 unknown `extensions` 隐式开启 Loop。

测试开放（2026-09-17，按用户明确要求）：`collaboration.experimental_fixed_loop_execution` 默认 `false`；
测试环境显式设为 `true` 后，validation、配置群、首次启动、one-shot、推进与 rerun 使用同一能力开关，
不再返回 `VALIDATION_ONLY_FEATURE`。该开关同时开启通用 progression recovery scanner，无需再开第二个开关。
独立 `experimental_progression_recovery` 仍可用于 v1 恢复测试，不单独开启 v2。
仓库 local 配置显式开启便于效果验证；example 与无配置部署保持默认关闭。关闭开关前应先结束 active v2 Runs，
否则它们将无法继续推进。此实验入口不代表 §22 的生产 FO、性能或发布验收已通过。

### 18.2 Persistence migration

对 MySQL 和 SQLite snapshot schema 做 additive migration。Legacy rows 的 execution plan 字段为 NULL。部署新版本
不会主动改写历史 v1 snapshots。

由于新代码读取 v2 snapshot 依赖 execution plan 字段，启用 Loop 前必须保证：

1. 所有 Store 实现已完成 migration；
2. Memory/MySQL/SQLite conformance tests 通过；
3. 运行实例均支持 compiler v1；
4. 不存在会被旧实例接管的 active v2 Run；
5. 若部署期间混合版本不可避免，Gateway/Definition capability 必须阻止在旧实例可调度范围创建 v2 Run。

### 18.3 Rollback

已创建的 v2 Loop Run 不能由不理解 execution plan 的旧版本继续执行。Rollback 前必须 drain 或确定性终止所有
active v2 Runs。数据库新增 nullable 字段可以保留，不要求 destructive rollback。

## 19. 分层与代码归属

按照 BCS 架构约束：

- Loop Definition、Execution Plan DTO 和公共 runtime view contract 位于 `bcs-domain` / `bcs-service-api` 相应合同层；
- 纯 Definition 校验、compile-to-DAG、outcome routing 和 LoopContext 规则位于
  `bcs-collaboration-runtime` core implementation；
- Application Service 继续编排 Run start/query/respond/cancel/rerun，不在 HTTP adapter 实现 Loop；
- Execution Plan snapshot port 位于 `bcs_service_api::port::repo`；
- Memory/MySQL/SQLite mapping 位于 `bcs-collaboration-store`；
- HTTP adapters 只做 versioned wire parsing/projection 和 Application Error 映射；
- HumanInput query 与通知复用 Runtime 的 LoopContext 构造；`bcs-channel` 负责按既有通知模式渲染并保存文本，不推导 Loop 选路或上一轮来源；
- frontend 只消费 graph metadata，不推导业务 progression；
- compiler、store、delivery 和 recovery 实现只在 composition root 装配；
- API、Event 和 authoring schema 是对外行为的权威，不能只更新 Rust struct。

主要代码落点：

```text
crates/contracts/bcs-domain/src/collaboration.rs
crates/services/bcs-collaboration-runtime/src/definition.rs
crates/services/bcs-collaboration-runtime/src/runtime.rs
crates/service-api/bcs-service-api/src/application/collaboration_runtime.rs
crates/service-api/bcs-service-api/src/port/session_channel_outbound.rs
crates/service-api/bcs-service-api/src/port/repo/collaboration.rs
crates/services/bcs-collaboration-store/src/lib.rs
crates/services/bcs-channel/src/lib.rs
api-contracts/v1/openapi/state-machine-runs.yaml
api-contracts/v1/openapi/collaboration-definitions.yaml
api-contracts/events/v1/catalog.yaml
api-contracts/events/v1/event-envelope.schema.json
crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md
assets/panel/src/StateMachineRunView.tsx
```

前端同步更新运行图、人工入口上下文和 Definition preview 的 execution/loop_route projection；其中实际运行面板
包含 `assets/panel/src/StateMachineRunView.tsx`，Definition preview 同步涉及 `src/frontend`。

## 20. 实施切片

### Slice 1：Contract 与纯编译器

1. 增加 state machine version 2、Loop node/domain DTO；
2. 增加 authoring strict-key validation；
3. 实现 Loop structural validator，拒绝空 continue/break-only 和空 break/exhausted targets，允许空 break 集合；
4. 实现确定性 compiler v1、compiled metadata 和三类 loop_route；
5. 对 compiled plan 复用现有 DAG validation；
6. 更新 CLI authoring reference、capability inference 和 validation preview contract；
7. 添加 compiler golden tests。

该切片不挂载执行，不允许创建 v2 Run。

### Slice 2：Snapshot 与 Store

1. 增加 execution plan snapshot schema/migration；
2. 扩展 repo port 和 Memory/MySQL/SQLite 实现；
3. 一次 snapshot 写入固化 Authoring Definition、Plan 和 resolved bindings，保留分步 Run 创建及派发前 snapshot barrier；
4. load/rerun 强制使用 snapshot plan；
5. 增加 store conformance tests。

### Slice 3：Runtime 执行

1. 使用 compiled plan 物化 Node Runs；
2. 支持 generated execution node IDs；
3. 实现共享 LoopContext 构造器、Bot prompt、PendingHumanNodeView 和 HumanInputReadyEvent 投影；
4. 验证 continue/break/exhausted progression；
5. Bot prompt 与 HumanInput upstream_artifacts 的 selected-edge/ControlOnly 过滤；
6. HumanInput、Judge、timeout、cancel 和 rerun 集成；
7. progression recovery/checkpoint 集成。

### Slice 4：API、Event 与 Frontend

1. 更新 OpenAPI Run/Node/Graph/PendingHumanNode views，以及共享 execution、loop_route、LoopContext schema；
2. 更新 Event schema/catalog/fixtures；
3. 更新 message history metadata projection；
4. 更新 legacy 与 V1 adapter DTO projection；
5. 更新 Definition preview edge.loop_route 和相关 fixtures；
6. 更新 Run graph、node detail、人工入口上下文展示和 IM 通知文本，补齐通知 port conformance；
7. 更新 bcs-cli 查询展示。

### Slice 5：发布门禁

1. 全部 contract/conformance tests；
2. MySQL/SQLite migration verification；
3. 单实例正常执行 E2E；
4. FO progression E2E；
5. 前端多轮展示验证；
6. 能力开关和 mixed-version deployment 检查；
7. 文档和示例 Definition。

## 21. 测试计划

### 21.1 Definition/Compiler unit tests

- 一轮后 break；
- 多轮 continue 后 break；
- 最后一轮 continue 路由 exhausted；
- 多个 continue outcomes；
- 多个 break outcomes；
- result 无 Judge、`continue_outcomes: [complete]`、`break_outcomes: []`；
- continue 集合为空必须拒绝，覆盖无 Judge 的 complete-break 和 `max_iterations: 1`；
- 有 Judge 且 break 集合为空时合法，每个 outcome 都继续，最后一轮走 exhausted；
- body 并行 fan-out/join；
- entry/result 缺失；
- body cycle；
- body 多入口或多 terminal；
- body node 跳出 Loop；
- outer node 跳入 body；
- nested/overlapping Loop；
- continue/break outcome 重叠或遗漏；
- exhausted outcome 冲突；
- break/exhausted transition 缺少 targets 或 targets 为空，即使编译后的普通 DAG 校验可通过也必须拒绝；
- max iterations 为 0 或超过 server limit；
- compiled node count/bytes 超限；
- generated ID 稳定、唯一、长度合法；
- 跨轮 continue edge 固化为 `ControlOnly`，其他展开 edge 固化为 `Artifact`；
- result 控制出口的 loop_route.kind/logical_outcome 覆盖 continue、break、exhausted；普通 edge 省略该字段；
- 多个 continue outcomes 在最后一轮保留各自真实 outcome，并映射到同一个 exhausted logical_outcome；
- 编译输出在相同 input/compiler version 下 byte-stable；
- compiled DAG 再校验通过。

### 21.2 Runtime progression tests

- iteration 1 entry 的 previous result 为 None；
- iteration 2 entry 精确读取 iteration 1 result；
- iteration 3 只自动注入 iteration 2，不累计 iteration 1 全文；
- iteration > 1 entry 的 `[Upstream Outputs]` 不重复出现上一轮 result artifact；
- previous result 同时包含 output 和 outcome；
- entry retry 看到相同 context；
- iteration 变化时 attempt 重置为 0；
- 同一 iteration retry 只增加 attempt；
- break 后未来 nodes 全部 Skipped；
- exhausted 不覆盖最后 result 的真实 continue outcome；
- Loop 外 successor 只投影 selected break result；
- Result Judge invalid outcome 不推进；
- Result timeout/retry/failure；
- HumanInput 每轮 response ref 唯一；
- HumanInput entry 首轮返回 loop_context 且 previous_result 为 null，后续轮次返回完整上一轮结果；
- 人工查询和通知与 Bot prompt 共用上下文来源，通用 upstream_artifacts 排除上一轮 ControlOnly edge；
- 非 entry HumanInput 和 v1 node 省略 loop_context，保持现有直接上游语义；
- final output 仍由 Loop 外唯一 final node 产生；
- Run completion 需要 Completed/Skipped 全覆盖。

### 21.3 Idempotency/FO tests

- 同一 result terminal event 重放只推进一次；
- iteration 1 的迟到事件不能完成 iteration 2；
- 旧 attempt 的迟到事件不能完成当前 attempt；
- crash after result completion before next entry dispatch；
- crash during future-iteration skip；
- 部分未来节点已 Skipped 时重新遍历，仍能跳过所有尚未处理的后代；
- crash after exhausted selection before target dispatch；
- recovery 使用 snapshot plan，不调用 current compiler；
- execution plan hash 不匹配时停止推进；
- rerun 复制 source plan 和 generated node IDs；
- cancel 后 reconciler 不派发未来 iteration；
- HumanInput 通知排队、重试和恢复复用已保存 notification_text，不重渲染当前 Definition 或重复创建请求。

### 21.4 Store conformance

- Memory、MySQL、SQLite round-trip Authoring Definition + Execution Plan；
- compiler version/hash 持久化；
- v1 nullable compatibility；
- v2 missing-plan rejection；
- 现有 Run/Node 创建与 rerun 防重事务；snapshot 单次完整写入；
- Run 已创建但 snapshot 保存失败或进程退出时不派发节点，错误可见且恢复不读取当前 Definition 补编；
- completion、skip、frontier 分步提交失败后的幂等补做，不要求跨步骤回滚；
- generated node correlation round-trip；
- execution plan round-trip 保留 loop_route，rerun 不改变真实 outcome 到 logical_outcome 的映射。

### 21.5 Contract tests

- OpenAPI start/get/rerun Run view、Node view、Run graph 和 Definition preview 的 Loop metadata projection；
- v2 Run view 的 `node_execution_metadata` 覆盖全部 Loop body execution nodes；
- Event schemas optional `execution` metadata；
- message history 持久化并返回相同 `execution` metadata；
- 同一 execution node 在 Run/Node/Graph/Event/Message 中的 metadata 完全一致；
- legacy/V1 byte-compatible optional-field behavior；
- unknown Loop keys rejected；
- version/capability diagnostics；
- Definition validation graph preview fixtures；
- Run graph 与 preview 的 edge.loop_route 复用同一 schema；v2 result 出口必填，v1/普通 edge 省略；
- exhausted graph fixture 同时包含真实 continue outcome、kind=exhausted 和 authoring exhausted logical_outcome；
- PendingHumanNodeView 与 HumanInputReadyEvent 的 loop_context 保持相同数据；首轮 null 与字段省略有明确区别；
- 人工界面与 direct_assignee IM golden tests 验证上下文单独展示且不重复；fixed_group 不泄漏私密上一轮结果；
- HumanInput query/通知缺少 plan 或后续轮次 previous result 时失败，不能返回缺上下文的 v2 人工入口。

### 21.6 E2E stories

至少提供三条 live story：

1. 三轮内第二轮 break，验证第二轮入口读到第一轮结果、第三轮节点 Skipped、最终输出成功；
2. 执行到最大轮数仍 continue，验证 exhausted -> HumanInput/兜底节点 -> final output，并检查真实 outcome 与 loop_route 的区别；
3. HumanInput 作为 entry 执行两轮：首轮无 previous result，第二轮人工查询和 direct_assignee 通知读取第一轮 result 的 outcome/output，回复使用本轮 response ref。

E2E 必须同时检查 node/event metadata、消息历史和 graph view，不能只检查 Run 最终状态。

## 22. 验收标准

固定 Loop 可以发布的最低标准：

1. Authoring、OpenAPI、Event 和 Runtime contracts 已文档化并通过 conformance tests；
2. v1 acyclic 全量回归通过；
3. v2 compiler 输出确定且 snapshot authority 生效；
4. BotTask 与 HumanInput 入口在正常执行及各自支持的 retry/FO 路径中都读取同一个上一轮结果；人工查询/通知具有结构化 loop_context；
5. iteration 与 attempt 在 persistence、Run/Node/Graph API、event、消息历史、UI 和 log 中可独立识别；
6. break、exhausted、failure 和 cancel 四种路径均可确定性收敛；
7. 未来 iterations 在提前 break 后均为 Skipped；
8. stale iteration/attempt events 不可推进当前状态；
9. MySQL、SQLite 和 Memory Store 行为一致；
10. 前端不需要绘制 cyclic edge 即可展示和操作运行中 Loop，并直接通过 loop_route 区分 break/exhausted；
11. 完整 FO 门禁满足，或发布说明明确禁止宣称 FO-safe 且能力开关默认关闭；
12. 没有通过 `extensions`、prompt 约定或 adapter 分支引入未记录的动态 State/Workflow 语义。

## 23. 备选方案与否决理由

### 23.1 标记可回跳节点

否决作为公共合同。目标节点标记只能说明“允许被回跳”，不能定义 Loop scope、iteration identity、break、exhausted、
output、join 和嵌套关系。可回跳 header 可以是未来 compiler 内部 invariant，不足以成为完整用户模型。

### 23.2 任意 goto 前置节点

否决。它允许不可约、交叉和重叠 cycle，要求原生 activation/token engine，并显著提高校验、FO、UI 和用户理解成本。

### 23.3 原地重置同一 Node Run

否决。它覆盖历史 artifact/status，混淆 iteration 与 attempt，使迟到事件、delivery correlation、Judge outputs 和审计
无法可靠区分轮次。

### 23.4 使用 attempt 作为 iteration

否决。Attempt 已是外部失败重试和 delivery idempotency identity。改变含义会破坏现有 FO/rerun contract，并导致
“第二轮第一次执行”和“第一轮第二次重试”不可区分。

### 23.5 直接启用 cyclic runtime

一期否决。当前 persistence、upstream barrier、skip、completion、event 和 frontend 都基于 DAG。原生 cyclic
需要独立的 NodeActivation/token/epoch 设计，不应由固定 Loop 需求顺带引入。

### 23.6 第一期开通通用 mutable State

否决。上一轮感知可以完全由上一轮 Result Node Run 满足。通用 State 需要 schema、reducer、ACL、CAS revision、
并发写和业务校验等独立合同，提前加入会扩大范围且与固定 Loop 解耦。

### 23.7 FO 时重新编译 Definition

否决。Compiler upgrade 可能改变 generated IDs、edge topology 和 projection。历史 Run 必须使用创建时固化的
Execution Plan。

## 24. 后续演进边界

本设计只为以下演进保留清晰边界，不预实现：

- 通用 `ExecutionState`、`StateSnapshot`、`StateCommand` 和 reducer；
- `for_each` 和动态 participant selector；
- Loop body nesting；
- 原生 cyclic/event-driven activation engine；
- 动态 PlanProposal 的校验与物化；
- Loop iteration 专用 events；
- 折叠式 Loop UI；
- iteration history 摘要、选择性投影和 token budget policy。

当需求超过“固定 body + 上一轮结果 + 有界 continue/break”时，应基于新的正式 contract 扩展，不能改变
`bcs.fixed-loop.compiler/v1` 已发布语义。
