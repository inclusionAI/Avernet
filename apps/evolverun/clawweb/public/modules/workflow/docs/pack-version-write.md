# ClawWeb complete workflow version writes

## Boundary and ownership

This is a ClawWeb Service API and Git adapter contract. No TaskGuard or ClawMind
client changes are required. Existing `workflow_deploy_history` actions, snapshot
API semantics and tag naming remain unchanged. Unfinished operations are stored
only in `clawweb_pack_operations`; they are not releases and never appear in the
shared deployment history.

A trusted host verifies the browser's existing login and applies the existing
workflow edit permission. The configured server Git credential is reused. There
is no second Git token, independent Pack ACL, client-selected repository or
client-supplied actor. Editors may change all regular files in the selected Pack
snapshot, including shared scripts and binary resources, subject to safe paths,
Git modes and documented size limits. Workflow identity remains bound to the URL.

## Read and save

`GET /api/workflows/:workflowId/pack-version?version=current|draft` returns the
complete file inventory at one commit. `current` means `wf/<id>` and `draft`
means `saved/<id>`; a missing ref is not replaced with the other ref. The trusted
host advertises `canWrite: true` only when it has installed the save handler.

`PUT /api/workflows/:workflowId/pack-version` accepts:

- `requestId`: client-generated stable ID (8–128 ASCII letters, digits, `_`, `-`);
  optional for legacy clients, generated server-side when absent;
- `sourceVersion`, `sourceCommit`: the snapshot actually opened by the editor;
- `expectedSavedHead`: exact saved head, or `null` when absent;
- `changes`: explicit create/replace/delete operations with Pack-relative paths,
  canonical Base64 bytes and regular Git mode `100644` or `100755`.

The browser does not write DB and Git as separate requests. File edits and canvas
spec edits use one server-side prepare/journal/push/complete pipeline. A successful
save changes only `saved/<id>` and the database draft projection, never `wf` or a
deployment tag. Untouched Pack files and their modes remain intact.

The existing `/api/auto-heal/save` is a compatibility adapter over that pipeline.
Its spec-only request preserves all other Pack contents and supports initial Git
snapshot creation. `createOnly: true` atomically creates a new workflow row, its
creator permission and journal identity without upserting an existing ID; same-request
retries preserve that identity. Confirmed pre-push preparation failures are retryable
without a second workflow creation. It optionally accepts a source
commit and expected saved head for optimistic concurrency.

## Deployment

`POST /api/auto-heal/deploy` accepts the existing fields plus `candidateCommit`
and `requestId`. New UI callers confirm a specific saved commit S. Legacy callers
without a commit capture the saved head once at operation start; they do not
rebuild the release from mutable DB YAML. If no saved snapshot exists, save first.

The service verifies that S is the current saved head or a completed save of the
same workflow instance. It builds a release commit with **exactly S's tree**, and
atomically pushes `wf/<id>` with its exact expected-head lease and a new immutable
`deploy/<id>/#N` tag. A later draft S2 neither substitutes for S nor gets deleted.

The final database transaction inserts the normal `action=deploy` record, switches
the active release and updates the workflow version. If a newer saved draft
already existed at deployment acceptance, its draft spec is retained; the release
history remains the authoritative published snapshot. Optional rerun is attempted
only after release completion; it is not part of the Git/DB transaction.

## Private journal and recovery

The journal records request identity, actor, input digest, the real workflow row
identity and DB baseline, active-release baseline, exact target objects, a bounded
self-contained Git bundle, and the final response. Bundles may contain repository
history and must receive the same access controls and retention policy as the
repository; they must never be included in HTTP responses or logs.

Phases: `preparing → prepared → pushing → completed`, or `failed` before a push,
or `unknown` after an ambiguous remote/DB outcome. A unique workflow lock blocks
other ClawWeb journal operations until the operation completes or is proven to
have failed before push. There is no time-based lock stealing. Preparation and
completion recheck the DB baseline; business writes and completion receipt commit
in one database transaction.

Each request also carries a server-side attempt generation. A confirmed pre-push failure may restart only with the same input and unchanged baseline, incrementing the generation. Stale prepare, claim, failure, recovery or completion workers cannot mutate the new attempt. Completion also compares the persisted target before business writes.

Same request ID with different input is a conflict. Confirmed requests return
their recorded result without another Git push, tag allocation or automatic run.
Uncertain requests retain their identity and are reconciled, not silently retried
as a new operation.

- `GET /api/workflows/:id/pack-operations/:requestId`: authenticated status only.
- `POST /api/workflows/:id/pack-operations/:requestId/reconcile`: same verified
  actor and the operation-specific permission (edit for save, execute or edit for deployment); applies the persisted target, never a newly
  generated commit or deployment number. Matching remote objects need no push;
  unchanged expected refs permit exact replay with leases; conflicting refs are
  not overwritten. Completion still checks the original database baseline.
- A preparation with no persisted target can be cancelled before any push; a late
  original worker then fails the journal transition before remote writes.

Writes require trusted Origin, a non-simple CSRF header and bounded JSON handling
in the host. GET must not perform reconciliation side effects. Failed recovery
returns an explicit status and retains the candidate; it is never converted into
a successful empty snapshot.

## Database and rollout

Migration **149** creates only the private journal. SQLite, MySQL and ZDAS DDL is
provided by `schema-pack-operations.ts`. Managed runtime accounts without DDL
permission require the deployment owner to apply the migration before rollout;
missing schema is a real failure, not an automatically ignored success. No PROD
DDL or deployment is performed by local unit tests.

The OCB host contains the authentication/composition root and delegates actual
save/deploy to the public workflow module, rather than keeping separate Git
implementations. Both repositories must build against matching, recorded revisions.
A local source overlay is development evidence only, not proof of the shipped
image's dependencies or the pre-release login environment.

## Limits and evidence

Git and SQL are not one distributed transaction. The journal makes partial
outcomes explicit and recoverable; it does not promise instantaneous atomic
visibility across arbitrary direct Git or DB writers. Ref leases and DB-baseline
checks detect competing state rather than force-overwriting it. External tools
retain their existing contracts; their internals are not rewritten for this API.

Local bare-Git + SQLite tests must cover complete files, binary/mode preservation,
S→S2 draft advancement, idempotency, conflicts, restart, remote success with lost
acknowledgement and DB failure. Actual managed DB behavior, image build, browser
SSO and pre-release requests require separate recorded verification. A mock
permission provider or passing disabled-route test is not deployment evidence.
