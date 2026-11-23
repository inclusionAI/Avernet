# Swarm Workflow 设计：基线与源码证据

- 日期：2026-11-23
- 基线：`04a8f4af78bc79d1db2f7e65320b0465828c2433`。
- 范围：仅本地公共 Backend 源码、当前 owning README 与相关测试；未读取私有运行态、未连接外部数据库/Agent。
- 本文保留设计阶段的基线观察和原始验证；下文“当前/尚未实现”均指该设计阶段。实现后的源码、命令、结果和剩余边界见 [delivery.md](delivery.md)，不以历史观察替代当前 owning 合同。

## 1. 现有扩展点

| 证据 | 观察与设计影响 |
| --- | --- |
| [L6 contracts](../../src/agentclaw/community/core/task/context_collab/l6_products/contracts.py) 的 WorkflowProfile / ProductProfileDefinition | 顶层 workflow 持有 Task state_machine_ref/actions，不是 DAG。应保留语义，通过 Swarm MethodTemplate.policy 声明新配置。 |
| [L5 policy](../../src/agentclaw/community/core/task/context_collab/l5_methods/method_policies.py) | 当前 SwarmMethodPolicy 仅有必填 plan_service_ref。独立新增 workflow policy 可保留现有必填合同与位置参数。 |
| [authoring](../../src/agentclaw/community/core/task/context_collab/l6_products/operations/product_profile.py)、[validation](../../src/agentclaw/community/core/task/context_collab/l6_products/validation.py)、[Catalog](../../src/agentclaw/community/core/task/context_collab/l6_products/catalog.py) | authoring 是纯操作，安装共享验证；产品定义为 code-owned 固定版本，不需要另建图注册中心。 |
| [统一规划接口](../../src/agentclaw/community/core/task/context_collab/swarm_planning.py) | PlanningRequest 已携带 Stage 状态、输出引用和参与事实；PlanningDecision 同时带完整初始候选和一个 StageAssignment。新增规划实现可复用接口。 |
| [runtime resolver](../../src/agentclaw/community/di/modules/swarm_runtime.py) | 当前按固定产品选择 plan_service_ref，且明确区分 coordinator 与 start_actor。新 policy 的实现选择应在这里，而不是 L5 反查产品。 |
| [Driver](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_driver.py) | 已有独立子 Task 要求、过期决策快照、READY/role 检查和统一分配链路。RUNNING/ALLOCATING 时不继续规划，当前是串行派发。 |

## 2. 不能忽略的现状限制

1. [Stage / 图校验](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_profile.py) 已有 dependency_stage_refs、层深/层宽和环检查；DAG 不应再拥有一份平行执行状态。
2. 同文件的 structural_violations 会将最大层宽与 plan.max_parallelism 比较。保留串行 Driver 时，也不能把分支 DAG 的 max_parallelism 直接填 1。
3. Stage 当前没有独立 objective，[start_task](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/report.py) 把 Stage.name 用作子 Task 目标；新增配置需要把完整目标贯穿到 Worker 和子 Task。
4. 当前 input_artifact_refs 在初始 Stage 中固定，通用 Driver 没有运行时汇集前驱实际输出的逻辑。[现有计算替身](../../tests/community/core/task/context_collab/products/swarm_execution_support.py) 依赖已知工作区文件，不能证明通用 DAG 已能传递真实输入引用。
5. [allocate_stage](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/stage.py) 对 Stage 更新与 Allocation 插入执行分开的 Store 调用；[Swarm Store](../../src/agentclaw/community/core/task/context_collab/persistence/method_store_impl/swarm_slice.py) 的单次锁不覆盖整个服务操作。新模式需要有界复合提交，不夸大现有原子性。
6. Driver.drain 当前最多处理 100 次事件；完整大 DAG 需要明确预算，不能只放开配置节点上限。
7. [参与事实](../../src/agentclaw/community/core/task/context_collab/l6_products/participation.py) 来自显式 participant_refs/角色绑定；配置候选不自动授予参与权。[角色匹配](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/matching.py) 是只读校验，不是录取/分配。
8. [完成操作](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/plan.py) 已要求有产物、独立 done 子 Task 且无 ACTIVE allocation。planner.finished 只是提议，不能代替完成权威，更不能完成根验收。
9. [MethodStore](../../src/agentclaw/community/core/task/context_collab/persistence/method_store_impl/base.py) 仍为进程内字典，且 SQL/Store/外部派发无共同事务。新增配置式规划以及后续补充的 Profile 配置持久化，仍不能声称获得执行态持久恢复或 exactly-once。

## 3. 回归证据入口

- [依赖就绪](../../tests/community/core/task/context_collab/test_swarm_stage_dependencies.py)：AND 汇合、暂停后投影、失败前驱阻断。
- [Driver wake](../../tests/community/core/task/context_collab/test_swarm_driver_wake.py)：构造不启动、可信 start_actor、重复 wake 合并和启动错误。
- [policy](../../tests/community/core/task/context_collab/test_l5_open_method_policies.py) 与 [Profile authoring](../../tests/community/core/task/context_collab/test_l6_product_profile_operations.py)：类型、不可变配置及旧调用合同。
- [runtime DI](../../tests/community/di/modules/test_swarm_runtime.py)：产品所选规划能力和根启动身份。
- [Swarm 集成](../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end_swarm.py) 与 [Open + Swarm](../../tests/community/core/task/context_collab/products/test_task_commission_end_to_end_open_swarm.py)：真实本地 DI/SQL/G1/G4，规划和计算为替身，不代表真实远程执行。
- [L5 依赖 gate](../../tests/community/architecture/test_context_collab_l5_dependencies.py)：禁止导入 L6、具体产品和旧 application facade；新定义/操作必须保持该边界。

## 4. ProductProfile 落库补充（2026-11-23）

用户进一步明确：ProductProfile 应有独立数据库表，包括本次 workflow 配置。初稿沿用当前 code-owned Catalog 的“新增 0 表”前提，该**设计取舍已被替换**；现方案改为新增 1 张完整产品定义版本表。以下证据区分现状与拟议演进，不声称数据库已经存在。

| 补充证据 | 当前事实及修订影响 |
| --- | --- |
| [Catalog](../../src/agentclaw/community/core/task/context_collab/l6_products/catalog.py)、[DI 安装](../../src/agentclaw/community/di/modules/context_collab_products.py)、[L6 当前合同](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md) | 当前来源是不可变代码定义，无数据库/动态注册。新方案是有意演进，不是发现现有配置已落库；实现时必须更新合同与全部消费者。 |
| [L4 ORM](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/persistence/models.py)、[Telemetry ORM](../../src/agentclaw/community/core/task/context_collab/g3_observe_evaluate_evolve/persistence.py)、[SQL](../../src/agentclaw/community/core/task/context_collab/sql/) | 当前源码/DDL 共定义 Collet、Event、Task、Telemetry 4 张表，未发现 ProductProfile 表；拟新增后为 5 张。此为源码定义计数，不是对部署数据库的查询。 |
| [L5 MethodProfile](../../src/agentclaw/community/core/task/context_collab/l5_methods/method_profile.py)、[Open publication](../../src/agentclaw/community/core/task/context_collab/l5_methods/publication.py) | MethodProfile 是代码声明的完整能力合同，Open admission 有不同 dataclass 子类型。存储 codec 需精确 MethodRef 和显式策略 discriminator，不能靠 asdict/JSON 自动恢复类型，更不能把 DB 配置当作执行代码。 |
| [ProductInstance](../../src/agentclaw/community/core/task/context_collab/l6_products/instance.py) | 已在 Collet.creation_facts 保存精确 profile_ref，decoder 比较完整 JSON 形态；本次复用引用即可，不必新建实例表或修改历史 JSON。精确历史定义必须显式导入并保留。 |
| [records decoder](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/records.py)、[L6 repository](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/repository.py)、[G4 产品合同](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/product_contract.py) | 这些同步路径当前直接消费 Catalog，decoder 还有“无额外查找”的明确合同。数据库读取不能只改 Swarm resolver；要演进 L6 精确定义读取/快照边界，同时不重读或混用 L4 SQL revision。 |
| [G1 authorization](../../src/agentclaw/community/core/task/context_collab/g1_secure_govern/authorization.py) | 现有 Collet 创建/Work 权限不等于发布产品定义权限；拟新增内部发布入口需要专门授权和消费者测试，不因没有 HTTP 路由而省略。 |

修订结论：完整 ProductProfile（含 Swarm policy 内 DAG）一版本一行原子发布，以 tenant/env/ref 唯一；不另拆 workflow/node/edge，不写入执行游标/分配。ProfileRepository/Reader、严格 codec、公开种子导入、历史引用核验及 Catalog 全消费者迁移纳入实施计划。MethodStore 仍为内存，定义可重读不等于执行可恢复。

初始观察表中“code-owned 固定版本、不需要图注册中心”是基线事实与原设计输入；其中“无需独立图注册中心”仍保留，但“产品定义仅代码所有”的原方案不再适用。

## 5. 节点产物与交接补充（2026-11-23）

本次用户进一步询问每节点是否需要明确产物、节点之间是否应以产物交接。方案修订为：每节点声明必需命名 outputs，后继通过 inputs 显式选择直接前驱端口；业务数据通过已验证的固定版本 Artifact 交接。原方案“合并所有直接前驱输出”的设计被替换为显式绑定，depends_on 仍只表达控制/AND 依赖；聊天和调度元数据不作为业务产物替代品。

| 源码/测试证据 | 当前事实与设计影响 |
| --- | --- |
| [L4 Artifact](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/artifact.py)、[ResourceRef](../../src/agentclaw/community/core/task/context_collab/l3_spaces_resources/space_resource_refs.py) | Artifact 只有 ID 和 ResourceBinding，资源正文在外部；通用 ResourceRef.revision 可空。新模式可复用该值对象，但需额外要求固定不可变 revision，不改变其他场景。产物角色、内容合同、来源端口不是当前 Artifact 已有字段。 |
| [Collet](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/collet.py)、[Artifact 测试](../../tests/community/core/task/context_collab/test_collet_artifacts.py) | Collet.artifacts 当前为 model-only 能力，报告/Task 不自动聚合。不能把已有模型当作已实现资源发布/产物索引，拟议交接复用报告和已有事件证据，不依赖自动聚合。 |
| [Stage](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_profile.py)、[报告值](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/execution_report.py)、[完成命令](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/contracts.py) | 当前角色和产物 refs 是字符串元组，无命名端口→具体版本的 typed 映射。仅靠 output_artifact_roles 或 refs 非空不能保证预期产物完整。需新增 named manifest，旧 refs 保留为投影、旧动态报告继续兼容。 |
| [报告服务](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/report.py)、[执行端口](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_driver_contracts.py) | report 在任何子 Task 写入前调用 executor.accepts，当前部分协调只比较 artifacts 列表；新增 manifest 可复用验证时序，但内容/版本校验与完整证据比较是待实现增量，execute/accepts 签名不变不代表旧实现已满足。 |
| [complete_stage](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/stage.py) | 当前直接完成只要求非空完成条件、有效分配与状态等，不解析业务产物内容。受管模式必须新增已接受子 Task manifest 守卫，不能只加强 report 却保留可绕过的完成入口。 |
| [G4 artifact guard](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/state_machine.py)、[标准 Task 合同](../../src/agentclaw/community/core/task/context_collab/l6_products/task_contracts.py) | 既有 required_artifacts_present 只要求非空 list；根 standard_task@1 固定其 actions/payload_fields。应单独演进受管子 Task 的版本化证据 envelope/schema，不声称现有 guard 已校验类型/数量，也不放宽根合同。 |
| [现有执行替身](../../tests/community/core/task/context_collab/products/swarm_execution_support.py) | 本地 accepts 会核验预定文件/测试结果，说明内容验证可由适配器承担，但依赖预定工作区和 opaque URI，不证明通用固定版本资源解析/命名交接已具备。 |

产物声明仍存同一 ProductProfile JSON；本次设计不额外增加 Artifact/交接表，实际内容继续由资源能力保存。完整 manifest 在既有事件 JSON 保留，Stage 发布/输入冻结仍属于内存 MethodStore，不能据此宣称运行态已持久恢复。

## 本次实际验证

- 已完成：源码、README、规范模板及架构约束阅读；确认方案涉及的现有文件和接口。
- 已执行基线回归：`DEPLOY_PROFILE=test uv run --no-sync pytest -q`，覆盖依赖就绪、Driver wake、Method policy、Profile authoring、runtime DI、L5 架构边界六个现有测试文件，结果 **124 passed，17 warnings（既有 Pydantic 弃用提示）**。这些结果不验证拟新增工作流功能。
- 已执行现有动态 Swarm 与 Open + Swarm 两个本地集成测试文件，结果 **2 passed，17 warnings（同类既有 Pydantic 弃用提示）**。规划/计算是已有本地替身，不代表真实远程 Agent 验证。
- 初稿已完成文档检查：4 个变更 Markdown 文件的 **208 个本地链接/标题锚点**、代码围栏配对、4 个 Python 示例块的语法检查通过；示例未做运行或类型验收。`git diff --check` 与新增文档空白/换行检查通过。该计数为本次落库修订前结果；修订后的检查另列，不覆盖初稿证据。
- 落库方案修订后补跑：`DEPLOY_PROFILE=test uv run --no-sync pytest -q tests/community/core/task/context_collab/test_optional_product_profiles.py tests/community/core/task/context_collab/test_l6_product_e2e.py`，结果 **35 passed、2 failed、17 warnings**（同类既有弃用提示）。2 个失败均在 [BBS E2E](../../tests/community/core/task/context_collab/test_l6_product_e2e.py) 的 `_create_command` 构造阶段：仍传 `space`，但当前 [CollaborationCreateCommand](../../src/agentclaw/community/core/task/context_collab/l6_products/service_api.py) 要求 `to_space`，不接受该参数，抛出 `TypeError`，尚未执行其业务断言。运行时代码未改，作为当前基线合同/测试不同步问题记录；本次未修复，不将此组回归报告为全通过。
- Profile 落库修订后的文档检查：4 个 Markdown 文件，**223 个本地链接/标题锚点**、4 个 Python 示例的 AST 语法、1 个 shell 代码块的 `bash -n`、围栏配对、空白/末尾换行检查通过；示例没有运行/类型验收。`git diff --check` 通过。
- 产物交接方案修订后补跑 4 个现有文件：`test_collet_artifacts.py`、`test_swarm_stage_dependencies.py`、`products/test_task_commission_end_to_end_swarm.py`、`products/test_task_commission_end_to_end_open_swarm.py`（均位于 `tests/community/core/task/context_collab/`），使用 `DEPLOY_PROFILE=test uv run --no-sync pytest -q`，结果 **41 passed、17 warnings**（既有 Pydantic 弃用提示）。它们验证现有 Artifact 值对象、依赖及旧报告链路，不验收拟新增命名产物/固定版本交接功能；此前 2 条 BBS 基线失败未在本轮修复或重跑。
- 产物交接修订后的文档检查：4 个 Markdown 文件的 **236 个本地链接/标题锚点**、5 个 Python 示例 AST、1 个 shell 块的 `bash -n`、围栏/空白/末尾换行检查通过；示例未运行或类型验收。`git diff --check` 通过。
- 未执行：新功能测试（尚未实现）、Backend 全量 CI、真实 Agent/A2A、企业宿主集成、生产 DDL 或部署。
- 本次变更仅方案文档及 Backend specs 导航；不把 proposed 设计写入 owning README 充当现行合同。实现后按知识标准再更新 owning knowledge。

## 6. 实现与扩展验证（2026-11-23）

已按用户后续要求实现代码与本地验收。最终目标组 708 passed；受影响广回归 2458 passed、184 failed。将失败 nodeid 与同一基线的独立干净工作树逐项比较，新增/消失集合均为空。完整 Backend CI 也已实际执行，但被可在干净基线重现的 create_happy endpoint 404 阻断；未通过完整 CI 或 coverage gate。

实现定位、可复现命令、测试可信边界、数据库 cutover 和真实资源 provider 要求统一记录在 [delivery.md](delivery.md)。第 1–5 节及“本次实际验证”保留为设计阶段证据，未删改其中先前已执行的结果。该验收时点尚无提交/PR/合入/宿主集成/部署证据；后续提交/推送以 Git 记录和执行回报为准，不改写以上验证事实。
