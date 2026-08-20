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
- Avernet PR: [#1238](https://github.com/inclusionAI/Avernet/pull/1238), OPEN.
- Conflict-resolution merge: `e9d4b4ed8` merges current
  `dev_refactory_collaboration` (`3d6531c5`) into the topic without rewriting
  or force-pushing branch history.
- OCB mirror PR: BLOCKED; its topic-branch push was rejected by remote project
  authorization.
- Planned titles:
  - Avernet: `feat(openapi): add public bot catalog endpoints`
  - OCB mirror: `feat(gateway): expose public bot catalog routes`
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec
- 人工意见模式: auto

## PR 判定

| 结果 | 证据 | 说明 |
|---|---|---|
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
