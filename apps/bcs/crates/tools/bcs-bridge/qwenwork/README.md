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

## Initialize the desktop connection

In a binary with the `init` command, register a QwenWork Bot first, then run:

```sh
bcs-bridge --config ~/.bcn-bridge/qwenwork.toml init --engine qwenwork
```

Use `0.1.4-beta.2` or newer; that distribution includes the initializer.

Initialization detects exactly one installed `QwenWorkCN` or `QwenWork` database,
validates its chat metadata schema read-only, generates separate channel and Hook
credentials, and writes the registered Bot's engine options. It backs up both
files, atomically writes private files, merges the required Hooks while preserving
other Hooks, and reuses credentials on repeat execution. It never edits the
WeCom channel database. Desktop options are also saved privately to
`bcs-bridge.toml` beside the selected desktop `settings.json`. Profiles reuse this
shared listener, channel identity and Hook credential; BCN credentials and session
state remain in each profile. Old profiles resolve the shared desktop options at
startup. Stop the active bridge before starting another profile.

The shared file survives clearing `~/.bcn-bridge`. For older installations with
only Hooks remaining, initialization can recover the Hook credential from a
complete, consistent bridge Hook set. Channel credentials cannot be recovered
from Hooks: configure the channel once with the newly printed values in that
case. Unrecognized or conflicting Hooks fail without modifying configuration.

Fill the printed WebSocket URL, Bot ID and Secret in the desktop's WeCom Bot
channel. Reload desktop Hooks, start bridge and enable the channel. The Secret
is printed deliberately for local setup; do not paste that output into issues.

If detection is ambiguous or your installation uses different paths, append
`--option database_path=/absolute/path/agents.db` and/or
`--option settings_path=/absolute/path/settings.json`. An alternate loopback
listener can be chosen with `--option listen=127.0.0.1:52493`; use the resulting
printed URL and health command. The command requires exactly one QwenWork Bot
in the selected bridge configuration.

## MCP coordination tools

QwenWork wraps native MCP calls in
`mcp__qw-builtin__qwenwork_mcp_tool_call`. The bridge unwraps its object-valued
`arguments` and qualified `toolName` for the start event, then uses that same name
for the result with the matching tool call ID, including out-of-order Hooks.
Unknown or malformed wrappers remain unchanged. Output cannot select a tool's
identity or grant coordination permissions.

Use a dedicated Provider with `coordination.mode = "native_mcp"` and exact
`tool_name_mapping` entries for the desktop MCP server's coordination echoes:
`bcs_assign_task` and `bcs_task_complete`. For a server named `bcs-mcp`, map
`mcp__bcs-mcp__bcs_assign_task` to `bcs_assign_task`, and likewise for completion.
Status and cancel tools call the task API directly and do not produce coordination
echoes. The retired worker-message tool is decoded only for compatibility. Do not map the generic QwenWork wrapper to a coordination
tool. BCS retains responsibility for Provider authorization and intent validation.

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
# Optional: enable BCS AskUserQuestion control on a Unix desktop.
mcp_socket_path = "/absolute/path/to/desktop-mcp.sock"
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
    "PreToolUse": [
      {"matcher":"^(?!AskUserQuestion$).*","hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":3}]},
      {"matcher":"AskUserQuestion","hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer <different-local-hook-secret>"},"timeout":300}]}
    ],
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
- Model, permissions, workspace and execution approvals stay in QwenWork.
  With `mcp_socket_path`, AskUserQuestion forms are routed through BCS (see below).
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
chat mapping are not forwarded. Binary output, execution-approval HITL, and live native
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

## AskUserQuestion through BCS

This adapter implements the existing BCS `interaction/requested` and
`interaction.resolve` contract with `kind: ask_user`. Enable it by configuring
`mcp_socket_path` to the running desktop's local Unix MCP socket and installing
one dedicated synchronous AskUserQuestion PreToolUse HTTP Hook as shown above.
The socket is version/platform dependent; do not assume a portable default.
Without this option, existing desktop-owned interaction behavior is retained.
Windows named pipes are not implemented by this adapter.

The Hook parks the original tool invocation while BCS presents its questions.
BCS submits `{action:"submit", answers:{question_1:{values:["choice"]}}}` or
`{action:"cancel"}` via the existing interaction endpoint. Single-select,
multi-select, free text and explicit empty selections are mapped to native
answers. The adapter supplies both header and full question text keys: the
1.2.5 desktop normalizes header keys, while the bundled worker consumes question
text keys. Supplying only headers updates the card but can leave the model
seeing an unanswered tool result.

After submission, bridge releases that original PreToolUse Hook, waits for its
exact toolCallId to appear as a pending AskUserQuestion in the correlated task,
and invokes `qwenwork_task_submit_response` over local MCP. Subsequent question
Hooks remain parked while that answer is submitted; a stale answer cannot be
sent to the next question. Already answered/missing questions, MCP errors or
uncertain delivery fail the run and trigger normal cancellation. No answer is
injected as a new chat message. Only a confirmed native answer emits resolved;
normal completion still requires QueryEnd, final text and paired tool results.
Cancel returns a native deny; abort releases the waiter and stops the desktop
run. Secret-marked forms are rejected before emitting question/tool payloads.
Duplicate resolves and Hook deliveries do not resubmit the native answer.

Question waits are bounded to 240 seconds (or the shorter overall run deadline).
The Hook timeout must be longer, e.g. 300 seconds; do not leave the ordinary
three-second Hook on AskUserQuestion or configure another short matching Hook.
At most 128 questions are accepted per run. Each submitted question adds one
read-only SQLite metadata lookup (at most two rows, 200 ms lock wait). One native detail query is scoped to that task and returns one recent message.
The desktop nevertheless loads the task's whole history internally (O(history)),
so bridge never repeats this query while waiting. Explicit no-pending answer
rejections allow at most ten submission attempts with backoff (200 ms to one
second) within ten seconds; those attempts inspect task/subchat metadata and
in-memory approvals rather than history. Socket calls have five-second
deadlines and an eight-MiB response bound. No background task/history polling is
introduced, and a lost mutation response is never retried.

**Desktop limitation:** QwenWorkCN 1.2.5 currently instructs models in IM
sessions not to invoke AskUserQuestion because IM cards cannot be rendered.
Live probes have demonstrated a native question and the response API, but the
opt-in bridge round-trip test also observed models declining to invoke the
question tool. This adapter does not rewrite that desktop policy. Therefore a
passing simulated bridge/MCP test does not certify that default IM sessions
will ask questions; desktop support for headless question delegation is still
needed for reliable use. PreToolUse `allow` plus updatedInput does not bypass
this desktop permission flow, and PermissionRequest HTTP Hooks were not invoked
in the tested desktop-managed flow.

Tests: `cargo test -p bcs-bridge-qwenwork`. The ignored
`live_desktop_question_roundtrip` test requires explicit `QWENWORK_LIVE_CONFIG`,
`QWENWORK_LIVE_SOCKET` and `QWENWORK_LIVE_PROMPT` values pointing to private local
configuration. It never passes merely because a model prints the chosen answer:
it requires an actual requested interaction and native tool result.

When changing the listener with `init --option listen=...`, initialization removes
only Hooks matching the previously saved listener URL and Hook credential.
Other Hooks, including those sharing that URL with another credential, are preserved.
A conflict at the new listener leaves both configuration files unchanged.

## Hook migration and cleanup (beta.3)

If `PostToolUseFailure` was configured for a compatible SDK, repeated init retains
its matcher/timeout and listener migration updates its URL and credential. Init
never adds this optional event on desktops that have not opted into it.

Disable the WeCom channel and run `bcs-bridge stop`, then
`bcs-bridge --config <registered-config> deinit --engine qwenwork` before removing
the binary/config. Deinit removes only HTTP Hooks matching this Bot's saved URL
and credential, including optional failure Hooks; it preserves other settings,
creates a private backup and never reads/writes the desktop database. Reload
Hooks afterward. Init persists the selected `settings_path` in engine options;
older configurations using custom paths must pass it explicitly to deinit.
The optional settings_path field requires beta.3; restore a pre-init backup before
rolling its configuration back to beta.2. Configuration symlinks are preserved.
Backups contain credentials: keep them private, and remove superseded backups
manually when rollback is no longer needed. Cleanup does not revoke BCS tokens.

QwenWorkCN 1.2.5's bundled SDK treats HTTP transport failures as non-blocking
errors and continues PreToolUse when no decision is produced. Isolated execution
of the bundled HTTP/aggregation/PreToolUse methods with a closed localhost port
confirmed this behavior; a full model-driven desktop round trip was not performed.
A slow endpoint
can still delay a tool until its Hook deadline (300 seconds for opted-in questions).
This is version-specific behavior, not a guarantee for future SDKs. Always remove
unused global Hooks; do not rely on the bridge being unreachable as cleanup.


## Manager–Worker task handles

`bcs_send_task_message` is retired from model-visible tools. Worker final replies
are automatically returned as task results. When blocked, end the turn and state
what is needed; the Manager assigns a follow-up to the same Worker, including
necessary context. Do not use old worker-message examples as invocation guidance.

Managers use `bcs_assign_task` (optional positive `timeout_s`), `bcs_task_status`
(1–20 refs, on demand only), and `bcs_cancel_task` (ref and reason). `task_ref` is
`T-` plus eight hex digits; ambiguous refs require a longer prefix. `stored` is not
admission or completion. `unknown`/`not_found` never proves stopped or safe to
resend. Cancellation waits briefly for confirmation, never exposes `cancelling`,
and does not roll back side effects. FIFO is not business idempotence.

The server consumes old task.message/v2 echoes for one compatibility release;
that compatibility path is not a newly exposed tool. Remove it only after old
producers and queued work have drained (cached payloads live for 24 hours).
