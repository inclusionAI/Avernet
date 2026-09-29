# PR drafts (not submitted)

Use these as two stacked review/Draft PRs; see `submission-plan.md` for exact
stage boundaries and the still-open unified Singlebox gate. PR creation is
reserved for the user. See `STAGE.md` for this branch and `submission-plan.md`
for the stage-2 CI trigger limitation; no PR URL exists.

## Stage 1 title

`feat(backend): add durable resource withdrawal delivery foundation`

### Problem

Single-chat file deletion currently has no durable TC fact that ECB can consume
to invalidate the corresponding upload reference. A best-effort synchronous
notification would lose deletion intent on outage, restart or response loss.
This is not chat-message recall or deletion of shared knowledge content.

### Solution

Add an opt-in, atomic resource-status/outbox transaction, additive migration,
stable event identity and tenant-scoped lease/fenced repository. Add a serial
lifecycle worker with bounded retry, strict durable-intake receipts, authenticated
HTTP plugin and fail-closed secret/config composition. Delivery defaults OFF;
production never substitutes the test fake. Normal service deletion remains
unwired in this foundation PR; the stacked single-chat PR enables emission.

### Validation

- Isolated stage-1 archive: 135 targeted/regression and 314 architecture tests
  passed, with loaded
  module paths checked to avoid the final editable checkout.
- Combined final implementation: 20,131 passed / 43 skipped; changed executable
  line coverage 98.64% (364/369). These combined results are not claimed to be a
  separate stage-1 full-suite run.
- Ruff, formatting of new files, secret scan, file size and whitespace passed.
  Cached flake8 SAST wrapper passed; company antflake/FLA rules unavailable.
- Unified Singlebox did **not** pass: existing upload integration acceptance
  passed, but shared BCS bot-connectivity/coverage/tooling checks failed. Re-run
  normal CI; no checks/thresholds were weakened. Details in `validation.md`.
- No real ECB, production MySQL or browser UI verification was performed.

### Compatibility and risk

No group-chat/BCS/frontend changes, shared-content deletion or historical
backfill. Apply additive SQL before writer activation. `accepted` means durable
intake, not business withdrawal. Wire endpoint/receipt is a draft pending ECB
confirmation; keep OFF. Pause retains facts; downgrade to a pre-writer version
requires pausing deletes or audited compensation. See the rollout runbook.

### Spec

`docs/specs/2026-09-28-tc-session-resource-withdrawal/spec.md` and
`docs/contracts/tc-resource-withdrawal-v1.md`. Stage 1 adds 1,477 non-doc lines.

---

## Stage 2 title

`feat(backend): sync single-chat file deletion to withdrawal outbox`

### Problem

The foundation alone does not connect user deletion to reliable withdrawal
intent or provide an operator entrypoint for inspecting and recovering blocked
events. Formal and legacy single-chat APIs must behave consistently.

### Solution

Wire only personal_bot_chat, friend_bot_chat and openapi_session to the existing
transactional delete hook. Preserve authorization, duplicate-delete responses
and deleted terminal state; exclude group/unknown scopes. Add trusted operator
stats/inspect/blocked-only replay with tenant/attempt CAS, actor/reason and
explicit confirmation. Add API rollback, redirect and crash-recovery tests.

### Validation

- Combined full Backend community suite: 20,131 passed / 43 skipped; changed-line
  coverage 98.64% across the complete feature. Per-stage CI must run after actual
  branch creation.
- Both delete routes tested for unfinished/ready files, duplicate/denied requests
  and atomic rollback on DB insertion failure. Group/unknown exclusion and A/B
  distinct upload IDs tested locally. Real HttpxClient does not follow redirects.
- Ruff/format/secret/size/whitespace checks passed; offline flake8 qualification
  and failed Singlebox gate are unchanged from stage 1 and not waived.
- ECB tombstones, trusted tenant authorization, A/B shared knowledge behavior,
  lists/summary/retrieval denial and real UI remain unverified release gates.

### Compatibility and risk

Stack on stage 1. Migration must precede this writer even while delivery is OFF;
otherwise deletion deliberately fails rather than silently losing the event.
No group behavior or shared-object GC added. Replay only re-queues, never marks
withdrawal complete. Keep worker paused until ECB/MySQL/operations gates pass.

### Spec

Same feature spec, contract and runbook. Stage 2 adds 382 non-doc lines relative
to stage 1, not a second oversized PR against dev.
