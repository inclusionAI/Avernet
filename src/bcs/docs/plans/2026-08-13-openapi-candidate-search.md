# OpenAPI Candidate Search Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a BCN OpenAPI V1 candidate-search operation with behavior equivalent to legacy `GET /actors/search`.

**Architecture:** Add a separate `GET /bots/{bot_id}/candidates/search` contract and route so the existing pageable candidate list remains unchanged. Extend the V1 Bot application service to authorize the acting perspective, call the existing `ActorDirectoryService` directly, and project legacy search entries into secret-free V1 `PhysicalBot` search results.

**Tech Stack:** OpenAPI 3.1 YAML, Rust, Axum, async-trait, serde, pytest, Cargo tests.

---

### Task 1: Lock the public OpenAPI contract

**Files:**
- Modify: `src/bcs/tests/openapi/test_contract.py`
- Modify: `src/bcs/tests/openapi/test_bot_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi/bots.yaml`
- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`
- Modify: `src/bcs/api-contracts/README.md`

**Steps:**

1. Add failing tests requiring `GET /openapi/v1/collaboration/bots/{bot_id}/candidates/search`, operation ID `search_bot_candidates`, parameters `bot_id`, `q`, and `purpose`, and the legacy-to-OpenAPI purpose mapping.
2. Assert that routing-only legacy parameters (`ctoken`, `current_bot_uuid`, `cooperatable_only`) are absent and that the response contains physical Bot items with `is_friend`, `tags`, optional score/profile enrichment, and recommendation context.
3. Run `uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi/test_contract.py src/bcs/tests/openapi/test_bot_v1_contract.py -q` and confirm failure because the operation is absent.
4. Add the path item and strict reusable schemas, update the approved operation count and contract README.
5. Rerun the same tests and the OpenAPI validator; confirm success.

### Task 2: Add the V1 application contract and projection

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/bot.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-bot/tests/conformance_bot_service.rs`

**Steps:**

1. Add failing application tests proving a managed Bot and current Human Actor map `q` and `purpose` to `ActorSearchCommand`, preserve recommendation order and metadata, and reject an unauthorized perspective before search.
2. Run the targeted `bcs-app-bot` tests and confirm compile/test failure because the search command and method do not exist.
3. Add `SearchBotCandidates`, `BotCandidateSearchItem`, and `BotCandidateSearchResult` to the V1 application contract and extend `BotService` with `search_candidates`.
4. Inject `ActorDirectoryService` into `BotServiceImpl`, extract the shared perspective-authorization helper, call legacy search with fixed limit 20, hydrate result IDs through the control plane, and construct the V1 projection in legacy ranking order.
5. Update existing constructors with explicit no-op or fake actor-directory services.
6. Run `cargo test -p bcs-app-bot` and confirm success.

### Task 3: Add and wire the HTTP route

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/bot.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`

**Steps:**

1. Add a failing route test for `/bots/human_staff-1/candidates/search?q=review&purpose=collaboration`, including verified Principal forwarding and unknown-query rejection.
2. Run `cargo test -p bcs-api-http --test bot_routes` and confirm failure because the route is absent.
3. Add a strict query DTO, mount the route, map it to `SearchBotCandidates`, and return the standard V1 envelope.
4. Update the fake `BotService` to record search commands.
5. Pass the existing composed `ActorDirectoryService` into `BotServiceImpl` in every bootstrap profile.
6. Run the route tests and `cargo check -p bcs`; confirm success.

### Task 4: Regenerate the Gateway contract snapshot and verify

**Files:**
- Modify: `src/gateway/configs/schemas/bcn.openapi.json`

**Steps:**

1. Validate the source OpenAPI contract.
2. Regenerate the deterministic Gateway BCN OpenAPI JSON snapshot using the repository script.
3. Run the complete OpenAPI tests, targeted Rust suites, architecture checks, and `git diff --check`.
4. Inspect the final diff for unrelated formatting or generated artifacts.
5. Commit with `feat(bcs): expose OpenAPI candidate search` after all verification succeeds.
