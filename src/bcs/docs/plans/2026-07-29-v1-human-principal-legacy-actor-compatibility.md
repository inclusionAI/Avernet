# V1 Human Principal Legacy Actor Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Gateway Human identity free of BCN actor prefixes while preserving BCN's existing Human Actor, `created_by`, relationship, and DM behavior.

**Architecture:** Remove `actor_id` from `HumanPrincipal`, then derive `human_<subject.id>` only inside the BCN application boundary. Before a V1 Human DM enters the existing `GroupManagementService`, materialize the legacy Human Actor with the current V1 display-name fallback.

**Tech Stack:** Rust, Tokio tests, `bcs-service-api`, `bcs-group`, Cargo.

## Global Constraints

- Do not change database schemas or persisted identity formats.
- Keep `created_by` equal to the raw `AuthenticatedUser.id`.
- Do not add tenant to Human Actor IDs or ownership checks.
- Preserve Legacy API behavior and existing `ensure_human_actor` idempotency.
- Do not run global `cargo fmt`.

---

### Task 1: Make Human Principal carry only original identity

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/v1_group_application_contracts.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/principal.rs`
- Modify: `src/bcs/crates/services/bcs-group-v1/tests/v1_group_service.rs`

**Interfaces:**
- Consumes: Gateway-compatible `AuthenticatedUser`.
- Produces: `Principal::human(subject, tenant, scopes)` and BCN-only `Principal::actor_id() -> String`.

- [ ] **Step 1: Write the failing Principal contract test**

Change the Human constructor call so it has no caller-provided Actor ID, assert
that serialization has no `actor_id`, and assert that BCN derives the existing
legacy Actor ID:

```rust
let human = Principal::human(
    AuthenticatedUser {
        id: "staff-1".into(),
        username: "alice".into(),
        display_name: None,
        full_name: None,
    },
    "tenant-a",
    BTreeSet::new(),
);
assert_eq!(human.actor_id(), "human_staff-1");
let value = serde_json::to_value(&human).expect("serialize Human Principal");
assert!(value.get("actor_id").is_none());
```

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```bash
cargo test -p bcs-service-api --test v1_group_application_contracts principal_preserves_gateway_identity_without_bot_impersonation
```

Expected: compilation fails because `Principal::human` still requires an
`actor_id` argument and `actor_id()` still borrows the stored field.

- [ ] **Step 3: Implement the minimal Principal change**

Remove `HumanPrincipal.actor_id`, change the constructor, and derive the BCN
Actor ID from the authenticated subject:

```rust
pub fn human(
    subject: AuthenticatedUser,
    tenant: impl Into<String>,
    scopes: BTreeSet<String>,
) -> Self {
    Self::Human(HumanPrincipal {
        subject,
        tenant: tenant.into(),
        scopes,
    })
}

pub fn actor_id(&self) -> String {
    match self {
        Self::Human(principal) => format!("human_{}", principal.subject.id),
        Self::Bot(principal) => principal.bot_uuid.clone(),
    }
}
```

Update Human test helpers to accept only `subject_id`.

- [ ] **Step 4: Run the service API contract test and verify GREEN**

Run:

```bash
cargo test -p bcs-service-api --test v1_group_application_contracts
```

Expected: all tests pass.

### Task 2: Preserve Human Actor creation and legacy DM behavior

**Files:**
- Modify: `src/bcs/crates/services/bcs-group-v1/tests/v1_group_service.rs`
- Modify: `src/bcs/crates/services/bcs-group-v1/src/lib.rs`

**Interfaces:**
- Consumes: `Principal::actor_id() -> String`,
  `BotRegistryCoreService::ensure_human_actor(staff_no, nick_name)`, and
  `GroupManagementService::create_dm(DmCreateCommand)`.
- Produces: V1 DM behavior that materializes `human_<subject.id>` and delegates
  to the existing management service.

- [ ] **Step 1: Write the failing V1 Human DM tests**

Create a Human Principal with `subject.id = "staff-1"` and
`display_name = Some("Alice")`. Create a DM without pre-registering the Human,
then assert:

```rust
let human = fixture
    .bots
    .get("human_staff-1")
    .await
    .expect("V1 must materialize the legacy Human Actor");
assert_eq!(human.capabilities.name.as_deref(), Some("Alice"));
assert_eq!(human.created_by.as_deref(), Some("staff-1"));
assert!(detail.participants.iter().any(|participant| {
    participant.actor_id == "human_staff-1"
}));
```

Add a second test that pre-creates `human_staff-1` with `"Original Name"`,
runs the V1 DM call with `"Changed Name"`, and asserts that the stored name
remains `"Original Name"`.

- [ ] **Step 2: Run the two tests and verify RED**

Run:

```bash
cargo test -p bcs-group-v1 --test v1_group_service human_principal
```

Expected: the missing Human Actor assertion fails because the current V1 path
creates a DM directly without calling `ensure_human_actor`.

- [ ] **Step 3: Implement the minimal V1 adapter**

Add one helper that preserves the current V1 display-name order:

```rust
fn human_display_name(human: &HumanPrincipal) -> String {
    human
        .subject
        .display_name
        .clone()
        .or_else(|| human.subject.full_name.clone())
        .unwrap_or_else(|| human.subject.username.clone())
}
```

In V1 `create_dm`, retain the existing eligibility check. For a Human
Principal, call:

```rust
self.registry
    .ensure_human_actor(
        &human.subject.id,
        &human_display_name(human),
    )
    .await
    .map_err(map_service_error)?;
```

Then delegate to the existing management use case:

```rust
let actor_id = principal.actor_id();
let result = self
    .management
    .create_dm(DmCreateCommand {
        group_id: None,
        caller_actor_id: Some(actor_id),
        driver_bot: None,
        target_actor_id: request.target_actor_id,
        label: request.name,
        topic: None,
        context: request.context,
    })
    .await
    .map_err(map_group_error)?;
```

Project the persisted Group returned by the existing management path.

- [ ] **Step 4: Run the V1 group tests and verify GREEN**

Run:

```bash
cargo test -p bcs-group-v1 --test v1_group_service
```

Expected: all tests pass, including Human creation/name preservation, DM
reuse, authorization, and error mapping.

### Task 3: Align design documentation and verify affected crates

**Files:**
- Modify: `src/bcs/docs/plans/2026-07-28-bcn-openapi-v1-design.md`
- Modify: `src/bcs/docs/plans/2026-07-28-bcn-openapi-v1-implementation.md`

**Interfaces:**
- Consumes: final `HumanPrincipal` and V1 DM behavior from Tasks 1 and 2.
- Produces: documentation that describes the implemented compatibility contract.

- [ ] **Step 1: Update the V1 design contract**

Document that Gateway supplies original Human identity, BCN derives
`human_<subject.id>`, `created_by` remains raw `subject.id`, tenant is not part
of the legacy identity key, and V1 retains `ensure_human_actor`.

- [ ] **Step 2: Update stale implementation examples**

Remove caller-provided Human `actor_id` from examples and replace the old
Gateway-normalizes-Human-actor statement with BCN application projection.

- [ ] **Step 3: Run focused affected-crate tests**

Run:

```bash
cargo test -p bcs-service-api --test v1_group_application_contracts
cargo test -p bcs-group-v1 --test v1_group_service
cargo test -p bcs-group-v1
```

Expected: all commands pass.

- [ ] **Step 4: Inspect the final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only the planned Principal, V1 Group,
tests, and documentation files are changed.
