# OpenClaw chat.send ACK

The Adapter waits for the OpenClaw `chat.send` response before acknowledging
the downstream WebSocket request. Other engines keep their immediate ACK.

```json
{
  "type": "res",
  "id": "downstream-request-id",
  "ok": true,
  "payload": {
    "accepted": true,
    "runId": "upstream-run-id",
    "status": "in_flight",
    "cached": true
  }
}
```

The Adapter preserves upstream `ok`, payload fields (including `status` and
`runId`), and `error`. `accepted` equals upstream `ok`; it does not mean the
run completed. The response `id` belongs to the downstream request.

| Upstream response | Adapter `cached` | Meaning |
| --- | --- | --- |
| `ok=true`, `status=started` | `false` | New request registered |
| `ok=true`, `status=in_flight` | `true` | Existing active run reused |
| `ok=true`, `status=ok` | `true` | Cached dispatch status replayed; no answer body |
| Failed, missing, or unknown status | `null` | Cannot infer reuse |

`cached` is **inferred from status**, based on OpenClaw 2026.5.12 semantics.
OpenClaw's internal log metadata `cached` is not transmitted in its response.
This field does not mean model prompt-cache usage, and identical prompts alone
do not prove a replay. Consumers should retain the original status and runId.

For an active run the Adapter relays subsequent events. Events arriving before
the ACK are buffered (up to 1.5 MiB per request) and filtered using the returned
runId before delivery. Known foreign sessions are excluded before counting
bytes; same-session and unidentified events are retained until ACK correlation.
Buffer overflow fails explicitly instead of silently dropping a terminal event.
Without resume, disconnects release that request's listeners without aborting
the upstream run.

With `resumeEnabled=true`, the same ACK includes `resumeTicket` and `recovery`.
The resume record adopts the actual upstream runId. Its background task and
replay cache survive disconnects, including failure to deliver the initial ACK.
Adapter-generated notices are also recorded and replayable. Failed ACKs mark
the resume record as error without generating an event, preserving prior behavior.

For ACK-aware streams, the Adapter handles ACKs as follows:

| ACK | Events after ACK |
| --- | --- |
| Successful `status=ok` | `chat` delta with a system notice, then final with the same message |
| Successful `status=in_flight` | Wait for the actual run's events; never synthesize an early final |
| Successful `status=started` | Forward normal upstream events |
| Failed ACK | Preserve the failed response and end the stream; no additional error or final event |

The notice says the request reused an existing record without starting another
execution or returning an answer body, and directs the user to the original
conversation. It explicitly does not assert that the original task finished:
dispatch completion may precede a queued model execution. Its payload carries
`cached=true` and `adapterNotice="cached_request"`, plus the original `runId`
and `sessionKey`. Delta/final contain the same cumulative assistant text, not
two separate replies. The delta also carries `deltaText` so BaaS/BCN can
accumulate incremental text; the final does not repeat an incremental delta.
No history is fetched or injected into the transcript.

BaaS and frontend can consume cached-replay notices as normal events without a
special cached-ACK branch. Failed ACKs still require their existing response-error
handling. The notice is not a replay of the model's original answer. It is cached
by Adapter resume, but does not appear in OpenClaw history after a fresh reload.

A new socket must explicitly resume or reattach to receive future events for
an active run. Reattaching after its final was already broadcast cannot recover
that event by waiting: use the existing resume cache/history recovery instead.

Existing stream consumers that do not request an ACK callback keep the prior
error/event behavior. OpenClaw WebSocket consumers now receive their ACK after
upstream acceptance (and any attachment preparation), not immediately.
