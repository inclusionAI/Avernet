# Swarm Workflow 模式实施与验证计划

- 日期：2026-11-23
- 状态：P1–P5 已实现；P6 本地目标验收通过，广回归/完整 CI 有可复现基线失败，部署集成未验收。详见 [delivery.md](delivery.md)。
- 范围依据：[spec.md](spec.md)，尤其是 ProductProfile 整体落库/历史迁移、串行 DAG、参与资格，以及配置持久化不提供运行态恢复的范围。
- 下列切片保留设计阶段的任务划分；路径相对 `apps/backend/`。实际文件合并/命名差异及未完成的外部验收在第 4 节和交付记录中单列，不以计划条目代替验证事实。

## 1. 实施切片

### P1：领域定义与 authoring（AC-01、02、17、18）

- 新增 `src/agentclaw/community/core/task/context_collab/l5_methods/swarm_workflow.py`：不可变 Ref/Node/Definition、命名输出声明/输入绑定、严格 codec、拓扑及来源端口校验和规范化 digest。
- 在 `l5_methods/method_policies.py` 新增 SwarmWorkflowPolicy 并扩展 MethodPolicy；不修改 SwarmMethodPolicy 的必填字段和位置参数。
- 更新 `l6_products/validation.py`，验证 policy 与 required/optional Method 的类型匹配、图规模及候选 Agent 的 participant_scope。
- `ProductProfileCreateCommand` 不增加顶层 DAG 字段；保持旧 workflow 的事件契约选择逻辑。节点只配置 outputs，运行时 output_artifact_roles 从输出名投影，避免两份权威；首版所有输出/输入必需，数量范围及规模有界。
- 新增 `tests/community/core/task/context_collab/test_swarm_workflow_definition.py`、扩展现有 policy/Profile authoring 测试。
- 出口：配置错误在 authoring、发布和解码同样失败；不发生实例化、随机选人或运行时写入。

### P2：ProductProfile 持久化、版本读取与迁移（AC-13、14、15、16）

- 新增 L6 `profile_codec.py`、`profile_reader.py`（建议文件名），定义 schema v1、严格完整定义编码、policy/admission 白名单 discriminator、完整 inputs/outputs 声明、规范化 digest 和 ProductProfileReader 合同。MethodProfile 仅存精确 ref，核对代码安装声明，不可由 DB 重写方法能力。
- 新增 `l6_products/persistence/profile_models.py`、`profile_repository.py` 以及独立增量 DDL `sql/2026_11_23_context_collab_product_profile.sql`（均为拟新增路径）；只增 1 张表，补齐模型加载、tenant guard、唯一索引、MySQL/SQLite 类型及 ID 比较语义。
- 新增独立 `ProductProfilePublishCommand` / `ProductProfilePublicationServiceProtocol` 和发布实现（建议 `l6_products/operations/product_profile_publication.py`）；补充专门的发布授权合同/绑定，不以 Collet 创建权限代替，不改变纯 authoring 的无 I/O 语义。
- 实现同版本不可变插入、同内容幂等返回、异内容冲突、并发唯一性冲突归一化与首次审计保留；授权失败/数据库失败不写 Catalog 或返回假成功。
- Reader 在每次调用取得受信任 tenant/env；精确版本读取与 capabilities 枚举均受作用域约束。首版不依赖缓存，不在缺行/DB 失败时 fallback 代码、最新版本或动态 planner。
- 迁移所有当前具体 Catalog 消费者及 DI：实例化、binding/instance/G4、Method bootstrap/recovery、Open、L6 records/repository、产品服务和适配器。纯 helper 仍接收已解析快照；L6 decoder 的额外 Profile 查找须显式更新合同并保持原 L4 SQL revision，L4/L5 不获得产品存储依赖。
- 新增显式公开种子导入入口（建议 `l6_products/profile_seed.py`，通过授权发布/受信任部署上下文使用）；输入目标作用域，一次事务完成该作用域导入，同版本冲突回滚。核验历史引用后才允许切换 DI，不在业务请求中补数据。
- 新增 `tests/community/core/task/context_collab/test_product_profile_codec.py`、`test_product_profile_persistence.py`、`test_product_profile_publication.py`、`test_product_profile_seed.py`（均拟新增）；以真实 SQLite 验证写入和重建 reader 后读取，不能用 repository mock 声称持久化通过。
- 补充 reader/发布服务消费者契约测试、静态适配器与数据库适配器的共同语义测试；MySQL DDL/排序规则/唯一索引在可用目标数据库独立验证，不将 SQLite 等同 MySQL 验收。
- 出口：新库导入与已有历史库迁移均能从 DB 读回完整产品和 DAG，历史 ProductInstance JSON 不改形态；没有新增运行态持久恢复承诺。

### P3：配置式规划能力与作用域（AC-03、04、06）

- 新增协作域根部 `workflow_planning.py`，实现既有 SwarmPlanningService。
- 编译完整 Stage DAG、稳定 Plan/Stage/子 Task ID，并用现有层宽/深度计算保持 structural_violations 约束。
- 根据实际 Stage 状态选下一节点；注入随机选择 callable，不保留可变节点游标。
- 新增 `test_swarm_workflow_planning.py`：链、分支、汇合、多入口/出口、未完成与错误状态、稳定 ID、单候选及可控随机替身。
- 出口：同一请求的图结构和 ID 可重现；随机只影响尚未提交的 assignment；动态 planner 不受影响。

### P4：可信 policy 接缝与运行时装配（AC-05、09、11）

- 在 L5-owned 合同中新增窄 SwarmWorkflowPolicyReader，并提供与消费者行为对应的契约测试。
- 扩展 `di/modules/swarm_runtime.py`，从固定 ProductInstance/数据库 ProductProfileReader 解析 policy，构造 DAG planner 或原具名 planner。
- Reader 与 RuntimeResolver 共享固定产品解析，不能依赖全局目录或默认最新版本。
- 保持 coordinator/start_actor 语义；补齐 Store/Service 测试替身和公共 DI 绑定，不用 optional 缺省绕过检查。
- 扩展 `tests/community/di/modules/test_swarm_runtime.py`，新增 workflow 守卫测试。
- 出口：动态缺 capability 仍失败；DAG 无需外部 plan capability；直接 API 不可篡改受管图或候选名单。

### P5：运行对象、产物交接和分配一致性（AC-07、08、09、13、17、18、19）

- 扩展 `l5_methods/swarm_profile.py` 的 Plan/Stage 字段及 codec；新图算法和校验放独立文件，修改后检查源文件不超过 1,000 行。
- 同步内部 `SwarmPlanCandidateCommand` 和计划形态白名单，编译完成合同中的完整输出声明；明确动态默认字段省略、workflow 派生输入不污染初始图幂等比较。
- 在 `persistence/method_store.py` 及 `method_store_impl/swarm_slice.py` 增加 workflow 分配复合提交；在一次锁范围内处理预期状态/重复接纳/Stage 更新/Allocation 插入。
- 更新 `l5_methods/operations/swarm/plan.py`、`stage.py` 的图和候选守卫；复用 G4，不复制状态机。
- `SwarmDriver`/受管 allocate 操作在 allocation 接纳前按 inputs 精确选择前驱成功端口，核对来源和已接受 manifest，冻结命名输入与固定版本引用；纯控制依赖不传数据。命名绑定和来源快照一并纳入过期决策围栏/复合提交；提交后再创建/启动子 Task 和调用 Worker。
- 在 G4 `execution_report.py` 或同域证据值模块新增仅依赖 L4 Artifact 的 `ArtifactPortBinding` 与严格 codec；Stage 新增 input/output_bindings，StageExecutionReport/SwarmStageOutcomeCommand 追加 keyword-only output_bindings，保留旧报告位置参数与序列化形态。
- `SwarmStageReportService.start_task` 使用有效 objective；Worker execute/accepts 签名保持不变，但执行适配器必须支持命名 I/O、固定 Resource revision、内容合同与下游权限检查，不能把旧 accepts 的非空文件判断直接视为受管合规。
- 新增 L5-owned `SwarmArtifactEvidenceValidator` 窄只读端口（建议独立 `l5_methods/swarm_artifact_contracts.py`），由 DI 适配器复用执行器的内容验证逻辑；report/complete_stage 都注入并实际调用，不让验证端口取得派发或状态写权限。补齐消费者契约测试，覆盖正常和无效内容/伪造 envelope。
- 输出校验先于所有子 Task 提交/验收：端口、数量、类型、精确版本、真实资源与内容合同均满足才接纳；artifact_refs 只是 manifest 的严格一致投影。不依赖 Collet.artifacts 自动聚合。
- 既有子 Task submitted/accepted 事件 payload 增加版本化产物证据 envelope，同步受管事件 schema/白名单而不放宽根 standard Task 合同；部分报告协调比较完整命名 manifest，同 ID 换端口或 revision 也要拒绝。
- workflow complete_stage 的直接调用同样核验 done 子 Task 的完整 manifest/提交验收主体，并再次调用共享 validator 验证固定资源；不能信任通用 Work 入口自填的 verified/envelope；输出 bindings/refs 与 SUCCEEDED 同一 Stage 更新发布，未验收上传不得作为下游输入。记录 SQL/MethodStore/外部资源之间的实际非原子边界，不宣称 exactly-once。
- 下游资源读取按最终被选 Agent 校验当前权限；输入不可读/版本不可用显式失败，不重新抽签、不传正文绕过授权、不改 latest。
- 将 workflow drain 事件预算绑定至已验证节点数量，保留动态模式原限制。
- 新增 `test_swarm_workflow_dispatch.py`：竞争快照、复合写入失败、重复 wake、输入冻结、错误目标、派发失败及部分提交边界。另新增 `test_swarm_workflow_artifacts.py`，覆盖明确输出、真实内容校验、报告伪造/改版本、命名数据交接、纯控制依赖和撤权阻断。
- 出口：非法 DAG/执行者不产生子 Task 或 Worker 调用；不宣称 SQL 与 MethodStore/外部 I/O 原子。

### P6：集成、兼容与知识更新（AC-10、11、12、13、14、16、17、18、19）

- 新增 `tests/community/core/task/context_collab/products/test_swarm_workflow_end_to_end.py`。
- 用真实 DI/SQLite/G1/G4/MethodStore，Profile 经授权发布写入真实 ProductProfile 表再实例化；只有 Worker 计算和随机选择器使用本地替身。
- 跑通 prepare → {build, review} → finalize；验证根 Task + 4 个独立子 Task、显式命名输入/固定产物版本、实际内容读取和输出声明校验、固定执行者、所有分配释放、Plan 完成与根验收分离。
- 产物集成测试使用受控本地资源适配器和真实临时内容，验证不存在/错内容/错版本不能完成，接受后修改外部 latest 不改变下游固定输入；不能只 mock accepts=True。
- 覆盖两个候选都可被替身选中，不通过多次真随机频率判断测试是否通过。
- 回归现有动态 Swarm、Open + Swarm、root wake、旧 ProductProfile.workflow 与序列化兼容；覆盖当前全部 Catalog 消费者，验证旧实例固定旧版本，新版本只用于新实例。
- 增加缺失 MethodStore 历史但已有 SQL 子 Task 的拒绝测试，不把确定性 ID 当作恢复能力。
- 单独验证“重新构建 reader 可以读回配置”与“MethodStore 丢失不能续跑”，不能用前者替代后者。
- 处理本次补跑发现的 BBS 基线测试旧调用：`test_l6_product_e2e.py` 两例仍传入当前命令不支持的 `space`，在构造阶段失败；按当前合同迁移测试后重跑，不为过时调用恢复已移除字段或跳过业务断言。
- 实现后更新协作域/L5/L6 README、数据库 cutover/公开导入指引、涉及的 Service API/存储协议和实际 DTO 文档；说明替换 code-owned Catalog 合同的迁移路径，规格记录真实验证与剩余限制。

## 2. 测试矩阵

| 层面 | 必测案例 |
| --- | --- |
| Profile 存储 codec | 完整定义往返、原根 workflow 和 Open admission 子类型不丢失；未知 key/schema/discriminator、重复 JSON key、错误版本、超 1 MiB、ref/digest 不一致、Method 合同冒用均拒绝。 |
| Profile 发布 | 独立 G1 授权、同内容幂等保留首次审计、异内容/并发冲突、发布失败无假成功、不得原地覆盖或删除历史。 |
| Profile 读取/迁移 | 真实 SQLite 持久化/重建 reader、跨租户/环境隔离、同 injector 不串作用域、全部 Catalog 消费者、种子重复导入/冲突回滚/历史缺失、DB 故障无 fallback、新旧版本并存。 |
| 存储兼容 | ORM/增量 DDL 对齐、ID 长度/大小写规则、MySQL/SQLite 唯一性、历史 ProductInstance JSON 不变；目标 MySQL 验证单列状态，不以本地 SQLite 冒充。 |
| 图与类型 | 空图/超限、重复 ID、空/重复 Agent、类型错误、环、自环、缺依赖、多入口/出口、fork/join、严格未知字段拒绝。 |
| 规划 | 首次完整 candidate + assignment；后续 candidate=None；READY/依赖复核；顺序稳定；失败不跳过；全部成功才提议 finished。 |
| 身份与授权 | type+id 匹配、Participant 歧义、未参与、产品 allowlist、撤销 admission、既有 RoleBinding 约束、直接 API 绕过尝试。 |
| 随机语义 | 固定替身可选任一合法候选、单候选、不从非法集合抽取、查询/重复 wake/在途不抽签。 |
| 产物声明 | 输出必需/名称唯一、数量与类型/内容合同；输入来源端口存在且是直接前驱；输出角色派生；profile codec 往返无损。 |
| 报告与证据 | 缺失/额外/重名端口、重复 ID、错类型/数量/内容/revision、资源不存在；refs 与 manifest 不符；只给 refs 或自填 verified/envelope 不能完成新模式；直接完成重验真实资源内容；同 ID 改绑定/版本的部分重放拒绝。 |
| 输入与任务 | 完整 objective/输出声明进入 Worker 和子 Task；命名端口映射/汇合、固定版本、平铺去重不丢别名、纯控制依赖不传数据；外部引用不可漂移；调度器不复制正文，适配器按权限读取验证。 |
| 数据交接授权 | 跨 Collet/Plan/租户伪造来源拒绝；先固定随机执行者再检查可读性，撤权/不可读/版本失效不换人、不授予权限、不替换 latest；版本固定不代表永久授权。 |
| 并发与失败 | 两个提议竞争只接纳一个；复合提交冲突无部分写；SQL CAS 失败无派发；已提交 allocation 不改人；manifest/成功状态同次发布、SQL/Store 部分失败如实保留；派发失败显式暴露。 |
| 兼容与版本 | 原位置参数 policy、旧 workflow、旧 JSON/报告默认字段省略、keyword-only 扩展保留位置参数、optional Method 不启用、固定历史版本/digest、跨租户/Collet/绑定隔离。 |
| 生命周期 | 暂停/恢复、失败阻断、迟到/错误报告者、Plan 完成独立核验、根 Task 独立验收、内存历史丢失拒绝重跑。 |
| 有界执行 | 至少 60 个同步节点不触发旧 100 事件误报；上限图仍有预算保护；异步报告必须宿主显式 wake。 |
| 架构 | L4 不导入 L5/L6，L5 不导入 L6/products/DI；配置装配在 DI；新增窄端口有消费者契约覆盖。 |

## 3. 验证命令

以下命令与当前文件布局对齐。实际已执行范围、命令和结果见 [delivery.md](delivery.md#本地验收)。发布、种子导入和历史迁移测试实际合并在 `test_product_profile_persistence.py`，未单建同名 publication/seed 测试文件。

在 `apps/backend/` 下：

```bash
# 新增功能
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/context_collab/test_product_profile_codec.py \
  tests/community/core/task/context_collab/test_product_profile_persistence.py \
  tests/community/core/task/context_collab/test_swarm_workflow_*.py \
  tests/community/core/task/context_collab/products/test_swarm_workflow_end_to_end.py

# 已有兼容路径与架构
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/context_collab/test_swarm_driver_wake.py \
  tests/community/core/task/context_collab/test_swarm_stage_dependencies.py \
  tests/community/core/task/context_collab/test_l5_open_method_policies.py \
  tests/community/core/task/context_collab/test_l6_product_profile_operations.py \
  tests/community/core/task/context_collab/test_optional_product_profiles.py \
  tests/community/core/task/context_collab/test_l6_product_e2e.py \
  tests/community/di/modules/test_swarm_runtime.py \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end_swarm.py \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end_open_swarm.py \
  tests/community/architecture

# Backend 完整 CI；本地执行记录与既有失败见 delivery.md
bash scripts/ci_test.sh
```

需要推送/改 CI 时再按仓库 push validation 执行，不关闭 hooks 或既有 gates。无新增 HTTP 路由不代表可忽略被影响的现有 DTO/endpoint 契约测试。

## 4. 验证与交付记录

设计阶段的原始证据保留在 [research.md](research.md#本次实际验证)，实现后的实际结果另见 [delivery.md](delivery.md)。

| 切片 | 实际状态 |
| --- | --- |
| P1 | 已实现 DAG/命名端口值合同、严格 codec、typed policy 和 Product scope 校验。 |
| P2 | 已实现 SQL Reader/独立授权发布/事务导入/消费者迁移；显式导入 helper 位于 `di/modules/context_collab_products.py`，发布/导入测试合并在 persistence 文件。MySQL/OceanBase 目标库尚未验收。 |
| P3 | 已实现 `WorkflowPlanningService` 与全图编译、稳定 ID、串行 READY 调度和可注入 chooser。 |
| P4 | 已实现 pinned policy port/DI/直接 API 守卫；公共资源 validator 默认 fail-closed，生产 provider 由宿主绑定。 |
| P5 | 已实现命名 manifest、输入冻结、复合 allocation/completion CAS、真实内容验证端口、child 事件合同快照与部分报告协调。 |
| P6 | 83 条新增功能用例包含在最终 708 项全通过目标组中，含真实 SQLite/DI/本地内容文件 fork/join、60 节点和旧模式兼容。扩大回归有 184 条基线失败，完整 CI 被另一个基线 endpoint 失败阻断；不标记全量 CI/coverage 为完成。 |

P6 原计划中的两条 BBS 旧 `space` 调用没有在本次迁移中单独修复。扩展检查证实存在 184 条基线失败，涉及多个已变更的旧 API；本次保留并记录它们，不扩大为无关旧 API 迁移、不恢复已删除合同，也不通过 skip/xfail 隐藏失败。新功能、现有 Catalog 消费者接口迁移及本次引入问题已修复，当前与同范围干净基线的失败用例差集为空。

提交/推送按用户后续要求单独执行，结果以 Git 记录为准；尚未合入、未部署。配置持久化测试不代表 MethodStore 重启恢复，临时文件 Worker 不代表真实远程 Agent/A2A 验收。
