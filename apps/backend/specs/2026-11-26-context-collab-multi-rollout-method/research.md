# L5 MultiRollout：依据、现状与方案阶段验证

- 日期：2026-11-26。
- 仓库基线：`3857a08d9300f7950978c2ba3fc516b2b5f80ff9`，分支 `feat/REL20261127_bbs_1`。
- 本文支撑 [spec](spec.md) 和 [plan](plan.md)，不将目标方案写成当前事实。

## 1. 用户目标及 ACCS 阅读边界

用户希望新增 L5 method：可嵌套 Swarm，支持 N 次独立并发后选优；或一次执行/验收，失败后借助人工反馈或自主诊断调整策略继续执行。

本轮通过网页读取工具访问用户给定 ACCS 文档未取得正文；普通公开 GET 跟随跳转后到达文档访问入口，
返回的是入口页面而非协议。没有读取/搜取本机认证材料，也未绕过访问控制。
不在公开仓库新增内部地址、原文副本、cookie、响应日志或本机路径。

以下引用已有 ACCS 对齐 spec 仅说明**仓库已有的术语与历史裁决**，不证明已核实语雀当前文本：

- [ACCS 按需采用版](../2026-11-09-bbs-context-collaboration-protocol/spec.md)：L1-L6/G1-G4 的对象/权限/证据边界。
- [L5 Method 对象](../2026-11-12-context-collab-l5-method-objects/spec.md)：Profile、Binding、组合矩阵的历史选择；其 draft/内存阶段与当前 durable v2 必须区分。
- [最近对象改造](../2026-11-26-context-collab-protocol-optimization/spec.md)及[交付记录](../2026-11-26-context-collab-protocol-optimization/delivery.md)：整数 ID、YAML stages、五方法、durable execution 及后续 UUID 幂等变更。

## 2. 实际读取的当前证据

| 证据 | 当前事实 | 对本方案的约束 |
| --- | --- | --- |
| [L5 README](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md) | durable rollout 与旧 process-local Method 路径并存；L5 禁止依赖 L6 | 新执行只能有一份 SQL 权威；不能把内存 Swarm state 当新候选数据库 |
| [MethodKind](../../src/agentclaw/community/core/task/context_collab/l5_methods/method_binding.py)、[Profile](../../src/agentclaw/community/core/task/context_collab/l5_methods/method_profile.py) | 当前恰为四种基础方法，固定 @0.2；产品组合 wire 单独处理 | MultiRollout 是显式本地扩展，不是当前已有标准；须更新精确版本/能力协商 |
| [组合矩阵](../../src/agentclaw/community/core/task/context_collab/l5_methods/method_composition.py) | PARALLELIZE 仅 Group/Swarm 同 kind；Open 不做父方法 | 用 MultiRollout NEST body，不伪装 MultiRollout PARALLELIZE Swarm |
| [L6 composition](../../src/agentclaw/community/core/task/context_collab/l6_products/contracts.py) | required/optional templates，关系按 method kind 引用 | 一份 body 模板实例化多个独立运行；不是写 N 份 required Swarm |
| [配置校验](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/configuration.py) | 精确 Profile/policy 校验，当前要求恰一个 SwarmStagesPolicy | 新根、策略和 adapter 要真正在发布/实例化时解析，不能只新增枚举 |
| [YAML 编译](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/stages_yaml.py) | 每节点只有 name/candidates/depends_on/acceptance，1..100 stages；不是任意 Agent plan | 新 stage 指令必须投影于受版本控制 RunPlan，不能声称现有 YAML 已支持 |
| [RuntimeSnapshot/ports](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_contracts.py) | 一个 current_rollout_id、一份 specs、共享 revision，提案不允许产生副作用 | N 个候选需明确成员、独立 plan 与版本；提案与权威提交分离 |
| [RolloutEngine](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_engine.py) | advance 串行一步；progress 仅 current stages；已保存分配才派发，未知投递不盲重试 | 需重构显式候选作用域、保留 dispatch fence；并发不只改为 gather |
| [lifecycle](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_lifecycle.py) | 当前 stage aggregate 直接投影 Collet；局部重跑复制合格证据但不复制 session | 多候选必须改总聚合；复用受输入等价和失效闭包约束 |
| [worker](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_worker.py) | intent revision 不匹配直接 superseded；宿主显式调度 | 兄弟写入会使其它候选有效 intent 被吃掉，必须改作用域有效性规则 |
| [SQL repo](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/rollout_repository.py) | scoped SQL transaction/CAS、回执、身份、intents；一份完整 snapshot | 延续原子性；新记录首版留同聚合，不轻率引入跨候选事务 |
| [snapshot codec](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/snapshot_codec.py) | 闭合字段、stage ID 全局唯一、所有 rollout graph 与同一 specs 一致、12 MiB 上限 | 新版本 decoder 必须校验各 RunPlan 及候选关系；不能随意补字段读取 |
| [五方法实现](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/service.py)、[合同](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/contracts.py) | 无 context/request_id；interactive UUID+内部 CAS；新干预限 current；instruction 不可改冻结图 | 保持签名及幂等；多候选 focus/有效集合需明确；不把自由反馈直接转配置写入 |
| [业务状态](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/product_status.py) | 六个 Status、三个 progress event、四个调整类型；stop 独立事实 | 等待/选优/调整放控制 metadata，不滥加 Work 业务状态 |
| [interactive 测试](../../tests/community/core/task/context_collab/product_profile_service/test_interactive_idempotency.py) | 真实多连接 SQLite 下重放、UUID 冲突、CAS 竞争、回执失败回滚、身份隔离 | 新策略须延续这些保证，而非只测 Agent happy path |
| [执行测试](../../tests/community/core/task/context_collab/product_profile_service/test_execution.py)、[精确字段测试](../../tests/community/core/task/context_collab/product_profile_service/test_models_yaml.py) | 当前独立历史/真实消息/验收及精确业务字段被测试锁定 | 首版尽量保持领域字段，用明确的新控制元数据扩展查询 |
| [L5 架构 gate](../../tests/community/architecture/test_context_collab_l5_dependencies.py) | 拒绝 L6/products 及 TYPE_CHECKING 反向依赖 | 新策略/运行 ports 不能为方便直接引用 ProductProfile 或具体产品 |

## 3. 选项与取舍

- **仅给 Swarm 加重试参数**：不能控制 Group/Single，也混淆 stage 并行与多次方法执行，拒绝。
- **在 L6 五方法外围 for/gather**：预算/进度/幂等变成分散实现，重启无统一候选权威，拒绝。
- **每候选新建 Collet**：隔离较直观，但会复制产品授权/root 上下文，现有互动/查询跨多个聚合事务；本轮不选。
- **每候选沿用 ColletRollout，另加 controller**：复用整数身份、stage 消息与验收；代价是显式扩展 current 展示语义、decoder 和聚合，选用。
- **首版每候选独立 SQL 行**：吞吐更好，但引入预算/选择/停止/回执跨聚合一致性；N≤32 时先保留单 CAS 行，后续凭容量证据再拆。
- **Agent 直接改已发布 stages**：违背 immutable Profile 和唯一 DAG 作者源，拒绝；首版只允许受限的运行计划 overlay。
- **无限自动直到成功**：有费用、权限、无进展和不可逆副作用风险，拒绝；硬预算/时限与策略失败必须可观测。

## 4. 本轮验证及未验证事项

本轮只编辑 Markdown 与 specs 导航，没有运行新功能实现或数据库迁移，也未 commit/push。
实际文档检查（使用 Backend 虚拟环境的 Python、ast、PyYAML）：

- 三份新文档及新增导航条目共 **43 个相对链接**目标存在。
- **2 个 Python 片段**通过 AST 语法检查；interactive 的参数 AST 与当前实现逐项一致，未重新加入 context/request_id/expected_revision。
- **2 份 YAML policy 示例**安全解析通过；检查字段判别、必需限额、整数/范围、并发与 samples/max_rollouts 关系及反馈超时。
- A1–A12 验收场景编号齐备；Markdown 围栏、末尾换行、尾空格及新增内容的内部 endpoint/凭证模式检查通过。
- `git diff --check` 通过；另外检查未跟踪的三份文档，避免只检查 tracked diff 漏掉新文件。
- 检查 HEAD 仍为上述基线，tracked 变更仅为 specs 导航，新增文件仅为本方案三份 Markdown；没有变更业务源码、DB 或 DI。

这些是**文档与示例一致性检查**，不是 policy 已能通过当前运行时创建校验、并发功能/数据库测试或 Backend CI 通过的证据。
仍未验证：ACCS 当前原文、目标 DTO 的 Python 实现、候选并发执行/隔离、Agent 策略、SQL 新版本迁移、
新旧 reader 兼容、MySQL、真实 Provider/Bot、企业 binding、远端取消与部署。不将旧提交 CI 结果当本提案证据。

## 5. 命名修订：明确多次执行语义

根据用户反馈，将拟议方法统一命名为 **MultiRollout@1.0**，涵盖并发采样和串行反馈迭代，而不是单次执行。
同步精确 Profile/枚举、配置示例、MultiRolloutMethodPolicy/相关新类型、multi_rollout 能力命名空间、
multi_rollout_control 查询元数据、实施计划和导航。既有 ColletRollout、StageRollout、RolloutEngine、
RolloutInteractionPort/RolloutExecutionPort 等单次执行对象与端口保持原名，运行代码未修改。

命名扫描发现传播表遗漏了一处旧查询元数据名，修正后重新执行检查：43 个相对链接、2 个 Python 片段及 2 份 YAML 示例通过；
确认方法声明/示例/组合关系使用同一 MultiRollout 名称，interactive 参数仍与当前实现一致；`git diff --check` 通过。
仍是 spec 阶段，没有运行功能测试，也未 commit/push。

## 6. 组合修订：MethodCompositionTemplate 是唯一作者源

用户明确组合必须沿用 MethodCompositionTemplate：由 required_methods 声明 MultiRollout 和 Swarm，
relations 的 MultiRollout → Swarm NEST 边表示前者嵌套后者。初稿在 policy 中另写执行体方法名，
形成重复的组合作者源，本次撤回该字段和两份 YAML 示例中的方法名/执行体名配置对。

- spec 先给出完整 MethodCompositionTemplate，再展示仅含 strategy/limits/isolation_ref 的 policy 参数。
- MultiRollout 和 Swarm 各自 policy 留在自己的 MethodTemplate；policy 内部固定 method 标识仅做类型匹配，不描述组合。
- L6 校验/实例化关系；L5 从有效 MethodCompositionEdge 解析子方法，不导入 L6，也不根据 policy 或列表顺序猜关系。
- 补充缺失/多个 NEST 目标、错误方向、未选择端点及重复选择字段的拒绝测试计划；运行记录的 Binding/边/digest 仅是由组合派生的冻结事实。
- spec、plan 和导航已同步；先前检查记录保留，本节替代初稿的重复组合描述。

修订后实际复检：43 个相对链接、2 个 Python AST 片段及 2 份 YAML 参数示例通过；
额外解析组合示例 AST，确认恰为 MultiRollout@1.0 与 Swarm@0.2，以及明确的 MultiRollout → Swarm NEST 边，
并对照当前源码核对三个 Template 的字段集合。YAML 不含方法名/执行体名选择字段；interactive 签名仍与当前实现一致。
`git diff --check` 通过；业务源码、DB、DI 和 HEAD 未改变，未运行功能测试，未 commit/push。

## 7. 端点命名修订：source_method / target_method

用户指出关系不一定是父子关系，因此将现有 MethodRelationTemplate 的字段重命名为
source_method、target_method，具体语义仍由 relation 决定；NEST 的 source 包含 target，
其它关系不因字段名自动获得包含语义。MultiRollout 自身仍为提案，未实现多候选执行。

已完成的独立修改：

- 合同、两条配置校验路径、拓扑投影、稳定 edge ID 生成、v2 运行实例化/恢复以及 commission DI 消费者改用新字段。
- 新 Python 构造及 v2 HTTP composition 只接受新名称；旧关键字、混用和未知字段明确拒绝。
- 显式存储适配器保留旧 v1 canonical JSON/digest，不更改 immutable publication；v2 存储读取兼容完整旧形状但不改写历史。
- 已存 L5 parent_binding/child_binding、边方向、ID、运行 snapshot 和 SQL DDL 均未改变；不是扩大范围重命名所有底层边字段。
- 部署先升级兼容 reader 再启用新 writer；旧 v2 reader 不能消费新字段，回滚不能覆盖已发布历史。
- 现行合同已同步到 [L6 导航](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md)、
  [v2 端点合同](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md#directed-method-relation-endpoints)
  和 [v1 存储合同](../../src/agentclaw/community/core/task/context_collab/l6_products/PROFILE-STORAGE.md#method-relation-endpoint-compatibility)。
- [新增端点契约测试](../../tests/community/core/task/context_collab/product_profile_service/test_method_relation_endpoints.py)
  使用真实 SQLite Profile/runtime 读写与重建；v1 摘要快照取自未修改 HEAD 的 Open+Single 定义，不是新实现的自比较。

### 实际验证

目录为 apps/backend，使用现有 Python 3.12 虚拟环境：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest -q \
  tests/community/core/task/context_collab \
  tests/community/repository/task/context_collab \
  tests/community/di/test_task_module.py \
  tests/community/architecture

DEPLOY_PROFILE=test .venv/bin/python -m pytest -q \
  tests/community/core/task/context_collab/product_profile_service/test_http.py \
  tests/community/core/task/context_collab/product_profile_service/test_method_relation_endpoints.py
```

- 模块/SQL/DI/架构回归：**1,781 passed**（191.72 秒）；新增字段及 HTTP 专项：**41 passed**（3.22 秒）。后者与前者有重叠，不相加作总数。
- 首次新增测试收集因 ORM 类名写错而失败；改为实际 ProductProfileModel 后，17 项端点专项及以上回归均通过。
- 修改的 12 个 Python 文件通过 Ruff format、F 类静态检查与 AST 解析，均少于 1,000 行；git diff --check 通过。
- 方案 Python/YAML 示例、三个 Template 字段集合及明确 source → target 的 NEST 边一致性检查通过；五方法参数未变。
- 全量改动 Markdown 链接扫描发现 **11 个既有缺链**，均已存在于 HEAD；本任务未修改这些历史链接，新增链接目标检查通过。
- 警告来自既有 Pydantic/Starlette 弃用和无效转义；无未解决的测试失败。HEAD 未变，未 commit/push。

### 仍未验证

未运行完整 Backend CI/覆盖率门禁、MySQL 或企业宿主联调；未部署新 reader/writer。
本节的字段重命名测试不是 MultiRollout 的 A1–A12 验收证据；ACCS 当前原文、候选并发隔离及 Agent 策略仍待后续验证。
