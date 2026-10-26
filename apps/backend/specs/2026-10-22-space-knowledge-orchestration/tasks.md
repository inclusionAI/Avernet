# Tasks: Isolate Space knowledge membership persistence

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked
> 2026-10-26 用户批准开始实现；按分组执行。Task 1 RED：15 个真实路径用例失败（缺表/HTTP 500），详见本地审查日志。

## Task 1 [x]: Lock down missing-table failures before the refactor
- **Goal:** 将 2026-10-26 审查探针转为能捕捉当前缺陷的正式回归。
- **Files:** `tests/community/core/space_knowledge/test_capture_isolation.py`、`tests/community/adapters/http/space_knowledge/test_capture_isolation_http.py`（均位于 apps/backend）。
- **Done when:**
  - [x] 真实 operator-managed SQLite 明确无同步表、禁止自动建表；当前实现 RED。
  - [x] 参数化团队/个人/Skill seam 创建、单个与批量成员/角色、改名/删除、人工/AUTO 审批，检查真实提交结果。
  - [x] 完整 DI/HTTP lifespan 用例不可被手工空 mock 替代；认证和旧响应 envelope 不变。
- **Depends on:** —
- **AC:** AC1、AC2、AC7、AC8。

## Task 2 [x]: Pilot the internal persistence extension on Space creation and membership
- **Goal:** 引入窄内部 contract、空实现和知识实现，先让小范围写入完成 RED→GREEN。
- **Files:** `core/repository/protocols/space_persistence_effects.py`、`implementations/spaces/{source_lock,persistence_effects,knowledge_effects}.py`、`implementations/spaces/{space,identity,knowledge_sync}.py`、相应 pilot fixtures（源码均位于 apps/backend/src/agentclaw/community）。
- **Done when:**
  - [x] 通用源锁不依赖知识表；未启用的创建和成员变更不查询知识表、不执行知识 write guard。
  - [x] 启用实现仍在调用者同一 session/事务记录快照和保护 retired ID。
  - [x] Constructor contract 必需且明确，无 Optional/静默默认覆盖遗漏装配。
  - [x] 首次激活/成员竞态和 ID 生命周期测试保持通过，pilot 缺表测试 GREEN。
- **Depends on:** Task 1。
- **AC:** AC2、AC4、AC5、AC7。

## Task 3 [x]: Wire explicit capture configuration and safe lifecycle checks
- **Goal:** 完整默认装配无知识表可用；记录与投递独立，并防止有历史时误切空实现。
- **Files:** `di/space_knowledge_capture_config.py`、`di/modules/space_knowledge_module.py`、`di/container.py`、`core/space_knowledge/capture_lifecycle.py`、配置/DI/lifespan 测试及相关配置示例。
- **Done when:**
  - [x] 缺省 capture=false，拒绝未知键/错误类型；纯配置校验在所有 profile eager 执行。
  - [x] 默认 graph 使用空扩展；真实 producer 必须与启用记录装配一致。
  - [x] 未启用且缺表时完整启动成功，不以预建同步表满足测试。
  - [x] DB bootstrap 后验证持久模式；生命周期异常不被 discovery 吞掉，验证前不得放行源写入。
  - [x] 当前 env 有任意 app 历史时禁止关闭 capture；Sink 暂停不影响 capture。
- **Depends on:** Task 2。
- **AC:** AC1、AC2、AC3、AC5。

## Task 4 [x]: Migrate maintenance, approval, and Skill Space creators
- **Goal:** 移除既有 writer 对知识 helper 的直接依赖，不改业务权限或事务边界。
- **Files:** `core/repository/implementations/spaces/management.py`、`work_orders/{work_order,auto_approval}.py`、`skill_center/space_skill.py`；`di/modules/{spaces_module,space_skill_repository_bindings,work_orders_module}.py` 按真实绑定需要调整；原有相关 tests/fixtures。
- **Done when:**
  - [x] 所有已受影响源 writer 使用同一策略实例，不遗漏创建入口或审批分支。
  - [x] 所有缺表回归 GREEN，已激活时事务失败/容量拒绝仍原子。
  - [x] MANUAL/PENDING/审批人/Owner 检查及 Skill 业务原样保留。
  - [x] AUTO 真实 DB Service 测试断言最终 FAILED、失败通知、无成员/无新快照，不误用 Repository 中间态。
- **Depends on:** Task 3。
- **AC:** AC1、AC4、AC5、AC7、AC8。

## Task 5 [x]: Bound pending candidate discovery and avoid payload reads
- **Goal:** 修正已复现的深积压扫描放大，只改知识模块内部。
- **Files:** `core/repository/implementations/spaces/knowledge_sync.py`、`core/repository/protocols/spaces.py`、`core/space_knowledge/membership_sync.py`、`tests/community/core/space_knowledge/test_pending_scan_bounds.py` 及原有同步测试。
- **Done when:**
  - [x] 最多 32 个 Space 候选，最多 33 次有界索引查找，有跨 Space 的继续游标。
  - [x] 1/1000/100000 单 Space pending 探针不再按全部版本线性扫描；已确认历史被排除。
  - [x] 调度/progress/ack 元数据查询不读取 payload；真正投递只取严格下一版。
  - [x] 缺失/损坏队头仍 fail closed；FIFO/hash/queue pointer 语义不变。
  - [x] 明确 SQL 往返权衡；不声称 SQLite 计划证明 MySQL/OceanBase 通过。
- **Depends on:** Task 4。
- **AC:** AC4、AC6、AC8。

## Task 6 [x]: Guard the dependency boundary and reconcile documentation
- **Goal:** 将隔离要求固定为架构与合同守卫，防止回退。
- **Files:** `tests/community/architecture/test_space_persistence_boundary.py`；`core/repository/README.md`、`core/space_knowledge/README.md`、本目录 `contract.md`、`spec.md`、`plan.md`、`tasks.md`，部署说明。
- **Done when:**
  - [x] 守卫拒绝既有 writer 直接导入知识快照/身份实现；scope 只约束本次有限边界。
  - [x] 新 contract/no-op/启用实现有行为测试；不扩 CI allowlist，不削弱原门禁。
  - [x] 文档区分从未启用、启用但未激活、暂停投递、有历史禁止关闭记录；取消全局 DDL 前置表述。
  - [x] 说明 rollout 全 writer 切换前不能激活，以及回滚/企业装配边界。
- **Depends on:** Task 5。
- **AC:** AC3、AC5、AC7。

## Task 7 [x]: Run verification and report delivery boundaries
- **Goal:** 用新实现的新证据逐条验收。
- **Done when:**
  - [x] AC1–AC8 全部映射到结果；配置/装配、原成员/维护/审批、Skill Repository、协议与架构回归通过。
  - [x] file-SQLite 跨连接/进程、暂停重启、ID tombstone、超限原子性测试通过。
  - [x] changed-file lint、所有修改 source ≤1000 行、git diff --check 通过。
  - [x] 尝试适用的统一 Singlebox gate；无法运行时记录实际阻塞，不用 focused 结果替代。
  - [x] 企业装配、MySQL/OceanBase、部署/DDL、远端 CI 未验证时明确列出；无授权不推送/提交 PR/执行线上操作。
- **Depends on:** Task 6。
- **AC:** AC1–AC8。

Task 7 evidence (2026-10-26): 2900 passed / 10 skipped across non-overlapping final suites; 20 focused missing-table/wiring cases passed. Ruff/SAST/source-size/diff checks passed. Unified Singlebox attempted but dependency download timed out; verifier confirms canonical artifacts missing. See verification.md; no remote delivery claim.

Task 6 evidence (2026-10-26): finite AST import guard and same-transaction/no-op behavioral contracts added; existing repository contract gates caught and fixed abstract/type-only import metadata without any exemptions; final verification follows Task 7.

Task 5 evidence (2026-10-26): new bounds tests RED (3 failed/1 passed), then 59 sync/race tests and 7 focused bounds/hash tests passed; metadata dispatch validates payload at execution; SQLite only, no remote DB claim.

Task 4 evidence (2026-10-26): 19 missing-table cases, 31 enabled/recovery cases, and 1068 broad repository/service regressions passed locally; no remote/Singlebox claim.

## Groups

- **Group A — Missing-table pilot:** Tasks 1, 2, 3
  - Theme: 先证明默认装配和小范围写入满足硬隔离。
- **Group B — Writer convergence:** Task 4
  - Theme: 维护、审批和 Skill 创建统一接入，保持旧业务语义。
- **Group C — Bounded producer and boundary guards:** Tasks 5, 6
  - Theme: 有界调度、低元数据读取成本、架构/部署合同固定。
- **Group D — Verification:** Task 7
  - Theme: 用新实现证据关闭所有验收项，严格区分本地与远端交付。
