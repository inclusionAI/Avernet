# BCN OpenAPI V1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the first BCN OpenAPI V1 surface for Group, Session, Participant, Invitation, Friendship, and Session message history while preserving every Legacy BCS endpoint unchanged.

**Architecture:** Add a new `bcs-api-http` delivery adapter that depends only on versioned contracts under `bcs_service_api::application::v1`. Existing domain crates implement those contracts and own authorization; bootstrap injects them and mounts the new router beside `bcs-http`. Gateway authenticates callers, forwards a trusted Principal, routes five BCN resource domains, and aggregates the BCN-owned OpenAPI artifact.

**Tech Stack:** Rust 1.91, Axum 0.8, Tokio, Serde, `async-trait`, Python 3.13/PyYAML for contract tooling, FastAPI/Pydantic in Gateway, Cargo tests, Pytest.

---

## Execution constraints

- Read `AGENTS.md`, `src/bcs/AGENTS.md`, `src/bcs/CLAUDE.md`,
  `docs/arch/arch.rules.md`, and `docs/arch/ci.enforce.md` before implementation.
- Install repository hooks in the implementation worktree with
  `scripts/install_git_hooks.sh`.
- Do not run global `cargo fmt` or `cargo fmt --all`; format only touched Rust
  files explicitly.
- Do not modify Legacy route paths, request DTOs, authentication extraction,
  response bodies, or authorization behavior.
- Do not mount production V1 routes until the trusted Principal verification
  task is complete.
- Do not add `POST /openapi/v1/sessions/{session_id}/messages`, SSE,
  Provider, StateMachineRun, Service Invocation, CollaborationTemplate, or
  Internal API operations.
- The architecture source is
  `src/bcs/docs/plans/2026-07-28-bcn-openapi-v1-design.md`.

## Production blockers to resolve before Task 15

Obtain explicit decisions from the Gateway/identity owners for:

1. the BotPrincipal schema and authentication strategy;
2. the Human `subject.id` to BCN Bot `created_by` identity mapping;
3. the signed Principal token format, algorithm, issuer, audience, TTL, and key
   rotation contract.

Tasks 1–14 may use an injected test verifier. Task 15 must not choose production
cryptography by assumption.

### Task 1: Land the candidate OpenAPI contract and validator

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/bcs/api-contracts/v1/openapi.yaml`
- Create: `src/bcs/api-contracts/v1/shared.yaml`
- Create: `src/bcs/api-contracts/v1/domain-models.yaml`
- Create: `src/bcs/api-contracts/v1/openapi/groups.yaml`
- Create: `src/bcs/api-contracts/v1/openapi/sessions.yaml`
- Create: `src/bcs/api-contracts/v1/openapi/invitations.yaml`
- Create: `src/bcs/api-contracts/v1/openapi/friendships.yaml`
- Create: `src/bcs/api-contracts/v1/internal/.gitkeep`
- Create: `src/bcs/scripts/validate_openapi_contract.py`
- Create: `src/bcs/scripts/bundle_openapi_contract.py`
- Create: `src/bcs/tests/openapi/test_contract.py`

**Step 1: Add the failing contract inventory test**

Write a test that loads all path fragments and compares the exact `(method,
path)` set with the 27 operations in the design:

```python
EXPECTED = {
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/groups"),
    ("post", "/openapi/v1/groups"),
    ("get", "/openapi/v1/groups/{group_id}"),
    ("patch", "/openapi/v1/groups/{group_id}"),
    ("delete", "/openapi/v1/groups/{group_id}"),
    ("post", "/openapi/v1/groups/{group_id}/participants"),
    ("patch", "/openapi/v1/groups/{group_id}/participants/{bot_uuid}"),
    ("delete", "/openapi/v1/groups/{group_id}/participants/{bot_uuid}"),
    ("post", "/openapi/v1/groups/{group_id}/sessions"),
    ("get", "/openapi/v1/groups/{group_id}/sessions"),
    ("get", "/openapi/v1/sessions/{session_id}"),
    ("patch", "/openapi/v1/sessions/{session_id}"),
    ("delete", "/openapi/v1/sessions/{session_id}"),
    ("post", "/openapi/v1/sessions/{session_id}/completion"),
    ("get", "/openapi/v1/sessions/{session_id}/messages"),
    ("post", "/openapi/v1/sessions/{session_id}/participants"),
    ("patch", "/openapi/v1/sessions/{session_id}/participants/{bot_uuid}"),
    ("delete", "/openapi/v1/sessions/{session_id}/participants/{bot_uuid}"),
    ("post", "/openapi/v1/groups/{group_id}/invitations"),
    ("post", "/openapi/v1/sessions/{session_id}/invitations"),
    ("post", "/openapi/v1/invitations/{token}/accept"),
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/friendships"),
    ("delete", "/openapi/v1/bots/collaboration/{bot_uuid}/friendships/{friend_bot_uuid}"),
    ("post", "/openapi/v1/bots/collaboration/{bot_uuid}/friend-requests"),
    ("get", "/openapi/v1/bots/collaboration/{bot_uuid}/friend-requests"),
    ("post", "/openapi/v1/friend-requests/{request_id}/accept"),
    ("post", "/openapi/v1/friend-requests/{request_id}/reject"),
}
```

Also assert:

```python
assert ("post", "/openapi/v1/sessions/{session_id}/messages") not in actual
assert not any(path.startswith("/openapi/v1/bcn/") for _, path in actual)
assert not any(path.startswith("/openapi/v1/actors/") for _, path in actual)
assert not internal_operations
```

**Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest src/bcs/tests/openapi/test_contract.py -q
```

Expected: FAIL because the contract files and loader do not exist.

**Step 3: Add public YAML tooling**

Add `pyyaml>=6.0` as a public root development dependency and update `uv.lock`.
Do not add a company-only package source.

**Step 4: Create the contract fragments**

For every operation, define:

- globally unique `operationId`;
- request and response schemas;
- `x-avernet-security`;
- the common `{code, message, data, request_id}` envelope;
- stable error codes for every non-2xx response;
- stable ordering and `offset`/`limit` for list operations;
- `additionalProperties: false` for request objects unless extension data is an
  explicit field.

Use this root shape:

```yaml
openapi: 3.1.0
info:
  title: BCN OpenAPI
  version: 1.0.0
paths: {}
components:
  schemas: {}
```

Do not define raw BCN `humanCookie`, `botRuntimeBearer`, or
`agentPassBearer` extraction as downstream behavior.

**Step 5: Implement validation and bundling**

The validator must fail on:

- unresolved `$ref`;
- duplicate `operationId`;
- a path outside `/openapi/v1/**`;
- any Internal API operation in V1 phase one;
- a missing `x-avernet-security`;
- a non-envelope JSON response;
- an undeclared non-2xx error code.

The bundler writes a deterministic artifact under a caller-provided output
directory; it must not commit generated output.

**Step 6: Run contract tests**

Run:

```bash
uv run pytest src/bcs/tests/openapi/test_contract.py -q
uv run python src/bcs/scripts/validate_openapi_contract.py --root src/bcs/api-contracts/v1
```

Expected: PASS and `27 operations validated`.

**Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/bcs/api-contracts src/bcs/scripts/validate_openapi_contract.py src/bcs/scripts/bundle_openapi_contract.py src/bcs/tests/openapi
git commit -m "docs(bcs): add candidate OpenAPI v1 contract"
```

### Task 2: Define V1 Principal, authorization vocabulary, and application errors

**Files:**

- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/principal.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/error.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/tests/v1_boundary_contracts.rs`

**Step 1: Write failing boundary tests**

Test the intended identity invariants:

```rust
#[test]
fn bot_principal_uses_the_bcn_bot_uuid() {
    let principal = Principal::bot("bot-123", "tenant-a", []);
    assert_eq!(principal.bot_uuid(), Some("bot-123"));
}

#[test]
fn human_principal_does_not_claim_a_bot_identity() {
    let principal = Principal::human(
        AuthenticatedUser {
            id: "user-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        },
        "tenant-a",
        [],
    );
    assert_eq!(principal.bot_uuid(), None);
    assert_eq!(principal.authenticated_user().unwrap().id, "user-1");
}
```

Extend the existing AST boundary test to scan `application/v1` and assert that
it imports neither Axum nor `bcs_protocol`.

**Step 2: Run tests and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api --test v1_boundary_contracts
```

Expected: FAIL because `application::v1` does not exist.

**Step 3: Add the Principal model**

Use a closed first-phase enum:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Principal {
    Human(HumanPrincipal),
    Bot(BotPrincipal),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HumanPrincipal {
    pub subject: AuthenticatedUser,
    pub tenant: String,
    pub scopes: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotPrincipal {
    pub bot_uuid: String,
    pub tenant: String,
    pub scopes: BTreeSet<String>,
}
```

Do not add Provider, ServiceKey, Admin, Integration, Public, or
InternalService variants to this V1 enum.

**Step 4: Add the authorization vocabulary**

Define `Action`, `ResourceRef`, and an application-level authorizer:

```rust
#[async_trait]
pub trait AuthorizationService: Send + Sync {
    async fn authorize(
        &self,
        principal: &Principal,
        action: Action,
        resource: ResourceRef<'_>,
    ) -> Result<(), ApplicationError>;
}
```

`ApplicationError` must distinguish invalid input, unauthenticated,
forbidden, not found, conflict, gone, quota, and internal failure without
containing HTTP types.

**Step 5: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/bcs/crates/service-api/bcs-service-api
git commit -m "feat(bcs): define OpenAPI v1 principal and authorization contracts"
```

### Task 3: Define the versioned domain Service APIs

**Files:**

- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/invitation.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/friendship.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/message.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/tests/v1_application_contracts.rs`

**Step 1: Write compile-time contract tests**

Create Noop implementations for:

```text
application::v1::group::GroupService
application::v1::session::SessionService
application::v1::invitation::InvitationService
application::v1::friendship::FriendshipService
application::v1::message::SessionMessageService
```

Assert every command contains `Principal` and does not contain a raw Cookie,
Bearer token, `sender`, or `from`.

**Step 2: Run tests and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api --test v1_application_contracts
```

Expected: FAIL because the traits do not exist.

**Step 3: Define narrow traits**

Use module versioning instead of type prefixes:

```rust
#[async_trait]
pub trait GroupService: Send + Sync {
    async fn list_bot_groups(
        &self,
        command: ListBotGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError>;
    async fn create(&self, command: CreateGroup) -> Result<Group, ApplicationError>;
    async fn get(&self, query: GetGroup) -> Result<Group, ApplicationError>;
    async fn update(&self, command: UpdateGroup) -> Result<Group, ApplicationError>;
    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError>;
    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError>;
    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError>;
    async fn delete_participant(
        &self,
        command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError>;
}
```

Define the remaining traits with one method per Contract operation. Reuse pure
`bcs-domain` entities where their semantics match; introduce application
result types when a response needs idempotency or pagination metadata.

**Step 4: Keep HTTP DTOs out**

Add AST assertions that the new modules do not import:

```text
axum
http
bcs_protocol
serde_json::Value as an untyped request body
```

Allow `serde_json::Value` only for a domain-defined Session completion output
if the Contract explicitly declares that extension point.

**Step 5: Run all Service API tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/bcs/crates/service-api/bcs-service-api
git commit -m "feat(bcs): add OpenAPI v1 application service contracts"
```

### Task 4: Implement shared BCN authorization in the Group domain

**Files:**

- Create: `src/bcs/crates/services/bcs-group/src/application/v1/mod.rs`
- Create: `src/bcs/crates/services/bcs-group/src/application/v1/authorization.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/application/mod.rs`
- Create: `src/bcs/crates/services/bcs-group/tests/v1_authorization.rs`

**Step 1: Write failing policy tests**

Cover:

- Group manager may manage Group and Participants.
- Direct Participant may read but not manage.
- Session-only Participant may read only the relevant Session/parent projection.
- Bot may act only as its own `bot_uuid`.
- Human owner may manage an owned Bot's Friendship/resource relation.
- Human owner is not granted Bot message-sender identity.
- Cross-tenant access is denied before existence details are disclosed.

Use repository and registry test doubles; do not exercise HTTP.

**Step 2: Run tests and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group --test v1_authorization
```

Expected: FAIL because the V1 authorizer does not exist.

**Step 3: Implement `AuthorizationService`**

Build the result from existing domain sources:

- `GroupCoreService` for Group and GroupParticipant relations;
- `SessionManagementService` for SessionParticipant relations;
- `BotRegistryCoreService` for `created_by`;
- `RelationCoreService` only where the existing domain relation is
  authoritative.

Do not create a second ACL database or encode policy in Route handlers.

**Step 4: Run focused and crate tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group --test v1_authorization
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-group
git commit -m "feat(bcs): implement v1 resource authorization"
```

### Task 5: Implement V1 Group and GroupParticipant use cases

**Files:**

- Create: `src/bcs/crates/services/bcs-group/src/application/v1/group.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/application/v1/mod.rs`
- Create: `src/bcs/crates/services/bcs-group/tests/v1_group_service.rs`

**Step 1: Write failing use-case tests**

Test all eight Group/GroupParticipant operations, including:

- `membership=all|direct|session_only`;
- creation derives caller identity from Principal rather than request fields;
- update uses an explicit allow-list of mutable fields;
- delete is idempotent;
- Participant duplicate/add/remove conflicts;
- role changes preserve driver/manager invariants;
- unauthorized access returns `ApplicationError::Forbidden`;
- invisible resources return the Contract-approved not-found response.

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group --test v1_group_service
```

Expected: FAIL because `GroupServiceImpl` does not exist.

**Step 3: Implement a V1 facade over existing capabilities**

The facade calls the V1 authorizer, then delegates to existing
`GroupManagementService`, `GroupQueryService`, and
`SessionManagementService`. Do not call Legacy handlers and do not copy their
HTTP error mapping.

**Step 4: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-group
git commit -m "feat(bcs): implement v1 group use cases"
```

### Task 6: Implement V1 Session and SessionParticipant use cases

**Files:**

- Create: `src/bcs/crates/services/bcs-session/src/application/mod.rs`
- Create: `src/bcs/crates/services/bcs-session/src/application/legacy.rs`
- Create: `src/bcs/crates/services/bcs-session/src/application/v1.rs`
- Modify: `src/bcs/crates/services/bcs-session/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-session/tests/v1_session_service.rs`

**Step 1: Move the existing implementation without behavior changes**

Move the current `application.rs` content to `application/legacy.rs`, re-export
`SessionManagementServiceImpl`, and run existing tests before adding V1 code.
Use `git mv`; do not edit behavior in this step.

**Step 2: Verify the move**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-session
```

Expected: PASS with no test changes.

**Step 3: Write failing V1 tests**

Cover all nine Session/SessionParticipant operations:

- create/list under a Group;
- read/update/delete Session;
- completion only from Running and idempotent after Completed;
- add/update/delete Participant;
- Human is never enrolled as a Participant and may only manage an authorized
  target Bot's membership;
- completed Session rejects mutable operations;
- Session belongs to the Group in the path;
- a caller cannot replace its Principal or manage an unauthorized `bot_uuid`.

**Step 4: Implement `SessionServiceImpl`**

Inject `Arc<dyn AuthorizationService>` and the existing
`SessionManagementService`. Reuse the existing persistence and state transition
logic. Do not route V1 completion through the Legacy HTTP function.

**Step 5: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-session
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/bcs/crates/services/bcs-session
git commit -m "feat(bcs): implement v1 session use cases"
```

### Task 7: Implement Invitation, Friendship, and message-history use cases

**Files:**

- Create: `src/bcs/crates/services/bcs-group/src/application/v1/invitation.rs`
- Modify: `src/bcs/crates/services/bcs-group/src/application/v1/mod.rs`
- Create: `src/bcs/crates/services/bcs-group/tests/v1_invitation_service.rs`
- Create: `src/bcs/crates/services/bcs-friend/src/application/v1.rs`
- Modify: `src/bcs/crates/services/bcs-friend/src/application/mod.rs`
- Create: `src/bcs/crates/services/bcs-friend/tests/v1_friendship_service.rs`
- Create: `src/bcs/crates/services/bcs-message/src/v1.rs`
- Modify: `src/bcs/crates/services/bcs-message/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-message/tests/v1_session_message_service.rs`

**Step 1: Write failing Invitation tests**

Test Group/Session invitation creation and unified token acceptance:

- token identifies target type and target ID;
- a Bot Principal joins only its own `bot_uuid`;
- a Human Principal may name only a target `bot_uuid` whose authoritative
  `created_by` matches the Human subject;
- expired token returns Gone;
- duplicate acceptance is idempotent;
- completed/non-joinable Session returns Conflict.

**Step 2: Implement Invitation V1 over existing InviteService**

Reuse token validation and join behavior, but map the unified
`/invitations/{token}/accept` command into Group or Session behavior inside the
application layer.

**Step 3: Write failing Friendship tests**

Cover all six operations and assert:

- both relationship endpoints are identified by BCN `bot_uuid`;
- Bot caller manages itself only;
- Human caller may manage only a Bot whose authoritative `created_by` matches;
- accept/reject only works for the receiver;
- delete is symmetric and idempotent;
- pagination and ordering are stable.

**Step 4: Implement Friendship V1**

Delegate persistence to existing friend/friend-request core services. Keep
Human ownership authorization in the application layer, not the HTTP adapter.

**Step 5: Write failing message-history tests**

Test:

- only GET exists;
- Session visibility is authorized;
- messages are sorted by `session_seq`;
- offset/limit are deterministic;
- `view_bot_id` or an equivalent perspective override is absent;
- ServiceInvocation/StateMachine-only history is outside this phase.

**Step 6: Implement `SessionMessageService`**

Reuse `GroupMessageHistoryService`; do not add sending methods to the V1 trait.

**Step 7: Run focused tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group --test v1_invitation_service
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-friend --test v1_friendship_service
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-message --test v1_session_message_service
```

Expected: PASS.

**Step 8: Commit**

```bash
git add src/bcs/crates/services/bcs-group src/bcs/crates/services/bcs-friend src/bcs/crates/services/bcs-message
git commit -m "feat(bcs): implement v1 invitation friendship and history use cases"
```

### Task 8: Expose V1 services through the service container

**Files:**

- Modify: `src/bcs/crates/service-api/bcs-services-container/src/services.rs`
- Modify: `src/bcs/crates/service-api/bcs-services-container/src/test_support.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/http_adapter.rs`
- Create: `src/bcs/crates/service-api/bcs-services-container/tests/v1_services.rs`

**Step 1: Write the failing builder test**

Assert production builder fails when a required V1 service is missing and
test-support can install V1 Noops explicitly.

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-services-container --test v1_services
```

Expected: FAIL because the fields and builder methods do not exist.

**Step 3: Add V1 service handles**

Add named fields:

```text
v1_authorization
v1_groups
v1_sessions
v1_invitations
v1_friendships
v1_session_messages
```

Do not replace or alias existing Legacy service fields.

**Step 4: Wire concrete implementations in bootstrap**

Construct the authorizer first, inject it into each domain V1 service, and
insert all handles into `ServicesBuilder`.

**Step 5: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-services-container
cargo check --manifest-path src/bcs/Cargo.toml -p bcs
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/bcs/crates/service-api/bcs-services-container src/bcs/crates/bootstrap/bcs/src/http_adapter.rs
git commit -m "feat(bcs): wire v1 application services"
```

### Task 9: Create the `bcs-api-http` adapter and common protocol behavior

**Files:**

- Modify: `src/bcs/Cargo.toml`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/Cargo.toml`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/lib.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/envelope.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/error.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/principal.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/request_id.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/router.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/state.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/common_contract.rs`

**Step 1: Write failing Envelope and Principal extraction tests**

Cover:

- success and every application error category map to the Contract envelope;
- request ID is propagated or generated;
- missing/invalid Principal returns `401`;
- a verified Principal is inserted into request extensions;
- there is no fallback to Legacy Cookie/token extraction;
- Internal router has zero registered business routes.

Use a test-only verifier:

```rust
#[async_trait]
pub trait PrincipalVerifier: Send + Sync {
    async fn verify(&self, headers: &HeaderMap) -> Result<Principal, PrincipalError>;
}
```

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test common_contract
```

Expected: FAIL because the crate does not exist.

**Step 3: Add the crate and context boundary**

Dependencies are limited to Axum, Serde, Tokio/async-trait as needed,
`bcs-service-api`, and `bcs-services-container`. Do not depend on
`services/*`, `bcs-http`, or `bcs-protocol`.

**Step 4: Implement common behavior**

Define:

```rust
pub struct Envelope<T> {
    pub code: u32,
    pub message: String,
    pub data: T,
    pub request_id: String,
}
```

Keep all status/error translation in `common/error.rs`; Route modules return a
single adapter error type.

**Step 5: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/bcs/Cargo.toml src/bcs/crates/adapters/http/bcs-api-http
git commit -m "feat(bcs): add OpenAPI v1 HTTP adapter foundation"
```

### Task 10: Add Group and GroupParticipant HTTP routes

**Files:**

- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/router.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_contract.rs`

**Step 1: Write failing route tests**

Test all eight operations with fake V1 services. For each, assert:

- exact path and method;
- Principal forwarded unchanged;
- request DTO rejects unknown fields;
- response uses the common envelope;
- each Application error maps to the specified HTTP status and stable code.

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test group_contract
```

Expected: FAIL with unmatched routes.

**Step 3: Implement thin route handlers**

Handlers may parse Path/Query/JSON and map DTOs only. They must not query a
Repo, inspect `created_by`, or decide Participant permissions.

**Step 4: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test group_contract
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http
git commit -m "feat(bcs): expose v1 group HTTP routes"
```

### Task 11: Add Session, SessionParticipant, completion, and history routes

**Files:**

- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/router.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_contract.rs`

**Step 1: Write failing route tests**

Test all nine Session operations. Explicitly assert:

```rust
assert_route_absent(Method::POST, "/openapi/v1/sessions/s-1/messages");
assert_route_absent(Method::POST, "/openapi/v1/sessions/s-1/chat");
```

Also verify no request DTO contains `sender_bot_uuid`, `sender`, `from`, or
`view_bot_uuid`.

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test session_contract
```

Expected: FAIL with unmatched routes.

**Step 3: Implement thin handlers**

The message route is GET-only. The completion route calls
`SessionService::complete` and returns the documented idempotent result.

**Step 4: Run tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test session_contract
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http
git commit -m "feat(bcs): expose v1 session HTTP routes"
```

### Task 12: Add Invitation and Friendship HTTP routes

**Files:**

- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/invitation.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/friendship.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/invitation.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/friendship.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/router.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_contract.rs`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/friendship_contract.rs`

**Step 1: Write failing route tests**

Cover all nine operations and verify:

- invitation acceptance uses the Bot Principal's own `bot_uuid`, or a
  Human-authorized target `bot_uuid`;
- Bot-scoped paths use `/bots/collaboration/{bot_uuid}` and never `/actors`;
- `from_bot_uuid` cannot override a Bot Principal;
- Human-owned-Bot management is passed to Application authorization;
- no Legacy response shapes leak into V1.

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test invitation_contract
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test friendship_contract
```

Expected: FAIL with unmatched routes.

**Step 3: Implement handlers and DTO mappings**

Keep token parsing/verification and Friendship policy in Application services.
The HTTP adapter treats token and IDs as validated protocol strings only.

**Step 4: Run tests**

Run the two focused tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http
git commit -m "feat(bcs): expose v1 invitation and friendship routes"
```

### Task 13: Mount V1 beside Legacy and prove route isolation

**Files:**

- Modify: `src/bcs/crates/bootstrap/bcs/Cargo.toml`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/http_adapter.rs`
- Create: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`
- Modify: `src/bcs/scripts/adapters_endpoint_coverage.py`

**Step 1: Write failing mount tests**

Build the complete router and assert:

- all 27 V1 routes are mounted;
- V1 POST Session messages is `404/405`;
- V1 Internal operations are absent;
- representative Legacy routes still resolve:
  - `POST /groups/{id}/messages`;
  - `POST /groups/{id}/chat`;
  - `POST /sessions/{sid}/chat`;
  - `GET /sessions/{sid}/messages`;
- V1 and Legacy states use separate adapters but the same injected domain
  services.

**Step 2: Run and verify failure**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --test openapi_v1_mount
```

Expected: FAIL because V1 is not mounted.

**Step 3: Merge the V1 router in bootstrap**

Add the `bcs-api-http` dependency and merge its router without nesting or
rewriting the path. Keep `bcs_http::router::build_router` unchanged.

**Step 4: Update endpoint coverage inventory**

Add the 27 V1 operations as Router API leaves and keep all Legacy denominators.
Do not mark absent POST Message/Internal routes as covered.

**Step 5: Run adapter and bootstrap tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http
cargo test --manifest-path src/bcs/Cargo.toml -p bcs --test openapi_v1_mount
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs src/bcs/scripts/adapters_endpoint_coverage.py
git commit -m "feat(bcs): mount OpenAPI v1 beside legacy routes"
```

### Task 14: Register BCN resource domains and aggregate its schema in Gateway

**Files:**

- Modify: `src/gateway/configs/upstreams.yaml`
- Modify: `src/gateway/configs/route_security.yaml`
- Modify: `src/gateway/tests/test_domain_map.py`
- Modify: `src/gateway/tests/test_served_openapi.py`
- Modify: `src/gateway/tests/integration/test_forward_route.py`
- Add generated test fixture under: `src/gateway/tests/fixtures/bcn.openapi.json`

**Step 1: Write failing domain tests**

Parametrize:

```python
@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/groups/g1",
        "/openapi/v1/sessions/s1",
        "/openapi/v1/bots/collaboration/b1/groups",
        "/openapi/v1/invitations/t/accept",
        "/openapi/v1/friend-requests/r1/accept",
    ],
)
def test_bcn_domains_resolve_to_one_server(path: str) -> None:
    ...
```

Also assert `/openapi/v1/bots/b1` still resolves to AgentClaw/TeamClaw, while
`/openapi/v1/bots/collaboration/b1/groups` resolves to BCN by longest-prefix
matching.

**Step 2: Run and verify failure**

Run:

```bash
cd src/gateway
uv run pytest tests/test_domain_map.py tests/test_served_openapi.py -q
```

Expected: FAIL because BCN domains are not configured.

**Step 3: Add one BCN server and resource-domain aliases**

Add one `bcn` server and map `groups`, `sessions`, `bots/collaboration`,
`invitations`, and `friend-requests` to it. Each alias points to the same BCN
schema artifact; do not duplicate schemas per operation. Keep the general
`bots/**` mapping owned by AgentClaw/TeamClaw.

**Step 4: Add fail-closed route security**

Declare the currently implemented Human strategy explicitly for testability.
Do not claim BotPrincipal support until Task 15 is complete.

**Step 5: Run Gateway tests**

Run:

```bash
cd src/gateway
uv run pytest tests/test_domain_map.py tests/test_served_openapi.py tests/integration/test_forward_route.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add src/gateway/configs src/gateway/tests
git commit -m "feat(gateway): route BCN OpenAPI resource domains"
```

### Task 15: Implement the approved trusted Principal transport

**Prerequisite:** The three production blockers at the top of this plan have
written owner approval. If not, stop here; do not expose V1 in production.

**Files:**

- Modify: `src/gateway/src/gateway/community/spi/authn/_models.py`
- Modify: `src/gateway/src/gateway/community/bootstrap/_authn.py`
- Modify: `src/gateway/src/gateway/community/adapters/web/_forward.py`
- Modify or create signer SPI files under:
  `src/gateway/src/gateway/community/spi/authn/`
- Modify or create signer implementation under:
  `src/gateway/src/gateway/community/plugins/authn/`
- Modify: `src/gateway/tests/test_authn_models.py`
- Create: `src/gateway/tests/test_principal_forwarding.py`
- Create verifier implementation under:
  `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/`
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/principal_verification.rs`
- Modify BCN config/bootstrap files required by the approved verifier flavor

**Step 1: Write Gateway Principal model tests**

Test exact serialization for UserPrincipal and the approved BotPrincipal.
Reject ambiguous or unknown principal types.

**Step 2: Write Gateway forwarding tests**

Assert:

- `Authenticator.authenticate` result is passed to the signer;
- only the signed token is attached downstream;
- incoming spoofed Principal headers are removed;
- audience is `bcn`;
- method/path binding follows the approved contract.

**Step 3: Write BCN verifier tests**

Assert rejection of:

- missing token;
- malformed token;
- invalid signature;
- expired token;
- wrong issuer;
- wrong audience;
- unsupported Principal type;
- Human with a subject ID that cannot be evaluated against BCN `created_by`;
- Bot without a valid BCN `bot_uuid`.

**Step 4: Run tests and verify failure**

Run:

```bash
cd src/gateway
uv run pytest tests/test_authn_models.py tests/test_principal_forwarding.py -q
cd ../..
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test principal_verification
```

Expected: FAIL before implementation.

**Step 5: Implement exactly the approved transport**

Keep signing in Gateway and verification in the BCN adapter boundary. Project
the verified wire claims into `bcs_service_api::application::v1::Principal`;
do not expose the wire JWT type to Application or domain code.

**Step 6: Run focused and full auth tests**

Run:

```bash
cd src/gateway
uv run pytest tests/test_auth_runner.py tests/test_authn_models.py tests/test_principal_forwarding.py -q
cd ../..
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/gateway src/bcs/crates/adapters/http/bcs-api-http
git commit -m "feat: forward and verify trusted Gateway principals"
```

### Task 16: Add generated API reference and compatibility gates

**Files:**

- Modify: `src/bcs/scripts/bundle_openapi_contract.py`
- Create: `src/bcs/scripts/generate_api_reference.py`
- Create: `src/bcs/scripts/check_openapi_compat.py`
- Create: `src/bcs/tests/openapi/test_generation.py`
- Create: `src/bcs/tests/openapi/test_compatibility.py`
- Modify: `src/bcs/scripts/ci_test.sh`
- Modify: `scripts/ci/singlebox_coverage_modules.yaml`
- Modify: `.github/workflows/singlebox-coverage.yml` if the canonical entrypoint
  does not already pick up the BCS contract gate
- Modify: `src/gateway/scripts/gate_and_publish_openapi.py`
- Modify: `src/gateway/tests/test_gate_and_publish.py`

**Step 1: Write failing deterministic-generation tests**

Generate twice into separate temporary directories and compare bytes. Assert
the public artifact contains exactly 27 operations and the Internal artifact
contains zero.

**Step 2: Write failing compatibility tests**

Fixtures must prove the gate rejects:

- operation removal;
- optional-to-required input;
- type/default change;
- enum narrowing;
- response field removal.

It must allow a new operation and a new optional response field.

**Step 3: Run and verify failure**

Run:

```bash
uv run pytest src/bcs/tests/openapi/test_generation.py src/bcs/tests/openapi/test_compatibility.py -q
```

Expected: FAIL because the generators/gates do not exist.

**Step 4: Implement generated artifacts**

Generate:

- bundled OpenAPI JSON/YAML;
- static Swagger UI or ReDoc assets that reference the bundle;
- a machine-readable route inventory.

Write all output to a supplied build/artifact directory. Do not commit local
generated output or machine-specific paths.

**Step 5: Integrate publish-time compatibility**

BCN release CI compares the candidate with the current published V1 artifact.
Gateway consumes only a successfully published artifact and retains its
last-known-good document on refresh failure.

**Step 6: Run tests and CI entrypoints**

Run:

```bash
uv run pytest src/bcs/tests/openapi -q
src/bcs/scripts/ci_test.sh --fast-fail
cd src/gateway
uv run pytest tests/test_gate_and_publish.py tests/test_served_openapi.py -q
```

Expected: PASS.

**Step 7: Commit**

```bash
git add src/bcs/scripts src/bcs/tests/openapi scripts/ci .github/workflows src/gateway/scripts src/gateway/tests
git commit -m "ci: generate and compatibility-check BCN OpenAPI"
```

### Task 17: Add cross-component E2E and release evidence

**Files:**

- Create: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_gateway_e2e.rs`
- Modify: `src/bcs/scripts/e2e-test/stories.sh`
- Modify: `scripts/ci/singlebox_coverage_modules.yaml`
- Modify: `src/bcs/docs/plans/2026-07-28-bcn-openapi-v1-design.md`

**Step 1: Add failing E2E stories**

Cover at least:

1. Human creates Group, manages Participant, creates Session, reads history,
   completes Session.
2. Bot friendship request lifecycle using BotPrincipal.
3. Human manages an owned Bot's Friendship without being treated as that Bot.
4. Invitation create/accept for Group and Session.
5. Cross-tenant and cross-resource attempts are rejected.
6. Missing/tampered Gateway Principal is rejected.
7. `POST /openapi/v1/sessions/{id}/messages` remains absent.
8. Representative Legacy chat/message/CLI stories still pass.

**Step 2: Run the focused E2E and verify failure**

Use the existing singlebox runner with the BCS module. Expected: new stories
fail before fixtures and Gateway Principal wiring are complete.

**Step 3: Complete fixtures only**

Add test identities, Bots, Groups, and published contract fixtures. Do not add
test-only production bypasses.

**Step 4: Run the required verification**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --workspace
cd src/gateway
uv run pytest -q
cd ../..
scripts/ci/singlebox_coverage.sh --module bcs
```

Expected:

- all Cargo tests pass;
- all Gateway tests pass;
- all new V1 and existing Legacy E2E stories pass;
- BCS endpoint coverage remains 100%;
- configured coverage thresholds pass.

**Step 5: Update design status and evidence**

Change the design status from `Draft for review` to `Implemented` only after
the required verification passes. Record:

- final Principal contract owner;
- published OpenAPI artifact location;
- compatibility baseline version;
- test commands and artifact paths.

**Step 6: Commit**

```bash
git add src/bcs/crates/bootstrap/bcs/tests src/bcs/scripts/e2e-test scripts/ci src/bcs/docs/plans/2026-07-28-bcn-openapi-v1-design.md
git commit -m "test: cover BCN OpenAPI v1 end to end"
```

## Final verification and handoff

Run:

```bash
git diff --check
cargo test --manifest-path src/bcs/Cargo.toml --workspace
cd src/gateway
uv run pytest -q
cd ../..
uv run pytest src/bcs/tests/openapi -q
scripts/ci/singlebox_coverage.sh --module bcs
git status --short
```

Expected: all tests and coverage gates pass; only intentionally untracked user
files remain; no generated API artifacts or local secrets are added.
