# Space knowledge orchestration v1

> **2026-10-26 isolation amendment (local implementation; delivery pending):**
> Never-enabled deployments keep legacy Space/member/approval/Skill Space
> creation working without `ac_space_knowledge_sync`, with auto-create disabled.
> Capture is explicitly configured, default false, independently of Sink delivery.
> **2026-10-27 availability amendment:** Retained history does not block startup
> or ordinary writes with capture explicitly disabled; producers still require
> capture. This amendment
> supersedes earlier global DDL prerequisites below; see [spec.md](spec.md),
> [plan.md](plan.md), [tasks.md](tasks.md) and the capture deployment README.

## Summary
Authenticated Space members create/query canonical knowledge assets using a configured provider. No local knowledge asset/content/ACL mirror; immutable membership delivery snapshots are retained until acknowledged. Ordinary members can create; provider makes the actual authenticated creator Owner. No Bot installation, grant or enablement side effects.

## Service and Plugin APIs
Service API: `SpaceKnowledgeServiceProtocol.create/get/list_knowledge_bases`.
Plugin APIs: `SpaceKnowledgeSync.ensure_synced/get_context`, `SpaceMembershipSink.is_enabled/apply_snapshot` and `SpaceKnowledgeProvider.create/get/list_knowledge_bases`.
All return typed values or safe `SpaceKnowledgeError` codes. Provider owns durable environment+Space+request_id idempotency, immutable-content conflict, authorization and deletion. Automatic initialization recovery is out of scope.
Sync must verify that authoritative current Space membership is acknowledged in the same environment, with continuous role/removal delivery. No fixed revision, timestamp or browser claims. First activation persists a complete snapshot in a real transaction before any outbound I/O. Default sync/provider are **unavailable**, not simulators. A deployment must bind a trustworthy producer before enabling real creation.

## HTTP API
- `POST /api/spaces/{space_id}/knowledge-bases` body `{request_id,name,description?}`.
- `GET /api/spaces/{space_id}/knowledge-bases/{kb_id}`.
- `GET /api/spaces/{space_id}/knowledge-bases` returns `{knowledge_bases:[State],next_cursor}` (page_size defaults to 50, maximum 100; cursor is evaluated only within the requested Space) for visible assets, including creating/failed/ready. Treat next_cursor=null as the end; an ACL-filtered page may be short or empty with a forward cursor. Pagination is not a snapshot across requests.
Existing AuthPlugin supplies the canonical current user; query/header/body actor assertions never select the caller. Create rejects unknown fields including actor, tenant and revision. This is not application-only OpenAPI delegation.
Result: `{kb_id,tenant_id,name,description,status,created_by,owner_id}`; 202 means accepted, **not a readiness guarantee** (always inspect status), replay/query/list 200. `status` is creating/ready/failed. Retries preserve the same request_id and immutable content after timeout; never generate a fresh ID. Detail queries use the original kb_id. Initialization uses the provider's existing normal flow; this change does not add background initialization recovery, retry scheduling or budgets. Failures remain observable for separate operational handling; the browser must not trigger a retry operation.
Validation failures use 422. Safe business errors: 400 INVALID_INPUT; 401 IDENTITY_REQUIRED; 403 SPACE_MEMBER_REQUIRED/KB_PERMISSION_DENIED; 404 RESOURCE_NOT_FOUND; 409 SPACE_NOT_SYNCED/SPACE_REVISION_MISMATCH/CREATE_REQUEST_CONFLICT; 410 KB_DELETED; 503 SPACE_SYNC_UNAVAILABLE/DEPENDENCY_UNAVAILABLE.

## Plan and Tasks
- Core reusable membership + ensure-synced + provider orchestration, with repeated member check after sync.
- Public HTTP is a thin authenticated adapter, business work runs off the event loop.
- Composition-root defaults fail closed; deployment adapters inject private clients/config.
- Validate consumer contract, no-provider calls on denied/stale sync, HTTP identity spoofing, default DI, adapter conformance and controlled fixed-combination integration.
- 2026-10-25: the independent `ac_space_knowledge_sync` table is required only for explicitly enabled capture; never-enabled deployments do not require it. No schema/model/index changes to `ac_space`, its member table, or `ac_task_queue`. Public defaults remain disabled, not simulated success.


## Validation Boundary (2026-10-23)
The real SQLite + mounted HTTP lifecycle flow verifies default-unavailable sync and live membership removal without a fake success producer. Its name is `space-knowledge-real-membership-unavailable-sync`. Success/provisioning is covered only in the separately reported internal controlled fixed-combination ASGI suite. Neither suite proves a deployed membership producer or remote provisioning.

Plugin contract DTOs/errors live in `plugin_api/space_knowledge.py`; that module imports neither core models nor HTTP. `ensure_synced(space_id, environment)` resolves authoritative facts internally rather than accepting a caller-provided full snapshot. A small route-registration extraction brings the existing app below its 1000-line cap; its stale oversize exemption is removed, not enlarged.


## Durable membership producer (2026-10-24)
This section supersedes the earlier no-producer validation boundary, not the asset readiness contract.
First create activates delivery and waits for current-revision acknowledgement. Later member changes record
immutable snapshots in the same transaction (ordinary, batch, manual and automatic approvals). No-op roster
updates do not increase the version. Space deletion remains physical; a disabled snapshot and independent
progress survive it. An event for removal cannot be collapsed into a later rejoin snapshot.

`get_context` is read-only: never-used spaces list as empty / detail as not found when the configured
integration is enabled. Disabled integration still reports unavailable. Activated reads use the provider's
persisted ACL; they do not push snapshots. Later changes are eventually consistent and can have unbounded
revocation delay during a provider outage. First-create rechecks source membership after confirmation.

The existing durable queue worker must be enabled. A bounded lifecycle scans the outbox every
30 seconds and dispatches one pending head per space through the existing queue interface.
Scheduling failures are retried from the outbox; terminal queue history may be purged normally.
Unacknowledged outbox history must not be purged. Same-version calls
may repeat after crashes/lease expiry; the sink must confirm exact scope/revision/hash. Existing externally
seeded provider state without a matching local history needs a controlled baseline adoption, not an
invented revision. Direct SQL changes outside the application writers are not captured.

Local producer tests include real TC repositories and the existing queue worker through an in-process
HTTP adapter to provider routes + SQLite. They do not prove SSO/gateway, deployed topology, remote
physical storage, OceanBase concurrency or mirror/gitlink compatibility.

Only `ac_space_knowledge_sync` is introduced. Its primary key is `(env, space_id, revision)`; each
row holds an immutable full `payload` (MySQL MEDIUMTEXT), `snapshot_hash`, source `status`, owning
`app`, and mutable `acknowledged` delivery state. Latest revision and latest acknowledged revision
provide progress without a separate cursor table. No cleanup is implemented; any future retention
must retain pending revisions, the latest row and the last acknowledged row.

Shared queue tasks hold only space/revision pointers. No queue schema, ORM, index, worker or
retention change is permitted; no adopter reads historical queue payloads. Missing or corrupt
outbox snapshots fail closed rather than being replaced with today's roster. The receiver's
2000-member / 1 MiB limits still apply. This supersedes the earlier experimental progress-only
table and fragmented queue-history design; that unpublished local schema needs an explicit
state-preserving conversion before running this implementation.

## Scope reduction (2026-10-24)
ECB automatic initialization compensation was explicitly removed from delivery. The existing single initialization, request idempotency, status projection and legacy ECB internal capabilities remain. This does not remove the separately approved durable TC membership delivery.

## Registered endpoint coverage (2026-10-25)
All three new HTTP routes are registered in the shared endpoint framework with
one successful member case and one denied non-member case each. The framework
owns HTTP invocation on the full application; cases use real space membership,
orchestration and snapshot persistence, substituting only external provider ports.
Success assertions pin authenticated identity (ignoring forged actor query input),
creation acknowledgement, response DTOs and forwarded pagination. Denied cases
assert no provider call. This complements rather than replaces the closed-default
lifecycle and provider contract suites. No coverage-baseline exemption is added.

Full-suite collection also requires unique module names under non-package test
directories. The HTTP and service suites use `test_space_knowledge_router.py` and
`test_space_knowledge_service.py`, avoiding existing `test_router` / `test_service`
modules without changing pytest collection mode or excluding any tests.

## Receiver admission and identity lifecycle repair (2026-10-25)

- Before inserting **any** immutable snapshot, enforce at most 2000 members and
  at most 1 MiB of `json.dumps(body, ensure_ascii=False)` encoded as UTF-8 (the
  receiver adapter's encoding). Storage capacity is not receiver capacity.
  Over-limit activation leaves no outbox row. Over-limit changes to an activated
  Space raise the safe `SPACE_MEMBERSHIP_LIMIT_EXCEEDED` error and roll back the
  entire source transaction: ordinary/bulk membership, approval state, approver
  state and result notifications cannot commit separately from the snapshot.
  Knowledge HTTP maps that error to 409. Existing member/maintenance/approval
  error envelopes remain unchanged; they report failed operations, never success.
  A payload-free warning records environment, Space/revision, member count and
  byte count. Ordinary never-activated Spaces retain their existing membership
  behavior; activation above the receiver capacity is refused.
- This is admission, not truncation or queue compaction. At capacity a rejected
  addition consumes no revision. Removals, a subsequent rejoin and physical
  deletion continue to append complete receivable snapshots in exact order.
  No revision may be skipped, rewritten, merged, or auto-acknowledged. Existing
  oversized/corrupt historical rows, if present from an older candidate, need
  explicit operator reconciliation before rollout; this repair does not silently
  mutate that history or claim to repair an already poisoned queue.
- Server databases retain source row locks. SQLite ignores `FOR UPDATE`, so
  `lock_space` first executes an environment/live-source-scoped no-op UPDATE,
  then reads the source and roster inside the same transaction. Explicitly
  assigning `gmt_modified` to itself suppresses ORM onupdate; the guard changes
  neither identity nor metadata. A zero-row UPDATE still takes the SQLite write
  reservation and the following read rejects a source deleted before the guard.
  Activation, ordinary/bulk member/role changes, rename, physical deletion and
  both manual/AUTO approval entrypoints use this same source guard. This fences
  "no history yet" decisions as well as revision allocation: a mutation cannot
  commit between first-roster capture and first-snapshot persistence without a
  subsequent outbox revision. PK conflict recovery alone is insufficient for
  that invariant and is not used as a substitute. No process lock, schema change,
  global database transaction-mode change, unbounded retry or outbound I/O is
  introduced. Contention honors the configured SQLite busy timeout and propagates
  a database failure with transaction rollback; unrelated write errors are never
  converted to idempotent success. SQLite serializes writers at database scope,
  so this guard can increase write contention; it is limited to these source
  transactions, not installed globally in DatabasePlugin.
- SQLite rowids can be reused after physical deletion. Immediately after a new
  Space INSERT acquires SQLite's cross-connection/process database write lock,
  but before member INSERTs, caller-visible IDs or external side effects, both
  TEAM and PERSONAL writers (including the additive Space Skill repository
  creation seam) reserve an ID above retained outbox Space IDs in
  that environment if SQLite's generated ID is not above them. The generated
  ID is already above all live Space rows. The ordered lookup uses the existing
  `(env,space_id,revision)` primary-key prefix, without reading payload history.
  Source/member writes still commit or roll back together. Other dialects retain
  native ID allocation. Old disabled snapshots remain deliverable independently.

### Compatibility, deployment and rollback

No `ac_space`, `ac_space_member`, outbox or shared-queue schema/model/index is
changed by the repair; the original independent outbox migration is still the
only knowledge DDL prerequisite for capture-enabled deployments, including
before their SQLite Space creation. Never-enabled deployments require no sync DDL. No receiver DTO,
revision/hash encoding, provider key, queue pointer or ACL ownership changes.
The additive safe error and capacity rejection are the intended observable
behavior changes. SQLite-generated IDs are opaque and may now have gaps; clients
must not assume contiguous or reusable IDs. All application writers must run
this repair before resuming SQLite Space creation. Retain at least the latest
row of every retired outbox scope permanently; deleting that identity tombstone
would invalidate the non-reuse guarantee.

No automatic migration of pre-existing reused identities is attempted. An upgrade
must inspect any previously deployed candidate for impossible snapshots, reused
IDs or direct-SQL writes; resolve them deliberately with the source and provider
owners before enabling delivery. Never delete pending disabled history to make a
new Space activate. Rolling back to the old writer reopens the admission/ID-reuse
bugs; pause knowledge-enabled membership/approval and SQLite creation during such
rollback rather than serving them with the unfixed writer. Local file-SQLite
regressions do not prove MySQL/OceanBase concurrency, remote receiver operation,
gateway/SSO, deployed revocation timing or an online migration.


## Internal persistence isolation (2026-10-26)

Space writers consume `SpacePersistenceEffects`, a caller-Session internal
contract (not a public Service/Plugin API). Defaults are no-op capture and
identity protection, retaining neutral source locking. The composition root
strictly validates optional `user_config.space_knowledge_capture.enabled` (bool,
default false; no unknown keys). A shared lifecycle/producer guard performs
schema/configuration checks after bootstrap or before first protected library
use, not in constructors. Retained history in the current environment, **any app**,
does not reject capture=false or ordinary source writes; missing table with
capture=false is valid. Explicit knowledge producers still reject disabled
capture. DB/schema failures propagate, never become an implicit no-op fallback.

Enablement sequence: migrate the original outbox → roll all source writers,
workers and direct library callers to capture=true → verify assembly → allow
first activation. Do not activate during mixed no-op/capture rolling deployment.
Pause delivery by stopping the Sink, not capture. Restart drains retained
remove/rejoin/role/disabled revisions in order. A rollback that must preserve
synchronization consistency should keep capture enabled or pause affected writes. Disabling capture no longer blocks the app,
but does not record source mutations or protect retired SQLite identities; no
revocation-gap reconciliation is added. No source/shared-queue tables or
indexes change; no event bus or second progress table is added.

Pending discovery uses at most 32 indexed Space-head seeks and a continuation
cursor, not GROUP BY over all pending revisions. Dispatch/progress/ack metadata
exclude payload. The worker reads the exact next immutable payload and validates
hash/envelope before Sink invocation. A corrupt-but-present head can be queued,
but delivery/ack fail closed; missing predecessors are not queued. FIFO, wire
encoding, exact hash acknowledgement, queue pointers and public DTOs do not change.
Corporate assembly, remote databases and online rollout need separate validation.
