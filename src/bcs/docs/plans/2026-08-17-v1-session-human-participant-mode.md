# V1 Session Human Participant Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow an authenticated Human to update their own Session participant mode and auto-join as an Observer when absent, while preserving current Bot management behavior.

**Architecture:** Evolve the OpenAPI and V1 Service API contracts to accept the complete participant-mode vocabulary. Keep transport parsing in `bcs-api-http`, enforce actor-aware authorization and mode validation in `bcs-app-session`, and delegate persistence through the existing `SessionManagementService` boundary.

**Tech Stack:** Rust, Axum, Serde, Tokio tests, YAML OpenAPI contract, pytest contract validation.

---

### Task 1: Evolve the input contract

**Files:**
- Modify: `src/bcs/api-contracts/v1/domain-models.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi/sessions.yaml`
- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/v1/session.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/v1_session_application_contracts.rs`

**Step 1:** Add a contract test asserting that the PATCH request mode is a union of `auto|muted` and `present|absent`.

**Step 2:** Run `uv run pytest src/bcs/tests/openapi/test_session_v1_contract.py -q` and confirm the new assertion fails because the request references `BotParticipantMode`.

**Step 3:** Add `HumanParticipantMode` and `SessionParticipantMode` schemas, reference the union from `UpdateSessionParticipantRequest`, and change `UpdateSessionParticipant.mode` to the existing Rust `ParticipantMode`.

**Step 4:** Replace the Service API test that rejects Human modes with round-trip coverage for all four `ParticipantMode` values.

**Step 5:** Run the OpenAPI and Service API contract tests and confirm they pass.

### Task 2: Update the HTTP adapter

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`

**Step 1:** Add a route test sending `{"mode":"present"}` and asserting that `ParticipantMode::Present` reaches the fake Session service.

**Step 2:** Run `cargo test -p bcs-api-http --test session_routes update_session_human_participant` from `src/bcs` and confirm it fails with HTTP 400.

**Step 3:** Change the request DTO and fake service to use `ParticipantMode` directly.

**Step 4:** Run the focused route tests and confirm both Human `present` and existing Bot `muted` cases pass.

### Task 3: Implement Human self-service and first-insert

**Files:**
- Modify: `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Modify: `src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs`

**Step 1:** Add failing application tests for:

- an existing Human changing their own mode;
- a missing Human being inserted as Observer with `present`;
- a Session manager being forbidden from modifying another Human;
- Human/Bot mode mismatches returning `invalid_participant_mode`;
- existing Bot mode update behavior remaining manager-authorized;
- a missing Bot not being auto-added.

**Step 2:** Run the focused tests and confirm failures arise from the missing Human-mode behavior and current manage-only authorization.

**Step 3:** Refactor `update_participant` to load the Session and parent Group once, branch on the target actor kind, enforce Human self/read authorization or Bot management authorization, validate `is_valid_for`, and first-insert only the authenticated Human.

**Step 4:** Remove the now-obsolete `map_v1_mode_to_domain` helper.

**Step 5:** Run `cargo test -p bcs-app-session --test v1_session_service` and confirm the complete application suite passes.

### Task 4: Verify the contract boundary

**Files:**
- Regenerate: `src/gateway/configs/schemas/bcn.openapi.json`
- Verify all modified files above.

**Step 1:** Run the OpenAPI contract validator and focused Python contract tests.

**Step 2:** Generate the Gateway OpenAPI snapshot twice with
`src/bcs/scripts/dump_openapi.py`, compare the temporary outputs, and replace
`src/gateway/configs/schemas/bcn.openapi.json` with the deterministic result.

**Step 3:** Run:

- `cargo test -p bcs-service-api --test v1_session_application_contracts`
- `cargo test -p bcs-api-http --test session_routes`
- `cargo test -p bcs-app-session --test v1_session_service`

**Step 4:** Run `git diff --check` and inspect the scoped diff for unrelated formatting or generated artifacts.

**Step 5:** Report the exact verification results and any tests that could not be run.
