# Bot WebSocket Error UUID Logging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Include the best-known Bot UUID and registration state on Bot WebSocket transport error logs, including provider rejection before streaming registration.

**Architecture:** Extend the adapter-owned `BotDispatchOutcome::BotConnect` result with the observed Bot UUID extracted from the already parsed connect request or the successfully resolved registration identity. The connection handler stores that diagnostic identity separately from `registered_bot_id`, so logging gains attribution without changing cleanup or delivery behavior.

**Tech Stack:** Rust, Tokio, Axum WebSocket, `tracing`, `tracing-subscriber`, Cargo integration and unit tests.

---

### Task 1: Preserve the observed UUID on rejected Bot connects

**Files:**
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat.rs:368-410`
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs:98-151`

**Step 1: Write the failing test**

Update `bot_connect_rejects_provider_delivery_before_streaming_registration` to capture the dispatch outcome and require the attempted UUID while registration remains false:

```rust
let outcome = dispatch_frame(
    &state.dispatch_state,
    &serde_json::to_string(&connect).unwrap(),
    &tx,
    &mut registered_bot_id,
)
.await
.unwrap();

assert!(matches!(
    outcome,
    BotDispatchOutcome::BotConnect {
        registered: false,
        bot_uuid: Some(ref bot_uuid),
    } if bot_uuid == "bot-provider"
));
```

**Step 2: Run the test to verify it fails**

Run:

```bash
cd src/bcs && cargo test -p bcs-ws --test frame_compat bot_connect_rejects_provider_delivery_before_streaming_registration -- --exact
```

Expected: compilation fails because `BotConnect` does not yet expose `bot_uuid`.

**Step 3: Implement the minimal outcome change**

Change the outcome to carry the diagnostic identity:

```rust
pub enum BotDispatchOutcome {
    Dispatched,
    BotConnect {
        registered: bool,
        bot_uuid: Option<String>,
    },
}
```

For an initial `bot.connect`, capture `params.bot_id` from the already parsed request before dispatch. After dispatch, prefer the successfully resolved `registered_bot_id` and otherwise retain the requested UUID:

```rust
let requested_bot_uuid = is_initial_connect
    .then(|| {
        req.params
            .as_ref()
            .and_then(|params| params.get("bot_id"))
            .and_then(Value::as_str)
            .map(str::to_owned)
    })
    .flatten();

// After handle_request_frame succeeds:
return Ok(BotDispatchOutcome::BotConnect {
    registered: registered_bot_id.is_some(),
    bot_uuid: registered_bot_id.clone().or(requested_bot_uuid),
});
```

**Step 4: Run the focused test to verify it passes**

Run the command from Step 2.

Expected: PASS; provider rejection exposes `bot-provider` while `registered_bot_id` remains `None`.

### Task 2: Add structured identity fields to WebSocket error logs

**Files:**
- Modify: `src/bcs/crates/adapters/ws/bcs-ws/src/bot/handler.rs:62-173`

**Step 1: Write failing unit tests for the log event**

Add handler-local tests using a custom `tracing_subscriber::Layer` that captures structured event fields. Call the wished-for logging helper and assert both the observed and fallback forms without depending on text formatting:

```rust
log_websocket_error(&"boom", Some("bot-provider"), false);
assert_eq!(event["bot_uuid"], "bot-provider");
assert_eq!(event["registered"], "false");

log_websocket_error(&"boom", None, false);
assert_eq!(event["bot_uuid"], "unknown");
```

**Step 2: Run the unit tests to verify RED**

Run:

```bash
cd src/bcs && cargo test -p bcs-ws bot::handler::tests::websocket_error_log -- --nocapture
```

Expected: compilation fails because `log_websocket_error` does not exist.

**Step 3: Implement connection-local observation and structured logging**

Add `observed_bot_uuid: Option<String>` beside `registered_bot_id`. Populate it from `BotDispatchOutcome::BotConnect` without altering registration state:

```rust
if let BotDispatchOutcome::BotConnect {
    registered,
    bot_uuid,
} = outcome
{
    if observed_bot_uuid.is_none() {
        observed_bot_uuid = bot_uuid;
    }
    // existing metrics and credential behavior remains unchanged
}
```

Add a small helper used by the general error branch:

```rust
fn log_websocket_error(
    error: &impl std::fmt::Display,
    bot_uuid: Option<&str>,
    registered: bool,
) {
    error!(
        bot_uuid = bot_uuid.unwrap_or("unknown"),
        registered,
        error = %error,
        "WebSocket error"
    );
}
```

Include the same `bot_uuid` and `registered` fields on the reset-without-close debug event. Do not log the token or complete connect frame.

**Step 4: Run the focused tests to verify GREEN**

Run the commands from Task 1 Step 2 and Task 2 Step 2.

Expected: both pass.

### Task 3: Verify the WebSocket adapter change

**Files:**
- Verify: `src/bcs/crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`
- Verify: `src/bcs/crates/adapters/ws/bcs-ws/src/bot/handler.rs`
- Verify: `src/bcs/crates/adapters/ws/bcs-ws/tests/frame_compat.rs`

**Step 1: Run the complete adapter test suite**

Run:

```bash
cd src/bcs && cargo test -p bcs-ws
```

Expected: PASS.

**Step 2: Check formatting only for touched Rust files**

Run targeted rustfmt checks; do not run global `cargo fmt`:

```bash
cd src/bcs && rustfmt --edition 2024 --check crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs crates/adapters/ws/bcs-ws/src/bot/handler.rs crates/adapters/ws/bcs-ws/tests/frame_compat.rs
```

Expected: no formatting diff.

**Step 3: Check the final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only the approved plan and BCS WebSocket files are modified.
