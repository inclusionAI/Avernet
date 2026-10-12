# Native QwenWork engine

The `bcs-bridge` binary includes `--engine qwenwork`. This Rust adapter attaches
to a running desktop app through its WeCom Bot channel and HTTP Hooks. It
requires neither the previous Python demo nor a separate adapter process.

```text
BCS WS / Provider webhook
  -> bridge runtime (runs, durable session mapping, idempotency)
  -> QwenWork Engine -> loopback WeCom WS -> QwenWork
                     <- cumulative text snapshots
                     <- HTTP tool and Stop Hooks
  <- bridge encoder (chat / agent stream and terminal)
```

## Configure

Register using the normal bridge flow with `--mode plugin --engine qwenwork`,
or change an existing bridge Bot to `engine = "qwenwork"`. Leave `engine_bin`,
`model` and `permission_mode` unset. Add the following to that Bot's TOML:

```toml
[bot.engine_options]
listen = "127.0.0.1:52491"
bot_id = "bridge-qwenwork"
secret = "<local-channel-secret>"
hook_secret = "<different-local-hook-secret>"
database_path = "/absolute/path/to/QwenWorkCN/data/agents.db"
```

For `[[bot]]` entries use `[bot.engine_options]` immediately after the selected
Bot's fields. The database must already exist. Give separate local listener
ports and channel credentials to independent desktop instances. One desktop
WeCom channel connects to one engine instance; duplicate authenticated sockets
are rejected. Only loopback binding is allowed. Protect the TOML file with
`chmod 600`; secrets must not be committed.

Configure QwenWork's **WeCom Bot** channel with matching `botId`, `secret` and
`wsUrl = ws://127.0.0.1:52491/ws`. QwenWork owns channel configuration; this
adapter does not rewrite its database or take over an existing channel. The
current desktop Connector exposes enable/restart of configured channels but
channel credential CRUD is UI-only. A release that does not expose `wsUrl`
configuration needs a separately supported setup path; verify before deployment.

Configure HTTP Hooks in QwenWork's user settings, preserving existing hooks:

```json
{
  "hooks": {
    "PreToolUse": [{"matcher":".*","hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]}],
    "PostToolUse": [{"matcher":".*","hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]}],
    "UserPromptSubmit": [{"hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]}],
    "QueryEnd": [{"hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]}],
    "PreCompact": [{"hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]}],
    "PostCompact": [{"hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]}]
  }
}
```

If the installed SDK supports `PostToolUseFailure`, configure it identically to
`PostToolUse` to deliver failed tool results. Unsupported Hook types must not be
advertised as working. The lifecycle Hooks above are required; older desktop versions without
`UserPromptSubmit`/`QueryEnd` must not be advertised as supported. Restart/reload
the desktop Hooks as required by that version, start bridge, then enable the channel. Check:

```sh
curl http://127.0.0.1:52491/health
# qwen_connected=true, faulted=false
```

This endpoint reports the local desktop connection only. BCS connectivity is
managed by bridge's existing plugin/gateway transport.

## Semantics

- Prompts carry no added correlation marker. Bridge persists a conversation
  UUID before delivery. Hooks' SDK `session_id` is matched through `sub_chats`
  and `chats.ext.imConversationId`, scoped to the configured WeCom bot.
- Prefix-growing snapshots produce `chat.delta`. When a snapshot revises
  already emitted text, subsequent deltas are suppressed; the full final text
  is authoritative. Thinking placeholders are not treated as assistant text.
- Pre/Post Hooks produce correlated tool start/result events with JSON inputs
  and outputs. Duplicate deliveries are ignored; an early result waits for its
  start. Missing Hooks fail completion rather than report a partial success.
- `UserPromptSubmit` binds the exact submitted prompt to the SDK's
  `request_set_id`. Only its matching top-level `QueryEnd` can end the main
  request; delayed previous-request and subagent callbacks are ignored. Hook
  prompt text must be preserved by the desktop channel. `Stop` is advisory
  because its hooks may continue execution.
- `PreCompact`/`PostCompact` track a pause inside the same run and conversation.
  They never create a final or reset the durable mapping, run timeout or tool
  bookkeeping. Compact state stays internal; no BCS protocol change is needed.
- A successful turn requires WS text finish, matching successful `QueryEnd`
  and all tool results. The 10-second reconciliation
  window begins only after the main `QueryEnd`, not
  after `Stop` or a text snapshot. Before then the bridge's overall run timeout
  and abort remain authoritative. Missing final or tool results still fail.
  Failed/aborted `QueryEnd` never becomes a successful final. Main `QueryEnd`
  also clears a failed compact attempt that did not emit `PostCompact`.
- Explicit `/compact` is rejected as a session-control prompt: it does not
  provide ordinary assistant text completion. Trigger it via the desktop's
  supported control interface between bridge turns. This adapter supports
  automatic compaction inside ordinary turns through the lifecycle Hooks.
- Cancellation/closed event consumers send `/stop` on the same conversation,
  with a separate request id, and wait up to 10 seconds for the command's final
  confirmation. Current QwenWork invokes its task-stop service before replying.
  Confirmation recognition uses current localized `✅`/`ℹ` prefixes; protocol
  changes require revalidation. A WS ACK alone is insufficient.
- If cancellation cannot be confirmed, health becomes `faulted=true` and new
  turns are fenced. Stop the task in the desktop app before restarting bridge.
  This fence is in memory; a process restart does not prove the old task stopped.
- Model, permissions, workspace and interactive approvals stay in QwenWork.
  Bridge's configured `cwd` remains part of its durable session binding but is
  not forwarded through the IM protocol. `chat.inject` uses bridge's existing
  next-turn queue. Session-control slash commands are rejected as turn prompts.

## Bounds and database cost

Maximum 64 active turns, 128 queued inputs per turn, 8 MiB per HTTP/WS message,
4096 distinct tool calls per turn and 32 MiB of out-of-order pending results.
Queues reject overflow instead of acknowledging lost events. Bridge applies
its existing stream retention budget separately. Listener authentication takes
at most 5 seconds; waiting for a desktop connection takes at most 10 seconds.

Desktop metadata is read-only: a cold Hook session lookup performs one SELECT,
returns at most two metadata rows and rejects ambiguous matches. The positive
mapping is cached until its run ends, so subsequent tool Hooks for that SDK
session perform zero database queries. Cold lookups are serialized, with a
500 ms admission timeout and 200 ms SQLite lock wait. No transaction/lock is
held across event delivery. Unrelated sessions may incur further lookups.

The inspected desktop schema has no `sub_chats.session_id` index: a cold lookup
can scan that table. This is an internal schema dependency, not a stable public
SDK contract; it is suitable for local desktop use and is not a demonstrated
high-concurrency interface. Background/subagent sessions without a direct IM
chat mapping are not forwarded. Binary output, bridge HITL, and live native
inject are not implemented.

## Test

```sh
cd apps/bcs
cargo test -p bcs-bridge-qwenwork -p bcs-bridge-core -p bcs-bridge-app -p bcs-bridge-cli
cargo clippy -p bcs-bridge-qwenwork --all-targets --no-deps -- -D warnings
```

Tests use a simulated desktop peer and temporary SQLite files. They validate
native Rust protocol handling and bridge integration; they do not certify a
real desktop release or a production BCS server. Verify the configured desktop
with one text turn, a tool turn, a follow-up and a long-turn cancellation before
switching an existing Bot. Stop the old client before connecting the new client
with the same BCS identity.
