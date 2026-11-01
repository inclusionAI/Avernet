# ClawWeb workflow and Pack writes

## Scope

This document describes the current working-tree implementation, including
uncommitted changes; it is not a claim about a deployed release. Workflow YAML
and other Pack files have **separate** save paths. There is no durable Pack
operation journal or atomic transaction spanning Git and the database.

A trusted host supplies the authenticated actor and enforces its existing
security boundary. The workflow routes check permissions; the Git adapter uses
server-configured Git access. Clients do not choose the repository or actor.

Before create-only or existing-workflow DB save, Pack-file save, and deployment,
the active services preflight for unresolved legacy journal rows for that
workflow. A match returns 409 `LEGACY_PACK_OPERATION_UNRESOLVED`; a specifically
recognized missing journal table is accepted, while other query errors fail the
operation. This is a read-only, per-operation retirement check, not a new journal
or a lock against older writers. Old journal writers must be quiesced before the
switch; see `pack-operation-migration.md` for the exact conditions and limits.

## Read and save

`GET /api/workflows/:workflowId/pack-version?version=current|draft` reads a Pack
snapshot from `wf/<id>` for `current` or `saved/<id>` for `draft`. A missing
ref is not replaced with the other ref. `canWrite` indicates that the host has
installed a Pack-file save handler, not that a journal or recovery exists.

`POST /api/auto-heal/save` validates an existing workflow's spec and stores the
submitted document in `workflow_specs` in a database transaction. The database
draft is the source of the workflow YAML used at deployment; an existing-workflow
save does not write Git, allocate a release version, or return a Git commit. It
requires edit permission and an existing workflow. `sourceCommit` and
`expectedSavedHead` are unsupported for an existing-workflow save. A request ID
does not make this save idempotent.

For `createOnly: true`, the service checks that the workflow and Pack identity
are unused in its database and Git preflight, initializes `saved/<id>` with the
workflow and manifest, and inserts the workflow and creator permissions in a
database transaction. It accepts `expectedSavedHead: null`, but not a non-null
head or `sourceCommit`. Success returns `branch` and `commit` as well as the
workflow and Pack IDs; it does not publish `wf/<id>` or a deploy tag. Configured
Git settings are required. The route resolves them before calling the service;
missing remote configuration is a 503, while the service itself returns
`CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED` when its Git configuration
or author is unavailable. Existing identities are not upserted. The Git push
occurs during the database transaction, not as a distributed transaction: a lost push response or later database failure can leave a partial
or unknown result (`CREATE_OUTCOME_UNKNOWN`). Only the saved ref is leased; an
independent writer can race the separate `wf/<id>` absence check. Do not assume
an error means neither Git nor the database changed, or retry an uncertain create.

`PUT /api/workflows/:workflowId/pack-version` saves other Pack-file changes to
`saved/<id>`, not the database or published `wf` branch. It takes the opened
`sourceVersion` and exact `sourceCommit`, the `expectedSavedHead` (or null),
and explicit create/replace/delete changes. File contents are canonical Base64
with regular Git modes; path, size, Pack layout and identity validation apply.
The selected workflow YAML content cannot be changed through this endpoint: such
a change is rejected with `WORKFLOW_YAML_REQUIRES_DB_SAVE`. The writer permits
byte-identical structural moves subject to its final manifest and identity
validation. A Pack ID change in a nested layout moves the selected Pack tree to
the new Pack root; an occupied or overlapping destination is rejected. Untouched
files and modes are preserved. Immediately before the push, the service also
checks that the current workflow row's Pack ID (or workflow ID when Pack ID is
null) matches the candidate Pack ID; otherwise it returns `PACK_ID_MISMATCH`.
Source/head checks and the saved-ref lease detect some conflicts, but they do not
make a request durable or coordinate it with the database YAML save.

These are separate writes, not a single atomic canvas-and-files save. A failure
or interruption between them can leave either side updated without the other.
A returned Pack-file `version` is informational, not a reserved deployment
version.

## Deployment

`POST /api/auto-heal/deploy` requires execute or edit permission and an existing
workflow row. It reads the database draft and selects the current `saved/<id>`
head as the source for the Pack; if no saved ref exists, it falls back only to
that workflow's existing `wf/<id>` ref. If neither exists it returns
`SAVE_REQUIRED`. A supplied `candidateCommit` must equal the selected source at
that check, or deployment returns `CANDIDATE_NOT_SAVED`; an older completed save
is not accepted merely because it once existed.

The service overlays the database-derived workflow YAML onto the selected Pack
tree, prepares a release commit, then attempts one atomic Git push updating
`wf/<id>` with an expected-head lease and creating a new
`deploy/<id>/#N` tag. Thus the published tree is **not necessarily identical**
to the saved commit's tree. The saved ref is not changed or leased by that push;
a later saved change does not change the source already selected. Git server
support for atomic push is required for the combined ref update.

After Git confirms the push, a separate database transaction checks its observed
workflow and active-history baselines, inserts a normal deploy-history record,
activates it, updates the workflow version and updates facade bindings when
applicable. A stale baseline or database failure can occur **after** Git has
changed. An optional run is attempted only after deployment completion; failure
to trigger it is not a rollback of the release.

## Unknown outcomes and retry

`requestId`, when supplied, is a correlation ID, not a durable idempotency key;
reusing it does not retrieve a receipt or prevent a second push, deployment
number, tag, or run. A create can return `CREATE_OUTCOME_UNKNOWN`, and a
Pack-file save can return `SAVE_OUTCOME_UNKNOWN` after a push is attempted.
Deployment can return `DEPLOY_OUTCOME_UNKNOWN`,
`DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN`, or `DEPLOY_COMPLETION_UNKNOWN`
depending on what the service observed. A lost client response can also leave
the outcome unknown even if the server completed. Any returned branch, commit,
tag or request ID is evidence for investigation, not a recovery receipt.

Do not blindly retry an uncertain create, save, or deployment, including with
the same request ID. The current service's Pack-operation status and reconcile
handlers return 503
`OPERATION_RECOVERY_UNAVAILABLE`; no journal-backed status, automatic replay or
reconciliation is available. Responsible operators must assess the actual Git
and database state before deciding what to do. This document does not prescribe
a repair runbook.

## Editor behavior and limits

The editor treats the database document and Git files as separate baselines. For
an existing workflow it verifies the database draft before and after saving the
workflow document, then saves any remaining Pack-file changes separately. If the
second write fails, the database may already have changed; remaining local file
candidates are held in the page session, not durably across a refresh or close.
The editor rejects divergent simultaneous canvas and workflow-file edits, and asks for
confirmation before replacing a different database draft with a Git-based YAML
candidate; it does not automatically merge them. Git file writes require the
snapshot's `canWrite` capability.

The new-workflow flow sends create once and checks the returned saved snapshot
and database document. An unconfirmed create is not automatically retried; if a
success response was received but subsequent verification fails, the page can
re-read for verification without creating again. This acknowledgement is held
in page memory, not a durable receipt. The editor similarly blocks automatic retry
of an uncertain Pack-file save or deployment and asks for manual investigation.
Its deploy confirmation identifies the selected Git file baseline separately
from the database workflow draft; unsaved editor changes are not deployed.

### Browser-local deployment marker

The Editor and AutoHeal panel share a workflow-scoped, non-sensitive marker in
browser `localStorage`. They check it and write/read it back before sending a
deploy; the AutoHeal panel also checks it before saving a repair. The marker
contains a flag, not a deployment payload or credential. If storage cannot be
read or the marker cannot be persisted and verified, deployment is not sent.

After a validated successful response for the intended workflow and Pack, the
caller removes the marker and verifies removal. An error, lost or invalid
response leaves the outcome uncertain and does not trigger automatic retry; a
marker may also remain if the page closes after setting it but before sending.
If marker removal fails, the UI reports the failure and may keep later deployment
blocked even though the server reported success. Inspect Git and the database
before deciding how to proceed; a marker is not proof that a deployment occurred
or failed.

The marker can survive a page reload in the same browser storage, unlike the
page-session rename guard below, but storage may be unavailable, cleared, or
evicted. It is scoped to the browser origin/profile, not the server. Its
check-and-set is not atomic: another tab, browser, profile, or direct API client
can race or bypass it. It is not a distributed lock, receipt, or recovery
mechanism. The AutoHeal panel also attempts to remove obsolete persisted
operation records and leave review markers without rendering their contents;
storage failures block that panel rather than proving those records were removed.

### Pack ID changes and workflow path moves

For an existing workflow, the Editor can submit a changed Pack ID in the manifest,
a changed selected workflow path, or both. The workflow ID remains unchanged and
both the manifest and the selected workflow must pass identity and layout
validation. If a moved workflow's old path remains in the candidate, its YAML
bytes must be unchanged; otherwise the Editor rejects the save. The Git writer
also validates the complete resulting Pack, including its other workflow
references, and rejects invalid or occupied destinations.

A Pack ID change is a **two-phase, non-atomic** save:

1. The Editor posts the new `packId` and workflow document to the DB save route,
   checks the returned Pack ID and re-reads the stored document.
2. Only then does it submit the manifest and other file changes to Git's
   `saved/<workflowId>` ref. For a nested Pack, the Git writer moves the Pack
   tree to its new root. A successful file save updates the Editor's draft
   snapshot; it is not a deployment.

A path move with unchanged workflow content and no other DB edit can be a
Git-only save. When the workflow YAML or canvas document also changes, the Editor
saves that content to the DB first and sends the original selected YAML bytes at
the candidate path to Git, along with the structural and other file changes.
The Git endpoint does not accept changed selected-workflow YAML bytes. Deployment
later overlays the DB document on the selected saved tree. It checks that the DB
Pack ID and any requested Pack ID match the selected Git Pack; a mismatch returns
`PACK_ID_MISMATCH` before publication.

If a Pack ID save fails after the DB request was sent, the Editor reports what it
observed: the DB response may be unknown, acknowledged but not verified, or
verified, and the Git save may be unstarted or have an unknown result. This
includes cancellation or failure before the Git request after a confirmed DB
write. It blocks the two local version states and guards subsequent Editor saves
in that page session, asking for manual inspection of DB and Git instead of an
automatic retry. A preflight failure before the DB request does not take this
rename guard. A path-only move does not use this specific rename guard; an
uncertain Git save follows the normal Pack-file save handling.

The rename guard and local drafts are **session-local**: refreshing or closing
the page loses them. It is not a server lock, durable receipt, rollback, or
automated recovery; another client or direct API caller is not fenced by it.
Inspect the actual database and Git state before deciding what to do, and do not
assume a refresh resolves a partial rename. Local Editor tests use mocked
transports; Git structural-move tests exercise local repositories. Neither proves
live deployment or full migration of other workflows sharing a Pack.

## Migration and validation boundaries

Migration 149 is retired and not mounted by the normal migration runner. This
behavior neither requires nor drops the old table; see
`pack-operation-migration.md`. Residual journal-related source or older tests do
not demonstrate active recovery behavior.

The atomic Git ref update and the database transaction are independent: neither
can atomically commit or roll back the other, and concurrent or external writers
can produce conflicts or partial outcomes. Local tests or a source overlay are
not evidence of a deployed image, managed database behavior, authentication, or
live Git integration. Verify those separately for the actual release rather than
assuming journal recovery is available.
