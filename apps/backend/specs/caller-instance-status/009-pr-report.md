# PR 收敛报告：caller-instance-status

## 范围
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/caller-instance-status-20261112`.
- Source repo: GitHub inclusionAI/Avernet; push/source remote explicitly `github`; internal mirror excluded.
- Head / base: `rebase/caller-instance-status-on-dev-20261112` / `dev`.
- Base (bottom): d0cdd023453055304418c6170897936f321746b3; implementation replayed on top via git rebase github/dev, no conflict.
- PR: https://github.com/inclusionAI/Avernet/pull/3914
- PR title: feat(backend): expose read-only caller instance status
- PR description sections: Problem / Solution / Validation / Compatibility and risk / Spec.
- Reviewers: no user-specified reviewers; OCB defaults do not apply to Avernet.
- Human comments mode: auto.
- OCB: excluded per user instruction; no worktree, gitlink change or PR.

## PR 判定
| 结果 | 证据 | 说明 |
|---|---|---|
| OPEN | PR #3914 | Repo, head, base and semantic metadata read back. |
| Local review/regression PASS | 003 / 003b reports | Independent signed-principal, health, no-write and logging tests. |
| ACI/CI PENDING | Head-specific checks on PR | Actual jobs started; local results are not remote results. |

## 自动意见
No remote review/comment at creation-time readback. Local independent review found inherited BaaS detail logging; a compatible per-read safe-log switch and six real MockTransport credential-sentinel regressions resolved it.

## ACI/CI
| Job/指标 | 状态 | 证据 | 说明 |
|---|---|---|---|
| Affected local suites | PASS | 002-code-report.md | 308 passed, zero failed/skipped. |
| API/architecture local suites | PASS | 002-code-report.md | 233 passed. |
| Independent final regression | PASS | 003b-regression-report.md | 43 passed, zero failed/skipped. |
| Focused executable changed lines | PASS | /tmp/caller-instance-status-coverage.json | 60/61 = 98.36%; BaaS inherited class exclusion is 0/0, not claimed as covered. |
| Focused runtime module lines | PASS | 002-code-report.md | 790/902 = 87.58%; not repository-wide coverage. |
| Local static/size/diff hygiene | PASS | 002 / 003 | No new unused-import/variable issues; inherited findings retained. |
| Plaintext secret scan | PASS | check_secrets.py --base github/dev --head HEAD | Synthetic marker literals were initially false positives; named marker fixture preserves assertions. No scanner rules changed. |
| Remote PR title, unit-test jobs, Singlebox | PENDING | PR checks | Monitor final pushed head; no pass inferred from pending. |

## 人工意见
No human review/comment at creation-time readback.

## 当前结论
- PR: OPEN
- 自动意见: CLEAR at creation-time observation; recheck latest head.
- ACI/CI: PENDING
- 人工意见: CLEAR at creation-time observation; recheck latest head.
- 下一步: observe actual checks and reviews for final pushed head; fix actionable failures, retain infrastructure failures as such.

## Remote round 1 — head 06361d0ca6
- Backend job https://github.com/inclusionAI/Avernet/actions/runs/43146388220/job/130945176613 failed on `test_explicit_user_id.py::test_the_pinned_number_of_operations_take_it`: new status route increases the exact user-scoped operation count from 298 to 299. JUnit: 18,628 executed, 18,615 passed, 1 failed, 12 skipped, 0 errors; fail-fast ended the remaining suite. Empty coverage XML cannot establish remote coverage.
- Fix: retain exact count assertions, update user-scoped count to 299 and Bot-path placement to 227 (one added path operation), with a route-specific explanatory comment. No thresholds or inventory checks removed.
- Local reproduction/fix validation: complete explicit-user-id contract suite 23 passed. Additional complete router admission/authorization/namespace/parity suites: 98 passed (75.76s).
- Other module jobs were successful through unchanged-scope skips, not full module test execution. Singlebox was still running at this observation; an older-head result cannot substitute for the next pushed head.
