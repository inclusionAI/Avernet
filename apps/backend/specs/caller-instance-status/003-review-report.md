---
agent: tc-code-reviewer
status: completed
created: 2026-11-12
iteration: 1
---

# Caller instance status code review

## Scope and verdict

**Local code review: PASS. Remote ACI evidence: PENDING.**

Worktree: `caller-instance-status-20261112`; base `github/dev` (`d0cdd02345`), uncommitted topic under implementation at review time. Reviewed actual working diff and untracked tests/schema, not an empty committed diff. Existing chain is public identity/grant -> ExpertChat chat access -> exact caller ledger -> BaaS health GET. Added points stay in those existing adapters/services/contracts, policy inventories, tests and owning documentation. No lifecycle writes, shared-online substitution, OCB worktree or deployment.

| Dimension | Verdict | Evidence |
| --- | --- | --- |
| Correctness | PASS | Absent/invalid allocation returns false/false without BaaS; registered UUID persists exists=true after release; only ACTIVE + health=true device is alive; top-level RELEASED overrides stale device health. Exact real owner and authenticated subject retained. |
| Authorization | PASS | Existing owner/public/collaborator chat policy is called before provider observation. Public noncollaborator succeeds; private unrelated caller receives masked 404; human/mixed user mismatch receives 403; app-only path requires addressed grant. |
| Architecture/scope | PASS | Flat endpoint on existing router; ServiceChecked delegates to existing chat policy. Core remains framework-free; protocols document additive read-only methods. No provisioning/upgrade/restart/publish/identity-exchange path called. |
| Performance | PASS | Bounded source lookup, ledger read and one provider GET; blocking reads run through asyncio.to_thread. No unbounded scans. |
| Static checks | PASS | Independent ruff F401/F841/E9 on changed production files and new tests; git diff --check. No new unused imports/variables. |
| File size | PASS | Independent test_no_oversized_modules.py: 2 passed. Router, instance service and ExpertChatService below 1000 lines. BaasService is already explicitly allowlisted by the existing gate; no waiver or allowlist was added. |
| Logs and credentials | PASS | Initial finding fixed: real get_bot previously logged complete provider bodies despite safe upper-layer projection. New compatible keyword-only log_response=False reuses existing HTTP log switch and suppresses raw error body/text only for this read path; default existing behavior retained. Independently ran six real BaasService + MockTransport scenarios, all passed, verifying successful response/404/503/API failure/invalid JSON/transport credential sentinels do not leak. Inbound/provider request/success/failure events retain business identity, status, duration and safe error class. |
| Tests | PASS | Independent four-suite run: 87 passed (domain, HTTP, admission inventory, authorization inventory). Post-fix focused domain+HTTP run: 43 passed. Post-fix real-BaaS sentinel run: 6 passed. |
| ACI coverage | PENDING | No PR/head-specific remote job available at review time; local focused totals do not establish repository-wide gate. |

## Coverage evidence and limits

Independent focused post-fix coverage: 43/43 tests passed, failed=0, skipped=0. Across three measured existing modules: 245/750 executable lines covered (32.67%); this deliberately narrow test selection does **not** measure the full Backend total or satisfy the total-line ACI gate. Working-diff executable additions in those three modules: **56/57 (98.25%)** covered; remaining router line 202 is generic re-raise for an unexpected exception. Core instance additions 24/24, application entry 7/7, router 25/26. XML/JUnit: `/tmp/caller-status-review-coverage.xml`, `/tmp/caller-status-review-junit.xml` (ephemeral local evidence).

BaasService has a pre-existing class-level coverage exclusion. Its safe option behavior is exercised by real-HTTP-fake assertions but this review does not claim measured coverage of excluded lines. Full local regression and head-specific remote ACI must report their actual denominators separately. Required remote thresholds remain case pass 100%, total lines >=70%, changed lines >=90% (plus stricter repository gates where applicable); none are weakened.

## Findings resolved

- Provider response logging could expose config/credentials below the new projection. Fixed through a narrow opt-out on get_bot, with real HTTP fake credential tests; rechecked source and independently reran all six cases.
- Generic MEMBER-only public policy would block authorized ordinary public-Bot callers. Final route uses ServiceChecked and the actual existing chat policy, proven through signed-principal HTTP tests.

## Remaining work

No blocking code finding. Independent regression, commit/rebase, PR creation and remote job/review convergence remain orchestration stages. Do not interpret this local PASS as remote ACI PASS or deployment verification.
