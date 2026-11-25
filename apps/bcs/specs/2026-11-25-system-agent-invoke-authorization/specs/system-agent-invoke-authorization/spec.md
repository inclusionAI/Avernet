## ADDED Requirements

### Requirement: First-class and stable system invocation kind

BCS SHALL define `GrantKind::SystemInvoke` with exact serde/DB value `system_invoke`, distinct from `permission_profile`, `rules`, `owner`, and `manager`. Existing spellings and meanings MUST remain unchanged. A system edge SHALL authorize only a direct A→B Agent invocation candidate, never reverse, transitive, friendship, management, discovery, group membership/message, or arbitrary tool access.

#### Scenario: Serialization and DB round trip
- **WHEN** a SystemInvoke edge is serialized, persisted and decoded
- **THEN** its kind is exactly `system_invoke`, without a legacy-kind alias or lossy fallback
- **AND** each of the four existing kinds round trips with its original spelling

#### Scenario: Direction and transitivity
- **WHEN** only approved A→B and B→C system edges exist
- **THEN** neither B→A nor A→C is authorized by those edges

### Requirement: Production system eligibility and bounded security domain

Both endpoints SHALL be distinct live system Bots in the same trusted production security domain. Environment, tenant/namespace and Provider bindings MUST be resolved from authenticated context and configured providers, not caller-supplied env/labels/Card metadata. Missing, stale, unavailable or ambiguous eligibility/identity SHALL fail closed. Shared env storage without provable tenant isolation MUST NOT enable this capability.

#### Scenario: Spoofed classification or cross-domain request
- **WHEN** a personal Bot supplies `system=true`, a system-looking name or catalog label, or a target from another security domain
- **THEN** no edge is created and no SystemInvoke decision allows the call

#### Scenario: Non-production and local development
- **WHEN** a normal non-production domain attempts to grant or consume this kind
- **THEN** it is denied
- **AND** isolated tests can use a configured synthetic production domain and fake providers without real production dependencies

### Requirement: Canonical Bot identity and trusted resolution

Persisted `from_id` and `to_id` SHALL be concrete byte-exact `bot_uuid` values. AgentCode/AgentId, Card name/URL and Provider ref MUST NOT replace them or confer proof of identity. Any token/code-only protocol SHALL validate the credential and resolve to exactly one scoped Bot before evaluating authorization. The original trusted originator/parent lineage MUST NOT be replaceable by request-body values.

#### Scenario: Shared agent code
- **WHEN** two Bots share an agent_code and a code-only lookup has no unique trusted binding
- **THEN** authorization fails rather than selecting the first record or sharing an edge across both Bots

#### Scenario: Missing optional AgentCard identity
- **WHEN** a Bot lacks agent_code but its bot_uuid and system eligibility are independently and uniquely verified
- **THEN** that missing optional Card identity alone does not cause rejection or automatic backfill
- **AND** a host authentication chain that actually requires the missing code fails explicitly

### Requirement: Canonical edge shape and coexistence

SystemInvoke SHALL use `grant_ref_id=0`, `rules=NULL`, `originator_policy_type=same_as_from`, NULL policy data and approved/revoked edge status. It SHALL use the existing five-column kind-inclusive unique key, coexist with legacy grant kinds, and never require or seed a default profile. Invalid shape MUST be rejected before use, including corrupt stored shapes.

#### Scenario: Same pair carries several kinds
- **WHEN** a pair contains profile, rules and system edges, with management metadata where valid
- **THEN** each retains its own identity and lifecycle without key collisions or kind substitution

#### Scenario: Invalid system shape
- **WHEN** a write or runtime read encounters nonzero stored ref, inline rules, policy Any, policy data or Human endpoint on a system edge
- **THEN** it fails without silently interpreting the row as valid or broadly allowing it

### Requirement: Explicit runtime candidate set and fallible security reads

`list_active_grants` SHALL add approved SystemInvoke to its existing PermissionProfile/Rules candidate set and exclude Owner/Manager. `is_authorized` SHALL be the same-set existence projection, not a complete operation decision. A separate fallible checked read SHALL propagate query, unknown-kind and row-decoding failures without partial/empty success; new operation decisions MUST use it.

#### Scenario: System-only versus metadata-only pair
- **WHEN** a pair has only one approved SystemInvoke edge
- **THEN** runtime candidate listing contains it and the repo existence projection is true
- **AND** a pair with only owner/manager metadata yields no runtime candidates

#### Scenario: Failed or partially corrupt read
- **WHEN** the security read encounters a database error or one corrupt row among valid rows
- **THEN** the entire checked read fails and no SystemInvoke/public-default success is fabricated

### Requirement: Operation-aware system invocation admission

SystemInvoke use SHALL require authenticated A binding, valid current domain and eligibility generations, live/non-hidden endpoints, direct invocation operation, trusted `originator==A`, current approved edge generation and canonical shape. It MUST NOT authorize arbitrary operations, delegated-originator calls or use fallback default-profile semantics. New admission SHALL use the registered central authorization extension point and transport-neutral application contracts.

#### Scenario: Valid direct call
- **WHEN** authenticated system A directly calls protected system B with trusted originator A and a current approved A→B edge
- **THEN** explicit system invocation admission succeeds without a default profile dependency

#### Scenario: Delegated call cannot launder originator
- **WHEN** B tries to use B→C while the trusted initiating originator is A
- **THEN** the system edge does not authorize the call, even if body fields claim originator B

#### Scenario: Hidden or retired endpoint
- **WHEN** either endpoint is hidden, retired, deleted or no longer a system Bot
- **THEN** the old approved edge does not authorize a new call

### Requirement: Legacy admission and friend semantics remain isolated

Generic admission and legacy AuthzContext building SHALL not treat an unverified SystemInvoke candidate as a default-profile or old-kind permission. `GET /bots/{id}/admission` SHALL retain its old meaning. `has_friend_edge`, friend lists, friend notifications, friend writeback and friend revoke SHALL remain default-profile-only; system requests MUST NOT be processed by friend request endpoints.

#### Scenario: System edge alone is not friendship or generic admission
- **WHEN** only an A→B system edge exists and no independent public/collaboration/profile/rules permission exists
- **THEN** both friend directions/lists remain empty and generic admission does not become allowed
- **AND** the specialized valid direct invocation can still succeed

#### Scenario: Friend cancellation is exact
- **WHEN** a default-profile friend edge is revoked while a system edge exists for the same pair
- **THEN** only the friend edge is revoked and no system approval is cancelled

### Requirement: Runtime grant provenance and revocation generation

A verified system invocation's AuthzGrantRef SHALL use kind `system_invoke`, ref_id equal to edge_id, source `edge_grant`, approval generation in revision and no profile digest. The stored `grant_ref_id=0` MUST NOT be confused with this runtime edge reference. Consumers SHALL support the new kind before receiving it. Every new invocation SHALL revalidate current generation; authorization caches MUST NOT let revoked or stale grants survive a successful revoke acknowledgement.

#### Scenario: Revoke then regrant
- **WHEN** an edge is revoked and separately reapproved
- **THEN** it retains its edge identity but has a newer approval generation, and old contexts cannot admit a new invocation
- **AND** already-admitted work follows its existing completion contract rather than a false promise of forced cancellation

### Requirement: Explicit management permissions

New management APIs SHALL authenticate all actions, reject legacy anonymous/auto-claim shortcuts, and enforce the design's scoped role matrix. Source owner/manager SHALL be able to request and revoke; target owner or explicitly delegated governance service SHALL be able to grant/approve/reject. Target manager status alone MUST NOT imply approval power. Read access SHALL be limited to controlled endpoint scope or explicit governance query scope.

#### Scenario: Source manager requests but cannot approve target
- **WHEN** source manager requests A→B then tries to approve without target-owner or delegated approval authority
- **THEN** the request remains pending and approval fails without creating an edge

#### Scenario: Management role is not invocation identity
- **WHEN** a Human owns or manages B but lacks a separately valid invocation identity/permission
- **THEN** the owner/manager edge is not consumed as a system invocation grant

### Requirement: Dedicated request lifecycle

BCS SHALL add `RequestKind::SystemInvoke` / `system_invoke` with requested_ref_id=0, no requested_rules, pending edge_id=NULL and a separate request lifecycle from Connect. Approval SHALL atomically create/restore only A→B and backfill its edge_id with actor, qualification generations and decision audit. Approval SHALL revalidate the requesting party's authority and both current identities. Rejected/cancelled requests MUST NOT create edges or approve reverse requests.

#### Scenario: Approval versus stale authority
- **WHEN** a pending request is approved after source control, identity or eligibility generations changed
- **THEN** it returns a conflict and requires a new valid request rather than accepting a stale snapshot

#### Scenario: Rejected or cancelled pending request
- **WHEN** an authorized target rejects, or the still-authorized requester/source owner cancels, a pending request
- **THEN** the matching terminal state is stored without any grant creation or reverse-request mutation

### Requirement: Dedicated HTTP resources and errors

Proposed `/api/v1/collaboration/system-invoke` resources SHALL implement the methods, DTOs, permission checks and error classes in design D6. Any exposed OpenAPI mirror SHALL call the same application use cases. Existing friend/admission/Card resources MUST NOT accept a hidden kind switch for this capability. Unknown fields including rules, status, kind, env and invented actor/audit identities MUST be rejected on writes.

#### Scenario: Required lifecycle through HTTP
- **WHEN** an authorized client submits a request, reads it, approves it, reads the edge and revokes it through the new resources
- **THEN** each response follows the documented status/schema and the observable domain state matches it

#### Scenario: Distinct rejection contracts
- **WHEN** requests lack valid authentication, lack scope, reference invisible resources, contain invalid fields, conflict on versions, exceed quotas or encounter dependencies failing
- **THEN** they return respectively documented 401/403/404/400/409/429/503 classes and stable non-secret error codes, not successful empty or partial responses

### Requirement: Atomic idempotent writes and race fencing

Create, approve, reject, cancel and revoke SHALL be fenced by state/version and idempotency receipts. Edge/request/generation/audit/receipt writes SHALL commit atomically; failure SHALL roll them back. Old successful idempotency replays after revoke MUST return historical outcome plus current state, never reactivate an edge. Unknown commit outcomes MUST be recoverable with the same key, not blind new actions. Target/source role checks and quota checks SHALL share the write's serialization boundary or equivalent version fence.

#### Scenario: Concurrent approve and cancel
- **WHEN** two transactions race approve and cancel for the same pending request
- **THEN** only one terminal transition commits and at most one direction edge is created

#### Scenario: Replayed create after revoke
- **WHEN** the old create idempotency key is replayed after a successful revoke
- **THEN** the system returns the original receipt and current revoked state without an approved write

#### Scenario: Audit failure and uncertain commit
- **WHEN** audit persistence fails before commit or commit acknowledgement times out
- **THEN** the first case rolls back all changes and the second reports outcome-uncertain with receipt reconciliation rather than falsely reporting success or issuing an unfenced retry

### Requirement: Exact revocation and durable lifecycle invalidation

Revocation SHALL identify one scoped SystemInvoke edge and its expected version, preserve other kinds and reverse edges, and audit the action. Identity binding or system eligibility loss SHALL durably invalidate the relevant approval generation. Restoring status/classification/bindings MUST NOT reactivate old approvals without reapproval. Reads and invocation SHALL fail closed if reliable invalidation generations cannot be proven.

#### Scenario: Mixed-grant revocation
- **WHEN** A→B system authorization is revoked while profile/rules or B→A grants also exist
- **THEN** those independent grants remain unchanged and cannot be reported as revoked by this operation

#### Scenario: Reclassified or recreated Bot
- **WHEN** a Bot is retired then restored, rebound or replaced with another bot_uuid
- **THEN** old system approval cannot transfer or silently resume

### Requirement: Bounded governance queries and quotas

Governance SHALL expose scoped inbound/outbound pages and degree aggregates separated by kind/status and security domain. Active out/in degrees SHALL count distinct target/source peers from stored approved rows with `count_basis=stored_approved` (not proof of current invocation eligibility), pending requests separately, with default page size 50 and maximum 100. Eligibility-invalid rows SHALL conservatively consume quota until durably revoked; dependency failure MUST NOT undercount them. Stable bound cursors SHALL reject changed filters. Writes SHALL enforce configured positive out/in/pending limits atomically, while revocation remains available to reduce risk.

#### Scenario: Mixed graph and empty page
- **WHEN** a Bot has friend, rules, revoked system and approved system edges, and the requested cursor is beyond the end
- **THEN** the page is empty with no false error, degree only includes stored approved system peers, and independent kinds/pending counts do not inflate it

#### Scenario: Concurrent limit boundary
- **WHEN** two approvals compete for the final remaining degree slot
- **THEN** at most one approval commits and the loser receives a documented quota failure without partial audit/edge writes

#### Scenario: Query or row decoding failure
- **WHEN** a governance list/count query or row decoding fails
- **THEN** the whole request fails, never returning a successful zero/partial graph

### Requirement: Indexed storage and frozen migrations

Stores SHALL own SQL, aggregate/count/pagination, identity comparison and transactions through declared outbound contracts. Existing numbered migrations MUST remain unchanged. Any added indexes/metadata SHALL use additive per-dialect migrations and retain exact identity comparison, the kind-inclusive unique key and single-approved-owner invariant. Query budgets, rows examined and failure paths SHALL be recorded and verified; SQLite success MUST NOT substitute for actual MySQL or host-dialect acceptance.

#### Scenario: Upgrade preserves existing authority and grants
- **WHEN** a populated pre-change database runs the complete additive migration chain
- **THEN** all four legacy kinds, uniqueness, owner invariant and byte-exact case/trailing-space identities remain intact

#### Scenario: High-degree query
- **WHEN** a maximum-size governance page is requested against a representative high-degree graph
- **THEN** store reads are bounded/paged and batched, no per-edge N+1 occurs, and required inbound/outbound query plans have measured evidence

### Requirement: Reader-first rollout and fail-safe rollback

Writer activation SHALL wait for compatible DB mappings, Service API consumers, runtime grant receivers, SDKs, request filters and host eligibility providers. Unknown kind MUST fail rather than map to a permissive legacy kind. Rollback SHALL disable new writes/admissions, preserve audit/schema and either retain compatible readers or prove old readers cannot touch new-kind rows; merely revoking them is insufficient for unconditional full-history decoders.

#### Scenario: Old reader remains
- **WHEN** any relevant runtime consumer cannot decode system_invoke
- **THEN** writers and delivery of new-kind contexts remain disabled

#### Scenario: Rollback with retained history
- **WHEN** new-kind rows remain in historical tables after rollback
- **THEN** reader compatibility or data isolation is verified without rewriting system grants into profile/rules/authority kinds

### Requirement: Architecture and layered regression gates

Implementation SHALL preserve domain/Service API/Plugin API/store/delivery/composition boundaries and central authorization hooks. Every changed boundary SHALL have shared conformance coverage across relevant memory/SQLite/MySQL providers, plus unit, registered-router HTTP contract, real-store and singlebox E2E coverage. Test results SHALL report actual counts, execution/skips and dialect limits; mocks/static specs alone MUST NOT be claimed as deployment or live authorization proof.

#### Scenario: Independent runtime proof
- **WHEN** implementation is submitted for acceptance
- **THEN** a real local HTTP/A2A fake-target chain proves denial before approval, success after approval, reverse denial and denial after revoke without creating friendship or management rights
- **AND** each affected GET/create/request/approve/reject/cancel/revoke/list/legacy path is tested independently, not substituted by one PATCH test

#### Scenario: Spec-only delivery
- **WHEN** this planning-only change is delivered
- **THEN** all implementation tasks remain unchecked, validation identifies only document/OpenSpec checks actually executed, and no runtime, production, CI, merge or deployment success is implied
