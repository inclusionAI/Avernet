# Context Collab Swarm Workflow 模式技术方案

- 日期：2026-11-23
- 状态：**已实现并完成本地目标验收；广回归/完整 CI 存在可复现的基线失败；尚未合入、未部署；提交/推送结果以 Git 记录为准**。实现与验证边界见 [交付记录](delivery.md)。
- 拥有模块：Backend `core/task/context_collab`，涉及 L5 Swarm、L6 ProductProfile 持久化与 DI 装配。
- 代码基线：`04a8f4af78bc79d1db2f7e65320b0465828c2433`。
- 配套：[设计阶段基线证据](research.md)、[实施与验证计划](plan.md)、[代码落点与实际验收](delivery.md)。
- 当前契约：[协作域](../../src/agentclaw/community/core/task/context_collab/README.md)、[L5 Swarm](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md#bounded-swarm-coordination)、[L6 产品](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md)。
- 架构约束：[架构宪法](../../../../docs/arch/arch.rules.md)、[CI](../../../../docs/arch/ci.enforce.md)、[边界格式](../../../../docs/arch/context-boundary-format.md)、[协议测试](../../../../docs/arch/protocol-contract-tests.md)。本方案不取代既有 ADR，不新增系统级架构决策。

## 1. 需求与设计结论

### 1.1 用户需求

1. 在 ProductProfile 中定义供 **Swarm 方法执行**的 workflow，其执行节点及依赖构成 DAG。
2. 每个节点配置非空 Agent 列表，实际执行时从其中随机选择一个 Agent。
3. Swarm 调用 `plan` 时，如果配置了 DAG，就根据 DAG 和实际完成情况给出下一个 Stage 的内容与执行者。
4. ProductProfile 及其中的 workflow 配置应落库，不能只随代码安装。新增专属 ProductProfile 表，支持完整定义的版本化保存和读取。
5. 每个节点明确定义预期产物；节点之间的业务数据以经校验、固定版本的 Artifact 交接，不能只依赖自然语言说明或会话历史。

### 1.2 推荐方案

**ProductProfile 整体按版本落库（内含 DAG）→ 实例固定产品版本 → DI 选择配置式规划器 → 复用现有 SwarmPlan / Stage / Allocation / 子 Task / 上报链路。**

- 新增 **1 张 `ac_context_collab_product_profile` 表**，一行保存一个已发布的 ProductProfile 版本；完整产品定义及其 DAG、节点候选 Agent 和依赖存入同一 JSON，不再单建 workflow/node/edge 定义表。
- 设计基线的 ProductCatalog 是代码所有的不可变目录。本次实现将其演进为“数据库定义来源 + 注入式精确版本读取”；代码仅保留 Method/执行能力实现与显式导入种子，不作为运行时缺行回退。
- 新增 L5 `SwarmWorkflowPolicy(workflow=...)`，由 ProductProfile 的 `MethodTemplate.policy` 选择。
- 保留现有 `SwarmMethodPolicy(plan_service_ref=...)`，继续使用外部动态规划能力；两种 policy 是互斥的类型选择，不增加重复的 mode 字段。
- 新增 `WorkflowPlanningService`，实现既有 `SwarmPlanningService.plan`。首次编译完整 DAG，之后仅根据权威 Stage 状态选择下一步，不调用动态规划服务。
- 同一次 `plan` 继续同时给出 **下一 Stage + 一个执行者 + I/O 契约**，不增加独立 MATCH 服务。
- DAG、节点候选范围和实际参与资格分别校验；**配置 Agent 不等于授权 Agent**。
- 一次分配成功后执行者固定在 Allocation 和子 Task 中，重复唤醒不重新抽签。
- 节点声明命名 `outputs`，后继通过 `inputs` 绑定 `from_node + from_output`；`depends_on` 只控制时序，不再隐式合并全部前驱产物。成功产物是受管节点的数据交接权威，聊天与日志只是辅助。

### 1.3 首版范围

支持固定、非空 DAG 的多入口、多出口、分支及 AND 汇合；每个节点执行一次，每个节点一个实际 Agent，声明至少一项必需输出。首版不支持可选输出/可选输入；无文件交付的评审或操作节点也应提交结构化结论/回执产物。

**首版保留现有 Driver 的串行派发语义**：多个节点同时 READY 时按配置顺序选一个，当前 Stage 完成后再规划。DAG 支持不等于并行执行承诺；并行调度不是本需求的必要前提，后续可单独扩展。

不包含：条件边、OR 汇合、循环、动态增删节点、失败自动重试/换人、在线健康探测、负载均衡、远程传输适配、持久化调度器、MethodStore 整体落库、根 Task 自动验收、UI、草稿/审批/启停管理、独立 workflow 注册中心或新的 HTTP Profile 管理接口。包含落库所必需的内部发布/精确版本读取能力，但不扩展成完整配置管理平台。

## 2. 与现有 workflow 概念的边界

当前 `ProductProfileDefinition.workflow: WorkflowProfile | None` 表达的是 **根 Task 的状态机和动作映射**，不是 Swarm Stage DAG。`workflow_kind`、`WorkflowProfile.state_machine_ref` 及 G4 Work 解析不能改作 DAG 调度用途。

本方案中的配置位置为：

```text
ProductProfileDefinition
├── workflow: WorkflowProfile | None          # 原有根 Task 动作/状态契约，不改语义
└── composition.required_methods
    └── MethodTemplate(profile=Swarm)
        └── policy: SwarmWorkflowPolicy
            └── workflow: SwarmWorkflowDefinition  # 新增 Stage DAG
```

二者可同时存在，也可以只配置 Swarm DAG、根 Task 使用既有 standard Task contract。DAG 完成不等于根 Task 验收通过。

不选择在顶层新增通用 `dag` 字段，也不把节点塞入旧 `WorkflowProfile.actions`：Swarm 策略归 L5 所有，L6 仅组合，避免第二套 Stage 引擎和 L5 对 L6 的反向依赖。

## 3. 配置与类型契约

### 3.1 新增 L5 值对象

建议由新文件 `l5_methods/swarm_workflow.py` 持有，不继续向接近 1,000 行的 `swarm_profile.py` 堆叠定义与图算法。

```python
@dataclass(frozen=True)
class SwarmWorkflowRef:
    workflow_id: str
    version: int

@dataclass(frozen=True)
class SwarmArtifactOutput:
    name: str
    description: str
    resource_type: str
    content_contract_ref: str
    min_items: int = 1
    max_items: int = 1

@dataclass(frozen=True)
class SwarmArtifactInput:
    name: str
    from_node: str
    from_output: str

@dataclass(frozen=True)
class SwarmWorkflowNode:
    node_id: str
    name: str
    objective: str
    agents: tuple[AgentRef, ...]
    input_contract_ref: str
    output_contract_ref: str
    completion_contract_ref: str
    outputs: tuple[SwarmArtifactOutput, ...]
    inputs: tuple[SwarmArtifactInput, ...] = ()
    depends_on: tuple[str, ...] = ()
    input_artifact_refs: tuple[str, ...] = ()

@dataclass(frozen=True)
class SwarmWorkflowDefinition:
    ref: SwarmWorkflowRef
    nodes: tuple[SwarmWorkflowNode, ...]

@dataclass(frozen=True)
class SwarmWorkflowPolicy:
    workflow: SwarmWorkflowDefinition
    method: Literal["Swarm"] = field(default="Swarm", init=False)
```

字段语义：

| 字段 | 契约 |
| --- | --- |
| `ref` | 工作流的固定 ID 与正整数版本；不引用 L6 的 `VersionedProfileRef`。作为固定 ProductProfile 版本的一部分存入数据库，不另建 workflow 注册中心。 |
| `node_id` | 工作流内唯一稳定标识，不使用展示名、列表位置或 Agent ID 代替。 |
| `name` / `objective` | 分别为展示名和完整执行目标；目标不能只保留在配置中而丢失在执行链路里。 |
| `agents` | 节点的候选白名单；元素为既有 `AgentRef(agent_type, agent_id)`，不是 Participant ID，也不是字符串拼接的 BCS 身份。 |
| `depends_on` | 全部直接前驱 node_id；仅当所有前驱 SUCCEEDED 才允许执行。空值表示入口；允许纯控制依赖，不自动读取其全部输出。 |
| `outputs` | 非空命名产物声明；每项指定用途、Resource 类型、固定内容合同及数量范围。`name` 在节点内唯一，对应运行时输出端口，不是文件路径。 |
| `inputs` | 命名输入绑定，显式选择直接前驱的某个输出；`name` 为本节点输入名，`from_node/from_output` 为来源。数量与类型由来源声明及本节点 input contract 共同约束。 |
| I/O 契约引用 | 声明所需执行能力的输入与输出协议，沿用运行时 ExecutionProfile 的契约引用语义。 |
| `completion_contract_ref` | 非空、版本化的完成判定契约引用，由注入的 `StageExecutionService.accepts` 解释；不是 Python 表达式或新 DSL。 |
| `output_artifact_roles`（运行时投影） | 不再作为 workflow 节点另一份可配置字段；编译为 `tuple(output.name for output in outputs)`，复用既有 Stage/子 Task 完成条件，不能与 outputs 分别维护。 |
| `input_artifact_refs` | 额外的外部既有 Artifact 引用，不代替节点间 inputs 绑定；执行前也必须解析到固定版本并检查读取权限。不会因配置了引用就获得 Resource 访问权限。 |

所有集合为不可变 tuple；严格解码拒绝未知字段、空白标识、非法类型与非正整数版本，不能以 `bool` 充当版本。条件结构用上述类型表达，不保存可变配置 dict 作为领域权威。

### 3.2 与现有 policy 的关系

```python
MethodPolicy = OpenMethodPolicy | SwarmMethodPolicy | SwarmWorkflowPolicy
```

- `SwarmMethodPolicy` 的必填 `plan_service_ref: str` 保持不变，包括位置参数调用。
- `SwarmWorkflowPolicy` 的 `workflow` 必填，不接受 `plan_service_ref`。
- 一个 Swarm MethodTemplate 只能选择其中一种；不支持“同时配置但 DAG 优先”的隐式覆盖。
- `policy=None` 仍表示仅有结构性 Method 声明，不自动启用任何执行模式。
- 可选 Method 声明中的 policy 仍不自动启用；首版工作流只作用于明确选中的 required Swarm 和其唯一 ACTIVE 顶层绑定。
- ProductProfile authoring、数据库发布/读取与静态 ProductCatalog 共用领域验证；`create_product_profile` 仍是纯创建定义，不隐式写库。只有独立、授权的发布操作才保存定义；发布不实例化、不创建 Plan 或执行。

### 3.3 ProductProfile 配置示例

以下为 Python authoring API 的配置示意，省略 import；不是自包含可直接运行的脚本。得到定义后，还需通过 §9.3 的内部发布操作保存至 ProductProfile 表；不新增 HTTP/YAML 导入接口。

```python
researchers = (AgentRef("bot", "research_a"), AgentRef("bot", "research_b"))
developers = (AgentRef("bot", "dev_a"), AgentRef("bot", "dev_b"))
reviewers = (AgentRef("bot", "review_a"), AgentRef("bot", "review_b"))
publishers = (AgentRef("bot", "publish_a"),)

def node(node_id, objective, agents, output_name, content_contract, inputs=()):
    return SwarmWorkflowNode(
        node_id=node_id,
        name=node_id,
        objective=objective,
        agents=agents,
        depends_on=tuple(dict.fromkeys(item.from_node for item in inputs)),
        inputs=inputs,
        input_contract_ref="workflow_artifact_inputs/1",
        output_contract_ref="workflow_artifact_outputs/1",
        completion_contract_ref="declared_artifact_outputs/1",
        outputs=(SwarmArtifactOutput(
            name=output_name,
            description=objective,
            resource_type="document",
            content_contract_ref=content_contract,
        ),),
    )

profile = create_product_profile(ProductProfileCreateCommand(
    ref=VersionedProfileRef("dag_delivery_swarm", 1),
    composition=MethodCompositionTemplate(required_methods=(
        MethodTemplate(
            profile=get_profile("Swarm", "0.2"),
            policy=SwarmWorkflowPolicy(workflow=SwarmWorkflowDefinition(
                ref=SwarmWorkflowRef("delivery_dag", 1),
                nodes=(
                    node("prepare", "分析目标并输出方案", researchers,
                         "plan", "delivery_plan/1"),
                    node("build", "依据方案实现并输出实现清单", developers,
                         "implementation", "implementation_manifest/1",
                         (SwarmArtifactInput("plan", "prepare", "plan"),)),
                    node("review", "独立评审方案并输出意见", reviewers,
                         "review_report", "review_report/1",
                         (SwarmArtifactInput("plan", "prepare", "plan"),)),
                    node("finalize", "汇总实现和评审结果", publishers,
                         "delivery", "delivery_summary/1", (
                             SwarmArtifactInput("implementation", "build", "implementation"),
                             SwarmArtifactInput("review", "review", "review_report"),
                         )),
                ),
            )),
        ),
    )),
    # workflow=None：仍使用 standard 根 Task contract，不取消 G1/G4。
))
```

示例 helper 在构造节点时显式生成 depends_on；领域 decoder/运行时不会替调用方补依赖。示例中的输入/输出/完成与内容合同引用需由宿主执行/资源适配器显式支持；公共默认装配没有安装这些示例合同的执行能力。这里所有 Resource 类型为 document，build 产出的是固定代码版本的实现清单；代码仓库、包或其他 Resource 也可通过对应类型/内容合同声明，不要求把代码正文存成文档。

图结构为 `prepare → {build, review} → finalize`。首版实际派发顺序为 prepare、build、review、finalize；若配置调整了独立分支的声明顺序，则同等 READY 节点的先后次序相应变化。

定义发布入库后，仍通过既有 ProductInstantiate 显式传入参与者；根 Task 的 `executor_ref` 是根成果提交者，不是每个节点执行者。可以将节点候选的并集通过 `participant_refs` 提供，但**不得因出现在 `agents` 中就自动增加 Participant、RoleBinding、Open admission 或 Resource Grant**。

### 3.4 配置校验

纯创建、发布与数据库解码时，执行 O(V+E) 拓扑校验；首版限制 `1 <= V <= 256`、`E <= 4096`，作为本模块明确的有界配置限制。

必须拒绝：重复 node_id、空 Agent 列表、重复 Agent 身份、重复依赖、自环、缺失依赖、环、空目标、空 I/O/完成契约、空 outputs、重复输出/输入名、空产物用途/类型/内容合同、非法数量范围，以及不存在的输入来源节点/输出端口。输入来源必须属于 depends_on 的直接前驱，不能只按字符串相同绑定或偷偷新增依赖。每个节点首版最多 16 个输出端口、64 个输入绑定；每个输出要求 `1 <= min_items <= max_items <= 64`，版本和数量均不接受 bool。重复 Agent 不用作权重；同 ID 不同 agent_type 是不同身份。

多入口、多出口、独立分量合法，全部节点均必须完成；不存在需要单独配置的 START/END 虚拟节点。Candidate Agent 必须满足 ProductProfile 的 `participant_scope`；这只检查产品准入范围，不声称其已参加当前 Collet 或在线可用。

首版完整节点数与层宽/深度必须能通过既有 Swarm `structural_violations`。输入、输出、完成与内容契约引用的运行时支持情况由执行能力检查，不在纯 Profile authoring 中访问网络或宣称已经验证。执行适配器应在 `execute` 内实际派发 Worker 之前校验 I/O、完成和内容契约是否受支持，并确认命名输入绑定可按目标 input contract 消费，未知契约必须失败；`accepts` 使用同一契约验证报告。这是适配器契约要求，不假定现有接口已经有额外的 supports 方法。

## 4. 分层与装配

```text
ac_context_collab_product_profile（完整版本化定义 JSON）
       │ DatabasePlugin → L6 ProfileRepository / ProductProfileReader
       ▼
L6 ProductProfile（不可变解码结果 / Catalog 快照）
       │ 选择 L5 SwarmWorkflowPolicy；固定产品版本
       ▼
DI ProductSwarmRuntimeResolver
       ├─ 动态 policy   → 已安装的具名 SwarmPlanningService
       └─ workflow policy → WorkflowPlanningService(workflow, scope, chooser)
                                      │ plan(PlanningRequest)
                                      ▼
L5 SwarmDriver → L5 Swarm 操作 / G1-G4 → MethodStore + L4 SQL → StageExecutionService
```

1. `WorkflowPlanningService` 建议位于协作域根部新增的 `workflow_planning.py`，与 `swarm_planning.py` 同层，实现现有出站规划接口；不放在 L5 中当作写权威服务。
2. 现有 `PlanningRequest`、`PlanningDecision`、`StageAssignment` 签名保持不变。规划器构造时接收固定 workflow、StoreAxes 与绑定执行作用域，并核对每个请求的 Collet/root Task 与构造作用域一致；`plan` 不自行读取 Catalog、数据库或全局 injector。
3. DI 在 `di/modules/task/swarm_runtime.py` 根据实际 policy 类型选择实现；DAG 模式不要求注册外部 plan capability，动态模式仍严格检查 `plan_service_ref`。
4. `SwarmRuntimeResolver` 继续提供规划身份与受信任的 `start_actor`。配置 DAG 不改变根 Work.start 的身份选择，也不让随机选中的节点 Agent 兼任根启动者。
5. 为防止只约束 Driver、却被直接调用 Swarm Service API 绕过，L5 新增窄端口 `SwarmWorkflowPolicyReader.workflow_for(collet_id, binding_id) -> SwarmWorkflowDefinition | None`。DI 的产品适配器从固定 ProductInstance 和作用域内的数据库 ProfileReader 返回下层定义；只有明确的非工作流场景才能返回 None，查找失败必须报错。
6. RuntimeResolver 与 PolicyReader 共用同一固定产品选择解析，不维护第二份注册表。后者不构造规划器、不解析远程执行能力，只暴露 L5 需要的合同。
7. L5 在 submit/adopt/allocate 边界使用同一工作流一致性校验；workflow 绑定上的候选若缺少匹配的来源信息或完整图，不能靠缺省字段伪装成动态计划。现有 HTTP DTO 无法表达的受管候选应明确拒绝，走受管 Driver 编译，不为兼容而补造来源或放宽校验；暂停/恢复等既有管理动作仍可使用。L5 不能导入 L6、具体产品、HTTP 或 DI。PolicyReader 保护的是真实上下层边界，需消费者级契约测试。

## 5. DAG 编译与运行时对象

### 5.1 首次编译

第一次 `plan(request)` 无 Stage 时，将完整 DAG 编译为一个 `SwarmPlanCandidateCommand`：

| 工作流声明 | 编译结果 |
| --- | --- |
| 一个 workflow | 一个 SwarmPlan，根 `task_ref` 使用当前 root Work。 |
| 一个 node | 一个 Stage，保留 node_id 对应关系；每个 Stage 分配独立子 Task ID。 |
| `depends_on` | 映射为同一个 Plan 内的 `dependency_stage_refs`。 |
| `name` / `objective` | Stage 展示名 / 完整执行目标，不能把二者混为同一字段。 |
| `inputs` / 外部输入引用 | 保留命名来源声明；初始 Stage.input_artifact_refs 仅含外部引用，前驱实际产物的命名绑定在分配时冻结。 |
| `outputs` / 完成契约 | 非空 Stage.completion_condition 包含固定完成 contract_ref 与完整 outputs 声明，Stage.output_artifact_roles 从 outputs.name 投影。后者只复用旧角色/完成条件，不替代产物类型、数量和内容校验。 |
| 全图完成 | Plan.termination_condition 固定为 `{"all_stages_succeeded": true}`，仍由权威完成操作复核。 |

Plan/Stage/子 Task ID 使用规范化元组的 SHA-256 摘要派生，元组包含租户、env、Collet、Swarm binding、根 Task、workflow 固定版本及 digest；节点对象另外包含 node_id 和对象类型。摘要使用完整十六进制或满足现有 128 字符限制的明确编码，不使用进程相关 `hash()`、展示名或随机 UUID。

- 相同执行作用域重编译得到同一组 ID；不同上下文、绑定或根 Task 不共用 ID。
- 子 Task ID 在编译时只是引用，实际创建仍由分配后的 `start_task` 完成。
- workflow digest 覆盖全部规范化声明，保留 nodes 的顺序，因为顺序影响调度。
- `max_depth` 与 `max_parallelism` 分别填入现有 `layer_metrics` 定义的深度与最大层宽，而不是把 fork/join DAG 的 `max_parallelism` 硬编码为 1。**该字段当前参与结构宽度校验，不等于 Driver 的实际并发数**；首版实际在途 Stage 仍最多一个。
- 图编译和第一次执行者选择只产生提议，不写 MethodStore、不创建 Task、不派发。

### 5.2 对已有值对象的最小扩展

| 对象 | 拟新增字段 | 兼容语义 |
| --- | --- | --- |
| SwarmPlan / 内部候选命令 | `workflow_ref: SwarmWorkflowRef | None`、`workflow_digest: str` | None + 空 digest 表示原动态计划；有 ref 时必须有匹配 digest。 |
| Stage | `workflow_node_id: str | None`、`objective: str` | 旧 Stage 为 None / 空字符串；旧模式有效目标仍为 name，新模式必须有完整 objective。 |
| Stage | `input_bindings`、`output_bindings`，均为不可变 `tuple[ArtifactPortBinding, ...]` | 旧 Stage 默认空且省略输出；workflow 输入在分配时固定，输出只有经过校验并完成时才发布。 |
| StageExecutionReport / SwarmStageOutcomeCommand | keyword-only `output_bindings`，默认为空 tuple | 保留既有位置参数与动态报告；workflow 要求完整命名产物，只有 artifact_refs 的旧报告不能冒充新模式。 |

这些字段不是新的持久化实体。需要同步值对象的严格 decoder、serializer、请求形态白名单和内存 Store 测试；旧字段缺失可读，默认扩展字段不输出以保持旧模式形态。

候选 Agent 列表不再复制到 Stage：其权威为固定 workflow 中该 node 的定义；PolicyReader + node_id 解析唯一候选范围。运行期不得更改 node_id、目标、依赖、输入来源/输出声明、完成契约或候选范围。

`SwarmStageReportService.start_task` 改为使用 Stage 的有效 objective 创建子 Task；`StageExecutionService.execute` 签名不变，接收包含完整目标、输出声明和已解析命名输入的 Stage；`accepts` 的行为合同扩展见 §7，不能把签名没变等同于实现无需更新。

### 5.3 每次 plan 的选择规则

对于已经安装的 Plan，Driver/Swarm 操作先核对工作流引用与 digest；规划器再验证请求中的 Stage 集完整、node_id 一一对应且不可变内容与构造时固定的定义一致。缺失或多出 Stage 是状态冲突，不能当成“首次调用”再生成新图。Plan 本身不在当前 PlanningRequest 中，不为此偷偷增加规划器的 Store 读取。

```python
if no_installed_stages:
    candidate = compile_complete_dag(workflow, scope, request.task_ref)
    next_node = first_entry_in_declaration_order(workflow)
else:
    candidate = None
    if all_nodes_succeeded_with_output_evidence(request.stages):
        return PlanningDecision(finished=True)
    next_node = first_ready_node_in_declaration_order(workflow, request.stages)

eligible = configured_agents_intersect_current_authorized_participants(next_node)
if not eligible:
    raise workflow_error("no_eligible_agent", node_id=next_node.node_id)
participant = chooser(eligible)
return PlanningDecision(candidate, StageAssignment(
    stage_ref=stage_id_of(next_node),
    participant_ref=participant.participant_id,
    input_contract_ref=next_node.input_contract_ref,
    output_contract_ref=next_node.output_contract_ref,
))
```

- 首次提议包含全部 PENDING Stage 和一个入口 assignment；Driver 通过 submit → validate → adopt → start 后才允许分配入口。
- 之后只选 **READY 且全部直接依赖 SUCCEEDED** 的 Stage；以实际状态为准，不依赖上次调用次数或内部游标。
- 多个合法 READY 节点按声明顺序选取；不随机选择节点，不因某个节点没有合法 Agent 而跳过它去执行其他分支。
- RUNNING/ALLOCATING 时 Driver 保持现有等待行为，不再生成第二份分配；PAUSED 或终态计划不派发。
- 有 FAILED/CANCELLED 前驱、状态集不完整或无法解释的无 READY 状态时显式报错，不能返回 finished，也不降级成动态重规划。
- 完成提议不带 candidate/assignment。最终仍需 `complete_plan` 核验子 Task、产物、租约/分配等权威事实，根 Task 提交和验收不变。

## 6. 随机选 Agent 与授权边界

节点 n 在执行前的候选集合为：

```text
E(n) = n.agents
       ∩ 当前 Collet 的实际 Agent Participants
       ∩ participant_scope / 当前 Open admission 等治理允许范围
       ∩ 当前 Stage 既有 RoleBinding 要求（若有）
```

按 `(agent_type, agent_id)` 完整匹配，再唯一映射到 Participant；身份映射歧义应拒绝，不能按 ID 模糊匹配、取第一个 Participant，或绕过已撤销的 admission。

- 生产默认对 E(n) 做**均匀随机**选择，使用注入的选择 callable；默认实现可使用 `random.SystemRandom().choice`，测试注入确定性替身。
- 单候选直接执行该候选；不同节点可以选中同一个 Agent。
- 不检查“在线”来扩大本需求；静态准入不代表传输一定成功。派发失败必须作为失败暴露，不能静默换人。
- 第一次有效规划到分配之间立即选人。尚未提交的过期提议允许丢弃后重新计算；**分配一旦提交，其 target_ref、execution profile 和子 Task.executor_refs 必须一致，不能重新抽签**。
- Profile authoring / instantiate 不选人；普通查询、重复 wake、已在途执行、已完成节点不调用 chooser。
- 分配提交前重新校验当前参与资格和 DAG；Driver 的旧快照或已缓存 Participant 不能作为长期授权凭证。
- 首版不建立“换一个 Agent 就是重试”的隐式语义。未来重试必须先演进 Stage/Allocation 生命周期、子 Task 重派和 attempt 围栏，另行设计。

## 7. 产物合同、交接与实际执行

### 7.1 定义产物，而不是定义“说一句完成”

**每个节点必须声明 outputs，节点间业务数据以经校验的 Artifact 交接。** 产物不局限于文件：方案文档、代码提交/构建包、数据集、测试报告、评审结论、审批决定、外部操作回执都可以是有固定内容合同的 Resource 投影。聊天、日志和口头“完成”不能代替节点输出；调度状态、执行身份、Task 目标等控制元数据也不必伪装成产物。

区分三个对象：

| 对象 | 保存位置 / 生命周期 | 用途 |
| --- | --- | --- |
| 输出声明 `SwarmArtifactOutput` | ProductProfile.definition_json 内，随产品版本不可变 | 约定“应交付什么”：逻辑名、用途、资源类型、内容合同及数量。没有运行期 URI、内容或随机结果。 |
| 实际产物 `Artifact(ResourceBinding(ResourceRef))` | 具体资源由外部资源能力保存；引用作为执行报告/既有事件证据保存 | 约定“实际交付哪一份”：Artifact ID、Resource 类型/ID/固定 revision，以及已有 grant 引用。不是数据库中的文件正文。 |
| 命名交接 `ArtifactPortBinding` | 报告、Stage 输入/输出；成功发布后不可变 | 把输出逻辑名关联到实际 Artifact；后继再用自己的输入名接收。不是第二个资源注册中心或新的运行表。 |

建议在既有 G4 `execution_report.py`（或其同域证据值模块）定义只依赖 L4 Artifact 的共享证据类型，L5 直接消费；不能把 L5 workflow 定义导入 L4：

```python
@dataclass(frozen=True)
class ArtifactPortBinding:
    name: str
    artifacts: tuple[Artifact, ...]
```

在 workflow 模式中，Artifact 的 `ResourceRef.revision` 必须为非空的、由资源提供方保证不可变的精确版本；L3 通用 ResourceRef 的 revision 仍允许 None，不改变其他场景。可变本地路径、未固定版本的链接、会变化的 latest 标记不满足交接要求；资源本身不支持固定版本时，适配器须先提供可验证的不可变快照，否则明确失败。`grant_ref` 只是授权引用，不因为它非空就自动授权下游。

### 7.2 控制边和数据绑定分离

- `depends_on`：谁完成后才允许调度，沿用全部前驱 SUCCEEDED 的 AND 语义。
- `inputs`：明确本节点需要哪个前驱的哪一个命名输出；所有来源节点必须已在 depends_on 声明。
- 存在依赖但没有相应 input 时，表示**纯控制依赖**；不会把该前驱的所有数据隐式暴露给 Worker。因此“以产物交接”不等于“每一条边都必须传输全部产物”。

示例交接：

```text
prepare.outputs.plan ──────→ build.inputs.plan
                    └─────→ review.inputs.plan
build.outputs.implementation ─→ finalize.inputs.implementation
review.outputs.review_report ─→ finalize.inputs.review
```

两个前驱即使都把输出命名为 result，也必须分别用 from_node/from_output 选择，并映射为本节点不同 input.name；不靠文件名、列表下标、聊天顺序猜测。首个节点可使用根任务目标与显式外部输入，末节点仍需产出最终成果，但这不自动完成根 Task 验收。

### 7.3 报告、校验、发布成功产物

workflow Worker 的 StageExecutionReport 在既有 artifact_refs 外携带完整 `output_bindings`。其 name 对应定义端口，artifacts 是 typed L4 Artifact；报告可按端口任意排列，入口校验后按声明顺序规范化，单端口内 Artifact 顺序保留。不允许缺少必需端口、额外端口、重名绑定、重复 Artifact ID、错误数量、不同类型或未固定 revision。

`artifact_refs` 必须恰好等于规范化 bindings 按声明顺序展开的 Artifact.artifact_id；它是旧链路所需的引用投影，不是另一份可以独立修改的输出。同一执行作用域中的同一 Artifact ID 不能映射到不同 Resource/版本；对输入来源的定位仍以固定 node/port 为准，而不是全局搜索相同字符串。

受管模式完成链路如下，其中第 1–3 步必须在任何子 Task 提交/内部验收之前完成：

1. 保留既有 actor、binding、Stage、Allocation、租约和子 Task executor 校验；报告必须来自实际被分配 Agent。
2. 通过 PolicyReader 的固定节点定义校验完整命名输出、数量、类型及精确版本，不能只检查 `artifact_refs` 非空或 output_artifact_roles 存在。
3. 经 L5-owned 窄只读端口 `SwarmArtifactEvidenceValidator.validate_outputs(stage, output_bindings)` 验证，由 DI 适配器复用执行适配器 `accepts(stage, report)` 的同一内容验证逻辑，按 content_contract_ref/完成合同，经已安装的资源读取与验证能力验证引用确实存在、版本可解析、内容结构/格式及约定完成证据。JSON schema、Markdown 必需章节或测试结果等判定由对应合同实现；不能让 L5 写死业务/文件逻辑，也不能把 Worker 自报“schema 通过”当成验证结果。验证会按权限读取必要内容，调度器不批量复制资源正文；未知合同或验证能力缺失必须失败。新窄端口无派发/状态写方法；report 和直接完成操作都必须使用，消费者测试证明没有绕过。
4. 在既有子 Task 的 task.submitted/task.accepted 事件 JSON payload 中保留 artifact_refs 投影，并附加**版本化的 workflow 产物证据 envelope**（schema_version=1、规范 output_bindings）。同步 G4 报告值/decoder、相关事件 schema 与受管子 Task 的证据校验白名单；不得靠忽略未知字段传入，也不改写根 standard_task@1 的 action/控制语义。
5. 子 Task 到 done 后，受管 `complete_stage` 核对该子 Task 的提交/验收主体、Allocation 与当前完整 manifest 一致，并通过同一 validator 再验证固定版本资源与内容，然后提交 output_bindings、output_artifact_refs 和 SUCCEEDED。事件里带了 envelope 不等于已由可信校验器验证，不能把调用方自填的 verified 标记当权威。普通 Swarm Service API 的直接完成调用也必须经过相同守卫，只有 refs/空完成条件的管理命令或经通用 Work 入口拼造的 manifest 不能绕过；保持原动态模式。

只有成功发布的 Stage.output_bindings 能成为下游输入。上传了文件、写入了日志、发送了 Message 或派发接口返回均不等于成功交接。首次校验失败不推进子 Task/Stage，也不放行后继；若后续重验或提交失败，则保留真实已提交的子 Task 状态，但不发布 Stage 成功/下游输入。错误显式暴露，不在此处自动换人重试。资源因上传先发生而留下但未发布，不代表可以被 DAG 使用；清理上传临时资源不在本期范围内。

SQL 与 MethodStore 仍无共同事务。提交后部分失败的同进程协调必须比较**完整命名 manifest**，包括 Artifact ID、Resource 类型/ID/revision 与 grant 引用，而不仅仅比较平铺 ID 列表；相同 refs 改端口/版本也要拒绝。Stage 输出与 SUCCEEDED 在同一 MethodStore 临界区提交，后继输入解析使用该成功快照；不宣称 SQL/MethodStore/外部存储分布式原子。当前 `Collet.artifacts` 是模型能力、报告尚未自动聚合，本方案不依赖或暗示该聚合已经存在。

### 7.4 下游派发：按绑定选择并冻结引用

在分配提交前，按 inputs 声明顺序从每个直接前驱的**成功 output_bindings** 取出指定输出；检查来源的 Plan/Collet/租户/Task 对应关系、manifest 与已接受事件一致、固定版本及下游实际 Agent 的访问权限，构造本节点命名输入：

```text
input_bindings(n)[input.name] =
    succeeded_stage(input.from_node).output_bindings[input.from_output]

input_artifact_refs(n) = stable_unique(
    explicit_external_refs(n)
    + flatten_artifact_ids(input_bindings(n), in_input_declaration_order)
)
```

平铺去重只为兼容旧 input_artifact_refs；**不删除命名绑定或合并两个输入端口**。命名 bindings 保留 Artifact 的 ResourceRef 精确版本，不能派发时重新查 latest；不复制所有祖先输出、不隐式合并未声明输出、不把来源相同当作允许跨 Collet 取数。

输入 bindings/派生 refs、Stage RUNNING 与 Allocation 一并冻结，纳入提议过期检测和复合提交的预期快照。Worker 收到完整 objective、命名输入、输出声明与 I/O 合同；执行适配器在实际读取时再次执行当前资源授权，配置 bindings/Grant 快照不能抵消撤权。不可读或版本失效应显式阻断执行，不隐式补授权、替换版本或发送正文绕过读取限制。先按现有合法候选随机选人，再检查该人的资源可读性；失败不在同次计划中偷偷抽另一人。

新模式的初始计划幂等比较以固定定义/digest 与不可变 Stage 字段为准，不能因为分配时填入了真实命名输入，就把同一初始 DAG 的重放判定为另一份图。动态模式沿用旧请求形态比较，不扩散改变其行为。

## 8. 一致性、失败与有界执行

### 8.1 防止重复分配

当前 Driver 的 `_snapshot` 可拒绝过期规划，但现有 Stage 更新、Allocation 插入分属多次 Store 调用；不能仅凭字典各自带锁就声称复合操作原子。

本增量为 workflow 分配增加 `MethodStoreProtocol` 的复合提交操作：

1. G1 等前置校验通过后，在同一 Store 锁/将来的数据库事务内先检查分配幂等键：同键同请求返回原结果，不再要求原 Stage 仍 READY；同键异形态冲突。重放保留首次 target、契约和租约请求，不以当前时间重新生成。
2. 对尚未提交的新分配，校验 binding ACTIVE、Plan 未变且 RUNNING、Stage 等于预期快照且 READY、计划无其他在途 Stage/ACTIVE allocation、该节点没有已提交分配。
3. 校验已声明 execution profile 与选中 target 一致；一次性提交派生命名输入/引用、Stage RUNNING 与唯一 ACTIVE Allocation，并围栏来源 Stage 的成功 manifest 快照。冲突不写上述记录，分配 ID 按 Stage 与首版 attempt=1 派生。
4. ExecutionProfile 可沿用先声明的幂等操作，其稳定 ID 额外包含选中 target 与 I/O 契约，避免未接纳的另一份随机提议占住同一 profile ID。竞争失败可能留下未使用 profile，但不能因此执行。不得在 Store 锁内调用网络或 Worker。
5. G1、DAG 和参与资格由上层操作在提交前校验；原子提交不替代授权。SQL 中的参与事实与 MethodStore 并非一个事务，不能宣称跨存储线性一致。

直接 Swarm 操作也必须使用工作流候选范围和复合提交，不能经另一条入口恢复为无约束分配。随机只影响提议，实际接纳的执行者以首次成功提交为准。

### 8.2 副作用与失败顺序

```text
读取新状态 → plan 提议 → 拒绝过期结果 → G1/G4 + DAG/候选校验
→ 接纳/启动 Plan（首次）→ 声明 ExecutionProfile → 原子提交分配
→ 通过 L4 SQL/CAS 创建并启动子 Task → executor.execute
→ Worker 上传固定版本产物并上报 manifest → 输出合同/内容校验
→ 子 Task 提交/内部验收并记录完整证据 → complete_stage 发布命名输出 → 下次 wake
```

- DAG 错误、候选范围错误必须在创建子 Task 或调用 Worker 之前失败；不能先执行再发现非法 assignment。
- 没有合法 Agent：保留 READY，不分配、不补授权、不返回成功；治理操作修复事实后可显式 wake。
- submit/adopt/start 是既有多步操作。中途失败暴露实际已提交状态；不得承诺再次 `drain` 会自动修复 DRAFT/READY 或不完整 Stage 集。
- 分配后子 Task 创建或派发失败：保留已提交的分配和错误上下文，停止继续派发；不自动释放租约、换人或重建 Task。后续恢复需显式管理处理。
- 报告失败沿用现有部分提交协调能力，只允许相同完整命名产物证据的报告继续协调；不得通过新 planning 分配替代报告恢复。
- 外部执行不是 exactly-once，MethodStore 与 SQL/派发之间没有分布式事务；复合提交只保证该进程内 Store 接纳分配时的一致性。

### 8.3 失败、暂停与调度预算

- 节点失败采用 fail-stop：不自动重试，不放行其后继，不自动把根 Task 改成成功或失败；管理者按既有显式操作处理。
- PAUSED 时不派发；已在途报告仍按既有契约接纳，依赖 READY 投影在 resume 后补齐。
- 异步 Worker 报告后仍需要宿主显式 wake；本方案不新增后台线程、轮询或持久队列。
- 当前 `drain()` 有 100 事件上限，256 节点的合法串行 DAG 会超过它。工作流模式使用有界预算 `max(100, 2 * (node_count + 1) + 4)`，动态模式保持原预算；不取消死循环保护，也不把派发返回当作节点完成。


### 8.4 诊断与可观测性

沿用现有日志/错误与 G3 边界，不新增“进度成功即完成”的捷径。运行记录应包含 product 固定版本、workflow_ref/digest、node_id、Stage/Task/Allocation ID、输入/输出端口与产物固定版本引用、候选数量及最终 Agent 身份，避免记录目标正文、凭证或资源内容。

配置无效、无合法候选、图/digest 不匹配、过期提议、分配冲突、执行/内容契约不支持、产物缺失或类型/数量/版本不符、输入访问拒绝、部分提交和内存历史丢失需要可区分的 reason。通过既有领域错误及 transport 映射暴露；本文伪代码中的 `workflow_error` 代表领域错误构造，不是已存在的新 API。未提供自动恢复能力的错误应保留已提交对象引用，明确需要治理/恢复处理，而不是返回假成功。

## 9. 版本、存储与兼容性

### 9.1 固定版本

DAG 与候选名单是 ProductProfile 的不可变合同。改目标、节点顺序、依赖、Agent 名单、inputs/outputs 或 I/O/完成/内容契约，必须发布新 ProductProfile 版本，同时更新 workflow 版本；不原地覆盖同版本定义。

ProductInstance 保持既有产品版本固定。Plan 保存 workflow_ref/digest，用于核验运行图与被选定义一致；运行中不能改用 Catalog 的“最新”版本。缺少历史定义或 digest 不一致即失败，不回退动态规划。

旧 ProductInstance 没有 DAG 的继续保持动态模式。新 DAG 只用于新产品版本/新实例，不自动给运行中的旧 Plan 增加图定义或重选执行者。

### 9.2 新增 ProductProfile 表：定义整体落库

**本方案新增数据库表 1 张：`ac_context_collab_product_profile`；现有表不增加 SQL 列。** 当前模块已有 Collet、Event、Task、Telemetry 共 4 张表；本方案实现并迁移后为 5 张。此数量仅覆盖本需求，不含未来 MethodStore 的持久化改造。

一行对应当前租户/环境中的一个**已发布且不可变的完整产品版本**。不采用“一行一个产品、更新覆盖 JSON”，也不把 DAG 单独存成另一份定义权威。

| 字段 | 建议 SQL 类型 / 约束 | 说明 |
| --- | --- | --- |
| `id` | BIGINT，自增主键 | 存储内部主键；SQLite 测试沿用现有 Integer variant。 |
| `profile_id` | VARCHAR(128)，NOT NULL | 映射 `VersionedProfileRef.profile_id`，不是枚举限制。 |
| `profile_version` | INT，NOT NULL，领域校验 > 0 | 映射 `VersionedProfileRef.version`。 |
| `definition_schema_version` | INT，NOT NULL，首版 1 | JSON 存储格式版本，区别于业务产品版本、Method 版本和 workflow 版本。 |
| `definition_json` | MEDIUMTEXT，NOT NULL | 完整 ProductProfile 声明的严格 JSON；SQLite 使用 TEXT，与模块已有 JSON 存储风格一致。 |
| `definition_digest` | CHAR(64)，NOT NULL | 规范化存储 envelope 的 SHA-256，读取时重算核对；不替代发布授权。 |
| `published_by` | TEXT，NOT NULL | 受信任发布主体的 typed PrincipalRef JSON；不接收配置里自报的发布者。 |
| `avernet_tenant` | VARCHAR(64)，NOT NULL | 沿用现有租户轴及 ORM tenant guard。 |
| `env` | VARCHAR(20)，NOT NULL | 沿用现有环境轴。 |
| `gmt_create` / `gmt_modified` | DATETIME，NOT NULL | 服务端 UTC 时间；首版无更新操作，两者在发布时相同。 |

唯一索引：`(avernet_tenant, env, profile_id, profile_version)`。产品 ID 使用精确、区分大小写的比较语义，DDL/ORM/查询必须一致，不能让 MySQL 默认排序规则与领域字符串比较产生不同的唯一性结果。首版只按精确版本和当前作用域枚举查询，不建 DAG JSON 索引或“当前节点”列。`profile_id` 的新存储长度限制须在发布/迁移前显式验证；不截断既有 ID。

`definition_json` 包含 ref、required/optional Method 组合及 policy、scenario_roles、governance、compatibility、participant_scope、domain、原根 Task workflow/actions、task_contract_ref 等全部定义字段。新 Stage DAG 就在 Swarm Method policy 内，包含 node_id、objective、agents、depends_on、inputs/outputs、I/O、内容和完成契约；只保存产物声明/来源，不保存本次实际产物或正文。**不能只保存 DAG 而把其余可配置 ProductProfile 字段留在代码作为第二份产品定义。**

以下仅展示 JSON 的嵌套位置；`...` 为省略内容，**不是完整合法发布请求**：

```text
definition_json = {
  "ref": {"profile_id": "dag_delivery_swarm", "version": 1},
  "composition": {
    "required_methods": [{
      "profile_ref": {"method": "Swarm", "version": "0.2"},
      "policy": {
        "kind": "swarm_workflow",
        "workflow": {
          "ref": {"workflow_id": "delivery_dag", "version": 1},
          "nodes": [{
            "node_id": "build",
            "objective": "依据方案实现并输出产物",
            "agents": [
              {"agent_type": "bot", "agent_id": "dev_a"},
              {"agent_type": "bot", "agent_id": "dev_b"}
            ],
            "depends_on": ["prepare"],
            "inputs": [{"name": "plan", "from_node": "prepare", "from_output": "plan"}],
            "outputs": [{
              "name": "implementation",
              "description": "交付固定代码版本的实现清单",
              "resource_type": "document",
              "content_contract_ref": "implementation_manifest/1",
              "min_items": 1, "max_items": 1
            }],
            ...
          }, ...]
        }
      }
    }],
    ...
  },
  ...
}
```

不拆分 workflow/node/edge 表的原因：DAG 是有界产品定义的一部分，与同一版本的角色、治理和 Method 配置一起发布、读取、校验；本需求没有跨产品独立编辑节点、跨版本复用可变 DAG 或按节点查询的要求。以后确有这些需求时再单独演进，不能现在引入第二套图生命周期。

### 9.3 严格存储 codec 与授权发布

新增 L6 `ProductProfilePublishCommand` / `ProductProfilePublicationServiceProtocol`，独立于纯 `create_product_profile`；通过注入的 ProfileRepository 使用 DatabasePlugin 写入。首版只支持“发布已解析的完整定义”，调用方显式指定正整数版本；不自动分配 latest+1，不隐式发布、覆盖或激活。内部运维入口也必须提供受信任 ActorContext，并通过专门的 G1 产品定义发布授权；不能复用 Collet 创建权限、信任 JSON 中的 actor，或因为调用的是内部方法就跳过授权。授权检查与受影响的 G1 合同/消费者测试须随实现一起补齐。

发布顺序为：**确认作用域和授权 → 领域验证 → 存储 codec/已安装声明核验 → 计算规范 JSON/digest → 单事务插入完整版本**。发布不需要创建 Collet、Task、Method 或预先随机选人。

- codec 版本 1 必须显式定义字段、枚举、tuple/list 映射和有界 policy discriminator：例如 `open`、`swarm_dynamic`、`swarm_workflow`；不能只用 `method="Swarm"` 区分两个策略。Open admission 的基类与 `BotSelfApplicationAdmission` 也要用白名单 discriminator 无损往返，不能丢失具体策略。
- 存储 `MethodTemplate.profile` 时只编码精确 `MethodProfileRef`。编码前要求完整声明与该版本已安装 MethodProfile 相同；解码时从代码已安装的精确版本还原完整 MethodProfile。数据库能选择 Method 合同，不能覆写其能力列表、任意导入 Python 类型或提供可执行代码。L5 MethodProfile 继续 code-owned；变更同版本 Method 实现合同不在本提案授权范围内。
- 原 WorkflowProfile 的状态机引用、Task 合同引用及动态 `plan_service_ref` 也只能选择已声明能力，不能在 JSON 内存 callable、对象地址、模块路径、SQL/Python 表达式或传输凭证。纯 authoring 仍只做结构校验；发布/读取检查本地可解析的精确声明，实例协商及执行适配器仍校验其各自的实际能力，发布成功不证明远程 Agent 在线。
- JSON 解码拒绝重复 key、未知字段/discriminator、未知 schema 版本、非有限数值、错误类型和超限内容；完整规范 JSON 的 UTF-8 大小首版不超过 1 MiB，发布和读取使用同一限制。规范化采用 UTF-8、排序对象 key、固定紧凑分隔符，保留所有有语义的数组顺序；对完整解析后声明编码，不以原始文本空白区分定义。
- `definition_digest = SHA256(canonical_json({schema_version, definition}))`，其中 definition 为严格 codec 输出；读时验证列中 ref 与 JSON ref 相同、digest 相同并重新执行领域验证。此 digest 覆盖整个产品；Plan 的 workflow_digest 只覆盖 DAG，二者不可混用。
- 并发发布依靠唯一索引和事务判定：同作用域、同 ref、同 schema 和规范定义的重放返回首次记录，保留原发布者和时间；同 ref 异内容明确冲突，不 UPDATE。重放仍先授权。数据库失败必须传播，不写内存 Catalog 后伪装成功。
- 首版只有已发布记录，不引入可变 draft/status/latest 行，也不提供 UPDATE/DELETE。修改完整配置必须发布新产品版本；修改 DAG 还必须更新 workflow 版本。历史行须保留供已有实例读取。若将来需要禁用新实例，需单独设计，不得删除运行实例依赖的定义。

### 9.4 数据库读取、Catalog 演进与历史迁移

新增 L6-owned `ProductProfileReader`，提供同步的精确版本 `get(product, version)` 和当前作用域的 `capabilities()`；DatabasePlugin 当前 ORM 路径亦为同步，不为本需求引入异步 API。数据库实现由注入的受信任 StoreAxes 提供器取得**每次调用**的 tenant/env，不把首个请求作用域固化在 injector 单例中，也不接收普通配置字段来覆盖作用域。ProfileRepository 的读、写、枚举及缓存键都必须包含这两个轴。

数据库发布记录是运行时产品定义来源，读出后还原为不可变 ProductProfileDefinition。ProductCatalog 可保留为实现同一读取合同的静态/测试适配器和纯计算所用的已解析快照；生产 DI 显式选择数据库实现，不合并两个来源、不按缺行回退到静态目录。纯 authoring 和纯校验函数不获得数据库依赖；需要纯 Catalog 快照的 helper，由上层先解析固定定义后传入快照。

读取链路：

```text
ProductInstantiate(profile_ref) / 已有 Collet.ProductInstance.profile_ref
  → 当前受信任 tenant/env 的 ProductProfileReader.get(精确 ref)
  → 校验 schema / 列与 JSON ref / digest / 领域声明 / 已安装 Method
  → 不可变 ProductProfileDefinition
  → 既有绑定、G1/G4、根 Task 合同解析及 Swarm RuntimeResolver / PolicyReader
```

不能只修改 Swarm resolver：现有 `binding.py`、`instance.py`、G4 产品合同、实例化、Method bootstrap/recovery、Open enrollment、L6 repository/records、具体产品服务及其 DI 均依赖具体 ProductCatalog。服务入口迁移为注入 reader，纯 helper 接收已解析不可变快照；capabilities 中的产品列表来自当前作用域数据库，Method/治理/执行能力仍来自实际安装声明，不能由 JSON 声称新增能力。

特别是 `ProductRecordDecoder.decode` 当前承诺“同一 L4 SQL 快照解码、无额外查找”。数据库化后要**显式演进 L6 decoder 的合同**：允许通过注入 reader 精确解析历史 Profile（可由 L6 repository 在已有事务中预取为快照），但不可再次查询 Collet/Task 或混入不同 L4 revision。L4 继续只调用下层既有 decoder port，不导入 L6/产品表；`ProductCollaborationPolicy` 和 G4 校验消费该次已解析定义，不在一次操作中选择不同产品版本。配置读取不会取得 Work 或 Method 的写权限。

首版不依赖缓存保证正确性，使用数据库精确读取；后续缓存必须以 tenant/env/ref/schema/digest 区分已验证不可变内容，产品枚举不得长期缓存漏掉新发布版本。数据库不可用、精确行缺失、旧 schema 不支持或 Method 版本不可用都显式失败；不切到最新版本、不降级成动态 planner、不在失败时偷偷读取代码种子。当前实例仅凭已有精确 profile_ref 即可引用不可变数据库版本，**不必新增 ProductInstance SQL 表/列或改写历史 JSON**。

迁移采用显式、可重放的公开种子导入，而不是业务请求自动建表/补数据：

1. 新建 L6 `persistence/profile_models.py` / `profile_repository.py`、存储 codec 与独立增量 DDL；遵守既有 ORM tenant guard、DatabasePlugin、MySQL/SQLite 类型约定。
2. 针对当前 `INSTALLED_PRODUCT_PROFILES` 生成/编码公开固定版本种子，逐个核对完整定义与 ref，包含仍被历史实例引用的旧产品版本；种子不携带实际租户、私有 Agent 或凭证。导入目标 tenant/env 由受信任运行参数提供，不假设只有默认租户。
3. 以每个目标作用域的一次导入事务插入缺失版本；已有同定义记录保持不变，任何同版本异内容或不可编码声明使事务回滚并报告冲突。不能覆盖数据库里合法发布的其他版本。
4. 切换 DI 前核验该作用域已有 ProductInstance/creation_facts 的全部 profile_ref 都能精确读取，检查编码前后语义相等、存储长度/图大小/Method 能力兼容；缺历史定义则阻止切换，不从“最新代码”猜造。
5. 完成 DDL/导入/回归后再将 TaskModule、持久化装配和产品适配器切换到数据库 reader；新版本实例只在对应发布记录提交后创建。独立本地环境和测试使用同一公开导入路径或明确隔离的静态适配器，不能依赖私有注册服务。
6. 发布新的 DAG 实例后，不能直接回滚到不认识 DB reader/新 policy 的旧程序并宣称兼容；回滚须保留能读取所引用历史版本及 codec 的程序，或显式阻止旧程序接管这些实例。数据库中的已发布版本不随应用回滚删除。

这有意演进当前 L6 README 中“没有 Catalog 数据库、安装需要改代码”的合同；实现时必须同步替换相关 owning knowledge 和消费者测试。当前提案仍未使该合同生效，不修改 README 来伪装已经完成数据库切换。

### 9.5 配置持久化不等于执行状态持久化

- Plan/Stage/Allocation 继续由现有内存 MethodStore 保存，仅扩展对象字段及复合操作；不把随机结果、节点状态、实际输入/输出 manifest、分配、租约或可变游标写回 ProductProfile。实际产物引用证据扩展既有事件 JSON，不另建 Artifact/交接表；正文由既有资源能力保存。
- 独立子 Task、事件、Artifact 引用继续使用现有 L4 存储；不建立第二套 workflow run/node run 权威。
- ProductInstance 继续在 Collet creation_facts 内固定产品选择；新增的是**可供多实例引用的产品定义表**，不是产品实例或运行表。

**Profile/DAG 在重启后可以重新读取，但进程重启仍可能丢失 Plan、Allocation 与随机选择结果，无法保证续跑。** 确定性 ID 不等于状态恢复；若发现 SQL 已存在对应子 Task 而 MethodStore 历史缺失，必须拒绝重新执行并报告状态丢失，不能以“编译相同 ID”冒充恢复。

如上线要求跨进程、多副本调度或重启恢复，需先完成 MethodStore 持久化与分布式派发幂等设计；新增 ProductProfile 表不能代替该前置能力。

### 9.6 传播范围

| 边界 | 变化与兼容措施 |
| --- | --- |
| Profile authoring / 发布 | 纯 authoring 不写库；新增授权发布 Service API、严格完整定义 codec 与单事务不可变插入；旧 SwarmMethodPolicy 和旧 workflow 语义不变。 |
| ProfileReader / Catalog / 全部消费者 | 从代码安装演进为作用域内数据库精确版本读取；显式迁移实例化、绑定、G4、Open、记录 decoder、产品服务及 DI，不新增全局默认来源。 |
| ORM / DDL / 种子 | 新增 1 张 ProductProfile 表及增量迁移、唯一索引和 tenant guard；现有表不加列，公开种子显式导入并核验历史引用。 |
| Swarm runtime DI | 按 policy 装配两种 planner；动态 capability 缺失仍报错，不隐式 fallback。 |
| L5 Service API / MethodStore | 新增 PolicyReader 依赖与 workflow 复合提交；现有调用方/测试替身需补齐绑定及协议实现，不以缺失依赖默认为无检查。 |
| Plan / Stage / 报告 codecs | 可选扩展字段缺失可读；旧模式序列化默认不增加字段。新模式的命名输入/输出、报告 manifest 与 refs 投影必须完整且一致；保留旧报告位置参数。 |
| G4 报告 / 事件证据 / complete_stage | 注入共享窄只读 EvidenceValidator；新模式校验输出合同/固定版本/内容并保存版本化 manifest，协调比较完整证据；直接完成 API 不可跳过子 Task/产物守卫。不放宽根 Task 事件白名单。 |
| L4 / Worker | 不读取产品表；子 Task 使用完整节点目标；executor 签名不变；报告、根 Task 生命周期不变。 |
| HTTP / OpenAPI | 不增加 Profile 配置、注册或 plan 路由；既有方法入口复用相同守卫。如 inspection 序列化新增字段，同步 DTO/契约测试；旧响应默认形态不变。 |
| 公共版 / 企业版 | 公共实现不能依赖私有 Agent 注册或传输。企业 Catalog 消费者、runtime resolver、policy 白名单、Store 实现和执行适配器需在实际宿主版本联测，不能假定兼容。 |

## 10. 验收标准

| ID | 必须满足的行为 |
| --- | --- |
| AC-01 | ProductProfile 可选择 SwarmWorkflowPolicy；原动态 SwarmMethodPolicy、无 policy、旧 WorkflowProfile 的语义不变。 |
| AC-02 | 链、fork/join、多入口/出口均可配置；重复节点、环、缺依赖、空/重复候选、非法契约、无效产物声明/输入来源和超限图在 authoring/发布/解码时拒绝。 |
| AC-03 | 首次 plan 给出完整 DAG 与入口 assignment；后续不修改图，依据实际 READY/SUCCEEDED 选择 next Stage。 |
| AC-04 | 节点只能从合法候选交集中均匀随机选一个；测试替身可覆盖每个候选，不用概率阈值作为不稳定 CI 断言。 |
| AC-05 | 身份类型参与匹配；未加入、被撤销、被产品范围或角色限制排除的 Agent 不可执行，候选为空不 fallback。 |
| AC-06 | 声明顺序决定同等 READY 节点顺序；join 等全部直接前驱成功，FAILED 前驱不能放行；首版在途 Stage 最多一个。 |
| AC-07 | Worker 与 SQL 子 Task 均收到完整节点 objective；输入仅为明确绑定的真实前驱固定版本产物加外部输入，命名端口保留，join 不丢任一声明分支。 |
| AC-08 | 重复 wake/query 不重抽签；两个竞争规划提议最多接纳一个 allocation，SQL CAS 失败不派发；已提交 assignment 不改人。 |
| AC-09 | 直接调用 Service API/现有 HTTP 管理入口不能绕过 workflow 图和 Agent 白名单，非法提议在子 Task/Worker 副作用前失败。 |
| AC-10 | 完成所有 Stage 后，Plan 完成仍校验子 Task、产物和分配；根 Task 保持独立提交/验收，G3 成功不能代替完成。 |
| AC-11 | 产品版本和 digest 固定；跨 Collet/租户/绑定不串图；缺失内存历史但已有 SQL 子 Task 时不重新执行。 |
| AC-12 | 真实 DI + SQLite + 本地 Worker 测试跑通 fork/join、随机替身选人和根验收；同时回归现有动态 Swarm / Open + Swarm。 |
| AC-13 | L4/L5 依赖门禁通过；有界大图不因旧 100 事件预算误报，异步报告仍需明确 wake；新增 1 张 ProductProfile 表，不修改现有表列，不建立 workflow/node/run 表。 |
| AC-14 | 完整 ProductProfile（含 DAG/候选/原根 workflow）经真实 SQLite 写入并在重建 reader 后等值读出；scope 隔离、digest/strict codec/Method 精确引用校验生效。 |
| AC-15 | 发布必须授权；同 ref 同内容重放保留首次审计，同 ref 异内容及并发竞争明确冲突；公开种子重复导入安全，历史版本缺失阻止切换且不回退代码。 |
| AC-16 | 所有 Catalog 消费者迁移覆盖；新版本只影响新实例，旧实例继续固定旧定义；缺行/DB 失败/未知 schema 不 fallback，配置恢复不被当作 MethodStore 恢复。 |
| AC-17 | 每节点声明必需命名 outputs；报告的名称、类型、数量、内容合同、固定版本及 refs 投影均校验通过才发布成功；只有 refs 的报告、未知/缺失/额外产物不得完成受管节点。 |
| AC-18 | inputs 显式选择直接前驱输出；纯控制依赖不泄露产物；来源/版本可追溯且命名映射冻结，下游访问拒绝不补授权、不换版本或重新抽人。 |
| AC-19 | 同 refs 但改端口/Resource revision 的部分报告重放被拒绝；SQL 存完整版本化 manifest；直接 complete_stage 或通用 Work 伪造 evidence 不能绕过共享验证器，旧动态报告/事件/根验收兼容且不新增产物表。 |

测试清单、实施顺序见 [plan.md](plan.md)；实际执行命令、通过/失败及未验收边界见 [delivery.md](delivery.md)。

## 11. 备选方案与取舍

| 方案 | 结论 |
| --- | --- |
| 直接让旧 ProductProfile.workflow 兼任 DAG | 不采用：混淆根 Work 状态契约与 Swarm Stage 结构，影响 G4 和既有产品。 |
| 扩展 SwarmMethodPolicy 为两个 optional 字段 | 不采用：会产生“双空/双有”非法状态，也弱化当前 plan_service_ref 必填合同；独立 policy 类型更明确。 |
| 在旧 planner 内 `if dag`，由 L5 读取 ProductProfile | 不采用：违反 L5 不依赖 L6 和配置驱动装配边界。 |
| 不声明产物、让下游理解聊天历史 | 不采用：无法稳定判断节点完成或校验交接，替换候选 Agent 后上下文也不可靠。 |
| 所有前驱输出无差别传给下游 | 不采用：控制依赖不等于数据依赖；命名 inputs 明确来源，避免歧义和不必要数据暴露。 |
| authoring 时随机选好所有 Agent | 不采用：参与资格在运行时变化，且声明不能产生实际分配权威。 |
| 每次 wake 都重新随机 | 不采用：破坏在途分配、报告者身份和子 Task executor 一致性。 |
| 继续仅靠代码安装 ProductProfile | 不采用：不满足完整产品配置及 workflow 落库的明确需求；代码仅保留可导入种子和已安装能力。 |
| 单独拆 workflow/node/edge 定义表 | 首版不采用：当前 DAG 有界且随整个产品版本原子发布，拆表增加多实体版本一致性成本。 |
| 引入独立 workflow 引擎和多张运行表 | 不采用：当前 Swarm 已有 DAG/Stage/Allocation，重复权威超出需求；ProductProfile 定义表不在此排除之列。 |
| 首版同时做并行、重试和运行态持久化 | 不纳入：需要另行演进调度/生命周期/事务边界；产品配置落库包含在本方案，不能混为一谈。 |

## 12. 交付状态与剩余边界

2026-11-23 已完成工作区代码实现，包含一张 ProductProfile 定义表、授权发布/导入/精确版本读取、配置式串行 DAG 规划、随机合法 Agent 分配，以及命名固定版本产物交接和直接 API 防绕过。规划器实际类型为 `WorkflowPlanningService`；其余实现位置及与初稿的差异见 [交付记录](delivery.md)。

最终功能、兼容性和架构目标组 **708 passed**；同范围广回归 **2458 passed、184 failed**，184 项失败用例与独立干净 HEAD 的基线集合完全相同，没有新增失败差集。完整 Backend CI 执行至 **6247 passed、10 skipped、1 failed** 后按既有 maxfail 停止；该 endpoint 失败也在干净 HEAD 单独复现。因此**不能宣称全量 CI 或 coverage gate 通过**。

公共默认发布权限 deny-all，受管产物内容验证 fail-closed。上线前必须执行 DDL、在可信作用域显式授权导入全部历史 Profile，并绑定真实不可变资源/内容/权限验证器及 Worker。MySQL/OceanBase、真实 Agent/A2A、企业宿主和部署均未验收；MethodStore 仍为内存，不支持重启续跑或多副本调度。

当前可复用合同已更新到 [协作域 README](../../src/agentclaw/community/core/task/context_collab/README.md)、[L5 README](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md#managed-swarm-workflow)、[L6 README](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md) 与 [Profile 存储及 cutover 指引](../../src/agentclaw/community/core/task/context_collab/l6_products/PROFILE-STORAGE.md)。本地实现不代表合入、宿主集成或上线。用户后续要求提交并推送，精确 commit 和远端结果以 Git 记录及执行回报为准；当前未创建 PR。
