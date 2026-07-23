# Organization Admin Run Create HTTP 200 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Return HTTP `200 OK` for successful organization admin run creation while preserving the existing response body and `Location` header.

**Architecture:** Keep the change in the BCS HTTP delivery adapter because HTTP status selection is a transport concern. Pin the contract with the existing bootstrap integration test and update the user-story E2E expectation; do not change application services, run state, callback behavior, or error mapping.

**Tech Stack:** Rust, Axum, Tokio tests, Bash E2E stories

---

### Task 1: Pin the successful HTTP status contract

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs:3854`
- Test: `src/bcs/crates/bootstrap/bcs/src/server.rs`

**Step 1: Write the failing test**

Change the existing assertion in
`detached_admin_run_observes_websocket_terminal_and_callbacks_once`:

```rust
assert_eq!(create_response.status(), StatusCode::OK);
```

Leave the response body and callback assertions unchanged so the test also
continues to cover the existing contract.

**Step 2: Run the focused test to verify it fails**

Run:

```bash
cd src/bcs
cargo test -p bcs detached_admin_run_observes_websocket_terminal_and_callbacks_once -- --exact
```

Expected: FAIL because the route returns `202` while the test expects `200`.

### Task 2: Implement HTTP 200 and update E2E

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/admin_invocations.rs:218`
- Modify: `src/bcs/scripts/e2e-test/stories.sh:843`

**Step 1: Write the minimal implementation**

Change the successful response tuple to:

```rust
(
    StatusCode::OK,
    Json(Envelope {
        // Existing body remains unchanged.
    }),
)
```

Do not change `message: "accepted"`, envelope code `20000`, the `Location`
header, or any failure response.

**Step 2: Update the E2E status expectation**

Change only the admin-run user-story assertion:

```bash
require_status "provider starts an organization admin run" "200" || return
```

Do not change unrelated endpoints that intentionally return `202`.

**Step 3: Run the focused test to verify it passes**

Run:

```bash
cd src/bcs
cargo test -p bcs detached_admin_run_observes_websocket_terminal_and_callbacks_once -- --exact
```

Expected: PASS.

### Task 3: Verify the affected BCS HTTP surface

**Files:**
- Verify: `src/bcs/crates/adapters/http/bcs-http/src/routes/admin_invocations.rs`
- Verify: `src/bcs/crates/bootstrap/bcs/src/server.rs`
- Verify: `src/bcs/scripts/e2e-test/stories.sh`

**Step 1: Run the HTTP adapter tests**

Run:

```bash
cd src/bcs
cargo test -p bcs-http
```

Expected: all tests pass.

**Step 2: Inspect the scoped diff**

Run:

```bash
git diff --check
git diff -- \
  src/bcs/crates/adapters/http/bcs-http/src/routes/admin_invocations.rs \
  src/bcs/crates/bootstrap/bcs/src/server.rs \
  src/bcs/scripts/e2e-test/stories.sh
```

Expected: only the three intended status expectations/selection change, with no
whitespace errors.

**Step 3: Commit**

```bash
git add \
  src/bcs/crates/adapters/http/bcs-http/src/routes/admin_invocations.rs \
  src/bcs/crates/bootstrap/bcs/src/server.rs \
  src/bcs/scripts/e2e-test/stories.sh
git commit -m "fix(bcs): return 200 for admin run creation"
```
