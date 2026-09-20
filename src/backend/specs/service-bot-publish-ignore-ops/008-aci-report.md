# PR / CI evidence — publish-ignore operations

Update: the user requested withdrawal of `docker/agent/start_service.sh` changes. That file now matches the release base byte-for-byte and its nine feature-specific tests are removed. The remaining 44 focused tests passed. All remote PASS results below refer to the previously validated head; the rollback commit requires fresh remote checks and is not covered by those historical results.

PR: https://github.com/inclusionAI/Avernet/pull/2254

- Source remote: GitHub `inclusionAI/Avernet` (not the internal mirror).
- Target/base: `REL20260917`, `6da0472eed89ae76cea85a23184fb15f6ca5c3fa`.
- Feature head: `1162ac28e30a026b08ea47cf8a3230ceccca24c3`.
- Rebase direction: feature commits replayed on top of the remote release branch.
- This file is a local evidence artifact, intentionally not pushed merely to retrigger the same source checks. The feature source and review reports are committed in the PR.

## Rebased local evidence

| Gate | Backend | Engine |
| --- | --- | --- |
| Executed tests | 18,788 passed; 43 existing skips | 2,692 passed; 5 existing corp-only deselections |
| Repository case-rate gate | 18,831/18,831 = 100% (the existing checker includes skips in the passing denominator) | 2,692/2,692 = 100% |
| Total line coverage | 98,598/110,572 = 89.17% | 39,375/42,114 = 93.50% |
| Changed line coverage | 210/211 = 99.53% | 395/396 = 99.75% |

Both changed-line results exceed 90%; Backend's repository threshold is 80%, so the task's stricter 90% threshold is also checked separately. No test thresholds, architecture allowlists or security hooks were bypassed. The secret scanner initially rejected test placeholders; they were corrected to explicit non-secret fixtures without weakening the scanner.

## Remote checks

Status: **PASS**. All nine GitHub check entries succeeded on the feature head above, including Singlebox coverage (13m28s). The following values were independently read from the downloaded GitHub `backend-testresult` and `engine-testresult` artifacts and checked against the exact base/head above:

| Remote unit gate | Backend | Engine |
| --- | --- | --- |
| Actual execution | 18,788 passed, 43 skipped, 0 failures/errors | 2,692 passed, 0 failures/errors |
| Repository case-rate gate | 18,831/18,831 = 100%, with the existing skip-counting convention noted above | 2,692/2,692 = 100% |
| Total covered lines | 98,591/110,572 = 89.16% (required ≥75%) | 39,375/42,114 = 93.50% (required ≥70%) |
| Changed covered lines | 210/211 = 99.53% (rechecked at ≥90%) | 395/396 = 99.75% (required ≥90%) |

The remote Backend Legacy Skill compatibility gate also passed. Successful PR entries are Backend, Engine, BCS, BaaS, Gateway and Sandbox-proxy unit checks; BCS E2E; Singlebox coverage; and PR title validation. Unaffected-module checks may exit successfully after change detection; success does not imply their full suites executed.

The PR remains open and unmerged. Independent code review passed after correcting architecture imports, endpoint-registry coverage, restart-busy handling and publication-binding change detection. Remote CI introduced no further failures requiring code changes.

Singlebox log evidence: 56 tests passed; BCS runtime E2E line coverage 41.62% (repository gate ≥40%), method coverage 36.77% (gate ≥36%), adapter endpoint coverage 156/156, and CLI command coverage 52/52. Artifact verification also passed. These runtime E2E thresholds are separate from the Backend/Engine unit coverage gates above; none were changed by this PR.

- Unit tests: https://github.com/inclusionAI/Avernet/actions/runs/35066702591
- Backend job: https://github.com/inclusionAI/Avernet/actions/runs/35066702591/job/104698488965
- Engine job: https://github.com/inclusionAI/Avernet/actions/runs/35066702591/job/104698488837
- Singlebox: https://github.com/inclusionAI/Avernet/actions/runs/35066702606
- E2E: https://github.com/inclusionAI/Avernet/actions/runs/35066702618

No remote Bot was deployed or restarted. The separate cp-ignore installer and runtime key/identity provisioning remain rollout prerequisites.
