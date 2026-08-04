# BBS 自主接单 skill — 实施任务清单(tasks.md)

> 配套 `spec.md`(WHAT/WHY)/ `plan.md`(HOW + 逐 Task TDD 步骤与代码)。本文给可勾任务清单,按顺序实现(上一任务确认后再做下一个);每任务 TDD(先红再绿),逐任务 commit。
> 任务详情(TDD 步骤/测试代码/实现代码/commit 信息)**见 `plan.md` 同名 Task N**;本文只给追踪面:文件 / 测试 / commit / 依赖 / 状态。
> **勾选状态**:以 git 历史为准。`[ ]` 未落,`[x]` 已落。
> 落点:ocb backend 四层单向依赖(`adapters/http → api → core`);core 不导入 api;不裸 SQL;`from __future__ import annotations` + `Optional[T]`(非 `T | None`)+ `StrEnum`;状态机零改动。
> 日期:2026-08-04。

---

## 0. 贯穿规矩(所有任务,详见 plan.md Global Constraints)

- **状态机零改动**:不改 `core/task/domain/state_machine.py` 的 `NODE_TRANSITIONS`/`GRAPH_TRANSITIONS`;接力复用现成 `RUNNING→FAILED`、`FAILED→RUNNING`。
- **状态唯一写口不变**:新方法走 `_emit`+`_apply_event` fold+`save`,与 `claim_node` 同形,不绕 guard/`on_event`。
- **分层不破**:新方法先进 core `TaskService`(及 `core/task/protocols.py` 的 `TaskService` Protocol),api 层 `api/task/service_api.py:TaskServiceProtocol` 仅加同名方法(`*args,**kwargs`),由 `tests/community/architecture/test_task_service_api_conformance.py` 自动校验。
- **测试基建**:`TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())`;仓库 `plugins/community/task/in_memory_repos.py`。
- **跑测试**:`cd src/backend && pytest tests/community/<path> -v`。

---

## 1. 任务清单(顺序实现)

> 依赖:Task 5 依赖 2/3/4;Task 7 依赖 2/3;Task 9 依赖 1-7;Task 8(skill 内容)独立,可与 6/7 并行但建议末尾评审。建议顺序:1→2→3→4→5→6→7→8→9。Task 4 的 409 测试需 Task 5 路由就位后才能真正跑过(4 先落 handler,5 落路由后同测)。

- [ ] **Task 1** `NODE_RELEASED` 事件 + fold(RUNNING→FAILED,不泵 tick/不升 HUMAN) — Files: `core/task/domain/events.py`(加 `EventKind.NODE_RELEASED` + `NodeReleased` dataclass)、`core/task/services/task_service.py:_apply_event`(加 fold 分支)。TDD: `tests/community/core/task/services/test_event_fold.py::test_node_released_fold_running_to_failed_no_escalation`。详见 plan.md Task 1。
- [ ] **Task 2** claim_node 设兜底 `lease_until` + `run_mode` 参 + `DispatchResult.lease_until` + `_utcnow` 时钟缝 — Files: `core/task/protocols.py`(`DispatchResult`+`TaskService.claim_node` Protocol)、`core/task/services/task_service.py`(模块常量 `BBS_LEASE_FALLBACK_SECONDS`+`_utcnow()`+`claim_node` 改写)。TDD: `test_task_service_state.py::test_claim_node_sets_lease_until_and_run_mode` + 现有 claim 用例不破。详见 plan.md Task 2。
- [ ] **Task 3** `TaskService.release_node`(主动让出,`Forbidden` 守 assignee)+ `expire_lease`(清扫器)+ Protocol/conformance — Files: `core/task/services/task_service.py`、`core/task/protocols.py`(`TaskService` Protocol 加 `release_node`/`expire_lease`)、`api/task/service_api.py`(`TaskServiceProtocol` 加 `release_node`)。TDD: `test_task_service_state.py -k "release_node or expire_lease or release_then_reclaim"` + `test_task_service_api_conformance.py`。详见 plan.md Task 3。
- [ ] **Task 4** `IllegalTransitionError → HTTP 409` 全局 handler — Files: `adapters/http/app.py`(新增 `@app.exception_handler(IllegalTransitionError)`)。TDD: `tests/community/adapters/http/task/test_router.py::test_illegal_transition_maps_to_409`(需 Task 5 路由就位)。详见 plan.md Task 4。
- [ ] **Task 5** `POST /claim` + `POST /release` 路由 + schemas(真实 service 桩跑 200/409/403) — Files: `adapters/http/task/schemas.py`(`ClaimRequest/ClaimResponse/ReleaseRequest/ReleaseResponse`)、`adapters/http/task/router.py`(两路由)。TDD: `test_router.py -k "claim or release or illegal_transition"` + `test_router_registers_claim_release_routes`。详见 plan.md Task 5。
- [ ] **Task 6** `get_node_detail` 直出 SubtaskState(intermediate_results/gap_records/artifacts,接力可见前序轨迹) — Files: `core/task/services/task_service.py`(`get_node_detail`/`_node_view`)、`adapters/http/task/schemas.py`(`TaskNodeDetailView`)。TDD: `test_task_service_state.py::test_get_node_detail_exposes_subtask_state` + `test_schemas.py`。详见 plan.md Task 6。
- [ ] **Task 7** 兜底租期清扫器:`find_expired_lease_nodes` + `sweep_expired_leases` + `LeaseSweeper` + DI — Files: `plugins/community/task/in_memory_repos.py`、`core/task/protocols.py`(`TaskRepo` Protocol 加 `find_expired_lease_nodes`)、`core/task/services/task_service.py`(`sweep_expired_leases`)、`core/task/services/lease_sweeper.py`(新建)、`di/`(绑 `LeaseSweeper`)。TDD: `tests/community/core/task/services/test_lease_sweeper.py`(过期→收回 lease_expired;未过期→0)。详见 plan.md Task 7。
- [ ] **Task 8** 内容 skill `bbs-relay-pickup`(SKILL.md + references) — Files: `skill/bbs-relay-pickup/SKILL.md`、`skill/bbs-relay-pickup/references/{task-api,judge-rubric,idempotency}.md`。验收:人工评审 + Task 9 集成测试驱动指令正确性。详见 plan.md Task 8。
- [ ] **Task 9** 集成场景测试(race 恰一赢 / handoff 立即接力 / crash 过期接力+轨迹保留) — Files: `tests/community/core/task/services/test_bbs_pickup_integration.py`。TDD: 自身 3 场景 + 全 task 域回归(`pytest tests/community/core/task/ tests/community/adapters/http/task/ tests/community/architecture/test_task_service_api_conformance.py`)。详见 plan.md Task 9。

---

## 2. 代码范围外 / 后续(不在本任务清单,登记防遗漏)

- [ ] **skill 发布到 skill center** — `bbs-relay-pickup` authored 源(本清单 Task 8 产出)经 `local://` 上传或 `git://` 同步 + 激活到 bot 的 active skills 目录;属部署接入。
- [ ] **清扫器定时调度接入** — `LeaseSweeper`(Task 7)的周期触发(APScheduler / asyncio loop 每 N 秒调 `sweep_once()`);属部署接入。`T_fallback` 取值与扫描周期为 spec §7.2 评审项。
- [ ] **ORM 仓库 `find_expired_lease_nodes`** — prod 扫描器数据源;InMemory(Task 7)已覆盖逻辑,ORM repo impl 按"加载 RUNNING 图谱→Python 扫节点 lease_until"模式补,加契约测试。
- [ ] **BBS 接单鉴权** — task 路由裸奔现状沿用;bot token/标头校验另系分(spec §7.2)。
- [ ] **`max_attempts` 与 handoff/lease_expired 策略** — `outcome=handoff`/`lease_expired` 不计 failure-attempt;BBS 节点是否放宽/取消 `max_attempts` 上限留评审(spec §7.2)。

---

## 3. 完成判据(全部 [x] 的前置)

- 9 个 Task 均 `[x]`(git 历史可追溯)。
- 全 task 域测试绿:`cd src/backend && pytest tests/community/core/task/ tests/community/adapters/http/task/ tests/community/architecture/test_task_service_api_conformance.py -v`。
- 集成场景(Task 9)三路径绿:多 bot 抢占恰一赢、release 立即接力、崩溃过期接力且轨迹保留。
- spec AC-01~11 满足(对照 spec.md §5)。
