# Single-table Service Bot release audit

## Accepted scope

Supersedes the two-table proposal in PR #3976.
Keep only `ac_service_bot_release_audit`: insert one attributed draft/submission row and update that same change when publication ends. Preserve all identity, environment, version, stage and time fields. Keep `change_id` to separate retries and stages.

Delete the event table/model/envelope, revision/snapshot copies, delivery handler, scanner, queue registration and their tests. Do not introduce a replacement queue, bus, background scanner or SLS implementation.

## Transaction and Plugin contract

The publication repository owns `DatabasePlugin.transactional_orm_session()`. Inject the audit Plugin into that repository; call `create(session, snapshot)` or `complete(session, ...)` using its active session. Plugins must not open or commit an independent session. Audit insert/update failure rolls back the native publication write. A rejected native CAS produces no audit mutation.

Freeze authenticated actor, original creator and system environment at submission. Persist the active change and trusted tenant alongside the publication correlation metadata for background completion; restore that tenant only for the bounded audit update. Terminal updates are conditional on pending state and expected stage; duplicate or old results cannot replace the first completion time. Existing workflow/change fences and artifact-last serialization remain intact.

## Plan and verification

1. Write real SQLite single-row insert/update tests and transaction failure cases; observe failure before implementation.
2. Replace the Plugin event-output contract with create/complete participating in the caller transaction. Remove the event DTO and schema.
3. Rewire production directly, remove delivery/DI lifecycle/queue, and reduce the migration to the single audit table. Update owning contracts and tests.
4. Verify source identity/env, creator/actor retention, expected-stage and attempt fences, first-completion idempotency, cross-tenant background scope, and AUTOCOMMIT rollback through the explicit transaction capability.
5. Run focused suites, architecture/contracts, static/credential checks, full Backend coverage, then open a dev PR from the rebased branch and address actual CI failures without weakening gates.

## Compatibility and diagnostics

The earlier PR remains unmerged. This change supplies a fresh-install single-table migration; it does not execute DDL, drop a deployed preview table or backfill invented history. Existing old preview event data, if any, needs a separately authorized migration.

Use existing logging for audit create/complete request, success, duplicate and failure. Include change/publication/Bot/version/env/result/time context, never credentials or raw publication ext. No runtime/engine, OCB gitlink or public API change is required.

## Verified legacy constructor compatibility

An existing enterprise compatibility test directly constructs the repository with only DB/offloader. That unattributed, non-audited use remains valid without a Plugin. Attributed starts or active-change completion fail closed when the Plugin is absent. The configured DI provider still requires the selected Plugin and supplies it explicitly. No enterprise source/test edit is needed.
