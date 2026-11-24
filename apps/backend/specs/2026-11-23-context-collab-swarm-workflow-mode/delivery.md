# Swarm Workflow 实现与本地验收记录

- 日期：2026-11-23。
- 范围：公共 Backend `core/task/context_collab`，依据 [spec.md](spec.md) 和 [plan.md](plan.md)。
- 实现基线：`04a8f4af78bc79d1db2f7e65320b0465828c2433`。
- 状态：工作区代码已实现，本地目标验收通过；广回归/完整 CI 有可复现基线失败。**尚未合入、未做宿主集成或部署。**
- 提交/推送：按用户要求单独执行，精确 commit 与远端结果以 Git 记录和执行回报为准；不由本地验收推断远端成功。
- 设计阶段的原始观察和验证保留在 [research.md](research.md)。

## 实现落点

以下链接均指向本次工作区源码，不表示其已在其他分支或环境生效。

| 能力 | 当前实现 |
| --- | --- |
| DAG 定义及命名 I/O | [swarm_workflow.py](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_workflow.py)：不可变 workflow/node/output/input、类型/规模/环/端口来源校验、规范化 digest。配置在 required Swarm MethodTemplate 的 `SwarmWorkflowPolicy`，不改变原根 Task 的 `definition.workflow`。 |
| ProductProfile 版本存储 | [profile_models.py](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/profile_models.py)、[profile_repository.py](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/profile_repository.py)、[增量 DDL](../../src/agentclaw/community/core/task/context_collab/sql/2026_11_23_context_collab_product_profile.sql)：新增 **1 张** `ac_context_collab_product_profile` 表，一行存完整定义和 DAG，不单拆 workflow/node/edge/Artifact 表。模块源码定义共 5 张表，未查询部署数据库。 |
| 严格序列化 | [profile_codec.py](../../src/agentclaw/community/core/task/context_collab/l6_products/profile_codec.py)、[value_codec.py](../../src/agentclaw/community/core/task/context_collab/l5_methods/value_codec.py)：schema v1、闭合类型白名单、规范 JSON/digest、未知字段/重复 key/非法类型/超限拒绝；MethodProfile 仅存精确已安装引用，不允许 DB 重写代码能力。 |
| 授权发布与导入 | [发布合同](../../src/agentclaw/community/core/task/context_collab/l6_products/profile_publication.py)、[发布实现](../../src/agentclaw/community/core/task/context_collab/l6_products/operations/product_profile_publication.py)、[独立发布授权](../../src/agentclaw/community/core/task/context_collab/g1_secure_govern/profile_publication.py)：同版本同内容幂等且保留首次审计，异内容冲突；事务批量导入并核验历史引用，不在业务请求中自动 seed。 |
| 精确版本读取 | [ProductProfileReader](../../src/agentclaw/community/core/task/context_collab/l6_products/profile_reader.py) 迁移所有原 Catalog 消费者，生产 DI 使用 SQL。每次解析可信 tenant/env，无缺行/latest/DB 故障时的代码 fallback。旧 ProductInstance JSON 继续固定原 profile_ref。 |
| DAG 规划 | [WorkflowPlanningService](../../src/agentclaw/community/core/task/context_collab/workflow_planning.py)、[workflow_compilation.py](../../src/agentclaw/community/core/task/context_collab/l5_methods/workflow_compilation.py)：首次返回完整 DAG 和首个 assignment，之后按声明顺序选 READY 节点；均匀随机选择配置候选与当前合法参与者的交集，可注入 chooser。保留原动态规划 policy。 |
| 分配与输入冻结 | [workflow_guards.py](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/workflow_guards.py)、[Stage 操作](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/stage.py)、[复合 Store 操作](../../src/agentclaw/community/core/task/context_collab/persistence/method_store_impl/swarm_slice.py)：固定图/节点/候选/I/O 守卫，针对完整 Plan/Stage 快照原子接纳一个分配及命名输入，防止直接 API 绕过；完成时原子发布输出/SUCCEEDED/释放 allocation。 |
| 产物证据及交接 | [Artifact manifest](../../src/agentclaw/community/core/task/context_collab/g4_contracts_consistency/artifact_manifest.py)、[报告服务](../../src/agentclaw/community/core/task/context_collab/l5_methods/operations/swarm/report.py)、[只读验证端口](../../src/agentclaw/community/core/task/context_collab/l5_methods/swarm_workflow_ports.py)：校验必需输出、数量/类型/固定 revision/实际内容/refs 投影；SQL child `started` 保存节点和输入合同快照，`submitted/accepted` 保存完整 manifest。直接完成重新检查 done child、主体和实际资源。后继仅消费显式绑定的成功端口，纯控制边不传产物。 |
| DI、兼容与预算 | [swarm_runtime.py](../../src/agentclaw/community/di/modules/task/swarm_runtime.py)：从固定实例选择配置规划器或原动态能力，受管图使用有界节点预算；重复在途 wake 不重抽/重派，旧根 Task/报告位置参数/动态 Swarm/Open + Swarm 路径保持兼容。 |

### 相对初稿的实现细节

- 实际 planner 名称是 `WorkflowPlanningService`，初稿名称为 `WorkflowSwarmPlanningService`；两者指同一个新增配置式规划实现，不是两套引擎。
- Plan 的 workflow ref/digest 使用既有 `termination_condition` 保存；Stage 新增不可变节点、objective/digest 和命名 bindings。没有增加运行表或现有表列。
- 精确 Profile 读取是独立 outbound port；发布的 command/audit/Protocol 位于纯合同文件，与 SQL 发布实现分离。
- 显式公开种子导入 helper 位于 [di/modules/task/context_collab_products.py](../../src/agentclaw/community/di/modules/task/context_collab_products.py)，不是单独的 `profile_seed.py`。发布、导入及迁移测试合并在 persistence 测试中。
- `DesignatedOpenAdmission` 值类迁至 L5 publication，原 commission 路径 identity-reexport；因此完整定义 codec 不需要 L6 反向依赖具体产品。
- 并发原子性测试直接竞争 MethodStore 复合 CAS；不使用共享单连接 SQLite 模拟独立事务并发。Profile 并发发布另以真实多连接 SQLite 验证。
- 收尾补强了受管 allocation 的幂等边界：相同请求的并发重放返回首次记录；在途/暂停/租约已过时的查询式重放不重新分配或续租，同 key 改租约仍拒绝，当前 G1 仍必须授权。Driver 的受管分配 ID 按 Stage/attempt 派生，动态分配保持原语义。
- 修正现有两个参数化测试的 xdist 收集不稳定：对 frozenset 参数排序、为动态时间参数固定测试 ID；未减弱业务断言或关闭 gate。

## 本地验收

测试均从 `apps/backend/` 执行，使用当前锁定环境 `uv run --no-sync` 和 `DEPLOY_PROFILE=test`。没有连接真实 Agent、外部数据库或私有服务。

| 检查 | 实际结果与边界 |
| --- | --- |
| 最终新增功能 + 兼容 + 架构目标组 | **708 passed，43 warnings，136.27s**。包含全部 83 条新增功能用例、既有动态 Swarm/Open + Swarm、相关 DI 和完整 architecture 目录。 |
| 最终受影响广回归 | **2458 passed、184 failed，43 warnings，184.27s**。未 skip/xfail 已知失败。 |
| 干净 HEAD 同范围基线 | **2375 passed、184 failed，44 warnings，114.60s**。独立临时 worktree、同 venv、显式基线 PYTHONPATH；对比完整失败 nodeid 集合，**新增失败 0、消失失败 0**。184 项主要涉及旧 API 调用和旧 record 形态，不作为本次无关 API 迁移修复。 |
| Backend 完整 CI 入口 | **6247 passed、10 skipped、1 failed，102 warnings，97.31s** 后按既有 maxfail 停止；未完成剩余套件或 coverage gate，不报告全绿。 |
| CI 首个失败基线核验 | 干净 HEAD 单独运行完全相同 nodeid，**1 failed，18 warnings，0.96s**，重现相同 endpoint 404。 |
| 补充 Profile/Method HTTP 检查 | codec + persistence + Method HTTP 文件共 **48 passed、2 failed，18 warnings，2.77s**。两个 HTTP 失败在干净 HEAD 同文件重跑均复现（**18 passed、2 failed，19 warnings，1.65s**）；与上述广回归范围不同，单独记录，不计入那 184 项。 |

43 条目标组 warnings 为现有依赖弃用和源码 `SyntaxWarning` 等；测试通过不等于 warning 已消除。

### 新增测试覆盖

- [定义校验](../../tests/community/core/task/context_collab/test_swarm_workflow_definition.py)、[规划](../../tests/community/core/task/context_collab/test_swarm_workflow_planning.py)：DAG、命名来源/规模/候选、稳定 ID、可控随机、READY/失败/在途规则。
- [Profile codec](../../tests/community/core/task/context_collab/test_product_profile_codec.py)、[真实持久化](../../tests/community/core/task/context_collab/test_product_profile_persistence.py)：完整往返、reader 重建、独立授权、幂等审计、版本冲突、事务回滚/历史缺失、作用域和大小写隔离、真实并发发布、DB 故障无回退。
- [分配守卫](../../tests/community/core/task/context_collab/test_swarm_workflow_dispatch.py)、[产物负向测试](../../tests/community/core/task/context_collab/test_swarm_workflow_artifacts.py)：篡改图/候选/I/O、复合 CAS 竞争及同请求重放、在途/暂停/租约过期的幂等结果、缺失/非法 manifest、真实资源不存在/错误内容/错误 revision、伪造 evidence/直接完成、部分 SQL 提交重放和同 ID 改版本、撤权不换人。
- [本地端到端](../../tests/community/core/task/context_collab/products/test_swarm_workflow_end_to_end.py)：真实 DI/SQLite/G1/G4/MethodStore，授权发布后再实例化；prepare → {build, review} → finalize，4 个 child + root Task、命名输入实际读取、控制边无数据、固定候选/产物、分配释放、根验收分离、60 节点链、重复在途 wake、内存历史丢失拒绝重派。
- [测试资源与 Worker](../../tests/community/core/task/context_collab/swarm_workflow_support.py) 读写真实临时内容地址化文件，校验 revision/内容并能撤权；这是本地消费者契约证据，不是生产资源服务或远程计算验收。

### 最终目标组命令

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/context_collab/test_swarm_workflow_definition.py \
  tests/community/core/task/context_collab/test_product_profile_codec.py \
  tests/community/core/task/context_collab/test_product_profile_persistence.py \
  tests/community/core/task/context_collab/test_swarm_workflow_planning.py \
  tests/community/core/task/context_collab/test_swarm_workflow_artifacts.py \
  tests/community/core/task/context_collab/test_swarm_workflow_dispatch.py \
  tests/community/core/task/context_collab/products/test_swarm_workflow_end_to_end.py \
  tests/community/core/task/context_collab/test_swarm_driver_wake.py \
  tests/community/core/task/context_collab/test_swarm_stage_dependencies.py \
  tests/community/core/task/context_collab/test_l5_open_method_policies.py \
  tests/community/core/task/context_collab/test_l6_product_profile_operations.py \
  tests/community/core/task/context_collab/test_optional_product_profiles.py \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end_swarm.py \
  tests/community/core/task/context_collab/products/test_task_commission_end_to_end_open_swarm.py \
  tests/community/core/task/context_collab/products/test_task_commission_method_topology.py \
  tests/community/core/task/context_collab/products/test_task_commission_protocol_integration.py \
  tests/community/di/modules/test_swarm_runtime.py \
  tests/community/architecture
```

### 受影响广回归命令

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/context_collab \
  tests/community/di/modules/test_swarm_runtime.py \
  tests/community/di/test_l4_collab_service.py \
  tests/community/repository/task/context_collab \
  tests/community/architecture
```

相同命令在干净基线中执行时，使用现有 venv 的 `python -m pytest`，且 `PYTHONPATH` 仅包含基线 checkout 的 `src` 和 Backend 根目录，避免误测修改后的源码。原计划中的两条 BBS 旧 `space` 调用仍在这 184 条失败之内；未恢复删除的合同或跳过它们。

### 完整 CI 及其阻断

```bash
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=4 \
  bash scripts/ci_test.sh --base HEAD
```

使用已安装锁定依赖；没有改 CI 脚本、coverage 阈值或排除列表。失败 nodeid 是：

```text
tests/community/endpoints/test_endpoint_runner.py::test_endpoint_injection[POST /api/v1/collaboration/task/context_collab :: create_happy]
```

该用例 fake creation 返回 `collet_case_001` 后，查询 events 没有对应记录，实际 404、断言期望 200。在干净 HEAD 单独复现相同失败，说明此阻断先于本次变更；**不能由此推断未执行的其他完整 CI 用例都没有问题**。

### 补充 HTTP 兼容检查

在最后一次仅调整 import 位置后，另执行 `DEPLOY_PROFILE=test uv run --no-sync pytest -q tests/community/core/task/context_collab/test_product_profile_codec.py tests/community/core/task/context_collab/test_product_profile_persistence.py tests/community/endpoints/test_context_collab_methods_endpoints.py`。30 条 Profile 用例通过，Method HTTP 为 18 passed、2 failed。

[Method HTTP 测试](../../tests/community/endpoints/test_context_collab_methods_endpoints.py) 的以下两项失败分别期待裸 Swarm 和旧不兼容组合返回 422，但当前及干净 HEAD 都返回 200；该范围两个失败 nodeid 集合完全相同，没有新增失败。本次没有更改相应创建规则或弱化旧断言：

```text
TestMethodBindingsDelivery::test_bare_top_level_method_kind_rejected_through_http
TestMethodBindingsDelivery::test_incompatible_product_composition_maps_422
```

基线只执行同一个 Method HTTP 文件（其余两个 Profile 文件为本次新增），仍使用基线源码路径和当前 venv；未把补充 HTTP 组报告为全通过。

## 上线前置与未验证事项

1. 在目标数据库验证并应用增量 DDL。当前只有真实 SQLite 验收，MySQL/OceanBase 的 DDL、排序规则和事务未实际执行。
2. 在每个目标可信 tenant/env 显式配置发布授权并导入全部批准的历史定义，检查旧实例引用后再切换流量。公共默认发布权限 **deny-all**；`create_product_profile` 只是纯 authoring，不自动持久化。
3. 宿主显式绑定真实 Worker 和只读 `SwarmArtifactEvidenceValidator`，支持声明的 I/O/完成/内容合同、不可变 revision 证明及选中 Agent 的当前读取权限。默认验证器 **fail-closed**，不能以 `accepts=True`、非空路径或 grant_ref 代替真实校验。
4. 本次 DAG 是串行调度。没有并行执行、自动重试/换人、隐式资源授权、根 Task 自动验收或新的 Profile HTTP 管理接口。
5. Profile 定义已经可以重新读取，但 MethodStore 仍为进程内状态。分配和完成的复合 CAS 不等于跨 SQL/MethodStore/资源系统事务；没有重启续跑、多副本调度或 exactly-once 承诺。
6. 未验证真实 Agent/A2A、企业宿主、部署或远端 CI。完整 Backend CI 及 coverage 尚未通过，应在处理基线问题后再执行正式合入门禁。

## 已更新的 owning knowledge

- [协作域 README](../../src/agentclaw/community/core/task/context_collab/README.md)：数据库定义来源、分层导航及迁移入口。
- [L5 README](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md#managed-swarm-workflow)：受管 DAG、复合提交、命名产物与端口语义。
- [L6 README](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md)：SQL Reader/纯 authoring/发布边界。
- [Profile 存储与 cutover](../../src/agentclaw/community/core/task/context_collab/l6_products/PROFILE-STORAGE.md)：显式导入、精确历史版本、失败诊断及真实资源 provider 要求。

## 收尾静态检查

- 80 个新增/修改 Python 文件的 Ruff format 检查及 `E9,F63,F7,F82` 检查通过；22 个新增 Python 文件的默认 Ruff 规则检查通过。
- 所有 80 个新增/修改 Python 文件均不超过 1,000 行，最大为 `di/modules/task_module.py` 的 983 行。
- 9 个 Markdown 文件的 486 个本地链接/标题锚点、11 个 Python 示例块 AST、5 个 shell 块 `bash -n`、围栏/空白/末尾换行检查通过。示例只检查语法，不声称已运行或完成类型验收。
- `git diff --check` 通过。独立基线临时 worktree 已清理；测试日志/数据库/凭证未加入版本文件。

静态检查不替代上述运行测试、完整 CI gate 或部署验收。
