# Session Creator Compatibility Follow-up Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore the legacy explicit-Human Session join boundary, including `participant_join_seq`, while documenting and testing V1 `acting_bot_id` as an explicit Human-or-Bot creator Actor selector.

**Architecture:** Keep both HTTP adapters mapped to the transport-neutral `SessionLaunchService`. Public creators and StateMachine Human Observers remain in the initial roster; an explicit Human creator not already covered by those rules is deferred until after a new Session is persisted and is then added through `SessionManagementService::add_participant`. The V1 wire name `acting_bot_id` stays unchanged, while its OpenAPI contract explicitly admits the authenticated Human Actor ID as well as an owned Bot ID.

**Tech Stack:** Rust 2024, Axum, async-trait, Serde, OpenAPI YAML, pytest, Cargo integration tests.

---

## Constraints

- Do not rename or remove any legacy request field.
- Keep the V1 wire field name `acting_bot_id`.
- Do not mutate the parent Group participant roster.
- Preserve public creator insertion, role defaults, and StateMachine Observer insertion.
- Add a private explicit Human only for a newly created Session, never for reactivation.
- Ensure the deferred Human is present before StateMachine startup or SessionContext delivery.
- Do not run global `cargo fmt`; format only touched Rust files if a targeted formatter is required.

### Task 1: Lock the private explicit-Human regression with shared-service tests

**Files:**

- Modify: `src/bcs/crates/services/bcs-session/tests/session_launch.rs`

**Step 1: Write the failing explicit-Human test**

Add a test named `explicit_private_human_creator_is_added_with_join_sequence` using the existing real `MemorySessionRepo` fixture:

```rust
#[tokio::test]
async fn explicit_private_human_creator_is_added_with_join_sequence() {
    let fixture = Fixture::new();
    fixture.add_bot("driver", "alice").await;
    fixture
        .add_group(Group::new(
            "group-1",
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        ))
        .await;

    let outcome = fixture
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("human_alice")),
        })
        .await
        .expect("explicit Human creator may create");

    let participant = outcome
        .session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "human_alice")
        .expect("explicit Human creator is inserted");
    assert_eq!(participant.actor_kind, ActorKind::Human);
    assert_eq!(participant.role, ParticipantRole::Driver);
    assert_eq!(participant.mode, Some(ParticipantMode::Present));
    assert_eq!(
        outcome
            .session
            .participant_join_seq
            .as_ref()
            .and_then(|join_seq| join_seq.get("human_alice"))
            .and_then(serde_json::Value::as_i64),
        Some(0)
    );
}
```

**Step 2: Strengthen the existing inferred/public/StateMachine assertions**

- In `inferred_private_human_creator_is_not_auto_added`, also assert that no `human_alice` join-sequence entry exists.
- In `public_non_member_is_added_with_requested_role`, assert the inferred Human remains in the initial roster and has no individual join-sequence entry.
- In `state_machine_human_is_added_as_present_observer`, assert the Human stays Observer/Present and is not rewritten as Driver.

**Step 3: Add a reactivation regression test**

Create a Session as the group Driver, complete it, then reactivate it with `requested_creator = Some("human_alice")`. Assert the reactivated Session does not gain `human_alice`, matching the legacy new-Session-only behavior.

**Step 4: Run the focused tests and verify RED**

Run:

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch explicit_private_human_creator_is_added_with_join_sequence -- --exact
```

Expected: FAIL because the Human is currently preloaded and `participant_join_seq["human_alice"]` is absent.

**Step 5: Commit the failing tests**

```bash
git add src/bcs/crates/services/bcs-session/tests/session_launch.rs
git commit -m "test(bcs): cover explicit human session join sequence"
```

### Task 2: Defer the private explicit Human until after Session creation

**Files:**

- Modify: `src/bcs/crates/services/bcs-session/src/launch.rs`
- Test: `src/bcs/crates/services/bcs-session/tests/session_launch.rs`

**Step 1: Represent initial and deferred participant construction**

Add a private value object near `PreparedLaunch`:

```rust
struct BuiltParticipants {
    initial: Vec<Participant>,
    deferred_after_create: Option<Participant>,
}
```

Add `deferred_after_create: Option<Participant>` to `PreparedLaunch`.

Change `build_participants` to return `Result<BuiltParticipants, SessionLaunchError>`. Keep public creator and StateMachine Observer insertion in `initial`. Replace the final explicit-Human `participants.push(...)` with:

```rust
let deferred_after_create = (explicit_human_creator
    && !participants
        .iter()
        .any(|participant| participant.bot_uuid == creator))
.then(|| Participant {
    bot_uuid: creator.to_string(),
    bot_name: request.caller.display_name().map(str::to_string),
    kind: None,
    role: ParticipantRole::Driver,
    actor_kind: ActorKind::Human,
    mode: Some(ParticipantMode::Present),
});

Ok(BuiltParticipants {
    initial: participants,
    deferred_after_create,
})
```

Use `initial` in `NewSessionParams.participants` and carry `deferred_after_create` in `PreparedLaunch`.

**Step 2: Apply the deferred participant only on create**

In `SessionLaunchService::create`, keep the returned Session mutable. When `outcome.created` and `prepared.deferred_after_create` is present, call:

```rust
session = self
    .sessions
    .add_participant(&session.id, participant)
    .await
    .map_err(map_session_error)?;
```

Pass the updated Session to `finish_launch`. Do not add this step to `reactivate`.

Clone or borrow the deferred participant before passing the complete `PreparedLaunch` to `finish_launch`; do not leak persistence behavior into either HTTP adapter.

**Step 3: Run focused shared-service tests**

Run:

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch
```

Expected: all Session launch tests pass, including explicit/inferred Human, public creator, StateMachine, and reactivation cases.

**Step 4: Run the crate checks**

Run:

```bash
cd src/bcs
cargo check -p bcs-session --all-targets
```

Expected: PASS with no warnings introduced by the changed code.

**Step 5: Commit**

```bash
git add src/bcs/crates/services/bcs-session/src/launch.rs \
  src/bcs/crates/services/bcs-session/tests/session_launch.rs
git commit -m "fix(bcs): restore explicit human session join boundary"
```

### Task 3: Make V1 explicit-Human creator support contractual

**Files:**

- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi/sessions.yaml`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs`
- Regenerate: `src/gateway/configs/schemas/bcn.openapi.json`

**Step 1: Write failing OpenAPI assertions**

Extend `test_create_group_session_accepts_the_v1_native_launch_fields` to assert that the `acting_bot_id` description documents all of the following:

- it is an explicit creator Actor selector;
- `human_{user.id}` selects the authenticated Human;
- a Human may select an owned Bot;
- a Bot caller may select only itself;
- omission selects the authenticated caller implicitly.

Keep the property name and the strict request property set unchanged.

**Step 2: Write the V1 route mapping test**

Add `create_session_accepts_explicit_authenticated_human_actor` to `session_routes.rs`. POST:

```json
{
  "acting_bot_id": "human_staff-1",
  "creator_role": "observer"
}
```

Assert HTTP 201 and that the recorded `CreateSession` command contains the authenticated Human caller, `acting_bot_id == Some("human_staff-1")`, and `creator_role == Some(ParticipantRole::Observer)`.

This test proves DTO mapping. The shared-service tests from Tasks 1–2 prove authorization and participant effects.

**Step 3: Run and verify RED**

Run:

```bash
cd src/bcs
uv run --with pytest --with pyyaml pytest tests/openapi/test_session_v1_contract.py -q
```

Expected: the description assertion fails because the current contract says only “Bot identity”.

**Step 4: Update the authoritative contract and Rust comments**

Keep `acting_bot_id` unchanged in JSON. Update `sessions.yaml` to describe the Human-or-Bot Actor semantics and authorization restrictions. Add matching Rust doc comments to the V1 DTO and application command fields; do not move ownership lookup into the DTO.

**Step 5: Regenerate the Gateway snapshot**

From the repository root, run:

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pyyaml python src/bcs/scripts/dump_openapi.py \
  src/gateway/configs/schemas/bcn.openapi.json \
  --root src/bcs/api-contracts/v1
```

Expected: validation succeeds and the deterministic JSON snapshot contains the updated description only.

**Step 6: Run V1 contract and adapter tests**

Run:

```bash
cd src/bcs
uv run --with pytest --with pyyaml pytest tests/openapi/test_session_v1_contract.py -q
cargo test -p bcs-api-http --test session_routes create_session_accepts_explicit_authenticated_human_actor -- --exact
```

Expected: both commands pass.

**Step 7: Commit**

```bash
git add src/bcs/tests/openapi/test_session_v1_contract.py \
  src/bcs/api-contracts/v1/openapi/sessions.yaml \
  src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs \
  src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs \
  src/gateway/configs/schemas/bcn.openapi.json
git commit -m "docs(bcs): define V1 acting creator actor semantics"
```

### Task 4: Add protocol-level legacy regression coverage

**Files:**

- Modify: `src/bcs/crates/adapters/http/bcs-http/tests/session_create_contract.rs`

**Step 1: Add a real-store legacy route test**

Add `legacy_explicit_private_human_creator_records_join_sequence`. Build a private Chat Group containing Alice's Driver Bot, use `MemorySessionRepo` plus `SessionManagementServiceImpl` for `session_management`, and wire the same implementation into `SessionLaunchApplication`. POST:

```json
{
  "created_by": "human_alice"
}
```

Assert HTTP 201, locate `human_alice` in the response participants, assert Driver/Present, and assert:

```rust
assert_eq!(body["participant_join_seq"]["human_alice"], 0);
```

Also assert the stored Group still contains only `driver-bot`.

**Step 2: Add the inferred-Human counterpart**

POST an empty body to another private Chat Session using the same authenticated Human. Assert `created_by == "human_alice"`, but the response participants and join-sequence map do not contain `human_alice`.

**Step 3: Run the legacy adapter test**

Run:

```bash
cd src/bcs
cargo test -p bcs-http --test session_create_contract
```

Expected: all legacy create/reactivate contract tests pass.

**Step 4: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-http/tests/session_create_contract.rs
git commit -m "test(bcs): preserve legacy human creator compatibility"
```

### Task 5: Run the affected contract and application verification set

**Files:**

- Verify only; no expected source edits.

**Step 1: Run focused Rust tests**

```bash
cd src/bcs
cargo test -p bcs-session --test session_launch
cargo test -p bcs-http --test session_create_contract
cargo test -p bcs-api-http --test session_routes
cargo test -p bcs-app-session --test v1_session_service
cargo test -p bcs-service-api --test session_launch_contract
```

Expected: all pass.

**Step 2: Run OpenAPI verification**

```bash
cd src/bcs
uv run --with pytest --with pyyaml pytest tests/openapi/test_session_v1_contract.py -q
uv run --with pyyaml python scripts/validate_openapi_contract.py --root api-contracts/v1
```

Expected: all pass.

**Step 3: Check patch hygiene**

From the repository root:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only intentional files changed.

**Step 4: Run the BCS pre-push lint gate if time permits**

```bash
scripts/ci/pre_push.sh
```

If this broader gate cannot run because of unrelated environment or dependency constraints, record the exact failure and retain the focused passing evidence above.

