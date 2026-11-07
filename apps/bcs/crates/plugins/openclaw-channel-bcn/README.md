# @avernet-plugin/openclaw-channel-bcn

BCS WebSocket channel plugin for OpenClaw.

## What It Provides

- Registers a `bcs` channel in OpenClaw
- Connects to a configured BCS WebSocket endpoint with auto reconnect and heartbeat
- Accepts `chat.send`, `chat.inject`, `chat.abort`, and `chat.history`
- Runs BCS messages as OpenClaw direct chats and sends replies back to BCS
- Persists BCS session state for reconnecting
- Provides BCS group routing and manager-worker task tools:
  - `bcs_route`
  - `bcs_assign_task`
  - `bcs_task_status` / `bcs_cancel_task`
  - `bcs_task_complete`

This plugin package intentionally does not include internal HITL, environment detection,
or internal endpoint defaults.

`chat.abort` is a request/response operation. The plugin registers an
`AbortController` before acknowledging each `chat.send`, then cancels only the
exact `(session_key, run_id)` requested by BCS. Repeated aborts of an already
terminal run succeed with an empty `aborted_run_ids`; unknown or cross-session
run IDs are rejected. The plugin requires protocol V3, so the key is the
canonical BCS Session ID from the original `chat.send`.

`session.delete` requires the canonical `bcs_session_id`; optional
`bcs_group_id` must match it. Group-only requests are rejected. The plugin
resolves and deletes only that Session for the current account, including after
a restart; it never deletes every Session in a Group. See the
[deletion contract](../../../docs/session-delete-protocol.md).

## 1.0.26 patch: OpenClaw direct-chat sessions

BCS owns group participation, routing and turn selection. The plugin uses
OpenClaw `direct` peers and `ChatType: direct` for both `chat.send` and
observe-only `chat.inject`; OpenClaw group activation and mention policies no
longer decide whether a BCS-dispatched turn should run. The BCS GroupContext
header remains in the message body, including participants, sender and routing
instructions, and BCS group/task tools retain their existing Session scope.
An intentionally silent or empty agent run can still produce `NO_REPLY`.

Each canonical BCS Session ID becomes a synthetic direct peer. The plugin
locally enforces `session.dmScope: per-account-channel-peer` for BCS processing
in both the default scope and BCS route-binding overrides without changing the
host configuration, so accounts and BCS Sessions do not
share OpenClaw's default main DM history. Send, inject and uncached history
lookups use the same direct routing rules; reply destinations remain BCS IDs.

Existing OpenClaw `group` transcripts are retained but are not copied into the
new direct sessions. After upgrading, each BCS Session starts a new OpenClaw
history. Peer-specific OpenClaw bindings for BCS must use `kind: direct`;
channel/account bindings continue to apply. Rolling back selects the previous
group histories again. BCS protocol V3 and persisted BCS history are unchanged.

## 1.0.25 patch

Fixes task tool Session scoping on V3 connections. Version 1.0.24 sends a bare
Group ID for task control RPCs when V3 separates Group and Session IDs, causing
assignment persistence failures. Upgrade to 1.0.25 to preserve the originating
Session for assignment, progress messages and completion. No protocol or schema
migration is required.

## Bot WebSocket Protocol

Version `1.0.24` and later require Bot WebSocket protocol V3. After `bot.connect`
negotiates `unified_run_events` and `canonical_session_id`, every run event is
sent with the canonical `runId`, `sessionId`, `seq`, and `ts` fields. Assistant
output uses `event: "chat"` with `state` and `content`; tool, thinking,
lifecycle, assistant, and error streams use a flat `event: "agent"` payload.
The plugin does not emit the legacy `chat.event`, `run_id`, `bcs_group_id`,
or `message` run-event fields on a V3 connection, and chat text is never sent
through the legacy `deltaText` compatibility field.

Task control RPCs (`task.dispatch`, `task.message`, and `task.complete`) retain
their existing session-scoped `group_id` format: `<group_id>:<session_suffix>`.
The plugin uses the canonical Session ID from `chat.send` when invoking these
RPCs, including when V3 supplies the group and Session IDs separately. This
keeps task dispatch, progress and completion within the originating Session.

## Install From npm

```bash
openclaw plugins install npm:@avernet-plugin/openclaw-channel-bcn
```

## Install From Source

```bash
npm install
npm run build
mkdir -p ~/.openclaw/extensions
ln -sfn "$(pwd)" ~/.openclaw/extensions/openclaw-channel-bcn
```

## Selecting the Plugin Source in singlebox

`scripts/singlebox.sh` can load this plugin two ways:

- **source** (default): builds `src/bcs/crates/plugins/openclaw-channel-bcn` from the repo.
- **npm**: installs `@avernet-plugin/openclaw-channel-bcn` via `openclaw plugins install`.

Select with the flag or env var (flag wins):

```bash
# npm mode
./singlebox/singlebox.sh --bcn-plugin-source npm
BCN_PLUGIN_SOURCE=npm ./singlebox/singlebox.sh

# pin a version in npm mode (default: latest)
BCN_PLUGIN_SOURCE=npm BCN_PLUGIN_VERSION=1.0.26 ./singlebox/singlebox.sh
```

## Configure

OpenClaw starts the BCS channel runtime only when `channels.bcs` is explicitly configured.
The public package requires an explicit `bcsUrl` or `BCS_URL`.

```json
{
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "wss://your-bcs.example/ws/bot"
    }
  }
}
```

If `channels.bcs` is missing, or neither `channels.bcs.bcsUrl` nor `BCS_URL` is set,
the plugin can still load and register, but the BCS WebSocket runtime will not start.

## Bot Identity and Service Bots

The plugin reads `~/.credentials` when the file is present. It accepts the following
key-value format:

```bash
BOT_TYPE=personal
BOT_ID=<bot id>
OWNER_ID=<owner id>
```

`ENTITY_ID` is supported as a legacy fallback when `OWNER_ID` is absent. When both
identity fields are available, the WebSocket `bot.connect` request uses
`BOT_ID:OWNER_ID` (or `BOT_ID:ENTITY_ID`) as `bot_id`. This credentials identity takes
priority over `channels.bcs.botId`, `BCS_BOT_ID`, and a mismatched saved session.

When `BOT_TYPE=service`, the plugin creates `.bcs/session.json` below the resolved Bot
data directory if it does not already exist, then skips the BCS WebSocket connection.
If `BOT_TYPE` is absent from the credentials file, the `BOT_TYPE` environment variable
is used as a fallback. Set `BCS_IGNORE_CREDENTIALS=1` to disable credentials identity,
service Bot detection, and service Bot session bootstrap, for example in a local
multi-Bot environment.

The Bot data directory is resolved in this order: `BOT_DATA_DIR`, the OpenClaw runtime
session store, `OPENCLAW_DATA_DIR`, then `~/.openclaw`. Public installations must still
configure `channels.bcs.bcsUrl` or `BCS_URL`; this package does not provide private
endpoint defaults.

## OpenClaw compatibility tests

The repository compatibility harness installs exact, published OpenClaw
versions from this package's declared `openclaw.compat.pluginApi` floor through
the current non-beta `latest` release. It probes real SDK exports and types,
then runs `chat.send` through a local deterministic model and a real OpenClaw
gateway:

```bash
scripts/openclaw_compat.sh --version 2026.3.28
scripts/openclaw_compat.sh --max-workers 2
```

Reports are written to `scripts/.dependencies/compat/openclaw/reports/`.

## Full Example

```json
{
  "channels": {
    "bcs": {
      "enabled": true,
      "bcsUrl": "ws://127.0.0.1:21000/ws/bot",
      "botId": "openclaw-bot",
      "botName": "OpenClaw Agent",
      "capabilities": {
        "summary": "AI Agent",
        "domains": ["general"],
        "skills": ["chat"],
        "scopes": ["chat"]
      },
      "heartbeatIntervalMs": 60000,
      "reconnectIntervalMs": 5000,
      "connectionTimeoutMs": 10000
    }
  }
}
```

## Environment Fallbacks

- `BCS_URL`
- `BCS_BOT_ID`
- `BCS_BOT_NAME`
- `BCS_BOT_SUMMARY`
- `BCS_BOT_DOMAINS`
- `BCS_BOT_SKILLS`
- `BCS_BOT_SCOPES`


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
