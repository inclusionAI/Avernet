# OpenAPI Human Actor Authority Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let OpenAPI v1 Human callers manage groups/sessions when they can act as the relevant Human/Bot actor, using targeted batch checks instead of listing every created bot.

**Architecture:** Keep auth policy in v1 application facades; HTTP adapters continue to pass authenticated caller only. Contract YAML documents the Service API behavior. Application tests cover the behavior with in-memory dependencies.

**Tech Stack:** Rust application crates (`bcs-app-group`, `bcs-app-session`), OpenAPI YAML contracts, Cargo tests.

---

### Task 1: Contract and docs

**Files:**
- Modify: `api-contracts/v1/openapi/groups.yaml`
- Modify: `api-contracts/v1/openapi/sessions.yaml`

**Steps:**
1. Document that mutating group/session participant endpoints require a Human caller that can act as a management actor (driver/originator/manager/session creator) or, for self-service participant updates, as the target actor.
2. Define `can act as`: direct `human_{user.id}`, bot `created_by == user.id`, or creator relation edge from `human_{user.id}` to the bot actor.
3. State the implementation uses target actor checks and does not require enumerating every bot created by the Human.

### Task 2: Failing group tests

**Files:**
- Modify: `crates/application/v1/bcs-app-group/tests/v1_group_participant.rs`

**Steps:**
1. Add tests where a Human owns the group driver bot but is not directly a participant.
2. Verify add participant succeeds, self-service target updates are allowed for owned bot actors, and unrelated Humans remain forbidden.
3. Run focused tests and confirm the new tests fail before implementation.

### Task 3: Failing session tests

**Files:**
- Modify: `crates/application/v1/bcs-app-session/tests/v1_session_service.rs`

**Steps:**
1. Add tests where a Human owns the parent group driver bot or session creator actor.
2. Verify session participant mutations are allowed without listing all created bots.
3. Run focused tests and confirm the new tests fail before implementation.

### Task 4: Implement group Human actor authority

**Files:**
- Modify: `crates/application/v1/bcs-app-group/src/lib.rs`

**Steps:**
1. Add a private targeted resolver for Human→actor authority over candidate actor ids.
2. Use it for group read/manage checks and self-service participant operations.
3. Preserve existing direct principal logic for non-Human callers and existing target eligibility rules.

### Task 5: Implement session Human actor authority

**Files:**
- Modify: `crates/application/v1/bcs-app-session/src/lib.rs`

**Steps:**
1. Add the same private targeted resolver in the session facade.
2. Use group management candidates plus session creator as manage candidates.
3. Preserve existing session authorization strength; do not copy weak legacy session auth.

### Task 6: Verify

**Commands:**
- `cargo test -p bcs-app-group --test v1_group_participant`
- `cargo test -p bcs-app-session --test v1_session_service`

