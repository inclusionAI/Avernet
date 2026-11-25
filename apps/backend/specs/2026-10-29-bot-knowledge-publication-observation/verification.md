# Verification: 服务 Bot 分状态知识库选择

## REL20261103 target correction (2026-11-02; current delivery)
- User requested a separate PR targeting `REL20261103`; #3439 remains open against dev and is not retargeted/closed by this delivery.
- Initial `github/REL20261103` was `7cfeb03d36857cbc149033f4c6ebd85220a382c5`, equal to the validated dev base; it advanced during submission to `9fef6b298032d3ab0d4c544123a0d782f7d1afde` (#3440, eval Quality Task linkage). Rebased without conflicts and retained all upstream changes. Dedicated branch: `codex/bot-knowledge-stages-rel20261103`; no old Release history or feature behavior change is imported.
- All feature source/tests stay byte-identical to delivered `28a9b521db18f3bd868e8df7f4831955906b8ea0`; this target correction changes only delivery documentation. The current complete backend tree additionally contains upstream #3440 changes. Previous complete dev Backend CI (**28953 passed / 40 skipped / 6 subtests**, line **90.83%**, changed **93.33%**) and independent **327 passed** delta review are prior feature evidence, NOT a full CI result for the advanced release tree.
- Fresh final release-target regressions at source/test `d2d808779e700dc383ab56d29624dafef519e39d`: **207 passed** in 95.76 seconds, covering knowledge sampling/stages/projection, native publish tasks, DI, real HTTP admission, upstream eval endpoints/path conventions and Quality Task creation/router. Initial target run was **147 passed**; the final expanded group supersedes it. Log: `/tmp/avernet-knowledge-stage-rel20261103-rebased-tests.log`.
- The normal `github/dev` pre-push gate runs again for this release-target delivery without bypass. New PR remote CI is checked separately; no merge, deployment, DDL, backfill, private frontend PR or gitlink update.

## Dev-targeted port (2026-11-02; current delivery)
- User corrected the target to `dev` and requested a new PR. Original #3293 is already merged into `REL20261029` as `5572adf8aec428b3073500bc235189edb1bcbe0e` at 2026-11-02 15:19:01 Asia/Shanghai; no revert or deployment is performed.
- Initial contribution baseline: `github/dev@6d67835cf1c858773a0580b72eef048fa96f451b`; rebased without conflicts onto `github/dev@7cfeb03d36857cbc149033f4c6ebd85220a382c5` before delivery. Branch `codex/bot-knowledge-stages-dev`; only the net feature patch from #3293 is applied, not Release history.
- Existing dev agentswarm type imports and its exact public protocol-label scanner handling are preserved; four Release-only repair files are excluded. Feature source/test bytes match reviewed `a50d0629` except the native restart comment already advanced on dev, which is retained.
- Fresh complete dev Backend CI at source/test `64762722867c338aeb8787005067af85b27abf01`: **28953 passed, 40 skipped, 6 subtests passed**, 549.16 seconds; case pass rate **100.00% (28999/28999)**, line coverage **90.83%**, changed coverage **93.33% (322/345)**; original gate passed. Log: `/tmp/avernet-knowledge-stage-dev-full-ci.log`. Rebased source/test Head `a89082c5e59f708d1405058574d008b13bc6058e` has byte-identical backend files, tests and gate scripts; intervening dev commit changes unrelated frontend files only. Documentation follow-up does not change tested source.
- Independent dev-port delta review: **PASS, no P2+**, **327 passed** including HTTP, DI, Protocol, boot, native publish/rollback/restart and auxiliary projection. Feature source/test behavior matches reviewed Release version; native restart comment retains dev wording. Normal pre-push `github/dev` gate passed, without bypass or lowered thresholds; push reruns the same hook.
- Fresh public composer regression: **1 suite / 7 tests passed** on dev. A local existing dependency installation is reused; this is not a fresh public dependency-install/build acceptance. Previous paired private frontend 243 tests below remain historical paired evidence, not a delivered private frontend PR.
- No private OCB frontend publication/gitlink change, merge, deploy, DDL or backfill is included. Full Singlebox, real devices/prepub/prod and OceanBase concurrency/fault latency remain unverified. Prior Release sections below are historical, not new-Head proof.

## Final whole-feature review and delivery candidate (2026-11-02)
- Fixed target/base: `REL20261029@9fd60385f93f36fe819c8130f3f93525e844d29d`; source/test Head `e0d4f4a46837e060042f211d1d77ba57c44c7f48`.
- The requested independent review covered the whole three-dot feature diff and paired private frontend, followed by fixed-Head incremental review. One P2 was found: unknown stage state returned before the existing management Space admission. `_scope()` now runs before snapshot reads and any empty/catalog response. The native conversation authorization policy is unchanged.
- New real HTTP/normal DI/SQLite matrix: PERSONAL Owner without SpaceMember, verify/online, missing/corrupt/storage/recorded snapshots. RED: **8 failed** (unknown bypass and catalog-before-admission); GREEN: **24 passed** for the entire stage HTTP file, including all **8 new permission regressions**. Authorized unknown requests still return the exact empty body and no catalog calls; unauthorized requests retain `403 SPACE_MEMBER_REQUIRED`.
- Fresh final complete Backend CI: **28095 passed, 40 skipped, 6 subtests passed**, 320.08 seconds; case pass rate **100.00% (28141/28141)**, line coverage **90.72%**, changed coverage **93.37% (324/347)**. Existing gate passed, no thresholds changed. Log: `/tmp/service-knowledge-3293-final-e0d-ci.log`.
- Fresh paired frontend TypeScript `--noEmit` and **28 suites / 243 tests passed** at unchanged private Head `d0459b63b3f511079aede70e31b7de7568d23c82`; earlier lint/build evidence remains source-identical. No private frontend PR or OCB gitlink update is included in this public delivery.
- Final Standards: original P2 closed; no remaining P2+ or blocking documented-standards violation. Independent fixed-Head related boot/DI/Protocol/agentswarm/HTTP group **377 passed, 1 deselected**; independent new HTTP matrix **8 passed**. Deselected test writes generated files.
- Final Spec: PASS; no remaining Spec P2+. Fixed-Head HTTP/conversation/DI **58 passed**, paired two-GET admission probes **8 passed**, publish/rollback/provider probes **10 passed**, type-import regressions **82 passed**. Restoring only the old method in-process made all **8 new permission cases fail**, independently confirming defect detection. Authorized unknown exact-empty and native conversation access remain intact.
- Final Performance: PASS WITH RISKS, no new P2+. Scope admission adds one existing JOIN SELECT to authorized unknown reads (5→6); unauthorized reads now stop before snapshot/catalog (4→3). Known/draft successful query totals, bounded projection/pagination and zero-DML replay remain unchanged. Four real SQLite delta scenarios passed. Shared DB/driver latency remains unverified.
- Pre-push target is **`github/dev` as required by the latest workspace instructions**, not the PR Release target. Actual hook fetched dev (`c3d09b74202e6758187d2954f0585b8b4c314d13`, merge-base `b3e161e084e056789681d22ad7903e6d8ff8fed5`) and passed secret scanning plus Backend/BaaS/Engine SAST in the repository's default lint-only mode. Complete Backend CI above ran separately. Log: `/tmp/service-knowledge-3293-prepush-e0d.log`.
- The dev-based hook exposed inherited Release issues, not feature regressions: six F821 type-name errors in agentswarm were fixed with only existing type imports; a public `OPEN_API:app:<name>` caller reference was mistaken for an opaque credential. Scanner recognition is narrowly anchored to that address format, known-secret rules still run first, and malformed/opaque/known credential forms remain blocked. Scanner focused tests: **10 passed**; combined scanner tests: **43 passed / 1 failed**. The failure for `headers=unknown[0]` was independently reproduced with the unmodified Release scanner; it remains explicitly unresolved baseline debt, not a green combined suite.
- `git diff --check` passed. Changed production source files remain below the 1000-line limit. The inherited 1148-line agentswarm test file has unchanged length; no allowlist or gate was weakened.
- Delivery is bounded to updating existing public PR #3293, not merge/deploy/DDL/backfill. Full Singlebox, real devices/prepub/prod, corp startup, OceanBase high-cardinality latency/concurrency and driver-fault timeout remain unverified. Previous sections preserve historical failures/results and must not be read as current delivery status.

## Previous local revision (2026-11-02; superseded by final review above)
- Base `9fd60385f93f36fe819c8130f3f93525e844d29d` (`REL20261029`); final source/test head `fa2ebc90c9927271918115e54f067e20e9036658`.
- OCB frontend code/test head `d0459b63b3f511079aede70e31b7de7568d23c82`, isolated branch `codex/service-bot-knowledge-stages`; no gitlink update.
- Latest user clarification: unknown auxiliary history is a normal exact empty HTTP page `{ "items": [], "next_cursor": null }` for both existing queries. No unknown notice, diagnostic metadata or underlying error is exposed; normal admission/parameter/provider errors are preserved and unknown never writes disabled state back.

### Fresh local validation
- Feature storage/HTTP/native task group: **219 passed** (`/tmp/stage-restart-isolation-tests.log`). Includes real DB missing/corrupt/missing-table HTTP matrix, precise empty body/no catalog calls/no draft write, 100-ID unique-key read under 1/1000/10000 disabled histories, one bulk insert at 1/100 missing rows/external tenant, transactional failure rollback, replay zero-DML and two-writer auxiliary consistency.
- Native publish-boundary / durable HTTP / task regression group: **67 passed** (`/tmp/stage-native-restart-rollback-tests.log`): build/release/rollback/re-promote/restart/recreate flows through existing test assembly; no real device acceptance implied.
- Assembled frontend: **28 suites / 243 tests passed**, TypeScript `--noEmit`, lint and Bigfish production build passed. Build retains a bundle-size warning. Public composer regressions preserve native team entity while knowledge requests use real Owner; missing team Owner does not guess.
- Local browser smoke uses the actual KnowledgePanel plus an external catalog fixture, not a deployed backend. Exact empty response shows ordinary empty list with no unknown/error notice. Screenshot: `/Users/qiaoyifan/.codex/visualizations/2026/11/02/service-bot-knowledge/unknown-empty.png`; temporary server/tab stopped. Earlier unknown-notice screenshot is obsolete and not current evidence.
- Additional real CommunityDatabase(SQLite) cross-version concurrency regression was RED against `0ebfcc268` and GREEN after auxiliary marker CAS; the **22 state tests passed** (`/tmp/stage-cas-green.log`).
- Local backend SAST block scan and `git diff --check` passed without lowering thresholds. Complete Backend CI at final source/test head: **28087 passed, 40 skipped, 6 subtests passed**, 301.10 seconds. Case pass rate **100.00%** (28133/28133), line coverage **90.72%**, changed coverage **93.31%** (321/344); existing gate passed. Log: `/tmp/stage-full-backend-ci-cas-final.log`.

```bash
PATH=/Users/qiaoyifan/.codex/worktrees/bot-knowledge-stage-best-effort/Avernet/apps/backend/.venv/bin:$PATH \
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=8 \
bash apps/backend/scripts/ci_test.sh \
  --base 9fd60385f93f36fe819c8130f3f93525e844d29d \
  --head fa2ebc90c9927271918115e54f067e20e9036658
```

### Review fixes and isolation boundary
- Auxiliary projection explicitly uses `transactional_orm_session`, not a potentially AUTOCOMMIT ordinary session. Only auxiliary marker/binding writes participate; native publish rows are never locked or mutated.
- Online bindings use complete unique-key probes then in-memory space/flag checks; missing bindings use one bounded Core insert with explicit trusted source-tenant stamp; identical projection replay produces no business DML.
- Real `rollback_flip` regression covers old marker present/absent. Pre-sync identity capture is compared inside the auxiliary write, so a paused old progress callback cannot endorse a new rollback token. The token reuses native rollback origin/timestamp; no ext field is introduced.
- Independent review additionally proved an old restart poll beginning after early rollback admission could bless old restart SUCCESS. The minimal correction removes all knowledge dependencies/calls from plain restart polling. Same-publication in-place restart retains its existing knowledge projection. True rollback still writes a new `publish.online`, enters `ONLINE_PUB` and uses the existing publication progress poll after actual deployment success.
- Recreate/retry that changes publication identity without a publication-progress observation may conservatively leave the optional display empty. No restart-specific transaction, ledger, worker or compensation is added.
- Independent Performance also reproduced a Community SQLite cross-version writer overwriting a newer marker because SQLite ignores FOR UPDATE. Final marker writes now CAS the old value; a lost race rolls back the entire auxiliary transaction. This adds no native lock, schema, retry or background task. The permanent real CommunityDatabase concurrency test pauses old v2 before its first DML, completes v3, resumes v2, and asserts v2 fails without changing v3 marker/bindings.
- Online publication progress adds one optional pre-sync publication-PK read (plus one origin-PK lookup for rollback); verify and plain restart add no such I/O. Identity-read failure skips auxiliary projection but still runs native sync. Exception isolation is not hard latency/worker isolation; shared DB waits remain a deployment concern.

### Prior runs and failures retained
- Initial source/test head `d6034a01f`: **14 failed / 28048 passed / 40 skipped / 6 subtests**. Twelve native test harnesses omitted the new provider, one boot test expected the removed URL, one read audit included unrelated native background initialization writes. Tests were corrected without replacing production assembly.
- Intermediate head `b54c7fe53`: full Backend CI **28062 passed / 40 skipped / 6 subtests**, line coverage **90.73%**, changed **93.90%** (277/295); superseded by subsequent production fixes, not reused as final proof.
- Intermediate head `67d627d12`: full Backend CI **1 failed / 28085 passed / 40 skipped / 6 subtests**. The new missing-table test used raw engine DROP outside the local DatabasePlugin lock while native background sessions shared SQLite StaticPool. It now executes real DROP through `session.connection()` under the plugin lock, retaining all original assertions. Failed log retained at `/tmp/stage-full-backend-ci-67d627d-failed.log`.

- Intermediate head `0ebfcc268`: complete Backend CI **28086 passed / 40 skipped / 6 subtests**, line coverage **90.73%**, changed **93.26%** (318/341), gate passed. Superseded only by the final auxiliary CAS fix/test and not reused as final proof.

### Fixed-head independent review closure
- **Standards:** final CAS delta `0ebfcc268...fa2ebc90c` has no new P2/P3; auxiliary-only CAS/rollback boundary and real CommunityDatabase test shape confirmed. Final full source/test Head is the same as the successful CI above.
- **Spec:** no new P2+; stale-restart case closed by removing observer calls from restart polling. Independent native probes cover marker absent/present, ordinary restart preservation and true rollback publication-progress completion. Independent final CAS/failure/replay probes: 3 passed.
- **Performance:** PASS WITH RISKS, no new P2+; old cross-version P2 closed. Independent final related tests **121 passed**; original cross-version probe **3/3**, plus missing-old-binding INSERT-before-failed-CAS probe **3/3**, show no stale marker/binding survives. Final 100-ID reads remain 3 SELECT / 2020 SQLite VM steps at disabled history 1/1000/10000, missing row creation remains one bulk binding INSERT at 1/100, replay has 0 DML. CAS replaces the marker UPDATE without added SELECT or scans. Per-active-task 8-second polling cost and production lock/latency uncertainty remain explicit, not a deployment claim. Evidence: `/tmp/avernet-perf-fa2ebc90c`.

### Live read-only schema/plan evidence
- ODC confirmed existing `online_enabled` is `tinyint(1) NOT NULL DEFAULT 0`; no DDL or backfill performed.
- Actual native publication index is `idx_sbp_e_s(source_bot_pk,env,status)`; sampled online/verify EXPLAIN used RANGE SCAN + TOP-N (estimated one row), not a full table scan. This is a sample plan, not high-cardinality OceanBase latency acceptance; the ORM-only proposed index was not claimed deployed.

### Current acceptance mapping / exclusions
AC-01 draft/CAS/Editor and personal wire compatibility; AC-02 bounded BUILDING capture; AC-03 native stage provenance; AC-04 isolated actual-publish completion; AC-05 rollback target with no draft overwrite; AC-06 same existing APIs/current grant and ready admission; AC-07 exact empty unknown response; AC-08 20/page and 100/snapshot bounds; AC-09 existing schema/no history migration; AC-10 stage propagation/readonly/empty frontend/lazy conversation are covered by the groups above and independent review below.

No push, PR update, merge, deployment, production writes, DDL or history backfill. Full Singlebox, real prepub/device/AGENTS.md acceptance, corp startup and production fault-timeout behavior remain unverified. Old sections below are explicitly historical and do not describe current API semantics.

## Historical Delivery Verification (superseded by 2026-11-02 revision)
- Historical GitHub Release base: `ed61d43cbf0da75b04de2d872aa4eccdc0ddc8c7` (`REL20261029`).
- Tested source/test head after rebase and review follow-up: `1e276db8d13520127e53f112e901283ad3af8c0d`.
- Follow-up changes are limited to HTTP README boundary/response semantics and permanent Protocol/concrete registration in `test_service_api_conformance.py`; no feature behavior was changed.
- Focused conformance/architecture/DI/boot/HTTP checks: **237 passed**, 0 failed before rebase.
- Fresh full Backend CI after rebase: **27909 passed, 40 skipped, 2 subtests passed, 0 failed**, 439.67 seconds. Existing JUnit gate: 100.00% (27951/27951); line coverage: **90.71%**; changed-line coverage: **99.19%** (123/124). All existing thresholds passed unchanged.
- Independent incremental review confirmed both previous gaps are addressed and found no new P2+; range-diff shows the original eight feature commits remain patch-equivalent, with only upstream surrounding context changing in the follow-up registry patch.
- Source/test files remain identical to the tested head after this documentation-only verification update. Push, remote CI and deployment are separate evidence, not implied by local CI.

```bash
PATH="$PWD/apps/backend/.venv/bin:$PATH" \
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=8 \
  bash apps/backend/scripts/ci_test.sh \
  --base ed61d43cbf0da75b04de2d872aa4eccdc0ddc8c7 \
  --head 1e276db8d13520127e53f112e901283ad3af8c0d
```

### Release Integration Limitation
The new Release supports resolving an existing personal space for a legacy Bot
whose persisted `space_id` is null. The historical implementation used the persisted
space ID, so such a legacy service Bot could not be captured and returns the
unrecorded result instead; native publication remains unaffected. This is an
historical limitation, corrected in the current revision by using the existing effective-space resolver; no history migration is introduced.

## Historical Initial Verification Scope
- Base: `2508dcabaa5f533321325e0549b724cd916650d9` (`REL20261029`).
- Tested code/test head: `223090213aff10bf90d9885daa674e6f7bdf1858`.
- Worktree: `feat/bot-knowledge-stage-best-effort`；原 PR #3177 未修改。
- 正常组装：现有 DatabasePlugin/common-config Repository、普通 Service API、既有构建任务及新增只读路由。没有生产 DDL、配置切换、合并或部署。

## Focused Validation
| 验证 | 结果 |
| --- | --- |
| 版本记录最初红/绿 | 缺少实现时导入失败；实现后 41 passed |
| 辅助表缺失回归 | 使用真实 SQLite DROP 辅助表，记录返回 False、查询 unavailable；通用配置 get 的历史兼容语义不变；42 passed |
| 构建任务 + 正常 DI | 48 passed；provider 构造、记录异常均不改变原生阶段结果，真实构建失败仍 Fail |
| 当前选择 + 任务 + DI + 模块/API 架构 | 171 passed（Group A 时点） |
| 注册 HTTP + 受控流程 | 11 passed；实际 DI/HTTP/DB、原生 Repository CAS 与 rollback_flip、现有 GET/PUT、mutation witness、路由唯一性 |
| 新架构 guards + 模块/API 架构 | 15 passed |
| 最终定向功能集合 | 172 passed；包含 1/1000/10000 禁用绑定、100/101 启用边界、个人 Bot 回归 |
| 完整门禁发现问题后的 boot/architecture/tasks 回归 | 79 passed |
| 查询数量/上限/个人 Bot 单独验证 | 6 passed |
| Backend local SAST block rules | passed，现有脚本 + flake8 fallback；未运行公司远端 SAST |
| diff whitespace / 源文件行数 | passed；7 个修改/新增生产 Python 文件均小于 1000 行 |

## Initial Full Backend CI

在代码与测试 head `223090213aff10bf90d9885daa674e6f7bdf1858` 完成第二轮完整运行：

- **27650 passed、40 skipped、2 subtests passed，0 failed**，308.49 秒。
- JUnit case pass rate **100.00%**（27692/27692，按既有 report_check 计算）。
- 全量行覆盖率 **90.65%**，门槛 75%。
- 变更行覆盖率 **99.19%**（123/124），门槛 80%。
- 原有 Backend CI/report gate **passed**；没有降低任何门槛。
- 初次验证后的 `6aec60cb3` 仅更新 SDD/验证文档；后续 review 修复与 Release rebase 的验证以本文 Current Delivery Verification 为准。

```bash
# Run from repository root with the backend virtualenv on PATH.
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=8 \
  bash apps/backend/scripts/ci_test.sh \
  --base 2508dcabaa5f533321325e0549b724cd916650d9 --head 223090213
```

原始报告保存在本 worktree 的 `apps/backend/pytest_report/TEST-junit.xml`、
`TEST-cov.xml`；它们是本地生成物，不提交进仓库。

## Cost Evidence
- 在 1 / 1000 / 10000 条额外禁用绑定下，TEAM Owner 采集均为 5 SELECT + 1 独立 upsert；按版本读取均为 5 SELECT、0 写入。
- 生产采集 SQL 含 tenant/env/source PK/space/enabled 条件及 LIMIT 101；SQLite 查询计划命中覆盖索引 `idx_bk_enabled`。
- 100 个启用 ID 可记录；第 101 个导致辅助记录失败，不产生部分记录，不影响原生任务。
- 已完成构建阶段的重放不再次解析记录 provider，也不新增辅助写入。
- 以上是本地 SQLite 查询数/索引证据，不是 OceanBase 实测执行计划或延迟保证。

## Review and Repairs
- Group A：Standards 无确认缺陷；Spec 发现缺表被误判为未记录的 P2，严格读取修复并经独立增量复审；Performance PASS WITH RISKS。
- Group B：Standards、Spec 均无新的确认缺陷；Performance PASS WITH RISKS。运行时成本常数、有界，不新增调度或逐 ID I/O。
- 首轮完整 CI：27642 passed、40 skipped、2 failed。两个新增失败是新路由未加入 boot 精确集合，以及 Core 类型引用 API 导出层违反架构规则。已最小修正；前者在干净 base 中单独通过，不将其伪称为基线失败。
- 修复只调整 boot 期望集合和 Core Protocol 引用，不修改构建/发布状态机或事务。最终增量 Standards / Spec 均无新的确认缺陷；Spec 评审者独立运行新增 6 个回归和 86 个 boot/task/DI/architecture 用例通过。最终 delta 只有类型引用修正及测试/文档，不再次触发新的 Performance 评审。

## Acceptance Mapping
- AC-01：现有 GET/PUT 注册用例、个人 Bot/当前选择与 CAS 回归。
- AC-02/03：独立采集、版本读取、当前草稿变更不改旧版本；上线只读无复制。
- AC-04：响应仅含观察状态，不给原生发布状态赋值；失败/非 BUILDING 不补写。
- AC-05：空数组、缺失/无效及存储失败分开；无草稿 fallback。
- AC-06：存储/provider 故障注入、原生任务结果、真实构建失败保留。
- AC-07：源 PK、业务 ID、租户、环境、空间、当前 Owner/Editor 及历史 Owner 测试。
- AC-08：历史未记录与原生 rollback_flip 选择旧版本；当前勾选不被恢复。
- AC-09：同一版本重建覆盖；保存失败保留原采集时间，仅称 last_recorded。
- AC-10：没有改变现有 AGENTS.md、MCP 授权、原生文件/发布/恢复实现。

## Remaining Boundaries
- 未新增前端接入，因此不宣称页面已展示分阶段开关。
- 未运行完整 Singlebox 或真实设备 AGENTS.md 验收；保留既有 bot_knowledge pending 和全部门禁阈值原样。
- 未验证真实预发/生产、OceanBase、内部集成包、生产 profile 启动或实际 DB 故障延迟。
- 可复核的 DatabasePlugin/公有插件与当前内部适配器代码没有提供本功能独立短截止时间；真实驱动/代理配置未知。
- 异常隔离不等于时间隔离，数据库等待仍可能占用原有构建 worker；整个底层数据库故障仍会影响原生流程。
- common-config 操作员保留其原有管理权限；记录不是防篡改审计或授权依据。
- 没有自动回填、补偿或恢复草稿勾选；同一发布单重建失败可保留带时间的上次记录。
