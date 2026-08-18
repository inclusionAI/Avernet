# A2A Authz Allow-Open Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make direct A2A chat continue delivery without `extensions.authz_context` when the authz context builder fails.

**Architecture:** Keep grant and policy semantics inside `AuthzContextBuilderCoreService`; message-flow only requests and injects the returned context. Change only the direct A2A delivery error branch so builder failure is allow-open while successful context injection remains fail-safe for serialization errors.

**Tech Stack:** Rust, Tokio async tests, Cargo contract tests under `src/bcs`.

---

### Task 1: Document allow-open direct A2A authz behavior

**Files:**
- Modify: `docs/superpowers/specs/2026-08-18-a2a-authz-allow-open-design.md`
- Modify: `docs/superpowers/plans/2026-08-18-a2a-authz-allow-open.md`

- [x] **Step 1: Write the design spec**

Describe the direct A2A allow-open behavior, preserving successful `AuthzContext` injection and listing these out-of-scope areas explicitly: collaboration runtime, state-machine permission/runs, group flow authorization, friendship/visibility checks, domain authorization structs, service-api authorization contracts, stores, migrations, and `bcs-authorization` core semantics.

- [x] **Step 2: Write this implementation plan**

Include explicit RED/GREEN commands and expected results for the focused contract test and related verification suites.

### Task 2: RED focused contract test

**Files:**
- Modify: `src/bcs/crates/services/bcs-message-flow/tests/contract_a2a_chat.rs`

- [x] **Step 1: Replace the fail-closed test with the allow-open test**

Test name: `a2a_chat_authz_failure_does_not_block_bot_delivery`.

Test behavior:
- configure `RecordingAuthzContextBuilder { decision: Decision::Deny }`;
- call `service.chat(chat_command("bot-target"))`;
- assert the call succeeds;
- assert a `chat.send` frame was delivered;
- assert `params.extensions.authz_context` is absent.

- [x] **Step 2: Run RED command before production-code changes**

Run:

```bash
cd src/bcs && cargo test --package bcs-message-flow --test contract_a2a_chat a2a_chat_authz_failure_does_not_block_bot_delivery -- --nocapture
```

Expected before production change: FAIL because direct chat still returns the authz builder error (`denied by test authz`) instead of allowing delivery.

Observed before production change: FAIL. Direct chat returned `Forbidden("denied by test authz")` instead of succeeding; `0 passed; 1 failed; 35 filtered out`.

### Task 3: GREEN minimal production change

**Files:**
- Modify: `src/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs`

- [x] **Step 1: Make the authz builder Err branch allow-open**

In direct A2A chat delivery, keep `Ok(context) => inject_authz_context_into_frame(&mut frame, &context)?`. Replace the `Err(error)` branch so it does not mark the run failed and does not return the error. Continue execution after the match, leaving the frame without `authz_context`.

- [x] **Step 2: Run GREEN focused command**

Run:

```bash
cd src/bcs && cargo test --package bcs-message-flow --test contract_a2a_chat a2a_chat_authz_failure_does_not_block_bot_delivery -- --nocapture
```

Expected after production change: PASS.

Observed: PASS, 1 passed; 0 failed; 35 filtered out.

- [x] **Step 3: Run related verification commands**

Run:

```bash
cd src/bcs && cargo test --package bcs-message-flow --test contract_a2a_chat a2a_chat_ -- --nocapture
cd src/bcs && cargo test --package bcs-authorization --test authz_context_builder -- --nocapture
cd src/bcs && cargo test --package bcs-protocol --test a2a_authz_context_contract -- --nocapture
```

Expected after production change: all selected tests pass.

Observed:
- `bcs-message-flow` focused `a2a_chat_`: PASS, 3 passed; 0 failed; 33 filtered out.
- `bcs-authorization` `authz_context_builder`: PASS, 7 passed; 0 failed.
- `bcs-protocol` `a2a_authz_context_contract`: PASS, 1 passed; 0 failed.

### Task 4: Self-review

**Files:**
- Inspect diff only for owned files.

- [x] **Step 1: Review changed files**

Run:

```bash
git diff -- docs/superpowers/specs/2026-08-18-a2a-authz-allow-open-design.md docs/superpowers/plans/2026-08-18-a2a-authz-allow-open.md src/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs src/bcs/crates/services/bcs-message-flow/tests/contract_a2a_chat.rs
```

Expected: only the allow-open docs, one focused test, and the minimal direct A2A authz builder error branch changed. The many pre-existing untracked docs under `docs/superpowers/specs/` are out-of-scope for this task and must not be staged.
