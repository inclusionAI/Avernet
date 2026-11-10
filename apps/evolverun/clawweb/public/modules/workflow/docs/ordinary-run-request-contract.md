# Internal ordinary run request association v1

This API associates an ordinary workflow request key with a `flow_runs` row. It
**registers a run only**: success is not evidence that the workflow was
scheduled, started or completed. It does not use the trial or fanout child
lifecycle, and does not cancel any child.

The router supports an injected `OrdinaryRequestAuth` with `authenticate(req)`
and `authorize(principal, { requestKey, parent, child }, tx)`. The host must
supply an independently verified Bot/owner principal and authorize this exact
parent run, child run/workflow and request key using trusted authority data.
The immutable `child.identity` passed to the authorizer contains `originBotId`,
`userId`, `originSessionKey` and `originSessionId` (each a string or null). The
host must verify every field against trusted Bot/owner, user and session bindings;
null values are not automatic authorization. On create these fields are the
frozen proposed values that will be inserted, not proof of identity. On retry
and lookup they come from the locked, persisted child row, never from a retry's
claims. The identities are not returned to the caller or logged by this API.
Authorization is checked inside the same database transaction on create,
retry and lookup. On create, the child ID is proposed and the child row does not
yet exist, so the host must authorize that prospective ID and workflow.
On MySQL, the host authorizer must use current locking reads (for example,
`SELECT ... FOR UPDATE`) for every mutable authority, ownership, and session
binding on which it relies, and retain the locks through the transaction. A
plain consistent read under `REPEATABLE READ` may see an older snapshot and is
not sufficient to prevent concurrent revocation. Authorization sources outside
this transaction need an equivalent independently verified consistency/fencing
mechanism; the supplied transaction alone does not provide one. The host must
also validate behavior for absent bindings and its configured isolation level.
Untrusted request metadata is not proof of authority. The public host does not
inject this capability: the new routes return **503** by default. The existing
engine signature is a shared credential and does not bind the method or path. Including an operation in a signed body is not a fix
for that protocol limitation. **Migration to a signed method/path with replay
protection is a hard blocker for production rollout**, along with trusted
transport and host authentication and authorization. This change must not be
represented as safe for production merely because its body is nested. In
particular, the shared signature can be replayed against the existing
`PUT /api/internal/runs/:flowId/retry-failed` route, which does not consume the
body; nesting the register fields cannot prevent that replay.

## Register

`POST /api/internal/runs/request-association/register`

```json
{
  "type": "ordinary-run-request.register.v1",
  "ordinary_request_key": "stable-request-key",
  "ordinary_parent_flow_id": "parent-flow-id",
  "run": {
    "flow_id": "proposed-child-flow-id",
    "workflow_id": "child-workflow-id",
    "status": "pending",
    "params_json": "{}",
    "input_json": "{}"
  }
}
```

The top-level envelope has exactly `type`, `ordinary_request_key`,
`ordinary_parent_flow_id` and `run`. The operation type, key, parent, and the
`run` object’s flow ID, workflow ID and status are required. All run fields are
nested under `run`; unknown body fields and query parameters are rejected.
The association does not store a fanout node, group execution, item, or attempt.
It therefore cannot alone prove that the registered child belongs to a specific
fanout item; matching a key, parent and workflow is not sufficient for that
purpose. Do not enable ordinary fanout visibility or a production launcher
until item-level attribution is independently established.

The key is globally unique, 1–128 characters from `[A-Za-z0-9_-]`. Parent,
child and workflow IDs must be 1–128 ASCII characters from `[A-Za-z0-9_.:-]`;
parent and child IDs must differ. Other optional registration fields follow the
existing `/runs` insert contract: `workflow_title`, `state_json`, `triggered_by`,
`params_json`, `input_json`, `node_count`, `identity_key`, `current_phase`, `started_at`,
`credentials_json`, `origin_session_key`, `origin_session_id`, `origin_bot_id`,
`user_id`, `plugin_version`, `engine`, `workflow_version`, and
`workflow_deploy_number`. `node_count` is a nonnegative signed 32-bit integer;
`started_at` is a nonnegative safe integer. The only accepted initial `status`
is `pending`; registration alone cannot establish that execution started.
`params_json`, `input_json` and `credentials_json`, when provided, must each be
a JSON object encoded as a string. Empty, malformed or non-object JSON is
rejected. If credentials contain `STAGE`, it must be a string; for non-personal Bots
its trimmed value must be `draft`, `verify`, or `online`. Personal Bots may
have an empty stage. JSON strings must remain byte-identical on retry.
Native subworkflow parent metadata in either `state_json` or
`params_json._clawmind_state`, when present, must match the declared parent;
providing both does not make either one authoritative over the other. Known
trial identity/context markers or trial session identifiers anywhere in
either state are rejected conservatively; this can reject business data using
the same marker field names. Each state is limited to 50,000 object/array nodes. Existing state and version validation still applies.

The key binds the parent, workflow, and every persisted input and identity field
listed above except `started_at`. A retry may propose a different `flow_id` and
`started_at`; it must keep every other field unchanged. Omitted and null optional
fields are equivalent; absent/null `node_count` becomes zero. In particular,
`state_json`, `credentials_json`, `current_phase`, status and plugin metadata must not change.
Omitted or null `current_phase` retains compatibility with previously stored
fingerprints that did not include a phase.
The association table stores SHA-256 hashes of the key and the frozen fields,
not raw parameters or credentials. These hashes are not encryption.

Initial registration returns 201; an exact retry returns 200 with the original
IDs and `created:false`:

```json
{"success":true,"data":{"created":true,"parent_flow_id":"parent-flow-id","flow_id":"original-flow-id","workflow_id":"child-workflow-id"}}
```

Trial fields (`run_request_id`, `run_request_parent_flow_id`,
`run_request_bot_id`, `run_request_owner_id`, `candidate_commit`, `pack_digest`,
`input_digest`) cannot be combined with this mode. Legacy implicit trial
registration is checked through the existing trial registration logic and
rejected, not bypassed. Trial or unauthorized parents are concealed with 404.
The legacy `POST /api/internal/runs` rejects these ordinary association fields;
clients must not use it for this protocol. Old servers do not have the dedicated
register path and return 404 rather than creating an unassociated run. The trusted
engine route has a separate, server-validated `parent_relation` receipt for
subworkflow run links; it is not part of this ordinary request-association
contract and must not be added to this route's envelope.

## Lookup

`POST /api/internal/runs/request-association/lookup`

```json
{"type":"ordinary-run-request.lookup.v1","ordinary_request_key":"stable-request-key"}
```

The signed body must contain exactly these two fields. A successful lookup
returns 200 after authenticating and authorizing the stored parent and child:

```json
{"success":true,"data":{"parent_flow_id":"parent-flow-id","flow_id":"original-flow-id","workflow_id":"child-workflow-id"}}
```

The client should compare `parent_flow_id` to the expected parent. Responses do
not contain parameters, input, state, credentials, or raw request key. New
endpoints use `Cache-Control: no-store`. Invalid input returns 400; missing
principal 401; absent or unauthorized relation 404; changed input/range or
explicit trial identity 409; unconfigured auth, storage failure or authorization
failure 503. A missing association and a storage failure are never conflated.

## Storage and rollout

Migration 150 creates `ordinary_run_requests`; migration 151 adds nullable
incarnation markers to associations and `flow_runs` on SQLite and MySQL.
Before recording v150, the migration also inspects an existing association table:
required identifiers and fingerprints must be non-null, with TEXT affinity on
SQLite or sufficiently wide CHAR/VARCHAR on MySQL. The request key and child
ID must have full single-column uniqueness (the SQLite request key is the sole
primary key); MySQL must use InnoDB. This v150 check does not require v151
markers or inspect `flow_runs`. Extra non-null association columns that are not
supplied by the insert must have a usable default (or be database-generated).
Generated columns are not writable substitutes for required association or
marker columns. A confirmed incompatible table defers v150 and
all later versions without deleting or rewriting rows. Operators must repair it
and rerun migrations. Metadata read and ledger write failures propagate rather
than being classified as an authorization denial or compatible schema.
Migration 151 only adds columns and can resume after partially applied MySQL
DDL. Concurrent instances that race to add a column re-inspect it only after an
explicit duplicate-column error. An existing or concurrently added marker must
also satisfy the runtime marker-column contract (SQLite non-primary-key TEXT affinity, nullable with no
non-null default; MySQL nullable CHAR/VARCHAR of at least 64 characters with no
non-null default). Incompatible marker definitions defer v151 without modifying
existing data or advancing the ledger; an operator must repair the schema before
retrying. If CREATE on the v150 association table or
ALTER on a v151 marker is explicitly denied, the affected version remains
unrecorded and later migrations stop, so the ledger's maximum version cannot
advance past the incomplete migration. Existing database
operations remain available; ordinary requests fail closed while their schema is
incomplete. After an operator provisions the missing schema, restart migration
to record the deferred version and resume later versions. Some MySQL privilege
configurations may still reject v150's `CREATE TABLE IF NOT EXISTS` even when
the table is provisioned; in that case an operator must grant the required
CREATE privilege for migration, or provision the full schema and use the
external-schema initialization strategy. The latter does not advance the
migration ledger or run subsequent migrations. Other migration, connection, and
write errors are not treated as ALTER permission denials. An explicit SQLite
BUSY or READONLY error during v150/v151 or their ledger insert also defers that
version after a successful ledger read; later versions stop. An unavailable
ledger, other I/O failures, or an ambiguous already-advanced ledger are not
reported as a successful deferral. If migration DDL or its ledger insert returns SQLITE_BUSY
and a peer has recorded that version or advanced through known version 151,
matching ledger entries and read-only schema verification for each known version
allow this instance to stop without applying later versions. Unexpected higher
versions remain fail-closed. This confirms neither host authorization nor
ordinary-request readiness.
This does not change errors in earlier or unrelated migrations. After a SQLite
lock or read-only deferral, trigger setup verifies existing definitions without
issuing trigger DDL. A lock, read-only, or incompatible-schema deferral makes
`initDatabase()` fail and close the connection without caching a ready
database, even when the existing triggers are compatible. Direct callers of
`runMigrations()` do not receive that initialization guarantee. If a
required trigger for a present table is missing or incompatible, initialization
also fails. Normal trigger creation failures stop initialization. After
correcting the cause, explicitly reinitialize or restart the process; there is
no background retry. The CLI exits on initialization failure, so automatic
recovery depends on an external process manager. Strict definition checks can
reject externally customized triggers and must be validated against the target
schema before rollout.

These metadata checks do not prove that arbitrary existing CHECK constraints,
triggers, or default expressions accept the operation or preserve its semantics.
Operators must review and test externally provisioned schemas before enabling
ordinary requests. A failed operation is not evidence of an absent association.

It does not delete or backfill existing rows: legacy associations lack
verifiable incarnation evidence and fail closed on lookup and retry. Operators
must resolve them through an independently verified recovery procedure; do not
copy identifiers or assign markers based solely on matching flow IDs.

Registration locks the parent and writes unpredictable 256-bit markers for the
parent and the new child in the same transaction as the association and child
insertion. Multiple associations for an existing parent share its marker.
Lookup and retries lock and verify both persisted run markers before authorizing
or returning an association; deleting and recreating either run with the same ID
cannot inherit the old relationship. Errors roll back all writes. A duplicate-key
race is retried once from a fresh transaction.

Every operation checks full unique indexes and marker columns; externally
provisioned schemas must provide nullable marker columns without a non-null
default. MySQL also requires sufficient column widths, strict SQL mode and
transactional InnoDB tables. SQLite is exercised with real transactions.
Isolated MySQL 5.7.24 checks exercised the service, migration re-entry,
concurrency, permission-denial and v150/v151 schema compatibility checks. These
checks do not establish production readiness. Before rollout, validate the target
MySQL version, its configuration, isolation and uniqueness races, and run complete HTTP/authentication and
authorization end-to-end tests. ZDAS and noop are unsupported and fail closed.
DDL-denied installations
must provision the migration 151 schema before enabling a trusted host. Do not
interpret an arbitrary 404 from an older server as proof that no run exists if
the caller cannot establish which protocol version is deployed.

## Experimental one-shot dispatch reservation

The fanout group CAS also accepts an experimental one-shot claim for an ordinary
launcher that cannot idempotently create a native run. One update changes one
previously `queued` item to `dispatch_pending`, adds a nonblank
`dispatchClaimToken`, and updates the cleanup projection. The request must not
change other item or group fields. The update is conditional on the parent still
being `running` and the group deadline not having expired at the database write.
Once present, the claim cannot be removed or replaced. A nonterminal item cannot
move back to `queued` after dispatch begins. The legacy managed-child creation
path rejects new creation and first execution for a claimed item, while retaining
read-only reuse and normal progress of an already started matching managed child.
An existing child with the legacy child ID prevents a new claim. A claim is an
admission record, not proof that a native run was created or that a child finished.

The older `queued` to `dispatch_pending` transition without a claim remains
supported for existing idempotent runtimes. An already pending item without a
claim cannot be retroactively claimed: it may already have launched. A caller
that loses a claim response or crashes after a successful claim must not infer
that no native run exists and must not blindly retry the non-idempotent launch.
Ordinary run creation rejects initial child `dispatchClaimToken` fields; this
alone does not establish that a supplied group or item has trusted provenance.
The item-level attribution and production authentication limitations above
continue to apply. Real MySQL/ZDAS lock/deadline behavior requires validation on
the target database before production use.
