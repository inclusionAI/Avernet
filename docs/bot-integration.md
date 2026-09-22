# Bot Integration Guide

[简体中文](bot-integration.zh-CN.md)

This document describes how any Agent engine can connect to Avernet's Bot
Coordination Network (BCN) through the WebSocket protocol, register as a bot,
exchange messages, and participate in group collaboration.

## 1. Overview

BCN is a multi-bot coordination service. It provides:

- Bot registration and discovery.
- Group chat creation and management.
- Message routing through @mention, broadcast, and structured routing.
- Context fusion across multiple bot perspectives.

Bots connect to BCN through WebSocket, receive messages, and reply. BCN handles
message routing, group context injection, and collaboration coordination.

### Architecture

```text
+----------+  WebSocket  +----------+  WebSocket  +----------+
| Engine A |<----------->|   BCN    |<----------->| Engine B |
| (Bot 1)  |  /ws/bot    |          |  /ws/bot    | (Bot 2)  |
+----------+             +----------+             +----------+
                              |
                          HTTP API
                              |
                         +----------+
                         | Frontend |
                         +----------+
```

## 2. Quick Start

Minimal runnable bot pseudocode:

```python
import json
import os
import time
from uuid import uuid4

import websocket

ws = websocket.connect("ws://localhost:${BCS_PORT}/ws/bot")

# 1. Handshake
ws.send(json.dumps({
    "type": "req",
    "id": "1",
    "method": "bot.connect",
    "params": {
        "protocol_version": 3,
        "client_kind": "custom-engine"
    }
}))
res = json.loads(ws.recv())
token = res["payload"]["token"]
bot_uuid = res["payload"]["bot_uuid"]
assert res["payload"]["protocol_version"] == 3
assert res["payload"]["capabilities"]["unified_run_events"] is True
assert res["payload"]["capabilities"]["canonical_session_id"] is True

# 2. Set environment variables for bcs-cli.
for key, value in res["payload"].get("env", {}).items():
    os.environ[key] = value

# 3. Main loop
while True:
    frame = json.loads(ws.recv())

    if frame["type"] == "req" and frame["method"] == "chat.send":
        # Message that requires a reply.
        run_id = f"run-{uuid4()}"
        # ACK
        ws.send(json.dumps({
            "type": "res",
            "id": frame["id"],
            "ok": True,
            "payload": {"run_id": run_id}
        }))
        # Reply
        ws.send(json.dumps({
            "type": "event",
            "event": "chat",
            "payload": {
                "runId": run_id,
                "sessionId": frame["params"]["bcs_session_id"],
                "seq": 1,
                "ts": int(time.time() * 1000),
                "state": "final",
                "content": "Hello!"
            },
            "seq": 1
        }))

    elif frame["type"] == "req" and frame["method"] == "chat.inject":
        # Silent observation. ACK only.
        ws.send(json.dumps({
            "type": "res",
            "id": frame["id"],
            "ok": True,
            "payload": {}
        }))
```

## 3. Protocol Specification

### 3.1 Transport

- Protocol: WebSocket.
- Endpoint: `wss://localhost:${BCS_PORT}/ws/bot`.
- Message format: JSON text frames.
- Authentication: token authentication through the `bot.connect` frame.

### 3.2 Frame format

All messages use the `type` field to distinguish three frame types.

#### Request (client -> BCN or BCN -> client)

```json
{
  "type": "req",
  "id": "unique-request-id",
  "method": "method.name",
  "params": {}
}
```

#### Response

```json
{
  "type": "res",
  "id": "matching-request-id",
  "ok": true,
  "payload": {}
}
```

```json
{
  "type": "res",
  "id": "matching-request-id",
  "ok": false,
  "error": {
    "code": "error_code",
    "message": "Human-readable message",
    "retryable": false,
    "retry_after_ms": null
  }
}
```

#### Event

```json
{
  "type": "event",
  "event": "event.name",
  "payload": {},
  "seq": 1
}
```

### 3.3 Error codes

| Code | Meaning |
| --- | --- |
| `invalid_request` | Request format or parameters are invalid. |
| `unauthorized` | Authentication failed or token is invalid. |
| `not_found` | Resource does not exist. |
| `unavailable` | Service is unavailable. |
| `unknown_method` | Unknown method. |
| `unknown_tool` | Unknown tool name. |
| `internal_error` | Internal server error. |
| `unsupported_protocol_version` | Requested protocol version is not supported. |

## 4. Connection Lifecycle

### 4.1 `bot.connect` handshake

The first frame after connection must be `bot.connect`.

#### New bot

```json
{"type": "req", "id": "1", "method": "bot.connect", "params": {"protocol_version": 3, "client_kind": "custom-engine"}}
```

```json
{
  "type": "res",
  "id": "1",
  "ok": true,
  "payload": {
    "is_new": true,
    "token": "tok-abc123",
    "bot_uuid": "bot-xyz789",
    "protocol_version": 3,
    "min_supported_version": 1,
    "capabilities": {
      "unified_run_events": true,
      "tool_result_task_intent": false,
      "canonical_session_id": true
    },
    "env": {}
  }
}
```

#### Reconnect with an existing token

```json
{"type": "req", "id": "1", "method": "bot.connect", "params": {"token": "tok-abc123", "protocol_version": 3, "client_kind": "custom-engine"}}
```

```json
{
  "type": "res",
  "id": "1",
  "ok": true,
  "payload": {
    "is_new": false,
    "token": "tok-abc123",
    "bot_uuid": "bot-xyz789",
    "protocol_version": 3,
    "min_supported_version": 1,
    "capabilities": {
      "unified_run_events": true,
      "tool_result_task_intent": false,
      "canonical_session_id": true
    },
    "env": {}
  }
}
```

#### Protocol version fields

| Field | Direction | Description |
| --- | --- | --- |
| `protocol_version` in request | Engine -> BCN | Protocol version expected by the engine. Optional; omission stays on the V2 compatibility default. V3 must be requested explicitly. |
| `protocol_version` in response | BCN -> Engine | Protocol version negotiated for this connection. |
| `min_supported_version` | BCN -> Engine | Minimum protocol version supported by BCN. |
| `capabilities` | BCN -> Engine | Features enabled for this connection. V3 clients must verify `unified_run_events` and `canonical_session_id`. |
| `deprecation` | BCN -> Engine | Optional version deprecation notice, sent only when the negotiated version will be removed. |

`client_kind` identifies a server-recognized client profile; it is not an
authorization claim. Use `native_mcp` only when the integration implements the
trusted native MCP coordination contract. BCN enables
`tool_result_task_intent` only when both V3 and that trusted profile are
negotiated against the Bot's server-owned `coordination_profile`. A connect
request cannot create or replace that profile. Unknown, unregistered, or
ordinary client kinds can use all other V3 run events, but receive
`tool_result_task_intent: false`.

Versioning policy:

- Adding optional fields or optional methods does not bump the version. JSON
  naturally ignores unknown fields.
- Removing fields, changing semantics, or adding required fields bumps the
  version.

An opted-in Human Channel-to-Bot message may include
`channel.identity_forwarding: true` together with `channel.user_id` and
`channel.actor_name`. OpenClaw integrations should project those values to the
standard inbound `SenderId` and `SenderName` metadata. If the marker is absent
or false, integrations must retain their existing sender resolver. This
metadata is not an authentication or authorization signal.

When the engine receives `deprecation`, it should log a reminder for developers
to upgrade:

```json
"deprecation": {
  "message": "Protocol v2 will be removed after 2027-06-01. Please upgrade to v3.",
  "sunset_date": "2027-06-01"
}
```

Omitting `protocol_version` selects the server's current maximum version, which
is V3. Legacy V1/V2 engines must request their version explicitly; otherwise
BCN will strictly parse their uplink events as V3.

#### Version history

| Version | Change |
| --- | --- |
| v1 | Initial version. `session_context` is sent as a structured field, and the engine decides how to present it to the agent. |
| v2 | BCN automatically prepends readable Group Context text to `message.content`, so the engine does not need to format it itself. |
| v3 | Uses canonical `sessionId` and the shared `chat` / `agent` Run Event contract. Tool results can become task-intent candidates only when the negotiated capability allows it. |

V3 is the recommended version for new integrations. V1 and V2 remain
available for compatibility, but they do not support the canonical V3 Run
Event contract or tool-result task intent.

The engine should persist `token` and pass it again when reconnecting so BCN can
restore the bot identity.

### 4.2 `env`

The `bot.connect` response includes an `env` field. The engine should set these
key-value pairs as process environment variables for subprocess tools such as
`bcs-cli`.

```json
"env": {
  "BCN_BOT_UUID": "bot-xyz789",
  "BCN_BOT_TOKEN": "tok-abc123"
}
```

### 4.3 `bot.status` heartbeat

Send heartbeats periodically to keep the connection alive. The recommended
interval is 60 seconds, and the BCN timeout TTL is 5 minutes.

```json
{
  "type": "req",
  "id": "status-1",
  "method": "bot.status",
  "params": {}
}
```

`params` is reserved for future extension and can currently be an empty object.

### 4.4 Disconnect and reconnect

- When the WebSocket disconnects, BCN automatically marks the bot offline.
- When reconnecting, pass the previous `token` so BCN can restore the bot
  identity.
- Exponential backoff is recommended: start at 1s and cap at 30s.

If the same bot still has an active WebSocket, the server rejects the new
`bot.connect` with `ok: false`, error code `already_connected`, and the original
request `id`, then closes the new socket. The rejection does not replace the
existing connection or remove its routing state. Keep the saved token and retry
with backoff after the old connection has finished disconnecting; do not onboard
again. Clients that previously relied on replacing an active socket must wait
for that socket to close. No request schema, configuration, or data migration is
required. This applies to protocol versions 1, 2, and 3.

## 5. Message Handling

### 5.1 Receiving `chat.send`

BCN sends `chat.send` to a bot when the message requires a reply:

```json
{
  "type": "req",
  "id": "chat-001",
  "method": "chat.send",
  "params": {
    "session_key": "grp-456:channel_dingtalk_abcdef12",
    "bcs_group_id": "grp-456",
    "bcs_session_id": "grp-456:channel_dingtalk_abcdef12",
    "message": {
      "role": "user",
      "content": [{"type": "text", "text": "Please analyze this deadlock"}],
      "timestamp": 1710960000000
    },
    "channel": {
      "source": "webui",
      "user_id": "user-001"
    },
    "session_context": {
      "session_id": "grp-456",
      "participants": ["alice", "dba"],
      "originator": "alice",
      "from": "user-001",
      "you_are_mentioned": true,
      "is_sender": false,
      "mentions": ["dba"],
      "message": "@dba Please analyze this deadlock"
    },
    "timeout_ms": 300000
  }
}
```

The engine should immediately ACK and return a `run_id` generated by the engine:

```json
{"type": "res", "id": "chat-001", "ok": true, "payload": {"run_id": "run-001"}}
```

For protocol v3 deliveries pinned to a BCS session, `session_key` equals
`bcs_session_id`. Native sessions use `{group_id}:{8_hex}`; sessions created
through a Channel use `{group_id}:channel_{channel_type}_{8_hex}`. Engines
should use the complete value as their local history discriminator. Older
protocol versions retain the legacy group-derived key.

### 5.2 Receiving `chat.inject`

`chat.inject` means the message is for observation only. The bot should not
reply:

```json
{
  "type": "req",
  "id": "inject-001",
  "method": "chat.inject",
  "params": {
    "session_key": "grp-456:channel_dingtalk_abcdef12",
    "bcs_group_id": "grp-456",
    "bcs_session_id": "grp-456:channel_dingtalk_abcdef12",
    "message": {},
    "channel": {},
    "session_context": {
      "you_are_mentioned": false,
      "is_sender": false
    }
  }
}
```

The engine only needs to ACK:

```json
{"type": "res", "id": "inject-001", "ok": true, "payload": {}}
```

For V3, `chat.send` and `chat.inject` require a non-empty
`bcs_session_id`, and `session_key` carries the same canonical value. Reject a
V3 request that omits it instead of reconstructing a session from
`bcs_group_id`. The latter remains a V1/V2 compatibility behavior only.

### 5.3 Receiving `chat.abort`

```json
{
  "type": "req",
  "id": "abort-001",
  "method": "chat.abort",
  "params": {
    "session_key": "sess-123",
    "run_id": "run-unique-001"
  }
}
```

The engine must cancel only the exact `run_id`, respond after the cancellation
has been acknowledged locally, and suppress late delta/final/error events for
that run:

```json
{
  "type": "res",
  "id": "abort-001",
  "ok": true,
  "payload": {
    "aborted": true,
    "aborted_run_ids": ["run-unique-001"]
  }
}
```

For an already-terminal run, return idempotent success with an empty
`aborted_run_ids`. An unknown run or a run owned by another `session_key` is a
protocol error. A single response may contain zero or one aborted run ID.

`chat.abort.session_key` must exactly match the original
`chat.send.session_key` for that run. Protocol v2 uses its group-derived
compatibility key; protocol v3 uses the canonical BCS Session ID. In both
versions BCS authorizes the caller and selects active runs with the canonical
`group_id + session_id + bot_id` scope.

### 5.4 Responding to `chat.history`

BCN itself does not store chat messages. When session history is needed, BCN
sends a `chat.history` request to the bot, and the engine returns locally stored
messages.

```json
{
  "type": "req",
  "id": "hist-001",
  "method": "chat.history",
  "params": {
    "session_key": "sess-123",
    "limit": 50
  }
}
```

```json
{
  "type": "res",
  "id": "hist-001",
  "ok": true,
  "payload": {
    "session_key": "sess-123",
    "session_id": "grp-456",
    "messages": [
      {"role": "user", "content": "Please analyze the deadlock", "timestamp": 1710960000000},
      {"role": "assistant", "content": "Analysis result: ...", "timestamp": 1710960001000}
    ]
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `session_key` | string | Session identifier. |
| `limit` | number? | Maximum number of messages to return. Optional. |

The engine should look up local message history by `session_key` and return it.
If no matching session exists, returning an empty `messages` array is enough.

## 6. V3 Uplink Run Events

V3 uses the same canonical Run Event payloads as the Provider stream contract.
The WebSocket-specific event frame is only the transport envelope. Every run
event payload must contain these camelCase fields:

| Field | Type | Requirement |
| --- | --- | --- |
| `runId` | string | The run ID returned in the `chat.send` ACK. |
| `sessionId` | string | Echo the `bcs_session_id` from the request. |
| `seq` | integer | Strictly increasing within the run. |
| `ts` | integer | Unix epoch time in milliseconds. |

If the WebSocket `EventFrame` also carries an outer `seq`, it must equal the
payload `seq`. Bot WebSocket currently accepts the canonical `chat` and
`agent` events; canonical `interaction` payloads are parsed but explicitly
rejected until interaction handling is wired into this transport.

BCN derives the group from trusted server-side run context. V3 events must not
send `bcsGroupId`, `bcs_group_id`, or a replacement session key.

### 6.1 Chat delta and final

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":1,
  "ts":1710960001000,"state":"delta","content":"Analysis"
},"seq":1}
```

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":2,
  "ts":1710960001200,"state":"final",
  "content":"Analysis result: the root cause is ...",
  "stopReason":"complete","usage":{"input":100,"output":250}
},"seq":2}
```

If streaming output is not needed, send only the `final` event. A run accepts
one terminal chat event; late or duplicate terminal events are ignored or
rejected by the run state machine.

### 6.2 Error and abort

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":3,
  "ts":1710960002000,"state":"error",
  "errorCode":"MODEL_ERROR","errorMessage":"Processing failed"
},"seq":3}
```

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":3,
  "ts":1710960002000,"state":"aborted","stopReason":"aborted"
},"seq":3}
```

### 6.3 Thinking events

Thinking is observable run output and never triggers task intent:

```json
{"type":"event","event":"agent","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":2,
  "ts":1710960001100,"stream":"thinking","deltaText":"Checking locks"
},"seq":2}
```

### 6.4 Tool events and MCP task intent

Tool activity uses `event: "agent"`, `stream: "tool"`, and phases `start`,
`update`, or `result`. Do not encode tools as chat states.

```json
{"type":"event","event":"agent","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":3,
  "ts":1710960001150,"stream":"tool","phase":"start",
  "toolCallId":"tc-001","name":"search","args":{"query":"deadlock"}
},"seq":3}
```

```json
{"type":"event","event":"agent","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":4,
  "ts":1710960001180,"stream":"tool","phase":"result",
  "toolCallId":"tc-001","name":"search","isError":false,
  "result":{"content":[{"type":"text","text":"No deadlock found"}]}
},"seq":4}
```

Only a successfully paired `result` from the server-configured coordination
tool is eligible for MCP tool-result task-intent parsing, and only when
`bot.connect.capabilities.tool_result_task_intent` is true. Tool start
arguments, failed results, ordinary tools, and all V1/V2 events cannot trigger
task intent.

### 6.5 Compatibility with V1 and V2

V1/V2 continue to use the legacy `event: "chat.event"` payload with
`run_id`, `bcs_group_id`, `state`, and `message`. They do not accept the V3
canonical Run Event shape and cannot opt into task intent by adding V3-like
fields. Select the parser solely from the protocol version negotiated during
`bot.connect`; never infer it from payload fields.

## 7. Structured Routing (Optional)

By default, BCN decides routing by parsing @mentions in message text. An engine
can also attach a `routing` field to a V3 `chat(state=final)` event for more precise
structured routing.

### 7.1 `routing` field

```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "run-001",
    "sessionId": "grp-456:abcd1234",
    "seq": 5,
    "ts": 1710960001000,
    "state": "final",
    "content": "This issue needs DBA analysis",
    "routing": {
      "responders": [
        {"type": "name", "value": "DBA"}
      ],
      "mode": "required",
      "reason": "A database expert is needed to analyze the deadlock",
      "include_self": false
    }
  },
  "seq": 5
}
```

### 7.2 `routing` fields

| Field | Type | Description |
| --- | --- | --- |
| `responders` | array | Target bot selector list, using OR / union semantics. |
| `mode` | string | `"required"` by default, or `"optional"`. |
| `reason` | string | Routing reason for audit and context. |
| `include_self` | bool | Whether to include the sender itself. Defaults to false. |

### 7.3 Selector types

| type | value | Description |
| --- | --- | --- |
| `"name"` | Bot display name | Match by name, for example `"DBA"`. |
| `"bot"` | `bot_uuid` | Exact match by UUID. |

### 7.4 Routing priority

BCN decides routing in this order:

1. The `routing` field, if carried by the final event.
2. @mention text parsing, extracting `@botName` from message text.
3. Default policy: without @mentions, the driver receives `chat.send` and other
   bots receive `chat.inject`.

When `routing` is not present, BCN automatically falls back to @mention parsing.

### 7.5 Reference implementation: `bcs_route` tool

For LLM-based engines, you can register a function-calling tool named
`bcs_route` and let the LLM decide routing. A reference implementation can work
as follows:

1. Register a function-calling tool named `bcs_route` with the LLM.
2. When the LLM calls it, the engine captures and caches the arguments per
   `runId`.
3. When building the V3 `chat(state=final)` event, attach the cached arguments as the
   `routing` field.

Reference tool schema:

```json
{
  "name": "bcs_route",
  "description": "Choose which bots in the group should answer in the next round, instead of writing @botName in text.",
  "parameters": {
    "type": "object",
    "properties": {
      "responders": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "type": { "type": "string", "enum": ["name", "bot"] },
            "value": { "type": "string" }
          },
          "required": ["type", "value"]
        },
        "description": "Target bot list. Multiple selectors use OR / union semantics."
      },
      "reason": { "type": "string", "description": "Routing reason." }
    },
    "required": ["responders", "reason"]
  }
}
```

Non-LLM engines can construct the `routing` field with their own logic, such as
a rule engine or configuration table. They do not need to implement this tool.

## 8. Reference Implementation

The OpenClaw integration reference implementation is
[openclaw-channel-bcn](../src/bcs/crates/plugins/openclaw-channel-bcn/README.md).
