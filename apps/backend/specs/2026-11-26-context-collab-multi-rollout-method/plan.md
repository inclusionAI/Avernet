# L5 MultiRollout Method 实施与验证计划

- 状态：Draft / MultiRollout 尚未执行；依赖 [spec](spec.md) 评审。关系端点命名已独立调整，不代表 S1 完成。
- 基线：`3857a08d93`；证据和外部文档限制见 [research](research.md)。
- 顺序原则：先解决运行权威/恢复，再做并发和 Agent；不能先在 L6 用循环拼出表面效果。

## 1. S1：精确合同与配置

- 已独立完成的前置命名：MethodRelationTemplate 使用 source_method/target_method；新声明不再使用 parent_method/child_method，历史存储保留受测兼容边界。

- L5 增加 MultiRollout@1.0 Profile、Kind、policy 判别联合、预算值和有限 NEST 矩阵。
- 保留旧四方法 @0.2 及旧组合 wire；新能力使用本地扩展声明，不伪造标准编号。
- 提供 MethodRunPort、策略输入/输出、RunPlan 与决定 DTO，依赖只向下/横切。
- L6 codec/联合配置验证仅从 MethodCompositionTemplate.required_methods/relations 识别新根和唯一 NEST 子方法；policy 不新增子方法选择字段，未安装 adapter 时拒绝发布。
- L6 实例化 MethodBinding/MethodCompositionEdge，L5 沿有效 NEST 边解析子方法；持久化派生的边/Binding/digest，不产生第二份可编辑组合。
- 测试：精确版本、Profile/矩阵一致性、环/深度、必填/未知字段、bool 非整数、上下界、optional 不激活。
- 测试：composition/profile/policy 严格往返；缺失/多个 NEST 目标、错误方向、未选择端点和在 policy 中添加子方法字段均拒绝；不按声明顺序推断关系。
- 测试：不同执行策略复用同一 NEST 关系，仅替换所属 MethodTemplate 的 policy；旧存量定义和五方法签名合同保持不变。

交付停点：可以校验和 round-trip 新声明，仍不能宣称可执行。

## 2. S2：多候选 snapshot、SQL 和单一运行权威

- 加显式 snapshot 版本、新控制/候选/计划记录；旧无版本记录走明确 legacy decoder。
- 将 current 展示焦点与执行候选作用域分开；candidate 与 Collet 的 aggregate 分开。
- 复用现有身份分配；每候选独立 Swarm Binding、stage/attempt/route/产物作用域。
- 原子保存预算预留、决定、回执、候选身份和 intents；补所需索引/lease DDL，不按领域类逐个建表。
- 演进 outbox 的逻辑键/claim/fence；不能用全局 revision 失配把兄弟 intent 无条件丢弃。
- 测试：真实 SQLite 多连接下 CAS、重复 claim、身份唯一、回执/outbox 写失败全回滚。
- 测试：旧/新 schema、缺治理事实、错误成员关系、重复 stage ID、route 不匹配、未知版本拒绝。
- 测试：重建 worker/service 后预算/等待/决定不丢失；12 MiB 上限显式失败。

交付停点：可恢复多个候选事实，尚不接真实 provider。

## 3. S3：durable Swarm body adapter 与隔离

- 把单候选执行推进从全局 current 依赖改成显式候选作用域，保留已有串行 Swarm 路径。
- MethodRunPort 的 Swarm adapter 复用实际 RolloutExecutionPort、运行观察、真实消息/证据端口。
- 每候选真实独立 session 与 workspace/外部执行键，输入只共享不可变资源引用。
- 实施按 stage 生成执行输入的 RunPlan overlay，不能只改变日志。
- 无隔离/验证能力默认 fail closed；真实外部副作用不得被 workspace 隔离假象掩盖。
- 测试：actual adapter → engine → SQLite → recording provider 的完整一次成功及失败。
- 测试：两个候选的 session、stage ID、分配、文件、输入不能串扰；资源权限被撤销后复查失败。

## 4. S4：parallel_best_of

- 预分配 N 个候选并做预算 admission；持久化执行槽，候选外部 I/O 有限并发。
- 等待 N 份终局，失败隔离；明确截止/stop 下不完整结果。
- 实现确定性 highest-score，统一 rubric、有限分值、稳定 tie-break 和唯一 winner。
- Agent judge 经过同一 typed 输出验证，已提交选择恢复时不重算。
- 用两个可控 blocking provider 调用证明实际重叠和峰值上限，不能仅断言创建了 N 个 asyncio Task。
- 测试：混合结果、全部失败、乱序、同分、未知/越权赢家、缺指标、NaN/Infinity；两个 Collet 竞争同一租户配额时不超分。
- 测试：两份候选 progress 的 CAS 冲突处理、重放和独立 evidence 验证；坏候选不阻塞其它已有效事实。

交付停点：并发策略可本地完整验收，仍不等于企业 Agent/provider 联调。

## 5. S5：adaptive_until_accepted 与统一互动裁决

- 接纳质量失败时原子写 WAITING_FEEDBACK/通知或唯一自主 adaptation intent。
- 人工/自主都调用同一策略端和 G1/G4 patch/预算裁决器；保持现有五方法签名。
- stage 调整只允许指令、原候选范围及重跑闭包；计划版本不可变，历史不得覆盖。
- 复用 stage 证据须证明等价/授权/不可变；默认完整重跑。
- 计数和预留覆盖 rollout、投递和策略调用；实现无进展、整体截止和人工等待超时。
- 测试旅程：失败 → 等待 → interactive → 新 plan → 重跑成功；等待前不得额外 dispatch。
- 测试旅程：失败 → 系统唯一 trigger → Agent → 重跑成功；duplicate intents 不多建后继。
- 测试：同 UUID、变目标/反馈、撤权 replay、不同 UUID 并发、proposal 后 SQL CAS 失败；在途重复不重复调用，崩溃恢复的新 Agent 调用单独计费于调用次数预算。
- 测试：no-op 不重置截止；反复 redo/无效 patch 有界终止；Agent 不能放宽验收或预算。

## 6. S6：HTTP、DI、到期调度及故障对账

- multi_rollout_control 查询元数据、OpenAPI/HTTP schemas、真实 stage 消息查询与 frontend/调用者合同同步。
- 保留 progress 的签名验证、ReportContext 参数及 aggregate expected_revision 意义；文档化确定的重试流程。
- outbox 增加选择/演进/通知/候选推进/对账；由宿主显式安装 worker 与到期检查。
- fail-closed 公共默认、确定性本地策略/recording Agent、宿主 adapter 绑定均需消费者级契约测试。
- 故障注入：start 后存路由前崩溃、取消未确认、选择/提案响应后提交前崩溃、stop 与迟到响应竞争。
- 真实 SQLite 事务验证恢复和回滚；provider 结果未知但无查询/幂等能力时必须停在可诊断状态。
- 补 G3 有限指标：开始/完成/通过数、并发峰值、预算预留/消耗、等待、选择/调整耗时、终止原因。
- 指标不含原始反馈/内部文档/凭证；观测不能反过来当权威状态。

## 7. 验证命令与证据边界

以下是**实施后待执行**的命令，目录为 `apps/backend/`；新测试目录在实施时创建。

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/context_collab/multi_rollout \
  tests/community/core/task/context_collab/product_profile_service

DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/architecture \
  tests/community/framework/test_coverage_gate.py \
  tests/community/adapters/http/openapi_v1/test_explicit_user_id.py \
  tests/community/adapters/http/openapi_v1/test_schema_docs.py \
  tests/community/core/bot_config_surface/test_no_handler_only_checks.py

bash scripts/ci_test.sh
```

- 新策略/执行接缝分别增加消费者 happy/failure/replay 契约测试，断言真实调用能力，不仅 suite 存在。
- 并发用例采用独立 DB 连接与确定同步栅栏；带覆盖率重复运行，避免借 sleep 掩盖 race。
- 旧串行 profile、Open/Swarm 组合、权限、进度签名和 session 路由均回归。
- 更新 L5/产品 owning README 与 Context Boundary，记录本地扩展和截止/恢复诊断。
- 实施交付另写 delivery：实现、CI、MySQL DDL、真实 provider、企业装配、部署分别列证据/缺口。
- 本轮 spec 的文档解析/链接检查不是上述测试通过；不得复用前轮 35,402 passed 作为新验收。
