# openclaw-channel-bcn

BCS WebSocket channel plugin for OpenClaw.

## What It Provides

- Registers a `bcs` channel in OpenClaw
- Connects to a configured BCS WebSocket endpoint with auto reconnect and heartbeat
- Accepts `chat.send`, `chat.inject`, and `chat.history`
- Routes inbound group messages into the OpenClaw runtime and sends replies back to BCS
- Persists BCS session state for reconnecting
- Provides BCS group routing and manager-worker task tools:
  - `bcs_route`
  - `bcs_assign_task`
  - `bcs_send_task_message`
  - `bcs_task_complete`

This plugin package intentionally does not include internal HITL, environment detection,
service bot bootstrap, private credentials, or internal endpoint defaults.

## Install From Source

```bash
npm install
npm run build
mkdir -p ~/.openclaw/extensions
ln -sfn "$(pwd)" ~/.openclaw/extensions/openclaw-channel-bcn
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
