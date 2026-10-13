# BCS State Machine 有界嵌套 Loop 规格

- 日期：2026-09-21
- Rebase 复核：2026-09-22，代码基线 `f49424bc4`
- 状态：实施中；核心编译、快照、运行时与公共路径投影已落地，完整 UI/容量/live 验收尚未完成
- 所属模块：BCS；前端消费方为 BCS panel 和 Avernet Frontend
- 范围：Authoring、递归编译、Execution Plan、上下文、恢复、API/Event/消息投影、运行图与验证
- 交付性质：沿用 compile-to-DAG 的中等规模跨层改造；不替换状态机执行引擎
- 本文中的“必须”是实施与验收要求，不表示当前代码已经具备该能力。

## 1. 目标与设计结论

允许固定 Loop 的 body 包含另一个固定 Loop，并在配置的深度、节点数和计划大小上限内递归组合。
每层 Loop 有独立的轮次、continue/break/exhausted 路由和上一轮结果。编译器在 Run 创建前
将完整嵌套结构展开成有限 DAG；运行时继续执行普通 BotTask/HumanInput Node Run。

本方案保留以下现有机制：

- 一条 execution node 对应一个 Run 内的一条 Node Run；
- `attempt` 只表示该 execution node 的失败重试；
- Node Run、delivery、Judge、人工回复、CAS 和 timeout 使用现有 execution identity；
- 分支完成后依靠选中边、可达性和 `Completed/Skipped` barrier 推进；
- Run 创建时保存不可变 Authoring Definition、Execution Plan 和 resolved bindings；
- 故障恢复（FO）及 rerun 使用原快照，不调用当前编译器重新解释历史 Run；
- Loop 不绑定 Bot，不直接拥有 Node Run，不新增运行时 LoopRun 或 activation/token 引擎。

本方案明确保留每层 `entry_node` 和 `result_node` 必须为普通可执行节点的限制。
子 Loop 可以出现在 body 中间及其分支中，不能直接充当父 Loop 的入口或结果。
这是稳定的组合边界，不通过临时字段、虚假 BotTask 或待删除的控制节点绕过。

## 2. 依据、现状与冲突

### 2.1 规范依据

- [Context map](../../../../CONTEXT-MAP.md) 与 [领域文档规则](../../../../docs/agents/domain.md)
- [架构规则](../../../../docs/arch/arch.rules.md) 与 [CI 规则](../../../../docs/arch/ci.enforce.md)
- [Context boundary 格式](../../../../docs/arch/context-boundary-format.md)
- [Protocol conformance 规则](../../../../docs/arch/protocol-contract-tests.md)
- [BCS 指导](../../AGENTS.md)、[BCS 分层](../../CLAUDE.md)
- [Service API Context](../../crates/service-api/bcs-service-api/CONTEXT.md)
- [固定 Loop 设计](../../docs/superpowers/specs/2026-09-02-bcs-fixed-loop-state-machine-design.md)
- [持久化、FO 与 rerun 设计](../../docs/superpowers/specs/2026-08-19-bcs-collaboration-state-persistence-fo-rerun-design.md)
- [状态机消息历史合同](../2026-09-21-state-machine-message-history/spec.md)
- [Authoring validation 设计](../../docs/superpowers/specs/2026-08-22-v1-collaboration-definition-validate-design.md)
- [现有 authoring 合同](../../crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md)

当前 [系统 ADR 目录](../../../../docs/adr/) 没有直接规定嵌套 Loop 的 ADR。
本方案延续已有 BCS 编译与执行边界，不引入需要替换的系统级 ADR。
本文扩展既有固定 Loop 设计中“不支持嵌套”和“一个 execution node 只属于一个 iteration”的范围：
后者改为“每个祖先 Loop 各有一个确定 iteration，完整路径唯一标识叶子执行”。
禁止重叠、交叉、跨作用域跳转、动态 topology 等限制继续有效。
实施时必须同步当前 authoring 文档与 Service API Context，不能继续宣称所有 Loop 都禁止嵌套。

### 2.2 当前代码证据

下表路径均相对 `apps/bcs/`。

| 边界 | 当前实现 | 本方案处理 |
| --- | --- | --- |
| Domain | `crates/contracts/bcs-domain/src/collaboration.rs` 的 `FixedLoopDefinition.nodes` 已使用统一节点类型 | 复用递归定义结构；新增执行路径合同 |
| Authoring | `validation.rs::validate_nodes_shape` 用 `inside_loop` 拒绝子 Loop | 改为携带路径和深度的递归校验 |
| Compiler | `fixed_loop.rs` 仅展开顶层 body，`outer_target` 只查询顶层 | 统一作用域解析与递归展开 |
| Identity | compiler v1 hash 使用 definition ID/version、一个 loop ID、一个 iteration、body ID | 嵌套计划使用完整轮次路径 |
| Context | `loop_context.rs` 仅有一个 loop/iteration/previous result | 保留最近所属 Loop 的结果语义，增加完整路径校验 |
| Snapshot | `snapshot.rs` 仅接受 compiler v1，按顶层 body 校验元数据和路由 | 增加 compiler v2 reader；保留 v1 原字节合同 |
| Progression | `progression.rs`、`runtime.rs` 按扁平 DAG 推进、skip、join | 复用；补嵌套控制流与恢复测试 |
| Store | `bcs-collaboration-store/src/lib.rs` 保存 plan JSON；Node Run 用不透明 node ID | 保留 schema 和主键；验证新 JSON 与读写成本 |
| 消息历史 | `runtime_message_history.rs` 接受事实并可靠补写；runtime 按 Session cutoff 选择 `runtime_history_query.rs` | 完整路径进入不可变消息；复用身份、恢复、选读与公共投影合同 |
| Graph | `loop_graph.rs` 只枚举顶层；panel/Frontend 只按单层 Loop 分组 | 递归 descriptor 与作用域内轮次选择 |

### 2.3 必须显式解决的冲突

1. 两处嵌套禁止校验都需要修改，只改 YAML 或只改 typed compiler 均不完整。
2. 同一个内层 Loop 会在不同外层轮次重复实例化，单个 loop ID 和 iteration 不再唯一。
3. 快照校验不能继续用 `authoring.nodes[loop_id]` 查找所有节点。
4. 现有 OpenAPI/Event schema 的未知字段限制需要同步扩展，新增 Rust 字段不等于 wire 兼容。
5. 旧 reader 无法执行 compiler v2 的 Run，部署不能只升级创建入口而保留旧恢复实例。
6. 不能改变已保存 compiler v1 的序列化或 hash，不能将旧字段改义为“最外层”。

### 2.4 Rebase 复核结论

相对原基线 `225b6aab9`，递归定义类型、单层编译器、snapshot/context/graph 及两处嵌套禁令
均未改变，核心方案仍适用。新合入的消息历史持久化与 Session cutoff 选读要求补齐第 9–11 节
的接入与验收：不能仅在历史查询时从 plan 补路径，也不能忽略已接受消息的持久化屏障。
沿用现有历史实现及其测试，不另建历史存储、配置或恢复机制；研发任务仍为 10 项。

## 3. 范围与非目标

### 3.1 本次交付

- 多层固定 Loop，以及同一 body 内多个串行或并行的子 Loop；
- 各层局部节点名，可在不同作用域重复；
- 各层独立 continue/break/exhausted，包括内外层使用相同 outcome 名；
- 各层独立 previous result，支持 BotTask 和 HumanInput 入口；
- 每个子 Loop 实例的 retry、timeout、failure、cancel、FO、整 Run rerun；
- 不透明 execution ID、路径元数据、API/Event/历史消息一致投影；
- 嵌套预览、运行图、节点详情及人工入口；
- 深度、递归展开规模和数据库访问成本验证。

### 3.2 非目标

- 子 Loop 直接作为父 Loop 的 `entry_node/result_node`；
- `break outer`、跨层 continue、跨 scope goto、跳入 body 中部；
- 无界 while、动态 for_each、运行时生成节点或参与者；
- 通用可变 State、reducer、跨层共享状态或自动拼接所有祖先历史输出；
- 子 Loop 独立 Run、局部 rerun、复用历史轮次的 artifact；
- 新增通用控制节点或另一套持久化调度器；
- 放大现有节点/字节默认上限，或者重构无关状态机、队列、租约机制。

## 4. 术语与身份

| 名称 | 定义 | 示例 |
| --- | --- | --- |
| Definition path | 从最外层到当前 Loop 的局部 ID 数组，不含轮次 | `["outer", "inner"]` |
| Loop instance | 某个 Loop 在确定祖先轮次中的一次完整调用，包含其所有自身轮次 | outer 第 2 轮中的 inner |
| Loop iteration path | 从最外层到最近所属 Loop 的 frame 数组，每帧含 ID、当前轮次、最大轮次 | outer 2/2、inner 1/3 |
| Definition node ID | 当前 body 内的叶子局部 ID | `review` |
| Execution node ID | 编译后唯一、不透明、可持久化的叶子执行 ID | `ln-<32 hex>` |

Loop instance 的逻辑身份为 `(definition_path, ancestor_iterations)`；
它的一次 iteration 再附加当前 Loop 的 iteration。叶子 execution identity 再附加局部 node ID。
这些身份由编译期元数据表达，不新增数据库中的可变实例对象。

同一 Run 的以下节点必须不同：

```text
outer[1] / inner[1] / review
outer[2] / inner[1] / review
outer[2] / inner[2] / review
outer[2] / review
```

## 5. Authoring 合同

### 5.1 版本和特性

Authoring 保持 `runtime.state_machine.version: 2`、`graph_mode: hierarchical`。
顶层 `api_version`、Definition ID/version 仍由现有流程管理，不要求作者增加新版本字段。
嵌套增加一种明确的结构能力，不改变已有单层 Loop 字段语义。

在现有 fixed-loop server features 之外，实际包含嵌套的 Definition 必须推导：

```text
state_machine.loop.nesting
```

不能依赖作者显式声明该 feature 才检查嵌套。未知 feature 仍拒绝。
只显式声明该 feature 而没有嵌套结构，不触发新 ID 格式或额外 runtime 语义。
新 feature 必须进入 authoring validation、capability 推导和 snapshot feature 处理链路。
不新增 Bot runtime feature：Bot 仍执行原 BotTask，路径和结果由 BCS 注入。

### 5.2 局部作用域

- 每个 `nodes` map 是独立作用域；局部 ID 沿用当前字符、长度和 `ln-` 保留前缀限制。
- `entry_node/result_node` 只能引用当前 body 的直接普通节点，不能是子 Loop。
- 普通节点 transitions 只能引用当前 `nodes` map 中的直接节点。
- Loop 节点 transitions 属于该 Loop 所在的父 scope，targets 也只能引用父 scope 的直接节点。
- 可以以子 Loop 节点为 target，编译器将其解析为当前祖先轮次下的首轮入口。
- 不支持 `/`、`..` 或完整路径形式的 authoring target；路径只用于执行身份和只读投影。
- 各层把子 Loop 当作一个逻辑节点后，必须仍是单入口、单结果的有限 DAG。
- 每层唯一 terminal 是普通 `result_node`；它不写 body transitions，由所属 Loop 接管结果路由。
- 所有 body 节点必须处于该层 entry 到 result 的某条路径上，禁止不可达孤岛。
- 整张外层图继续恰有一个普通 `final_output` 节点，所有 body 都禁止 `final_output`。

### 5.3 每层出口规则

继续沿用固定 Loop 合同：

- `continue_outcomes` 非空；`break_outcomes` 可以为空；两集合唯一且互斥。
- 有 Judge 时，两集合必须完整划分 result 的 Judge outcomes。
- 无 Judge 时，continue 只能是 `[complete]`，break 必须为空。
- `exhausted_outcome` 非空，且不属于 result 的真实 outcome 集合。
- Loop transitions 的键必须恰为 break outcomes 加 exhausted outcome，targets 非空。
- 不支持 guarded transition，不在嵌套时放开其他尚未支持的节点能力。

### 5.4 完整示例

下面的示例使用 outer 2 轮、inner 3 轮。参与者通过现有运行绑定解析。
示例是本 spec 的待实现合同，当前校验器应拒绝其中的嵌套。

```yaml
name: Nested review
participants:
  worker:
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 2
    graph_mode: hierarchical
    nodes:
      outer:
        kind: loop
        display_name: 整体修订
        loop:
          mode: fixed
          max_iterations: 2
          entry_node: prepare
          result_node: assess
          continue_outcomes: [repeat]
          break_outcomes: [done]
          exhausted_outcome: capped
          nodes:
            prepare:
              kind: bot_task
              display_name: 制定本轮修订任务
              assignee: {type: bot_binding, binding: worker}
              instruction: 根据输入和上一轮整体评估制定本轮任务。
              transitions:
                complete: {targets: [inner]}
            inner:
              kind: loop
              display_name: 局部评审
              loop:
                mode: fixed
                max_iterations: 3
                entry_node: draft
                result_node: review
                continue_outcomes: [revise]
                break_outcomes: [approved]
                exhausted_outcome: exhausted
                nodes:
                  draft:
                    kind: bot_task
                    display_name: 修订草稿
                    assignee: {type: bot_binding, binding: worker}
                    instruction: 首轮使用直接上游任务，后续轮次使用上一轮评审结果。
                    transitions:
                      complete: {targets: [review]}
                  review:
                    kind: bot_task
                    display_name: 评审草稿
                    assignee: {type: bot_binding, binding: worker}
                    instruction: 评审草稿，输出问题和结论。
                    judge:
                      type: llm
                      criteria: [草稿是否满足本轮修订任务]
                      outcomes: [revise, approved]
              transitions:
                approved: {targets: [assess]}
                exhausted: {targets: [assess]}
            assess:
              kind: bot_task
              display_name: 整体评估
              assignee: {type: bot_binding, binding: worker}
              instruction: 根据本轮结果判断是否需要再进行一轮整体修订。
              judge:
                type: llm
                criteria: [整体目标是否完成]
                outcomes: [repeat, done]
        transitions:
          done: {targets: [publish]}
          capped: {targets: [publish]}
      publish:
        kind: bot_task
        display_name: 输出最终结果
        assignee: {type: bot_binding, binding: worker}
        instruction: 汇总最终结果和未解决的问题。
        final_output: true
```

## 6. 执行语义

### 6.1 轮次与退出

| 事件 | 结果 |
| --- | --- |
| inner[i,j] 选择 continue 且 j 未达上限 | 进入 inner[i,j+1]，outer 轮次 i 不变 |
| inner[i,j] 选择 break | 剩余 inner[i,*] 进入 Skipped，沿父 scope 配置进入 outer[i] 的后继 |
| inner[i,last] 选择 continue | 真实 outcome 保留，逻辑路由为 inner exhausted，进入同一父 scope 后继 |
| outer[i] 选择 continue | 进入 outer[i+1]；其中每个 inner 是新实例，从第 1 轮开始 |
| outer[i] 选择 break | 后续 outer 轮次及其所有子孙节点进入 Skipped，执行外层选中后继 |
| 内层节点失败且重试耗尽 | 沿用 Run failure 合同；不自动转成该层或祖先的 break/exhausted |
| 取消 Run | 沿用整 Run cancel；不新增“仅取消子 Loop”语义 |

内层退出不会隐式增加外层轮次或提前终止外层。需要决定外层继续时，作者必须通过父 scope
中的普通节点和父结果节点表达，不能在内层 Judge outcome 中编码跨层跳转。

### 6.2 示例运行轨迹

第 5.4 节完整展开为 `2 × (1 + 3 × 2 + 1) + 1 = 17` 个 execution nodes。
可用以下确定性轨迹作为 golden fixture：

1. outer[1].prepare 完成；inner[1,1] review=revise，inner[1,2] review=approved。
2. inner[1,3] 两个节点 Skipped；outer[1].assess=repeat。
3. outer[2].prepare 读取 outer[1].assess；inner[2,1] 的 previous result 为 null。
4. inner[2,1..3] review 均为 revise；第 3 轮以 exhausted 路由到 outer[2].assess。
5. outer[2].assess=done，publish 完成；共 15 个 Completed、2 个 Skipped。

inner[2,1] 不读取 inner[1,2] 的结果；inner[2,2] 精确读取 inner[2,1].review。
publish 只读取本次实际选中的外层结果，不混入 outer[1].assess。

### 6.3 分支、并行和 join

body 继续允许既有 fan-out/join。body 内部的 barrier 只包含当前实例中由合法边连接的 upstream；
跨轮次 continue 和兄弟 Loop 汇入父 scope 的 join 通过第 7.4 节的编译边连接。
父后继可以有来自子 Loop 多个潜在退出轮次的静态 upstream；必须等待这些 upstream
均为 Completed/Skipped，并且至少一个真实选中边到达该后继。

内层提前退出要完整跳过当前实例的后续轮次，才能解除父后继 barrier。
不能仅跳过未来入口而留下其后代 Pending，也不能因为一个分支被跳过而跳过仍可从其他
选中分支到达的 join。前台推进与恢复扫描必须产生相同的完成/跳过集合。

## 7. 递归编译

### 7.1 单一编译流程

采用一个递归 scope 编译流程，不复制单层和嵌套两套调度算法：

1. 对原始 authoring 做 strict-key、局部 ID、节点能力和 scope 结构校验。
2. 递归预计算深度与展开节点数，使用 checked arithmetic；超限在展开前返回。
3. 将每个 scope 的直接节点分为普通节点和 Loop；计算可解析的入口及各逻辑出口。
4. 普通节点生成一个叶子副本；Loop 按轮次生成 body，递归传入祖先 frames。
5. 普通边解析同 scope 的目标；目标是 Loop 时落到其第 1 轮普通入口。
6. result 的 continue 连接本 Loop 下一轮入口；break/exhausted 解析父 scope 目标。
7. 生成节点、边、上一轮结果引用和逻辑 descriptor 的一致映射。
8. 对最终扁平图复用既有 DAG 校验，确认单入口、可达性、唯一 terminal/final_output。
9. 有界序列化并计算 plan hash，保存完整 snapshot 后才能派发。

局部变量必须携带完整 authoring path，以便错误定位到
`$.runtime.state_machine.nodes.outer.loop.nodes.inner.loop.nodes.review`。
不能用生成的 hash ID 代替用户可修改的 authoring 位置。

### 7.2 编译版本选择与旧合同

| 输入 | 新创建计划 |
| --- | --- |
| 普通 version 1 DAG | 保持无 execution plan 的既有路径 |
| version 2，所有 Loop 深度均为 1，或没有 Loop | 保持 compiler v1，原 ID、序列化和外部投影不变 |
| version 2，存在深度至少为 2 的 Loop | 使用 `bcs.fixed-loop.compiler/v2` |

版本选择仅由实际 authoring 结构决定，不由开关状态、运行时间或显式 feature 列表决定。
保留 compiler v1 格式的依据是已有快照、golden fixtures 和现有单层消费者，不增加新旧
语义别名。scope 校验、递归展开和边映射共用一套实现；版本差异限于 identity、序列化和
投影边界，v1/v2 的持久化格式和 hash 合同分别固定，不保留两套业务编译算法。

### 7.3 compiler v2 execution ID

所有处于 Loop 内的叶子，包括嵌套计划中只有一层祖先的叶子，采用：

```text
prefix bytes = "bcs.fixed-loop.compiler/v2/execution-node\0"
tuple = [definition_id, definition_version, [[loop_id, iteration], ...], body_node_id]
digest = SHA-256(prefix bytes || compact UTF-8 JSON(tuple))
execution_node_id = "ln-" + digest 的前 32 个小写十六进制字符
```

frames 顺序固定为外到内。使用数组和结构化序列化，不能依赖字符串拼接分隔符。
最大轮数不参与 ID tuple，但进入 plan 内容和 hash。顶层普通节点仍沿用局部 authoring ID。
所有生成 ID 必须检查碰撞；重复不得覆盖已有 node。禁止把 `/` 路径直接放入持久化 node ID。

同一 Definition ID/version、同一 authoring 和 compiler 必须产生 byte-stable plan。
节点 map 和 edge list 使用确定排序；不可引入随机 UUID、当前时间或迭代容器的不稳定顺序。
现有 validation preview 的临时 Definition identity 行为不改变；只有 identity 相同才比较生成 ID。

### 7.4 边与结果映射

- continue：`ControlOnly`，真实 outcome 和 `loop_route.logical_outcome` 相同。
- exhausted：`Artifact`，真实 outcome 保留为 continue outcome，逻辑 outcome 为本层 exhausted。
- break：`Artifact`，真实和逻辑 outcome 相同。
- 普通边：`Artifact`，没有 `loop_route`。
- `loop_route` 仍为一个对象，无需数组；所属 Loop 是 source 的最近一层 frame。
- 允许内层和外层使用相同 outcome 名，因为它们的执行 source 和 scope 不同。
- 显示名称按原规则继承，不参与选路、identity 或 previous result 判定。

因 entry/result 都是普通叶子，一个执行节点至多是一个 Loop 的 entry/result。
因此继续使用 `is_loop_entry/is_loop_result`；不引入多层控制角色位图。

## 8. 执行元数据与上下文

### 8.1 公共 Loop frame

新增稳定值对象 `StateMachineLoopFrame`：

```json
{"loop_id": "inner", "iteration": 1, "max_iterations": 3}
```

三个字段都必填，ID 为该层局部 ID，轮次为从 1 开始的整数，满足
`1 <= iteration <= max_iterations`。Frame 本身不含可变执行状态。

### 8.2 节点元数据

compiler v2 的 Loop 叶子在 `CompiledNodeMetadata` 和公共
`StateMachineNodeExecutionMetadata` 中增加必有非空值的 `loop_path`：

```json
{
  "definition_node_id": "review",
  "loop_id": "inner",
  "iteration": 1,
  "max_iterations": 3,
  "loop_path": [
    {"loop_id": "outer", "iteration": 2, "max_iterations": 2},
    {"loop_id": "inner", "iteration": 1, "max_iterations": 3}
  ]
}
```

原三个 loop 字段始终等于最后一帧，保留是为了现有 Node/Event/消息消费者，不是独立事实源。
不一致必须报错；不能择一使用。`definition_node_id` 仍是当前 body 内的叶子局部 ID。
完整 authoring node path 可由所有 frame ID 和叶子 ID 唯一确定。

- compiler v1：新增字段缺省且序列化时省略，不生成空数组或 null，不修改原 hash。
- compiler v2 Loop 叶子：路径必填；只有一层时也提供长度为 1 的数组。
- 顶层普通节点：公共 `execution` 仍省略；内部 metadata 的新增路径字段省略。
- 读写端按计划版本校验上述条件，不能把 v2 缺路径解释为 legacy。

在原格式完成版本和 hash 校验后，提供统一的只读路径访问器：v1 Loop 由既有字段提供单帧
视图，v2 使用已验证的 `loop_path`，顶层普通节点返回空路径。运行时的作用域比较与上下文
构造共用此视图，不在各调用点重复分支；不新增持久化字段或缓存副本，也不把归一化结果
写回 snapshot 或旧版 wire payload。

### 8.3 previous result

继续只有当前所属 Loop 的 entry 得到 `LoopContext`，普通非 entry 节点仍不注入该对象。
嵌套计划的 context 增加同一个 `loop_path`，原 loop/iteration/max 字段和最近 frame 一致。
`previous_result` 的原字段与首轮显式 null 语义保留。

对于 path `[outer i, inner j]`：

- j=1：`previous_result` 必须为 null，不因 i>1 而读取上一次外层中的 inner。
- j>1：引用 `[outer i, inner j-1]` 的 result execution node。
- 更深层同理：祖先 frames 完全相同，只允许最后一帧 iteration 减 1。
- 外层 entry 引用外层上一轮普通 result，不能用子 Loop 最后一个完成节点代替。

`previous_result_node_id` 仍是单个编译期明确的 execution ID。
共享构造器必须校验 run ID、node ID、完整祖先路径、相邻 iteration、result 身份、
Completed、非空 outcome、artifact、完成时间，以及实际选中的 ControlOnly continue 边。
任一事实缺失都返回错误，不返回缺上下文的入口，不 fallback 到“最近完成的节点”。

### 8.4 Prompt、HumanInput 和投影

嵌套计划的 Loop 叶子 prompt 增加 `[Loop Path]` 文本，按外到内逐行显示
`outer: 2/2`、`inner: 1/3`；该文本仅渲染结构化 metadata，不从 node ID 解码。
入口继续显示原 `[Loop Context]` 和 `[Previous Iteration Result]`，其内容仅属于最近 Loop。
单层 compiler v1 prompt 保持原格式。

父入口输出通过正常选中 Artifact 边传给子 Loop 首轮入口。
子 Loop 后续入口仍只自动收到本实例上一轮结果；不自动重复父入口或所有祖先历史输出。
需要跨轮保留任务信息时，由 body 正常输出传递，遵守当前 upstream contract。
上一轮 ControlOnly 来源不在 `[Upstream Outputs]` 或人工 upstream_artifacts 中重复出现。

Bot prompt、PendingHumanNodeView、HumanInputReadyEvent 和 channel 通知使用同一份
上下文构造结果。直接通知包含当前路径和本层 previous result；fixed_group 继续遵循现有
隐私规则，不因嵌套泄露私密结果。通知重试/恢复复用已保存的 notification_text 和请求身份。
人工回复继续绑定 execution node + attempt；父/兄弟/旧轮次的 response ref 不能完成当前节点。

## 9. Snapshot、持久化与恢复

### 9.1 存储形式

保留已有 `execution_plan_json`、`execution_plan_content_hash`、
`execution_plan_compiler_version`。路径元数据进入 plan JSON。
Node Run 的 `(env, run_id, node_id)` 主键、attempt、correlation、lease 和 checkpoint 不变。
本方案不要求数据库 DDL 迁移，不增加 LoopRun 表或 parent_iteration 列。
如果实施发现真实 schema 限制，必须在修改前补充本 spec 并使用新的迁移，禁止改历史迁移。

### 9.2 Reader 与 hash

reader 明确支持 compiler v1/v2，逐版本验证，不能“未知版本先当 v2 试一下”。
hash 继续是该版本稳定序列化的整个 plan 的 SHA-256，不根据当前 authoring 重算计划。
扩展公共 Rust struct 时必须使用保证旧字段顺序及省略规则的序列化策略；仅通过
`serde(default)` 成功反序列化不足以证明历史 hash 兼容。

compiler v2 reader 必须验证：

1. envelope compiler 与 plan 一致，hash 匹配，authoring 仍是 version 2 hierarchical。
2. execution graph 只有受支持叶子节点且是合法 DAG，metadata 一一覆盖。
3. 每个 frame 都能逐层解析到 authoring 的 Loop，轮次/上限匹配，叶子存在。
4. 执行逻辑键 `(loop_path 的 ID/iteration, definition_node_id)` 唯一且展开全集完整。
5. compiled 叶子除 transitions 外与保存的 authoring 对应节点一致。
6. ordinary、continue、break、exhausted 的 source/target、scope、projection 和逻辑 outcome 全部合法。
7. previous result 与相同祖先实例的上一轮 result 精确对应。
8. 不存在 Loop 作为可执行节点、跨祖先轮次连边、缺失/重复 metadata、额外 transition。

这些检查验证保存的映射，不用当前 ID 生成器替换历史 identity。
读取历史计划不应用当前的编译深度/节点数/字节配置；缩小配置不能使已准入 Run 无法恢复。

### 9.3 创建与故障恢复

保持现有分步持久化与 snapshot barrier：完整 Run/Node materialization 和完整 snapshot
成功后才能开始派发；写失败向上传播。不得保存部分嵌套 plan 后再边运行边补编。

result completion、后续 skip、frontier dispatch 不要求跨步骤大事务。
FO 必须从 immutable plan 和已提交 Node Run 补做未完成工作，覆盖：

- 内层 result 已完成，未来内层节点只跳过了一部分；
- 内层退出已经提交，父后继尚未派发；
- 外层 continue 已提交，新外层入口尚未派发；
- 外层 break 已提交，未来整个嵌套子树尚未完全 skip；
- 人工请求/dispatch 已持久化，外部发送结果未知；
- retry、timeout、Judge 与迟到终态竞争；
- cancel、Session activation 变化、lease 到期和换主。

继续复用 Run/attempt CAS、租约 fencing 和已有的 scanner 退避。
不得引入进程内 stack/counter 作为恢复事实；`loop_path` 是不可变身份，不是运行时栈。
并行分支和嵌套深度不改变“每个 attempt 最多一次有效结算”的合同。

消息历史启用时，继续通过既有接受命令在同一事务内提交节点事实和不可变 `history_message`
checkpoint，再幂等写入 messages 并确认。投影未完成不能返回接受成功或派发依赖后继；
retry/Judge 前须先固化已接受输出，不能覆盖失败 attempt 的消息。重放直接使用保存的
`NewMessage` 正文、完整 execution、受众和时间，不按当前节点状态或 plan 重新渲染。
Pending 历史补写覆盖终态 Run，关闭嵌套准入或关闭历史 persistence 不阻止已接受事实恢复；
复用现有终态 `history_repair` 标记，清理不能删除 Pending 历史事实或重复网络投递。

### 9.4 rerun

整 Run rerun 复制源 authoring、plan、bindings 和 generated IDs，在新 run_id 下重新物化节点。
不复用源 artifact/status，不继承源已执行到的轮次，不执行当前编译器。
已保存计划不受后来缩小的编译 limits 影响；是否允许创建新的嵌套 Run 仍受第 12 节准入开关控制。
新 run_id 同时区分新的消息身份；不复制源 Run 的历史 checkpoint 或消息接受状态。

## 10. API、事件和运行图

### 10.1 投影传播

共享 schema 扩展必须同步以下现有消费者：

- start/get/rerun 的 `node_execution_metadata`；Node view 和 Run graph；
- Definition validation preview 的执行节点与 logical Loop descriptors；
- `state_machine.node.*` 事件中的 execution，包括 completed、started、retry_scheduled；
- 已持久化的消息 execution metadata，以及历史查询中的透传；
- PendingHumanNodeView、HumanInputReadyEvent、通知 checkpoint；
- legacy HTTP 和 V1 HTTP 两套 adapter；BCS CLI；
- BCS panel 运行图与节点详情；Frontend Definition preview。

OpenAPI 和 Event 的 `additionalProperties: false` 保留，显式增加合法字段及嵌套 fixtures，
不能通过整体放宽 schema 来接受新 payload。新客户端同时理解旧字段省略和新路径；
旧客户端对嵌套 payload 不保证兼容，必须在首次开放嵌套预览或返回嵌套运行数据前完成部署协调；
关闭执行准入不阻止预览，不能单独充当消费合同的发布屏障。
日志可以包含完整路径；指标不增加 loop ID、node ID、轮次路径等高基数 label。
IterationStarted/Completed 继续按每层普通 entry/result 的首次有效 CAS 计数，retry 不重计。

### 10.2 Logical Loop descriptor

保留 `graph.loops` map。compiler v2 图递归包含每个 logical Loop，key 为该 Loop 的
definition path 用 `/` 连接，例如 `outer`、`outer/inner`。局部 ID 禁止 `/`，故 key 无歧义。
这只是展示索引，不能作为 authoring transition target 或 execution ID。

descriptor 不新增重复的 `definition_path` 或 `parent_loop_id` 字段。key 的各段均为非空合法
局部 ID；按 `/` 分段即可取得逻辑路径，去掉末段得到父 key，追加直接子 Loop ID 得到子 key。
现有字段保持原语义：`body_node_ids` 是直接子节点 ID，可能包含子 Loop；entry/result
仍是直接普通叶子 ID。compiler v1 的 descriptor map key 与字段保持不变。
逻辑路径只负责容器层级，具体运行实例仍由节点 `loop_path` 中的祖先轮次区分。

### 10.3 UI 行为

- 容器按 definition path 建树，每个容器内可显示普通叶子和子容器。
- 某层轮次选择的状态 key 必须包含该 Loop instance 的祖先轮次，不能只用 `loop_id`。
- 改变外层轮次时，内层历史与当前状态来自新选中的实例，不能沿用另一实例的“已通过”。
- preview 默认每层显示第 1 轮，并标注各层最大轮次。
- runtime 默认选择该实例正在运行的轮次，否则最后实际进入的轮次；未进入显示结构占位。
- 历史展开按实际进入的轮次展示；未来未执行节点不伪装为已执行历史。
- 节点详情显示完整路径、局部 node ID、execution ID、attempt、真实 outcome 和逻辑出口。
- 子 Loop break/exhausted 连到父 scope 后继；不画成父 Loop 已退出。
- 数据缺少必需路径或映射矛盾时显示明确错误，不按重复局部名称合并节点。

布局消费服务端映射，不能在前端推导业务 progression、重建 generated ID 或修改选路。

### 10.4 消息历史接入

沿用第 2.1 节引用的状态机消息历史合同；本次只增加路径传播和嵌套回归：

- 路径保存在 `content.metadata.state_machine.execution.loop_path`，随接受 artifact 和历史
  checkpoint 冻结。`history_schema_version: 1` 与 checkpoint 的 `schema_version: 1`
  独立于 compiler v2，不因嵌套升级历史格式版本。
- 复用环境/Session 范围内的 `(run_id, execution_node_id, attempt, event)` 身份、旧 Human
  response 归一化和物理消息 ID helper；execution ID 已隔离父轮次，不再把路径拼入消息主键。
- `state_machine_history.persistence_enabled` 开启且 `Session.created_at` 不早于
  `message_history.state_machine_cutoff_timestamp` 时，专用状态机历史走 messages-only。
  one-shot、重新激活或 rerun 不以新 Run 创建时间改变 Session 分流。其他情况保留原读取合同。
- messages-only 不为补路径读取 Run/Node/plan，也不因空页、缺消息或查询失败回退运行态。
  原链路按稳定身份优先采用持久记录，保留旧 attempt；受众不可见的持久记录不能被可见快照替代。
- 保留两套 HTTP 的 Full/Participant 权限、分页与混合 Chat/ManagerWorker 的原有 owner、
  cutoff 和窗口补偿；新增历史投影不能挤占普通消息的新参与者窗口。
- 公共 panel/prompt/output 消息沿用共享 projector：不在顶层 `run_id` 或 `historyMeta`
  注入 Run 级对话分组，工作流身份留在 `metadata.state_machine.run_id`；
  `published_result` 和普通聊天保持原聚合。嵌套各节点、父轮次和 attempt 须独立展示。

## 11. 资源限制与数据库成本

### 11.1 编译预算

`collaboration.fixed_loop_limits` 新增 `max_fixed_loop_depth: u32`，默认 `4`，合法值为正整数。
最外层 Loop 深度为 1；这是所有 authoring 结构的最大语法深度，不取决于运行时是否走到该分支。
实现必须在遍历中有界检查深度，不先无界递归展开后才计数。

其他默认值不变：每层 max iterations=32、每个 body 的直接节点数=64、
整个编译计划 nodes=2048、bytes=4 MiB。body 直接节点数包括子 Loop 本身。
具体 Definition 的每层 max_iterations 仍由作者声明，不自动取服务端上限。

递归成本：

```text
cost(ordinary) = 1
cost(loop) = loop.max_iterations × Σ cost(loop.body.direct_nodes)
compiled_nodes = Σ cost(top_level_nodes)
```

所有加法/乘法 checked，任何分支即使预计不会执行也计入预算。
先做数量预检，再边展开边核算节点、metadata、边的字节预算，最终有界序列化验证完整 plan。
路径元数据也计入字节数。失败不能产生 Run、snapshot、dispatch 等副作用。

### 11.2 当前存储访问事实

以下来自现有 DB store，不把一个 Repo 调用笼统等同于一个事务或一个网络往返：

| 路径 | 当前访问形态 | 嵌套后的约束 |
| --- | --- | --- |
| 普通 create_run | 一个事务内插入 Run 和全部 N 个 Node Run；节点为多值 INSERT | 总行数为 N+1，不能因为 depth 改成逐节点事务 |
| session-idle 创建/rerun | 现有事务内按 48 行分块，含 Session 防重/锁定步骤 | Node INSERT 数为 ceil(N/48)，维持原原子边界 |
| snapshot 保存/加载 | 保存完整 JSON；加载走 DB 查询，没有在该 store 路径看到 plan 缓存 | 每次多读的字节受计划上限约束，不为每层新增 snapshot 查询 |
| previous result | 后续轮次 entry 通过 execution ID 查询一条 Node Run | 每次构造最多一个结果读取，与 depth 无关 |
| prompt/barrier | 按 U 个静态 upstream 查询节点，包含已跳过的候选出口 | 不能新增逐祖先历史查询；报告 U 与节点行数成本 |
| 前台 skip | 对遍历的节点先 SELECT，未开始节点再条件 UPDATE；竞争可能追加重读 | K 个正常待跳过节点约 2K 次 DB 语句，不能声称提前 break 是 O(1) |
| progression recovery | 多次读取该 Run 全部 N 行，derive 可达性；逐节点 guarded skip | 限制单 Run/单页工作；不能根据小图测试宣称无限扩展 |
| 历史接受与投影 | 原接受事务增加 checkpoint INSERT；随后 messages 幂等追加及 checkpoint 确认 | 沿用事务/CAS，成本报告计入查重、物理序号事务与确认写 |
| 历史补写 | 独立 Pending checkpoint 分页；终态页批量查询 `history_repair` 标记 | 不逐条回查 Run/Node/plan；保留有界扫描及失败退避 |
| 专用 messages-only 历史 | Session 分流后按受众过滤读取消息页，无运行表读取 | 直接透传已存路径，保留 LIMIT 前过滤及 lookahead |

数据库插件的 transaction 是一个 port 调用，但可能执行多个 SQL 和多次数据库协议交换。
性能报告必须分别列 Repo 调用数、SQL 数、读取/更新行数与事务大小。
在 N=2048 时，48 行分块需要 43 条节点插入语句；首轮退出可能仍需跳过约 N 个节点。

当前 progression scanner 每页至多 32 个 Run，间隔基线为 1 秒，并有 leader/backoff 控制。
32 个满容量 Run 的一次全节点读取就有 65536 行；一页可能多次读取，不能据此推算成
固定每秒 65536 行，也不能忽略同池的 Session、通知、清理扫描及前台并发。
历史补写另有每页最多 100 个 checkpoint、间隔基线 5 秒的扫描；不得算作免费的恢复工作。
路径随正文在 checkpoint、messages 及相关 artifact 中保存，4 MiB plan 上限不限制整个 Run 的
消息存储总量；报告须另计这些 payload 的字节成本。

### 11.3 本次实现要求与优化边界

- 递归解析路径和 ancestor identity 只在内存中的 immutable plan 上进行，零新增 DB 查询。
- 不自动读取每层 previous result；按第 8 节只有当前入口所属层需要一条结果。
- 不提高现有节点/字节/scan page 默认上限，不增加并行恢复 worker。
- 在相同历史 persistence/cutoff 配置下使用同一扁平 DAG 对照：增加 nesting metadata
  不应增加同形 DAG 的 SQL 数；分别测量历史启用/关闭，不能把双写本身的成本归因于 depth。
- 必须验证 17 节点示例、接近 2048 节点、接近 4 MiB、32 个并存 Run、大范围 early break、
  DB 持续失败和重复恢复；报告 SQL/行数、扫描耗时及事务范围。
- 有界扫描和现有退避必须继续生效；一个失败 Run 不能阻塞后续页面的可恢复 Run。
- 若测得必须批量读取/skip 才能满足实际容量，作为本需求的显式 store/port 子改动补充
  conformance 和查询数断言；保留 env 隔离、Run active 条件、CAS 和错误传播。
  不引入未经验证的全局缓存，也不把外部网络发送放入 DB 事务。

本 spec 不承诺未测量的吞吐或时延。满容量成本报告是开启嵌套执行的验收项。

## 12. 配置、准入与发布兼容

### 12.1 开关

保留现有 `collaboration.loop_execution_enabled`。
新增 `collaboration.nested_loop_execution_enabled: bool`，默认 `false`，控制新的嵌套 Run 准入。
该字段表达能力，名称不包含 experimental、phase 或发布状态。

| 操作 | 要求 |
| --- | --- |
| 校验/预览嵌套 Definition | reader/compiler 支持并满足当前 limits；执行开关关闭不阻止有效预览；depth limit 为 1 时拒绝嵌套并不返回执行图 |
| 新建实际含嵌套的 Run | 两个执行开关都为 true，且遵循现有权限、Session、绑定和 Judge 可用性检查 |
| rerun 嵌套 Run | 同样要求两个执行开关；复制源 plan，不套用当前编译 limits |
| 执行/恢复已准入的嵌套 Run | 支持该 plan 的 reader，保持既有总 Loop 开关要求；不再次检查新增准入开关 |
| 查询历史 Run/图/上下文 | 按权限读取 snapshot；消息历史另按第 10.4 节选读；均不受新增准入开关影响 |
| 单层 Loop | 新增准入开关不影响原行为 |

`nested_loop_execution_enabled=true` 且 `loop_execution_enabled=false` 是无效启动配置，
应明确报错。关闭新能力时只关闭嵌套准入开关，保留总 Loop 开关以排空已有 Run。
配置加载和 wiring 只在 config/bootstrap/composition root 完成，core 不读原始环境变量。
历史 persistence 与 cutoff 沿用独立配置及发布合同；开启嵌套不自动开启双写或重设 cutoff。

满足编译 limits、验证有效但无法执行时，沿用 `VALIDATION_ONLY_FEATURE` warning，路径指向首个嵌套 Loop，
说明当前部署只提供 validation/preview。启动拒绝沿用 application `InvalidRequest`
及现有 adapter 映射；错误带 feature 名称，不新增未经约定的 HTTP 状态码。

### 12.2 发布顺序

1. 先发布同时支持旧 payload 和嵌套路径的 API/Event schema、CLI、panel、Frontend 及实际
   消费者。仍在使用的页面、缓存客户端和严格 schema 校验器也必须完成兼容升级。
2. 滚动部署支持 compiler v1/v2 的服务端；每个升级实例显式配置
   `max_fixed_loop_depth=1`、`nested_loop_execution_enabled=false`。与服务端打包的消费者
   可在此阶段一起升级；深度限制使新旧实例都拒绝嵌套编译，不向旧消费者泄露嵌套预览。
3. 确认消费者兼容，且所有可能提供预览、接收回调、查询 Run 或承担恢复的实例均支持 v2。
   再将深度上限统一调整为目标值（默认 4），开放嵌套 validation/preview，执行准入仍关闭。
4. 验证嵌套预览与关闭准入的行为，并完成第 15 节验收，再开启新的嵌套 Run 准入。

单个创建入口升级不充分。当前共享恢复扫描不能假定旧实例会按 compiler 自动分流。
混合二进制版本期间同时保持深度上限为 1 和新准入关闭；不新增预览开关或跨版本调度隔离系统。
编译深度限制与执行准入分别约束预览结构和新 Run 创建，不能互相替代。

### 12.3 回滚

先关闭嵌套准入，保留新 reader 排空已有嵌套 Run；必要时使用既有 cancel 路径确定性终止。
若同时关闭新嵌套预览，可将深度上限降为 1；这不会隐藏已保存的嵌套 Run/图/消息。
排空后仍需保留兼容消费者，并考虑历史 Run 查询：旧二进制不能读取 v2 plan。
优先以关闭能力代替二进制降级；若必须降级，只能在不承接 v2 历史读取的明确运维边界中进行。
不把 v2 快照重写成 v1，不重算历史 ID/hash，不删除历史数据以掩盖不兼容。

## 13. 错误合同

| 条件 | 诊断与副作用 |
| --- | --- |
| target 越出当前 scope、入口/结果是子 Loop、body 有环、多 terminal | `INVALID_DEFINITION`，准确 authoring path，无 Run 写入 |
| 缺字段、未知字段、非法 ID 或类型 | 复用现有 FORMAT/INVALID_DEFINITION 分类，保留深层位置 |
| 深度、节点数、字节数超限或 arithmetic overflow | 校验失败并标记现有 ResourceLimit instrumentation，指出对应 limit |
| Judge/绑定等能力缺失 | 沿用既有 UNAVAILABLE_FEATURE 或运行准入错误，递归节点不能漏检 |
| 新执行能力关闭 | 满足编译 limits 的有效预览加 warning；启动/rerun 拒绝，零执行副作用 |
| snapshot 版本未知、hash 错、路径/边/结果映射损坏 | 显式错误，停止推进，不 fallback 到当前 Definition |
| previous result 缺失或来自另一父轮次 | 显式上下文错误，不派发缺上下文任务 |
| 任意持久化失败 | 传播错误，保留已提交事实供现有 FO 补做，不返回伪成功 |

错误文本不暴露 artifact 全文、凭据或私密 prompt；日志可包含 Run/execution ID 和作用域定位。

## 14. 实现归属与实施切片

### 14.1 变更落点

| 区域 | 责任 |
| --- | --- |
| `crates/contracts/bcs-domain` | Loop frame、metadata/context 的版本兼容 DTO |
| `crates/service-api/bcs-service-api` | graph descriptor、application/port 的投影合同与 Context |
| `crates/service-api/bcs-config-api`、bootstrap config | 深度配置、准入配置、schema 校验与 wiring |
| `crates/services/bcs-collaboration-runtime` | strict validator、递归 compiler、snapshot reader、context、runtime/history projection 与恢复 |
| `crates/services/bcs-collaboration-store` | JSON round-trip、历史字节兼容、接受事实与历史 checkpoint conformance；不拥有选路策略 |
| `crates/services/bcs-channel` 及 channel 合同 | 人工 context 与保存后的通知渲染/恢复 |
| `crates/services/bcs-eventing`、`bcs-message` 与相关 store | execution metadata 校验、保存和透传 |
| `crates/adapters/http/bcs-http`、`bcs-api-http` | 只做 DTO/错误投影，不实现 scope 解析 |
| `api-contracts/v1/openapi`、`api-contracts/events/v1` | 共享 schema、catalog、fixtures 与严格校验 |
| `crates/tools/bcs-cli` 与 authoring reference | 示例、capability、命令输出中的完整路径 |
| `assets/panel/src` | 递归运行图、历史轮次、人工入口、详情 |
| `apps/frontend/src`（相对仓库根） | API 类型、Definition preview、布局与展示测试 |

所有业务解释留在现有 core/runtime；application 继续编排用例，adapter/channel/UI 只消费结果。
不新增 Plugin API，不引入 core 到 DB/cache plugin 的依赖；已有 repo 边界保持。
按当前用户范围约束，已有文件保留布局，只做必要局部修改，不以 1000 行门禁为由拆分、搬迁
既有实现或测试。新增编译/映射职责可放入明确的新模块，不借此搬迁无关代码。
完成前检查全部改动源码的行数；若既有超长文件导致门禁失败，记录具体文件与结果，
不擅自拆分或放宽检查。不运行全局 formatter。

### 14.2 实施顺序

1. **合同与 fixtures**：固定 frame、v2 metadata、descriptor、开关语义及有效/无效 authoring 示例。
2. **纯编译**：递归 validator、预算、scope resolver、ID、边映射；通过 golden 与 v1 回归。
3. **快照与存储**：v1/v2 readers、hash、Memory/SQLite/MySQL round-trip、坏快照拒绝、rerun。
4. **运行时**：context/prompt、人工作用域、选路/skip/join、故障恢复及查询成本验证。
5. **消费方**：两套 HTTP、事件/消息、通知、CLI、panel 和 Frontend 同步实现并通过 conformance。
6. **发布验收**：live stories、全模块门禁、容量/失败路径报告、混合版本与回滚演练。

前五个切片可逐步合入；实际部署须遵循第 12.2 节，不能只凭执行准入关闭就开放嵌套预览。
本次设计文档为本目录 spec/tasks；实施时追加 validation，不要求修改根目录 docs/scripts/.github。

## 15. 测试与验收

### 15.1 纯编译与结构校验

| 编号 | 必须覆盖 |
| --- | --- |
| C01 | 2 层与 3 层嵌套，等于深度上限通过，超过拒绝 |
| C02 | 第 5.4 节得到 17 个节点，无 Loop 可执行节点，只有 publish 为 final |
| C03 | 不同父轮次、不同兄弟 scope、同名 body 节点、内外同名 Loop 均无 ID 碰撞 |
| C04 | 内层各轮 continue、early break、last continue→exhausted 都落入正确 scope |
| C05 | 多 continue、多 break、空 break 合法；空 continue 或 outcome 分区错误拒绝 |
| C06 | 同 body 多个串行子 Loop，以及并行子 Loop 后 join，展开连边正确 |
| C07 | child 作为 entry/result、跨 scope 引用、body cycle、孤岛、多入口/终点均拒绝 |
| C08 | 各层普通能力校验生效：Judge、assignee、未知键、guard、final_output 等 |
| C09 | 节点/字节预算边界、checked overflow、路径元数据预算均在副作用前拒绝 |
| C10 | 同 identity/input 计划 byte-stable，v1 既有 ID/JSON/golden 保持不变 |
| C11 | feature 自动推导；满足 limits 时关闭执行仍可 preview；显式 feature 不改变非嵌套编译格式 |

### 15.2 上下文与运行

| 编号 | 必须覆盖 |
| --- | --- |
| R01 | 第 6.2 节轨迹：15 Completed、2 Skipped，结果传播准确 |
| R02 | 每个父轮次中 child 第 1 轮 previous_result=null，后续只取本实例相邻结果 |
| R03 | 外层 entry 只取外层 result；不自动暴露祖先或兄弟历史 |
| R04 | entry retry 路径和 context 不变；下一 iteration attempt 从 0 开始 |
| R05 | 内层退出不结束外层；外层退出跳过后续完整子树；各层 exhaustion 保留真实 outcome |
| R06 | 多分支、并行子 Loop、shared join，不因另一分支 skip 提前派发或永久等待 |
| R07 | 未选中边及 ControlOnly 不投影 artifact，parent successor 只取实际退出结果 |
| R08 | 内外层 HumanInput entry，唯一 response ref；旧父轮次/旧 attempt 回复拒绝 |
| R09 | timeout、重试耗尽、Judge 非法 outcome、cancel 沿用原失败/取消语义 |
| R10 | Run/Node/Graph/Event/Message 的同节点 execution metadata 一致 |

### 15.3 FO、快照与兼容

| 编号 | 必须覆盖 |
| --- | --- |
| F01 | 内层 break 后部分 skip crash，重启补齐，父后继只有效派发一次 |
| F02 | 外层 continue 后派发前 crash，恢复准确祖先实例；旧轮次迟到事件无效 |
| F03 | 外层 break 后部分子树 skip crash，最终全部 Completed/Skipped，不能遗留 Pending |
| F04 | completion、retry、cancel、lease fencing 竞争及 terminal 重放 |
| F05 | HumanInput 通知持久化后 crash/重试，复用保存文本和 request identity |
| F06 | Memory、SQLite、真实 MySQL 保存/读取/rerun 新 plan；旧 plan byte/hash 不变 |
| F07 | 即使重新 hash，错误祖先路径、跨父轮次边、缺节点、错误 previous result 仍拒绝 |
| F08 | 缩小 compiler limits 不影响历史恢复/rerun plan；reader 不调用 compiler |
| F09 | 关闭新增准入拒绝 start/rerun，但已准入 Run 可完成；历史查询可用 |
| F10 | 未知 compiler 明确拒绝；总开关/嵌套开关无效组合启动失败 |

### 15.4 消费合同、UI 与成本

- 更新 OpenAPI/Event strict schemas 和 fixtures，验证旧 payload 省略行为及新路径的必填条件。
- 新客户端兼容旧服务端 payload；depth limit=1 时嵌套校验报超限且不返回执行图。
- depth limit>=2、嵌套准入关闭时，嵌套预览可用而 start/rerun 拒绝；特别覆盖内外 Loop 同名，
  确认 Frontend 不按局部 loop_id 合并层级或查找不存在的叶子。
- 两套 HTTP adapter、CLI 和消息历史均透传完整路径；人为矛盾的最近帧字段不得被静默接受。
- 在现有 `history_loop`、`history_identity`、`history_persistence` 测试中增加嵌套变体：
  Judge 失败后 retry 保留各 attempt；消息写失败、已写未确认、终态/关闭开关后的补写均保留
  原路径、正文、受众和时间，不重复分配 sequence，不重复网络发送。
- 扩展 `history_read`、bootstrap `state_machine_history_read` 及现有 consumer fixtures：
  cutoff 前/等于/后及旧 Session 新 Run 正确分流，messages-only 不依赖运行表，隐藏受众不回退；
  Workbench 与 CLI 保留各实例/attempt，普通聊天及 published result 聚合不变。
- panel 切换 outer 轮次后 child history/status 隔离；同名子 Loop、未进入容器和三层嵌套可正确展示。
- Frontend preview 递归容器与真实扁平映射一致；inner exhausted 不显示为 outer exhausted。
- direct-assignee 通知 golden 与 fixed_group 隐私测试；人工查询与通知上下文一致。
- 按第 11 节记录正常、最大规模、32 个 Run、early break、DB 持续失败的成本，并保留查询数断言。
- 同历史配置的扁平 DAG 对照验证路径解析不增加数据库查询；验证历史补写失败的分页/退避、
  checkpoint/messages 字节成本及查询侧窗口补偿，保留已有 batch/CAS 边界。

### 15.5 Live stories 与门禁

采用两套真实服务故事 fixture，故障注入复用同一流程，不另建第三套业务场景：

1. 两层 BotTask：内层提前通过，外层继续；下一父轮次内层耗尽，再完成 Run，验证第 6.2 节。
2. 三层及 HumanInput：至少一层人工入口执行第二轮；检查 query、通知、回复身份和跨实例隔离。

对上述故事增加故障变体：在 inner exit/outer continue/子树部分 skip 的提交边界注入故障，
重启后完成；同时发送旧轮次和旧 attempt 回调，验证无错误推进。
至少一套故事开启历史 persistence 并命中 messages-only，复用故障变体验证接受后补写与
关闭嵌套准入后的恢复；历史开关/cutoff 完整矩阵由定向合同测试承担，不另加业务故事。

故事必须检查最终状态、artifact、metadata、graph、消息历史和人工入口，不能只断言 Run completed。
覆盖两个父轮次各自的内层实例，也必须覆盖外层 early break 的大量 Skipped 节点。

实施后运行受影响 Rust crate 单元/集成与 conformance、BCS architecture gate、OpenAPI/Event tests、
panel/Frontend 相应 CI，以及仓库规定的 BCS/BCN 和统一 singlebox coverage 门禁。
继续使用既有 runner 和 artifact verifier，不因嵌套降低覆盖阈值或另建绕过门禁的入口。
不能运行的门禁必须记录具体原因和未验证边界，不能以单元测试代替 MySQL/FO/live story。

测试按责任分层：结构与路由的边界和组合由纯编译测试覆盖，store conformance 验证各后端的同一组
快照/写入合同，真实服务故事验证跨边界执行和恢复。复用定义 fixture，无需在每个后端和
每个 UI 上重复深度、outcome、故障点的完整笛卡尔积；第 15.1–15.4 节的行为覆盖仍需保留。

## 16. 替代方案与规模判断

| 方案 | 结论与原因 |
| --- | --- |
| 递归展开为 DAG | 采用；复杂性主要在 scope/identity/合同传播，运行机制可复用 |
| 只解除 nested 校验 | 拒绝；当前 compiler、snapshot、context、UI 会错误解释嵌套 |
| 把完整路径拼进旧 loop_id | 拒绝；改变旧字段语义，仍不能表达不同祖先轮次 |
| 只增加 parent_loop_id | 不充分；知道逻辑父节点仍无法区分父第 1/2 轮实例 |
| 重置已有 Node Run 或用 attempt 表示轮次 | 拒绝；破坏历史结果、迟到事件隔离和重试语义 |
| 子 Loop 创建子 Run | 不采用；新增生命周期、父子完成、取消、恢复和事务合同 |
| 原生 cyclic/token engine | 超出需求；影响现有 DAG barrier、持久化、回放和前端 |
| 子 Loop 直接充当父 entry/result | 不在本次范围；需要多层入口 context、动态结果出口与 outcome 合成合同 |

在本 spec 边界内，不需要推翻现有实现，也不需要新增数据库执行模型。
工作量主要是一个递归编译与映射能力、一个计划版本 reader、现有消费者的路径传播，以及充分的恢复测试。
若扩展到非目标中的动态循环、子 Run 或复合入口/结果，应重新评估规模，不把它们隐含算作本次嵌套支持。

本版收敛后的最小实现边界是：一套递归编译算法、一套只读路径视图、现有 DAG 调度与存储，
descriptor 复用 map key，发布复用深度限制和执行准入，live 验收复用两套故事及其故障变体。
不能再删掉完整实例路径、v1/v2 格式边界或独立的新执行准入：它们分别解决同名/跨父轮次
隔离、真实历史快照兼容，以及停止接收新 Run 后排空已有 Run。把所有单层新计划也升级为
v2 会扩大合同迁移范围，并不能减少历史 reader，因此本次不采用。

## 17. 当前验证证据与完成标准

2026-09-21 在原基线 `225b6aab9` 运行，并于 2026-09-22 在 rebase 基线 `f49424bc4` 重跑：

```bash
cd apps/bcs
cargo test --offline -p bcs-collaboration-runtime \
  --test fixed_loop_compiler \
  --test fixed_loop_snapshot \
  --test fixed_loop_context
```

结果：compiler 21、snapshot 4、context 3，共 28 项通过。
其中已有测试明确拒绝 nested Loop；这些结果仅证明现有单层编译、快照和上下文基线，
不证明嵌套能力已实现。当前没有执行嵌套运行、真实 MySQL、UI 或 live FO 验收。

本次另运行新历史路径的回归：

```bash
cd apps/bcs
cargo test --offline -p bcs-collaboration-runtime --test runtime_progression history
```

结果：55 项通过（含该 target 引入的迁移测试），175 项被 filter 排除；覆盖现有历史接受、
Loop retry、补写、选读和关闭 persistence 后恢复，不代表新增嵌套验收完成。

本次文档复核通过：spec/tasks 共 16 个本地引用均存在，代码块、空白、JSON/YAML 和 shell
语法检查通过；10 项任务依赖无环，31 个 C/R/F 用例各有唯一主责任任务；命令中的 Cargo
package/test target 均存在。结构化示例与复核前一致。

原设计阶段的示例检查结果（本次未修改示例）：

- 使用标准 YAML parser 读取第 5.4 节示例，并用独立的结构化脚本检查作用域、结果分区和 DAG。
- 按本文规则递归展开得到 17 个唯一逻辑执行键；模拟第 6.2 节轨迹得到 15 Completed、2 Skipped。
- 校验 outer[2] 的 inner 第 1 轮没有 previous result，第 2 轮只引用同一父轮次的第 1 轮。

上述脚本仅验证 spec 示例内部一致性，不调用生产嵌套编译器，不能作为第 15 节实现验收的替代。

实施后应将实现证据记录为同目录 `validation.md`，包括对应提交/PR、测试命令与结果、
schema/旧快照 fixtures、成本报告、live story artifacts、发布配置和未验证边界。

能力完成必须同时满足：

1. 第 15 节全部适用测试通过，旧 DAG/单层 Loop 回归无变化。
2. 每个叶子的完整实例身份、路由和 previous result 可从 immutable plan 唯一验证。
3. FO/rerun 不重编，旧快照 hash 不变，新计划无法被旧 reader 误执行。
4. API、事件、消息、人工通知及两个前端一致理解作用域，不丢路径、不混轮次。
5. 新准入可独立关闭，正在执行的任务可以排空；部署不依赖旧实例碰巧不接管。
6. 资源上限、满容量访问成本和持续失败退避有实测证据，未通过前保持新增准入关闭。
