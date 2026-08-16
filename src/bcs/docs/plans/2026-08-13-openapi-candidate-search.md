# OpenAPI Candidate Search Core Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor candidate search into a shared Core used independently by legacy and OpenAPI V1 applications, while making `q` optional and removing raw BCSFuse data from the V1 contract.

**Architecture:** Extract recommendation, visibility, friendship, enrichment, ordering, and name-fallback policy from `ActorDirectoryService` into a narrow `BotCandidateSearchCoreService`. Legacy actor-directory and V1 Bot applications both depend on this Core and own only their authorization and response projections. HTTP adapters remain transport-only, and the BCSFuse implementation stays selected by bootstrap.

**Tech Stack:** Rust, async-trait, Axum, serde, OpenAPI 3.1 YAML, pytest, Cargo tests.

---

### Task 1: Define the shared candidate-search Core contract

**Files:**
- Create: `src/bcs/crates/service-api/bcs-service-api/src/core/candidate_search.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/core/mod.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/actor_directory.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/lib.rs`
- Test: `src/bcs/crates/service-api/bcs-service-api/tests/`

**Steps:**

1. Add a compile-time contract test that imports a candidate-search Core query, result, hit, and mode plus the worker-profile Core contract from `bcs_service_api::core`.
2. Run the focused `bcs-service-api` test and confirm it fails because those Core contracts do not exist.
3. Define the Core input using normalized query text, acting Actor ID, collaboration/discovery visibility intent, and limit.
4. Define normalized result modes `empty_query`, `semantic`, and `name_fallback` and hits containing `RegisteredBot`, friendship, tags, optional score, and optional short profile.
5. Move the worker recommendation/profile contract types out of the legacy application module into Core. Preserve root re-exports needed by existing consumers while removing application ownership of those contracts.
6. Keep an explicitly internal legacy-compatibility field for the opaque recommendation response; document that it must never enter OpenAPI V1.
7. Run the focused test and `cargo check -p bcs-service-api`; confirm success.
8. Commit only the service contract changes with `refactor(bcs): define candidate search core contract`.

### Task 2: Implement the shared candidate-search Core with TDD

**Files:**
- Create: `src/bcs/crates/services/bcs-bot/src/core/candidate_search_core.rs`
- Modify: `src/bcs/crates/services/bcs-bot/src/core/mod.rs`
- Modify: `src/bcs/crates/services/bcs-bot/src/lib.rs`
- Create: `src/bcs/crates/services/bcs-bot/tests/candidate_search_core.rs`

**Steps:**

1. Add a test proving missing-normalized/empty query returns no hits with `empty_query` mode and invokes neither recommendation nor profile lookup.
2. Add a test proving semantic recommendations preserve provider order, exclude the acting Actor and Human rows, apply discovery visibility, attach friendship/tags/profile data, and retain a real score including `0.0` when supplied by the provider.
3. Add a collaboration test proving public Bots and accepted private/protected friends are included while non-friend non-public Bots are excluded.
4. Add fallback tests for recommendation failure, empty recommendations, and recommendations entirely removed by visibility/registry resolution.
5. Assert fallback uses trimmed case-insensitive name matching, has `name_fallback` mode, and produces `score: None` rather than synthetic zero.
6. Run the new tests and confirm they fail because the Core implementation is absent.
7. Move the reusable logic from `ActorDirectory` into the new Core without changing the underlying registry/friend/profile contracts or fallback policy.
8. Ensure profile-tag failure degrades to empty tags and recommendation failure degrades to name search, matching legacy behavior.
9. Run `cargo test -p bcs-bot --test candidate_search_core`; confirm all new tests pass.
10. Commit with `refactor(bcs): extract candidate search core`.

### Task 3: Make BCSFuse implement the Core worker-profile contract

**Files:**
- Modify: `src/bcs/crates/services/bcs-fusion/src/core/fuse_backed_worker_profiles.rs`
- Modify: `src/bcs/crates/services/bcs-fusion/src/core/mod.rs`
- Modify: `src/bcs/crates/services/bcs-fusion/src/lib.rs`
- Modify: `src/bcs/crates/services/bcs-fusion/tests/conformance_fuse_worker_profile_service.rs`
- Modify: `src/bcs/crates/test-support/bcs-test-support/src/noop.rs`
- Modify: `src/bcs/crates/test-support/bcs-test-support/src/contract/application/mod.rs`

**Steps:**

1. Update the conformance test to target the Core worker-profile contract and assert `query`, `top_k`, and `min_score` are forwarded to BCSFuse.
2. Run the conformance test and confirm it fails until the implementation is moved to the Core contract.
3. Update `FuseWorkerProfileService` and no-op implementations to implement the Core contract. A type rename is optional; avoid renaming unless it materially improves clarity.
4. Remove the obsolete application-level worker-profile conformance hook.
5. Run `cargo test -p bcs-fusion --test conformance_fuse_worker_profile_service` and `cargo check -p bcs-test-support`; confirm success.
6. Commit with `refactor(bcs): move worker profiles to core contract`.

### Task 4: Convert legacy ActorDirectory into a Core consumer

**Files:**
- Modify: `src/bcs/crates/services/bcs-bot/src/application/actor_directory.rs`
- Modify: `src/bcs/crates/services/bcs-bot/tests/actor_directory.rs`
- Modify: `src/bcs/crates/services/bcs-bot/tests/conformance_application_services.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/tests/route_contract.rs`
- Verify: `src/bcs/crates/adapters/http/bcs-http/src/routes/actors.rs`

**Steps:**

1. Extend legacy tests to lock existing `/actors/search` behavior for empty query, semantic order/score/profile/tags/context, name fallback with `score: 0`, dynamic status, downlink flag, and fallback skill summary.
2. Run the focused legacy tests before refactoring and record the passing baseline.
3. Inject `BotCandidateSearchCoreService` into `ActorDirectory` and replace its private semantic/fallback orchestration with one Core call.
4. Keep `/actors/list`, its tag enrichment, and `update_actor_status_for_caller` behavior unchanged.
5. Project Core modes back into the legacy DTO: semantic scores pass through; name fallback restores legacy `score: 0` and skill-summary profile; the internal compatibility response remains only under legacy `context.recommend_response`.
6. Remove recommendation/search helpers that became dead after extraction, but do not refactor unrelated list or status code.
7. Run `cargo test -p bcs-bot --test actor_directory`, `cargo test -p bcs-bot --test conformance_application_services`, and the focused `bcs-http` route contract test; confirm compatibility.
8. Commit with `refactor(bcs): route legacy actor search through core`.

### Task 5: Make the V1 application call Core directly

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/bot.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/conformance_bot_service.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/Cargo.toml`

**Steps:**

1. Replace recording legacy-application fakes with a recording candidate-search Core fake.
2. Add failing tests proving V1 calls Core only after Human perspective authorization and maps missing, empty, and whitespace-only queries to the same empty-query behavior.
3. Add tests proving semantic and name-fallback order are preserved after control-plane hydration, every item uses the complete `PhysicalBot` projection, and Human candidates cannot enter the response.
4. Add response assertions that raw recommendation context is absent, `search_mode` is explicit, semantic score is preserved, and fallback score is omitted.
5. Run `cargo test -p bcs-app-bot` and confirm failures against the current Application-to-Application implementation.
6. Replace `ActorDirectoryService` in `BotServiceImpl` with `BotCandidateSearchCoreService`.
7. Make V1 `SearchBotCandidates.query` optional and normalize absence/empty/whitespace consistently before the Core call.
8. Remove `BotCandidateSearchContext`; add the normalized search-mode field to `BotCandidateSearchResult`.
9. Keep V1 ownership, Human Actor authorization, control-plane hydration, Provider enrichment, reachability, and result-order restoration in the V1 application.
10. Remove the no-longer-needed `serde_json` dev dependency if no test still needs it.
11. Run `cargo test -p bcs-app-bot`; confirm success.
12. Commit with `refactor(bcs): call candidate search core from v1`.

### Task 6: Fix the OpenAPI and HTTP query/response contracts

**Files:**
- Modify: `src/bcs/tests/openapi/test_bot_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi/bots.yaml`
- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`
- Modify: `src/bcs/api-contracts/README.md`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/bot.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs`

**Steps:**

1. Change contract tests so `q` is optional and has no non-empty constraint; assert omitted `q`, `q=`, and whitespace are accepted by route tests.
2. Assert the V1 response contains `items` and `search_mode`, uses the complete shared `PhysicalBot` schema, and has no `context`, `recommend_response`, or other raw BCSFuse fields.
3. Assert `search_mode` is exactly `empty_query | semantic | name_fallback` and fallback `score` is optional/omitted.
4. Run the focused OpenAPI and route tests and confirm failures against the current contract.
5. Update the OpenAPI source and Rust query DTO so `q` is optional and forwarded as optional input to the V1 application.
6. Update the response DTO/schema and README to document empty-query and normalized search-mode semantics.
7. Keep the adapter limited to parsing, Principal extraction, Application invocation, error mapping, and envelope serialization.
8. Run `uv run pytest src/bcs/tests/openapi/test_bot_v1_contract.py -q` and `cargo test -p bcs-api-http --test bot_routes`; confirm success.
9. Commit with `fix(bcs): normalize candidate search contract`.

### Task 7: Wire one shared Core in every composition profile

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify constructor call sites found by `rg -n 'ActorDirectory::new\(|BotServiceImpl::new\(' src/bcs --glob '*.rs'`

**Steps:**

1. Add a bootstrap-level compile or mount test that requires both applications to be built with the shared candidate-search Core.
2. Run `cargo check -p bcs` and confirm constructor failures until wiring is updated.
3. Construct one worker-profile provider and one candidate-search Core per application graph, applying `bcsfuse.recommend_min_score` exactly once.
4. Inject the same Core instance into legacy `ActorDirectory` and V1 `BotServiceImpl` in standalone, Singlebox, and other bootstrap profiles.
5. Preserve provider selection in bootstrap: configured BCSFuse provider when available, no-op provider otherwise.
6. Run `cargo check -p bcs` and the relevant bootstrap mount tests; confirm success.
7. Commit with `refactor(bcs): share candidate search core in bootstrap`.

### Task 8: Regenerate contracts and perform final regression verification

**Files:**
- Modify generated artifact: `src/gateway/configs/schemas/bcn.openapi.json`
- Verify all files changed from `origin/dev...HEAD`

**Steps:**

1. Run only narrow formatting for touched Rust files/packages; do not run workspace-wide formatting.
2. Validate the source contract:

   ```bash
   uv run python src/bcs/scripts/validate_openapi_contract.py --root src/bcs/api-contracts/v1
   ```

3. Run all BCS OpenAPI tests:

   ```bash
   uv run pytest src/bcs/tests/openapi -q
   ```

4. Regenerate `src/gateway/configs/schemas/bcn.openapi.json` with `src/bcs/scripts/dump_openapi.py`, regenerate a second temporary copy, and use `cmp` to prove determinism.
5. Run the focused Rust suites:

   ```bash
   cd src/bcs
   cargo test -p bcs-bot
   cargo test -p bcs-fusion
   cargo test -p bcs-app-bot
   cargo test -p bcs-api-http --test bot_routes
   cargo check -p bcs
   ```

6. Run the Gateway schema publication tests affected by the regenerated artifact.
7. Audit dependency direction with `rg`: V1 Bot Application must not reference `ActorDirectoryService`; both applications must reference `BotCandidateSearchCoreService`; HTTP adapters must not reference registry, friendship, control-plane, or BCSFuse implementations.
8. Confirm the generated V1 schema contains no `recommend_response`, accepts omitted `q`, and exposes the complete `PhysicalBot` plus normalized search mode.
9. Run `git diff --check` and inspect `git status --short`. Ignore the pre-existing unrelated PR-history/session-plan commits as requested, but do not add further unrelated changes.
10. Commit any final generated/test adjustments with `test(bcs): verify shared candidate search core`.
