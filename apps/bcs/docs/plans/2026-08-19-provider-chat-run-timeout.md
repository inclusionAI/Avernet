# Configurable Provider Chat Run Timeout Implementation Plan

> **For Codex:** Implement each task test-first and keep explicit `timeout_ms`
> precedence intact.

**Goal:** Make the default HTTP Provider `chat.send` run deadline configurable,
default it to three hours, and keep all explicit per-delivery timeouts unchanged.

**Architecture:** `BcsConfig` owns one process-lifetime default. Bootstrap
injects it into the HTTP Provider adapter and the application services that
create implicit Provider run contexts. Core/application services retain the
public default in constructors for compatibility. The transport computes the
effective timeout as explicit frame value or configured fallback.

**Tech Stack:** Rust, serde/TOML configuration, async traits, Tokio, Reqwest,
Cargo tests.

---

### Task 1: Change and parse the public default

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/provider.rs`
- Modify: `src/bcs/crates/service-api/bcs-service-api/tests/provider_callback_timeout_contract.rs`
- Modify: `src/bcs/crates/bootstrap/bcs/src/config.rs`

**Steps:**

1. Change the service API contract test to expect `10_800_000` and add config
   tests for the three-hour default and an explicit override.
2. Run the focused tests and confirm they fail against the one-hour constant
   and missing config field.
3. Change `DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS` to three hours and add
   `provider_chat_run_timeout_ms` to `BcsConfig`, defaulted from that constant.
4. Run the focused tests and `cargo check -p bcs-service-api`.

### Task 2: Apply fallback and explicit precedence in the HTTP Provider adapter

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`

**Steps:**

1. Add tests proving an injected Provider chat default is sent when the frame
   omits `timeout_ms`, and an explicit frame value wins over that injected
   default.
2. Run the focused tests and confirm the injected-default test fails.
3. Store the default on `HttpProviderTransport`, add a builder method, and pass
   it only to `provider_request_from_frame` for ordinary delivery. Leave
   `interaction.resolve` and explicit frame parsing unchanged.
4. Run the Provider transport contract tests.

### Task 3: Align application run contexts

**Files:**
- Modify: `src/bcs/crates/services/bcs-message-flow/src/group_flow.rs`
- Modify: `src/bcs/crates/services/bcs-message-flow/tests/contract_message_flow.rs`
- Modify: `src/bcs/crates/services/bcs-system-message/src/dispatcher.rs`
- Modify: `src/bcs/crates/services/bcs-system-message/src/dispatcher_test.rs`
- Modify: `src/bcs/crates/services/bcs-collaboration-runtime/src/runtime.rs`
- Modify: `src/bcs/crates/services/bcs-collaboration-runtime/tests/runtime_progression.rs`

**Steps:**

1. Add focused tests that inject a non-default timeout and assert the created
   `BotRunContext.deadline_ms` uses it for message-flow, system-message, and a
   state-machine Bot node without an explicit node deadline.
2. Run each focused test and confirm it fails against the hard-coded default.
3. Add defaulted builder fields/methods to the three services and replace only
   implicit Provider callback deadlines. Preserve explicit state-machine node
   deadlines.
4. Run the three affected package test suites.

### Task 4: Wire the setting through production bootstrap

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Modify bootstrap tests in: `src/bcs/crates/bootstrap/bcs/src/server.rs`

**Steps:**

1. Add or extend a bootstrap construction test so the configured value must be
   accepted through every production profile.
2. Run `cargo check -p bcs` and use constructor/test failures to identify any
   missed composition path.
3. Inject `config.provider_chat_run_timeout_ms` into each
   `HttpProviderTransport`, `BcsMessageFlow`, `SystemMessageDispatcherImpl`,
   and `CollaborationRuntime` construction path.
4. Do not change the A2A construction or its explicit two-hour Provider frame
   timeout.
5. Run bootstrap tests and `cargo check -p bcs`.

### Task 5: Regression verification and review

**Files:**
- Verify all files changed from the design commit.

**Steps:**

1. Run the service-api, Provider adapter, message-flow, system-message,
   collaboration-runtime, and bootstrap tests affected above.
2. Run the A2A test that asserts `A2A_DOWNSTREAM_EXECUTION_TIMEOUT_MS` is sent
   explicitly; add a narrow assertion if current coverage is insufficient.
3. Search all production uses of `DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS` and all
   `BotRunContext` creation sites to confirm no implicit Provider run context
   remains disconnected from configuration.
4. Run `git diff --check` and inspect the complete diff for unrelated changes.
5. Apply the verification-before-completion checklist before reporting success.
