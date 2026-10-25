# Space knowledge orchestration v1

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
- 2026-10-25: only the independent `ac_space_knowledge_sync` table is required before deploying membership hooks (even with delivery disabled). No schema/model/index changes to `ac_space`, its member table, or `ac_task_queue`. Public defaults remain disabled, not simulated success.


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
