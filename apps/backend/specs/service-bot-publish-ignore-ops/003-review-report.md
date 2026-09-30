---
agent: tc-code-reviewer
status: completed
created: 2026-09-16
iteration: 2
---

# Code review: publish-ignore operations

Historical review of iteration 2, not an approval of subsequent contract changes. The current 001 spec removes version selection and delegates binding/connection resolution to shared services; current-head validation is recorded in the local 009 report and PR checks.

## Scope

- Branch: `feat/service-bot-publish-ignore-ops-rel20260917`.
- Base: `github/REL20260917` (`e12a495a2d9a3f65fe5a4a6227ed25142fb1d8f4`).
- Includes Backend/Engine files and credential identity reading; Docker startup changes and their nine tests were withdrawn at the user's request. No tar or copy-algorithm changes.
- Reviewed Head: `f21ea371bf8546c020185a8bcfa32031d1cb15db`; 33 changed files including specification and validation reports.
- Inputs: `001-spec-output.md`, `002-code-report.md`, `003b-regression-report.md`, complete committed diff and new-file contents. Remote PR/ACI remains pending; code approval is not deployment approval.

## Evidence independently reproduced

| Check | Result | Evidence |
| --- | --- | --- |
| Backend focused behavior (historical review) | PASS | 44/44 at review time; nine Docker startup tests were later withdrawn together with the user-excluded script change |
| Backend focused line coverage | PASS, local only | New domain service 77/77; transport 63/64; combined 140/141 (99.29%) |
| Engine focused behavior | PASS | 28/28 tests through real router/DI and filesystem |
| Engine focused line coverage | PASS, local only | File implementation 139/140 (99.29%) |
| Static checks | PASS | Reviewer independently ran focused flake8; Engine E302 corrected and rechecked. Main orchestrator also reports Backend/Engine blocking SAST and whitespace checks passed |
| Architecture and endpoint registry | PASS | Dependency direction and explicit context declarations fixed; final focused boundary/behavior run 55/55; real endpoint registry runner 2/2, with 1,362 unrelated cases deselected |
| Cross-component behavior | PASS | Reviewer independently ran five Backend signing → real Engine HTTP/DI/filesystem cases, covering both providers, identity mismatch and tampering |
| Local ACI-compatible checks | PASS | Reviewer independently reran report_check.py against the committed base/head and full-suite XML; exact metrics below |
| Remote PR/ACI | PENDING | No completed remote job at review time; local evidence is not a remote PASS |

## Findings and resolution tracking

1. **Resolved: cross-Bot signing authority.** Replaced shared symmetric secret with Ed25519: Backend owns private signing key, Engine receives verification public key only. Target identity is signed and checked against managed runtime credentials. A persistent bounded request journal prevents replay after a later opposite mutation and after process restart.
2. **Resolved: authorization and target handling.** Empty/anonymous callers fail closed even with an admin flag; normal callers reuse existing Bot management permissions, admins can address other Bots. Selection uses source Bot/entity/environment and exact publication version/stage. BaaS enumerates fixed replica UUIDs; ARCA resolves its binding without BaaS enumeration. A second snapshot failure preserves completed results and marks snapshot unknown.
3. **Resolved: dependency direction.** Shared command/error/target values now live in neutral kernel contracts. Core and runtime plugin no longer import the Service API. Context README declarations name the new dependencies without test exemptions.
4. **Resolved: endpoint and meaningful contract evidence.** Both endpoint registry scenarios execute the real service with seeded repositories and a pinned signed runtime call. Consumer contract tests now cover success and failure and assert the remote transport invocation.
5. **Resolved: static warning.** Engine model E302 corrected; independent focused flake8 returns zero warnings.
6. **Resolved: restart and binding-switch races.** Initial `ext.restart.restarting` rejects mutation before device calls. The final snapshot rereads the exact publication and checks the stage binding, restart marker and allowed state, so a replacement binding cannot be reported as stable merely because the old binding remains ACTIVE. Behavior tests cover both cases. This is observation of changes, not a cross-system transaction or a guarantee against changes after the final snapshot.

## Requirement checks

| Requirement | Review |
| --- | --- |
| Admin all Bots / ordinary authorized managers | PASS: service behavior and real endpoint registry checks |
| Exact entity/version/stage; no latest fallback | Verified selection and runtime identity guard |
| BaaS and ARCA | Verified separate transport paths and existing ARCA builder headers |
| Atomic fixed-file change, safe literal rules | Verified no-follow regular-file opens, fixed sibling lock, bounded lock wait, same-directory replace, failed replace preservation, concurrent updates, CRLF/comments and Unicode line separator behavior |
| Trusted mutation and replay control | Verified Ed25519 verification, expiry recheck after lock, durable request consumption, mismatch denial |
| Scope and compatibility | Current instances only; no restart, application-file deletion or tar changes. Installer support remains a separate deployment prerequisite |

## Full-suite local gate evidence

Base/head: `e12a495a2d9a3f65fe5a4a6227ed25142fb1d8f4` / `f21ea371bf8546c020185a8bcfa32031d1cb15db`.

| Module | Executed cases | Total line coverage | Changed-line coverage |
| --- | --- | --- | --- |
| Backend | 18,781 passed, 43 skipped, 0 failed | 98,615/110,609 = 89.16% | 210/211 = 99.53% |
| Engine | 2,692/2,692 passed, 5 existing corp-only cases deselected | 39,375/42,114 = 93.50% | 395/396 = 99.75% |

The repository report script includes skipped cases in its Backend case-rate denominator and prints 18,824/18,824; this is not a claim that skipped cases executed. All executed cases passed. The reviewer independently ran both modules' compatibility checks using project Python and thresholds 100% / 70% / 90%; both returned success. Remaining focused misses are wrong-key-type defensive branches in the signing/verifying implementation, with malformed, missing, expired and tampered credentials separately behavior-tested.

Boundary logs were checked for request/response/failure events, correlation, target/operation, safe results, duration and signature redaction. No ordinary chat credential can substitute for Backend signing authority. No outstanding high/medium new security finding remains after the above fixes.

## Overall conclusion

**PASS: local code review and validation.** No remaining must-fix item from this review. Remote PR/ACI remains **PENDING** and must finish after rebase/push; do not interpret this report as remote approval or deployment authorization.

Gate thresholds remain unchanged: casePassRate 100%, lineCoverage at least 70%, changeLineCoverage at least 90%. If rebase or CI fixes change production code, rerun affected checks against the resulting head. Real NAS and deployed-device validation remain rollout prerequisites documented in the implementation/regression reports.
