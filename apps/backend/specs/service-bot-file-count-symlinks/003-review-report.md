---
agent: tc-code-reviewer
status: completed
created: 2026-09-21
iteration: 2
---

# File-count symlink and timeout review

## Scope and conclusion

Worktree: `service-bot-file-count-symlinks`; topic: `feat/service-bot-file-count-symlinks`.
Reviewed approved spec, coding reports, tracked/untracked changes and the complete fixed-tree diff (14 files, including reports).

**Code review: PASS. Local ACI-compatible gates: PASS. Remote ACI: PENDING.**
No deployment or remote CI success is asserted by this report. Final commit/rebase must retain the reviewed source and be checked against the actual PR base/head.

Existing router → domain/binding service → provider runtime → Engine HTTP → filesystem worker remains unchanged. Modifications are limited to count deadlines, worker traversal, necessary diagnostics, contract text and tests. No auth/stage/provider routing, general HTTP client, deployment or gitlink changes.

## Fixed checks

| Dimension | Result | Evidence |
| --- | --- | --- |
| Correctness | PASS | 120-second Engine budget; 150-second Backend outer/provider deadlines; bounded logical-entry counting; invalid links skip without hiding ordinary scan failures. |
| Security | PASS | Explicit readlink with descriptor-relative no-follow opens; link-before-parent semantics; allowed-root checks; inode/replacement checks; trusted roots cannot themselves redirect. No new high/medium exploitable issue identified. |
| Performance | PASS | Ordinary directories remain one recursive traversal; links add ancestry resolution. Two-worker bound and process termination/reaping preserved. Aliases intentionally count separately, so alias-heavy trees can still time out. |
| Architecture/style | PASS | Existing API signatures and response fields retained; protocol docstrings updated to the changed behavior. No unrelated refactor; production files remain below 1,000 lines. |
| Behavioral tests | PASS | Reviewer independently ran final 52 Engine focused tests and 53 Backend runtime/real-Engine-HTTP tests. Subsequent equivalent test-variable rename was independently rechecked with 17 symlink tests. |
| Static checks | PASS | Reviewer ran flake8 F401/F841/F821/E203/E265/E303/W291/W293 across all nine changed Python files; no findings. `git diff --check` passed. |
| Boundary diagnostics | PASS | Existing HTTP request/success/failure logs retained; Backend events add timeout_seconds=150. Engine completion logs bounded reason counts, request correlation, count and budget, not resolved targets. Tests assert events, business fields and credential/target absence. |

## Independent ACI-compatible evidence

Base: `cf20a597b2e058c9baa0d017d0b432a7235f08d3` (`github/REL20260922`).
Reviewed immutable head tree: `f93de31cde47209b31010579537135912766146f`.
This is a nonempty tree diff, not an empty base==HEAD gate. Reviewer reran `scripts/ci/report_check.py` for both modules against these exact identifiers with thresholds 100% case pass, 70% total lines, 90% changed lines; both exited zero.

| Module | Cases | Total lines | Changed lines |
| --- | --- | --- | --- |
| Engine | 2861/2861 passed, 0 skipped, 0 failed; 5 corp-only cases deselected per existing CI selection | 41388/44161 = 93.72% | 266/267 = 99.63% |
| Backend | 19709 passed, 43 skipped, 0 failed; checker reports 19752/19752 non-failing outcomes, including skips | 102483/114849 = 89.23% | 1/1 = 100% |

Artifacts: Engine `pytest_report/symlink-engine-junit.xml` and `symlink-engine-cov.xml`; Backend `pytest_report/regression-junit.xml` and `regression-cov.xml`, relative to each module. Regression additionally reports 314 architecture tests passed.

The full run preceded only an equivalent local test-variable rename (`token` → `request_context`) and report edits. Production code and executable line positions did not change; reviewer independently reran all 17 symlink tests after the rename, then reran both fixed-tree gate calculations. Working source/tests were verified identical to this final tree.

The sole uncovered Engine changed executable line is `src/engine/src/engine/community/plugins/file_count_worker.py:188`, attaching aggregate skip counts to worker IPC. Its real subprocess behavior is asserted by `test_scan_budget_and_bounded_skip_logs`; the parent-process coverage collector does not credit that child execution. This is not an untested contract branch and the unchanged threshold passes. No exclusions or threshold reductions were introduced.

Remote ACI job: **PENDING** — no remote job supplied at review time; local numbers do not prove remote completion.

## Review spec checks

| ID | Check | Result |
| --- | --- | --- |
| R-01 | Follow requested/middle/file/directory links only under runtime root and sibling openclawExt; preserve relative target `..` semantics | PASS: real filesystem tests, intermediate escape and lexical-cancellation regression |
| R-02 | Dangling/cyclic/outside links skip, including requested links; ordinary missing/permission/mutation errors still fail | PASS: worker and BaaS/ARCA HTTP integration assertions |
| R-03 | Detect active ancestors, not globally deduplicate; aliases/hardlinks count by logical entry | PASS after first-round ordinary-child cycle correction |
| R-04 | No replacement escape or worker leak | PASS: no-follow/inode guards, mutation tests, timeout/repeated-cancel/spawn/kill/reap tests |
| R-05 | Preserve auth/stage/provider/schema behavior and 120/150-second budget propagation | PASS: unchanged routing/auth code, both provider deadline and real HTTP tests |
| R-06 | Bounded skip diagnostics and non-sensitive boundary logging | PASS: aggregate reasons only, request IDs and budget assertions, no individual target logging |

## First-round finding and resolution

An ordinary-directory edge could re-enter an active inode after following a link to its parent. Reviewer independently reproduced `root/sub/file` plus `sub/parent -> ..`: requesting `sub` incorrectly returned 2 rather than 1. The correction checks ordinary-child opened identity against active ancestors before recursing, while still validating entry identity and closing the descriptor. The new regression also includes a separate parent-level file, proving the fix skips only the cycle rather than the entire legitimate parent subtree. Independent final focused tests pass; this finding is closed.

## Remaining boundaries

Both configured roots and their ancestry must be real directories, not symlink aliases; redirecting the trusted root remains a configuration/security error, not a skipped business link. Successful count is under the approved skip rules, not a physical-inode count or atomic snapshot. Deployment gateway/frontend deadlines and actual pre-environment behavior remain outside this local review and must not be claimed verified.
