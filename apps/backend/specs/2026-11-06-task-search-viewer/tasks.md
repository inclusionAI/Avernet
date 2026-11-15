# Tasks: optional viewer scope for task semantic search

Status: approved by Shangjian on 2026-11-06; Groups A/B/C completed and locally verified; no deployment or push.

## Task 1: approve and update the single contract
- [x] Review HTTP/service optional-field diffs and behavior matrix in plan.md.
- [x] After approval only, update the existing protected search contract sections.
- Done when: one consistent optional extension; old requests remain valid.
- Depends on: explicit Shangjian approval.

## Task 2: implement Backend viewer scoping test-first
- [x] Add failing request-pair, unchanged-default and viewer-propagation tests.
- [x] Extend request DTO, existing service Protocol and implementation.
- [x] Pass explicit viewer through the existing semantic pipeline and reuse product
      viewer policy without overriding it with the old default filters.
- [x] Preserve generation validation, task readiness, candidate cap and response.
- Done when: old/new request matrix passes and invalid viewers never reach upstream.
- Depends on: Task 1.

## Task 3: extend only the source task-loop transport
- [x] Keep Backend four-field compatibility, but require an explicit Bot viewer in Skill.
- [x] Reject Human, missing/null viewers and invalid IDs before network calls.
- [x] Document current Runtime identity resolution before S5c; no owner/candidate
      inference or unscoped fallback. Keep query projection rules unchanged.
- Done when: only Bot passes Skill validation; Backend still accepts old/Human calls.
- Depends on: Task 2; edit only aiworkbench source, no install/sync.

## Task 4: final compatibility and scope verification
- [x] Run focused DTO/router/service/semantic/catalog tests and Skill transport tests.
- [x] Check missing/invalid/half-pair, public/protected/private and readiness matrix.
- [x] Review diff for accidental protocol/state changes and unrelated edits.
- [x] Record verification and remaining live-environment limits.
- Done when: acceptance criteria verified; no real task execution or deployment.
- Depends on: Tasks 2 and 3.

## Groups
- Group A — Backend contract and scope: Tasks 1, 2.
- Group B — Bot-only Skill transport: Task 3.
- Group C — Verification: Task 4.


## Verification results
- Backend task HTTP, task service/semantic integration and product catalog seams:
  806 passed, 2 skipped (existing live tests require unavailable OpenAPI credentials).
- Source task-loop suite after Bot-only refinement: 90 passed, 16 subtests passed.
- Earlier independent review covered Backend compatibility and optional viewers.
  The subsequent Bot-only Skill refinement intentionally makes Skill validation
  stricter than Backend; the earlier cross-layer acceptance equivalence no longer
  applies. New local tests reject valid Human and absent/null viewers.
- Invalid viewer validation uses field-scoped errors; HTTP tests verify no whole
  relay request/token echo in those 422 responses.
- Changed Python files passed Ruff checks; git diff --check passed in both repos.
- Only aiworkbench Skill source changed, no installed copies synchronized.
- No live task requests, state changes, deployment, commit, push or PR creation.
- Existing pending ECB dispatch-expiry and TeamOS Cookie-retention work remains
  separate and untouched by this implementation.
