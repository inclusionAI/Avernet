# OpenClaw session-key recovery

`chat.resume` is optional and does not replace `chat.send` or `chat.history`.
Only the OpenClaw transport dispatch gains this lookup; other engines continue
through their existing relay paths.

- With `resumeTicket` present (including an invalid/empty value), retain the
  previous ticket lookup and response semantics. Never fall back to a session
  lookup after an invalid ticket.
- Without a ticket, require a nonempty `sessionKey`. Read `chat.history` with
  `limit: 1` through the connection's existing authenticated relay before
  exposing cached output. Fail closed if that read fails or no relay exists.
  This intentionally uses the same read-access boundary as history, not a new
  owner/editor policy or a caller-supplied identity assertion. Deployment must
  retain its authenticated Bot/proxy WebSocket access boundary.
- Select the sole unfinished run, or the most recently finished retained run.
  Multiple unfinished runs yield `AMBIGUOUS_RUN`. No record yields
  `{resumed: false, reason: "NO_ACTIVE_RUN"}`; exhausted cache yields
  `{resumed: false, reason: "CACHE_LIMIT"}`.
- Success preserves `resumed`, `runId`, `state`, `replayCount`, and
  `lastResumeSeq`. Replay all cached events, then live events; no text snapshot
  is added. Completed runs retain their existing 180-second TTL.
- Successful attachment also includes optional `startedAt` (server Unix
  milliseconds). Old clients can ignore it. It bounds history reconciliation
  when the engine omits run/message identifiers; it is not an access credential.
- OpenClaw sends cache recovery events by default. Omit `resumeEnabled` on updated adapters; explicit `true` remains compatible, and explicit `false` opts out. Other engines are unchanged. Mixed-version clients may retain `true` until older adapters are upgraded.
- Recovery capacity or initialization failure falls back before any engine send; it must never retry an already-started send. Cache overflow during a run disables replay only, not live delivery. Disconnect is not abort for cached runs.
  Recovery does not automatically enable caching for other send paths.

Clients may ignore `resumeSeq`: rebuild the current answer from a fresh parser
for each connection's single recovery attempt. Buffer events that arrive
before the resume response, and discard obsolete connection attempts. Do not
append a second copy of history; reconcile by reliable message/run identity,
or use a temporary replay view and reconcile persisted history at completion.
The frontend prefers native message/round identity. Without it, it requires a
unique persisted user turn and an exact final answer inside the server run
interval (`startedAt` through the final event's `ts`). Missing timestamps,
multiple turns, conflicting IDs or incomplete history must not cause deletion.
Old adapters without these bounds retain the conservative identity-only path.

Old ticket-only adapters can return `resumed: false` for ticketless requests;
older adapters can reject the method. Fall back to history and normal sends,
never automatically repeat the prompt. Uncertain transport failures must not
trigger a second concurrent recovery subscription. Recovery remains process
local; absence of a cache record does not prove the engine is idle.
