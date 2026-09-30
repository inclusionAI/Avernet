# BCS `chat.abort` Protocol Contract

This document defines the cancellation contract across Workbench WebSocket,
BCS run coordination, Bot WebSocket plugins, and HTTP Providers. Cancellation
does not impose a single-active-run rule: one Bot/Session may have multiple
non-terminal runs.

## Workbench to BCS

Both UserBound and SessionBound connections must successfully call `connect`
and subscribe to the target Session before `chat.abort`.

UserBound request:

```json
{
  "type": "req",
  "id": "abort-command-id",
  "method": "chat.abort",
  "params": {
    "group_id": "group-id",
    "session_id": "session-id",
    "bot_id": "target-bcs-bot-id"
  }
}
```

SessionBound requests require only `bot_id`; BCS takes Group/Session from the
token and closes the connection with `token_scope_mismatch` if a supplied
scope differs. Legacy `{group_id, run_id}` remains accepted for one compatibility
version and is resolved through the active-run index.

Every request reauthorizes the bound Human against current Session state. The
Human must be a present Session participant or own a Bot in the Session. A
direct Human participant marked `Absent` is denied. The target Bot must belong
to the Session, but may itself be `Absent` so stale runs can be cleaned up.

Success response:

```json
{
  "type": "res",
  "id": "abort-command-id",
  "ok": true,
  "payload": {
    "aborted": true,
    "aborted_run_ids": ["canonical-bcs-run-id"],
    "run_ids": ["canonical-bcs-run-id"]
  }
}
```

`run_ids` is a deprecated one-version alias. Partial delivery is not rolled
back; BCS returns `chat_abort_partial_failure` with successful and failed run
IDs so the caller can retry. Each failure includes a stable `code`.

An older Bot WebSocket plugin may reject the downstream `chat.abort` method
with an unknown-method response. BCS normalizes that response without parsing
its human-readable message. When no run was aborted and every failure has this
cause, the Workbench response uses `chat_abort_not_supported`, sets
`retryable=false`, and includes `details.resolution="restart_bot"`. Mixed
outcomes remain `chat_abort_partial_failure`, with affected failures carrying
`code="chat_abort_not_supported"`. Timeouts and disconnects are not treated as
evidence of an old plugin.

## BCS active-run ownership

BCS indexes non-terminal contexts by `(group_id, session_id, bot_id)` before
sending `chat.send`. Each context stores canonical and downstream run IDs, the
exact downstream `session_key`, deadline, terminal state, and non-sensitive transport ownership. Abort follows
the transport captured at run creation and never switches based on the Bot's
current configuration.

BCS changes a run to `Aborted` only after a matching downstream acknowledgement
or event wins the common terminal compare-and-set. Late or out-of-scope IDs do
not create a BCS terminal state.

## BCS to Bot WebSocket plugin

BCS sends one exact RequestFrame per active plugin-owned run and waits for its
matching ResponseFrame:

```json
{
  "type": "req",
  "id": "per-run-command-id",
  "method": "chat.abort",
  "params": {
    "session_key": "original-chat-send-session-key",
    "run_id": "plugin-run-id"
  }
}
```

An active run returns that ID. An already-terminal run returns an empty array.
Unknown or cross-session run IDs are protocol errors. One response may contain
zero or one ID. Multiple run requests are delivered with bounded concurrency.

The plugin-facing key is version-compatible: protocol v2 reuses the
group-derived `session_key` from the original `chat.send`, while protocol v3
reuses the canonical BCS Session ID. This compatibility key is not used to
select runs or authorize callers; BCS always performs those operations with
the canonical `(group_id, session_id, bot_id)` scope.

## BCS to HTTP Provider

BCS sends exactly one scope request for a Provider Bot/Session, regardless of
the number of locally indexed runs. `to_bot` contains `provider_id` and
`provider_bot_ref`; `session_id` is canonical; `params` is null. No client
`env` is accepted or forwarded.

The Provider combines its server environment and atomically terminates rows
matching `(bot_id, session_id, env, status=RUNNING)`. `PENDING` is not aborted.
It returns the actual run IDs changed by that operation. `200` with an empty
array and `410 run_terminated` are idempotent no-ops. Other errors and timeouts
do not create new BCS `Aborted` terminal states.
