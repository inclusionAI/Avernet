# BCN Collaboration Paths Implementation Plan

> **Superseded:** Do not execute this two-prefix plan. It is replaced by
> [`2026-08-03-bcn-collaboration-prefix.md`](./2026-08-03-bcn-collaboration-prefix.md).
> The current checked-in contract exposes every BCN V1 operation below
> `/openapi/v1/collaboration/**`, removes the public session completion and
> group-participant patch endpoints, and uses
> `GET /openapi/v1/collaboration/bots/{bot_id}/groups` for Group list.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move BCN Bot control-plane and global Session operations onto Gateway-safe public prefixes without changing operation behavior.

**Architecture:** The versioned OpenAPI contract remains authoritative. Contract tests move first, then the YAML path keys, followed by Axum route tests and route literals. Bot collaboration uses the fixed prefix `/openapi/v1/bots/collaboration`; global Session resources use `/openapi/v1/group-sessions`; nested `/openapi/v1/groups/{group_id}/sessions` remains unchanged.

**Tech Stack:** OpenAPI 3.1 YAML, Python/pytest contract tests, Rust/Axum, Cargo.

## Global Constraints

- Change only BCS contracts, BCS HTTP delivery routes, related tests, and current contract documentation.
- Do not mount `bcs-api-http` in the production bootstrap.
- Do not change DTOs, application services, authorization, persistence, or Gateway Principal verification.
- Do not retain compatibility aliases at the old V1 paths.
- Do not run `cargo fmt`, `cargo fmt --all`, or another global BCS formatter.
- Keep the approved operation count at exactly 32.

---

### Task 1: Move the authoritative OpenAPI paths

**Files:**
- Modify: `src/bcs/tests/openapi/test_contract.py`
- Modify: `src/bcs/tests/openapi/test_bot_v1_contract.py`
- Modify: `src/bcs/tests/openapi/test_session_v1_contract.py`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`
- Modify: `src/bcs/api-contracts/README.md`

**Interfaces:**
- Consumes: `scripts.validate_openapi_contract.load_contract(Path) -> dict`
- Produces: a 32-operation contract whose Bot control-plane paths are under `/openapi/v1/bots/collaboration/**` and global Session paths are under `/openapi/v1/group-sessions/**`.

- [ ] **Step 1: Change the exact-operation tests to the approved paths**

Replace the five Bot entries with:

```python
BOT_OPERATIONS = {
    ("get", "/openapi/v1/bots/collaboration/{bot_id}/candidates"),
    ("post", "/openapi/v1/bots/collaboration/query"),
    ("get", "/openapi/v1/bots/collaboration/{bot_id}"),
    ("patch", "/openapi/v1/bots/collaboration/{bot_id}"),
    ("get", "/openapi/v1/bots/collaboration/mine"),
}
```

Replace every global `/openapi/v1/sessions/{session_id}` expectation with `/openapi/v1/group-sessions/{session_id}`. Preserve both operations on `/openapi/v1/groups/{group_id}/sessions`.

Add explicit exclusion assertions for the old base paths:

```python
assert not any(path.startswith("/openapi/v1/sessions/") for _, path in actual)
assert ("get", "/openapi/v1/bots/{bot_id}") not in actual
assert ("get", "/openapi/v1/bots/mine") not in actual
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```bash
uv run --with pytest --with pyyaml pytest \
  src/bcs/tests/openapi/test_contract.py \
  src/bcs/tests/openapi/test_bot_v1_contract.py \
  src/bcs/tests/openapi/test_session_v1_contract.py -q
```

Expected: FAIL because `openapi.yaml` still publishes the old Bot and Session path keys.

- [ ] **Step 3: Move the OpenAPI path keys and current README inventory**

In `api-contracts/v1/openapi.yaml`, apply these prefix mappings without changing `$ref` targets:

```text
/openapi/v1/bots/mine                         -> /openapi/v1/bots/collaboration/mine
/openapi/v1/bots/query                        -> /openapi/v1/bots/collaboration/query
/openapi/v1/bots/{bot_id}                     -> /openapi/v1/bots/collaboration/{bot_id}
/openapi/v1/bots/{bot_id}/candidates          -> /openapi/v1/bots/collaboration/{bot_id}/candidates
/openapi/v1/sessions/{session_id}             -> /openapi/v1/group-sessions/{session_id}
/openapi/v1/sessions/{session_id}/**          -> /openapi/v1/group-sessions/{session_id}/**
```

Update `api-contracts/README.md` so its five-operation Bot inventory uses the collaboration prefix and states that global Session resources use `group-sessions`.

- [ ] **Step 4: Run the OpenAPI suite and verify GREEN**

Run:

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
```

Expected: 18 passed, including exactly 32 operations.

- [ ] **Step 5: Commit the contract migration**

```bash
git add src/bcs/api-contracts src/bcs/tests/openapi
git commit -m "feat(bcs): namespace BCN bot and session contracts"
```

---

### Task 2: Move Bot control-plane HTTP routes

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`

**Interfaces:**
- Consumes: existing `ApiState`, `BotService`, DTOs, and principal middleware.
- Produces: the same five handlers mounted exclusively below `/openapi/v1/bots/collaboration/**`.

- [ ] **Step 1: Point Bot route tests at the approved prefix and reject old paths**

Update all five successful request URIs. Add a test that sends authenticated requests to `/openapi/v1/bots/bot-1` and `/openapi/v1/bots/mine` and asserts `StatusCode::NOT_FOUND` without recording a `FakeBotService` call.

Representative new requests:

```rust
request(
    "GET",
    "/openapi/v1/bots/collaboration/acting/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
    Value::Null,
)

request(
    "POST",
    "/openapi/v1/bots/collaboration/query",
    json!({"bot_ids": ["bot-2", "bot-1", "bot-2"]}),
)
```

- [ ] **Step 2: Run Bot route tests and verify RED**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-api-http --test bot_routes
```

Expected: FAIL because the router still mounts the five handlers at the old paths.

- [ ] **Step 3: Move the Axum Bot route literals**

Change `routes/bot.rs::router()` to mount:

```rust
Router::new()
    .route(
        "/openapi/v1/bots/collaboration/{bot_id}/candidates",
        get(list_candidates),
    )
    .route("/openapi/v1/bots/collaboration/query", post(query_bots))
    .route("/openapi/v1/bots/collaboration/mine", get(list_mine))
    .route(
        "/openapi/v1/bots/collaboration/{bot_id}",
        get(get_bot).patch(update_bot),
    )
```

Do not change handler bodies or service commands.

- [ ] **Step 4: Run Bot route tests and verify GREEN**

Run the Task 2 test command again. Expected: 2 or more tests passed, 0 failed.

- [ ] **Step 5: Commit the Bot router migration**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/bot_routes.rs
git commit -m "feat(bcs): move bot control plane under collaboration"
```

---

### Task 3: Move global Session and Session invitation HTTP routes

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/invitation.rs`

**Interfaces:**
- Consumes: existing `SessionService`, `SessionMessageService`, `InvitationService`, DTOs, and principal middleware.
- Produces: global Session handlers exclusively under `/openapi/v1/group-sessions/{session_id}/**`; nested Group Session collection handlers remain unchanged.

- [ ] **Step 1: Point Session tests at `group-sessions` and reject old paths**

Replace every test request beginning `/openapi/v1/sessions/session-1` with `/openapi/v1/group-sessions/session-1`, including invitation tests. Do not change `/openapi/v1/groups/group-1/sessions`.

Add a route-isolation test that asserts authenticated requests to these old paths return `StatusCode::NOT_FOUND`:

```text
GET  /openapi/v1/sessions/session-1
GET  /openapi/v1/sessions/session-1/messages
POST /openapi/v1/sessions/session-1/invitations
```

- [ ] **Step 2: Run Session and invitation route tests and verify RED**

Run:

```bash
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-api-http \
  --test session_routes --test invitation_routes
```

Expected: FAIL because the production route literals still use `/openapi/v1/sessions`.

- [ ] **Step 3: Move the Axum Session route literals**

In `routes/session.rs`, keep `/openapi/v1/groups/{group_id}/sessions` unchanged and change the other five route patterns to the `group-sessions` prefix:

```text
/openapi/v1/group-sessions/{session_id}
/openapi/v1/group-sessions/{session_id}/completion
/openapi/v1/group-sessions/{session_id}/messages
/openapi/v1/group-sessions/{session_id}/participants
/openapi/v1/group-sessions/{session_id}/participants/{bot_uuid}
```

In `routes/invitation.rs`, change only the Session invitation pattern to:

```text
/openapi/v1/group-sessions/{session_id}/invitations
```

- [ ] **Step 4: Run Session and invitation route tests and verify GREEN**

Run the Task 3 test command again. Expected: all Session and invitation route tests pass.

- [ ] **Step 5: Commit the Session router migration**

```bash
git add src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/session.rs \
  src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/invitation.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/session_routes.rs \
  src/bcs/crates/adapters/http/bcs-api-http/tests/invitation_routes.rs
git commit -m "feat(bcs): rename collaboration sessions to group sessions"
```

---

### Task 4: Verify contract/runtime parity and stale-path absence

**Files:**
- Modify only if a verification failure identifies a stale current contract reference.

**Interfaces:**
- Consumes: completed OpenAPI contract and `bcs-api-http` router.
- Produces: evidence that the contract and implementation expose the same migrated path families.

- [ ] **Step 1: Validate and bundle the OpenAPI contract**

```bash
uv run --with pyyaml python src/bcs/scripts/validate_openapi_contract.py \
  --root src/bcs/api-contracts/v1
uv run --with pyyaml python src/bcs/scripts/bundle_openapi_contract.py \
  --root src/bcs/api-contracts/v1 \
  --output-dir /tmp/bcn-collaboration-paths-openapi
```

Expected: validation succeeds and `bcn-openapi-v1.yaml` is generated.

- [ ] **Step 2: Scan authoritative and executable surfaces for stale paths**

```bash
rg -n '/openapi/v1/bots/(mine|query|\{bot_id\})|/openapi/v1/sessions/\{session_id\}' \
  src/bcs/api-contracts \
  src/bcs/crates/adapters/http/bcs-api-http/src \
  src/bcs/tests/openapi
```

Expected: no matches except explicit negative assertions that document old paths.

- [ ] **Step 3: Run focused full suites**

```bash
uv run --with pytest --with pyyaml pytest src/bcs/tests/openapi -q
cargo test --manifest-path src/bcs/Cargo.toml --package bcs-api-http
cargo check --manifest-path src/bcs/Cargo.toml --package bcs-api-http --all-targets
git diff --check
```

Expected: OpenAPI 18 passed; all `bcs-api-http` tests and checks pass; no whitespace errors.

- [ ] **Step 4: Review the final diff**

Confirm that no application, persistence, bootstrap, Gateway, or unrelated BCS files changed and that the operation count remains 32.
