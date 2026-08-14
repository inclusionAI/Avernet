# BCS Bot Control-Plane Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move V1 Bot persistence orchestration out of `bcs-app-bot` and behind a separate `BotControlPlaneCoreService` implementation without changing HTTP or domain behavior.

**Architecture:** `bcs-service-api` declares shared transport-agnostic control-plane values under `types`, hydrated Core views, and the `BotControlPlaneCoreService` trait. A new `BotControlPlaneCore` in the existing `bcs-bot` crate consumes the three repository ports and hydrates Provider summaries. `bcs-app-bot` calls this Core service while retaining Human authorization, V1 projection, friendship context, and runtime reachability policy.

**Tech Stack:** Rust, `async-trait`, BCS Service API/Core/Repo contracts, Tokio tests, Cargo workspace.

## Global Constraints

- Do not add a crate, Cargo package, workspace member, database schema, or configuration source.
- Do not add responsibilities or repository dependencies to the existing `BotCore`.
- Production `bcs-app-bot` code must not import, store, or call any `*RepoPort`.
- Preserve all V1 HTTP schemas, Principal semantics, authorization rules, ordering, pagination, Provider projection, and reachability behavior.
- Do not add an onboarding predicate or change corrupted persisted-data compatibility.
- Do not run `cargo fmt`; restrict formatting changes to touched lines.
- Use TDD: observe the intended test failure before writing production implementation.

---

### Task 1: Add the Core contract and independent implementation

**Files:**
- Create: `src/bcs/crates/service-api/bcs-service-api/src/core/bot_control_plane.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/core/mod.rs`
- Create: `src/bcs/crates/service-api/bcs-service-api/src/types/bot_control_plane.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/types/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/port/repo/bot_control_plane.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/port/repo/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/port/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-bot/src/core/bot_control_plane_core.rs`
- Modify: `src/bcs/crates/services/bcs-bot/src/core/mod.rs`
- Modify: `src/bcs/crates/services/bcs-bot/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-bot/tests/bot_control_plane_core.rs`

**Interfaces:**
- Consumes: existing `BotControlPlaneRepoPort`, `ProviderRepoPort`, and `ProviderBotBindingRepoPort`.
- Produces: `BotControlPlaneCoreService`, `BotControlPlaneCore`, `BotControlPlaneView`, `BotControlPlaneProvider`, and `BotControlPlaneCandidate`.

- [x] **Step 1: Write the failing Core behavior test**

Create a test using real `MemoryBotRepo` and `MemoryProviderStore`. Seed one Human row and two physical Bots in a deliberate request order, bind one physical Bot to a Provider, then assert:

```rust
let views = core
    .get_by_ids(
        &["human_staff-1".to_string(), "provider-bot".to_string(), "local-bot".to_string()],
        &env,
    )
    .await
    .expect("query control-plane views");

assert_eq!(
    views.iter().map(|view| view.record.bot_id.as_str()).collect::<Vec<_>>(),
    vec!["human_staff-1", "provider-bot", "local-bot"]
);
assert_eq!(
    views[1].provider.as_ref().map(|provider| provider.provider_id.as_str()),
    Some("provider-1")
);
assert!(views[0].provider.is_none());
assert!(views[2].provider.is_none());
```

The test must also call `list_candidates`, `list_by_creator`, and `patch` once so the new Core boundary is exercised rather than only its hydration helper.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-bot --test bot_control_plane_core
```

Expected: compilation fails because `BotControlPlaneCore` and `BotControlPlaneCoreService` do not exist.

- [x] **Step 3: Define Core-facing types and the Core service trait**

Move the transport-agnostic descriptor, record, query, candidate-query, owned-query, and patch types from `port::repo::bot_control_plane` into `types::bot_control_plane`, preserving their public root re-exports. Define the hydrated Core views and service trait in `core::bot_control_plane`:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneProvider {
    pub provider_id: String,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneView {
    pub record: BotControlPlaneRecord,
    pub provider: Option<BotControlPlaneProvider>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BotControlPlaneCandidate {
    pub bot: BotControlPlaneView,
    pub is_friend: bool,
}

#[async_trait]
pub trait BotControlPlaneCoreService: Send + Sync {
    async fn get_record(&self, bot_id: &str, env: &str)
        -> ServiceResult<Option<BotControlPlaneRecord>>;
    async fn get(&self, bot_id: &str, env: &str)
        -> ServiceResult<Option<BotControlPlaneView>>;
    async fn get_by_ids(&self, bot_ids: &[String], env: &str)
        -> ServiceResult<Vec<BotControlPlaneView>>;
    async fn list_candidates(&self, query: BotCandidateReadQuery)
        -> ServiceResult<(Vec<BotControlPlaneCandidate>, u64)>;
    async fn list_by_creator(&self, query: BotControlPlaneOwnedQuery)
        -> ServiceResult<Vec<BotControlPlaneView>>;
    async fn patch(&self, bot_id: &str, env: &str, patch: BotControlPlanePatch)
        -> ServiceResult<Option<BotControlPlaneView>>;
}
```

Keep `BotControlPlaneRepoPort` under `port::repo`; it imports the shared query and record values from `types` and retains its existing persistence method signatures. This avoids a forbidden `port -> core` reverse dependency.

- [x] **Step 4: Implement `BotControlPlaneCore`**

Create a focused struct with only the three repository dependencies:

```rust
pub struct BotControlPlaneCore {
    control_plane: Arc<dyn BotControlPlaneRepoPort>,
    providers: Arc<dyn ProviderRepoPort>,
    provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
}
```

Implement a private `hydrate(Vec<BotControlPlaneRecord>)` method that:

1. selects physical Bot IDs;
2. batch-loads bindings;
3. deduplicates and batch-loads Provider records;
4. builds lookup maps; and
5. returns `BotControlPlaneView` values in the input order.

Implement all six Core methods by delegating to `BotControlPlaneRepoPort`. Keep `get_record` unhydrated for authorization-before-Provider error ordering, and hydrate the response-facing methods. Preserve candidate `is_friend` values and totals exactly.

- [x] **Step 5: Run focused Core and store tests and verify GREEN**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-bot --test bot_control_plane_core
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-bot-store --test conformance_bot_control_plane_repo
```

Expected: both commands pass.

### Task 2: Rewire the V1 Application behind the Core boundary

**Files:**
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/conformance_bot_service.rs`
- Create: `src/bcs/crates/application/v1/bcs-app-bot/tests/layering_contract.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/CONTEXT.md`

**Interfaces:**
- Consumes: `Arc<dyn BotControlPlaneCoreService>`, `Arc<dyn BotRegistryCoreService>`, and `Arc<dyn FriendCoreService>`.
- Produces: a `BotServiceImpl` constructor with no repository-port parameters and unchanged `BotService` behavior.

- [x] **Step 1: Write the failing layering and constructor tests**

Add a layering contract that reads production `src/lib.rs` and rejects the concrete forbidden dependency names:

```rust
for forbidden in [
    "BotControlPlaneRepoPort",
    "ProviderRepoPort",
    "ProviderBotBindingRepoPort",
] {
    assert!(
        !source.contains(forbidden),
        "bcs-app-bot production code must depend on Core services, not {forbidden}",
    );
}
```

Update the test fixture to construct a real `BotControlPlaneCore` and pass it to `BotServiceImpl::new`. This intentionally fails to compile before the Application constructor changes.

- [x] **Step 2: Run Application tests and verify RED**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-app-bot
```

Expected: the layering contract reports direct RepoPort dependencies and/or the fixture fails because the constructor still accepts repository ports.

- [x] **Step 3: Replace repository dependencies with the Core service**

Change `BotServiceImpl` to hold:

```rust
control_plane: Arc<dyn BotControlPlaneCoreService>,
registry: Arc<dyn BotRegistryCoreService>,
friends: Arc<dyn FriendCoreService>,
config: BotServiceConfig,
```

Change record projection to accept `Vec<BotControlPlaneView>`. Use the hydrated Provider summary from each view instead of loading bindings and Providers inside the Application service. Use `get_record` for acting-Bot and update authorization so denial still precedes Provider hydration; delegate response-facing `get`, `get_by_ids`, `list_candidates`, `list_by_creator`, and `patch` to the Core service while preserving all existing validation, ownership, kind, reachability, and pagination branches.

Update both test fixtures to create `BotControlPlaneCore::new(repo, providers.clone(), providers)` and pass the resulting Core trait object.

- [x] **Step 4: Update the crate context boundary**

State that `bcs-app-bot` consumes application and Core contracts plus non-repository ports only, and explicitly forbid direct repository-port dependencies in production code.

- [x] **Step 5: Run Application and V1 HTTP route tests and verify GREEN**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-app-bot
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-api-http --test bot_routes
```

Expected: all tests pass with unchanged V1 behavior.

### Task 3: Verify the affected boundary and address the review thread

**Files:**
- Verify: `src/bcs/crates/service-api/bcs-service-api`
- Verify: `src/bcs/crates/services/bcs-bot`
- Verify: `src/bcs/crates/services/bcs-bot-store`
- Verify: `src/bcs/crates/application/v1/bcs-app-bot`
- Verify: `src/bcs/crates/adapters/http/bcs-api-http`

**Interfaces:**
- Consumes: the completed Core contract and Application rewiring.
- Produces: test evidence and a resolved first PR review thread.

- [x] **Step 1: Run focused package checks**

Run:

```bash
cargo check --manifest-path src/bcs/Cargo.toml --package bcs-service-api --all-targets
cargo check --manifest-path src/bcs/Cargo.toml --package bcs-bot --all-targets
cargo check --manifest-path src/bcs/Cargo.toml --package bcs-app-bot --all-targets
```

Expected: all commands pass without warnings introduced by this change.

- [x] **Step 2: Run affected tests**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-service-api
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-bot
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-bot-store --test conformance_bot_control_plane_repo
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-app-bot
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-api-http --test bot_routes
```

Expected: all commands pass.

- [x] **Step 3: Inspect the final diff**

Run:

```bash
git diff --check
git diff --stat
git status --short
```

Expected: no whitespace errors, no unrelated files, no generated artifacts, and no direct RepoPort references in production `bcs-app-bot`.

- [ ] **Step 4: Reply to and resolve the first PR review thread**

Reply in the existing inline thread with a concise summary that persistence orchestration and Provider hydration now live behind the independent `BotControlPlaneCoreService` implementation, while Application behavior remains unchanged. Resolve the thread only after all required checks pass.
