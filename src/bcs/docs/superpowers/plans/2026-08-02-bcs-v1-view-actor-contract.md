# BCS V1 Human Caller Integration Implementation Plan

> **Execution:** Use `superpowers:executing-plans` in this worktree and apply
> `superpowers:test-driven-development` within each behavior-changing task.

**Goal:** Carry the complete Gateway-authenticated caller through all 32 BCS
V1 operations, admit only callers with User identity, and implement the
approved Human, View Actor, owned-Bot, and detail-read authorization rules.

**Architecture:** The HTTP verifier authenticates the signed
`X-Avernet-Principal` JWT and stores `AuthenticatedCaller`. Delivery DTOs pass
that caller unchanged. Application use cases project only `caller.user` to the
existing Human `Principal` for domain authorization. Core and persistence stay
transport-neutral. Exact `bot.created_by == caller.user.id` is the only V1 Bot
ownership rule.

**Tech stack:** Rust, Axum, async-trait, existing BCS Application/Core ports,
OpenAPI 3.1 YAML, pytest/PyYAML Contract tests, Cargo tests.

## Binding behavior

- All 32 operations declare `x-avernet-security: {user: required}`.
- A valid caller without User is rejected by Application with Forbidden.
- User plus Bot/App/AccessKey behaves exactly like the same User alone.
- Group list is `GET /openapi/v1/collaboration/groups` and uses the same
  optional `view_bot_id` selector as Session list and Session history.
- On every View Actor read, omission means `human_<user.id>`; an explicit Human
  must be that same Actor; an explicit Bot must have exact `created_by`
  ownership.
- Group/Session detail has no `view_bot_id`. Read succeeds when the resource
  participants contain either the current Human Actor or a Bot created by the
  current User. This does not grant mutation or message-view authority.
- Creator relation edges may remain collaboration-eligibility evidence where
  already defined, but never count as ownership.
- Legacy HTTP and CLI behavior is unchanged.

---

### Task 1: Finalize and validate the Contract

**Files:**
- Modify `src/bcs/api-contracts/v1/openapi.yaml`
- Modify `src/bcs/api-contracts/v1/shared.yaml`
- Modify `src/bcs/api-contracts/v1/openapi/{bots,friendships,groups,invitations,sessions}.yaml`
- Modify `src/bcs/tests/openapi/test_contract.py`
- Modify `src/bcs/tests/openapi/test_bot_v1_contract.py`
- Modify `src/bcs/tests/openapi/test_group_v1_contract.py`
- Create `src/bcs/tests/openapi/test_session_v1_contract.py`
- Modify `src/bcs/docs/superpowers/specs/2026-08-02-bcs-v1-human-caller-integration-design.md`

- [x] Add failing tests for the Gateway security marker and detail-read rules.
- [x] Use `user: required` on all 32 operations.
- [x] Expose `view_bot_id` on Group list, Session list, and Session history with shared View Actor semantics.
- [x] Document Human/owned-Bot participant access for Group and Session detail.
- [x] Run all OpenAPI tests, validation, and deterministic bundling.

### Task 2: Change the Service API caller boundary

**Files:**
- Modify `src/bcs/crates/service-api/bcs-service-api/src/application/v1/authorization.rs`
- Modify `src/bcs/crates/service-api/bcs-service-api/src/application/v1/{bot,friendship,group,invitation,message,session}.rs`
- Add or modify focused tests under `src/bcs/crates/service-api/bcs-service-api/tests/`

- [ ] Write tests for `require_human`: User-only and multi-identity callers
  project identically; callers without User return Forbidden.
- [ ] Add the transport-neutral Human projection and raw User-ID helpers.
- [ ] Replace every command/query `principal: Principal` with
  `caller: AuthenticatedCaller`.
- [ ] Use `ListGroups`/`list_groups` with optional `view_bot_id` for Group list,
  sharing the Session list/history View Actor contract.
- [ ] Update message-history documentation to the Human-default participant
  semantics.
- [ ] Run the Service API crate tests and formatting check.

### Task 3: Change the HTTP verification and propagation boundary

**Files:**
- Modify `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/principal.rs`
- Modify `src/bcs/crates/adapters/http/bcs-api-http/src/v1/gateway_principal/{mod,verifier}.rs`
- Modify `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/{bot,friendship,group,invitation,session}.rs`
- Modify `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/{bot,friendship,group,invitation,session}.rs`
- Modify route tests under `src/bcs/crates/adapters/http/bcs-api-http/tests/`

- [ ] Write failing verifier tests for missing, duplicate, non-UTF-8, blank,
  and invalid signed `X-Avernet-Principal`, plus successful full-caller output.
- [ ] Make `PrincipalVerifier` return `AuthenticatedCaller` and insert it into
  Axum extensions.
- [ ] Add the production header adapter that extracts exactly one compact JWT
  and delegates cryptographic verification.
- [ ] Change all route extractors and DTO command builders to pass full caller.
- [ ] Keep Group list routing at `GET /openapi/v1/collaboration/groups`, expose
  optional `view_bot_id`, and keep `view_bot_id` off detail routes.
- [ ] Run the HTTP adapter route and Gateway-principal tests.

### Task 4: Enforce Human admission and exact Bot ownership

**Files:**
- Modify `src/bcs/crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify `src/bcs/crates/application/v1/bcs-app-friendship/src/lib.rs`
- Modify `src/bcs/crates/application/v1/bcs-app-invitation/src/lib.rs`
- Modify their focused test suites.

- [ ] Add failing tests for non-User callers and User-plus-extra-identity callers.
- [ ] Project Human explicitly in every public use case.
- [ ] Make Bot and Friendship ownership checks use only exact `created_by`.
- [ ] Ensure invitation acceptance joins as `human_<user.id>` and never falls
  back to a Bot identity.
- [ ] Run all three Application crate test suites.

### Task 5: Implement Group View Actor and detail-read rules

**Files:**
- Modify `src/bcs/crates/application/v1/bcs-app-group/src/lib.rs`
- Modify `src/bcs/crates/application/v1/bcs-app-group/tests/v1_group_service.rs`

- [ ] Write failing tests for path Bot ownership, invalid/unowned path Bots,
  pre-pagination filtering, no `view_bot_id`, and empty pages.
- [ ] Resolve the list Actor from the path `bot_id` with exact Bot ownership and no fallback.
- [ ] Write failing detail tests for direct Human participant, owned-Bot
  participant, unrelated participants, and creator-relation-only non-ownership.
- [ ] Implement implicit detail access from Human plus exact-created Bots.
- [ ] Keep Group mutation authorization on the Human Actor only.
- [ ] Run the Group Application suite.

### Task 6: Implement Session list, detail, and history rules

**Files:**
- Modify `src/bcs/crates/application/v1/bcs-app-session/src/lib.rs`
- Modify `src/bcs/crates/application/v1/bcs-app-session/tests/v1_session_service.rs`

- [ ] Write failing list tests for the three View Actor branches, ownership
  rejection, participant-only filtering, total-before-pagination, and empty pages.
- [ ] Implement list scoping by the effective participant Actor.
- [ ] Write failing detail tests matching the Human-or-owned-Bot participant rule.
- [ ] Implement implicit detail access without exposing a message perspective.
- [ ] Write failing history tests proving omission equals Human self view,
  manager/creator has no fallback view, and owned Bot must be a participant.
- [ ] Remove creator-edge ownership from session self-service operations and
  implement the approved history visibility/cutoff behavior.
- [ ] Keep all Session mutation authorization on the Human Actor only.
- [ ] Run the Session Application suite.

### Task 7: Integration regression and architecture verification

**Files:**
- Modify only tests or implementation files directly required by failures.

- [ ] Run `cargo fmt --check` for the touched BCS packages/files.
- [ ] Run focused tests for Service API, five V1 Application crates, and
  `bcs-api-http`.
- [ ] Run `cargo test --workspace` if practical; otherwise record the exact
  remaining gate and reason.
- [ ] Re-run OpenAPI tests, validation, bundling, and `git diff --check`.
- [ ] Run the applicable architecture/BCS boundary checks from the repository
  instructions and inspect the final diff for Legacy API or CLI changes.
