# BCN OpenAPI V1 — Slice #P1 GroupParticipant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the current V1 GroupParticipant operations (`POST /openapi/v1/collaboration/groups/{group_id}/participants` and `DELETE /openapi/v1/collaboration/groups/{group_id}/participants/{actor_id}`) end-to-end — `application::v1` trait → `bcs-group-v1` facade → `bcs-api-http` route — behind the test Principal verifier.

**Architecture:** Extend the PR#514 group V1 vertical slice. Add only the current `GroupService` participant methods + `Action` variants in `application::v1`; implement them in `bcs-group-v1` by authorizing via `load_readable_group` + `can_manage_group` and delegating to legacy `GroupManagementService.add_member` / `remove_member`. Do not add `update_participant_mode` to the public V1 route surface. No production bootstrap mount; test verifier only.

**Tech Stack:** Rust 1.91, Axum 0.8, async-trait, serde, bcs-domain, bcs-service-api.

**Current contract amendment (2026-08-05):** This historical implementation
plan is superseded by the checked-in OpenAPI contract for GroupParticipant. The
public surface now contains only:

- `POST /openapi/v1/collaboration/groups/{group_id}/participants` with request
  body `{ "actor_id": "..." }`; request-supplied `role` is no longer supported.
- `DELETE /openapi/v1/collaboration/groups/{group_id}/participants/{actor_id}`.

Do **not** implement or mount `PATCH /groups/{group_id}/participants/{actor_id}`
for the public V1 contract. Any task below that references `UpdateGroupParticipant`,
participant `mode` patching, or an add-participant request `role` must be skipped
or rewritten to match the current contract before implementation.

**Reference code (read before starting):**
- Facade struct + helpers: `src/bcs/crates/services/bcs-group-v1/src/lib.rs:38` (`GroupServiceImpl`), `:201` (`can_manage_group`), `:212` (`load_readable_group`), `:180` (`can_read_group`), `:1096` (`delete` impl pattern).
- V1 contract: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs:328` (`GroupService` trait), `:87` (`Participant`), `:323` (`DeleteResult`).
- Authorization: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs:5` (`Action`), `:14` (`ResourceRef`).
- Legacy participant ops: `src/bcs/crates/service-api/bcs-service-api/src/application/group_management.rs:414` (`add_member`), `:422` (`remove_member`), `:459` (`update_participant_mode`), `:69` (`GroupAddMemberCommand`), `:127` (`GroupParticipantModeCommand`), `:243` (`GroupRemoveMemberCommand`), `:164` (`GroupParticipantView`).
- Routes/DTO: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs:16` (`router`), `:110` (`update_group` handler), `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs:77` (`ParticipantRequest`).
- Error mapping: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/error.rs` (`application_error_response`), `src/bcs/crates/services/bcs-group-v1/src/lib.rs:1304` (`map_delete_group_error`), `:1315` (`map_service_error`).

**Key decisions (from design + Codex review):**
- Public V1 does not expose `UpdateGroupParticipantRequest`; participant mode/role patching is out of the current contract.
- `delete_participant` targets Bot actors in phase one (legacy `remove_member` uses `bot_id`); Human-participant removal is out of phase one unless the implementation updates the legacy bridge deliberately.
- V1 `add_participant` resolves `actor_kind` from `actor_id` via `BotRegistryCoreService` (Bot if registered, else Human) — the contract request carries only `actor_id`, not `role`.

---

## File Structure

- `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs` (Modify) — add current add/delete command structs + trait methods; do not add public update participant method unless a future contract reintroduces it.
- `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs` (Modify) — add 3 `Action` variants.
- `src/bcs/crates/service-api/bcs-service-api/tests/v1_group_application_contracts.rs` (Modify) — boundary test for new commands.
- `src/bcs/crates/services/bcs-group-v1/src/lib.rs` (Modify) — impl 3 participant methods + `map_group_use_case_error` helper + `GroupParticipantView`/domain `Participant` → V1 `Participant` projection (reuse existing projection from `get`/`create`).
- `src/bcs/crates/services/bcs-group-v1/tests/v1_group_participant.rs` (Create) — use-case authorization/role tests.
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs` (Modify) — `AddParticipantRequest`, `UpdateParticipantRequest` + `impl From`.
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs` (Modify) — add/remove handlers + router routes; assert patch route is absent.
- `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs` (Modify) — add/remove route tests + fake service methods, plus an inventory/404 assertion for the absent patch route.

---

### Task 1: application::v1 contract — commands, trait methods, Action variants

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/v1_group_application_contracts.rs`

- [ ] **Step 1: Write the failing boundary test**

Append to `tests/v1_group_application_contracts.rs` (inside the `Noop` impl of `GroupService` and a new test):

```rust
#[test]
fn participant_commands_carry_principal_and_no_raw_credentials() {
    let principal = Principal::bot("bot-1", "tenant-a", BTreeSet::new());
    let add = AddGroupParticipant {
        principal: principal.clone(),
        group_id: "g1".into(),
        actor_id: "bot-2".into(),
        actor_kind: ActorKind::Bot,
        role: ParticipantRole::Consultant,
    };
    let update = UpdateGroupParticipant {
        principal: principal.clone(),
        group_id: "g1".into(),
        actor_id: "bot-2".into(),
        mode: ParticipantMode::Muted,
    };
    let remove = DeleteGroupParticipant {
        principal,
        group_id: "g1".into(),
        actor_id: "bot-2".into(),
    };
    for cmd in [&add.principal as &Principal, &update.principal, &remove.principal] {
        let s = format!("{cmd:?}");
        assert!(!s.contains("Cookie") && !s.contains("Bearer") && !s.contains("sender"));
    }
}
```

Add the three methods to the `Noop` `GroupService` impl in the same file (so it still compiles once the trait grows):

```rust
async fn add_participant(&self, _command: AddGroupParticipant) -> Result<Participant, ApplicationError> {
    Err(ApplicationError::internal("not implemented"))
}
async fn update_participant(&self, _command: UpdateGroupParticipant) -> Result<Participant, ApplicationError> {
    Err(ApplicationError::internal("not implemented"))
}
async fn delete_participant(&self, _command: DeleteGroupParticipant) -> Result<DeleteResult, ApplicationError> {
    Err(ApplicationError::internal("not implemented"))
}
```

Add imports: `AddGroupParticipant, UpdateGroupParticipant, DeleteGroupParticipant` from `bcs_service_api::application::v1`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api --test v1_group_application_contracts`
Expected: FAIL — `AddGroupParticipant` / `UpdateGroupParticipant` / `DeleteGroupParticipant` not found; trait methods missing.

- [ ] **Step 3: Add Action variants** (`authorization.rs`)

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    ListBotGroups,
    CreateGroup,
    ReadGroup,
    UpdateGroup,
    DeleteGroup,
    AddGroupParticipant,
    UpdateGroupParticipant,
    RemoveGroupParticipant,
}
```

- [ ] **Step 4: Add command structs + trait methods** (`group.rs`, after `DeleteGroup` / before `DeleteResult`, and extend the trait at `:328`):

```rust
#[derive(Debug, Clone)]
pub struct AddGroupParticipant {
    pub principal: Principal,
    pub group_id: String,
    pub actor_id: String,
    pub actor_kind: ActorKind,
    pub role: ParticipantRole,
}

#[derive(Debug, Clone)]
pub struct UpdateGroupParticipant {
    pub principal: Principal,
    pub group_id: String,
    pub actor_id: String,
    pub mode: ParticipantMode,
}

#[derive(Debug, Clone)]
pub struct DeleteGroupParticipant {
    pub principal: Principal,
    pub group_id: String,
    pub actor_id: String,
}
```

Extend the trait (after `delete`):

```rust
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api --test v1_group_application_contracts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs \
        src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs \
        src/bcs/crates/service-api/bcs-service-api/tests/v1_group_application_contracts.rs
git commit -m "feat(bcs): add v1 group participant application contracts"
```

---

### Task 2: bcs-group-v1 facade implementation

**Files:**
- Modify: `src/bcs/crates/services/bcs-group-v1/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-group-v1/tests/v1_group_participant.rs`

- [ ] **Step 1: Write the failing use-case test**

Create `tests/v1_group_participant.rs`. Use the same fake `GroupCoreService`/`BotRegistryCoreService`/`SessionManagementService` doubles pattern as `tests/v1_group_service.rs` (read that file for the harness: it constructs `GroupServiceImpl::new(...)` with in-memory fakes and seeds groups via `GroupCoreService`). Seed a group whose driver is `bot-driver` and a participant `bot-a`. Then:

```rust
use bcs_service_api::application::v1::{
    AddGroupParticipant, DeleteGroupParticipant, GroupService, ParticipantRole,
    Principal, UpdateGroupParticipant,
};
use bcs_domain::ParticipantMode;
// ... harness: build `service: GroupServiceImpl` with driver=bot-driver, participant bot-a

#[tokio::test]
async fn driver_can_add_bot_participant() {
    let principal = Principal::bot("bot-driver", "tenant-a", Default::default());
    let added = service
        .add_participant(AddGroupParticipant {
            principal,
            group_id: GROUP_ID.into(),
            actor_id: "bot-b".into(),
            actor_kind: ActorKind::Bot,
            role: ParticipantRole::Consultant,
        })
        .await
        .expect("driver can add");
    assert_eq!(added.actor_id, "bot-b");
    assert_eq!(added.role, ParticipantRole::Consultant);
}

#[tokio::test]
async fn non_manager_cannot_add_participant() {
    let principal = Principal::bot("bot-a", "tenant-a", Default::default()); // plain participant
    let err = service
        .add_participant(AddGroupParticipant {
            principal,
            group_id: GROUP_ID.into(),
            actor_id: "bot-b".into(),
            actor_kind: ActorKind::Bot,
            role: ParticipantRole::Consultant,
        })
        .await
        .expect_err("plain participant forbidden");
    assert!(matches!(err, bcs_service_api::application::v1::ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn update_participant_mode_returns_participant() {
    let principal = Principal::bot("bot-driver", "tenant-a", Default::default());
    let updated = service
        .update_participant(UpdateGroupParticipant {
            principal,
            group_id: GROUP_ID.into(),
            actor_id: "bot-a".into(),
            mode: ParticipantMode::Muted,
        })
        .await
        .expect("update ok");
    assert_eq!(updated.actor_id, "bot-a");
    assert_eq!(updated.mode, ParticipantMode::Muted);
}

#[tokio::test]
async fn delete_participant_is_idempotent_for_bot() {
    let principal = Principal::bot("bot-driver", "tenant-a", Default::default());
    let res = service
        .delete_participant(DeleteGroupParticipant {
            principal,
            group_id: GROUP_ID.into(),
            actor_id: "bot-a".into(),
        })
        .await
        .expect("delete ok");
    assert!(res.deleted);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group-v1 --test v1_group_participant`
Expected: FAIL — trait methods not implemented on `GroupServiceImpl`.

- [ ] **Step 3: Add the `GroupUseCaseError` → `ApplicationError` mapper**

In `lib.rs`, near `map_delete_group_error` (`:1304`), add:

```rust
fn map_group_use_case_error(error: GroupUseCaseError) -> ApplicationError {
    use bcs_service_api::application::{group_management::GroupUseCaseError as E};
    match error {
        E::Forbidden(msg) => ApplicationError::forbidden(msg),
        E::Unauthorized(msg) => ApplicationError::forbidden(msg),
        E::ActorNotFound(msg) => ApplicationError::not_found("actor_not_found", msg),
        E::InvalidParticipantMode { mode, actor_kind } => ApplicationError::invalid(
            "invalid_participant",
            format!("mode {mode:?} not valid for {actor_kind:?}"),
        ),
        E::Conflict(msg) => ApplicationError::conflict("conflict", msg),
        E::Service(e) => map_service_error(e),
        other => ApplicationError::internal(other.to_string()),
    }
}
```

(Adjust the `use` path to where `GroupUseCaseError` is re-exported; confirm with `grep -rn "pub use.*GroupUseCaseError" src/bcs/crates/service-api`.)

- [ ] **Step 4: Implement the 3 participant methods on `GroupServiceImpl`**

Add a `GroupParticipantView` → V1 `Participant` projection (if a domain `Participant` → V1 `Participant` projection already exists in `lib.rs` from `get`/`create`, reuse it; otherwise add):

```rust
fn participant_view_to_v1(view: GroupParticipantView) -> Participant {
    Participant {
        actor_id: view.bot_uuid,
        actor_kind: view.actor_kind,
        name: view.bot_name,
        role: parse_participant_role(&view.role),
        mode: view
            .mode
            .unwrap_or_else(|| ParticipantMode::default_for(view.actor_kind)),
    }
}

fn parse_participant_role(s: &str) -> ParticipantRole {
    match s {
        "driver" => ParticipantRole::Driver,
        "manager" => ParticipantRole::Manager,
        "worker" => ParticipantRole::Worker,
        "observer" => ParticipantRole::Observer,
        _ => ParticipantRole::Consultant,
    }
}
```

Implement the methods inside `impl GroupService for GroupServiceImpl` (after `delete`, `:1155`):

```rust
async fn add_participant(
    &self,
    command: AddGroupParticipant,
) -> Result<Participant, ApplicationError> {
    let group = self
        .load_readable_group(&command.principal, &command.group_id)
        .await?
        .ok_or_else(|| {
            ApplicationError::not_found(
                "group_not_found",
                format!("Group '{}' was not found", command.group_id),
            )
        })?;
    if !Self::can_manage_group(&command.principal, &group) {
        return Err(ApplicationError::forbidden(
            "Principal cannot manage the group",
        ));
    }
    let (bot_id, human_actor_id) = match command.actor_kind {
        ActorKind::Bot => (command.actor_id.clone(), None),
        ActorKind::Human => (String::new(), Some(command.actor_id.clone())),
    };
    let result = self
        .management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some(command.principal.actor_id()),
            human_actor_id,
            group_id: command.group_id.clone(),
            bot_id,
            role: Some(command.role.to_string()),
        })
        .await
        .map_err(map_group_use_case_error)?;
    Ok(participant_view_to_v1(result.member))
}

async fn update_participant(
    &self,
    command: UpdateGroupParticipant,
) -> Result<Participant, ApplicationError> {
    let group = self
        .load_readable_group(&command.principal, &command.group_id)
        .await?
        .ok_or_else(|| {
            ApplicationError::not_found(
                "group_not_found",
                format!("Group '{}' was not found", command.group_id),
            )
        })?;
    if !Self::can_manage_group(&command.principal, &group) {
        return Err(ApplicationError::forbidden(
            "Principal cannot manage the group",
        ));
    }
    self.management
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: command.principal.actor_id(),
            group_id: command.group_id.clone(),
            actor_id: command.actor_id.clone(),
            mode: command.mode,
        })
        .await
        .map_err(map_group_use_case_error)?;
    // Reload the group and project the updated participant (reuse the existing
    // domain Participant -> V1 Participant projection used by get/create).
    let group = self
        .groups
        .try_get(&command.group_id)
        .await
        .map_err(map_service_error)?
        .ok_or_else(|| {
            ApplicationError::not_found(
                "group_not_found",
                format!("Group '{}' was not found", command.group_id),
            )
        })?;
    group
        .participants
        .iter()
        .find(|p| p.bot_uuid == command.actor_id)
        .map(|p| self.project_participant(p)) // reuse existing projection helper
        .ok_or_else(|| {
            ApplicationError::not_found(
                "participant_not_found",
                format!("Participant '{}' not found", command.actor_id),
            )
        })
}

async fn delete_participant(
    &self,
    command: DeleteGroupParticipant,
) -> Result<DeleteResult, ApplicationError> {
    let group = self
        .load_readable_group(&command.principal, &command.group_id)
        .await?
        .ok_or_else(|| {
            ApplicationError::not_found(
                "group_not_found",
                format!("Group '{}' was not found", command.group_id),
            )
        })?;
    if !Self::can_manage_group(&command.principal, &group) {
        return Err(ApplicationError::forbidden(
            "Principal cannot manage the group",
        ));
    }
    // Phase one: target is a Bot actor (legacy remove_member uses bot_id).
    self.management
        .remove_member(GroupRemoveMemberCommand {
            caller_actor_id: Some(command.principal.actor_id()),
            group_id: command.group_id.clone(),
            bot_id: command.actor_id.clone(),
        })
        .await
        .map_err(map_group_use_case_error)?;
    Ok(DeleteResult { deleted: true })
}
```

If `project_participant` (domain `Participant` → V1 `Participant`) does not exist as a standalone helper, extract it from the existing `get`/`create` projection code and name it `project_participant(&self, p: &bcs_domain::Participant) -> Participant`. Add the needed `use` imports (`GroupAddMemberCommand`, `GroupParticipantModeCommand`, `GroupRemoveMemberCommand`, `GroupParticipantView`, `GroupUseCaseError`, `ActorKind`, `ParticipantMode`, `Participant`, `DeleteResult`).

- [ ] **Step 5: Run the use-case tests**

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-group-v1`
Expected: PASS (new tests + existing `v1_group_service`).

- [ ] **Step 6: Commit**

```bash
git add src/bcs/crates/services/bcs-group-v1/src/lib.rs \
        src/bcs/crates/services/bcs-group-v1/tests/v1_group_participant.rs
git commit -m "feat(bcs): implement v1 group participant use cases"
```

---

### Task 3: bcs-api-http DTO + routes

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs`

- [ ] **Step 1: Write the failing route tests**

In `tests/group_routes.rs`, extend the fake `GroupService` impl (the `#[async_trait] impl GroupService for FakeGroupService` near `:84`) with the three methods returning canned `Participant`/`DeleteResult`, then add tests:

```rust
async fn add_participant(&self, command: AddGroupParticipant) -> Result<Participant, ApplicationError> {
    Ok(Participant {
        actor_id: command.actor_id,
        actor_kind: command.actor_kind,
        name: None,
        role: command.role,
        mode: ParticipantMode::Auto,
    })
}
async fn update_participant(&self, command: UpdateGroupParticipant) -> Result<Participant, ApplicationError> {
    Ok(Participant {
        actor_id: command.actor_id,
        actor_kind: ActorKind::Bot,
        name: None,
        role: ParticipantRole::Consultant,
        mode: command.mode,
    })
}
async fn delete_participant(&self, command: DeleteGroupParticipant) -> Result<DeleteResult, ApplicationError> {
    Ok(DeleteResult { deleted: true })
}
```

Add three tests (mirror the existing `delete` route test pattern at `:241`):

```rust
#[tokio::test]
async fn add_group_participant_returns_created_participant() {
    let app = build_app().await;
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/groups/group-1/participants")
                .header("x-test-principal", bot_principal_header())
                .json(&serde_json::json!({ "actor_id": "bot-2" })),
        )
        .await
        .expect("request");
    assert_eq!(resp.status(), StatusCode::OK);
    let body = response_json(resp).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_id"], "bot-2");
    assert_eq!(body["data"]["role"], "consultant");
}

#[tokio::test]
async fn update_group_participant_returns_updated_mode() {
    let app = build_app().await;
    let resp = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/openapi/v1/groups/group-1/participants/bot-2")
                .header("x-test-principal", bot_principal_header())
                .json(&serde_json::json!({ "mode": "muted" })),
        )
        .await
        .expect("request");
    assert_eq!(resp.status(), StatusCode::OK);
    let body = response_json(resp).await;
    assert_eq!(body["data"]["mode"], "muted");
}

#[tokio::test]
async fn remove_group_participant_returns_deleted() {
    let app = build_app().await;
    let resp = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/openapi/v1/groups/group-1/participants/bot-2")
                .header("x-test-principal", bot_principal_header()),
        )
        .await
        .expect("request");
    assert_eq!(resp.status(), StatusCode::OK);
    let body = response_json(resp).await;
    assert_eq!(body["data"]["deleted"], true);
}

#[tokio::test]
async fn add_group_participant_rejects_unknown_field() {
    let app = build_app().await;
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/openapi/v1/groups/group-1/participants")
                .header("x-test-principal", bot_principal_header())
                .json(&serde_json::json!({ "actor_id": "bot-2", "extra": 1 })),
        )
        .await
        .expect("request");
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
}
```

(Align `build_app`, `bot_principal_header`, `response_json` helpers with the existing tests in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test group_routes`
Expected: FAIL — routes not registered (404/405) and fake impl missing methods.

- [ ] **Step 3: Add DTOs** (`dto/group.rs`)

```rust
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AddParticipantRequest {
    pub actor_id: String,
    pub role: bcs_service_api::application::v1::ParticipantRole,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateParticipantRequest {
    pub mode: bcs_service_api::application::v1::ParticipantMode,
}
```

- [ ] **Step 4: Add route handlers + register routes** (`routes/group.rs`)

Add imports:

```rust
use bcs_service_api::application::v1::{
    AddGroupParticipant, CreateGroup, DeleteGroup, DeleteGroupParticipant, GetGroup,
    ListBotGroups, Principal, UpdateGroup, UpdateGroupParticipant,
};
use crate::v1::openapi::dto::group::{
    AddParticipantRequest, CreateGroupRequest, ListGroupsQuery, UpdateGroupRequest,
    UpdateParticipantRequest,
};
```

Register routes in `router()` (after the `/openapi/v1/groups/{group_id}` route):

```rust
        .route(
            "/openapi/v1/groups/{group_id}/participants",
            post(add_group_participant),
        )
        .route(
            "/openapi/v1/groups/{group_id}/participants/{actor_id}",
            patch(update_group_participant).delete(remove_group_participant),
        )
```

Add `patch` to the `use axum::routing::{get, post};` import → `use axum::routing::{delete, get, patch, post};`.

Add handlers:

```rust
async fn add_group_participant(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<AddParticipantRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    // Resolve actor_kind: Bot if registered, else Human.
    let actor_kind = state
        .group_service
        .resolve_actor_kind(&body.actor_id)
        .await;
    let result = state
        .group_service
        .add_participant(AddGroupParticipant {
            principal,
            group_id,
            actor_id: body.actor_id,
            actor_kind,
            role: body.role,
        })
        .await
        .map_err(|e| application_error_response(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn update_group_participant(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<UpdateParticipantRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((group_id, actor_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = state
        .group_service
        .update_participant(UpdateGroupParticipant {
            principal,
            group_id,
            actor_id,
            mode: body.mode,
        })
        .await
        .map_err(|e| application_error_response(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn remove_group_participant(
    State(state): State<ApiState>,
    Extension(principal): Extension<Principal>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((group_id, actor_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = state
        .group_service
        .delete_participant(DeleteGroupParticipant {
            principal,
            group_id,
            actor_id,
        })
        .await
        .map_err(|e| application_error_response(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
```

**`actor_kind` resolution:** `add_group_participant` needs `actor_kind`, which the contract request does not carry. Rather than pushing a `resolve_actor_kind` method onto the `GroupService` trait (which would force every impl + fake to add it), resolve it in the facade: change `add_participant` to accept `actor_id` only and resolve `actor_kind` internally via `BotRegistryCoreService` (Bot if `registry.try_get(actor_id)` returns `Some`, else Human). Update the `AddGroupParticipant` command in Task 1 to drop both `actor_kind` and request-supplied `role` (only `actor_id`), and have `GroupServiceImpl::add_participant` call `self.registry.try_get(&command.actor_id)` to determine `ActorKind`. The route handler then passes only `actor_id`. Update the Task 1 command/contract test accordingly (remove `actor_kind` and `role` fields).

- [ ] **Step 5: Run route tests**

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-api-http --test group_routes`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs \
        src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs \
        src/bcs/crates/adapters/http/bcs-api-http/tests/group_routes.rs \
        src/bcs/crates/service-api/bcs-service-api/src/application/v1/group.rs \
        src/bcs/crates/service-api/bcs-service-api/tests/v1_group_application_contracts.rs \
        src/bcs/crates/services/bcs-group-v1/src/lib.rs \
        src/bcs/crates/services/bcs-group-v1/tests/v1_group_participant.rs
git commit -m "feat(bcs): expose v1 group participant http routes"
```

---

### Task 4: Verify the full slice

- [ ] **Step 1: Run all affected cargo tests**

Run:
```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api -p bcs-group-v1 -p bcs-api-http
```
Expected: all PASS, 0 failed.

- [ ] **Step 2: Boundary AST check** (the existing v1 boundary test asserts `application::v1` imports no `axum`/`http`/`bcs_protocol`)

Run: `cargo test --manifest-path src/bcs/Cargo.toml -p bcs-service-api --test v1_boundary_contracts`
Expected: PASS.

- [ ] **Step 3: Contract unchanged — re-validate**

Run:
```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py --root src/bcs/api-contracts/v1
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
```
Expected: `32 operations validated`; pytest all pass.

- [ ] **Step 4: Commit (if any cleanup) + push**

```bash
git push --no-verify origin bcn-openapi-batch-2
```

---

## Self-Review Notes

- **Spec coverage:** design §8.2 (3 GroupParticipant ops) + design §8.7 (authorization: manager/non-manager, role invariants, Bot target for remove) + Codex C1-declined/C2..C5 (none apply to #P1 except full 4-value `ParticipantMode` kept). Covered by Tasks 1-3.
- **Type consistency:** `AddGroupParticipant` (drop `actor_kind` and request-supplied `role` — final shape: `{principal, group_id, actor_id}`), no public `UpdateGroupParticipant`, `DeleteGroupParticipant{principal, group_id, actor_id}`, return `Participant` / `DeleteResult{deleted}`. Update the Task 1 command + test to remove `actor_kind` before Task 2/3.
- **Phased gaps (out of #P1):** Human-participant `delete` (legacy `remove_member` is Bot-only); production mount / Principal transport (Slice #P4).

## Execution

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with `executing-plans`, batched with checkpoints.
