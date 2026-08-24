# 任务模块真实case本地singlebox端到端集成用例

- **日期**: 2026-08-13
- **状态**: specify 阶段
- **参考(权威框架与案例)**: `src/backend/specs/2026-08-09-task-goal-driven-execution-framework/{spec,plan,tasks}.md`（案例 `gwqie46v7hzr1w6h` 存储行业尽调：三阶段三模态 + FAIL治愈 + MISS升BBS + BBS认领 + STUCK→HUNG）
- **代码权威源**: Avernet 仓 `src/backend`。`ocb-public` 仅为其同步镜像，**只读**；所有改动只能落 Avernet。

---

## 1. 背景 (WHY)

`core/task`（任务目标驱动执行框架，M0–M5 已落地）已具备完整的编排核：`TaskService` facade（`execute` / `get_task_dashboard`）、`ExecutionEngine`（`on_execute/on_report/on_miss/on_harness` 事件驱动 + 状态条件推进）、`TaskPlanner`/`TaskDispatcher`（first-match-wins 策略池，`set_strategies` 注入 seam）、`TaskRunner`（三模态 + `DeliveryPort`/`set_delivery` + `TaskLoopCallback` 回投）、`TaskHarness`（旁路复位）。

但现状有两个关键缺口：

1. **seam 全是 stub**：Avernet 默认策略 `GapBasedPlanningStrategy` 返 `[]`、`SearchBasedDispatchStrategy` 恒 `MISS`、`TaskRunner` 投递只记日志返 `True`。`gwqie46v7hzr1w6h` 案例目前只在 **`tests/.../task/e2e/test_e2e.py`** 用 **in-process stub**（`CaseDecomposer`/`CaseBotDiscover` + test runner stub）跑通——seam 真值未经验证。
2. **未接线到运行时**：`TaskService` **未被任何 DI module 装配、没有任何 HTTP router 暴露**（`grep` 无 `task_module.py`、无 `/openapi/v1/collaboration/tasks/*` 路由、`TaskService(` 仅在自身 core 构造）。即框架在 singlebox 运行态根本不可达，无法被真实 API 驱动。

因此现有 e2e 是"内核逻辑正确性"验证，**不是"真实工程链路"验证**：真实 bot、真实 skill（规划/搜推/验收）、真实投递、真实回投都未被打通。一旦 corp 在 ocb 侧替换策略/投递，没有人能保证 seam 契约在真实 IO 下成立、回调并发下锁模型成立、HTTP 边界协议成立。

此外，同学已在 `core/task/task_runner/integration/` 落地了三模态执行接入的真值骨架（`OpenApiBotAdapter`/`BcsHttpAdapter`/`TaskExecutor`/`TaskExecutorResultPoller`/`PromptFormatterImpl`/double 实现/`build_integration` 组合根），但**①策略 body 仍是 stub（plan 返 []、dispatch 恒 MISS）、②`_build_runner` 未注入 `TaskExecutor`、③无 DI module/HTTP router 接线**——integration 骨架悬空，运行态仍不可达。

### 核心诉求

工程需要一份 **真实 case 的本地 singlebox 端到端集成用例**：起本地 singlebox 服务 → 经真实 API 创建 bot → 创建并安装三个真实 skill（规划 / 搜推 / 验收）到 bot → 用真实 case 拉通"理解→规划→派发→执行→验收→重规划"全链路。从而：

- 验证 seam（`PlanningStrategy`/`DispatchStrategy`/`DeliveryPort`/`TaskLoopCallback`）在真实 IO 下的契约成立。
- 验证 `TaskService` 经 HTTP 边界可被驱动、回调可回投、并发锁模型在跨请求回调下成立。
- 验证三模态（single_bot / coop_group / bbs）+ 重规划循环（FAIL治愈 / MISS升BBS）在真实 skill 产出下可跑通。
- 为 corp（ocb 侧）替换真实 LLM 策略/真实 BCS 投递提供一份可回归的 baseline 集成用例。

---

## 2. 目标 (WHAT)

构建一套 **本地 singlebox 真实链路端到端集成用例**，把 `core/task` 的 stub seam 替换为"真实 bot + 真实安装 skill + 真实投递 + 真实回投"的适配层，并用权威案例 `gwqie46v7hzr1w6h` 拉通全链路。

### G1. 真实实现适配层（策略类名/接口不变，stub body → 真实 body）
- **定位原则（评审纠偏）**：任务执行引擎只有一套 `ExecutionEngine`，**不变**。skill 安装到 bot、bot 创建、singlebox 起全栈都是**前置环境准备**，与任务执行逻辑无关。框架**不感知 bot 装了哪些 skill、不感知 bot 内部怎么执行/验收**，只负责：组装 prompt 上下文 → 投给指定 bot → 回收结构化结果 → 更新图谱 → 驱动流程往下。
- **策略类名/接口不变，只实现体变真实**：`GapBasedPlanningStrategy` / `SearchBasedDispatchStrategy` / `DeliveryPort` 都是框架自带默认策略（Avernet stub）。类名、方法签名、策略池结构都不变。本轮只把实现体从 stub（返 `[]` / 恒 MISS / 记日志）改为真实实现（组 payload → 投 bot → 收结构化 result）。corp 接真实 LLM 同理替换 body，seam 不变。
- **结果回收分两种**：
  - **plan/dispatch 同步收 result**（`await apply()` 当返回值直接拿，引擎锁内已 await，**不需异步回投**）。
  - **execute/verify 经 `report_result` 异步回投**（投递给 bot → bot 凭已装 skill 执行完上报 → `POST /openapi/v1/collaboration/tasks/callback/report` → `on_report` 翻态推进）。
- **skill 宿主划分**（bot 侧黑盒，框架不感知 skill 存在，只投 payload 给指定 bot）：
  - `planning` + `search`（验收决策类 skill）→ **owner bot**（`owner_bot_id`）。
  - `execute`（执行操作；**叶子节点的自验收 verdict 折叠进 execute 回投**）→ **worker bot**（`assignee`）。
  - `verify`/验收（**聚合结构子 output + goal/acceptances，含根终验**）→ **owner bot**（`owner_bot_id`）——对齐框架 2026-08-09 §聚合收敛/§5（owner/master bot 经 source_channel 回投 verdict；engine 不主动验，无 `OwnerBotVerifyPort`）。
- 所有"与 bot 通信"经 `OpenApiBotPort`（Protocol，`integration/ports.py`）隔离；plan/dispatch 用 `send_and_wait_async` 同步取结果，execute 用 `send_message`+旁路 poller 回投。

### G2. 后端接线（DI + HTTP，补齐运行时可达性缺口）
- 新 `di/modules/task_module.py`：装配 `TaskService(TaskGraphService, TaskHarness)`，**引擎始终是 `ExecutionEngine`**；`_build_runner` 注入 `build_integration()` 返回的 `TaskExecutor`（三模态投递）+ `set_strategies` 注入真实 plan/dispatch 策略 body（`TASK_ENGINE=skill` 时，否则默认 stub；prod 不清）。`OpenApiBotPort` 经 `ApiKeyProvider`（base_url/api_key/cookie/referer）配置注入——local/prod 纯配置差异。
- 新 `adapters/http/task/router.py`（thin）：`POST /openapi/v1/collaboration/tasks/execute`、`GET /openapi/v1/collaboration/tasks/dashboard`、`POST /openapi/v1/collaboration/tasks/callback/report`（回投 → `callback.report_result`）。Router 只翻译协议，不持领域策略。
- App include 该 router；router-hit 进 singlebox coverage 记录。

### G3. 三个真实 skill（作为真实可安装 skill 包）
- 规划 skill / 搜推 skill / 验收 skill：以 **真实 skill 包**（SKILL.md + 确定性 scaffold）形式存在，经 `/api/skills/upload` 上传、经 skillset 激活安装到 owner bot；输入输出为结构化 JSON（对齐 seam 契约）。scaffold 针对 `gwqie46v7hzr1w6h` 案例确定式产出，确保轨迹可复现（"真实"在工程链路，不在 LLM 随机性）。

### G4. 真实 case 端到端用例
- 用例：`POST /openapi/v1/collaboration/tasks/execute` 提交 `gwqie46v7hzr1w6h`（存储行业尽调）TaskInfo；real `on_execute` 跑：规划 skill 拆解 → 搜推 skill 匹配 worker bots → 投递执行 → 验收 skill 验收 → 回投翻态 → FAIL治愈/MISS升BBS（按案例）→ 根 DONE。
- 轮询 `GET /openapi/v1/collaboration/tasks/dashboard` 至终态，断言分解树、节点状态、run_mode、传播、补救、终态与权威剧本一致。
- 至少覆盖 **三阶段三模态 happy 路径 + 一条重规划恢复路径（FAIL治愈）**；BBS/STUCK 作为可选第二用例。

### G5. singlebox 运行入口与门禁
- 提供可重复运行的入口（脚本/pytest session fixture）：起 singlebox `--mode real` → 健康检查 → 跑用例 → 拆除；可用 `--module task_e2e` 单独跑。
- 用例默认 gated（`SINGLEBOX_TASK_E2E=1` 或服务在位才跑），不影响默认单测/CI 默认面。

### G6. 遵守微内核宪法与框架约束
- 框架 **零 case 知识** 不破坏：真实适配与 skill 产出含节点名，框架代码不含。
- Rule 14（配置驱动 wiring）、Rule 20（local/prod 实现）、Rule 22（上下文边界）、Rule 25（HTTP adapter 契约测试）。
- transport-agnostic：HTTP router thin，不引入领域策略。
- **只改 Avernet**；新文件落 Avernet `src/backend`，自动同步 ocb-public，不在 ocb-public 手改。

---

## 3. 非目标 (Non-Goals)

- **不**替换框架内核：不改 `core/task` 的 domain/graph/engine/planner/dispatcher/runner/harness 的编排逻辑与契约（仅在必要时补最小 seam，且需在 plan 显式说明）。
- **不**引入真实 LLM 规划/验收算法：skill scaffold 是确定式测试产物（对齐案例），目标是验证工程链路而非算法质量。
- **接真实 BCS 拉群建群**：singlebox `all` 起栈含 BCS，`form_coop_group` body 真实化对接 BCS `POST /groups`（`group_strategy` 三态对齐 `CollaborationRuntimeDefinition`），验证三模态 + 协作群成员角色分工 + 回投链路。
- **不**做 prod 接线（不把 skill-backed 策略/投递设为 prod 默认；prod 的 corp LLM/BCS 仍属 ocb 侧后续，沿用同一 seam）。
- **不**统一 skill 调用 RPC 为新公开 API（`OpenApiBotPort` 是集成用 local seam，不对外契约化）。
- **不**做前端/副屏可视化对接。

---

## 4. 范围与形态 (Scope)

### 交付物
- `spec.md`（本文件）：WHAT/WHY。
- `plan.md`：HOW——`integration/` 接线（`TaskExecutor`/`OpenApiBotAdapter`/`BcsHttpAdapter`/poller）、plan/dispatch 策略 body、DI/HTTP、skill 包、用例驱动、singlebox 入口、文件落点。
- `tasks.md`：可独立验收的编号清单（spike → 适配层 → 接线 → skill → 用例 → 门禁）。

### 形态
- **运行形态**：本地 singlebox（ocb `scripts/local_setup.sh` / `singlebox_coverage.sh --mode real`）起 backend(8888) 等真实服务；用例经 HTTP(async httpx) 驱动。
- **代码落点**：全部在 Avernet `src/backend`：
  - `integration/` 接线（`TaskExecutor`/poller/`OpenApiBotAdapter`/`BcsHttpAdapter`）已在 `core/task/task_runner/integration/`；plan/dispatch 策略 body 真实化落各 strategy 文件。
  - DI/HTTP：`di/modules/task_module.py` + `adapters/http/task/`。
  - skill 包：`tests/.../task/singlebox_e2e/skills/`（fixtures，运行时上传）。
  - 用例：`tests/.../task/singlebox_e2e/test_realcase_e2e.py`。
  - 入口：`scripts/ci/singlebox_task_e2e.sh`（或 pytest session fixture）。

---

## 5. 利益相关者与约束

| 角色 | 关注点 |
|---|---|
| 框架维护者 | seam 契约在真实 IO/并发回调下成立；锁模型不破；零 case 知识不破 |
| corp 接入者（ocb 侧） | 有一份可回归 baseline 集成用例；替换真实 LLM/BCS 时能对账 |
| singlebox/CI 维护者 | 用例 gated、可单独跑、不拖慢默认 CI；进 coverage manifest |
| 微内核宪法 | DI 接线、thin router、local/prod、契约测试 |

### 硬约束
- **只改 Avernet**；ocb-public 只读镜像。
- 不破坏现有 `test_e2e.py`（in-process stub 用例保留，作为内核逻辑快测；新用例为真实链路集成测，二者并存）。

---

## 6. 成功标准（验收条件）

- **AC-1**：singlebox 起服务后，经真实 HTTP API 能创建 owner bot + N 个 worker bot，并上传+激活安装规划/搜推/验收三个真实 skill 到 owner bot（可查激活状态）。
- **AC-2**：`POST /openapi/v1/collaboration/tasks/execute` 提交 `gwqie46v7hzr1w6h` TaskInfo 后，框架经 **真实 skill 调用**（非 stub）完成首帧：规划 skill 拆出第一批子节点 → 搜推 skill 为其决出执行者 → 投递给真实 bot。
- **AC-3**：执行结果经真实回投链路（`/openapi/v1/collaboration/tasks/callback/report` → `callback.report_result` → `on_report`）翻态、传播、补救；`GET /openapi/v1/collaboration/tasks/dashboard` 反映分解树与状态推进。
- **AC-4**：三阶段三模态 happy 路径跑到 **根 DONE**；断言分解树结构、run_mode（single_bot/coop_group/bbs）、验收 PASS 传播、终态与权威剧本一致。
- **AC-5**：至少一条重规划恢复路径（FAIL治愈：验收 FAIL+gaps → 规划 skill 产补救子 → 重投 → PASS）被真实链路跑通并断言。
- **AC-6**：HTTP adapter 有契约测试（execute/callback/dashboard 协议）；`OpenApiBotPort`/`TaskExecutor` 有 double 实现 + 契约测试。
- **AC-7**：用例 gated，可 `--module task_e2e` 单独跑；默认 `pytest`/CI 默认面不跑（除非显式开）。
- **AC-8**：框架代码 `grep` 无新节点名字面量（`N_overview`/`N_market`… 只出现在 skill 产出/测试）；内核零改动或改动在 plan 显式列明并经审议。

---

## 7. 风险与开放问题（留给 plan 收敛）

- **R1（已定，同学实现）**："投指令给 bot、取结构化结果"的真值 = `OpenApiBotAdapter`（`OpenApiBotPort` 实现）：`send_and_wait_async`（plan/dispatch 同步 round-trip 取结果）+ `send_message`/`get_run`（execute 异步投递）。已落地于 `integration/open_api_bot_adapter.py`。
- **R2 回投路径**：真实引擎异步回调 vs 适配层同步回投。为确定性与 singlebox 可行性，倾向 **适配层时效内回投**（`deliver` 调 skill 取结果 → `POST /openapi/v1/collaboration/tasks/callback/report`），但真实异步回调仍需验证。需 plan 决策。
- **R3 并发锁模型**：HTTP 回投引入跨请求同 task_id 并发；框架注释指出 corp 单持久 loop 需切 `asyncio.Lock`。需 plan 验证 `threading.RLock` 在 singlebox 回投模型下是否成立，必要时切锁（最小 seam 改动）。
- **R4 确定性**：skill 产出需确定式（scaffold 而非自由 LLM），否则轨迹不可断言。singlebox 已有 mock model config 可配合。
- **R5 singlebox 启停开销**：重型、慢；需 gated + 可单独跑 + 复用 coverage harness。
- **R6 worker bot catalog 规模**：案例多角色 bot；需创建多少 worker bot、搜推 skill 如何映射 node→bot。需 plan 定。
- **OQ-1**：skill-backed 策略/投递激活方式——按 **能力** env `TASK_ENGINE=skill` 在 DI 装配时注入真实实现到 `ExecutionEngine`（非环境 flag、非引擎子类）；`OpenApiBotPort` 经 `ApiKeyProvider` 配置注入（local→localhost / prod→prod URL）。singlebox 起停属测试 harness。
- **OQ-2（已定，方案 A）**：验收 skill 宿主 = **owner bot**（`owner_bot_id`），对齐框架 §聚合收敛/§5；叶子自验收折叠进 execute 回投（worker）。
- **OQ-3（已定）**：coop_group 拉群走真实 BCS（singlebox 起栈含 BCS）；`form_coop_group` 对接 BCS `POST /groups`，`group_strategy` 三态对齐 `CollaborationRuntimeDefinition::{Chat,ManagerWorker,StateMachine}`。
