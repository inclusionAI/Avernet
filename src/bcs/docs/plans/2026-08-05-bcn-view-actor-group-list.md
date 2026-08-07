# BCN View Actor Group List Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align group list with the same View Actor selector semantics used by session list/history, while documenting and testing that session creation inherits Human participants from the parent Group.

**Architecture:** Public group list returns to `GET /openapi/v1/collaboration/groups` with optional `view_bot_id`. The application layer resolves omitted `view_bot_id` to the authenticated Human actor, explicit self-Human to that Human actor, and explicit owned Bot to that Bot actor. Session creation continues to snapshot the parent Group roster, including Human participants, and the contract/docs make that intentional.

**Tech Stack:** Rust Axum HTTP adapter, BCS application services, YAML OpenAPI contracts, shell E2E story.

---

### Task 1: Contract and docs

**Files:**
- Modify: `api-contracts/v1/openapi.yaml`
- Modify: `api-contracts/v1/openapi/groups.yaml`
- Modify: `api-contracts/v1/openapi/sessions.yaml`
- Modify: `docs/superpowers/specs/2026-08-02-bcs-v1-human-caller-integration-design.md`
- Modify: `docs/superpowers/plans/2026-08-02-bcs-v1-view-actor-contract.md`

**Steps:**
1. Remove the public `/bots/{bot_id}/groups` path from the OpenAPI root.
2. Put `GET list_groups` back under `/groups` alongside `POST create_group`.
3. Add the shared `ViewBotIdQuery` parameter to group list and document omitted/self-Human/owned-Bot semantics.
4. Document that create-session inherits parent Group participants, including Human participants.

### Task 2: HTTP and application code

**Files:**
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/group.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/dto/group.rs`
- Modify: `crates/service-api/bcs-service-api/src/application/v1/group.rs`
- Modify: `crates/application/v1/bcs-app-group/src/lib.rs`
- Modify: `scripts/e2e-test/stories.sh`

**Steps:**
1. Mount `GET /groups` for list and keep `POST /groups` for create.
2. Add `view_bot_id` to the group list query DTO and application command.
3. Resolve group list via `resolve_view_actor(caller, view_bot_id.as_deref())`.
4. Restore create-group participant roles in the E2E body.

### Task 3: Tests

**Files:**
- Modify: `crates/application/v1/bcs-app-group/tests/v1_group_service.rs`
- Modify: `crates/application/v1/bcs-app-session/tests/v1_session_service.rs`

**Steps:**
1. Add group-list tests for default Human, explicit self-Human, owned Bot, other Human forbidden, and unowned Bot forbidden.
2. Add session-create test proving Human Group participants are inherited into Session participants.

### Task 4: Verification

Run targeted checks:

```bash
bash -n scripts/e2e-test/stories.sh
cargo test -p bcs-app-session create_session
cargo test -p bcs-app-group list
cargo test -p bcs-api-http --test group_routes
git diff --check
```

Do not run `cargo fmt` or any global formatter.
