# PR convergence report: baas-caller-status

## Scope
- Worktree: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/baas-caller-status-20261118`
- Source repository / push remote: `inclusionAI/Avernet` / `github`; never the internal mirror `origin`.
- Initial topic / base: `feat/baas-caller-status-20261118` / `dev` at `d24f0e1ed2d240a54fe475d7acb03465948dbada`.
- PR: NOT_CREATED.
- Human comments: auto.
- Prerequisite: [Caller status PR #3914](https://github.com/inclusionAI/Avernet/pull/3914) is OPEN and not merged. The new complete branch carries its seven commits, rebased onto current dev; the previous PR is left unchanged.
- New BaaS endpoint: `GET /api/v1/expert-chats/app-caller-instance/status?bot_id=<bot>&owner_id=<owner>&user_id=<caller>`; required service credential header `X-Avernet-Principal`, shared signature verifier with issuer `baas`.
- Existing public endpoint retains user identity, addressed Bot grant and chat policies. A BaaS service JWT is not newly admitted on the public endpoint.
- OCB/gitlink, deployment and production lifecycle work are outside scope.

## Rebase evidence
- Initial seven prerequisite commits replayed onto `github/dev`; no force push or original-worktree changes.
- README conflict retains both current-dev lifecycle/shared-runtime sections and prerequisite read-only status documentation.
- Explicit-user inventory keeps current-dev additions and increments the public status route exactly once: Bot-path 234→235; user-scoped 317→318. Final schema tests remain required.

## Automated feedback
No PR exists yet; feedback not evaluated. Independent local review inspected the service signature/issuer boundary, unchanged public permission checks and trusted server tenant/environment; no code blocker remains.

## Local validation
- New and existing BaaS signed-HTTP boundary suites: 70 passed before the additional public-refusal case.
- Independent relevant BaaS/public/domain regression: 113 passed; executable new lines in the two BaaS runtime files 33/33 (100%). This is local changed-line evidence, not full-repository coverage.
- Combined complete OpenAPI adapter, endpoint framework and relevant domain/JWT suites: 2804 passed, 2 skipped, 0 failed (132.79 seconds).
- Same real `iss=baas` token is refused by the original public GET with 401; final BaaS boundary suite: 38 passed.
- Four real DI/SQLite registered endpoint cases passed. New live Singlebox acceptance story collected successfully; a full local runtime was not started because this checkout lacks the complete stack. Final-head remote Singlebox remains required.
- Full final-tree Backend community suite with 4 workers: 32508 passed, 41 skipped, 6 subtests passed, 0 failed/errors in 474.13 seconds (JUnit total 32555 including subtests; `/tmp/baas-caller-status-backend.xml`). This behavior run did not collect coverage; remote coverage remains authoritative.
- Local code review PASS; independent regression 42 passed, 0 failed/skipped.
- Pre-push compatibility: prerequisite public policy row is kept within the existing 1000-line source cap; authorization/source-size suite 37 passed.
- Prerequisite fixed-range range-diff verified all seven commits, with only necessary latest-dev documentation and inventory conflict adaptation.

## ACI/CI
| Gate | State | Evidence |
| --- | --- | --- |
| Local implementation/review/regression | PASS | Complete Backend 32508 passed +6 subtests; independent code review/regression PASS |
| Remote Backend | PENDING | Not created |
| Remote Singlebox | PENDING | Not created |

## Current conclusion
- PR: NOT_CREATED
- Automated comments: PENDING
- ACI/CI: PENDING
- Human comments: PENDING
- Next: complete implementation and local gates; create a fresh rebase result branch on verified latest GitHub dev, push and open an English PR.
