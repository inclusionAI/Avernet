# OpenClaw `chat.inject` Stopgap

## Summary

OpenClaw `2026.5.12` stores gateway `chat.inject` as transcript-only assistant
messages with `provider: "openclaw"` and `model: "gateway-injected"`. OpenClaw
replay now filters those assistant messages out of the model context, but BCS
still needs `chat.inject` observers to receive group context on the next turn.

Short term, Avernet rewrites only the newly injected transcript entry into a
user message. Long term, BCS should own pending observer context buffering
instead of depending on OpenClaw transcript layout.

## Shared Rewrite Semantics

- Keep calling OpenClaw gateway `chat.inject`; do not trigger a model run.
- Use the returned `messageId` to match exactly one transcript entry.
- Modify only `${sessionId}.jsonl`; do not modify `.trajectory-path.json` or
  `.trajectory.jsonl` sidecar files.
- Preserve `message.content` unchanged.
- Change `message.role` from `assistant` to `user`.
- Remove assistant/model-output fields: `api`, `provider`, `model`,
  `stopReason`, and `usage`.
- If transcript rewrite fails, log a warning and keep `chat.inject` successful.
- Do not migrate historical injected messages.

## Plugin Stopgap

The OpenClaw BCN plugin already resolves the target route and local
`sessions.json` store before calling gateway `chat.inject`.

- Ensure the transcript file exists before injection.
- Prefer `sessionEntry.sessionFile` when it stays inside the sessions directory;
  otherwise fall back to `${sessionId}.jsonl`.
- After gateway `chat.inject` returns, rewrite the returned `messageId` in the
  resolved transcript path.

## Engine Stopgap

OpenClaw `v2026.5.12` has `sessions.describe`, but its `GatewaySessionRow` does
not expose `sessionFile`; it only exposes `sessionId`.

- Add an OpenClaw engine config value `OPENCLAW_SESSION_TRANSCRIPT_DIR`, default:
  `/home/admin/.openclaw/agents/main/sessions`.
- Implement OpenClaw adapter `inject` so engine `chat.inject` no longer uses the
  generic passthrough path.
- In the OpenClaw port, call gateway `chat.inject`; if OpenClaw returns
  `session not found`, call `sessions.patch` and retry once.
- Call `sessions.describe` after a successful inject and derive
  `${OPENCLAW_SESSION_TRANSCRIPT_DIR}/${sessionId}.jsonl`.
- Validate `sessionId` as a safe filename token before building the path.
- Rewrite the returned `messageId` using the shared semantics.

## Tests

- Plugin: gateway `chat.inject` on protocol 3-4 returns `messageId`, and the
  matching transcript entry is rewritten to a user message.
- Engine adapter: `inject` delegates to the OpenClaw port and returns the port
  payload.
- Engine port: successful `chat.inject` plus `sessions.describe` rewrites the
  matching transcript entry.
- Engine port: `session not found` preserves the existing
  `sessions.patch`-then-retry behavior.
