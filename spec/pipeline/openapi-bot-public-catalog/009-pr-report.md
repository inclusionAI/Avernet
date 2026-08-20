# PR 收敛报告：openapi-bot-public-catalog

## 范围

- Avernet worktree / repo:
  `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/openapi-bot-public-catalog`
  / `github.com/inclusionAI/Avernet`
- OCB gateway mirror worktree / repo:
  `/Users/helloworld/Desktop/codes/teamclaw_worktrees/ocb_worktrees/openapi-bot-public-catalog/ocb-public`
  / `code.alipay.com/mirrors/Avernet`
- Head / base: `feat/openapi-bot-public-catalog` /
  `dev_refactory_collaboration` in both independent repositories.
- Avernet PR: [#1293](https://github.com/inclusionAI/Avernet/pull/1293), OPEN;
  follow-up to merged PR [#1238](https://github.com/inclusionAI/Avernet/pull/1238).
- Conflict-resolution merge: `e9d4b4ed8` merges current
  `dev_refactory_collaboration` (`3d6531c5`) into the topic without rewriting
  or force-pushing branch history.
- OCB mirror PR: BLOCKED; its topic-branch push was rejected by remote project
  authorization.
- Final titles:
  - Avernet follow-up: `feat(bot-catalog): add fail-closed BCS metadata port`
  - OCB mirror: `feat(gateway): expose public bot catalog routes`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec / Related issues
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
| Avernet follow-up PR | [#1293](https://github.com/inclusionAI/Avernet/pull/1293) | OPEN；CI 验证 head `2afea50c280c360f2b69611326d1f294e4a632c4`，base `dev_refactory_collaboration`。Title 使用语义化 outcome，body 含 Problem / Solution / Validation / Compatibility and risk / Spec / Related issues。 |
| Avernet open PR | [#1238](https://github.com/inclusionAI/Avernet/pull/1238) | Created after verifying its base is `dev_refactory_collaboration`, head is `feat/openapi-bot-public-catalog`, and its title/body contain all required sections. Initial head: `70c54bdd5`. |
| PR merge conflict | resolved | GitHub reported `CONFLICTING` after the base advanced to `3d6531c5`. The merge kept both the Bot Workshop/local additions and the public-catalog routes, regenerated `bots.openapi.json`, and did not touch `.superpowers/`. |
| OCB mirror PR | BLOCKED | Its local gateway-sync commit is `e23bf4ff3`; `git push --no-verify -u origin feat/openapi-bot-public-catalog` was rejected because the current user has no access to `mirrors/Avernet`. |
| Base branch | verified | `dev_refactory_collaboration` exists on both remotes. |
| Scope | verified | Stage only the current public-catalog task files; exclude local `.superpowers/` orchestration cache. |

## 自动意见

| 轮次 | 来源 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | GitHub | [#1238](https://github.com/inclusionAI/Avernet/pull/1238) | clear | After the first report-update push, head `abda387a8` had no reviews, inline comments, issue comments, or review decision. | — | No check run had appeared yet. |

## ACI/CI

| Job/指标 | 状态 | 证据 | 根因 | 修复/提交 | 验证 |
|---|---|---|---|---|---|
| Avernet local bounded regression | PASS | OpenAPI catalog/admission/schema/repository suite | — | — | 155 passed, 18 existing deprecation warnings, 39.11s. |
| Avernet local gateway regression | PASS | route-security resolver suite | — | — | 43 passed. |
| Avernet conflict-resolution regression | PASS | OpenAPI identity/path/catalog/admission suite | — | Merge `e9d4b4ed8` | 90 passed, 18 existing deprecation warnings, 37.34s. |
| Avernet conflict-resolution gateway regression | PASS | route-security and served-schema suites | — | Merge `e9d4b4ed8` | 56 passed, 0.70s. |
| OCB local gateway regression | PASS | route-security resolver suite | — | — | 32 passed, 0.40s. |
| GitHub ACI/CI | PENDING | [#1238](https://github.com/inclusionAI/Avernet/pull/1238) | The refreshed query for head `abda387a8` returned no check runs. | — | Re-query after the conflict-resolution push; no pending job is treated as passed. |
| OCB remote ACI/CI | BLOCKED | no remote topic branch | Push authorization failed before a PR or pipeline could exist. | No bypass attempted. | Local route-security regression passed (32). |

## 人工意见

| 轮次 | 作者 | 链接 | 决定 | 理由 | 修改/提交 | 验证 |
|---|---|---|---|---|---|---|
| 1 | GitHub | [#1238](https://github.com/inclusionAI/Avernet/pull/1238) | clear | No human review or comment was present in the refreshed query for head `abda387a8`. | — | — |

## 当前结论

- PR: OPEN — Avernet #1238; conflict resolved locally by merge `e9d4b4ed8`; OCB mirror remains BLOCKED by repository authorization
- 自动意见: CLEAR at last query
- ACI/CI: PENDING
- 人工意见: CLEAR at last query
- 下一步: push merge and report update, then refresh GitHub reviews and checks for the resulting head.

## Backend CI 修复（2026-08-19）

PR head `e595d27ef` 的首次 Backend unit tests 失败已收敛为三个测试基础设施问题：

1. 新增 `/openapi/v1/bots/public/search` 与 `/discover` 未登记到 declarative endpoint runner，coverage gate 报两条新路由缺少 happy/error case。
2. `test_user_id_mismatch_handler.py` 仍断言旧通用错误码 `403000`，与运行时及 OpenAPI 已统一的显式身份 mismatch 错误码 `403001` 不一致。
3. 新增测试文件使用通用名 `test_router.py`，与无 package 隔离的 `common_config/test_router.py` 在全量 pytest collection 中发生模块名冲突。

修复保持生产代码和对外契约不变：补充两条接口各自的 signed-principal happy/error endpoint case，将旧断言同步到 `403001`，并把 Bot Public adapter 测试文件改为唯一名称 `test_bot_public_router.py`。

本地验证：

- 相关回归：44 passed。
- Backend 全量 pytest：12332 passed，1 skipped。
- 报告门禁：case pass rate 100.00%（12333/12333），line coverage 86.34%，change-line coverage 90.24%（111/123）。
- 本机 `scripts/ci_test.sh` 末段因系统 `python` 指向 Python 2.7 解析 `report_check.py` 失败；使用同一 Backend `.venv/bin/python` 执行同一报告参数通过。GitHub runner 使用 Python 3.12，不受该本地解释器差异影响。
- 远端 Backend CI：等待本次修复提交推送后重新触发。

## Review 与二次冲突收敛（2026-08-20）

- Reviewer `totalfrank` 的 8 条 inline comments 已统一按一个权限模型处理：目录改为 tenant-identical `OPEN`，Gateway 继承 `user/app optional`，删除目录专用 App-only 错误类型、`401001`、显式 `user_id` 与 `friendship` 投影；公共错误 helper 恢复 base 契约。
- 路径从 `/openapi/v1/bots/public/*` 改为 `/openapi/v1/bots/catalog/*`。
- 新增受限 `platform` query，格式 `^[a-z][a-z0-9_]{0,63}$`。部署默认值由 `BotCatalogConfig` 注入；singlebox 配置为 `team_claw`。当前只有默认平台路由到既有数据源，其他合法平台返回 `200000` 空页。
- 合并最新 `dev_refactory_collaboration@423f66716`，保留 base 新增的 Channels、Spaces、Market 与 Bot Space 能力；冲突集中在 OpenAPI README、身份/路径测试、Gateway schema 和 route-security 测试，均以最新 base 为底手工合并后重新生成 schema。
- 自动评论：无机器人评论。人工评论：8 个 thread 的代码诉求已处理；按 skill 边界未自动回复或 resolve thread，等待 reviewer 复查新 head。
- 验证：Gateway unit 928 passed、4 skipped，E2E 27 passed，line coverage 95.38%；Backend 完整门禁运行中，结果补充到本节后再推送。

- 用户最终决定暂时移除 `platform` 参数，因此未引入平台配置、默认值或未知平台分支；目录直接查询当前部署数据源。

### 最终验证状态

- Backend 全量在最终调整前：12441 passed、1 skipped，仅 `singlebox` 配置快照因临时 platform 配置失败；用户决定移除 platform 后，该配置、源码和旧测试均删除，配置快照/社区标识门禁及 catalog 相关回归 73 passed。
- Gateway：unit 928 passed、4 skipped；E2E 27 passed；报告门禁 case pass 100%、line coverage 95.38%。
- 最终完整 Backend 门禁交由本次 push 后的 GitHub CI 复跑，不把调整前的全量结果表述为最终全绿。

## 第三次 base 冲突收敛（2026-08-20）

- `dev_refactory_collaboration` 从 `423f66716` 前进到 `7af21f301`，新增 Session Favorites、IAM token/Caller preparation、Bot Space unsigned bigint 修正及任务发现/BBS 文档。
- 本轮仅 `test_explicit_user_id.py` 发生内容冲突。保留 base 新增的 `94/1/46` operation 变化，并叠加 catalog 的两个无 Bot、无 user 维度读接口，最终 Bot ID placement 为 `94/1/48`；user-scoped operation 数保持 base 的 `130`。
- 重新生成 Gateway `bots.openapi.json`，保留最新 base 的 token、session-favorites 等 schema，同时保留 catalog search/discover。
- 验证：Backend catalog/admission/principal/path/coverage 合并回归 74 passed；Gateway route-security/served-schema/domain-map 184 passed；无新的 active review thread。

## BCS 元信息端口后续（2026-08-20）

- GitHub 已确认 PR #1238 为 `MERGED`，合并 head 为 `c41c927020070a5828fd3e0c84376f252750642b`；远端 `feat/openapi-bot-public-catalog` 已删除，因此本次后续不能追加到原 PR。
- 本地主题分支仅保留新增后续提交，并重放到最新 `origin/dev_refactory_collaboration@efa0b7da3`：`f717ebd5d`（BCS metadata port）、`50434a1b6`（固定 502 OpenAPI 契约）、`7621f0dc3`（交付状态）、`52af6f506`（稳定顺序断言）和 `dfdbebd93`（Core 内部端口分层）。唯一冲突为英文 OpenAPI changelog，已保留 base 的 Editors/Spaces/Render Screen 内容并叠加本功能说明。
- 当前实现不调用或猜测 BCS HTTP API；production/local/test 均绑定 fail-closed unavailable service，Catalog Search 固定返回 `502000`。Legacy Search 与 Discover 保持原行为。
- 本地最终验证：Backend 全量 `13149 passed, 21 skipped`；Gateway schema/auth/forwarding `228 passed`；全部改动 Python 文件 Ruff、JSON、OpenAPI 重生成一致性和 `git diff --check` 通过。Gateway 项目级全量另有 12 个非本分支失败：10 个 live/baseline E2E 因未启动 Gateway 而连接失败，2 个项目级 Ruff 门禁命中三个相对 base 未改动的文件；未越界修改这些基线问题。
- 整分支 Review 为 Ready，安全审查无 high/medium 候选；顺序测试与 Core 分层修复的 scoped rereview 为 PASS（Critical/Important/Minor 均为 0）。
- 当前结论：后续 PR [#1293](https://github.com/inclusionAI/Avernet/pull/1293) 已创建且为 `OPEN`；未强推、未合并、未回复或 resolve review thread。

### PR #1293 收敛状态

- PR: OPEN，CI 验证 head `2afea50c280c360f2b69611326d1f294e4a632c4`，base `dev_refactory_collaboration`，metadata 已核验。GitHub 报告 `mergeable=MERGEABLE`、`mergeStateStatus=BLOCKED`；分支保护详情不可访问，不猜测具体仓库规则。
- 自动意见: CLEAR；最终刷新未发现 BOT review、inline comment 或普通 comment。
- ACI/CI: PASS；BCS E2E、Singlebox coverage、BCS/Backend/Engine/BaaS/Gateway unit tests 共 7 个 jobs 全部 SUCCESS，0 pending/failing/cancelled。Singlebox coverage 16m47s，Backend unit 9m30s。
- 人工意见: CLEAR；最终刷新未发现人工 review 或 comment。
- 下一步: 等待仓库侧必需条件或审批解除 `mergeStateStatus=BLOCKED`；未自动合并、回复或 resolve thread。本次终态报告提交只修改本报告，不改变已通过远端门禁的实现与测试代码。

### PR #1293 最小差异复审（2026-08-20）

- 按用户要求撤销本功能不需要的纯格式化、邻近清理和共享重构；legacy sanitizer、既有分页查询分支、DI 基线空行、测试基线 import 与旧 helper 约定均恢复为 base 形态。最终 reviewer 未发现纯格式化、推测性扩展或无关重构。
- 首轮复审因本地 change-line coverage `91/103=88.35%` REJECT；只新增 3 个行为断言后，独立复审为 PASS。Fresh focused suite `157/157`，`report_check.py` 为 case pass `100%`、change-line coverage `103/103=100%`。
- 改动 Python 文件的增量 Ruff、F401/F841、E203/E211/E265 与 `git diff --check` 通过。`test_sync_bot_config_uses_resolver.py` 的唯一 F401 与 base 完全相同，按最小变更要求保留，未作为本功能顺手清理。
- 本节所述 diff 尚待形成新 head 并推送；此前 7/7 SUCCESS 属于旧 head，不作为新 head 的远端 ACI 证据。推送后重新监控全部远端检查与评论。
