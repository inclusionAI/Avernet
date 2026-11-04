---
agent: tc-code-reviewer
status: completed
created: 2026-11-04T22:20:00+08:00
iteration: 1
---

# Code review: failed-draft-restart

## Scope and decision

**Code review: PASS. Full local/remote CI: PENDING.** The pending gate is independent; this report does not declare the PR ready or deployment authorized.

- Repository: `inclusionAI/Avernet`.
- Worktree: isolated `fix-failed-draft-restart-rel20261105`.
- Branch: `fix/failed-draft-restart-rel20261105`.
- Verified target: `github/REL20261105` at `b2ff992a5d6450783af118529f94570b5e7440fd`.
- Reviewed working-tree source and tests before commit; final committed base/head and remote jobs must be rechecked by the PR workflow.
- Inputs: `001-spec-output.md`, `002-code-report.md`, actual diff, new helper/tests, and existing DeviceService, Caller and published binding writers. `/tmp/CODE_REVIEW.md` was absent.

## Existing chain and allowed boundary

The existing service resolves a historical Provider before taking its durable restart lock, then delegates allocation and successful binding writeback to existing code. The patch adds service-only scope classification at that selection point. The historical binding remains read-only evidence. No Router, database schema/repository API, external SDK, Caller provisioning, published-stage resolution, Engine or OCB gitlink behavior changes.

| File | Reviewed purpose | Result |
|---|---|---|
| `services/restart_binding_scope.py` | Pure classifier for exact entity/type/environment/Bot identity, known foreign scopes, managed-draft lifecycle tuple and unknown scope | PASS |
| `services/bot_service.py` | Pass Bot type; classify service history before status checks; reject unknown history; preserve personal behavior | PASS |
| `test_service_draft_restart_recovery.py` | Thirty new cases through public restart, including the real background allocator failure/success path and lock reuse | PASS |
| Task spec/plan/coding report | Describe this focused behavior and actual validation without production data | PASS |

Source paths in this table are under `apps/backend/src/agentclaw/community/core/bot_management`; tests are under the matching community test subtree.

## Fixed review dimensions

| Dimension | Result | Evidence |
|---|---|---|
| Correctness | PASS | Known foreign runtime scopes are skipped before live-state checks; latest trustworthy live draft still refuses; unknown scope does not fall through to default allocation |
| Safety | PASS | Exact immutable resource coordinates checked; callback credential is used only as a nonempty-string presence proof and never returned or logged |
| Performance | PASS | Existing newest-first paginated repository read is reused; no publication-history scan, new external query or N+1 lookup |
| Architecture/style | PASS | Core helper is pure and uses the existing record type; minimal 21-line wiring in the pre-existing large service; helper55/test240 lines; no unrelated refactoring or allowlist changes |
| Behavioral tests | PASS | Reviewer independently ran109/109 cases, zero skips/failures; real allocator first fails, releases lock, then succeeds with new binding84 while old42 stays RELEASED |
| Focused changed coverage | PASS | Independent XML plus actual working-tree diff: helper20/20 and touched BotService executable lines8/8; combined28/28 (100%) |
| Static checks | PASS | Independent Ruff with `--preview --select F,E203,E211,E265` on all three source/test files and `git diff --check` |
| External boundary logging | N/A | No external inbound/outbound operation or payload was added/modified; existing allocation/logging is reused |
| Full ACI-compatible gate | PENDING | Independent regression agent owns full-suite coverage and post-commit base/head check; remote job has not yet been created |

## Spec checks

| Check | Result | Evidence |
|---|---|---|
| TC-01: newer pending Caller does not block terminal draft | PASS | Public restart test skips exact Caller reason, selects original draft Provider and acquires one lock |
| TC-02: published/eval separation and BaaS draft compatibility | PASS | Tests cover all three publication operation markers, both evaluation tags, explicit non-draft stage, Caller precedence, and managed BaaS drafts carrying publish_id |
| TC-03: latest live draft refuses older history | PASS | ACTIVE/PENDING cases assert no allocation and no lock acquired |
| TC-04: unknown/malformed/missing history or Provider refuses | PASS | Unknown matching row returns refusal; malformed lifecycle proof and missing Provider never allocate |
| TC-05: exact resource/Bot identity | PASS | Row entity/type/env mismatch and complete Bot-ID mismatch skipped; repository arguments assert exact immutable scope |
| TC-06: pagination | PASS | First100 known Caller rows skipped, page2 supplies terminal draft; page calls asserted |
| TC-07: personal/bound compatibility | PASS | Existing79 restart-idempotency cases remain unchanged and pass with new service-only classifier path |
| TC-08: concurrent/duplicate recovery | PASS | New unbound-service held-lock test suppresses allocation; unchanged existing concurrent restart tests pass |
| TC-09: allocation failure then explicit retry | PASS | Real start_bot/_allocate_device_async exercised with synchronous thread and mocked allocation boundary; failure leaves FAILED/NULL and releases lock, retry writes fresh binding/device/ACTIVE |
| TC-10: useful diagnostics without credentials | PASS | Caller/unknown scope and binding/entity IDs asserted; sentinel callback credential absent from captured logs |

The actual writers were checked: Caller inserts the exact machine-readable `caller_instance:<bot_id>` reason with bolt_id; published-stage bindings contain Provider operation fields without the managed callback/entity tuple; DeviceService-managed drafts persist that tuple, and BaaS drafts can also contain publish_id. This supports the classification order and avoids using Provider or a generic publish ID as a stage guess.

## Independent validation

Executed from `apps/backend`:

```bash
DEPLOY_PROFILE=test PYTHONPATH=src:. COVERAGE_FILE=/tmp/failed-draft-restart-review-20261104/.coverage .venv/bin/python -m pytest tests/community/core/bot_management/services/test_bot_service_restart_idempotency.py tests/community/core/bot_management/services/test_service_draft_restart_recovery.py -q --junitxml=/tmp/failed-draft-restart-review-20261104/junit.xml --cov=agentclaw.community.core.bot_management.services.restart_binding_scope --cov=agentclaw.community.core.bot_management.services.bot_service --cov-report=xml:/tmp/failed-draft-restart-review-20261104/coverage.xml --cov-report=term-missing
```

Result:109 passed in1.84s,17 pre-existing dependency deprecation warnings. Traceback/source paths pointed to this worktree. The independent review XML reports20 executable helper lines, all covered. Intersecting touched BotService diff lines with XML executable lines gives8/8 covered, no missed added executable lines.

The focused run measures only restart behavior; its pre-existing whole BotService coverage622/2072 is not a full-backend gate measurement and is not substituted for total backend coverage. Full-suite regression must establish the correct denominator.

## ACI evidence status

- Case pass rate, focused review:109/109 (100%), skipped0, failed0.
- Full-backend case rate: PENDING independent full run.
- Full-backend line coverage: PENDING; threshold at least repository75% (stricter than general70%).
- Final committed changed-line coverage: PENDING exact base/head report; threshold90%. Working-tree focused evidence is28/28, not a fabricated empty-diff head result.
- Remote CI/jobs: PENDING. Creation/rebase/push changes the final head and requires actual checks.

## Findings and conclusion

No blocking code findings. Existing durable lock, Provider override policy, bound-Bot restarts and successful binding writeback were not changed. Unknown service history intentionally remains unavailable; recovery does not reactivate an old binding or silently switch to another runtime.

**PASS for code review.** Continue independent full local regression, exact base/head coverage and remote PR checks. Do not report CI PASS or PR readiness until those gates have real passing evidence.
