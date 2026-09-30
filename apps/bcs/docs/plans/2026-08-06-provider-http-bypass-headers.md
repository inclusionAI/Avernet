# Provider HTTP Bypass Headers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let BCS forward only configured inbound HTTP headers from bcs-cli requests to HTTP provider webhooks.

**Architecture:** BCS config owns the allowlist. HTTP delivery adapters extract allowlisted inbound headers into request context / delivery commands; core remains transport-agnostic. The provider HTTP delivery adapter applies those opaque headers when posting provider webhooks, while reserved/auth/protocol headers stay controlled by BCS.

**Tech Stack:** Rust, axum HeaderMap, reqwest, serde TOML config, cargo test.

---

### Task 1: Lock provider transport behavior with a failing test

**Files:**
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`

**Step 1:** Add a unit/integration-style test near existing provider request tests that starts a local HTTP server, sends a provider request with passthrough headers, and asserts the provider receives the allowlisted header but not reserved ones.

**Step 2:** Run `cargo test --package bcs-provider-http provider_request_applies_passthrough_headers -- --nocapture`.
Expected: FAIL because the provider transport has no passthrough header input yet.

### Task 2: Add config schema and validation

**Files:**
- Modify: `crates/bootstrap/bcs/src/config.rs`
- Modify as needed: `crates/bootstrap/bcs/src/config_loader.rs`

**Step 1:** Add failing config tests for `[provider_http] bypass_headers = [...]` parsing and invalid reserved/invalid header names.

**Step 2:** Implement `ProviderHttpConfig { bypass_headers: Vec<String> }`, default empty, deny unknown fields, validation via `HeaderName`, and reserved header rejection.

**Step 3:** Run targeted config tests.

### Task 3: Capture inbound allowlisted headers at HTTP boundary

**Files:**
- Modify HTTP route/state files under `crates/bootstrap/bcs/src/server.rs` and/or `crates/adapters/http/bcs-http/` where chat/group/service requests create delivery requests.

**Step 1:** Add a failing test that sends an inbound request with a configured bypass header and observes it on the provider webhook.

**Step 2:** Extract only configured header names from the inbound `HeaderMap` in delivery adapter code and carry them through a transport-agnostic field.

### Task 4: Carry headers through service/delivery command contract

**Files:**
- Modify `crates/service-api/bcs-service-api/src/...` containing `BotDeliveryCommand`.
- Modify constructors/call sites compiling against it.

**Step 1:** Add `provider_bypass_headers` or equivalent to `BotDeliveryCommand` with default empty value for existing constructors.

**Step 2:** Ensure WebSocket delivery ignores it; HTTP provider delivery consumes it.

### Task 5: Verify

**Commands:**
- `cargo test --package bcs-provider-http provider_request_applies_passthrough_headers -- --nocapture`
- `cargo test --package bcs config:: -- --nocapture` or targeted config tests
- Targeted integration test for inbound request to provider webhook if available.

