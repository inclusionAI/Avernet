# @avernet-plugin/openclaw-channel-bcn

BCS WebSocket channel plugin for OpenClaw.

## What It Provides

- Registers a `bcs` channel in OpenClaw
- Connects to a configured BCS WebSocket endpoint with auto reconnect and heartbeat
- Accepts `chat.send`, `chat.inject`, `chat.abort`, and `chat.history`
- Routes inbound group messages into the OpenClaw runtime and sends replies back to BCS
- Persists BCS session state for reconnecting
- Provides BCS group routing and manager-worker task tools:
  - `bcs_route`
  - `bcs_assign_task`
  - `bcs_send_task_message`
  - `bcs_task_complete`

This plugin package intentionally does not include internal HITL, environment detection,
or internal endpoint defaults.

`chat.abort` is a request/response operation. The plugin registers an
`AbortController` before acknowledging each `chat.send`, then cancels only the
exact `(session_key, run_id)` requested by BCS. Repeated aborts of an already
terminal run succeed with an empty `aborted_run_ids`; unknown or cross-session
run IDs are rejected. The key is the exact value from the original
`chat.send`: group-derived for protocol v2 and the canonical BCS Session ID
for protocol v3.

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
./scripts/singlebox.sh --bcn-plugin-source npm
BCN_PLUGIN_SOURCE=npm ./scripts/singlebox.sh

# pin a version in npm mode (default: latest)
BCN_PLUGIN_SOURCE=npm BCN_PLUGIN_VERSION=1.0.18 ./scripts/singlebox.sh
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
