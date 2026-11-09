# Historical dual-branch workflow and Pack writes

> **Superseded on 2026-11-07:** the current implementation follows
> [wf save / tag deployment](workflow-wf-save-tag-deploy.md). The text below
> records the former dual-branch contract; its saved-write, wf-production and
> DB-only AutoHeal behavior must not be used to implement new clients.

## Historical scope

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
ref is not replaced with the other ref. New browser editing sessions default to
`draft`; a missing or unreadable draft remains an error until the user explicitly
selects another version or retries. `canWrite` indicates that the host has
installed a Pack-file save handler, not that a journal or recovery exists.
The explicit version reader fetches only this workflow's two branch tips, without
checking out the default branch or fetching all repository history. It verifies
that the advertised refs still match during the read and fails closed if they
change. It does not affect the separate Git paths used for save or deployment.
A duplicate matching manifest or overlapping Pack layout is rejected as 409
`SNAPSHOT_CONFLICT` instead of selecting a file tree implicitly. The service logs
only a fixed failure phase and error category, never Git credentials, raw errors,
or file contents. Other errors may still return 503 `SERVICE_UNAVAILABLE`.

`POST /api/auto-heal/save` validates and transactionally stores an existing
workflow draft, then writes its full Pack to `wf/<workflowId>`. It requires
edit permission and an existing workflow; a successful response includes the
Git branch and commit. A first draft restored through this ordinary endpoint
also writes `wf`. Database and Git are not an atomic transaction: a failure
after the DB commit can require reconciliation.

The Editor uses `POST /api/auto-heal/editor-save-first-draft` as a **DB-only
intermediate step** when an active deployment has no draft row. It supplies
`expectedDraftAbsent: true`, the active `packId`, `expectedActiveDeployNumber`,
and `expectedActiveToken` captured from the active database release. The host
must authenticate the actor and explicitly call `saveEditorFirstDraft`; request
fields cannot select this behavior for ordinary `/save`. This endpoint requires
those preconditions, does not resolve or write Git, and does not fall back to
legacy `/save`. Its success confirms only the DB draft, not a complete Pack save.
The Editor must subsequently invoke unified save using the Git source commit and
`wf` head observed before the first-draft step and the new draft token; only a
confirmed unified save writes the complete Pack to `wf`. Concurrent Git changes
must cause a conflict rather than replacing the observed baseline. The active
token binds the stored spec text and publication identity, and is checked again
under the active-row transaction lock. Older AutoHeal clients may omit the optional token
and retain their previous behavior. If a draft already exists or another insert wins, the service
returns 409 `DRAFT_ALREADY_EXISTS` without overwriting it; if the re-locked
active release has a different deploy number, it returns 409 `BASELINE_STALE`
without inserting. The first-draft preconditions accept only `true` and a
positive integer, cannot be combined with `createOnly`, and are omitted by
ordinary existing-draft saves.
See [the editor draft boundary](workflow-editor-draft-boundary.md) for draft-first
reading and the separate Apply/save actions. `sourceCommit` and
`expectedSavedHead` are unsupported for an existing-workflow save. For an existing row, AutoHeal also rechecks its initially observed database
row under a transaction lock before updating; a changed row produces
`DRAFT_CONFLICT` rather than overwriting that intervening change. This does not
detect a diagnosis that was already stale before the request or an ABA change.
A request ID does not make this save idempotent.

For an existing database draft, the editor uses the separate
`GET /api/workflows/:workflowId/editor-draft` and `PUT` to the same path. GET
returns the raw `spec_json` and an opaque current-value token from one row;
PUT requires that token and compares it under a database row lock before writing.
A stale token returns `DRAFT_CONFLICT` without a write. An unresolved legacy
Pack operation also blocks the CAS write; that check is a retirement preflight,
not a fence against legacy writers that begin afterward. The public host mounts
these routes behind its trusted-principal, origin and CSRF boundary; other hosts
must explicitly provide an equivalent boundary and route before opting in. The
editor does not fall back to an unguarded write when this endpoint is missing.
Before dispatching an Editor database write, the UI establishes a workflow-scoped
pending marker shared by QueryClients in the same tab and verifies its
sessionStorage copy. Pending or uncertain writes prevent fresh editor reads and
transition commits; old reads cannot become a newly confirmed baseline. A
verified success or a recognized pre-write CAS conflict clears the marker.
Lost or unverifiable responses remain blocked for manual reconciliation; even a
successful GET cannot prove a late server write has terminated. The marker can
survive a same-tab reload, but does not coordinate other tabs or API clients and
can be lost when the tab is closed or its storage is cleared. It is not a
server-side fence or recovery receipt. AutoHeal saves in this browser tab also
use the same marker; an unavailable marker store therefore blocks those saves
before network dispatch, and an unverifiable AutoHeal response requires manual
review. A verified non-create-only `DRAFT_CONFLICT` response clears only that
attempt's marker because the database update was rejected. Other errors remain
uncertain. AutoHeal save/deploy capture the workflow write epoch at submission
and recheck it at dispatch, so a queued request is blocked if another same-tab
write completed in between. AutoHealPanel also binds its deployment to the
same-tab epoch from its validated save receipt; a later same-tab write requires
renewed inspection and diagnosis instead of deploying an implicitly newer draft.
Neither epoch check observes other tabs or clients. These guards intentionally
change failure handling; they do not change the normal AutoHeal save/deploy
endpoints or their request bodies.
Editor deployment also binds the confirmed write epoch to the actual transport,
so a deferred mutation cannot publish after a same-tab write starts or finishes.
A separate local internal-host checkout inspected during development has not
mounted the editor-draft or editor-deploy routes, and its legacy AutoHeal save
route drops the first-draft precondition fields. This is evidence about that
checkout only, not a claim about a deployed host. Updating the frontend or the
shared workflow package alone is therefore insufficient: the target host must
mount the editor-specific routes behind its own trusted identity, origin/CSRF,
request-size and sensitive logging boundaries, and ensure first-draft conditions
are enforced end to end. Existing legacy AutoHeal requests retain their contract.
The host must verify its actual resolved workflow package version and Pack-wide
authorization policy; capability declarations alone do not upgrade old code.
This is a database-only check, not the complete Git/DB snapshot protocol. It
does not detect an ABA change back to the same token inputs or prevent an older
unconditional writer from overwriting a later successful save. SQLite locking is
tested locally; MySQL/ZDAS behavior needs validation in the target environment.
The endpoint does not allow changing Pack identity, and the editor currently
rejects such changes before sending the request. This restriction is not global:
legacy AutoHeal and other existing writers have their own policies. Authorization
is checked during the transaction, but permission changes are not serialized
with the workflow row lock; a stricter revocation guarantee requires a separate
shared-lock policy. Existing editor sessions also retain the original raw
`pack_id`, distinguishing NULL from an explicit workflow ID, and compare it
before database and file saves. A further database recheck after confirmed Git
save detects some concurrent changes but is not a cross-store lock; its
manual-review state is local to the page session and is lost on refresh.
Complete historical documents retain every raw field, including
`content`, which may be user-authored. Only a content-only legacy envelope is
unwrapped; its version value and type are retained rather than normalized. Legacy
envelopes with unsafe normalization changes to other fields are rejected. If the draft is absent, a complete active history record with a string
`content` is rejected as ambiguous: legacy history projections reused that name
for YAML, so it cannot safely be distinguished from user-authored content. This
conservative restriction requires explicit reconciliation before editing.
When an active history value cannot be safely bound (including an actual U+FFFD
character), history remains readable but the token is omitted and first-draft
saving fails closed. A secure browser context with Web Crypto is also required
to verify the active snapshot and token; older servers without these fields
cannot establish this first-draft baseline.

Editor-side YAML input and raw DB receipts reject numbers that cannot be safely
round-tripped through JavaScript and reject non-string mapping keys. This safety
check also rejects YAML alias keys even when the alias resolves to a string, and
YAML 1.1 numeric forms such as sexagesimal floats that the checker cannot prove
lossless; such inputs require explicit conversion. YAML validation remains
synchronous: although it now reuses one parsed AST, very large mappings can still
block the UI; there is no Worker or input size limit in this change.

For `createOnly: true`, the service checks that the workflow and Pack identity
are unused in its database and Git preflight, prepares a workflow and manifest,
and commits the workflow and creator permissions in a database transaction
before attempting to initialize `saved/<id>`. It accepts `expectedSavedHead: null`, but not a non-null
head or `sourceCommit`. Success returns `branch` and `commit` as well as the
workflow and Pack IDs; it does not publish `wf/<id>` or a deploy tag. Configured
Git settings are required. The route resolves them before calling the service;
missing remote configuration is a 503, while the service itself returns
`CREATE_ONLY_UNAVAILABLE_GIT_OWNERSHIP_UNVERIFIED` when its Git configuration
or author is unavailable. Existing identities are not upserted. After DB commit the service rechecks the
created row and edit permission before pushing; this is not a lock, so another
writer can still change either after the check. There is no cross-storage atomic
commit. `CREATE_DB_OUTCOME_UNKNOWN` means the DB commit result is unknown and
Git push did not start; `CREATE_DB_CONFIRMED_GIT_NOT_STARTED` means the DB
commit was confirmed but the recheck did not pass or could not complete;
`CREATE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN` means the DB commit was confirmed and
the Git push result is unknown. Later changes may already have modified or
deleted the committed row. Only the saved ref is leased; an independent writer
can race the separate `wf/<id>` absence check. Do not assume an error means
neither Git nor the database changed, or retry an uncertain create.

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
A request based on `current` is rejected when an observed `saved` head exists
and differs from the source commit; confirming the old head does not authorize
replacing that draft. A first save may still initialize `saved` from `current`
when it does not exist. The saved-ref lease also rejects concurrent changes to
that ref. Neither check locks an independently writable `wf` ref, makes the
request durable, nor coordinates it with the database YAML save.

These are separate writes, not a single atomic canvas-and-files save. A failure
or interruption between them can leave either side updated without the other.
A returned Pack-file `version` is informational, not a reserved deployment
version.

## Snapshot foundations (not active)

The worktree contains a trusted Git candidate builder, a versioned lossless
workflow-document projection, and a V2 save-request fingerprint helper. There
is deliberately **no `workflow_snapshot_operations` table** and no new
operation-journal migration. These helpers are not a cross-storage transaction:
they validate and freeze inputs, compare existing DB/Git baselines, and fail
closed on ambiguity, but they cannot roll back a Git ref after a database write
fails. No production endpoint currently submits a unified full-snapshot save.
The retired v149 journal remains retired. Existing AutoHeal and legacy writers
are not silently moved to a new table or hidden protocol.

## Deployment

`POST /api/auto-heal/deploy` requires execute or edit permission and an existing
workflow row. It reads the database draft and selects the current `saved/<id>`
head as the source for the Pack; if no saved ref exists, it falls back only to
that workflow's existing `wf/<id>` ref. If neither exists it returns
`SAVE_REQUIRED`. A supplied `candidateCommit` must equal the selected source at
that check, or deployment returns `CANDIDATE_NOT_SAVED`; an older completed save
is not accepted merely because it once existed.

Legacy `/auto-heal/deploy` and `/auto-heal/editor-deploy` overlay the database
workflow YAML on the chosen saved or wf file baseline. The legacy AutoHeal path
retains its compatibility behavior; its selected tree may remove existing paths.
The old Editor endpoint additionally checks the draft token and rejects some tree
losses, but its output tree need not equal the saved tree.

The current Editor uses the separate `/auto-heal/editor-deploy/snapshot-v2`
protocol. Its request explicitly binds `saved-tree-v2`, a saved candidate commit,
the observed wf commit, draft token and a confirmation that the file differences
were reviewed. Editor shows changes between saved and wf, including file bytes and
modes; unsupported or unreviewable content blocks confirmation. The service
checks the selected saved ref, the draft token, YAML/DB semantic equivalence and
the expected wf commit. It preserves existing deployment guards and, for nested
Packs, rejects changes outside the visible Pack. The release commit tree is
exactly the selected saved tree. For a first deployment, an authorized state
read must explicitly prove the wf ref and active release are absent; the UI shows
the saved files as additions, and the push uses a ref-must-not-exist lease. A
failed or missing read is not proof of absence. Protected deletions and mode
changes can still be rejected. The trusted host must supply independent exact-
candidate deployment authorization; absent policy denies snapshot-v2.

Both variants push `wf/<id>` with an expected-head lease and create the
`deploy/<id>/#N` tag in one atomic Git push; atomic Git push support is required.
The saved ref is not leased by that push. A change after the final saved check
can leave the explicitly confirmed older candidate deployed; it cannot silently
substitute a newer candidate. These are Git checks, not a DB/Git atomic commit.

After Git confirms the push, a separate database transaction checks its observed
workflow and active-history baselines, inserts a normal deploy-history record,
activates it, updates the workflow version and updates facade bindings when
applicable. A stale baseline or database failure can occur **after** Git has
changed. An optional run is attempted only after deployment completion; failure
to trigger it is not a rollback of the release.

## Unknown outcomes and retry

`requestId`, when supplied, is a correlation ID, not a durable idempotency key;
reusing it does not retrieve a receipt or prevent a second push, deployment
number, tag, or run. Create reports its observed DB and Git phases using the
codes above; older servers may return `CREATE_OUTCOME_UNKNOWN`. A Pack-file save
can return `SAVE_OUTCOME_UNKNOWN` after a push is attempted.
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

The browser-local deployment marker is only a warning, not an inter-tab or
server lock. A known in-flight Editor or AutoHeal request blocks manual marker removal in
the same page runtime; result and reconciliation paths compare their attempt
token before clearing. `localStorage` does not provide atomic compare-and-swap
across tabs. If marker removal may have succeeded but verification fails, an
in-memory quarantine blocks this page until reconciliation; it does not
survive a refresh or page close.

## Editor behavior and limits

The editor has one shared top-level Save and Deploy control regardless of the
selected tab; the embedded files tab does not add another Save button. Both tabs
enter the same editor save path and retain all session edits across tab switches.
This is an interface convergence, **not** the complete snapshot contract: a
canvas-only save still updates the database without updating saved Git YAML. The
status message identifies these separate effects and does not claim an atomic
or fully unified snapshot.

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

### Pack-file origin rejection

For the current Pack-file `PUT`, an HTTP **403** with a parsed JSON object whose
`code` is exactly **`ORIGIN_NOT_ALLOWED`** is a known pre-write rejection by the
host's origin guard, not an unknown save outcome. If the body includes a
`requestId`, it must match this attempt; a mismatched or invalid ID remains
unverified. The existing bounded error-body parser still applies.

The editor ends only that attempt's pending-save state, does not create a
reconciliation or block the candidate, and reports:
“版本文件保存请求被来源校验拒绝，本地修改保留。请检查访问入口或代理配置后，再主动保存。”
Candidate file bytes, modes, additions/deletions, selected source version,
`sourceCommit`, and `expectedSavedHead` are retained. There is no automatic
resubmission, status lookup, or reconciliation; another explicit user save
allocates a new `requestId` and retains the same source/CAS baseline.

This exception never clears an existing unknown outcome or another version's
block. It does not apply to a status/reconcile/head lookup, nor to a late response
after the original PUT timed out. Generic 403s, other status/code combinations,
invalid JSON, network errors, timeouts, unknown 5xx responses, and
`SAVE_OUTCOME_UNKNOWN` retain their existing protection. It is not a general
4xx exception and does not relax the server's security guard.

The rejection concerns only the Pack-file write. The separate workflow-document
DB save may already have succeeded; the message does not imply that the entire
Editor save performed no database writes. Other guards, including the partial
Pack-ID rename guard described below, remain in force. Local candidates are still
page-session state, not durable recovery data.

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

The rename guard has one source of truth: `Session.state.uncertainRename`.
`null` means no unfinished identity write is known in that page session; a
non-null message records a rename that requires manual DB/Git inspection. It is
independent of per-version `blocked`, dirty files, and Git-save reconciliation.
Both edit risk and overall Pack risk include it, so a successful `current` or
`draft` reload may replace files and clear that version's transient flags but
cannot settle the rename. Switching versions, a clean canvas, and component
unmount/remount do not clear it or allow `releaseCleanSession` to discard the
session. The combined-save guard and its message survive those actions; the
Editor continues to reject deployment before confirmation or transport.

This includes a confirmed DB rename followed by exact 403
`ORIGIN_NOT_ALLOWED`: the Pack PUT remains a known rejection with no unknown Git
outcome, but the independently unfinished DB/Git rename still carries risk.
An ordinary non-rename origin rejection and a fully confirmed DB/Git rename do
not acquire this guard. Reloading snapshots is not an acknowledgement or a
repair mechanism, and no new guard-clearing or automatic recovery action is
introduced.

The rename guard and local drafts are **session-local**: refreshing or closing
the browser page loses them (unmounting a component does not). It is not a server lock, durable receipt, rollback, or
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

Git and database are intentionally treated as separate durable systems. A
successful Git write is not rolled back by a failed database write, and a
successful database write is not rolled back by a failed Git write. The protocol
therefore records and exposes the two outcomes separately, verifies exact
commit/tree/DB hashes, blocks unsafe deployment, and requires manual
reconciliation for an unknown result. It does not claim distributed atomicity.
Local tests or a source overlay are not evidence of a deployed image, managed
database behavior, authentication, or live Git integration. Verify those
separately for the actual release.

## Additional safety foundations in this worktree

The candidate builder now snapshots all caller-controlled save parameters before
its first asynchronous Git operation. Later mutations of workflow identity,
source refs, file changes, Git configuration, or the trusted workflow YAML do
not change the candidate that is validated or pushed. This is a TOCTOU guard,
not authorization or publication provenance.

The worktree also contains an unconnected V2 save-request fingerprint helper.
It binds a trusted host repository identity, actor, Pack/workflow identity,
source refs, `expectedDraftToken`, the raw lossless database JSON, and every
ordered Pack-relative file change. It rejects impossible parent/child file
operations and definite lower-bound violations of the candidate Pack limits.
It is not a route, ACL, operation journal, database fence, or recovery protocol;
a future service must use its frozen request snapshot for candidate construction
and compare `expectedDraftToken` against the locked database row.

Deployment-history recovery requires special care: an inline
`__clawweb_snapshot_v1` field is ordinary user-controlled history content, not
proof of origin. Its shape or a hash of the history row cannot establish that
`content` was generated by the server. Automatically stripping the field or
replacing the outer document with `content` can lose legitimate user data.
Ambiguous historical records must not be silently converted into an editable
and savable draft; their complete raw data must remain available for manual
inspection. A verified provenance mechanism or a different unambiguous storage
contract is needed for automatic lossless recovery of all historical formats.
Existing records are not repaired merely by changing the reader. The history
projection retains its legacy contract: top-level `content` archives the exact
deployed workflow YAML, including any authored `content` field within that
YAML. It is an archive, not the ordinary editable draft or deployment input.
When the database draft is confirmed absent, the Editor should recover from the
active deployment history, subject to safe identification and validation of the
archived document. Existing malformed or ambiguous historical records still
require a safe fallback; the fail-closed reader currently blocks some normal
legacy snapshots containing string `content`. Existing snapshot consumers also
read the entire history JSON as workflowSpec; because its top-level `content`
is the archived YAML rather than any user-authored field of the same name, that
consumer contract needs explicit compatibility work. A full restore path and
legacy consumer compatibility have not yet been verified.

AutoHeal and Editor must use the same save and deployment core; caller-specific
access checks, confirmation UI, diagnosis context and optional AutoHeal run may
remain separate. Their present deployment wrappers share a service but their
save paths still differ. Both must be integrated with the complete candidate
protocol before claiming the unified contract is implemented. Legacy AutoHeal
validation is under compatibility review: do not claim arbitrary old YAML and
numeric forms remain deployable until the regression tests and target-host
checks have passed. In particular, tagged scalars, non-string mapping keys,
large exactly representable numbers, and large escaped documents require tests.

### Unrouted unified-save prototype: review findings

`workflow-unified-save-service.ts` is an experimental application service, not
an enabled production save path. Its two acknowledgements are historical results
from one invocation, not proof of current cross-store consistency. Independent
review found that workflow edit permission alone cannot authorize changes to a
shared Pack; the trusted host must enforce explicit Pack write authorization and
prevent unauthorized changes to other workflow documents. Also, the current
`wf` source may advance after candidate preparation, and another database writer
may win after DB CAS but before or after the Git push. A later read or a browser
lock does not fence either race. Until every writer and the source-ref condition
are addressed, do not wire this prototype into production or report a complete
snapshot as current merely because both stages acknowledged. As a conservative
interim boundary the prototype requires a trusted Pack write policy to approve
the exact prepared candidate and frozen file operations, and rejects changes to
the manifest and declared workflow paths. A configured policy must also protect
DB-backed workflow files not declared in the manifest and govern shared files;
it defaults to deny. No production policy or route is connected here. The
source-ref and other-writer races remain unresolved.

The worktree also has a shared-save HTTP adapter mounted default-deny in the public host and a read-only
active-history recovery helper. The HTTP adapter requires a host to supply
verified identity, CSRF enforcement and an authorized service binding; its
result is not a crash-recovery receipt. The recovery helper requires an
independently trusted host reader that actually verifies and binds the release
and active row; mocked reader output or an active-row hash is not provenance.
The public route is default-deny unless the host supplies explicit verified
identity, repository binding and Pack authorization. The separate AutoHeal
adapter is selected by the UI only when a host advertises its capability.
`GET /api/auto-heal/capabilities` returns `autoHealUnifiedSave: "v1"` when the
host supplies that adapter, or `false` when it does not. The client also
accepts the earlier explicit `null` disabled value; request failures and other
values remain errors rather than silently enabling a compatibility fallback.
The recovery helper is not connected. In particular,
ordinary archived `content` histories remain blocked by the current Editor
fallback until a trustworthy recovery path is integrated.

There is no `workflow_snapshot_operations` table, durable operation journal,
cross-store atomic transaction, automatic crash recovery, or safe blind retry.
After an unknown response or process failure, preserve available evidence and
require authorized manual reconciliation; a single ref read cannot prove an
in-flight push has ended. The source-ref and shared-writer coordination mechanism
and operational procedure remain open work under this constraint.

## Remaining implementation tasks under the no-new-table model

1. **Unified save application service**: combine the current editor DB CAS
   receipt, complete Pack candidate and `saved/<workflowId>` CAS result under
   one request-level state machine in memory/request scope, without claiming
   cross-storage atomicity. Return `dbConfirmed`, `gitConfirmed`, `candidate`
   and `reconciliationRequired` separately.
2. **Existing-writer coordination**: update or explicitly disable every writer
   that can change the same workflow draft, Pack files, `saved`, `wf` or deploy
   history. A legacy preflight alone is not a fence.
3. **Exact deployment**: deploy the confirmed candidate tree, write `wf`/tag and
   `workflow_deploy_history` as separate verified steps, compare commit/tree/DB
   hashes, and block automatic continuation after partial success.
4. **Manual reconciliation**: provide an authorized read-only reconciliation
   path or operational runbook based on request hash, DB draft token, Git refs,
   commit/tree and history rows. It must not infer success from a timeout or a
   single GET and must not blindly replay a write.
5. **Host integration**: mount the editor-specific routes in the real internal
   host with trusted identity, CSRF/origin, body-size and sensitive-log
   protections; verify the actual resolved workflow package at build time.
6. **Validation**: run multi-process and MySQL/ZDAS tests, plus real Git remote
   failure injection. SQLite and bare-Git tests alone do not prove production
   behavior.

The former `workflow_snapshot_operations` proposal is intentionally removed.
The existing legacy pack-operation code is not silently deleted or migrated by
this change; if that old table is also to be retired, it requires a separate
inventory of deployed rows and writers.


### Current integration boundary (2026-11-06)

The public host mounts the shared unified-save route before its JSON parser and
only enables writes when a trusted host explicitly supplies verified principal,
repository binding and exact-candidate Pack authorization. Its default remains
deny. Editor uses the unified protocol after read-only capability discovery; when
the host cannot provide it, the user may explicitly choose DB-only compatibility,
which does not save Git files. AutoHeal has a separate adapter to the same core
and selects it only where a trusted host explicitly provides its resolver; legacy
AutoHeal save and deployment remain available with a compatibility warning.
The corporate host has a separate composition root and has not yet been wired or
validated in this worktree. Neither prototype nor these adapter tests establish
production availability.

Legacy database `pack_id = NULL` means the workflow's own Pack. The unified
service preserves that raw NULL and rejects a different candidate identity.
A `partial` result that claims a confirmed database write must contain the
matching request hash, candidate commit and draft token; otherwise the adapter
returns `unknown` and requires manual review. These checks do not create a
durable receipt or guarantee current DB/Git consistency.

For deployment, matching a current `saved` commit and draft token is not itself
a record that a previous save completed. Snapshot-v2 uses a new user confirmation
for the exact candidate and captured wf baseline, and never calls the legacy
AutoHeal deployment protocol. A response after an uncertain write still requires
manual inspection rather than automatic replay.
