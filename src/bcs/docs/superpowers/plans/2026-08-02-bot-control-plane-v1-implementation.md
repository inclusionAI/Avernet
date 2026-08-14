# Bot Control-plane OpenAPI V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` and `superpowers:test-driven-development`.

**Goal:** Implement the five approved Bot control-plane V1 operations as a
testable BCN vertical slice while preserving all Legacy routes.

**Architecture:** Add a versioned `BotService` contract, a narrow non-sensitive
control-plane store port, a new `bcs-app-bot` use-case crate, and thin
`bcs-api-http` routes. Keep production mounting out of this slice because the
trusted Gateway Principal transport is still a rollout prerequisite.

**Tech Stack:** Rust 1.91, Axum 0.8, Tokio, Serde, async-trait, existing DB and
cache plugin APIs, Python/PyYAML OpenAPI tests.

---

### Task 1: Define the versioned Bot application contract

**Files:**

- Create: `crates/service-api/bcs-service-api/src/application/v1/bot.rs`
- Modify: `crates/service-api/bcs-service-api/src/application/v1/mod.rs`
- Create: `crates/application/v1/bcs-app-bot/Cargo.toml`
- Create: `crates/application/v1/bcs-app-bot/src/lib.rs`
- Create: `crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`
- Modify: `Cargo.toml`

- [ ] Add a compile-time test using the five commands and `BotService` methods.
- [ ] Run `cargo test -p bcs-app-bot --test v1_bot_service` and confirm RED
  because the V1 Bot contract is absent.
- [ ] Define the serializable V1 Bot models, page/result types, commands,
  patches, filters, and `BotService` trait.
- [ ] Keep HTTP/Axum types out of the contract and application crate.
- [ ] Re-run the focused test until the contract compiles; behavioral tests may
  remain red until Task 3.

### Task 2: Add the narrow control-plane persistence boundary

**Files:**

- Create: `crates/service-api/bcs-service-api/src/port/repo/bot_control_plane.rs`
- Modify: `crates/service-api/bcs-service-api/src/port/repo/mod.rs`
- Modify: `crates/service-api/bcs-service-api/src/port/mod.rs`
- Modify: `crates/service-api/bcs-service-api/src/lib.rs`
- Modify: `crates/services/bcs-bot-store/src/lib.rs`
- Modify: `crates/services/bcs-bot-store/src/memory.rs`
- Create: `crates/services/bcs-bot-store/tests/conformance_bot_control_plane_repo.rs`

- [ ] Add conformance tests for exact/batch reads, both kinds, Unix-ms audit
  timestamps, first-occurrence ordering, candidate visibility/friendship/order,
  owned static filters, patch replacement semantics, and no credential fields.
- [ ] Run the new conformance test and confirm RED because the port and store
  implementations are absent.
- [ ] Implement the port records/queries and `PersistentBotRepo` mapping using
  dialect-aware `gmt_create/gmt_modified` projections.
- [ ] Implement the equivalent `MemoryBotRepo` behavior without changing
  Legacy query semantics.
- [ ] Apply name/visibility/status/descriptor patches in one store operation,
  refresh `updated_at`, and keep hot in-memory state synchronized.
- [ ] Run the conformance test until GREEN.

### Task 3: Implement Bot application behavior

**Files:**

- Modify: `crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify: `crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`

- [ ] Add failing tests for Human-only access, acting-Bot ownership/kind,
  candidates purposes, query ordering/omission, exact read, owner-only patch,
  Human descriptor rejection, mine filters, reachability, provider projection,
  timestamps, and error propagation.
- [ ] Implement `BotServiceImpl` over `BotControlPlaneRepoPort`,
  `BotRegistryCoreService`, `FriendCoreService`, `ProviderBotBindingRepoPort`,
  and `ProviderRepoPort`.
- [ ] Batch reachability and provider enrichment; never expose provider config,
  credentials, tokens, webhook URLs, or binding refs.
- [ ] Run `cargo test -p bcs-app-bot` until GREEN.

### Task 4: Add the five thin HTTP routes

**Files:**

- Create: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/bot.rs`
- Create: `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/mod.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/mod.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/common/state.rs`
- Create: `crates/adapters/http/bcs-api-http/tests/bot_routes.rs`
- Modify: existing `bcs-api-http` route test `ApiState::new` call sites

- [ ] Add route tests for all five paths and confirm RED before registering Bot
  routes/state.
- [ ] Add strict DTOs with defaults and limits matching the OpenAPI contract.
- [ ] Forward only verified Principal plus decoded commands to `BotService`.
- [ ] Return the common envelope and existing `ApplicationError` mapping.
- [ ] Update test state constructors with a no-op/fake Bot service.
- [ ] Run `cargo test -p bcs-api-http` until GREEN.

### Task 5: Boundary and regression verification

**Files:**

- Modify if needed: `crates/adapters/http/bcs-api-http/tests/boundary_contract.rs`
- Modify if needed: `tests/openapi/test_bot_v1_contract.py`

- [ ] Format only the Rust files changed by this implementation.
- [ ] Run `cargo test -p bcs-app-bot -p bcs-bot-store -p bcs-api-http`.
- [ ] Run `python -m pytest tests/openapi -q` and the custom OpenAPI validator.
- [ ] Inspect `git diff -- src/bcs` and confirm no Legacy HTTP behavior or
  non-BCS files changed.
- [ ] Run `git diff --check` and summarize any unrun broader gates explicitly.

### Task 6: Review checkpoint

- [ ] Self-review authorization, missing/deleted behavior, timestamp units,
  query ordering, descriptor replacement, and credential exclusion against the
  approved OpenAPI annotations.
- [ ] Use `superpowers:verification-before-completion` before reporting success.
- [ ] Do not commit or push unless the user explicitly requests it.
