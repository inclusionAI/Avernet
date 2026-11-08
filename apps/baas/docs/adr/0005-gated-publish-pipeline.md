# ADR-0005: Multi-stage gated publish pipeline

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

Deploying bot changes to production is the highest-risk operation BaaS
performs; an unreviewed batch push or an unnoticed failed device would be
unacceptable. The platform therefore models deployment as an explicit pipeline
(`PublishType` docstring: "Types of publish operations per D-06") with
per-type stages and approval gates, rather than as a single API call.

## Decision

Run every deployment through a publish pipeline with per-type stages and
stage-gate approvals:

- **Types** (`PublishType`): `CREATE`, `UPDATE`, `RESTART` (gated pipelines),
  `SCALE_UP`, `SCALE_DOWN`, `DESTROY`, `STOP`, `UPDATE_DEVICE` (direct,
  single-batch, zero gates).
- **Stages for gated pipelines**: `PREPUB` (1–2 device validation) →
  `GRAY` (gray environment) → `PROD_FIRST_BATCH` (first production batch,
  pause) → `PROD_OTHER_BATCH`; gates: 3 for CREATE/UPDATE, 1 for RESTART,
  0 for direct types. Auto-compact reduces stages when device count is
  smaller than the stage count.
- **Statuses**: `PENDING → ACTIVE → APPROVING → …` with terminals
  `REJECTED / FAILED / SUCCESS / REVOKED` (ADR-0004); each batch records
  `BatchStatus` incl. `ROLLED_BACK`, and each device attempt records
  `PublishRecordResult` (`PENDING → PROCESSING → SUCCESS | FAILED`).
- **Recovery**, not surgery: retries re-run the full per-device lifecycle
  under an explicit per-request budget (`_publish_retry_orchestrator.py`),
  and a scheduled sweep advances timed-out device attempts
  (`PublishRetrySweepTask`, ADR-0014); `RESTART` can target
  `all` or only `unhealthy` devices (`RestartScope`).
- The admin/audit surface (`_admin_service.py`) exposes submit/approve/
  reject/revoke so gates are enforceable through the API.

## Consequences

**Positive:** progressive rollout with human checkpoints; failure scope is
bounded by batch; retry semantics are explicit and budgeted; the audit trail
exists per device attempt.

**Negative:** deployments are slower and need an approver; more moving parts
(records, batches, orchestrator, sweep) to operate.

**Risks:** gate fatigue leading to rubber-stamping; mitigated by making
`PROD_FIRST_BATCH` results (per-device failures) visible in the approve step.

## Evidence

- community: `src/secbaas/community/api/publish_manage/_enums.py` — types, stages, gates, batch/record enums
- community: `src/secbaas/community/core/service/publish_manage/_publish_service.py` — pipeline execution
- community: `src/secbaas/community/core/service/publish_manage/_publish_retry_orchestrator.py` — retry budgets
- community: `src/secbaas/community/core/service/publish_manage/_admin_service.py` — approval surface
- community: `src/secbaas/community/core/service/scheduler/_tasks/_publish_retry_sweep_task.py` — timed-out attempt sweep
- enterprise: `README.md` — legacy publish tables (history; community enums are the authority)