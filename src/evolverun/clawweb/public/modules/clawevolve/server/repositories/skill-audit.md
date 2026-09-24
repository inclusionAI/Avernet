# Skill business-event contract

`ce_skill_events` is the canonical Skill history store. It records one row for
registration and one row for each top-level Skill diagnosis or optimization task.
Internal Stage, round, candidate-decision and version-application transitions update
that task row; they never become additional user-facing events.

Migration 127 folds historical operation rows into this model. Runtime code reads
and writes only `ce_skill_events`; the former append-only table remains an inert
rollback snapshot and is not a compatibility source.

## Write boundary

- Registration and its baseline version commit with one completed `registered` row.
- Skill diagnosis creation commits one `diagnosis` row; full Skill evolution
  creation commits one `optimization` row. Both link the exact frozen
  `targetSkill` baseline. Stage integration tests are excluded. Retries update the
  same event ID and return it to `running`.
- Task lifecycle maps to `running`, `waiting_user_input`, `waiting_acceptance`,
  `completed`, `failed` or `canceled`. Outcomes distinguish no cases, no
  improvement, rejection, successful application and application failures.
  A finalize report, candidate reference, waiting state and event commit together.
  A no-cases Diagnose completion (builtin, replacement, or postprocessor) also
  commits its Step report, Task completion and `no_cases` event together. Audit
  failure rolls back the Step, so the same report can be retried without invoking
  a model or adding a new Step. Successful duplicates do not add events.
- An accepted decision is durable intent, **not** proof of an Host write. It is
  stored on the optimization event before the external CAS. Rejection updates the
  same event and completed task state without creating a version or writing Host.
  Both decisions lock the same Task row and recheck its frozen configuration and
  persisted decisions inside the transaction. Once acceptance is committed,
  rejection is forbidden even if Host or the final Task update subsequently fails.
  A recorded application also blocks rejection for interrupted older writes.
  This is not cancellation or rollback: retry acceptance against the same frozen
  candidate. A rejection that wins first prevents a concurrent stale acceptance
  from calling Host. No database lock is held over external I/O.
- Successful Host application is followed by one transaction for the immutable
  version, current asset pointer and the optimization event's `version_to`, linked by `sourceTaskId`.
  If this transaction fails, its writes roll back but the accepted intent remains.
  Retrying the same decision uses the frozen candidate: an Host conflict can be
  recovered only after the live package is proven equivalent to that exact
  candidate. A recorded version also requires this verification before completing
  an interrupted task-state update. There is no distributed atomic transaction
  or automatic model/task replay.
  Version allocation and the source-Task idempotency check are protected by the
  same asset-row transaction, including concurrent acceptance retries.
- An Host failure/conflict keeps the event at `waiting_acceptance`, records the
  failure in `outcome`, and never claims an applied version.
  The application request's `Idempotency-Key` identifies the attempt; callers
  retrying a request should reuse it. A new user submission gets a fresh key.
  Legacy clients without a key share a task-scoped logical attempt. A repeated
  failure outcome for the same attempt is recorded once. Applied versions and
  accepted/rejected decisions remain task-scoped and cannot duplicate on retry.

The common writer requires the caller's database transaction and propagates
persistence errors. Registration and task business keys are unique; `task_id` is
also unique, enforcing one event per top-level Skill task.
Decision/version/final-report audit transactions sharing one SQLite connection
are queued locally; MySQL/ZDAS use database row locks for cross-worker exclusion.
Snapshots retain Skill name/description, tenant authorization identity, Bot and
Host Skill ID, explicit actor type/identity, task/version links and operation time.
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

Lifecycle, decision and application updates retain that frozen snapshot in
`detail_json.testBench` on the same business event. Repeated
selection of the same Step does not refresh scores; a different selection cannot
overwrite it. Finalize retries preserve the selected evidence. Terminal historical
tasks without a frozen association are not backfilled by repeated callbacks,
decisions, or GETs. Registration/start events have no selected evaluation.

The event API exposes optional `testBench` through a strict field whitelist; it
never returns arbitrary `detail_json`, report output, package URLs, or errors.
It never joins a live Step to construct an old event. Skill events and the version
list use the same `TestBenchComparison` UI component. Missing historical links
show **未记录评测关联**; a linked producer without scores shows **未评测**;
individual missing numbers show **—**, not 0. Existing version-list scores and
Test Bench selection semantics remain unchanged.

## Read boundary

`GET /api/evolve/skill-events` and `GET /api/evolve/skill-assets/:assetId/history`
select persisted business-event rows visible to the authenticated owner or an
accessible team space. Bot Owner is optional **current display metadata**, resolved
independently from Host; it is never substituted for authorization or actor identity.
Event descriptions, outcomes and version references are read directly from the
canonical row. There is no read-time legacy relabeling or version inference.
Task links use the recorded task ID; version application records carry the exact
version ID. Existing version content and frozen-baseline diff APIs are unchanged.

Verification: isolated real SQLite repository/HTTP tests cover transaction failure,
replayed callbacks and decisions, CAS/application failure, exact-candidate recovery,
no-backfill and tenant isolation. MySQL/ZDAS DDL rendering is covered; a live MySQL
server and live Host/model execution are not part of these regression tests.
Test Bench regressions additionally cover immutable selected-round evidence,
concurrent selection, finalize recovery, owner/task/run/phase/status guards,
public field whitelisting, the real full/postprocess router path, and identical
positive/negative/zero/missing-score rendering in both lists.
