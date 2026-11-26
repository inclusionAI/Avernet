# Context Collab R3：依据、源码证据与修订记录

- 日期：2026-11-26；目标基线 `f181311410`，`feat/REL20261127_bbs_1`。
- 用途：支撑 [R3 改造提案](spec.md)，不替代当前契约，也不是新对象/新服务已经实现的记录。R1/R2 的读取、选择与验证作为历史保留；ProductProfile 的最新字段要求见第 8 节。

## 1. 输入读取

R1 实际通过已授权客户端读取用户指定的《协议层核心接口及领域对象》，当时元数据更新时间为 2026-11-26 07:48:36 UTC（北京时间 15:48:36）。R2 本轮重新读取文档和更新后的架构图，元数据更新时间为 2026-11-26 09:30:33 UTC（北京时间 17:30:33）；正文仍是一张图，确认查询方法已标为 query_collet_rollout。

两次原图及放大裁片只在仓库外临时目录读取；不将内部地址/原图文件/凭据写入仓库。R2 按用户要求在方案中写出对象字段及通用身份的 YAML 示例，不复制内部资源定位。

图示明确表达了：模板/Profile、Collet、ColletRollout/StageRollout、候选与依赖、产物及验收结果、结果上报、全部轮次查询、平台 Bot 干预及会话摘要。

图示没有完整定义：鉴权、幂等、租约与版本冲突、数据库恢复、外部取消、运行成功事件、Action 的结构和执行策略。这些是本次提出的补充设计，不应归为原图已经规定的行为。

## 2. 当前源码证据

以下链接均已实际读取。行号用于记录本基线位置，不承诺后续版本保持不变。“对方案的影响”保留 R2 判断；其中 ProductProfile 三字段的取舍已由第 8 节 R3 替换。R1 被替换的选择另见第 6 节，不把旧建议冒充当前目标。

| 编号 | 证据与当前事实 | 对方案的影响 |
| --- | --- | --- |
| E1 | [Profile 构建](../../src/agentclaw/community/core/task/context_collab/l6_products/operations/product_profile.py) `create_product_profile` 为纯函数；[发布实现](../../src/agentclaw/community/core/task/context_collab/l6_products/operations/product_profile_publication.py) 另行授权发布；[存储指南](../../src/agentclaw/community/core/task/context_collab/l6_products/PROFILE-STORAGE.md) 定义精确版本与显式导入。 | R2 明确替换为 ProductProfile(id/name/stages YAML)，服务 create 受权持久化；旧纯构建/发布调用链须显式迁移，不能漏掉原发布授权。 |
| E2 | [Swarm models](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_profile.py) 中 Plan、Stage、Allocation、ExecutionRecord 分别在约 349、429、560、748 行定义；[修订构建](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_replanning.py) 使用前驱 Plan、触发 Event 与复用 Task/Stage。 | 可复用执行算法，但 R2 要求独立的整数 ID ColletRollout/StageRollout 领域对象与持久化映射，不再只给旧 Plan/Stage 做查询别名。 |
| E3 | [内部执行控制合同](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_execution_contracts.py) 提供 Step/InteractionCommand、ignore/replan_all/replan_from；[执行体](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/service.py) 已有 Event journal、重放与版本检查。 | “人工反馈触发重规划”不是零基础；补 stop、结构化提案及持久化，不重建第二个 planner。 |
| E4 | [报告合同](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/execution_report.py) 只有 scope、Stage/Allocation 和产物；[报告服务](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/report.py) 校验 live 分配/身份/lease、非空产物和 accepts，再提交/验收子 Task 并完成 Stage。 | 没有图示 ProgressEvent 的完整失败/逐条件结果字段；当前 SQL 部分提交可对账不等于通用报告幂等回执。已完成后重报也不能被假定为成功。 |
| E5 | [MethodStore 骨架](../../src/agentclaw/community/core/task/context_collab/persistence/method_store_impl/base.py) 约 25–53 行持有绑定、Open、计划、阶段、分配等 dict；[TaskModule](../../src/agentclaw/community/di/modules/task_module.py) 386 行绑定 InMemoryMethodStore。 | 运行恢复缺口包括治理/录取前提，而不只是 Stage 状态丢失。 |
| E6 | [执行读取](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_execution_reads.py) 已返回当前图、plans、archived_stages、messages；[会话辅助](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_execution_state.py) 构造 `swarm:{binding}:{stage}`。 | R2 的 session_id 必须来自真实执行；query_stage_rollout_details 不得用逻辑协作事件/摘要充当真实会话明细。 |
| E7 | [执行 DI](../../src/agentclaw/community/di/modules/task/swarm_execution.py) 默认 ConservativeInteractionPlanner；[运行 DI](../../src/agentclaw/community/di/modules/task/swarm_runtime.py) 约 216–233 行绑定 unavailable executor/evidence validator、空动态规划能力。 | 公共默认不等于已经接通真实 Worker、真实内容校验或智能调整。不能为展示效果改成 permissive success。 |
| E8 | [HTTP](../../src/agentclaw/community/adapters/http/task/context_collab_swarm_execution.py) 中 stages/interactions 使用 `asyncio.to_thread`，step 在 async handler 中直接调用同步 service；[执行适配合同](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_driver_contracts.py) 要求 execute 尽快返回。 | 文档“阻塞调用均 offload”的描述与 step 源码不完全一致；需补事件循环保护、锁外 IO/快照校验。未执行负载测试，不能量化影响。 |
| E9 | [L4 Artifact](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/artifact.py) 只保存 ResourceBinding；[命名 manifest](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/artifact_manifest.py) 拒绝 latest/head 等可变 revision；[Task](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/task.py) 54–55 行明确 artifacts 尚未由 Work/report 填充或聚合。 | R2 将 Artifact 正式改为 type/name/content 及三类内容对象，显式替换当前引用 shape；底层资源/版本权限仍由 adapter 和内部证据守卫。 |
| E10 | [G4 Method 状态机](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/method_state_machines.py) 已有 Stage.fail/Plan.terminate/Allocation.revoke；当前 Stage.cancel 没有 PENDING 入边。 | 停止须受控并声明历史 pending 的展示；不能直接把全部 Stage 改 CANCELLED。 |
| E11 | [产品实例化](../../src/agentclaw/community/core/task/context_collab/l6_products/operations/product_instantiation.py) 明确 L4 创建、产品选择与 MethodStore 写入为分离事务，失败可能返回可恢复的已提交 Collet。 | 创建也需幂等/未完成状态，不只关注执行阶段。不能称现有组合流程全原子。 |
| E12 | [当前执行指南](../../src/agentclaw/community/core/task/context_collab/l5_methods/SWARM-EXECUTION.md) 链接的 `test_swarm_execution_api.py`、`test_swarm_execution_endpoints.py`、`2026-11-23-swarm-execution-interactions/spec.md` 在本分支不存在。旧 [DAG 交付记录](../2026-11-23-context-collab-swarm-workflow-mode/delivery.md) 的若干专项文件亦不存在；仅有本机 pycache 不能算可复现测试。 | 优先补回/重写有当前合同依据的专项测试与导航；历史 708 passed 不得冒充本分支今日验证结果。 |

## 3. 已执行验证

从 `apps/backend/` 使用现有虚拟环境执行，没有安装依赖、修改测试或访问外部服务：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest -q \
  tests/community/core/task/context_collab/test_method_store.py \
  tests/community/core/task/context_collab/test_layer_operations.py \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end_open_swarm.py \
  tests/community/architecture/test_context_collab_l4_dependencies.py \
  tests/community/architecture/test_context_collab_l5_dependencies.py \
  tests/community/architecture/test_context_collab_compatibility_imports.py
```

R1 实际结果：**48 passed、17 warnings、1.36s**。本轮未重新执行该组，也没有把结果归入 R2 新对象/服务验收。告警来自现有 Pydantic class Config / json_encoders 弃用。

覆盖的是：MethodStore 基础行为、层内操作边界/兼容导出、现存 Open+Swarm 本地旅程，以及三个相关架构检查。该旅程使用本地能力，不证明真实 Bot/LLM 或跨进程调度。

## 4. 失败、缺失及尚未验证

- 直接网页读取未获得正文；通过授权客户端取得图片并读取，不把“能访问元数据”当作读完原图。
- 图像下载首次在受限网络中 DNS 解析失败，经显式审批后下载成功；没有向公开仓库写内部资源。
- E12 的指定文件路径确实缺失；未编造执行结果，也未从 pycache 推断版本对应关系。未逐一审计所有历史链接，不能由此断言全部 Swarm 测试均不存在。
- 没有运行完整 Backend CI、覆盖率门禁、全部 task/context_collab/repository/HTTP 回归或目标数据库 DDL。
- 没有验证多实例竞争、真实运行会话、远程取消、外部消息通知、真实产物资源服务或企业宿主。
- 没有证据表明当前分支代码就是部署版本；也没有给出当前吞吐、恢复时间或错误率指标。因此不承诺定量性能收益或工期。

## 5. R1 文档交付核对（历史）

R1 新增 spec/research/plan 并更新 Backend specs 导航；不改变现行契约、源码、状态机或 ADR。已核对三份文档中的 40 个相对文件链接全部存在，spec/research/plan 均可从 Backend specs 导航发现；未包含原始内部地址、原图定位或机器专属绝对路径。`git diff --check` 通过；新建文件另检查了尾空格、末尾换行和围栏配对。未修改历史验收记录；对其中缺失引用只在本研究中记录本基线观察。


## 6. R2 用户要求与初稿选择的替换

| R1 选择（不再采用） | R2 目标（历史，ProductProfile 以 R3 为准） |
| --- | --- |
| 保留 ProductProfileDefinition 与现有组合字段作为产品定义 | 删除旧类型，ProductProfile 只含 id/name/stages；YAML 为配置源，相关职责和调用链迁移。此三字段限制已由 R3 保留 composition 的要求替换。 |
| Collet 保持原领域成员，图示对象只作视图 | Collet 正式改为 id/profile_id/content/status；安全/版本/作用域数据进入内部记录。 |
| Rollout 直接复用字符串 Plan/Stage ID，仅增加查询语义 | 新增 ColletRollout/StageRollout 正式对象及整数 ID；复用执行链时显式映射。 |
| Artifact 仍只含 ResourceBinding，File/Link/Text 仅作展示 | Artifact 采用图示 type/name/content；新增 ArtifactContent 及三个正式子类。 |
| 报告继续扩展既有成功报告，自行增加多种事件枚举 | 使用 ProgressEvent/AcceptanceResult 图示字段及三个 EventTypeEnum；并发/幂等信息留在可信入口上下文/回执。 |
| 所有能力仍只从 TaskCommission facade 进入 | L6 新 ProductProfileService 作为正式入口，显式 Service API Protocol 与 DI。 |
| 会话摘要是主要新增查询能力 | query_stage_rollout_details 根据已保存真实 session_id 查消息明细；摘要至多内部辅助，不替代消息。 |

R1 的底层可靠性观察仍可复用，但不能以“复用”为由取消上述已明确对象/接口调整。R2 是待实施的破坏性合同迁移提案，不改写当前 README 中仍生效的契约。

### 6.1 本轮补读的源码证据

- E13：[l6_products/profiles.py](../../src/agentclaw/community/core/task/context_collab/l6_products/profiles.py) 的 ProductProfile 目前是字符串枚举，而不是图示领域对象。实现时须处理同名冲突并迁移所有 key 消费方，不能只把 Definition 改名后留下两种 ProductProfile。
- E14：[会话端口](../../src/agentclaw/community/core/task/task_runner/client/ports.py) 中 OpenApiBotPort 与 BcsClientPort 均有 async get_session_messages；前者有 limit、bot_id、lifecycle_stage，后者另有 BCS 的时间/消息过滤及 caller 身份参数。不是一个现成的统一分页合同。
- E15：[Bot 消息 adapter](../../src/agentclaw/community/core/task/task_runner/client/open_api_bot_adapter.py) 的 get_session_messages 读取 BaaS 会话消息，支持 limit 和实际 Bot/lifecycle 路由，文档也保留了排序/方向联调限制。可作新服务的出站实现候选，但本轮没有连接真实 session 验证。

### 6.2 本轮读取与验证边界

- 重新读取了更新后的原图及放大字段/YAML。受限环境的文档客户端首次无法取得授权头，经显式审批后读取与图片下载成功。
- 本轮编辑范围仅为 spec.md、plan.md、research.md 及 Backend specs 导航；没有新增 product_profile_service 运行模块、修改模型/方法或执行 DDL。
- 新类型、共享枚举及五方法草图只在 Markdown 中定义；未把它们当作 Python 实现或契约测试已经交付。
- R1 的旧实现 48 passed 保留为历史基线；本轮不重新运行旧 suite 充当新服务验证，也不声称真实会话、重启或远程消息查询已验证。
- 文档检查结果在完成后记录于下一节；运行时、新增消费者合同、完整 CI/coverage、目标数据库及真实宿主集成仍待后续实施验证。


## 7. R2 文档检查结果

- 三份方案文档的 **43 个相对文件链接**均存在，spec/plan/research 均可从 Backend specs 导航找到。
- 对 Markdown Python 草图做 AST 检查：**12 个对象的精确字段集合**、三个内容子类的继承关系、三组枚举及**恰好五个 async 服务方法**符合本轮目标。图中验收成功枚举的实际拼写是 `ACCEPTANCE_SUCCEED`，已放大原图核对，不擅自改成 SUCCEEDED。
- YAML 示例通过安全解析；顶层/阶段/候选字段符合图示，覆盖 acceptance 单 mapping 与列表，依赖引用存在。
- 末尾换行、尾空格、代码围栏配对、内部地址/原图定位/机器专属路径检查均通过；`git diff --check` 通过。新文件未被 tracked diff 覆盖的格式也单独检查。
- 这些是**文档及类型草图检查**，不是新模型、五方法实现、真实会话读取或完整运行回归通过的证据。


## 8. R3：ProductProfile 保留 MethodCompositionTemplate

用户在 R2 基础上明确要求 ProductProfile 仍需要 MethodCompositionTemplate。本轮将 `composition: MethodCompositionTemplate` 作为必填成员保留；当前目标为 `ProductProfile(id, name, composition, stages)`。不恢复 ProductProfileDefinition，也不撤回 stages 使用 YAML、其他图示对象或 L6 五方法的要求。

### 8.1 本轮读取的源码证据

- E16：[L6 contracts](../../src/agentclaw/community/core/task/context_collab/l6_products/contracts.py) 已定义 MethodCompositionTemplate(required_methods, optional_methods, relations)，旧 ProductProfileDefinition 持有该类型。其合同要求由 required MethodProfiles 决定 Method kinds，optional methods 不隐式选中；MethodTemplate 没有 policy 时不隐式获得 Open 录取或自主 Swarm 能力。本轮复用该类型和语义，不发明另一种同义模板。
- E17：[L5 Method policies](../../src/agentclaw/community/core/task/context_collab/l5_methods/method_policies.py) 的 SwarmWorkflowPolicy 当前包含 typed workflow，与 stages YAML 存在重复图配置的迁移风险。R3 提案以 YAML 为阶段图唯一配置源，策略使用内部编译结果；提取/适配及冲突拒绝须在实现中验证，不能声称当前源码已支持。
- 补读现有 Profile 创建及产品实例化：当前纯构建和独立提交边界仍如 E1/E11，R3 仅同步目标方案，不把草图当作现有原子存储或服务实现。

### 8.2 同步的方案决策与验证边界

- spec/plan 同步 ProductProfile 四字段、必填 create_product_profile 参数、composition 与 YAML 的联合校验/完整往返/幂等，以及按组合初始化 Method 绑定、按 YAML 创建阶段的职责。
- Method relations 与阶段 depends_on 是不同层级，不要求两种边相同；不从 YAML 反推组合、不隐式启用 optional methods、不静默覆盖重复 DAG。
- 更新方案导航为 R3；保留第 3/5/7 节的实际历史验证。仅修改方案文件及导航，不实现运行代码，不重跑旧 suite 冒充新能力验收，未重新读取外部原图。


## 9. R3 文档检查结果

- 使用现有 Backend 虚拟环境对 Python 草图做 AST 检查：12 个领域对象的精确字段及类型、三个内容子类继承关系、三组枚举、恰好五个 async 服务方法均通过；ProductProfile 为四字段，create_product_profile 的 composition 参数必填且无默认值。
- 对照当前源码确认复用的 MethodCompositionTemplate 成员仍是 required_methods/optional_methods/relations；未复制或修改运行类型。
- YAML 示例安全解析及字段检查通过，同时覆盖 acceptance mapping/list，阶段依赖引用存在且无环。
- 三份方案文档的 46 个相对文件链接存在，均可从 Backend specs 导航找到；格式、围栏、私有地址/本机路径扫描及当前 spec 的旧三字段表述清理检查通过。`git diff --check` 通过，未跟踪的新文档也已单独检查格式。
- 以上仅为方案文档、示例及类型草图验证；未运行新模型/服务实现测试、真实会话查询、数据库迁移或完整 CI，也未将 R1 的 48 passed 记作 R3 验收。


## 10. 开发阶段后继记录

2026-11-26 用户随后授权按 R3 完成开发与测试。新的运行代码、当前验收结果、实施时修复的失败和全量迁移/外部验证限制单独记录于 [delivery.md](delivery.md)。第 1–9 节保留方案阶段的实际读取及实验，不把其旧基线改写成新实现验收；E12 中的历史缺失链接也不由本机 pycache 补证。
