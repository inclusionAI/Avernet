# Service Bot release audit implementation

## Boundary

Existing flow: publication requests create version rows and enqueue durable build/release/poll tasks. Status and ext changes are optimistic repository updates. The permitted addition is authenticated actor attribution, transactional audit event production, an outbound audit Plugin and recoverable projection. No Engine, Relay, BaaS protocol, UI, public query API or OCB gitlink is changed.

## Implementation

- Immutable kernel snapshots contain Bot/creator/actor identity, source and desired stages, numeric version, system environment, submission and terminal times, result, and explicit JSON metadata. Audit schema and event schema use tenant/env/change keys.
- Publication insert/status-CAS transactions append complete audit events. Draft creation emits revision 2 immediately; submissions revision 1; successful terminal transitions revision 2. New retries get a new change marker. Rejected CAS emits no event. An outbox insert failure rolls back the business write.
- Real authenticated actor flows through ordinary and administrator entries, version upgrades, service conversion, offline redraft and its approval callback. Execution Owner is not substituted for authenticated actor. Legacy internal calls without an actor retain their previous behavior and do not invent historical attribution.
- The database Plugin uses revision CAS and full snapshots, supporting duplicate, out-of-order and concurrent at-least-once delivery. Same-revision conflicting content raises an error. A paired local implementation reuses the same ORM body.
- A lifecycle scanner recovers committed pending events and submits bounded, idempotent persistent delivery tasks. It prioritizes least-attempted rows so a failed batch does not starve later events. Delivery restores the event tenant and only acknowledges after Plugin success.
- Retry-to-stable-status paths wait for actual restart confirmation, not the intermediate rollback to success. Completion checks the originally observed change/workflow before updating its marker. A restart submission failure closes audit as failed without inventing a different native publication status.
- Production DDL is supplied in `sql/2026_11_13_service_bot_release_audit.sql`. It has not been applied to any remote database. SLS output remains a future Plugin implementation.

## Verification evidence

- Producer TDD: draft creation expected 1 event and observed 0 before implementation; then PASS.
- Actor TDD: missing authenticated attribution reproduced in submission/retry tests; related service/router suites 461 passed.
- Sink/DTO tests: 21 passed; real SQLite, concurrent duplicate insertion, revision conflict, tenant isolation, fault/retry and credential-free logs.
- Producer/flow integration: 195 passed, including observed old workflow while a newer retry is created, and failed retry submission preserving native status.
- Plugin consumer/delivery/original repository focused suite: 83 passed.
- Fairness: 102-event failure backlog reproduced starvation before the order fix; then PASS.
- Rollback test proves an event insert exception also rolls back publication status; old expected_ext cannot terminalize a new retry.
- Ruff focused new files and `git diff --check` passed.
- Full architecture and full Backend results are recorded separately by the orchestration/review reports. No remote runtime/deployment validation is claimed here.

## Diagnostics

Events: `release_audit.event_staged`, `event_delivered`, `delivery.request`, `delivery.success`, `delivery.failed`, `delivery.duplicate`, `delivery.conflict`, `scan_failed`. Stable event/change/publication/Bot/environment/tenant/actor/stage/revision fields identify one change. Sink outcomes include elapsed time; driver exceptions are logged by category because raw exception strings can embed SQL-bound data. Raw request/config/publication ext, tokens and reusable authentication materials are not copied into audit snapshots or logs. Database/Plugin failure, duplicate and sensitive-extension tests validate these choices.

## Limitations

The local transaction does not atomically commit a remote BaaS action. Existing operation recovery remains responsible for observing remote results after a local failure. A queue deadline alone is not a business failure and does not produce a fabricated audit terminal state. Existing pre-feature rows are not backfilled. Audit completion means the business publication status is confirmed; subsequent cleanup may still run.

## Source hygiene

All new source files are below the 1,000-line source gate. Existing large service/router files receive only required actor parameters or audit completion wiring; no unrelated structural refactor or formatting is included. The changed publication repository remains below 1,000 lines.

## Independent review corrections

- Removed ambient request-tenant filtering from trusted persisted change/publication correlation; the submission snapshot supplies its original tenant when a background task has no request context. Real external-tenant submission/default-context terminal transition reproduced the error before the fix and passed afterwards.
- Switched all five audit-producing publication write units to the existing `transactional_orm_session()` capability. Ordinary ORM sessions may AUTOCOMMIT in production. A real AUTOCOMMIT SQLite engine with a distinct explicit-transaction entrypoint proves outbox failure rolls back status; repository and producer suite: 61 passed at that revision.
- Fixed artifact ordering after an audit change marker is added. Raw inline/offloaded artifact representations remain last, matching `ConfigArtifactOffloader.prepare`; otherwise an unchanged ext fails its serialized CAS. Both representations failed before the fix; producer plus the original online-channel HTTP endpoint: 16 passed.
- Preserved source Bot creation timestamps with unknown timezone instead of labeling naive source values UTC; new event times remain UTC. Added terminal version/before/final/time fields to sink logs and assertions. Sink/DTO suite: 23 passed.
- Added real lifecycle startup/recovery/shutdown and scanner-failure behavior tests. Delivery/consumer suite: 10 passed.
- Updated declared Context Boundary dependencies without exemptions; focused producer/module-boundary suite: 15 passed. The first full architecture run reported the missing declarations (425 passed/1 failed), which these declarations correct; final full rerun belongs to the regression report.
