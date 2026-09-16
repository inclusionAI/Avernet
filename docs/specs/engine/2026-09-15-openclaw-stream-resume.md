# OpenClaw chat stream recovery v1

## Scope

Only an OpenClaw `chat.send` with `resumeEnabled: true` uses this contract. All
other engines and existing clients continue through the original WebSocket
path. The Adapter keeps the upstream chat task and a bounded event replay
channel alive after the initiating browser socket closes.

This version is process-local. It does not recover after an Adapter restart,
container replacement, or reconnect to a different Adapter instance. A
terminal run remains available for 180 seconds. Up to 16 runs, 20,000 events
or 16 MiB per run, and 64 MiB total can be buffered in one process. Reaching
a buffer limit disables replay for that run; it does not truncate replay and
pretend that the stream is complete. A slow subscriber is disconnected from
the recovery channel rather than slowing the upstream stream.
When the 16-run limit is reached, the earliest completed run is evicted before
a new one is accepted. If all 16 runs are still active, a new opt-in send is
rejected with `UNAVAILABLE`; existing non-opt-in sends are unaffected.

## WebSocket contract

The existing Bot connection authorization applies to every WebSocket. The
OpenClaw hello feature list additionally advertises `chat.status` and
`chat.resume`.

```json
{"type":"req","id":"1","method":"chat.send","params":{"sessionKey":"session:<uuid>:user:<user_id>","message":"...","resumeEnabled":true}}
```

An accepted opt-in send replies with `accepted: true`, `runId`,
`resumeTicket`, and `recovery: "process_local_v1"`. The prompt is sent only
once. The initiating connection receives ordinary `agent` and `chat` events;
each opt-in event also has a monotonic, run-local `resumeSeq` in its payload.
The existing upstream `seq` field is preserved.

After reconnecting with a newly authorized Bot WebSocket, the client uses the
original `sessionKey` and ticket:

```json
{"type":"req","id":"2","method":"chat.status","params":{"sessionKey":"session:<uuid>:user:<user_id>","resumeTicket":"<secret>"}}
{"type":"req","id":"3","method":"chat.resume","params":{"sessionKey":"session:<uuid>:user:<user_id>","resumeTicket":"<secret>"}}
```

`chat.status` returns `found: true` with `runId`, `state`, `resumable`,
`reason`, `lastResumeSeq`, and `bufferedEvents` when a matching run exists.
`chat.resume` returns `resumed: true` with `runId`, `state`, `replayCount`, and
`lastResumeSeq`, then sends buffered events in order followed by live events.
The replay is the complete bounded event stream, not a semantic message
snapshot. The client must deduplicate by `resumeSeq` and reconcile the
terminal event with persisted chat history. Event delivery may begin before
the `chat.resume` response frame; the client should be ready to handle both.

A missing, expired, wrong-session, or wrong ticket yields the same
`NO_ACTIVE_RUN` response. A run whose buffer exceeded a limit reports
`CACHE_LIMIT` and cannot be resumed; the client should load persisted history
and show that live recovery was unavailable. No automatic resend of the
original prompt is permitted.

## Credential boundary

`sessionKey` and `runId` are identifiers, not read authorization. The
unguessable `resumeTicket` is a bearer credential scoped to one run and
session, issued only to the accepted opt-in sender. The Adapter stores only
its SHA-256 digest. Clients must keep the ticket out of URLs, logs, analytics,
and shared storage, and remove it after the terminal event or expiry. The
new WebSocket must still pass normal Bot connection authorization. A ticket
does not grant `chat.send` or access to another session.
