# V1 and Legacy Session Launch Parity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Route legacy and OpenAPI V1 Session creation through one transport-neutral application workflow while preserving the legacy contract and giving V1 every legacy launch capability except reactivation.

**Architecture:** Add a `SessionLaunchService` contract to `bcs-service-api` and implement it in `bcs-session` above the existing `SessionManagementService`. Each HTTP adapter authenticates and normalizes its caller, maps its native DTO into the shared command, then projects the shared outcome into legacy JSON or a V1 Envelope. Bootstrap injects the same service into both adapter states; persistence, StateMachine runtime, and SystemMessage services remain separate dependencies.

**Tech Stack:** Rust 2024, Axum, async-trait, Serde/serde_json, BCS service ports, pytest OpenAPI contract tests, Cargo integration tests.

---

## Constraints to preserve throughout

- Do not rename, remove, or narrow any legacy request field.
- Do not change legacy create/reactivate status codes or JSON response aliases.
- Do not expose `session_id` or a reactivation URL in V1.
- V1 request objects remain strict (`additionalProperties: false`), except the
  explicitly open input and metadata objects.
- V1 output always uses the standard success/error Envelope.
- Adapters may normalize authenticated identity and protocol fields, but all
  database-backed authorization belongs to `SessionLaunchService`.
- Application input and metadata are passed as raw `serde_json::Value`; no
  `query`, callback, channel, or source remapping is allowed.
- Follow RED-GREEN-REFACTOR for every production change below.

### Task 1: Define the neutral Session launch application contract

**Files:**

- Create: `src/bcs/crates/service-api/bcs-service-api/src/application/session_launch.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/lib.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/tests/session_launch_contract.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md`

**Step 1: Write the failing object-safety and data-shape test**

Create `session_launch_contract.rs` with compile-time assertions that:

```rust
use std::sync::Arc;

use bcs_service_api::{
    CreateSessionLaunch, ReactivateSessionLaunch, SessionCaller,
    SessionLaunchRequest, SessionLaunchService,
};

fn accepts_object_safe_service(_: Arc<dyn SessionLaunchService>) {}

#[test]
fn neutral_command_carries_no_transport_identity() {
    let request = SessionLaunchRequest {
        caller: SessionCaller::Human {
            actor_id: "human_alice".into(),
            owner_id: "alice".into(),
            display_name: Some("Alice".into()),
        },
        group_id: "group-1".into(),
        requested_creator: Some("bot-owned".into()),
        title: Some("task".into()),
        kind: None,
        input: Some(serde_json::json!({"query": "hello", "custom": 1})),
        meta: Some(serde_json::json!({"channel": {"source": "ding"}})),
        public_creator_role: None,
        context_delivery: None,
    };

    let create = CreateSessionLaunch { request: request.clone() };
    let reactivate = ReactivateSessionLaunch {
        session_id: "session-1".into(),
        request,
    };

    assert_eq!(create.request.caller.actor_id(), "human_alice");
    assert_eq!(reactivate.session_id, "session-1");
}
```

Also assert that a Bot caller exposes its own actor ID and has no Human owner
key or display name.

**Step 2: Run the test and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api --test session_launch_contract
```

Expected: compile failure because the neutral Session launch types and trait do
not exist.

**Step 3: Add the minimal transport-neutral contract**

Define:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionCaller {
    Human {
        actor_id: String,
        owner_id: String,
        display_name: Option<String>,
    },
    Bot {
        bot_uuid: String,
    },
}

#[derive(Debug, Clone)]
pub struct SessionLaunchRequest {
    pub caller: SessionCaller,
    pub group_id: String,
    pub requested_creator: Option<String>,
    pub title: Option<String>,
    pub kind: Option<SessionKind>,
    pub input: Option<Value>,
    pub meta: Option<Value>,
    pub public_creator_role: Option<ParticipantRole>,
    pub context_delivery: Option<DeliveryType>,
}

#[derive(Debug, Clone)]
pub struct CreateSessionLaunch {
    pub request: SessionLaunchRequest,
}

#[derive(Debug, Clone)]
pub struct ReactivateSessionLaunch {
    pub session_id: String,
    pub request: SessionLaunchRequest,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionLaunchOutcome {
    pub session: Session,
    pub state_machine_run: Option<StateMachineRunView>,
}
```

Define explicit `SessionLaunchError` variants for Group not found, Session not
found, forbidden, invalid role/request, conflict/callback pending, runtime
failure, and internal service failure. Do not put status codes or V1 error
codes in this enum.

Define object-safe async methods:

```rust
#[async_trait]
pub trait SessionLaunchService: Send + Sync {
    async fn create(
        &self,
        command: CreateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError>;

    async fn reactivate(
        &self,
        command: ReactivateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError>;
}
```

Export the module through the application and crate roots. Update `CONTEXT.md`
to state that the contract is transport-neutral and that adapters own
credential extraction and response projection.

**Step 4: Run the test and verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api --test session_launch_contract
cargo check -p bcs-service-api --all-targets
```

Expected: both commands pass.

**Step 5: Commit**

```bash
git add src/bcs/crates/service-api/bcs-service-api
git commit -m "feat(bcs): define shared session launch contract"
```

### Task 2: Implement shared identity, authorization, and participant policy

**Files:**

- Create: `src/bcs/crates/services/bcs-session/src/launch.rs`
- Modify: `src/bcs/crates/services/bcs-session/src/lib.rs`
- Modify: `src/bcs/crates/services/bcs-session/Cargo.toml`
- Create: `src/bcs/crates/services/bcs-session/tests/session_launch.rs`
- Modify: `src/bcs/crates/services/bcs-session/CONTEXT.md`

**Step 1: Write failing shared-service authorization tests**

Build the test fixture from the real memory Group, Bot, and Session stores plus
the real `SessionManagementServiceImpl`. Use small recording fakes only for
`CollaborationRuntimeService` and `SystemMessageService` so the tests observe
external side effects without testing mock call choreography.

Add one focused test for each behavior:

```text
human_creates_as_self_when_creator_is_omitted
human_creates_as_owned_bot
human_cannot_create_as_unowned_bot
bot_creates_only_as_itself
private_group_requires_caller_access
private_group_requires_creator_access
public_non_member_is_added_with_requested_role
public_non_member_defaults_to_consultant
public_non_member_cannot_be_driver
explicit_private_human_creator_is_added_as_driver
inferred_private_human_creator_is_not_auto_added
state_machine_human_is_added_as_present_observer
```

For persistence-backed authorization, inject a registry whose `try_get` or
`try_list_bots_by_creator` can fail and assert the failure becomes
`SessionLaunchError::Internal`, not a false forbidden/missing result.

**Step 2: Run the tests and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch
```

Expected: compile failure because `SessionLaunchApplication` does not exist.

**Step 3: Implement the minimal authorization workflow**

Add:

```rust
pub struct SessionLaunchApplication {
    registry: Arc<dyn BotRegistryCoreService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    runtime: Arc<dyn CollaborationRuntimeService>,
    system_message: Arc<dyn SystemMessageService>,
}
```

Implement fallible helpers in this order:

1. `load_group` uses `GroupCoreService::try_get`.
2. `caller_has_access` checks direct participation or Human-owned participant
   Bots using fallible registry methods.
3. `resolve_creator` defaults to the caller, rejects Bot impersonation, and
   verifies Human Bot ownership through persisted `RegisteredBot.created_by`.
4. `creator_has_access` repeats the private-Group resource check for the
   effective creator.
5. `resolve_kind` uses the legacy default: StateMachine -> service invocation,
   otherwise Chat.
6. `build_participants` clones Group participants, fills missing modes, applies
   public non-member role/default restrictions, adds the StateMachine Human
   Observer, and preserves explicit/inferred Human creator behavior.

Use the presence of `requested_creator` to retain the explicit Human creator
distinction; do not add another protocol flag.

For public Human callers, call `ensure_human_actor(owner_id, display_name)` and
propagate a failed write.

Do not start runtime or SystemMessage behavior in this step; create the Session
through `SessionManagementService::create_or_reactivate` and return an outcome
with `state_machine_run: None`.

**Step 4: Run the tests and verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch
cargo test -p bcs-session
```

Expected: authorization and participant tests pass; existing Session
management tests remain green.

**Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-session
git commit -m "feat(bcs): centralize session launch authorization"
```

### Task 3: Add the complete launch-mode and side-effect matrix

**Files:**

- Modify: `src/bcs/crates/services/bcs-session/src/launch.rs`
- Modify: `src/bcs/crates/services/bcs-session/tests/session_launch.rs`

**Step 1: Write failing input and startup tests**

Add parameterized or table-driven tests for:

```text
Chat + chat                         -> SessionContext
Chat + service_invocation           -> SessionContext
ManagerWorker + chat                -> SessionContext
ManagerWorker + service_invocation  -> SessionContext
StateMachine + chat                 -> SessionContext
StateMachine + service_invocation   -> StateMachine run, no SessionContext
```

Assert that:

- string input persists and reaches SessionContext unchanged;
- object input persists with every property and reaches the runtime unchanged;
- omitted input remains `None` and runtime receives JSON null only when a
  StateMachine service invocation requires a concrete runtime value;
- metadata is persisted byte-for-value, including `callback_target`, `channel`,
  caller-provided `channel.source`, and unknown keys;
- `context_delivery` is forwarded to `SystemMessageEvent::SessionContext` for
  both kinds;
- SessionContext is scheduled only for a newly created non-StateMachine-run
  Session;
- the StateMachine command carries Session ID, caller ID, authenticated Human
  information, and the raw input;
- a runtime start error is returned after Session persistence without deleting
  the Session.

Add reactivation tests proving:

- a completed Session is reactivated;
- supplied input replaces the entire previous input;
- omitted input preserves the existing input;
- new metadata/title/kind do not overwrite existing reactivation state;
- a running Session remains a conflict;
- wrong Group/Session association is rejected;
- reactivation can start the same runtime branch as legacy;
- no SessionContext is emitted merely because a reactivation returns running.

**Step 2: Run and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch launch_matrix
cargo test -p bcs-session --test session_launch reactivate
```

Expected: failures because runtime/SystemMessage orchestration and explicit
reactivation are not implemented.

**Step 3: Implement startup and reactivation**

Extract one private `launch_prepared` helper used by public `create` and
`reactivate` methods. It must:

- call `create_or_reactivate` with `session_id: None` for create and
  `Some(session_id)` for reactivation;
- verify `belongs_to_group` before reactivation;
- invoke `start_state_machine_run` only for StateMachine service invocation;
- pass `session.input.clone().unwrap_or(Value::Null)` into the runtime;
- return `run.view` in `SessionLaunchOutcome`;
- otherwise enqueue the existing SessionContext event with the resolved topic,
  raw input, complete participant roster, and delivery override;
- retain legacy best-effort asynchronous SessionContext delivery without
  reporting delivery failure as Session creation failure.

Do not put this orchestration into `SessionManagementServiceImpl`.

**Step 4: Run and verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch
cargo test -p bcs-session
```

Expected: all new matrix/reactivation tests and existing tests pass.

**Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-session/src/launch.rs \
  src/bcs/crates/services/bcs-session/tests/session_launch.rs
git commit -m "feat(bcs): share session collaboration startup"
```

### Task 4: Specify the expanded V1 create-session contract

**Files:**

- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi/sessions.yaml`
- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`
- Modify generated artifact: `src/gateway/configs/schemas/bcn.openapi.json`

**Step 1: Write failing OpenAPI assertions**

Add tests that the V1 create operation:

- remains only POST on the existing Group Sessions path;
- has no V1 request field or path containing reactivation or `session_id`;
- declares optional User/App/Bot security plus
  `x-bcn-identity-policy: human_or_owned_bot`;
- accepts exactly `title`, `kind`, `acting_bot_id`, `creator_role`, `input`,
  `meta`, and `context_delivery`;
- defines `kind` enum `chat | service_invocation`;
- defines `creator_role` enum `consultant | manager | worker | observer`;
- defines `context_delivery` enum `send | inject`;
- defines `input` as `oneOf` string or open object with optional string
  `query`;
- defines metadata known properties with `additionalProperties: true`;
- documents callback and channel legacy field names without a `payload` or
  `extensions` property;
- returns strict `CreatedSessionEnvelope` data containing resolved kind, raw
  input/meta, participants, and optional StateMachine run fields.

Add assertions that the request object stays `additionalProperties: false`.

**Step 2: Run and verify RED**

Run:

```bash
cd src/bcs
uv run pytest -q tests/openapi/test_session_v1_contract.py
```

Expected: the new create-session assertions fail against the current
title/query-only contract.

**Step 3: Update authoritative YAML**

In `sessions.yaml`, add the seven V1-native request fields and the identity
policy. Keep the operation ID and URL unchanged.

In `domain-models.yaml`:

- replace object-only `SessionInput` with the approved string/object `oneOf`;
- add `SessionKind` and `ContextDelivery` enums if not already reusable;
- add explicit `SessionMetadata`, `CallbackTargetMetadata`, and
  `ChannelMetadata` objects;
- keep `additionalProperties: true` on `SessionMetadata` and nested metadata
  objects so unknown legacy-compatible metadata round-trips;
- type `context_projection` as `group | direct_bot` and `session_scope` as the
  current channel values;
- extend `SessionDetail` with `kind`, `meta`, `state_machine_run_id`, and an
  explicitly modeled optional StateMachine run view.

Do not add V1 `payload`, `extensions`, or reactivation schemas.

**Step 4: Validate and regenerate the Gateway artifact**

Run:

```bash
uv run python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run python src/bcs/scripts/dump_openapi.py \
  --root src/bcs/api-contracts/v1 \
  --output src/gateway/configs/schemas/bcn.openapi.json
```

If `dump_openapi.py --help` shows a different output flag on this branch, use
the documented flag from `src/bcs/api-contracts/README.md`; do not hand-edit
the generated JSON.

Run the contract test again:

```bash
cd src/bcs
uv run pytest -q tests/openapi/test_session_v1_contract.py
```

Expected: validation and all tests pass.

**Step 5: Commit**

```bash
git add src/bcs/tests/openapi/test_session_v1_contract.py \
  src/bcs/api-contracts/v1/openapi/sessions.yaml \
  src/bcs/api-contracts/v1/domain-models.yaml \
  src/gateway/configs/schemas/bcn.openapi.json
git commit -m "feat(bcs): specify V1 session launch parity"
```

### Task 5: Map V1 Principal and DTOs into the shared command

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs`

**Step 1: Write failing DTO and route tests**

In `session_routes.rs`, add a recording `SessionLaunchService` and tests for:

- Human Principal -> neutral Human with actor ID, owner ID, and display name;
- Bot Principal -> neutral Bot;
- mismatched combined Human/Bot claims -> V1 forbidden Envelope;
- Human plus `acting_bot_id` maps the requested creator but does not authorize
  ownership in the adapter;
- Bot plus a different `acting_bot_id` reaches the shared service and is
  rejected there;
- string/object input and metadata reach the command unchanged;
- array, number, boolean, null, unknown request fields, and `session_id` are
  rejected with `invalid_request` before service invocation;
- the route always calls `create`, never `reactivate`;
- success is HTTP 201 / code 20100 and includes the shared outcome in a V1
  Envelope;
- shared errors map to stable V1 codes/statuses.

Add a direct DTO round-trip test for `{"query":"x","custom":{"n":1}}`.

**Step 2: Run and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-api-http --test session_routes create_session
```

Expected: failures because ApiState has no shared launch service and the DTO
supports only title/query.

**Step 3: Implement V1 boundary translation**

- Add `session_launch: Arc<dyn SessionLaunchService>` to `ApiState` and make it
  a required constructor argument.
- In the route, call `select_principal(..., IdentityPolicy::HumanOrOwnedBot)`
  and convert the selected Principal into `SessionCaller`.
- Deserialize V1 input with an untagged enum containing only `String` and
  `Map<String, Value>` variants, then convert directly to `Value`.
- Keep `meta` as `Option<Map<String, Value>>` at the V1 boundary and convert it
  directly to `Value::Object`.
- Map `acting_bot_id`, `creator_role`, `kind`, and `context_delivery` directly
  into `SessionLaunchRequest`.
- Call only `state.session_launch.create`.
- Project domain Session fields into V1 `SessionDetail`, using raw input/meta
  and optional run view from the outcome.

Remove the old V1 `SessionServiceImpl::create` workflow and its synthetic
Group-context/query fallback. Remove `create` from the V1-only SessionService
trait if it is no longer called; migrate its create-specific tests into the
shared service and route suites rather than leaving a second implementation.

**Step 4: Run and verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-api-http --test session_routes create_session
cargo test -p bcs-app-session --test v1_session_service
cargo test -p bcs-api-http
```

Expected: new V1 route tests pass and unrelated V1 Session operations remain
green.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http \
  src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs \
  src/bcs/crates/application/v1/bcs-app-session
git commit -m "feat(bcs): route V1 session creation through shared launch"
```

### Task 6: Preserve the legacy protocol while delegating its workflow

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/tests/session_create_contract.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/tests/session_create_request_dto.rs`

**Step 1: Expand the legacy regression suite before slimming the handler**

Keep all existing tests and add coverage for:

- Bot token and Human cookie map to the correct neutral caller;
- every existing request field maps unchanged;
- arbitrary string/object/array/number/boolean input remains accepted;
- arbitrary metadata remains accepted, including scalar metadata if currently
  accepted by Serde;
- caller-provided `channel.source` is unchanged and never inferred;
- omitted `session_kind` retains StateMachine defaulting;
- `session_id` still selects reactivation;
- create returns 201, reactivation returns 200;
- responses retain `id`, `session_id`, and legacy StateMachine run fields;
- unauthorized, forbidden, invalid-role, not-found, conflict, callback-pending,
  and runtime errors retain the legacy status and JSON shape.

Use the real `SessionLaunchApplication` in route fixtures so the tests cover
the adapter-to-application boundary.

**Step 2: Run the expanded suite before the refactor**

Run:

```bash
cd src/bcs
cargo test -p bcs-http --test session_create_request_dto
cargo test -p bcs-http --test session_create_contract
```

Expected: existing assertions pass. Newly added delegation-specific assertions
fail because the handler still owns the workflow.

**Step 3: Replace handler-owned policy with mapping and projection**

Keep `CreateSessionRequest` exactly compatible. In
`create_session_for_group`:

1. Call the existing `resolve_group_chat_caller`.
2. Convert `GroupChatCaller` to neutral `SessionCaller` without database
   authorization.
3. Map legacy fields:

```text
session_title            -> title
session_kind             -> kind
created_by               -> requested_creator
caller_role              -> public_creator_role
group_context_delivery   -> context_delivery
input                    -> input
meta                     -> meta
```

4. If `session_id` is absent, call `SessionLaunchService::create`; otherwise
   call `reactivate`.
5. Preserve the existing `session_to_json_with_state_machine_run` projection,
   201/200 selection, and legacy error JSON.

Delete only helper code made dead by this extraction. Keep authorization
helpers still used by other legacy Session routes.

**Step 4: Run and verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-http --test session_create_request_dto
cargo test -p bcs-http --test session_create_contract
cargo test -p bcs-http
```

Expected: all legacy adapter tests pass without snapshot or schema changes.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs \
  src/bcs/crates/adapters/http/bcs-http/tests/session_create_contract.rs \
  src/bcs/crates/adapters/http/bcs-http/tests/session_create_request_dto.rs
git commit -m "refactor(bcs): delegate legacy session launch"
```

### Task 7: Wire one shared service into both adapters

**Files:**

- Modify: `src/bcs/crates/service-api/bcs-services-container/src/services.rs`
- Modify: `src/bcs/crates/service-api/bcs-services-container/src/test_support.rs`
- Modify: `src/bcs/crates/service-api/bcs-services-container/tests/builder_fail_fast.rs`
- Modify: `src/bcs/crates/test-support/bcs-test-support/src/lib.rs` or the
  existing no-op service module
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/CONTEXT.md`
- Modify constructor call sites found by `rg 'ApiState::new\(' src/bcs`

**Step 1: Write failing composition tests**

Add builder tests proving `session_launch` is required and the test-support
builder supplies a no-op implementation. Add a bootstrap/state assertion that
the V1 ApiState and legacy Services container receive clones of the same
`Arc<dyn SessionLaunchService>`.

**Step 2: Run and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-services-container --test builder_fail_fast
cargo test -p bcs --lib session_launch
```

Expected: failures because the builder and bootstrap do not expose the service.

**Step 3: Add fail-fast composition wiring**

- Add `session_launch` to `Services` and `ServicesBuilder` as a required
  service.
- Add `NoopSessionLaunchService` only under test support.
- Construct `Arc<SessionLaunchApplication>` in every production/bootstrap
  composition path after registry, Group, SessionManagement, runtime, and
  SystemMessage dependencies exist.
- Pass the same Arc to `ServicesBuilder::session_launch` and `ApiState::new`.
- Update all ApiState test constructors with a recording or no-op launch
  service, according to the test.
- Update context-boundary documentation for the new dependency.

Do not instantiate the concrete application inside either HTTP adapter.

**Step 4: Run and verify GREEN**

Run:

```bash
cd src/bcs
cargo test -p bcs-services-container
cargo test -p bcs-api-http
cargo test -p bcs-http --test session_create_contract
cargo check -p bcs --all-targets
```

Expected: all composition, adapter, and bootstrap targets pass.

**Step 5: Commit**

```bash
git add src/bcs/crates/service-api/bcs-services-container \
  src/bcs/crates/test-support/bcs-test-support \
  src/bcs/crates/bootstrap/bcs \
  src/bcs/crates/adapters/http/bcs-api-http/tests
git commit -m "feat(bcs): wire shared session launch service"
```

### Task 8: Prove parity and finish boundary documentation

**Files:**

- Modify if needed: `src/bcs/crates/adapters/http/bcs-api-http/CONTEXT.md`
- Modify if needed: `src/bcs/crates/adapters/http/bcs-http/CONTEXT.md`
- Modify if needed: `src/bcs/crates/services/bcs-session/CONTEXT.md`
- Modify if needed: `src/bcs/crates/service-api/bcs-service-api/CONTEXT.md`
- Modify if needed: `src/bcs/crates/bootstrap/bcs/CONTEXT.md`

**Step 1: Run formatting and diff hygiene**

Run:

```bash
cd src/bcs
cargo fmt --all -- --check
cd ../..
git diff --check
```

If formatting fails, run `cargo fmt --all`, inspect that it changes only files
in scope, then rerun both checks.

**Step 2: Run focused parity verification**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api --test session_launch_contract
cargo test -p bcs-session --test session_launch
cargo test -p bcs-app-session --test v1_session_service
cargo test -p bcs-http --test session_create_request_dto
cargo test -p bcs-http --test session_create_contract
cargo test -p bcs-api-http --test session_routes
uv run pytest -q tests/openapi/test_session_v1_contract.py
```

Expected: every command passes. Record exact counts in the final handoff.

**Step 3: Run affected crate and architecture gates**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api
cargo test -p bcs-session
cargo test -p bcs-app-session
cargo test -p bcs-services-container
cargo test -p bcs-http
cargo test -p bcs-api-http
cargo check -p bcs --all-targets
uv run python scripts/validate_openapi_contract.py --root api-contracts/v1
```

Run the repository architecture gate named in `docs/arch/ci.enforce.md` for
changed BCS boundaries. If the documented command differs from an old plan,
use the current architecture document as authority.

**Step 4: Review the final diff against the three user constraints**

Check explicitly:

```text
[ ] legacy DTO fields unchanged
[ ] legacy reactivation still mounted and tested
[ ] legacy input/meta admission unchanged
[ ] V1 has no reactivation input or route
[ ] V1 supports Human and Bot caller creation
[ ] V1 supports both kinds and all Group strategies
[ ] V1 input/meta/context delivery round-trip
[ ] V1 uses strict DTOs and Envelope
[ ] both adapters call the same SessionLaunchService
[ ] no adapter performs database-backed creator authorization
```

**Step 5: Commit final documentation or cleanup**

```bash
git add src/bcs/crates/*/*/CONTEXT.md src/bcs/crates/bootstrap/bcs/CONTEXT.md
git commit -m "docs(bcs): document shared session launch boundary"
```

Skip this commit if no documentation changes remain after prior tasks.

## Final validation result format

Report:

- legacy request/response compatibility status;
- V1 feature matrix and explicit lack of reactivation;
- shared application boundary and where identity is normalized;
- exact test commands and pass counts;
- any validation not run and the reason;
- branch/worktree path and commit list.
