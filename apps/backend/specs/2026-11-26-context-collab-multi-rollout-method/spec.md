# Context Collab L5 MultiRollout Method：并发选优与反馈驱动迭代

- 日期：2026-11-26
- 状态：**Implemented / 本地开发与验收已执行，完整 CI 结果见 [delivery](delivery.md)；未合并、未部署，外部集成未验证**。
- 拥有模块：Backend `core/task/context_collab/l5_methods`；L6 负责选择与装配。
- 目标基线：`3857a08d9300f7950978c2ba3fc516b2b5f80ff9`。
- 配套：[源码与依据](research.md)、[实施与验证计划](plan.md)。
- 当前合同：[L5](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md)、[ProductProfileService](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)。
- 约束：[架构宪法](../../../../docs/arch/arch.rules.md)、[CI](../../../../docs/arch/ci.enforce.md)、[Context Boundary](../../../../docs/arch/context-boundary-format.md)、[Protocol tests](../../../../docs/arch/protocol-contract-tests.md)。
- 决策核对：[仓库 ADR](../../../../docs/adr/README.md)、[Backend ADR 导航](../../docs/adr/README.md)；本提案不替代已接受 ADR，也不改变 Bot Genome 的演进/发布所有权。

> ACCS 原文访问限制：本轮访问用户指定的语雀文档，只获得访问入口，未取得正文。
> 下文依据仓库现有 ACCS 对齐合同及当前源码；**MultiRollout 是拟议的本地 L5 扩展，
> 不宣称是 ACCS 已定义的第五种基础方法，不分配或猜测 ACCS RFC 编号**。
> 原文的扩展声明、组合及一致性要求，须在宣称 ACCS 相容前补核。

## 1. 目标与关键决策

新增 L5 **`MultiRollout@1.0`**，作为可组合的控制方法，不在 L6 写一个仅服务某产品的重试循环。
它管理“调用哪个子方法、运行多少次、何时验收/选优、失败后如何演进”，而不接管子方法内部协作。

命名约定：**MultiRollout 表示组织多次 rollout 的方法**，同时覆盖并发采样和串行迭代，不限定为并发执行。单次执行仍称 rollout，既有 `ColletRollout`、`StageRollout`、`RolloutEngine` 及相关单次运行端口名称保持不变。

```text
ProductProfile
└── MultiRollout MethodBinding              # 策略、预算、轮次、最终选择
    └── NEST → Swarm Method 模板       # 一份可执行子方法定义
        ├── candidate 1 → Swarm run 1 → stages → 独立 sessions/artifacts
        ├── candidate 2 → Swarm run 2 → stages → 独立 sessions/artifacts
        └── candidate N → Swarm run N → stages → 独立 sessions/artifacts
```

同一控制方法支持两个互斥策略：

1. **`parallel_best_of`**：独立执行同一子方法 N 次，有限并发，收齐候选终局记录后，在**验收通过**的候选中选优。
2. **`adaptive_until_accepted`**：每次只执行一个候选；验收失败后，由配置的策略 Agent 决定不调整重跑、调整后重跑或停止。反馈来源支持：
   - `interactive`：等待用户反馈，再调用策略 Agent；
   - `autonomous`：不等待人，由失败证据直接触发策略 Agent。

关键约束：

- **候选结果、验收事实、排名、调整提案、最终采用是不同事实**；评分最高不等于验收通过。
- **一次候选运行 ≠ 一个 stage ≠ 一次技术投递 attempt**；不能用重发请求伪造新 rollout。
- 第一版执行闭环为 `MultiRollout NEST Swarm`，使用 durable v2 路径演进，不复活内存 MethodStore 作为第二权威。
- `Single/Group` 通过相同子方法运行合同接入；无真实适配器时拒绝启用，不能声称枚举支持就等于可执行。
- 接受失败必须保留原始标准；策略不能通过删验收项、改目标或扩大权限“使任务成功”。
- “持续直到通过”是**有界持续**：成功、显式停止、预算/时限耗尽、无进展或不可恢复故障都会结束。

## 2. 范围及非目标

### 2.1 第一版必须交付

- MultiRollout 的精确版本 Profile、独立 Binding、明确 NEST 边和本地扩展能力声明。
- 上述两种策略；并发候选独立运行与持久化选择；迭代的人工/自主触发共用同一调整裁决流程。
- Swarm 子方法的真实会话、产物、验收与隔离执行；并发度、费用以外的硬计数预算及超时。
- 已有五方法 Service API 的接入和查询投影、interactive 幂等、signed progress 接入适配。
- SQL 恢复、并发 CAS、outbox、策略调用留痕和 fail-closed 外部能力绑定。

### 2.2 本次不做

- 不把 `best_of`、自动重试或 stage fan-out 偷加为 Swarm 自身的含糊语义。
- 不要求单个 Swarm 内部 stages 也并行；外层 N 个 Swarm 并行与其内部串行互不矛盾。
- 不实现递归 MultiRollout、无限迭代、分布式通用 DAG 引擎、任意策略 DSL 或多赢家合并。
- 不接管 Open 招募、L4 Work/root 验收、G1 授权、G3 全局能力演化或 Bot Genome 发布。
- 不自动执行中选结果中的生产发布、支付、发信、合并代码等不可回滚副作用。
- 第一版不改 stage 拓扑/验收合同，见 §7；不承诺仅凭当前 v2 数据就能安全热迁移。

## 3. ACCS 映射与组合边界

| 边界 | 本提案职责 | 不承担的职责 |
| --- | --- | --- |
| L1 | 既有运行/上报交付；HTTP 仍在 Delivery | 不把 HTTP 的五方法当作 L1 标准操作 |
| L2/L3 | 使用既有 Agent、Space、Resource 引用 | 不创建身份、资源授权或永久能力升级 |
| L4 | 引用既有 Message、Artifact、AcceptanceResult 等证据 | 不复制 root Task 权威，不伪造 Resource/Task |
| L5 | 版本化 MultiRollout Profile/Binding；NEST 子方法；候选与策略控制 | 不依赖 L6、具体产品或传输框架 |
| L6 | ProductProfile 选择 MultiRollout/Swarm policy，发布时联合校验 | 不实现另一套 rollout 循环 |
| G1 | 当前调用者、候选执行者、评审者和策略 Agent 权限 | Agent 提案不是授权 |
| G2 | 幂等、认领/执行租约、重试边界、未知结果对账与恢复 | 不把未知投递重发当恢复，也不替代 G1 的资源授权 |
| G3 | 候选质量、代价、失败原因及演进指标 | 观测/打分不直接改 Work 或发布全局策略 |
| G4 | 控制状态机、schema、精确版本、组合和证据一致性 | 不把策略自然语言当可执行状态迁移 |

### 3.1 Profile 与矩阵变更

- 新增本地 `MethodKind.MULTI_ROLLOUT = "MultiRollout"` 和 `get_profile("MultiRollout", "1.0")`。
- 保留 Single/Group/Swarm/Open 的 `@0.2` 精确合同与现有 wire；`get_profile` 的旧默认不改成“latest”。
- `MultiRollout@1.0` 使用 L4 Task/Message/Artifact/Decision/Activity/Query 的既有词汇；本地 participation_model 使用 `body`，表示参与结构复用子方法，不新增参与者权限模型。
- 能力名使用本地 `multi_rollout.run.* / multi_rollout.candidate.* / multi_rollout.selection.* / multi_rollout.adjustment.*`；不是新 ACCS 标准 wire 操作。
- 新增 `MultiRollout --NEST--> Single|Group|Swarm`；MultiRollout 只能有一个可执行 body 模板。
- `Open` 是可支持 body 的招募方法，不是能重复产生工作结果的独立 body；不允许 `MultiRollout NEST Open`。
- 第一版不扩展其它方法作为父的现有矩阵，也不新增 `MultiRollout PARALLELIZE Swarm`。并行次数由 MultiRollout policy 表达，不能破坏 PARALLELIZE 的同 kind 语义。
- 禁止直接/间接 MultiRollout 套 MultiRollout；保持同 Collet、无环、深度和预算校验。
- 不新增 `"MultiRollout + Swarm"` 历史组合 wire，也不把 `composition_method_kind` 字符串拼接当新运行入口；新路径消费明确模板与关系。

第一版的“可组合”具体指独立 Profile/Binding/边及可替换 body 运行缝；必须实际验证
`MultiRollout NEST Swarm`。更广父子组合要有执行适配及测试后才能打开，而不是先放宽矩阵。

### 3.2 模板、运行实例和身份

沿 `MethodCompositionTemplate.relations` 声明的 NEST 边选中的一份子方法模板产生 N 个运行实例，不要求作者在 `required_methods` 中重复写 N 个 Swarm。
每个候选必须有独立的 Swarm 运行作用域/Binding、rollout ID、stage IDs、allocation/attempt、session 路由和产物空间。
模板边不等于运行实例共享同一个可变 Plan；各 Binding/实例关系必须持久化。
`parent_scope_ref` 只能引用真实已存在的 L4 作用域，不把 rollout ID 冒充 Task/Activity ID。

## 4. 最小合同与配置

以下是原始方案的目标合同；2026-11-27 已实现本地执行路径，实际交付与外部集成边界见 [delivery](delivery.md)。
既有 Template 的端点命名已单独调整为 source_method/target_method；新增值类型属于 L5，不把子方法配置复制到 L6。

### 4.1 组合关系：仅使用 MethodCompositionTemplate

**`MultiRollout` 与 `Swarm` 的选择和关系只由 `MethodCompositionTemplate` 描述**：
`required_methods` 声明两个方法及各自 policy，`relations` 显式声明 **MultiRollout 中嵌套 Swarm**。
不新增方法名/执行体名的配置对，也不在 MultiRollout policy 中重复选择 Swarm。

```python
composition = MethodCompositionTemplate(
    required_methods=(
        MethodTemplate(
            profile=get_profile("MultiRollout", "1.0"),
            policy=multi_rollout_policy,
        ),
        MethodTemplate(
            profile=get_profile("Swarm", "0.2"),
            policy=SwarmStagesPolicy(
                execution_ref="swarm-executor@1",
                acceptance_ref="swarm-evidence@1",
                overall_acceptance="owner",
            ),
        ),
    ),
    relations=(
        MethodRelationTemplate(
            source_method="MultiRollout",
            target_method="Swarm",
            relation=CompositionRelation.NEST,
        ),
    ),
)
```

`MethodRelationTemplate` 的端点采用 `source_method` / `target_method`，仅表示关系方向，
不预设父子层级；具体语义由 `relation` 定义。本例 `NEST` 表示 source 的 MultiRollout 中嵌套 target 的 Swarm。
此端点命名同样适用于 SUPPORT、DELEGATE_TO、PARALLELIZE、ESCALATE 等关系，不因此赋予它们包含语义。
新 Python/HTTP 使用新字段；旧 v1 canonical JSON/digest 保持不变，旧 v2 已存定义仅在存储读取边界显式兼容；
不改变 L5 已有边字段和方向，细节见 [现行端点合同](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md#directed-method-relation-endpoints)。

其中 multi_rollout_policy 只携带 §4.2 的多次执行策略、预算与隔离要求。
两个方法的 policy 分别属于各自 `MethodTemplate`；Swarm 的执行/验收配置不移入 MultiRollout。

组合解析与校验：

1. L6 发布时依据 `required_methods` 校验精确 Profile/policy，再校验 `relations` 的端点、方向、矩阵和无环要求。
2. 第一版要求每个 MultiRollout 恰有一条指向可执行子方法的 NEST 出边；该例唯一目标是 Swarm。缺失、多个目标或目标未被 required 选择时拒绝。
3. **不能**因两个方法共同出现、声明顺序相邻或策略默认值，就自动推断嵌套关系；也不能用 policy 字段覆盖关系边。
4. L6 实例化 `MethodBinding` 与 `MethodCompositionEdge`；L5 只沿所属 controller 的有效 NEST 边解析子方法 Binding/精确 Profile，不反向导入 L6 模板。
5. 运行时的 body 仅是“已由组合边解析出的子方法”的简称；body Binding/边引用及 composition digest 是派生的冻结事实，不是另一份可编辑组合配置。每个候选据此创建独立运行实例。

`ProductProfile(id, name, composition, stages)` 保持四字段；stages YAML 仍是已发布 DAG 的唯一作者源。
两种 Method policy 均不保存第二份 stages。optional methods 不隐式启用。
`create_product_profile` 必须验证沿 NEST 边解析出的子方法 adapter、选择/调整能力、隔离能力和 evidence validator 全部可用。
不能只保留一条装饰性的 MultiRollout 边，再绕过其预算直接启动 Swarm。

### 4.2 各方法自己的 Policy

| 类型 | 必需成员与含义 |
| --- | --- |
| `MultiRolloutMethodPolicy` | `strategy`、`limits`、`isolation_ref`；仅描述自身多次执行策略，不选择子方法 |
| `ParallelBestOf` | `kind="parallel_best_of"`、`samples`、`max_concurrency`、精确 `selection_ref` |
| `AdaptiveUntilAccepted` | `kind="adaptive_until_accepted"`、`feedback_mode`、精确 `adaptation_ref`；interactive 模式另需 `feedback_timeout_seconds` |
| `MultiRolloutLimits` | `max_rollouts`、`max_elapsed_seconds`、`max_stage_dispatches`、`max_strategy_calls`、`max_no_progress` |

`MultiRolloutMethodPolicy` 沿用现有 MethodPolicy 的类型匹配惯例：`method` 是固定为
`"MultiRollout"` 的只读类型标识（`init=False`），用于验证 policy 与所属 Profile 一致，不表达组合关系。
**不定义 `body_method`、子方法 Profile 或子方法 policy 字段**，不在 policy 内重新声明嵌套对象。

校验及默认：

- strategy 是判别联合，非本策略字段拒绝；引用选择已注册精确版本，禁止任意 import、URL 或 inline Python。
- `samples` 为非 bool 整数 1..32；`max_concurrency` 为 1..min(samples, 8)，且不超过租户配额。
- `max_rollouts` 为 1..32；并发策略必须等于 samples，第一版只做一批，不偷偷补跑失败候选。
- `max_stage_dispatches` 为 1..3200；`max_strategy_calls` 为 1..64；`max_no_progress` 为 1..8。
- `max_elapsed_seconds` 为 1..86400；人工等待超时为正数且不超过整体剩余时限。
- 首轮算一次 rollout；重新执行整个 body 算新一次；有明确取消确认的技术重试只计 dispatch，不冒充新候选。
- 所有限额必填；发布与运行 admission 同时检查宿主更严格的配额，不接受 infinite/0 表示无限。
- 不宣称上述次数预算就是 token/金额预算。有可靠计量/预留能力的宿主可增加受版本约束的成本上限；未知成本不能伪装为零。

并发策略参数示意（仅为 `MultiRolloutMethodPolicy` 的参数内容，**不是方法组合配置格式**；能力引用为占位声明，不是当前已安装实现）：

```yaml
strategy:
  kind: parallel_best_of
  samples: 4
  max_concurrency: 2
  selection_ref: highest-score@1
limits:
  max_rollouts: 4
  max_elapsed_seconds: 3600
  max_stage_dispatches: 100
  max_strategy_calls: 4
  max_no_progress: 2
isolation_ref: per-candidate-workspace@1
```

迭代策略参数示意（同样不包含方法组合关系）：

```yaml
strategy:
  kind: adaptive_until_accepted
  feedback_mode: interactive
  adaptation_ref: stage-improvement-agent@1
  feedback_timeout_seconds: 600
limits:
  max_rollouts: 5
  max_elapsed_seconds: 7200
  max_stage_dispatches: 100
  max_strategy_calls: 8
  max_no_progress: 2
isolation_ref: per-candidate-workspace@1
```

改为 `feedback_mode: autonomous` 时**删除** feedback_timeout_seconds，失败后直接调用同一 adaptation_ref。

### 4.3 运行、评估和提案记录

下列是本地扩展的持久化运行记录，不冒充 ACCS 已定义对象，也不按类逐个建表：

| 记录 | 最小信息 |
| --- | --- |
| `MultiRolloutRunRecord` | run ID、controller Binding、已解析 NEST 边/子方法 Binding 与 composition digest、冻结 policy/version/digest、控制 generation、phase、outcome/reason、候选顺序、focus/winner、限额/已用/预留、创建/截止时刻 |
| `MultiRolloutCandidateRecord` | ColletRollout ID、所属 run/generation、顺序、前驱候选、body Binding、RunPlan 引用、运行/验收证据、结果摘要及不可变产物引用、隔离执行定位 |
| `RunPlan` | plan version/digest、发布 Profile digest、上个 plan、有限 stage instruction overrides、合法候选选择约束、需要重跑的 stage 闭包、触发证据 |
| `SelectionDecision` | 候选/证据集 digest、策略精确版本、各候选评分/理由/缺失项、唯一 winner ID、选择者与裁决证据 |
| `AdjustmentDecision` | 源候选/失败证据、反馈来源、interactive UUID 或系统触发 ID、策略版本、类型、理由、typed patch、request_feedback、提案主体及提交时的 generation/plan fence |

身份由仓储分配或既有稳定内部键产生；序号仅排序，不能用 list index/hash 代替 rollout/stage ID。
artifact 文本、文件访问与 session 读取仍经过当前 G1/资源访问合同。

## 5. 策略一：parallel_best_of

### 5.1 执行流程

1. 创建 controller 与 N 个候选身份；冻结相同输入版本、body 模板、验收合同和策略版本；预检总预算与隔离能力。
2. 候选进入有界队列；最多 max_concurrency 个占用执行槽。候选内部由自己的 Swarm 运行 stages。
3. 每个候选独立收集真实执行结果、完整 AcceptanceResult 和产物。一个候选失败不使其它候选失效。
4. N 个候选均终局或整体截止后，冻结候选集合。正常完成必须有 N 份终局记录；不保证每份都有成功产物。
5. 仅允许已按子方法合同 `ACCEPTANCE_SUCCEED` 的候选参与选择；没有通过者，结束为 `NO_ACCEPTED_CANDIDATE`。
6. 对合格集合调用 selection_ref，验证返回属于该集合，原子提交唯一 winner、决定证据和 controller 结果。

正常路径等待全部 N 份终局，**第一版没有 first-success 提前取消**。
整体截止、用户停止或不可恢复隔离故障可以提前结束；未启动/未完成项显式标记为未完成，不能补造 N 个结果。
查询必须能区分“配置 N / 已启动 / 已终局 / 已成功 / 尚未确认取消”。

### 5.2 选择策略与确定性

- 首个本地实现为 `highest-score@1`：按配置的同一指标/量纲计算分数降序，再按 candidate 顺序升序打破平局。
- selection_ref 解析到不可变选择描述（rubric/metric、方向、合法量程及受信评分能力的精确版本）。指标必须来自授权 evaluator 对实际产物的检查；不能把 worker 自报“100 分”当受信评分。缺失指标明确失败，不默认补零。
- 可安装 `agent-judge@版本`，按统一 rubric 读取已授权的候选产物，返回结构化评分及理由。
- 打分没有放宽原验收的权限；NaN/Infinity、缺评分、重复/未知候选、选择失败候选或跨运行候选都拒绝。
- 所有合格候选必须在同一份冻结 rubric 下可比；不得拿不同条件下的分数直接比较。
- Agent 的随机性只影响**提案**；一旦选择提交，恢复/重放只读原决定，不再次抽样或重新判赢家。
- 候选完成顺序、网络返回顺序和 SQL list 顺序不得决定赢家。

### 5.3 隔离及副作用

每个候选独立 session、工作目录/分支、外部执行键和产物 namespace；共用的输入只能是可验证的不可变引用。
同一 Agent 可执行多个候选，但不能共享可变会话历史或输出目录。需要随机 seed 的实现记录 seed，不承诺结果一定不同。

公共默认拒绝缺失隔离/证据能力的执行。并发采样只允许沙箱、只读或有明确补偿/幂等保证的动作；
仅候选 workspace 隔离不足以隔离发信、部署等外部系统副作用。不可逆操作必须移到中选后的独立授权步骤。
“中选”是采用产物/证据，不是自动授予后续发布权限。

## 6. 策略二：adaptive_until_accepted

### 6.1 基本循环

```text
execute body once → collect real completion → governed acceptance
  ├─ PASS → adopt this candidate → finish Method
  ├─ runtime/dispatch/validator unavailable → explicit infrastructure failure/reconciliation
  └─ ACCEPTANCE_FAILED
       ├─ interactive → WAITING_FEEDBACK → user feedback
       └─ autonomous  → failure evidence directly
                         ↓
                 strategy Agent proposal
                         ↓
                 G1/G4 + budget + version validation
                   ├─ stop → finish
                   ├─ keep waiting / no progress → bounded wait or terminate
                   └─ redo / valid patch → freeze next RunPlan → new candidate → loop
```

- 同一 run 同时只有一个未终局候选；不在人工等待期间占用已确认释放的执行槽。
- 策略 Agent 输入包含原目标、不可变验收项、当前 RunPlan、失败条件及理由、已授权产物、有限历史和可选反馈。
- 通过则停止，不因为仍有预算继续“优化”。第一版不实现“已通过仍搜索更优”。
- `overall_acceptance=owner` 时，Method 成功只表示得到合格结果；Collet 保持 RUN_FINISHED，不能代替 owner 的 root Work 验收。
- `RUN_FAILED`、调用超时、鉴权/验证服务不可用不是 ACCEPTANCE_FAILED；第一版不自动归因成“质量不好”然后换策略重跑。

### 6.2 人工与自主触发

**interactive**：失败后持久化 WAITING_FEEDBACK、失败引用及绝对截止时间，并写入幂等通知 intent。
`interactive(rollout_id, uuid, feedback)` 先鉴权/查回执，随后调用 adaptation_ref，校验并提交提案。
DO_NOTHING 保持等待，不重置等待/总截止，不产生新候选；同一 UUID 的已提交决定重放不新增策略调用。
等待超时默认终止为 `FEEDBACK_TIMEOUT`；第一版不暗中切成 autonomous。

**autonomous**：实例创建时必须校验发起者有权委托该策略主体及其有限动作；配置一个 Agent 引用或 worker 读取到 owner 不等于获得自主权限。失败接纳事务只写入唯一 adaptation intent，不在 progress 请求内无限循环。
系统触发键由 run/generation/失败候选/失败证据版本确定；重复 worker 唤醒不能重复创建下一轮。
策略可返回 SHUTDOWN；或返回 `DO_NOTHING + request_feedback=true` 请求人工升级，其余类型必须 request_feedback=false。没有获授的自主权限时不得执行自动调整。
人工升级进入有截止的 WAITING_FEEDBACK，后续仍走同一 interactive 合同。

### 6.3 决策类型与无进展

沿用 `DO_NOTHING / RE_DO / OPTIMIZE / SHUTDOWN`，但 MultiRollout 分支的 typed 决策须由统一裁决器处理：

- `RE_DO`：RunPlan 不变，创建独立下一候选；消耗 rollout/dispatch 预算。
- `OPTIMIZE`：合法 patch 导致 plan digest 变化，冻结新版本后创建下一候选。
- `SHUTDOWN`：停止 run、撤销有效分配、持久化取消/对账 intents，不伪造取消成功。
- `DO_NOTHING`：人工路径继续原等待；自主路径视为无进展，不立即重复调用自己。若 request_feedback=false 且没有新的可等待证据源，则结束为 NO_PROGRESS；true 则进入有界人工等待。

按失败条件集合、失败证据 digest、plan digest 和提案类型记录连续无进展。
相同计划重跑且失败未改善、连续 no-op 或策略只给出同一 patch 都计数；达到 max_no_progress 终止。
任何新策略调用在执行前预留 max_strategy_calls，rollout/dispatch 也在执行前预留；失败调用不免费、不重置预算。

## 7. 策略调整的授权边界

### 7.1 第一版可调整的 Swarm stage 策略

- 为已有 stage 增加/修订有大小上限的执行指令，例如“先补充并发反例，再实现”。
- 从原 stage candidates 中收窄候选或选择另一个实际有权执行的候选。
- 指定重跑哪些已有 stage；服务端重新计算受影响后继闭包。
- 用已授权的失败证据/用户反馈作为后继运行的补充输入。

所有调整写入新的 **RunPlan**，不修改已发布 ProductProfile，也不覆盖历史 RunPlan。
当前代码仅把 instruction 放入整体 content；新实现必须真正按 stage 投影指令，不能只改日志就声称 stage 策略已调整。

### 7.2 第一版禁止的调整

- 增删/改名 stage、重连 depends_on、删除或放宽验收项；这属于 DAG/合同版本变更。
- 添加原 whitelist 外的 Agent，扩大资源/工具权限，延长整体时限、增加预算或切换未授权策略实现。
- 修改目标、伪造通过证据、覆盖已完成候选的内容/会话/产物。
- 直接派发/取消执行、写 SQL 或发布全局策略；策略 Agent 只能提案。

拓扑调整属于后续显式扩展：需受权创建新不可变 Profile，完整 DAG/证据合同校验及兼容策略，不能把
`RunPlan` 变成绕过 stages YAML 唯一作者源的第二个配置入口。此分期需用户评审确认，见 §12。

### 7.3 复用边界

迭代默认完整重跑 body。显式局部重跑时，仅可复用已验收、不可变资源仍可读且输入/指令/验收等价的 stage 证据。
受影响 stage 及其后继必须生成新 stage IDs 和真实新 session；未受影响复用项同样用新 stage ID，并记录 reused_from，不复制 session。
若整体目标/输入变化的影响无法证明局部性，视为影响所有 stages，不能凭 Agent 自述“无影响”跳过验证。
并发 N 次第一版不跨候选复用执行结果，以免 N 次独立执行名存实亡。

## 8. 运行权威、状态与现有对象的关系

### 8.1 现有对象不变，增加有版本的控制元数据

保留 ProductProfile/Collet/ColletRollout/StageRollout/Artifact/AcceptanceResult 的当前业务字段。
**每个实际 body 执行仍是一个独立 ColletRollout**；并行兄弟与迭代前后继通过 CandidateRecord 关联。
N 个候选不能压成同一 ColletRollout 中 N 份同名 stages，也不能通过切换 current 指针轮流冒充并发。

新增 `product-runtime/multi-rollout@1` snapshot 分支，包含 controller、候选成员、独立 RunPlans、决定及预算记录。
controller 是本地控制记录，不再造一个假的 ColletRollout/Task。每个 stage ID 恰属一个候选，实际 session 路由保持唯一。
同一 Collet 的 SQL snapshot/CAS 仍是第一版的**唯一写权威**，不同时写旧 MethodStore；暂不拆候选表以免引入跨聚合事务。

### 8.2 current 指针与候选集合

对未选择 MultiRollout 的旧 profile，所有 current/串行规则保持不变。
对新 MultiRollout profile 明确增加以下语义：

- `current_rollout_id` 是**交互/展示焦点**：初始为顺序最小的候选；迭代时是最新候选；选择提交后为 winner（无 winner 则保留失败焦点）。
- 焦点在批次内不随完成顺序变化；它不再是唯一允许 progress 的候选。
- 有效上报范围从 controller 的当前 generation、候选成员及 active allocation 推导；绝不以 client 提供的 active IDs 为准。
- focus、各候选状态、选优状态和 Collet 总状态一起原子更新。一个失败候选不能让仍运行的其它候选/Collet 提前失败。
- 单个候选的 status 仍按其 stages 聚合；Collet 的 status 改由 controller 聚合，不再强制等于焦点候选的 status。

### 8.3 控制状态与业务状态投影

控制 phase 使用 G4 版本化状态机：

```text
CREATED → SAMPLING → SELECTING → SUCCEEDED | FAILED
CREATED → EXECUTING → SUCCEEDED
                    └→ WAITING_FEEDBACK → ADAPTING → EXECUTING
                    └→ ADAPTING → EXECUTING | WAITING_FEEDBACK
任意非终态 → STOPPED | FAILED
```

SUCCEEDED/FAILED/STOPPED 均终态；取消对账可以在终态之后继续，不能恢复调度或换赢家。
暂停/恢复不列入第一版，避免未定义的后台继续执行。

| 控制事实 | Collet.status / 元数据 |
| --- | --- |
| 尚未启动 | CREATE |
| 执行、选优、调整中 | RUNNING |
| 无活跃执行、等待人工失败反馈 | RUN_FINISHED + phase=WAITING_FEEDBACK；候选仍保留 ACCEPTANCE_FAILED |
| 有中选合格结果，overall_acceptance=stages | ACCEPTANCE_SUCCEED |
| 有合格结果，overall_acceptance=owner | RUN_FINISHED；root Work 验收仍独立 |
| 质量不通过/有界演进耗尽 | ACCEPTANCE_FAILED + 具体 outcome |
| 执行/策略能力不可用等不可恢复故障 | RUN_FAILED + 具体 outcome |
| 人工停止 | stopped=true + phase=STOPPED；沿用停止前业务事实，不扩展 StatusEnum |

超时撤销的未终局 stage 保留最后真实业务状态，另记调度撤销/超时原因；不得为了凑终局记录伪造 Bot 的 RUN_FAILED 上报。

## 9. Service API、策略能力及查询

### 9.1 五方法签名不增加 context/request_id/expected_revision

```python
async def interactive(
    self,
    collet_rollout_id: int,
    interactive_uuid: str,
    interactive_context: str,
) -> InteractiveResult: ...
```

身份继续来自可信 task-local actor scope；interactive UUID 继续按 tenant/env/actor 隔离，128 UTF-8 bytes 上限不变。
同 UUID+原 rollout+原反馈返回原结果，即使 current/winner 已变化；不同反馈/目标冲突。
鉴权先于 replay；新 UUID 不能控制过期 generation，内部 revision/CAS 不暴露为参数。
同一操作在途时有界等待其结果；若尚不能确定，返回可重试的在途领域错误，不虚构 DO_NOTHING/已停回执。

MultiRollout 第一版交互范围：

- 以 focus rollout 定位整个 controller，不通过自由文本猜“只改哪个并行分支”。
- 迭代的 RE_DO/OPTIMIZE/DO_NOTHING 在 WAITING_FEEDBACK 接受；SHUTDOWN 可在任意非终态提交。
- 并发策略第一版只支持 SHUTDOWN，不混入重规划/第二批采样。停止提案沿用已安装 RolloutInteractionPort 控制实现，不能借此产生 stage patch；默认 no-op 实现不能声称已停止。
- 终态只允许已有回执重放；新干预冲突，要重新运行需新建 Collet。普通旧 profile 的既有行为不变。
- `InteractiveResult(type, message)` 保持两字段；调用者通过查询取得候选、计划/控制状态，不从文案解析控制信息。

### 9.2 查询结果明确扩展，不声称完全 wire 无变化

保留 `query_collet_rollout(collet_id, all_rollout=True)`：true 返回所有实际候选/历史，false 返回 focus 一个。
通过现有 RolloutList/HTTP Envelope 的控制元数据新增**仅 MultiRollout 分支**的 `multi_rollout_control`：

- schema/version、run ID、phase/outcome/reason、strategy、generation；
- focus、winner、配置/已启动/已终局数量、有效候选 ID、预算及等待截止；
- 每候选顺序/前驱、RunPlan version/digest、body Binding、评分及是否中选；
- 取消 requested/unknown/confirmed 与不完整结果标记。

这些是查询投影，来自同一 snapshot，不成为第二权威。旧 profile 的返回形状不变。
`query_stage_rollout_details` 可查任意被授权历史候选的 stage，仍只读其真实路由，不伪造 session。
这是 opt-in 的响应/运行合同扩展；HTTP schema/OpenAPI/消费者与合同测试必须同步。
第一版用 32 候选/100 stages/12 MiB snapshot 的硬上限约束读量，不宣称可无界返回历史。

### 9.3 三类接缝，不增加多余通用平台

1. **L5 内部 `MethodRunPort`**：require/start/observe/collect/request_cancel；以有效 NEST 边解析出的子方法精确 Profile/Binding、候选/RunPlan、可信执行身份为输入，产出真实运行/验收结果。第一实现为 durable Swarm adapter。
2. **出站领域策略 `MultiRolloutStrategyPort`**：`select` 与 `propose_adjustment`，只消费脱敏、有权限的不可变输入，返回 typed 提案；公共本地提供确定性选择和 recording 策略替身，生产 Agent 由宿主显式绑定。
3. **既有 RolloutExecutionPort/证据/资源能力**：真实执行、观察、取消、授权、验收及隔离，不让策略端自己调用执行工具。

策略合同放在 context_collab 领域边界，类似现有 SwarmPlanningService，L5 可消费、L6 可选择；不为了放到更低层 plugin_api 而引入其对 L5 的反向依赖。
若未来抽成通用 Plugin API，先使用不依赖 L5 的 DTO，再补独立契约测试；第一版不额外造插件注册体系。
内部 MethodRunPort 不承担外部 provider 选择，具体绑定仅在 DI。

人工入口的 RolloutInteractionPort 通过 MultiRollout adapter 委托同一策略/裁决器；自主 worker 也调用该裁决器，
两者不能有两套 patch 校验/预算逻辑。现有普通 profile 的交互能力合同保持可用。

## 10. 并发、持久化与故障恢复

### 10.1 提交原子性及 outbox

继续复用 v2 配置、身份、snapshot、receipt、intent 表，不按上文记录逐类加表。
新增 snapshot schema discriminator、严格解码及迁移；运行元数据、决定、预算预留、下一候选身份、回执及相关 intents 在一次 SQL CAS 中提交。
现有无版本 snapshot 只能由明确适配器解为旧串行合同，未知版本 fail closed，不能默认当最新运行。

outbox 至少可区分 advance-candidate、evaluate/select、adapt、notify、cancel/reconcile：

- 逻辑键包含 run/generation/candidate/plan/operation；重复调度只能认领一次。
- 当前 worker 以全局 revision 失配就 supersede 的规则**必须演进**：兄弟候选的正常更新不能吃掉其它候选的有效 intent。
- 提交时仍做全局 CAS；是否失效由目标 generation/plan/allocation 决定，而非全局 revision 是否变动。
- claimed intent 有持久 lease/owner/fence；崩溃恢复先对账，不凭 lease 到期直接重发未知投递。
- 配额/槽位先预留后网络 I/O，未知执行和未确认取消继续占用外部运行额度，避免实际并发越界。
- 跨 Collet 的租户配额须由已安装的宿主 admission/资源租约能力原子预留；一个 Collet 的 CAS 或进程内 Semaphore 不能证明全租户限流。
- 第一版在一个 Collet 内串行化短 SQL 提交、并发外部 I/O；不承诺高吞吐/无争用。

所有 provider/Agent 调用在事务外。每个 UUID/系统触发键对应唯一策略操作，持久化 in-flight 认领与响应状态；并发重复先等待/读取该操作，不同时调用 Agent。每次真实外部策略调用前单独预留并计入 max_strategy_calls，崩溃后允许重新计算的调用也消耗新额度，不因逻辑键相同而免费。
Agent 可以在明确恢复边界下重复计算无副作用提案，但只有一次通过当前 fence 的决定能生效；已提交决定必须重放，不允许恢复时重新问 Agent 再换结果。

### 10.2 progress 与冲突

ProgressEvent 的三个业务事件及签名身份不变；ReportContext 的 request_id/attempt/expected_revision 不删除，
也不重解释成 interactive UUID。MultiRollout 将 stage 映射到所属有效候选，而不是只查 snapshot.current()。
校验候选 generation、stage/attempt、lease、真实 route、上报主体和完整冻结条件。

保留 expected_revision 表示**aggregate revision**的现有语义，因此并发合法上报会遇到正常 CAS 冲突。
不能把不同候选的冲突当数据损坏或丢弃上报；提供受限的生产者冲突处理：

1. 结果未知时用原请求原 revision 重试以查已有回执，不能先更换 request_id 造成重复效果。
2. 确认原请求因 stale revision 未提交后，刷新 aggregate revision，并用新技术 request_id 提交同一受 fence 保护的业务事实。
3. 已终局/撤销/过期的 allocation 不重投；重算当前权限/条件，禁止旧证据自动套用到新计划。
4. 合同测试必须证明不同候选的两份有效事实最终都能接纳，重复/晚到事实不二次推进。

progress 的回执/CAS、interactive、stop、选择/演进决定竞争同一权威；
停止先提交则后续决定失效，决定先提交则停止撤销其新 intents。旧决定不能复活停止的执行。

### 10.3 外部未知结果与恢复

- start 超时/进程崩溃：保留未知投递；有 provider 幂等执行键/查询能力时对账，无此能力则明确阻塞/失败，不盲重发。
- cancel 未确认：保留 requested/unknown，不伪装已取消；迟到 session 记取消对账，不能再次进入有效候选。
- Agent 响应后提交前崩溃：优先恢复已记录响应；若不能恢复且允许重新计算，则新调用先消耗额外策略预算。提交后的决定直接复用；重复 interactive 不按字面次数重复扣已完成操作。
- SQL/回执/outbox 写失败：整笔回滚并返回错误，不返回“已接受”而丢掉下一轮。
- WAITING_FEEDBACK 重建：恢复原绝对截止，通知幂等，不因重启延长等待。
- snapshot 超 12 MiB：在提交前显式失败；外部产物只保留不可变引用/有限摘要，不裁掉验收/幂等历史来继续。
- 宿主必须显式调度 worker 和到期检查；没有常驻 worker 就不承诺自动迭代/等待超时生效，不隐藏起后台线程。

## 11. 传播、兼容和交付边界

| 位置/消费者 | 必须一起处理 |
| --- | --- |
| L5 Profile/Kind/矩阵、inspection、授权 | 精确本地扩展，旧四方法合同不变；未知扩展拒绝，不降级成 Swarm |
| L5 runtime/reducer/worker | 单 current → 多候选作用域；分离 candidate/Collet 聚合；计划/预算/决定/恢复 |
| L6 composition/config codec | 新 typed policy、联合校验；仅由 MethodCompositionTemplate 的显式 NEST 边确定嵌套对象；严格序列化，不依赖历史组合 wire |
| SQL repository/snapshot codec | discriminated 版本、各候选 plan、Binding、route、预算、receipt/intent 原子性 |
| HTTP/API 调用者 | 五方法参数不变，响应 multi_rollout_control 的 opt-in 扩展和 progress CAS 重试合同 |
| DI/配置/宿主 | 精确策略、隔离/evidence、配额及 worker；缺失能力 fail closed |
| 测试/知识 | Context Boundary、架构白名单、消费者合同、README/诊断与本地验收 |

- 仅新发布、明确选择 MultiRollout 的 Profile 启用新 snapshot；旧 Profile/运行保持原串行解码及行为。
- 不把存量 v1 内存 Swarm 或运行中的 v2 自动转成并发；有需要须停写、排空/取消、验证完整事实后显式迁移。
- 不修改历史 Profile 内容。回滚先停止/对账新运行，再使用能识别新 schema 的 reader；旧 reader 不能写新 snapshot。
- 当前表是否需要 intent 唯一键/认领 lease 等列变更，由实施阶段按真实 schema 确认并提供受测 DDL；**不承诺仅改 JSON 无迁移**。
- 本 spec 只确认公开仓库方案；企业策略 Agent、真实 provider、MySQL、远端取消与部署均未验证。

## 12. 验收标准与评审点

### 12.1 必须可复现的验收旅程

| 编号 | 场景 | 通过条件 |
| --- | --- | --- |
| A1 | MultiRollout NEST Swarm，N=4、并发=2 | 4 个真实独立候选/会话作用域；观测峰值 ≤2 且确实重叠执行；子 Swarm 内部仍可串行 |
| A2 | 候选混合 PASS/FAIL，乱序完成/分数相同 | 不因单候选失败停止其它；只从 PASS 选择；相同输入决定不受完成顺序影响 |
| A3 | 无合格候选/评分非法/超时 | 无伪造赢家；明确原因与不完整数量，取消状态可区分 |
| A4 | 失败→人工反馈→stage 策略 patch→重跑通过 | 无反馈不自动跑；RunPlan 新版本、旧历史不变；执行输入实际改变；无多建后继 |
| A5 | 失败→自主 Agent→重跑通过 | 不依赖 interactive；重复 adaptation intent 只创建一个下一候选 |
| A6 | 恶意/越权提案 | 加 Agent/增预算/删验收/改 DAG/直接副作用均拒绝；既有结果不被标成通过 |
| A7 | UUID 重复、进程重建及并发干预 | 原结果重放；修改目标/反馈冲突；鉴权不被 replay 绕过；无重复 committed 效果 |
| A8 | 两候选并发 report、stop/selection/interactive 竞争 | 有效事实经明确 CAS 重试均保留；失效决定不生效；停止后无新执行 |
| A9 | 预算耗尽、连续 no-op、人工超时 | 有界终止；重启不重置截止/预算；无紧循环 |
| A10 | start/cancel 结果未知、回执/intent 写失败 | 不盲重派、不伪装取消；SQL 原子回滚；重建可对账或明确 fail closed |
| A11 | 候选共享副作用资源 | 隔离验证拒绝启动；独立 workspace 也不能绕过对真实外部副作用的限制 |
| A12 | 旧 profile 与 owner 验收 | 旧五方法与串行语义不变；Method 合格不替代 root owner Work 验收 |

使用真实隔离 SQLite/文件资源与 recording 外部 Protocol 替身，且至少一条 Swarm adapter 旅程穿过真实 engine/repository/worker。
“同一协程内 gather 完成”不足以证明真实候选隔离、N 次执行、峰值并发或持久化恢复；详细测试见 [plan](plan.md)。

### 12.2 待评审确认，不阻碍本轮形成方案

1. 名称采用 `MultiRollout@1.0` 本地扩展；取得 ACCS 原文后核对扩展能力与组合约束，不提前宣称相容。
2. 第一版 stage 调整限定指令、合法执行者选择和重跑闭包；如需增删/重连 stages，须扩展不可变配置版本合同。
3. 并发默认收齐 N 份终局再选一个；不实现首个通过就停，也不允许失败候选补跑来凑 N 份成功。
4. 人工等待有超时且默认停止；自主模式显式获权，绝不无限持续。

### 12.3 本轮证据与状态

MultiRollout 本身仍是 Draft，尚未实现。独立的 MethodRelationTemplate 端点重命名已传播至合同、校验、
拓扑、存储读取和 DI 消费者；五方法签名与 SQL DDL 未改。实际验证与之前的方案阶段证据分别记入
[research](research.md)；端点重命名回归不代表上述多候选验收用例、完整 Backend CI、真实 Agent/Provider、MySQL 或部署已通过。


### 12.4 2026-11-27 开发交付

用户确认实施后，新增 MultiRollout@1.0 的策略/预算、候选作用域 durable Swarm、
单 SQL snapshot/CAS、跨 Collet SQL admission、leased/fenced outbox、可信选优、
人工/自主统一策略裁决与幂等、绝对等待/全局截止、查询/HTTP schema 和 fail-closed DI。
独立 candidate Binding/edge/plan/session 与预算/后继/回执/outbox 原子保存；默认完整重跑，
不声称未经证明的局部证据复用。§12.3 保留为方案阶段历史，不是当前实现状态。

当前 owning 合同：[L5 MultiRollout](../../src/agentclaw/community/core/task/context_collab/l5_methods/MULTI-ROLLOUT.md)、
[ProductProfile 配置与集成](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md#multirollout-nest-swarm-opt-in)。
新增测试位于 `tests/community/core/task/context_collab/multi_rollout/`。
执行命令、失败修复、最终 CI、DDL 与外部能力缺口分别记录在 [delivery](delivery.md)，
不将方案阶段或端点改名的旧测试统计当作本次验收。
