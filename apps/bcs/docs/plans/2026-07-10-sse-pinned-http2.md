# SSE Pinned HTTP/2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure BCS provider SSE requests remain unbounded by the normal 65-second timeout after DNS pinning, use HTTP/2 only, and expose request-to-response-header timing.

**Architecture:** Model normal and SSE client behavior as explicit policies and use the selected policy for both shared and DNS-pinned clients. Keep the existing per-frame SSE diagnostics and add request-stage fields around the outbound POST so response-header stalls can be distinguished from stream-read stalls.

**Tech Stack:** Rust, reqwest, tokio, existing `bcs-provider-http` unit tests.

---

### Task 1: Lock Down Client Policy

**Files:**
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`

1. Add failing unit tests asserting that SSE policy has no total timeout and is HTTP/2-only, while callback policy retains the 65-second timeout.
2. Run the focused tests and confirm they fail because the policy API does not exist.
3. Add the minimal policy type and policy-aware client builder.
4. Run the focused tests and confirm they pass.

### Task 2: Preserve Policy Through DNS Pinning

**Files:**
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`

1. Add a failing test for selecting SSE policy when constructing a DNS-pinned client.
2. Pass the selected policy into `provider_client_for_url` so DNS resolution changes only addresses, not timeout or protocol behavior.
3. Configure SSE builders with `http2_prior_knowledge`, no total timeout, the existing 125-second read timeout, and redirect rejection.
4. Run the focused tests and confirm they pass.

### Task 3: Add Request-Stage Diagnostics

**Files:**
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`

1. Log the selected client policy, whether DNS pinning is active, and request start metadata before sending.
2. Log response-header elapsed time, response status, and negotiated HTTP version after `send()` returns.
3. Keep `bcs_sse_detail` as the source of per-frame `frame_ts`, `recv_ms`, and `lag_ms`.

### Task 4: Verify

**Files:**
- Test: `crates/adapters/http/bcs-provider-http/src/lib.rs`

1. Run `cargo test -p bcs-provider-http`.
2. Run `cargo check -p bcs-provider-http`.
3. Inspect the diff to ensure no BAAS files or unrelated formatting changed.
4. Query the successful production run's `bcs-sse-detail` records and calculate delay statistics from `lag_ms`.

### Task 5: Merge PR 82 Response Header Diagnostics

**Files:**
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`
- Test: `crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`

1. Extend the existing tracing contract test to require `accept_sse`, `target_bot_id`, `content_type`, `content_length`, and `transfer_encoding` on the response-header event.
2. Run the focused contract test and confirm it fails because those fields are absent.
3. Add the fields to the existing `provider downlink: response headers received` event without adding a duplicate log event.
4. Run the focused test, the full `bcs-provider-http` test suite, and `cargo check -p bcs-provider-http`.
