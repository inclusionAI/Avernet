# OpenAPI V1 Session Collection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose idempotent Session collect and uncollect operations through BCN OpenAPI V1 for an authenticated Human acting on behalf of an owned participant Bot.

**Architecture:** Extend the transport-independent V1 `SessionService`, implement authorization and legacy-use-case delegation in `bcs-app-session`, and keep `bcs-api-http` limited to parsing and V1 envelope translation. Reuse `SessionManagementService::collect/uncollect` and the existing Session stores without persistence changes, then regenerate the Gateway-owned OpenAPI artifact.

**Tech Stack:** Rust, Axum, async-trait, serde, Python, pytest, PyYAML, OpenAPI 3.1, Cargo, uv.

---

## Working Rules

- Read `AGENTS.md`, `src/bcs/AGENTS.md`, and `src/bcs/CLAUDE.md` before implementation.
- Use @superpowers:test-driven-development for each behavior change.
- Do not run `cargo fmt`, `cargo fmt --all`, or another global formatter in BCS.
- Keep the existing legacy routes and tests unchanged.
- Do not add CLI commands, list filters, migrations, or Bot-Principal support.
- Commit only the files named by each task; preserve unrelated worktree changes.

### Task 1: Define the additive OpenAPI contract

**Files:**

- Modify: `src/bcs/tests/openapi/test_contract.py`
- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi/sessions.yaml`
- Modify: `src/bcs/api-contracts/README.md`

**Step 1: Write the failing contract inventory test**

Add these operations to `EXPECTED_OPERATIONS` in `test_contract.py`:

```python
("post", "/openapi/v1/collaboration/sessions/{session_id}/collect"),
("delete", "/openapi/v1/collaboration/sessions/{session_id}/collect"),
```

Rename `test_contract_contains_exactly_the_41_approved_operations` to use 43.

**Step 2: Write failing Session collection schema tests**

Add tests to `test_session_v1_contract.py` that assert:

```python
path = contract["paths"][
    "/openapi/v1/collaboration/sessions/{session_id}/collect"
]
assert set(path) == {"post", "delete"}
assert path["post"]["x-avernet-security"] == {
    "user": "required",
    "app": "required",
}
assert path["delete"]["x-avernet-security"] == {
    "user": "required",
    "app": "required",
}
```

Assert that POST has a required, strict JSON object with one required
`participant` string and that DELETE has one required `participant` query
parameter. Assert that both 200 responses contain a strict V1 envelope whose
`data` has exactly `session_id`, `participant`, and `collected`, and that the
declared errors include 400/401/403/404/500 with `session_not_found` on 404.

**Step 3: Run the tests to verify they fail**

Run from the repository root:

```bash
uv run --with pytest --with pyyaml pytest \
  src/bcs/tests/openapi/test_contract.py \
  src/bcs/tests/openapi/test_session_v1_contract.py -q
```

Expected: FAIL because the collection path is absent and the inventory still
contains only 41 operations.

**Step 4: Add the OpenAPI path and schemas**

In `openapi.yaml`, add:

```yaml
  /openapi/v1/collaboration/sessions/{session_id}/collect:
    $ref: ./openapi/sessions.yaml#/SessionCollectionPath
```

In `sessions.yaml`, add strict schemas equivalent to:

```yaml
CollectSessionRequest:
  type: object
  additionalProperties: false
  required: [participant]
  properties:
    participant:
      type: string
      minLength: 1

SessionCollectionResult:
  type: object
  additionalProperties: false
  required: [session_id, participant, collected]
  properties:
    session_id:
      type: string
    participant:
      type: string
    collected:
      type: boolean

SessionCollectionEnvelope:
  type: object
  additionalProperties: false
  required: [code, message, data, request_id]
  properties:
    code:
      const: 20000
    message:
      type: string
    data:
      $ref: "#/SessionCollectionResult"
    request_id:
      $ref: ../shared.yaml#/RequestId
```

Define `SessionCollectionPath` with:

- POST operation ID `collect_session`, required JSON body
  `CollectSessionRequest`, and 200 `SessionCollectionEnvelope`.
- DELETE operation ID `uncollect_session`, required `participant` query
  parameter, and 200 `SessionCollectionEnvelope`.
- `x-avernet-security: {user: required, app: required}` on both operations.
- 400 `invalid_request`, 401 `unauthenticated`, 403 `forbidden`, 404
  `session_not_found`, and the shared 500 response.

Update `api-contracts/README.md` from 41 to 43 approved operations and mention
the two collection operations in the Session section.

**Step 5: Validate the contract and tests**

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pytest --with pyyaml pytest \
  src/bcs/tests/openapi/test_contract.py \
  src/bcs/tests/openapi/test_session_v1_contract.py -q
```

Expected: validator prints `43 operations validated`; tests PASS.

**Step 6: Commit**

```bash
git add src/bcs/api-contracts/v1/openapi.yaml \
  src/bcs/api-contracts/v1/openapi/sessions.yaml \
  src/bcs/api-contracts/README.md \
  src/bcs/tests/openapi/test_contract.py \
  src/bcs/tests/openapi/test_session_v1_contract.py
git commit -m "feat(bcs): define session collection OpenAPI contract"
```

### Task 2: Extend the V1 Session application contract

**Files:**

- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/v1_session_application_contracts.rs`

**Step 1: Write the failing application contract test**

Import and construct `CollectSession`, `UncollectSession`, and
`SessionCollectionResult` in `v1_session_application_contracts.rs`. Add their
callers to `session_commands_carry_caller_and_no_raw_credentials`, and assert:

```rust
assert_eq!(collect.session_id, "s1");
assert_eq!(collect.participant, "bot-1");
assert_eq!(uncollect.session_id, "s1");
assert_eq!(uncollect.participant, "bot-1");

let result = SessionCollectionResult {
    session_id: "s1".into(),
    participant: "bot-1".into(),
    collected: true,
};
assert_eq!(serde_json::to_value(result).unwrap()["collected"], true);
```

Extend `NoopSessionService` with `collect` and `uncollect` methods so the test
also proves the expanded trait remains object-safe.

**Step 2: Run the test to verify it fails**

```bash
cd src/bcs
cargo test -p bcs-service-api --test v1_session_application_contracts
```

Expected: compilation FAIL because the three collection contract types and
trait methods do not exist.

**Step 3: Add commands, result, and trait methods**

Add to `application/v1/session.rs`:

```rust
#[derive(Debug, Clone)]
pub struct CollectSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub participant: String,
}

#[derive(Debug, Clone)]
pub struct UncollectSession {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub participant: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionCollectionResult {
    pub session_id: String,
    pub participant: String,
    pub collected: bool,
}
```

Add required methods to `SessionService`:

```rust
async fn collect(
    &self,
    command: CollectSession,
) -> Result<SessionCollectionResult, ApplicationError>;

async fn uncollect(
    &self,
    command: UncollectSession,
) -> Result<SessionCollectionResult, ApplicationError>;
```

**Step 4: Run the contract test**

```bash
cd src/bcs
cargo test -p bcs-service-api --test v1_session_application_contracts
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs \
  src/bcs/crates/service-api/bcs-service-api/tests/v1_session_application_contracts.rs
git commit -m "feat(bcs): add V1 session collection service contract"
```

### Task 3: Implement Human-for-owned-Bot authorization in the V1 facade

**Files:**

- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/tests/group_session_connection.rs`

**Step 1: Add failing facade tests**

In `v1_session_service.rs`, use the real `MemorySessionRepo` fixture to cover:

1. Human collects for an owned Bot participant and
   `collected_at_map(&[session_id], bot_id)` contains the Session.
2. Repeated collect remains successful and preserves one collection entry.
3. Human uncollects and a repeated uncollect remains successful.
4. A Bot owned by another Human returns `ApplicationError::Forbidden`.
5. An owned Bot absent from the Session returns `session_not_found`.
6. A missing Session returns `session_not_found`.
7. Bot-only and App-only callers return `ApplicationError::Forbidden`.

The happy-path assertion should exercise the public V1 service:

```rust
let result = fixture
    .service
    .collect(CollectSession {
        caller: human_caller("owner-1"),
        session_id: session_id.into(),
        participant: "bot-1".into(),
    })
    .await
    .expect("owned participant Bot can collect");
assert_eq!(result.participant, "bot-1");
assert!(result.collected);
```

Add placeholder `collect` and `uncollect` methods to the `FakeSessionService`
in `group_session_connection.rs` so that test target can compile after the
trait expands.

**Step 2: Run the facade tests to verify failure**

```bash
cd src/bcs
cargo test -p bcs-app-session --test v1_session_service
```

Expected: FAIL because `SessionServiceImpl` has not implemented the new
methods.

**Step 3: Add a targeted ownership-and-membership helper**

In `SessionServiceImpl`, add a helper with this behavior:

```rust
async fn load_owned_participant_bot(
    &self,
    caller: &AuthenticatedCaller,
    session_id: &str,
    participant: &str,
) -> Result<Session, ApplicationError> {
    let user = require_authenticated_user(caller)?;
    let owned = self
        .registry
        .try_get(participant)
        .await
        .map_err(map_service_error)?
        .is_some_and(|bot| {
            bot.actor_kind == ActorKind::Bot
                && bot.created_by.as_deref() == Some(user.id.as_str())
        });
    if !owned {
        return Err(ApplicationError::forbidden(
            "The target Bot is not owned by the authenticated Human",
        ));
    }

    let session = self.load_session(session_id).await?;
    let present = session.participants.iter().any(|entry| {
        entry.actor_kind == ActorKind::Bot && entry.bot_uuid == participant
    });
    if !present {
        return Err(ApplicationError::not_found(
            "session_not_found",
            format!("Session '{session_id}' was not found"),
        ));
    }
    Ok(session)
}
```

This deliberately maps a missing/non-Bot/non-owned target to 403 and an owned
Bot that is not a participant to 404, matching the approved design.

**Step 4: Implement both V1 facade methods**

Implement `SessionService::collect` and `uncollect` by calling the helper,
delegating to `self.sessions.collect/uncollect`, mapping errors through
`map_session_error`, and returning:

```rust
SessionCollectionResult {
    session_id: command.session_id,
    participant: command.participant,
    collected: true, // false for uncollect
}
```

Do not access `SessionRepoPort` directly for these mutations.

**Step 5: Run the facade package tests**

```bash
cd src/bcs
cargo test -p bcs-app-session --test v1_session_service
cargo test -p bcs-app-session --test group_session_connection
```

Expected: PASS.

**Step 6: Commit**

```bash
git add \
  src/bcs/crates/application/v1/bcs-app-session/src/lib.rs \
  src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs \
  src/bcs/crates/application/v1/bcs-app-session/tests/group_session_connection.rs
git commit -m "feat(bcs): authorize V1 session collection"
```

### Task 4: Add the versioned HTTP routes and envelopes

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/friendship_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/boundary_contract.rs`

**Step 1: Add failing route tests**

Extend `FakeSessionService` in `session_routes.rs` with recorded
`CollectSession` and `UncollectSession` commands. Add tests that verify:

- POST with `{"participant":"bot-1"}` returns a 200 V1 envelope and forwards
  the authenticated caller, Session ID, and participant.
- DELETE with `?participant=bot-1` returns a 200 envelope with
  `collected: false` and forwards the same fields.
- Missing POST body, missing DELETE query, an empty participant, and unknown
  JSON fields return 400 `invalid_request` without invoking the service.
- An application 403/404 is translated to the declared V1 error envelope.

Add required no-op `collect` and `uncollect` implementations to every other
`SessionService` test double listed in the Files section. Do not add default
trait implementations merely to make mocks compile.

**Step 2: Run route tests to verify failure**

```bash
cd src/bcs
cargo test -p bcs-api-http --test session_routes
```

Expected: FAIL because the collection route and DTO do not exist.

**Step 3: Add strict request DTOs**

In `dto/session.rs`, add separate names for the body and query shapes even
though both contain the same field:

```rust
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollectSessionRequest {
    pub participant: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UncollectSessionQuery {
    pub participant: String,
}
```

After extraction, reject `participant.trim().is_empty()` with
`invalid_request` before invoking the service. Preserve the original non-blank
identifier rather than silently trimming or rewriting it.

**Step 4: Register and implement the handlers**

Add to the Session router:

```rust
.route(
    "/sessions/{session_id}/collect",
    post(collect_session).delete(uncollect_session),
)
```

`collect_session` extracts `Path`, `Json<CollectSessionRequest>`, caller, and
request ID; `uncollect_session` extracts `Path`,
`Query<UncollectSessionQuery>`, caller, and request ID. Each builds the V1
command, calls `state.session_service`, maps errors through
`application_error_response`, and returns:

```rust
(
    StatusCode::OK,
    Json(Envelope::success(20_000, "OK", result, request_id.0)),
)
```

Do not import or call `bcs-http`, a concrete service, core trait, or repo port.

**Step 5: Run adapter and boundary tests**

```bash
cd src/bcs
cargo test -p bcs-api-http --test session_routes
cargo test -p bcs-api-http --test boundary_contract
```

Expected: PASS, including the adapter dependency boundary.

**Step 6: Commit**

```bash
git add \
  src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs \
  src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/friendship_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/boundary_contract.rs
git commit -m "feat(bcs): serve OpenAPI session collection routes"
```

### Task 5: Prove production mounting and publish the Gateway schema

**Files:**

- Modify: `src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs`
- Modify: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify: `src/gateway/tests/unit/scripts/test_dump_and_publish_script.py`
- Modify: `src/gateway/tests/unit/scripts/test_gate_and_publish.py`
- Modify: `src/gateway/tests/unit/core/forwarding/test_served_openapi.py`
- Modify: `src/gateway/tests/unit/core/authn/test_route_security.py`

**Step 1: Add failing mount and publication assertions**

Add a bootstrap integration assertion that a request to each new mounted path
without a Gateway Principal returns 401 rather than 404. Add Gateway tests that
expect:

- 43 operations in the BCN artifact and dry-run dump.
- POST and DELETE on the collection path.
- required User and App security on both methods.
- both operations to survive served-OpenAPI aggregation.
- shipped route security to resolve both concrete HTTP paths to required User
  and App identities.

Rename test functions that encode `41` in their names to `43`.

**Step 2: Run publication tests to verify failure**

```bash
uv run --project src/gateway pytest \
  src/gateway/tests/unit/scripts/test_dump_and_publish_script.py \
  src/gateway/tests/unit/scripts/test_gate_and_publish.py \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py \
  src/gateway/tests/unit/core/authn/test_route_security.py -q
```

Expected: FAIL because the checked-in Gateway snapshot still has 41
operations and lacks the collection path.

**Step 3: Regenerate the Gateway-owned artifact**

Run from the repository root:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  src/gateway/configs/schemas/bcn.openapi.json
```

Expected: the deterministic JSON artifact is updated and contains 43
operations.

**Step 4: Run the mount and Gateway publication tests**

```bash
cd src/bcs
cargo test -p bcs --test openapi_v1_mount
cd ../..
uv run --project src/gateway pytest \
  src/gateway/tests/unit/scripts/test_dump_and_publish_script.py \
  src/gateway/tests/unit/scripts/test_gate_and_publish.py \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py \
  src/gateway/tests/unit/core/authn/test_route_security.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/bcs/crates/bootstrap/bcs/tests/openapi_v1_mount.rs \
  src/gateway/configs/schemas/bcn.openapi.json \
  src/gateway/tests/unit/scripts/test_dump_and_publish_script.py \
  src/gateway/tests/unit/scripts/test_gate_and_publish.py \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py \
  src/gateway/tests/unit/core/authn/test_route_security.py
git commit -m "build(gateway): publish session collection OpenAPI"
```

### Task 6: Run focused regression and architecture verification

**Files:**

- Test only; modify production or test files only if a failure identifies a
  defect in the scoped implementation.

**Step 1: Run the full BCS OpenAPI contract suite**

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
```

Expected: PASS.

**Step 2: Run the affected Rust packages**

```bash
cd src/bcs
cargo test -p bcs-service-api
cargo test -p bcs-app-session
cargo test -p bcs-api-http
```

Expected: PASS.

**Step 3: Prove legacy compatibility**

```bash
cd src/bcs
cargo test -p bcs-http --test session_collection_contract
```

Expected: PASS without changing the legacy route contract.

**Step 4: Revalidate the published artifact**

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --project src/gateway pytest \
  src/gateway/tests/unit/scripts/test_dump_and_publish_script.py \
  src/gateway/tests/unit/scripts/test_gate_and_publish.py \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py \
  src/gateway/tests/unit/core/authn/test_route_security.py -q
```

Expected: `43 operations validated`; all tests PASS.

**Step 5: Inspect the final change boundary**

```bash
git diff --check origin/dev...HEAD
git status --short
```

Expected: no whitespace errors; only scoped files are changed. Existing
unrelated untracked files may remain and must not be staged.

**Step 6: Commit any test-only correction if required**

If verification required a scoped correction, commit only that correction:

```bash
git add <scoped-files>
git commit -m "test(bcs): verify OpenAPI session collection"
```

If all verification passed without further edits, do not create an empty
commit.
