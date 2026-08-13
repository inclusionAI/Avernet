# OpenAPI V1 Session History Legacy Message Compatibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `GET /openapi/v1/collaboration/sessions/{session_id}/messages` return the exact legacy `GroupMessage[]` item contract inside the standard V1 success Envelope, without changing the existing history service, repository port, storage, or database behavior.

**Architecture:** Keep V1 authentication and View Actor authorization in `bcs-app-session`, then delegate Chat/ManagerWorker history to the existing `GroupMessageHistoryService` and StateMachine history to the existing `CollaborationRuntimeService`. Remove the V1 facade's direct `bcs-message`/`MessageRepoPort` history dependency and all per-message projection/cursor logic. The legacy adapter remains unchanged; only the V1 adapter wraps the returned `Vec<GroupMessage>` as `Envelope<Vec<GroupMessage>>`.

**Tech Stack:** Rust 2024 workspace, Tokio, Axum, Serde, async-trait, YAML OpenAPI 3.1, Python/pytest contract tests, deterministic Gateway OpenAPI JSON export.

---

## Guardrails

- Read and follow `docs/arch/arch.rules.md`, `docs/arch/ci.enforce.md`, `src/bcs/AGENTS.md`, and `src/bcs/CLAUDE.md` before implementation.
- Do not modify `bcs-message`, `bcs-message-flow`, `MessageRepoPort`, `bcs-message-store`, Bot `chat.history`, or any database schema/migration.
- Do not change the legacy `GET /sessions/{session_id}/messages` production route or its bare-array response.
- Do not add `next_cursor`, `has_more`, or another message page object.
- Do not run workspace-wide `cargo fmt`; format only touched Rust files with `rustfmt` or `cargo fmt -p <package>` after checking that it is scoped.
- Preserve unrelated untracked files and user changes.
- Use the approved design as the behavioral source of truth: `docs/plans/2026-08-13-openapi-v1-session-history-legacy-message-compatibility-design.md`.

## Target Contract

```text
Legacy success: GroupMessage[]
V1 success:     Envelope<GroupMessage[]>
```

For the same authorized view, the compatibility invariant is:

```rust
assert_eq!(v1_response["data"], legacy_response);
```

The V1 request accepts `before: integer<int64>` in milliseconds, `limit`, and `view_bot_id`. V1-specific Principal and View Actor authorization remains in the V1 application facade.

### Task 1: Replace the V1 message application contract with `Vec<GroupMessage>`

**Files:**

- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/message.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/v1_session_application_contracts.rs`

**Step 1: Write the failing application-contract test**

In `v1_session_application_contracts.rs`, replace `session_message_uses_id_not_message_id` with a test that serializes a rich domain `GroupMessage` and proves the legacy field names and omission rules:

```rust
#[test]
fn session_message_service_uses_legacy_group_message_wire_shape() {
    let message = GroupMessage {
        id: "m1".into(),
        timestamp: 99,
        sender: "bot-1".into(),
        content: "hello".into(),
        message_type: GroupMessageType::Bot,
        bot_name: Some("Worker".into()),
        role: MessageRole::Assistant,
        run_id: "run-1".into(),
        history_meta: Some(serde_json::json!({"assistantAggregation": true})),
        metadata: Some(serde_json::json!({"tool": "search"})),
        attachments: Some(vec![MessageAttachment {
            attachment_id: "att-1".into(),
            attachment_type: AttachmentType::Image,
            file_name: "result.png".into(),
            mime_type: Some("image/png".into()),
            size: Some(42),
            sha256: Some("abcd".into()),
            url: Some("https://download.example/result.png".into()),
            expires_at: Some(123),
        }]),
    };

    let json = serde_json::to_value(&message).expect("serialize GroupMessage");
    assert_eq!(json["id"], "m1");
    assert_eq!(json["timestamp"], 99);
    assert_eq!(json["sender"], "bot-1");
    assert_eq!(json["message_type"], "bot");
    assert_eq!(json["role"], "assistant");
    assert_eq!(json["historyMeta"]["assistantAggregation"], true);
    assert!(json.get("session_seq").is_none());
    assert!(json.get("sender_id").is_none());
    assert!(json.get("created_at").is_none());
}
```

Also change `NoopSessionMessageService::list` to the intended signature and return `Ok(Vec::new())`. Set the command fixture's `before` to `Some(123)` and assert it is the same integer.

**Step 2: Run the focused test and verify it fails**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api --test v1_session_application_contracts session_message_service_uses_legacy_group_message_wire_shape
```

Expected: FAIL to compile because the V1 trait still returns `SessionMessagePage` and the old V1-only message types are still imported.

**Step 3: Make the minimal contract change**

In `application/v1/message.rs`:

- Delete `MessageSenderKind`, `SessionMessageKind`, `SessionMessage`, and `SessionMessagePage`.
- Import `GroupMessage` from the transport-independent core/domain re-export.
- Change `ListSessionMessages.before` from `Option<String>` to `Option<u64>`.
- Change `SessionMessageService::list` to:

```rust
#[async_trait]
pub trait SessionMessageService: Send + Sync {
    async fn list(
        &self,
        query: ListSessionMessages,
    ) -> Result<Vec<GroupMessage>, ApplicationError>;
}
```

Keep the command transport-neutral and document `before` as an exclusive millisecond timestamp bound understood by the existing history service.

**Step 4: Run the service-api tests**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api --test v1_session_application_contracts
```

Expected: PASS.

**Step 5: Commit the contract checkpoint**

```bash
git add src/bcs/crates/service-api/bcs-service-api/src/application/v1/message.rs \
  src/bcs/crates/service-api/bcs-service-api/tests/v1_session_application_contracts.rs
git commit -m "refactor(bcs): use GroupMessage for V1 session history"
```

### Task 2: Delegate V1 history to the existing application services

**Files:**

- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/Cargo.toml`

**Step 1: Replace direct-repository history fixtures with recording service fakes**

In `v1_session_service.rs`, add a `RecordingHistoryService` implementing `GroupMessageHistoryService`. It should return a configured `SessionHistoryResult` and record the complete `SessionHistoryCommand`:

```rust
#[derive(Default)]
struct RecordingHistoryService {
    session_calls: std::sync::Mutex<Vec<SessionHistoryCommand>>,
    messages: std::sync::Mutex<Vec<GroupMessage>>,
}

#[async_trait]
impl GroupMessageHistoryService for RecordingHistoryService {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        panic!("group history is not used by SessionServiceImpl")
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        let messages = self.messages.lock().expect("messages lock").clone();
        self.session_calls.lock().expect("history lock").push(cmd.clone());
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages,
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}
```

Update `Fixture` to inject:

```rust
history: Arc<RecordingHistoryService>,
runtime: Arc<dyn CollaborationRuntimeService>,
```

Use `NoopCollaborationRuntimeService` for ordinary tests. Remove `MemoryMessageRepo` from the fixture and stop seeding persisted messages for V1 facade history tests.

**Step 2: Write the failing rich-message passthrough/delegation test**

Replace `list_messages_returns_descending_with_cursor`, `list_messages_composite_cursor_no_skip_tied_created_at`, and `list_messages_rejects_malformed_before_cursor` with a single responsibility-focused test:

```rust
#[tokio::test]
async fn list_messages_delegates_and_returns_legacy_group_messages_unchanged() {
    let fixture = Fixture::new().await;
    // Create/store an authorized Chat session with human_staff-1 as Participant.
    let expected = rich_group_message();
    *fixture.history.messages.lock().expect("messages lock") = vec![expected.clone()];

    let messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            before: Some(1_786_590_000_000),
            limit: 25,
            view_bot_id: None,
        },
    )
    .await
    .expect("list messages");

    assert_eq!(serde_json::to_value(&messages).unwrap(), serde_json::to_value([expected]).unwrap());
    let calls = fixture.history.session_calls.lock().expect("history lock");
    let call = calls.last().expect("history call");
    assert_eq!(call.group_id, "g1");
    assert_eq!(call.session_id, session_id);
    assert_eq!(call.view_bot_id.as_deref(), Some("human_staff-1"));
    assert_eq!(call.limit, 25);
    assert_eq!(call.before, Some(1_786_590_000_000));
    assert_eq!(
        call.caller,
        CallerContext::Human(HumanActor {
            actor_id: "human_staff-1".into(),
            staff_no: "staff-1".into(),
        })
    );
}
```

The `rich_group_message()` helper must populate `bot_name`, `role`, `run_id`, `historyMeta`, `metadata`, and one attachment so the test fails if any projection is reintroduced.

**Step 3: Preserve View Actor authorization tests at the facade boundary**

Rewrite the existing message-view tests so they assert authorization and the recorded command instead of repo-filtered `session_seq` values:

- omitted view resolves to `human_<user-id>` and requires that Human Actor to be a Session Participant;
- explicit self-Human view is allowed;
- explicit exact-`created_by` owned Bot view is allowed and is passed as `Some(bot_id)`;
- unowned Bot view remains `forbidden` and produces no history-service call;
- `limit == 0` and `limit > 100` remain `invalid_request` and produce no downstream call.

**Step 4: Write the failing StateMachine source-selection test**

Add a small recording runtime based on the required methods already demonstrated by `RecordingRuntime` in `bcs-app-group/tests/v1_group_service.rs`. Record `(session_id, limit, before)` in `get_state_machine_session_history` and return a rich `SessionHistoryResult`.

Test that a StateMachine group/session:

- returns the runtime's `messages` unchanged;
- invokes runtime with the timestamp `before` and limit;
- does not invoke `GroupMessageHistoryService`;
- returns `[]` when runtime returns `Ok(None)`;
- maps a runtime failure to the standard `ApplicationError` surface.

**Step 5: Run the focused tests and verify they fail**

Run:

```bash
cd src/bcs
cargo test -p bcs-app-session --test v1_session_service list_messages_
cargo test -p bcs-app-session --test v1_session_service state_machine_session_history_
```

Expected: FAIL to compile or fail assertions because `SessionServiceImpl` still reads `MessageRepoPort`, returns `SessionMessagePage`, and does not hold the injected history/runtime services.

**Step 6: Implement application delegation**

Change `SessionServiceImpl` fields and constructor to hold:

```rust
session_repo: Arc<dyn SessionRepoPort>,
history: Arc<dyn GroupMessageHistoryService>,
collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
```

Remove `message_repo`. Keep `session_repo`, because session list/count behavior still uses it.

Implement `SessionMessageService::list` as:

```rust
async fn list(
    &self,
    query: ListSessionMessages,
) -> Result<Vec<GroupMessage>, ApplicationError> {
    if query.limit == 0 || query.limit > 100 {
        return Err(ApplicationError::invalid(
            "invalid_request",
            "limit must be between 1 and 100",
        ));
    }

    let user = require_authenticated_user(&query.caller)?;
    let view_actor_id = self
        .resolve_view_actor(&query.caller, query.view_bot_id.as_deref())
        .await?;
    let session = self.load_session(&query.session_id).await?;
    if !session
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == view_actor_id)
    {
        return Err(ApplicationError::forbidden(
            "The selected View Actor is not a Session Participant",
        ));
    }
    let group = self.load_group(&session.group_id).await?;

    if group.group_strategy == GroupStrategy::StateMachine {
        return self
            .collaboration_runtime
            .get_state_machine_session_history(&query.session_id, query.limit, query.before)
            .await
            .map(|result| result.map_or_else(Vec::new, |result| result.messages))
            .map_err(map_runtime_error);
    }

    let caller = CallerContext::Human(HumanActor {
        actor_id: format!("human_{}", user.id),
        staff_no: user.id.clone(),
    });
    let result = self
        .history
        .get_session_history(SessionHistoryCommand {
            caller,
            group_id: session.group_id.clone(),
            session_id: query.session_id,
            session_participants: session.participants,
            view_bot_id: Some(view_actor_id),
            limit: query.limit,
            before: query.before,
        })
        .await
        .map_err(map_group_use_case_error)?;
    Ok(result.messages)
}
```

Adjust ownership/borrowing as needed without changing the sequence of authorization checks.

Delete:

- `NEW_PARTICIPANT_VISIBLE_LIMIT`;
- `project_message`, `project_sender_kind`, `project_message_kind`, and `project_content`;
- `encode_cursor` and `decode_cursor`;
- imports of `PersistedMessage`, `SenderType`, `MessageService`, and `MessageRepoPort`.

Add a focused `map_runtime_error` matching the standard V1 behavior:

```rust
fn map_runtime_error(error: CollaborationRuntimeError) -> ApplicationError {
    match error {
        CollaborationRuntimeError::Unauthenticated => ApplicationError::Unauthenticated,
        CollaborationRuntimeError::Forbidden(message) => ApplicationError::forbidden(message),
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        CollaborationRuntimeError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}
```

Do not move this source-selection policy into the HTTP adapter.

**Step 7: Remove the concrete production dependency**

Delete this normal dependency from `bcs-app-session/Cargo.toml`:

```toml
bcs-message = { workspace = true }
```

Remove `bcs-message-store` from dev-dependencies only if no remaining test imports it after the fixture rewrite.

**Step 8: Run all V1 Session facade tests**

Run:

```bash
cd src/bcs
cargo test -p bcs-app-session
cargo tree -p bcs-app-session -e normal | rg 'bcs-message'
```

Expected: all tests PASS; the `cargo tree` search returns no match and exits 1 because the direct/transitive normal dependency is absent.

**Step 9: Commit the facade checkpoint**

```bash
git add src/bcs/crates/application/v1/bcs-app-session/src/lib.rs \
  src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs \
  src/bcs/crates/application/v1/bcs-app-session/Cargo.toml
git commit -m "refactor(bcs): delegate V1 session history"
```

### Task 3: Wire the existing history and runtime services in the composition root

**Files:**

- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`

**Step 1: Run the bootstrap compile and capture the constructor failure**

Run:

```bash
cd src/bcs
cargo check -p bcs
```

Expected: FAIL at `SessionServiceImpl::new` because its constructor now requires `GroupMessageHistoryService` and `CollaborationRuntimeService`, not `MessageRepoPort`.

**Step 2: Update only the composition-root wiring**

Change `build_openapi_v1_state` to accept:

```rust
group_message_history: Arc<dyn GroupMessageHistoryService>,
```

Remove only its `message_repo: Arc<dyn MessageRepoPort>` parameter. Do not remove the bootstrap's other `message_repo` values; those remain necessary for the existing message stack.

Clone the runtime when constructing `GroupServiceImpl`, then construct `SessionServiceImpl` with:

```rust
let session_service = Arc::new(SessionServiceImpl::new(
    sessions.clone(),
    groups.clone(),
    registry.clone(),
    friends.clone(),
    relation,
    session_repo,
    group_message_history,
    collaboration_runtime,
    SessionServiceConfig { relation_env },
));
```

At all three `build_openapi_v1_state` call sites, pass the already-created `group_message_history.clone()` and stop passing `message_repo.clone()` in that argument position.

**Step 3: Verify all runtime composition modes compile**

Run:

```bash
cd src/bcs
cargo check -p bcs
cargo test -p bcs --test openapi_v1_mount
```

Expected: PASS for memory/test and configured runtime construction paths.

**Step 4: Commit the composition checkpoint**

```bash
git add src/bcs/crates/bootstrap/bcs/src/server.rs
git commit -m "refactor(bcs): inject session history facade dependencies"
```

### Task 4: Return a legacy-compatible message array from the V1 HTTP adapter

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`
- Verify/no behavior change expected: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`
- Modify compile-only fakes in:
  - `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs`
  - `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`
  - `src/bcs/crates/adapters/http/bcs-api-http/tests/friendship_routes.rs`
  - `src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs`

**Step 1: Rewrite the V1 route test for the approved Envelope shape**

Change `FakeSessionMessageService` to return `Vec<GroupMessage>` containing a rich message fixture. Delete `page_override`.

Replace `list_session_messages_returns_cursor_page` with:

```rust
#[tokio::test]
async fn list_session_messages_wraps_legacy_group_message_array_in_envelope() {
    // Build router with FakeSessionMessageService returning rich_group_messages().
    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1/messages?limit=50",
            Value::Null,
        ))
        .await
        .expect("list messages response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let legacy_response = serde_json::to_value(rich_group_messages()).unwrap();
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"], legacy_response);
    assert!(body["data"].is_array());
    assert!(body["data"].get("messages").is_none());
    assert!(body["data"].get("next_cursor").is_none());
    assert!(body["data"].get("has_more").is_none());
}
```

This assertion is the adapter compatibility invariant: V1 `data` is exactly the JSON array returned by legacy for the same `GroupMessage` values.

**Step 2: Change the request parsing test to integer timestamp pagination**

Rename `list_session_messages_passes_opaque_before_cursor_through` to `list_session_messages_passes_before_timestamp_through` and request:

```text
?before=1234567890&limit=10
```

Assert:

```rust
assert_eq!(listed.before, Some(1_234_567_890));
```

Add a malformed string query test (`?before=not-a-timestamp`) that expects the standard V1 `400 invalid_request` ErrorEnvelope and verifies the service was not called.

Delete `list_session_messages_surfaces_next_cursor_when_has_more`.

**Step 3: Run the focused route tests and verify failure**

Run:

```bash
cd src/bcs
cargo test -p bcs-api-http --test session_routes list_session_messages_
```

Expected: FAIL because the DTO still parses an opaque string and the fakes/tests still use `SessionMessagePage`.

**Step 4: Implement the DTO and fake changes**

Change `ListSessionMessagesQuery` to:

```rust
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListSessionMessagesQuery {
    /// Exclusive legacy-compatible millisecond timestamp bound.
    #[serde(default)]
    pub before: Option<u64>,
    #[serde(default = "default_messages_limit")]
    pub limit: u64,
    #[serde(default)]
    pub view_bot_id: Option<String>,
}
```

The existing route implementation should require no structural change: once the facade returns `Vec<GroupMessage>`, its current `Envelope::success(..., result, ...)` naturally emits `data: []`.

Update every no-op message-service implementation in the other route-test files to return `Result<Vec<GroupMessage>, ApplicationError>` and `Ok(Vec::new())`.

**Step 5: Run all V1 adapter tests**

Run:

```bash
cd src/bcs
cargo test -p bcs-api-http
```

Expected: PASS.

**Step 6: Commit the adapter checkpoint**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/friendship_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs
git commit -m "fix(bcs): expose legacy messages through V1 envelope"
```

### Task 5: Lock the legacy route regression contract

**Files:**

- Modify test only: `src/bcs/crates/adapters/http/bcs-http/tests/group_messages_contract.rs`
- Do not modify production route: `src/bcs/crates/adapters/http/bcs-http/src/routes/sessions.rs`

**Step 1: Add a rich legacy Session-history response assertion**

Extend the existing recording `GroupMessageHistoryService` fixture to return the same rich `GroupMessage` field set used by the V1 route test. In the existing `/sessions/{sid}/messages` happy-path test, assert:

```rust
let body = response_json(response).await;
let expected = serde_json::to_value(rich_group_messages()).unwrap();
assert_eq!(body, expected);
assert!(body.is_array());
assert_eq!(body[0]["historyMeta"]["assistantAggregation"], true);
assert_eq!(body[0]["attachments"][0]["type"], "image");
```

Also assert the legacy route still has no `code`, `message`, `data`, or `request_id` wrapper.

**Step 2: Run the legacy regression test**

Run:

```bash
cd src/bcs
cargo test -p bcs-http --test group_messages_contract
```

Expected: PASS without changing the production legacy route. If it fails, fix only the test fixture/expectation unless the failure proves an existing regression unrelated to this change; do not reshape legacy production output.

**Step 3: Commit the regression test**

```bash
git add src/bcs/crates/adapters/http/bcs-http/tests/group_messages_contract.rs
git commit -m "test(bcs): lock legacy session message wire shape"
```

### Task 6: Replace the OpenAPI V1 message-page schema with explicit `GroupMessage`

**Files:**

- Modify: `src/bcs/api-contracts/v1/openapi/sessions.yaml`
- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`

**Step 1: Write failing OpenAPI contract tests**

Add tests to `test_session_v1_contract.py` that resolve the session-message `200` schema and assert:

```python
def test_session_history_uses_legacy_group_message_array_envelope() -> None:
    contract = load_contract(CONTRACT_ROOT)
    operation = contract["paths"][
        "/openapi/v1/collaboration/sessions/{session_id}/messages"
    ]["get"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert response["additionalProperties"] is False
    data = response["properties"]["data"]
    assert data["type"] == "array"
    item = data["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == [
        "id", "timestamp", "sender", "content", "message_type", "role"
    ]
    assert set(item["properties"]) == {
        "id", "timestamp", "sender", "content", "message_type", "bot_name",
        "role", "run_id", "historyMeta", "metadata", "attachments",
    }
    assert {"messages", "next_cursor", "has_more"}.isdisjoint(data)
```

Add a second test that asserts the `before` query parameter schema is:

```python
{"type": "integer", "format": "int64", "minimum": 0}
```

and that the old component names are absent from the loaded/bundled contract.

**Step 2: Run the focused tests and verify failure**

Run:

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi/test_session_v1_contract.py -q
```

Expected: FAIL because `before` is still a string and success data is still `SessionMessagePage`.

**Step 3: Define the explicit legacy-compatible schemas**

In `domain-models.yaml`, remove `SessionMessageKind`, `MessageSenderKind`, `SessionMessage`, `SessionMessagePage`, and `SessionMessagePageEnvelope` after confirming they have no other references.

Add these explicit schemas with `additionalProperties: false` on fixed-shape objects:

```yaml
MessageAttachment:
  type: object
  additionalProperties: false
  required: [attachment_id, type, file_name]
  properties:
    attachment_id:
      type: string
    type:
      type: string
      enum: [image, file]
    file_name:
      type: string
    mime_type:
      type: string
    size:
      type: integer
      format: int64
      minimum: 0
    sha256:
      type: string
    url:
      type: string
    expires_at:
      type: integer
      format: int64
      minimum: 0

GroupMessage:
  type: object
  additionalProperties: false
  required: [id, timestamp, sender, content, message_type, role]
  properties:
    id:
      type: string
    timestamp:
      type: integer
      format: int64
      minimum: 0
    sender:
      type: string
    content:
      type: string
    message_type:
      type: string
      enum: [bot, system, fusion]
    bot_name:
      type: string
    role:
      type: string
      enum: [user, tool_result, assistant, system]
    run_id:
      type: string
    historyMeta:
      type: object
      additionalProperties: true
    metadata:
      type: object
      additionalProperties: true
    attachments:
      type: array
      items:
        $ref: "#/MessageAttachment"

SessionMessagesEnvelope:
  type: object
  additionalProperties: false
  required: [code, message, data, request_id]
  properties:
    code:
      const: 20000
    message:
      type: string
    data:
      type: array
      items:
        $ref: "#/GroupMessage"
    request_id:
      type: string
```

`historyMeta` and `metadata` intentionally allow dynamic keys because their values come from the legacy normalized message contract. `additionalProperties: false` remains on `GroupMessage`, `MessageAttachment`, and the Envelope, so undocumented top-level fields are rejected by validators and generated clients.

In `openapi/sessions.yaml`:

- change the summary from cursor-based history to Session message history;
- define `before` as non-negative `integer`, `format: int64`, measured in milliseconds;
- remove all `next_cursor`/`has_more` language;
- point `200` to `SessionMessagesEnvelope`;
- retain the existing `401/403/404/500` ErrorEnvelope responses and V1 security metadata.

In `openapi.yaml`, replace the exported `SessionMessage` component with `GroupMessage` (and expose `MessageAttachment` only if external generators need the named component).

**Step 4: Validate and run all BCS OpenAPI tests**

Run:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
```

Expected: validator prints no errors and exits 0; all tests PASS.

**Step 5: Commit the OpenAPI source checkpoint**

```bash
git add src/bcs/api-contracts/v1/openapi/sessions.yaml \
  src/bcs/api-contracts/v1/domain-models.yaml \
  src/bcs/api-contracts/v1/openapi.yaml \
  src/bcs/tests/openapi/test_session_v1_contract.py
git commit -m "fix(bcs): align V1 session message schema with legacy"
```

### Task 7: Regenerate and verify the Gateway BCN OpenAPI artifact

**Files:**

- Modify generated artifact: `src/gateway/configs/schemas/bcn.openapi.json`
- Modify only if needed for a new targeted assertion: `src/gateway/tests/unit/core/forwarding/test_served_openapi.py`

**Step 1: Export the deterministic candidate**

Run:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  /tmp/bcn.openapi.json \
  --root src/bcs/api-contracts/v1
```

Expected: command prints `/tmp/bcn.openapi.json` and exits 0.

**Step 2: Inspect the generated operation before replacing the artifact**

Run:

```bash
jq '.paths["/openapi/v1/collaboration/sessions/{session_id}/messages"].get.responses["200"].content["application/json"].schema.properties.data' /tmp/bcn.openapi.json
```

Expected: an array schema whose items are the fully resolved `GroupMessage`; no `messages`, `next_cursor`, or `has_more` properties.

**Step 3: Replace the checked-in generated artifact mechanically**

Use `apply_patch` for a normal textual update when practical. Because this artifact is a one-line deterministic JSON file, a mechanical copy command is acceptable for this generated-file replacement:

```bash
cp /tmp/bcn.openapi.json src/gateway/configs/schemas/bcn.openapi.json
```

Do not hand-edit the generated JSON.

**Step 4: Add/adjust a Gateway aggregation assertion if coverage is missing**

In `test_served_openapi.py`, resolve the served collaboration message-history response and assert `data.type == "array"`, the item includes `historyMeta` and `attachments`, and pagination wrapper fields are absent. Do not change the existing operation-count assertion; this contract change adds no operation.

**Step 5: Run the Gateway schema tests**

Run:

```bash
uv run --with pytest pytest \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py \
  src/gateway/tests/unit/scripts/test_gate_and_publish.py -q
```

Expected: PASS. The compatibility gate itself may classify the approved V1 item replacement as breaking when comparing against an older published schema; do not weaken the gate. Release publication must use the repository's explicit approved-breaking workflow if that gate is run against the previous artifact.

**Step 6: Prove the generated file is reproducible**

Run the exporter again to a second temporary file and compare:

```bash
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  /tmp/bcn.openapi.verify.json \
  --root src/bcs/api-contracts/v1
cmp /tmp/bcn.openapi.verify.json src/gateway/configs/schemas/bcn.openapi.json
```

Expected: `cmp` exits 0 with no output.

**Step 7: Commit the generated artifact checkpoint**

```bash
git add src/gateway/configs/schemas/bcn.openapi.json \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py
git commit -m "build(gateway): refresh BCN session message schema"
```

If `test_served_openapi.py` did not need a change, omit it from `git add`.

### Task 8: Final dependency, compatibility, and regression verification

**Files:**

- Verify only: all files changed above
- Update only if implementation details diverged: `docs/plans/2026-08-13-openapi-v1-session-history-legacy-message-compatibility-design.md`

**Step 1: Format only touched Rust packages/files**

From `src/bcs`, run the narrowest available formatter for the touched packages/files. Do not run workspace-wide formatting. Then inspect `git diff --stat` to ensure no unrelated format noise was introduced.

**Step 2: Run focused Rust verification**

Run:

```bash
cd src/bcs
cargo test -p bcs-service-api --test v1_session_application_contracts
cargo test -p bcs-app-session
cargo test -p bcs-api-http
cargo test -p bcs-http --test group_messages_contract
cargo test -p bcs --test openapi_v1_mount
cargo check -p bcs
```

Expected: all commands PASS.

**Step 3: Run contract and Gateway verification**

Run from repository root:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
uv run --with pytest pytest \
  src/gateway/tests/unit/core/forwarding/test_served_openapi.py \
  src/gateway/tests/unit/scripts/test_gate_and_publish.py -q
```

Expected: all commands PASS.

**Step 4: Audit the forbidden lower-layer scope**

Run:

```bash
git diff --name-only HEAD~7..HEAD
cargo tree --manifest-path src/bcs/Cargo.toml -p bcs-app-session -e normal | rg 'bcs-message|bcs-message-store'
rg -n 'SessionMessagePage|next_cursor|has_more|created_at:session_seq' \
  src/bcs/crates/service-api/bcs-service-api/src/application/v1 \
  src/bcs/crates/application/v1/bcs-app-session \
  src/bcs/crates/adapters/http/bcs-api-http \
  src/bcs/api-contracts/v1
```

Expected:

- no production file under `bcs-message`, `bcs-message-flow`, repo/store, or DB/migration directories appears in the change set;
- `bcs-app-session` has no normal dependency on `bcs-message` or `bcs-message-store`;
- old V1 page/cursor terms have no remaining production-contract matches (test comments about absence are acceptable).

Use the actual merge base instead of `HEAD~7` if commits were squashed or split differently.

**Step 5: Review the public JSON diff**

Confirm manually from the route test or a local response fixture:

```json
{
  "code": 20000,
  "message": "OK",
  "data": [
    {
      "id": "message-id",
      "timestamp": 1786590000000,
      "sender": "bot-1",
      "content": "done",
      "message_type": "bot",
      "bot_name": "Worker",
      "role": "assistant",
      "run_id": "run-1",
      "historyMeta": {},
      "metadata": {},
      "attachments": []
    }
  ],
  "request_id": "request-id"
}
```

Confirm `data` is directly an array, optional absent fields are omitted, and the legacy route still returns only the array.

**Step 6: Check the final diff and working tree**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exits 0; only intentional changes and pre-existing unrelated untracked files are present.

**Step 7: Final implementation commit if needed**

If formatting or final test-only adjustments remain:

```bash
git add <only-the-intended-files>
git commit -m "test(bcs): verify V1 legacy message compatibility"
```

Do not stage `.worktree/` or `docs/superpowers/plans/2026-07-23-bcs-discover-exclude-requester.md`.
