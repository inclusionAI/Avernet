# AgentPass Resolve JWT Response Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Return a complete formatted JWT header and payload from `POST /providers/agentpass/resolve` without logging or returning reusable credential material.

**Architecture:** Keep the change inside the Avernet `bcs-http` delivery adapter. A private route helper performs non-authoritative Base64URL/JSON formatting, while the existing `BotRuntimeTokenResolverPort` remains the sole source of `agent_code`; the route adds diagnostic JWT data and `Cache-Control: no-store` without changing any OCB/internal AgentPass SDK contract.

**Tech Stack:** Rust 2024, Axum, `base64`, `serde_json`, Tokio/Tower contract tests.

---

### Task 1: Specify the HTTP response contract

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-http/tests/bot_events_contract.rs:1522`

**Step 1: Add a JWT fixture helper**

Import `base64::Engine`, then add a helper near the provider test helpers:

```rust
fn diagnostic_jwt(header: Value, payload: Value, signature: &[u8]) -> String {
    let encode = |bytes: &[u8]| {
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    };
    format!(
        "{}.{}.{}",
        encode(&serde_json::to_vec(&header).unwrap()),
        encode(&serde_json::to_vec(&payload).unwrap()),
        encode(signature),
    )
}
```

**Step 2: Write the failing resolved-token contract test**

Change `agentpass_resolve_returns_agent_code_binding_and_bot` to use a real
three-segment JWT fixture whose payload includes strings, numbers, booleans,
arrays, nested objects, null, and representative identity claims. Assert:

```rust
assert_eq!(body["jwt"]["header"], header);
assert_eq!(body["jwt"]["payload"], payload);
assert_eq!(body["jwt"]["signature"]["present"], true);
assert_eq!(body["jwt"]["signature"]["byte_length"], signature.len());
assert_eq!(body["jwt"]["agentpass_resolved"], true);
assert!(body["jwt"].get("token").is_none());
assert!(body["jwt"]["signature"].get("value").is_none());
assert_eq!(response.headers()["cache-control"], "no-store");
```

Read and clone the response headers before consuming its body.

**Step 3: Write the failing unresolved-token contract test**

Use a syntactically valid JWT with `StaticAgentpassResolver::default()` and
assert that the complete header/payload are still formatted, existing business
fields remain null, and `agentpass_resolved` is false.

**Step 4: Write the failing malformed-token contract test**

Send a malformed three-segment token and assert HTTP 200, null formatted
header/payload, a bounded `parse_error` category, false resolution, and
`Cache-Control: no-store`.

**Step 5: Write the failing no-log contract test**

Run a successful resolve request under `capture_tracing_logs`, using unique
sentinel values in `sno`, `sub`, and another claim. Assert that neither the raw
token nor any sentinel value occurs in captured logs.

**Step 6: Run the focused tests and verify RED**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http --test bot_events_contract agentpass_resolve -- --nocapture
```

Expected: the new assertions fail because the response has no `jwt` object or
`Cache-Control: no-store` header.

**Step 7: Commit the contract tests**

```bash
git add src/bcs/crates/adapters/http/bcs-http/tests/bot_events_contract.rs
git commit -m "test(bcs): specify AgentPass JWT diagnostics"
```

### Task 2: Implement safe JWT formatting

**Files:**
- Create: `src/bcs/crates/adapters/http/bcs-http/src/routes/agentpass_jwt.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/mod.rs:1-30`
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/providers.rs:467-502`

**Step 1: Add the private formatter module**

Create a route-private helper with this API:

```rust
pub(super) fn format_jwt(token: &str, agentpass_resolved: bool) -> Value
```

It must:

- require exactly three non-empty JWT segments;
- Base64URL-decode header, payload, and signature, accepting padded and
  unpadded URL-safe input;
- deserialize header and payload to `serde_json::Value`, preserving every JSON
  field and value;
- return signature presence and decoded byte length, never signature contents;
- return bounded categories such as `invalid_segment_count`,
  `invalid_header_encoding`, `invalid_header_json`,
  `invalid_payload_encoding`, `invalid_payload_json`, or
  `invalid_signature_encoding`;
- never log token data or decoded claims.

On success the helper returns:

```rust
json!({
    "header": header,
    "payload": payload,
    "signature": {
        "present": true,
        "byte_length": signature.len(),
    },
    "agentpass_resolved": agentpass_resolved,
})
```

On failure it returns null header/payload and the bounded error category. It
must not include error strings from Base64 or JSON libraries because they may
contain input fragments.

**Step 2: Register the private route module**

Add this declaration to `routes/mod.rs`:

```rust
mod agentpass_jwt;
```

**Step 3: Restructure the endpoint without changing business resolution**

Remove the early return when the resolver yields `None`. Resolve binding and
Bot only when `agent_code` exists, then include:

```rust
"jwt": super::agentpass_jwt::format_jwt(&token, agent_code.is_some()),
```

The existing `agent_code`, `provider_bot_binding`, and `bot` values must retain
their current semantics.

**Step 4: Add the no-store response header**

Change the handler return type to `Result<Response, ProviderRouteError>`, build
the JSON response with `Json(...).into_response()`, and insert:

```rust
response.headers_mut().insert(
    axum::http::header::CACHE_CONTROL,
    axum::http::HeaderValue::from_static("no-store"),
);
```

Do not add tracing statements for the token, JWT object, or response body.

**Step 5: Run the focused tests and verify GREEN**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http --test bot_events_contract agentpass_resolve -- --nocapture
```

Expected: all `agentpass_resolve` tests pass.

**Step 6: Commit the implementation**

```bash
git add src/bcs/crates/adapters/http/bcs-http/src/routes/agentpass_jwt.rs \
  src/bcs/crates/adapters/http/bcs-http/src/routes/mod.rs \
  src/bcs/crates/adapters/http/bcs-http/src/routes/providers.rs
git commit -m "feat(bcs): return formatted AgentPass JWT details"
```

### Task 3: Verify regression and scope

**Files:**
- Verify only; no planned source changes.

**Step 1: Run all bcs-http tests**

```bash
cargo test --manifest-path src/bcs/Cargo.toml -p bcs-http
```

Expected: PASS with no new warnings.

**Step 2: Check the targeted diff**

```bash
git diff --check HEAD~2..HEAD
git status --short
```

Expected: no whitespace errors and a clean Avernet working tree.

**Step 3: Confirm the parent OCB repository remains untouched**

From the OCB root, verify the only parent-repository status associated with
Avernet is the already-present submodule pointer difference; do not stage or
commit that pointer in OCB.
