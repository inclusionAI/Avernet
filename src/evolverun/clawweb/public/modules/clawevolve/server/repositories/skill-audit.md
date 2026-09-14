# Skill operation audit contract

`ce_skill_audit_events` records operations when they happen. It is not a view of
`ce_tasks` or `ce_skill_versions`. Migration 123 creates an empty table; there is
no backfill, read-time repair, inferred score, or invented historical outcome.

## Write boundary

- Registration and its baseline version commit with `registered`.
- Task creation commits with `evolution_started`, using the actual frozen
  `targetSkill.assetId`. Stage integration tests are excluded. An explicit task
  retry starts another run; replayed terminal reports deduplicate within that run.
- Task termination commits with `evolution_finished`. Results distinguish failure,
  cancellation, no cases, no improvement, and waiting for version confirmation.
  A finalize report, candidate reference, waiting state and event commit together.
  A no-cases Diagnose completion (builtin, replacement, or postprocessor) also
  commits its Step report, Task completion and `no_cases` event together. Audit
  failure rolls back the Step, so the same report can be retried without invoking
  a model or adding a new Step. Successful duplicates do not add events.
- `candidate_accepted` is a durable user decision/intent, **not** proof of an OCB
  write. It precedes the external CAS. `candidate_rejected` commits with the user
  decision and completed task state, without creating a version or writing OCB.
  Both decisions lock the same Task row and recheck its frozen configuration and
  persisted decisions inside the transaction. Once acceptance is committed,
  rejection is forbidden even if OCB or the final Task update subsequently fails.
  A recorded application also blocks rejection for interrupted older writes.
  This is not cancellation or rollback: retry acceptance against the same frozen
  candidate. A rejection that wins first prevents a concurrent stale acceptance
  from calling OCB. No database lock is held over external I/O.
- Successful OCB application is followed by one transaction for the immutable
  version, current asset pointer and `version_applied`, linked by `sourceTaskId`.
  If this transaction fails, its writes roll back but the accepted intent remains.
  Retrying the same decision uses the frozen candidate: an OCB conflict can be
  recovered only after the live package is proven equivalent to that exact
  candidate. A recorded version also requires this verification before completing
  an interrupted task-state update. There is no distributed atomic transaction
  or automatic model/task replay.
  Version allocation and the source-Task idempotency check are protected by the
  same asset-row transaction, including concurrent acceptance retries.
- An OCB failure/conflict produces `version_apply_failed`, never an applied version.
  The application request's `Idempotency-Key` identifies the attempt; callers
  retrying a request should reuse it. A new user submission gets a fresh key.
  Legacy clients without a key share a task-scoped logical attempt. A repeated
  failure outcome for the same attempt is recorded once. Applied versions and
  accepted/rejected decisions remain task-scoped and cannot duplicate on retry.

The common writer requires the caller's database transaction and propagates
persistence errors. The event key is a SHA-256 digest with a unique constraint.
Decision/version/final-report audit transactions sharing one SQLite connection
are queued locally; MySQL/ZDAS use database row locks for cross-worker exclusion.
Snapshots retain Skill name/description, tenant authorization identity, Bot and
OCB Skill ID, explicit actor type/identity, task/version links and operation time.
System callbacks have a system actor, not an invented human operator.

## Test Bench association (new operations only)

When orchestration stops iterating Optimize, it freezes the **selected producer**
in `config.skillAuditTestBench` under a task-row transaction. This is the actual
builtin Optimize Step, or a verified same-owner/task Optimize replacement run;
an Optimize postprocessor keeps its successful same-round predecessor as the
Bench producer. Continuing rounds do not select a result. No task-wide latest
round lookup, score ranking, model selection, or scoring policy is introduced.
The snapshot contains only `taskId`, `stepId`, `round`, and the producer's reported
`scoreComparison` (`name`, `baseline`, `candidate`, `delta`). Missing scores stay
null; no score or delta is calculated. A producer with no score is distinguishable
from an event with no recorded association.

New termination/accept/reject/application-failure events copy that frozen snapshot
into `detail_json.testBench` in their existing business transaction. Application
copies the exact accepted event's association in the version transaction. Repeated
selection of the same Step does not refresh scores; a different selection cannot
overwrite it. Finalize retries preserve the selected evidence. Terminal historical
tasks without a frozen association are not backfilled by repeated callbacks,
decisions, or GETs. Registration/start events have no selected evaluation.

The event API exposes optional `testBench` through a strict field whitelist; it
never returns arbitrary `detail_json`, report output, package URLs, or errors.
It never joins a live Step to construct an old event. Skill events and the version
list use the same `TestBenchComparison` UI component. Missing historical links
show **未记录评测关联**; a linked producer without scores shows **未评测**;
individual missing numbers show **—**, not 0. The existing version-list scores,
event types, operation results and selection semantics remain unchanged.

## Read and compatibility boundary

`GET /api/evolve/skill-events` only selects persisted audit rows for the authenticated
registrar/asset tenant. Bot Owner is optional **current display metadata**, resolved
independently from OCB; it is never substituted for authorization or actor identity.
Event descriptions/results/version references are read from the event snapshot.
The internal client supports nullable versions and the event type/result fields.
Task links use the recorded task ID; version application records carry the exact
version ID. Existing version content and frozen-baseline diff APIs are unchanged.

Verification: isolated real SQLite repository/HTTP tests cover transaction failure,
replayed callbacks and decisions, CAS/application failure, exact-candidate recovery,
no-backfill and tenant isolation. MySQL/ZDAS DDL rendering is covered; a live MySQL
server and live OCB/model execution are not part of these regression tests.
Test Bench regressions additionally cover immutable selected-round evidence,
concurrent selection, finalize recovery, owner/task/run/phase/status guards,
public field whitelisting, the real full/postprocess router path, and identical
positive/negative/zero/missing-score rendering in both lists.
