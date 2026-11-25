# ClawWeb Pack operations: retired migration 149

## Current scope

This describes the current working-tree implementation, not a deployed release.
The normal shared database migration runner reserves **v149** as retired; it does
not create, validate, or require `clawweb_pack_operations`, and it does not apply
or record v149. Current save and deploy services do not write a journal, but
they query a pre-existing journal at operation time as a retirement safety
preflight. The old schema and repository code may still be present in the source
tree; their presence is not evidence of active journal-backed recovery.

No production DDL is required by this behavior. Do **not** apply the old v149
DDL or manually stamp the migration ledger. The change also does **not** drop an
existing table, remove its rows, or undo an existing ledger entry. Do not drop or
alter an existing table as part of this update: any cleanup and treatment of
existing data require a separately reviewed decision. The runner's ordinary
behavior for other migrations is unchanged.

## Legacy journal preflight and rollout

The current `legacy-pack-operation-guard` checks the requested workflow before
DB draft save (including create-only), Pack-file save, or deploy changes state.
If a matching legacy row has a null or non-terminal phase (anything other than
`completed` or `failed`), or still has a non-null workflow lock even in a
terminal phase, the service rejects the operation with 409
`LEGACY_PACK_OPERATION_UNRESOLVED`. Terminal rows with no lock do not block;
rows for another workflow do not block this workflow. The check does not resolve,
modify, or delete any row. An authorized operator must investigate the original
operation and its Git and DB effects using an appropriate separately reviewed
legacy procedure; the current service's recovery endpoints cannot do this.

A positively identified missing `clawweb_pack_operations` table is accepted.
The guard runs per operation, not at startup; it performs no DDL and does not
introduce a startup schema dependency. It tolerates only the specifically
recognized SQLite or MySQL/ZDAS missing-table errors for this table. Permission,
connection, malformed-schema, other missing-table, and unrecognized error
formats propagate as failures rather than being treated as absence. MySQL/ZDAS
error-shape tests use mocked errors, not a live managed database; verify the
actual environment. The operator-supplied inspection reported the table absent
in one specific inspected PROD database. That is not evidence about any other
database or a substitute for checking each rollout target.

**Quiesce all older journal-writing service instances and other old writers
before switching to journal-free operations.** The preflight is a read, not a
lock or an atomic fence: an old writer can create or advance a row after the
check. Do not clear a lock, delete a row, or drop a table merely to bypass it.

## Operations and unknown outcomes

There is no durable Pack operation record, persisted workflow operation lock,
receipt, or journal-backed recovery in the active save/deploy path. A request ID
is correlation metadata, not an idempotency key. The operation status and
reconcile handlers supplied by the current service return
`OPERATION_RECOVERY_UNAVAILABLE` (503); their routes must not be treated as a
working recovery mechanism.

A lost response, an ambiguous Git push, or a failure after Git succeeds can leave
an unknown or partial outcome, including during create-only initialization of
the saved ref and database workflow. An error is not proof that nothing changed, and
repeating the same request ID does not safely replay a recorded result. Do not
blindly retry an uncertain create, save, or deployment. The responsible operator
needs to assess the relevant Git refs and, for create or deployment, database
state. An Editor Pack ID change also writes the database before the Git file
candidate, so it can leave the two identities split. After an incomplete rename
whose DB request was sent, the Editor blocks subsequent saves in that page
session and marks both local version states blocked. This guard and local drafts
are lost on refresh or close; it is not a persistent workflow lock and does not
fence other clients. No automatic reconciliation or documented repair procedure
is provided. See `pack-version-write.md` for the separate path-move behavior.

## Evidence and limitations

The current migration runner, preflight, and no-journal service tests describe
local code behavior; they do not establish what schema or service revision is deployed, nor
validate production Git or managed database behavior. Git and the database do
not share an atomic transaction. See `pack-version-write.md` for the actual
write paths and their failure boundaries.
