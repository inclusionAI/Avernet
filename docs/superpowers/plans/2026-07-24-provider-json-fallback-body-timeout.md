# Provider JSON Fallback Body Timeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent protocol 2.0 SSE-preferred delivery from hanging indefinitely when a provider falls back to an incomplete JSON acknowledgement body.

**Architecture:** Keep the SSE HTTP client free of total/read timeouts. Introduce a private acknowledgement-body reader that applies a finite timeout only to JSON decoding, and call it from the protocol 2.0 JSON fallback branch.

**Tech Stack:** Rust, Tokio, Reqwest, Cargo tests.

## Global Constraints

- Do not add a client-wide total or read timeout to the SSE client.
- Keep the production JSON fallback body timeout at 125 seconds.
- Do not change public protocol contracts or add dependencies.
- Format only the touched Rust file; do not run global `cargo fmt`.

---

### Task 1: Bound the JSON fallback acknowledgement body

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs`
- Test: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs`

**Interfaces:**
- Consumes: `reqwest::Response` returned after provider response headers are accepted.
- Produces: private `read_provider_ack_body(response, timeout)` returning either `ProviderAckResponse`, a decode error, or a timeout.

- [ ] **Step 1: Write the failing test**

Add a raw HTTP/1 test server that declares a longer JSON body than it sends and
keeps the socket open. Call `read_provider_ack_body(response,
Duration::from_millis(10))` under a 250ms test guard and require the explicit
timeout variant:

```rust
#[tokio::test]
async fn json_fallback_body_timeout_bounds_incomplete_response() {
    let addr = spawn_stalled_json_body_server(Duration::from_secs(1)).await;
    let response = reqwest::Client::new()
        .get(format!("http://{addr}"))
        .send()
        .await
        .unwrap();

    let result = tokio::time::timeout(
        Duration::from_millis(250),
        read_provider_ack_body(response, Duration::from_millis(10)),
    )
    .await
    .expect("ack body reader must not remain pending");

    assert!(matches!(result, Err(ProviderAckBodyError::Timeout)));
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cargo test -p bcs-provider-http client_policy_tests::json_fallback_body_timeout_bounds_incomplete_response -- --exact
```

Expected: FAIL because the body read is not yet independently bounded.

- [ ] **Step 3: Implement the minimal body timeout**

Add `JSON_FALLBACK_BODY_TIMEOUT_MS = 125_000`, a private error enum, and the
reader:

```rust
#[derive(Debug)]
enum ProviderAckBodyError {
    Decode(reqwest::Error),
    Timeout,
}

async fn read_provider_ack_body(
    response: reqwest::Response,
    timeout: Duration,
) -> Result<ProviderAckResponse, ProviderAckBodyError> {
    match tokio::time::timeout(timeout, response.json::<ProviderAckResponse>()).await {
        Ok(Ok(ack)) => Ok(ack),
        Ok(Err(error)) => Err(ProviderAckBodyError::Decode(error)),
        Err(_) => Err(ProviderAckBodyError::Timeout),
    }
}
```

Replace the protocol 2.0 JSON fallback's direct `.json().await` with this
helper, passing `Duration::from_millis(JSON_FALLBACK_BODY_TIMEOUT_MS)`. Preserve
the existing decode warning and error, and add a timeout warning/error that
includes the configured timeout.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cargo test -p bcs-provider-http client_policy_tests::json_fallback_body_timeout_bounds_incomplete_response -- --exact
cargo test -p bcs-provider-http provider_delivery_2_0_chat_send_with_sse_preference_advertises_sse -- --exact
```

Expected: both PASS.

- [ ] **Step 5: Format and run package verification**

Run:

```bash
rustfmt --edition 2024 src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs
cargo test -p bcs-provider-http
cargo check -p bcs-provider-http
```

Expected: formatting succeeds, all package tests pass, and the package checks.

- [ ] **Step 6: Commit**

```bash
git add docs/plans/2026-07-24-provider-json-fallback-body-timeout-design.md \
  docs/superpowers/plans/2026-07-24-provider-json-fallback-body-timeout.md \
  src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs
git commit -m "fix(provider): bound JSON fallback body reads"
```
