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
| `ok=true`, `status=started` | `false` | New run started |
| `ok=true`, `status=in_flight` | `true` | Existing active run reused |
| `ok=true`, `status=ok` | `true` | Completed response replayed |
| Failed, missing, or unknown status | `null` | Cannot infer reuse |

`cached` is **inferred from status**, based on OpenClaw 2026.5.12 semantics.
OpenClaw's internal log metadata `cached` is not transmitted in its response.
This field does not mean model prompt-cache usage, and identical prompts alone
do not prove a replay. Consumers should retain the original status and runId.

For an active run the Adapter relays subsequent events. Events arriving before
the ACK are buffered (up to 1.5 MiB per request) and filtered using the returned
runId before delivery. Buffer overflow fails explicitly instead of silently
dropping a terminal event. Disconnects release that request's listeners; they
do not abort the upstream run.

Failed ACKs and completed-replay ACKs end this request's event subscription.
The Adapter does **not** synthesize a final event or fetch conversation history.
BaaS must handle these ACKs instead of unconditionally waiting for a new final.
This change alone does not fix BaaS's running/timeout handling.

Existing stream consumers that do not request an ACK callback keep the prior
error/event behavior. OpenClaw WebSocket consumers now receive their ACK after
upstream acceptance (and any attachment preparation), not immediately.
