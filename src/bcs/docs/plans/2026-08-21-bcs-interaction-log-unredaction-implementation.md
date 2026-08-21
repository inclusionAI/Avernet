# BCS Interaction Log Unredaction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Log complete Provider 2.0 HITL interaction business payloads while retaining credential and temporary attachment URL redaction.

**Architecture:** Keep the change inside the `bcs-provider-http` delivery adapter, where Provider request bodies and SSE detail records are already formatted. Remove only the interaction-specific business-payload projection; retain the independent attachment URL redaction and all authentication/credential protections.

**Tech Stack:** Rust, Serde JSON, Tokio/Cargo tests, Markdown protocol documentation

---

### Task 1: Specify Unredacted Interaction Request Logs

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs:2246-2285`

**Step 1: Write the failing test**

Rename `provider_body_log_redacts_interaction_answers_and_extensions` to
`provider_body_log_preserves_interaction_business_payload` and replace its
assertions with:

```rust
assert!(logged.contains("\"action\":\"submit\""));
assert!(logged.contains("sensitive answer"));
assert!(logged.contains("sensitive extension"));
assert!(!logged.contains("<redacted>"));
```

**Step 2: Run the test to verify it fails**

Run:

```bash
cd src/bcs
cargo test -p bcs-provider-http provider_body_log_preserves_interaction_business_payload -- --exact
```

Expected: FAIL because `action`, `answers`, and `providerExtension` are still
replaced with `<redacted>`.

**Step 3: Implement the minimal request-log change**

In `provider_body_log`, delete the `interaction.resolve` special case:

```rust
if body.method == "interaction.resolve" {
    redact_interaction_resolution_params(&mut redacted);
}
```

Delete the now-unused `redact_interaction_resolution_params` helper. Leave the
attachment URL loop unchanged.

**Step 4: Run the focused test**

Run the same Cargo command. Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs
git commit -m "fix(bcs): log interaction resolve payloads"
```

### Task 2: Specify Unredacted Interaction SSE Detail Logs

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs:1226-1263`
- Modify: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs:2288-2301`

**Step 1: Write the failing test**

Rename `sse_detail_log_redacts_interaction_business_payload` to
`sse_detail_log_preserves_interaction_business_payload` and assert:

```rust
assert!(logged.contains("\"command\":\"sensitive command\""));
assert!(logged.contains("sensitive answer"));

let malformed = sse_data_log("interaction", "not-json interaction payload");
assert_eq!(malformed, "not-json interaction payload");
```

**Step 2: Run the test to verify it fails**

Run:

```bash
cd src/bcs
cargo test -p bcs-provider-http sse_detail_log_preserves_interaction_business_payload -- --exact
```

Expected: FAIL because the helper currently keeps only correlation metadata and
returns a redacted byte count for malformed interaction data.

**Step 3: Implement the minimal SSE-log change**

Replace `sse_data_log` with:

```rust
fn sse_data_log(_event: &str, data: &str) -> String {
    data.to_string()
}
```

This keeps the existing call sites stable while making interaction event logs
consistent with other Provider SSE event logs.

**Step 4: Run the focused test**

Run the same Cargo command. Expected: PASS.

**Step 5: Commit**

```bash
git add src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs
git commit -m "fix(bcs): log interaction SSE payloads"
```

### Task 3: Align Provider HITL Documentation

**Files:**
- Modify: `src/bcs/docs/bcs-provider-2.0-sse-protocol.md:755-758`
- Modify: `src/bcs/docs/plans/2026-08-12-bcn-provider-sse-hitl-design.md:552-562`

**Step 1: Update the current protocol documentation**

State that BCS INFO/WARN and `bcs_sse_detail` logs include complete interaction
business payloads for diagnostics, while authentication credentials,
authorization headers, and temporary attachment URLs remain redacted.

**Step 2: Update the original HITL design observability section**

Replace the obsolete prohibition on logging user answers and command contents
with the approved scoped policy.

**Step 3: Check documentation consistency**

Run:

```bash
rg -n "interaction 业务 payload|secret user answers|sensitive command contents" src/bcs/docs
git diff --check
```

Expected: no stale prohibition remains; `git diff --check` exits 0.

**Step 4: Commit**

```bash
git add src/bcs/docs/bcs-provider-2.0-sse-protocol.md \
  src/bcs/docs/plans/2026-08-12-bcn-provider-sse-hitl-design.md
git commit -m "docs(bcs): document interaction payload logging"
```

### Task 4: Verify the BCS Change

**Files:**
- Test: `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs`

**Step 1: Run the complete adapter test suite**

```bash
cd src/bcs
cargo test -p bcs-provider-http
```

Expected: PASS.

**Step 2: Run the BCS compile check**

```bash
cd src/bcs
cargo check -p bcs
```

Expected: PASS.

**Step 3: Run repository hygiene checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional files are changed. Do not run
`cargo fmt` or `cargo fmt --all`.

**Step 4: Commit any remaining intentional verification/documentation changes**

If all intended files were already committed, do not create an empty commit.
