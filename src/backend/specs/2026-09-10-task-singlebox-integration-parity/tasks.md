# Tasks: 任务 Singlebox 集成链路同构化

> Status legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked

## Task 1: Characterize integration and authorization behavior
- **Goal:** 固化请求、响应、身份和 401/403 行为。
- **Files:** `src/backend/tests/community/core/task/task_runner/integration/`, `src/backend/tests/community/core/task/singlebox_e2e/`, `src/backend/tests/contracts/`
- **Done when:**
  - [ ] 以设计文档的“全量 Task 对外依赖接口清单”为唯一盘点基线，逐项标记 owner、调用场景、身份/凭据、Singlebox/生产实现、影响、改造归属和验证状态；未发现差异的项也必须显式结论为“已验证同构”。
  - [ ] 按功能覆盖：单 Bot 执行/授权、协作群建群/会话/状态机、BCS token 与 callback、BCN 候选、搜索技能、确认 session/前端 URL、通知/工单、人员部门、Bot identity/binding、TaskAuthGate 与持久化。
  - [ ] `create_group`、BCS `create_session` 分别覆盖成功、错误 HMAC、错误 caller/driver、非成员/不可达成员；不能把“接口已实现”当作鉴权已同构。
  - [ ] 每个本地 rewrite、public-bypass、direct connection、Noop callback 和 double 都有生产对应物、owner、接口请求、影响和验收测试。
- **Depends on:** —

## Task 2: Define task integration capability contract
- **Goal:** 校验 endpoint、credential source、caller identity、callback 和 BCN reachability。
- **Files:** `src/backend/src/agentclaw/community/api/task/`, `src/backend/src/agentclaw/community/di/config.py`, `src/backend/tests/community/config/`
- **Done when:**
  - [ ] schema 拒绝未知、缺失或不兼容的 live-E2E 字段。
  - [ ] 可输出脱敏 evidence，task core 不读取环境变量。
  - [ ] endpoint scope 校验拒绝 singlebox → production、production → singlebox 的跨环境调用和隐式 URL 回退。
- **Depends on:** Task 1

## Task 3: Consolidate shared port composition
- **Goal:** `OpenApiBotAdapter`、`BcsHttpAdapter` 成为唯一 task runtime adapters。
- **Files:** `src/backend/src/agentclaw/community/di/modules/task_module.py`, `src/backend/src/agentclaw/community/di/modules/infrastructure/community/task_runner_integration.py`
- **Done when:**
  - [ ] task core 不按 `DEPLOY_PROFILE` 选 adapter。
  - [ ] singlebox 与生产按不同配置选择同一 adapter。
  - [ ] 缺能力在执行前 fail-fast。
  - [ ] TaskModule 不再持有 `DEPLOY_PROFILE`、`SINGLEBOX_*`、localhost 或 WebSocket 路由选择逻辑。
- **Depends on:** Task 2

## Task 4: Converge single-Bot and BCS authorization
- **Goal:** 移除 direct WebSocket runtime，验证 BaaS/BCS 授权、BCN reachability 与 callback。
- **Files:** task client adapters、callback auth、singlebox E2E fixtures
- **Done when:**
  - [ ] live singlebox 对单 Bot 执行、协作群/会话和发现确认会话使用各自的 common Port adapter；不允许 direct WebSocket 或 connection target runtime。
  - [ ] grant、caller bearer、HMAC、callback signature、owner 与 reachability 有负向测试。
  - [ ] 引擎未支持的单 Bot 执行能力以 task-scoped `OpenApiBotPort` production-contract mock 注入；若仅 BCS→Engine 投递暂缺，只 mock `start/get_state_machine_run` 执行子链路。两者均不替换全局 Engine/BCS/Backend binding，也不跳过任务 poller、状态流转或 callback ingress。
- **Depends on:** Task 3

## Task 5: Restrict doubles and prove release readiness
- **Goal:** double 仅用于模拟测试，live gate 输出可信 integration evidence。
- **Files:** `client/double/`, `scripts/ci/singlebox_coverage.sh`, live E2E tests
- **Done when:**
  - [ ] live E2E/coverage gate 拒绝 double。
  - [ ] E2E 报告每个依赖的 selected plugin、endpoint scope、auth mode 和脱敏 correlation evidence。
  - [ ] 发布门禁拒绝任何 `simulated` 的关键依赖，并要求无权限、错误 scope、过期凭据、错误签名和不可达 Bot 的负向测试通过。
  - [ ] 端口、配置、callback、live 和预发 smoke 全部通过。
- **Depends on:** Task 4

## Groups

- **Group A — Baseline and capability:** Tasks 1, 2
- **Group B — Shared integration:** Task 3
- **Group C — Authenticated execution:** Task 4
- **Group D — Gate and verification:** Task 5
