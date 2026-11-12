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

## Final delivery head and request template
- Current PR head: `b6167ad5a73e7eb64d3eb0651a97d7567f41ce70`; base: GitHub `inclusionAI/Avernet:dev`. The latest head contains the exact inventory pin correction; previous-head results are historical evidence only.
- The remote correction follows 23 explicit-user contract tests and 98 router inventory/namespace/parity tests, all passed.
- Final checks: Backend run `43148622567` / job `130952282155`; Singlebox run `43148622546` / job `130952395672`. Both were running at this observation.
- Request template below assumes the normal authenticated public gateway client/required authentication headers; it embeds no credentials and has not been executed against production:

```bash
curl --get "$PUBLIC_API_BASE/openapi/v1/bots/$BOT_ID/caller-instance/status" \
  --data-urlencode "user_id=$USER_ID" \
  --data-urlencode "owner_id=$OWNER_ID"
```

`owner_id` is the compatibility alias of addressed `entity_id`; use the actual Bot owner/entity. Standard envelope `data` contains exactly `{"exists": true, "alive": false}` for an allocated but non-live Caller. Human or mixed principals cannot query another subject by changing `user_id`; pure applications still require the addressed-Bot grant.

This report's final CI observations are maintained locally without further report-only pushes, keeping the checked head stable.

## Remote round 2 — same final head 0396a07c20
- Backend first attempt https://github.com/inclusionAI/Avernet/actions/runs/43148622567/job/130952282155 failed on the unrelated existing Task dashboard event-loop test. JUnit: 19,234 executed, 19,221 passed, 1 failed, 12 skipped, zero errors. Fail-fast stopped remaining tests; coverage XML is empty, so no remote coverage result is claimed.
- Failure: `test_read_event_loop.py::test_slow_dashboard_reads_do_not_block_other_requests[repository-/openapi/v1/collaboration/tasks/dashboard-params0]`, assertion `[False] == [True]`. The test worker's release wait is 0.5 seconds (the separate start wait is 2 seconds). The exact reason for scheduling delay is not proven.
- Baseline check: this test and Task public/internal read handlers have zero diff against github/dev; the handler already uses run_in_threadpool for synchronous repository and artifact reads.
- Reproduction: complete event-loop suite 22 passed (0.57s); all ten dashboard parameters repeated in three fresh runs, 10 passed each (0.26s each). No timeout, fixture assertion or business behavior was changed.
- Bounded flaky retry: only this failed Backend job was retried once, same head, using gh run rerun --job. Replacement Backend job `130960938243`, run `43148622567`. No further retry is automatically authorized by this report.
- Low-sensitivity evidence: `/tmp/caller-instance-status-ci-0396a07c20/TEST-junit.xml` and `/tmp/caller-instance-status-backend-job0396.log`.
- Current CI: PENDING for the one bounded Backend retry and same-head Singlebox run `43148622546`.

## Singlebox exact coverage failure and acceptance correction
- Final-head Singlebox job https://github.com/inclusionAI/Avernet/actions/runs/43148622546/job/130952395672 failed its ExpertChat core non-regression gate: 565/936 = 60.36%, below the unchanged 61.18% threshold. Acceptance stories themselves passed, as did BCS endpoint 181/181 and CLI 66/66 gates. Other module metrics are not substituted for the failed ExpertChat gate.
- Add one live HTTP acceptance story with three assertions: no ledger → false/false without creating a ledger; pending ledger without UUID → false/false with identical full row; registered unknown UUID → actual BaaS 404/RELEASED → true/false with identical full row. No Caller container is created, restarted or upgraded.
- Add the new GET to the existing ExpertChat router denominator. Coverage thresholds and source denominators remain intact.
- Local validation: manifest validation, story collection, static/diff hygiene and 36 affected tests passed. Full local live Singlebox was not run: this checkout lacks Python environments, BCS instrumented binary and frontend dependencies, and --module still requires setup/start all. It would need a fresh full-stack setup; no existing process was stopped or modified.
- The next head's actual Singlebox result, not an estimated coverage gain, determines PASS. Prior Backend single-job retry and its outcome are historical after the next push.

## Current acceptance-fix head
- Latest pushed head: `8fc3c3634488c2e29225ba6b52b57fe90ce9700b`; current base remains GitHub dev. Prior head/run observations above are historical and do not establish this head's gate.
- Backend https://github.com/inclusionAI/Avernet/actions/runs/43153450906/job/130967814338: PENDING, real test/coverage step started 2026-11-12 12:14:32 Asia/Shanghai.
- Singlebox https://github.com/inclusionAI/Avernet/actions/runs/43153450587/job/130967813274: PENDING, real coverage step started 2026-11-12 12:15:43 Asia/Shanghai.
- PR title and design guard: SUCCESS. Unchanged module tests are scope skips, not claimed executed suites.
- No new code changes or additional retries were made after this head.

## Complete OpenAPI contract audit
- Head 8fc3c36344 Backend exposed a second strict contract inventory: `engine_runtime/test_stage_addressing.py` requires an explicit set of owner-addressed non-stage operations. Added only the new Caller GET to that set; retained all equality assertions.
- Full OpenAPI adapter suite then found one additional schema-description gap before the next push: `CallerInstanceStatus` lacked model-level documentation. Added a class docstring; field descriptions and runtime behavior remain unchanged.
- Targeted two failing suites: 28 passed. Complete OpenAPI adapter suite after both corrections: **2505 passed, 2 skipped**, zero failed, 83.21s; JUnit `/tmp/caller-status-full-openapi-final-junit.xml`. This includes all stage, address, admission, authorization, count, schema and routing inventories, not only selected inventory files.
- Same-head Singlebox 43153450587/job130967813274: **SUCCESS**, actual new acceptance story executed; exact artifact metrics are being extracted. The next pushed head still requires its own checks.

## Latest public-contract-fix head
- Latest pushed head: `b6167ad5a73e7eb64d3eb0651a97d7567f41ce70`; base GitHub dev. No runtime control-flow changes since acceptance-fix head; new DTO docstring and exact owner-address inventory only.
- Backend: https://github.com/inclusionAI/Avernet/actions/runs/43156194469/job/130976578582 — PENDING.
- Singlebox: https://github.com/inclusionAI/Avernet/actions/runs/43156194488/job/130976578388 — PENDING.
- Previous-head actual Singlebox artifact: 74/74 passed, zero failed/skipped; new status acceptance story passed in 1.290s. ExpertChat core589/936=62.93% (threshold61.18%), router12/12=100%. This is historical live evidence, not a substitute for the current head's check.
- Artifact path: `/tmp/caller-instance-status-singlebox-ci-8fc3/singlebox/.dependencies/coverage/singlebox/reports/summary.json`; artifact13915267684.

## Full Backend closure after endpoint-framework registration
- Previous-head Backend failed only the global endpoint coverage gate: new GET lacked registered happy/error cases. Added four real DI/SQLite framework cases (absent, pending unchanged, private denied, missing Bot); no mocking, baseline exemption or threshold change.
- Real endpoint runner4 passed; complete framework128 passed; schema/path/error-schema/namespace27 passed. Independent second review PASS.
- Entire local Backend community suite, four workers, no fail-fast: **31081 passed, 40 skipped, 0 failed/0 errors**, plus **6 subtests passed**, 414.76s. JUnit aggregate tests31127 includes subtests; artifact `/tmp/caller-status-full-backend-junit.xml`. Coverage was not enabled for this local closure; do not infer repository coverage from it.
- Current-head Singlebox b6167 run43156194488/job130976578388 was SUCCESS. The new pushed head still requires its own remote Backend/Singlebox checks.
