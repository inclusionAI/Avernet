# 评测门禁发布推进 seam（草稿态 → 预发）

- 日期：2026-10-19
- 状态：实现 PR in review（inclusionAI/Avernet #2828，`zhangyiaddress:yk_gate → dev`）。community 侧契约/seam 已落地；corp 端实现、MASA 评测链路、前端 internal overlay 在各仓独立交付。
- 参与模块：Backend `plugin_api/evaluation_gate`、`plugins/community/evaluation_gate`、`plugins/local/evaluation_gate`、`di/modules/infrastructure/{community,test}/evaluation_gate`、`di/profile_modules`、`core/service_bot/services/publish_flow_service`；Frontend-nextgen `extensions/empty`、`components/BotWorkshop/ServicePublicationDrawer`。
- 输入：PRD「工作台-评测」评测门禁方案 v5.3；评测门禁系分文档 §4.2.3（gate_result 结构）/§8.2（命中名单流程）/§14（不默认放行）。
- 约束：`AGENTS.md`、`CONTEXT-MAP.md`、`docs/arch/arch.rules.md`（Rule 14 配置驱动接线、Rule 20 local+prod 双实现、Rule 21 Noop/Mock、Rule 25 契约一致性测试）；`docs/arch/protocol-contract-tests.md`。
- 关联决定：Rule 14（DI 配置驱动）、Rule 20/21/25（Plugin Protocol 骨架）、D7（跨消费者隔离：未绑定 seam 的部署零回归）、D10（门禁总开关独立 DRM，回滚语义）。

## 1. 问题与目标

服务化 Bot 从「草稿态」推进到「预发态」（`DRAFT → BUILDING` 的 CAS）缺少质量门禁拦截：命中门禁名单的 Bot 可以不经评测直接预发。原始拦截入口在旧 open-claw 仓 ServiceBotList，前后端迁移到 Avernet/ocb 双仓模型后该能力缺失。

目标：在 Avernet（Open Core）发布流的最小决策面注入「评测门禁 seam」——草稿态推进预发前先做一次门禁决策，未放行则不推进、不留下半启动状态；命中阻断时把决策摘要写入发布单 `ext.gate` 供前端摘要与运维查询。

非目标（不属本 spec / 本 PR）：

- 门禁名单 / 策略 / 评测执行 / 判定 / 审批——全部由门禁平台侧承接，TC 侧不计算门禁是否通过（corp 实现透传平台结果）。本 spec 只定义 community 发布流的决策面。
- corp 端 HTTP 实现（门禁平台 `agent-gate-list` + 评测平台 `/eval/start`、`/eval/progress` + 审批 OpenAPI）——OCB `agentclaw.corp` 独立交付。
- MASA 评测侧 / eval_env 生命周期编排——`masaagenteval` 与 OCB corp 独立交付。
- 前端门禁 Dialog、开始评测 / 紧急发布的真实实现——OCB `apps/frontend-nextgen/src/internal/evaluation-gate` 独立交付。本 PR 在 Avernet 侧只暴露 public slot。

## 2. 方案

### A. 在发布服务里硬编码门禁调用

会被 bound 到具体平台契约，community（无门禁平台）与 baas/engine 等其它消费者被迫外呼。否。

### B. Optional DI 的 Plugin Protocol seam（推荐，采用）

`PublishFlowService` 以 Optional DI 参数（`evaluation_gate: Optional[EvaluationGatePlugin] = None`）消费门禁协议；契约只定义 community 发布流的最小决策面：

- `check_publish_gate(...)` → `GatePublishDecision`：草稿态→预发推进前决策。`allowed=False` 阻断，`reason` 带用户文案。
- `query_gate_policy(...)` → `GatePolicyInfo`：名单命中与模式查询（前端策略弹窗 / 运维查询用）。

接线（Rule 14 配置驱动）：community 形态绑定 allow-all 实现（不命中原发布语义）；test/singlebox 绑定本地 allow-all；未绑定的部署（baas/secbaas、engine 等其它消费者）默认 None → 完全跳过门禁（D7 跨消费者隔离）。corp 形态绑定 HTTP 实现（本 spec 不涉及）。

### C. 状态机前移阻断

仅在 `process()` 内 `current_status == PublishStatus.DRAFT` 分支、`DRAFT → BUILDING` CAS 之前插入门禁决策；非草稿态走原有 describe-only，不触达门禁（防回归由 `test_gate_not_consulted_for_non_draft_status` 锁定）。

否决项：把门禁判定挪到 describe 分支或抬到 process 顶部——会在非推进点误触发，且无法避免状态已推进而门禁拒绝的半启动状态。

## 3. 契约

### 3.1 `EvaluationGatePlugin`（Plugin Protocol）

`plugin_api/evaluation_gate.py`：

- 常量：`PLATFORM_TEAMCLAW = "TC"`；决策 status 取值 `GATE_ALLOWED / GATE_NOT_HIT / GATE_EVALUATING / GATE_EVAL_PASS / GATE_EVAL_FAILED_OBSERVE / GATE_EVAL_FAILED_ENFORCE / GATE_ERROR`。
- `GatePolicyInfo(enabled, hit, hit_type, policy_id, policy_name, mode, reason, raw)`：名单命中查询结果。`enabled=False` 表示 TC 侧门禁总开关关闭（策略视为未命中）。
- `GatePublishDecision(allowed, status, reason, hit, hit_type, mode, policy_name, failure_summary, report_url, data)`：发布推进决策。字段透传到发布单 `ext.gate` 与前端摘要。
- 两个 async 方法（`check_publish_gate` / `query_gate_policy`），prod 实现内部走 HTTP。

### 3.2 `PublishFlowService._check_evaluation_gate`

- gate seam 未绑定（`None`）或决策 `allowed=True` → 不拦截。
- 决策阻断 → 把决策摘要写入发布单 `ext.gate`（`status / checked_at / hit_type / mode / policy_name / reason / failure_summary / report_url / operator`），返回草稿态 `PublishFlowResult`（`data={"gate": gate_summary}`），不做任何状态推进。
- `ext.gate` 写为**合并**而非覆盖（幂等，reviewer L366）：只刷新上述决策面字段，保留编排已写的 `evaluation_id` / `eval_env_context` / `started_at`。重复 `publish_staging` 检查不清掉"已在评的评测"状态——放行后由 `EvaluationOrchestrator.query_progress` 回写终态。验证：`test_gate_block_ext_write_preserves_in_progress_eval`。
- 决策接口抛异常（非预期崩溃）→ fail-open：灰度期门禁平台抖动不应卡住发布主流程，记录 warning 后放行（`return None`，不落 `ext.gate`）。真正的"是否拦截"由 corp 侧 DRM 总开关 + 门禁名单两层控制（见 §6）；本层只兜底非预期异常。plugin 自身 handled 错误（如 corp `ProdEvaluationGate` 把 HTTP 错误转成 `GATE_ERROR` 决策返回）仍按其返回的阻断决策阻断——不在本 fail-open 分支。
- `ext.gate` 落库失败（`mutate_publish_ext` 抛异常）→ warning 吞掉，但阻断结果照常返回（摘要仍在 `result.data.gate`，避免有人误把落库失败回退成放行）。
- 推进点之前解析 `space_id`（`bot_service.get_bot`）；解析失败不阻断门禁判定（`space_id=""` 传给 gate）。

### 3.3 前端 publish_staging 拦截 slot（Open Core）

`extensions/empty.ts`：`appExtension.publishStagingGate: null`（Open Core 默认直通、无门禁）。slot 类型 `PublishStagingGateSpec`（`check / startEvaluation / emergencyPublish / Dialog`）+ `PublishStagingGatePolicy` + `PublishStagingGateDialogProps` 为 public 无业务语义接口；internal overlay（OCB）在组装期注入真实实现。

`ServicePublicationDrawer.tsx`：`publish_staging` 的 `run()` 前查门禁——若 `gate` 非 null 且 `gate.check()` 返回 `!allowed` → 弹 `gate.Dialog`（Suspense lazy load）、不调 `advance`；`gate=null` 或 `allowed` → 正常 `advance('prestable')`，Open Core 字节不变。

边界：`apps/frontend-nextgen/CLAUDE.md` Open Core 约束——public 侧仅 null + 类型，无 `@/internal` import、无内部域名、无内部注册表；`scripts/check-open-core.cjs` 校验零泄漏。

## 4. 接线收敛点

- community：`CommunityEvaluationGateModule` 绑 `EvaluationGatePlugin → DirectEvaluationGate`（`plugins/community/evaluation_gate.py`，allow-all）。
- test/singlebox：`TestEvaluationGateModule` 绑 `EvaluationGatePlugin → LocalEvaluationGate`（`plugins/local/evaluation_gate.py`，`@plugin_impl(mode=LOCAL, flavor=FAKE)`）。
- 注册：`di/profile_modules.py` 的 `_common_test_doubles()`（测试列）与 community `modules_for(profile)` 各自注册对应模块。
- corp：`CorpEvaluationGateModule` 绑 prod HTTP 实现（OCB corp，本 spec 不定义）。

## 5. 验收边界（已落测试）

| 测试 | 文件 | 守的契约 |
| --- | --- | --- |
| Rule 25 契约一致性 | `tests/community/contracts/test_evaluation_gate.py` | 绑定实现自身的契约面：community 列策略未命中、`allowed=True` 且 `status=GATE_NOT_HIT`、决策携带 reason |
| 未绑定 seam 零回归 | `test_publish_flow_gate.py::test_no_gate_seam_keeps_original_draft_flow` | gate=None → DRAFT→BUILDING 照常推进（D7） |
| 放行推进 | `test_gate_allowed_decision_advances` | `allowed=True` → 推进、`mutate_publish_ext` 不被调用、gate 拿到 space/bot/platform |
| 正式模式阻断 | `test_gate_blocked_enforce_stops_draft_advance` | `allowed=False` enforce → CAS 不推进、`ext.gate` 落摘要、返回 draft |
| 评测中 | `test_gate_evaluating_blocks_and_records_ext` | `evaluating` → 不推进、`ext.gate` 落『评测中』摘要 |
| 实现异常 fail-open | `test_publish_flow_gate.py::test_gate_crash_does_not_block_main_flow` | seam 抛异常 → 主流程照常推进 DRAFT→BUILDING、不落 `ext.gate`（灰度期不卡发布；真正拦截靠 corp DRM + 名单） |
| space 解析失败不绕门禁 | `test_gate_space_resolve_failure_does_not_skip_gate` | `bot_service.get_bot` 异常 → `space_id=""` 仍判门禁 |
| 非草稿态不查门禁 | `test_gate_not_consulted_for_non_draft_status` | BUILDING → `gate.calls` 为空、`mutate_publish_ext` 不被调用（防回归） |
| 摘要落库失败仍阻断 | `test_gate_ext_record_failure_does_not_unblock` | `mutate_publish_ext` 抛异常 → 仍阻断、`result.data.gate` 照常返回 |
| Open Core slot 默认 null | `test/extensionsPublishStagingGate.test.ts` | `appExtension.publishStagingGate === null`（D7 零回归）；slot 契约形状稳定（check/startEvaluation/emergencyPublish/Dialog） |

本地已跑：`tests/community/contracts/test_evaluation_gate.py` + `tests/community/core/service_bot/services/test_publish_flow_gate.py` → 10 passed（PYTHONPATH 跨 corp src + Avernet submodule src，无 venv 环境；CI 用 `uv run pytest`）。前端 jest（`npm run ci`）依赖 `node_modules`，本机未构建，由 CI 覆盖。

未覆盖（明确归属）：

- corp 端 HTTP 决策链（门禁名单 / 评测 progress / gate_result 结构化解析 / report_url 回退 / 审批 OpenAPI 透传）→ OCB corp `tests/corp/plugins/prod/test_evaluation_gate.py`。
- MASA 评测侧 eval_env 上下文 / gate_result 透出 → `masaagenteval` 仓测试。
- 前端组件级 Dialog / 开始评测 / 紧急发布行为 → OCB `apps/frontend-nextgen/src/internal/evaluation-gate` 测试。

## 6. 兼容性与回滚

- 契约影响：`appExtension` 新增可选字段 `publishStagingGate`，默认 null。既有消费方（spread `{...openCoreAppExtension}`）effect-safe；无 schema/migration（capability 纯前端 runtime 读取）。
- 状态机影响：DRAFT→BUILDING 的推进语义仅在「gate seam 已绑定且决策阻断」时改变；community/test/未绑定均不变。
- 回滚：internal overlay 把 `publishStagingGate` 置空 → 行为等同 Open Core；public slot 本身 infra-safe，无需回滚。corp 侧门禁总开关（独立 DRM `EvaluationGate.config`，D10）关闭时 prod 实现亦返回 not-hit/allowed 决策——回滚不依赖本 spec 侧改动。
