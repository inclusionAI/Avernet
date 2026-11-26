# Single-table Service Bot release audit

## Accepted scope

Supersedes the two-table proposal in PR #3976.
Keep only `ac_service_bot_release_audit`: insert one attributed draft/submission row and update that same change when publication ends. Preserve all identity, environment, version, stage and time fields. Keep `change_id` to separate retries and stages.

Delete the event table/model/envelope, revision/snapshot copies, delivery handler, scanner, queue registration and their tests. Do not introduce a replacement queue, bus, background scanner or SLS implementation.

## Transaction and Plugin contract

Native publication writes keep the existing `DatabasePlugin.orm_session()` semantics, including production AUTOCOMMIT. Only the separate audit operation uses `DatabasePlugin.transactional_orm_session()`. Inject the audit Plugin into that repository; call `create(session, snapshot)` or `complete(session, ...)` in a separate session after the native transaction commits. Plugins must not open or commit an independent session. Only the separate failed audit transaction rolls back, logs a warning and leaves the committed native write intact. New native attempts retain their new correlation IDs even if audit INSERT fails. Native publication/artifact failures still propagate. A rejected native CAS produces no audit mutation.

Freeze authenticated actor, original creator and system environment at submission. Persist the active change and trusted tenant alongside the publication correlation metadata for background completion; restore that tenant only for the bounded audit update. Terminal updates are conditional on pending state and expected stage; duplicate or old results cannot replace the first completion time. Existing workflow/change fences and artifact-last serialization remain intact.

## Plan and verification

1. Write real SQLite single-row insert/update tests and failure-isolation cases; observe failure before implementation.
2. Replace the Plugin event-output contract with create/complete participating in the caller transaction. Remove the event DTO and schema.
3. Rewire production directly, remove delivery/DI lifecycle/queue, and reduce the migration to the single audit table. Update owning contracts and tests.
4. Verify source identity/env, creator/actor retention, expected-stage and attempt fences, first-completion idempotency, cross-tenant background scope, and AUTOCOMMIT post-commit isolation through the explicit transaction capability.
5. Run focused suites, architecture/contracts, static/credential checks, full Backend coverage, then open a REL20261127 PR from the rebased branch and address actual CI failures without weakening gates.

## Compatibility and diagnostics

The earlier PR remains unmerged. This change supplies a fresh-install single-table migration; it does not execute DDL, drop a deployed preview table or backfill invented history. Existing old preview event data, if any, needs a separately authorized migration.

Use existing logging for audit create/complete request, success, duplicate and failure. Include change/publication/Bot/version/env/result/time context, never credentials or raw publication ext. No runtime/engine, OCB gitlink or public API change is required.

## Verified legacy constructor compatibility

An existing enterprise compatibility test directly constructs the repository with only DB/offloader. That unattributed, non-audited use remains valid without a Plugin. Missing Plugins skip auditing with a warning, preserving native operations. The configured DI provider still requires the selected Plugin and supplies it explicitly. No enterprise source/test edit is needed.

## 2026-11-26 CR revision

Accept CR 4705107662: snapshot/create/complete errors, missing schema and missing Plugin must not abort existing draft/status/ext operations. Use a separate post-commit transaction to isolate flush, connection and transaction failures. Recheck committed status/ext before observing; never overwrite newer publication data. Audit gaps are logged; no retry guarantee, queue or worker.

Accept CR 4705374174: remove artifact-specific key manipulation; prepend audit metadata while preserving remaining ext values/order. Verify inline/offloaded artifact CAS.

Tests: draft/start/completion, partial Plugin writes, missing table/Bot/Plugin, native write failure, tenant/restart fences and duplicate completion. Rebase on GitHub REL20261127; one topic commit. No OCB gitlink/worktree.

## Restore legacy publication sessions (2026-11-26)

Restore the five publication sessions changed by this feature: insert, status, status+ext, ext CAS, and status+ext CAS. Leave pre-existing explicit transactions in other methods untouched. Audit persistence remains separately transactional and failure-isolated after native session exit. Verify actual AUTOCOMMIT writes, persisted new change markers, rejected CAS, retries and artifact handling. Do not introduce a new lock or change legacy rollback/visibility semantics.

AUTOCOMMIT concurrency: prepare correlation metadata as a pure serialized payload and write it with status in the same native UPDATE, never through a later ORM dirty flush. The status-only entrypoint uses an ext CAS and rereads on contention so another successful native ext write survives; explicit status/ext CAS callers retain their rejection semantics. Verify deterministic interleavings before and after SQL execution with a second connection.
