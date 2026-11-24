# OpenClaw Gateway Connection Recovery

`OpenClawGatewayClient` retains the RPC, event subscription and chat-stream API.
`GatewayConnection` owns the socket, handshake and connection lifecycle.

- Connect and disconnect are serialized per client, not across the entire pool.
- A stale connected flag without a socket must initiate a new handshake.
- Failed or cancelled handshakes clean up sockets, receive tasks and stale state.
- Old receive loops cannot invalidate a replacement socket.
- Reconnection preserves event listeners, but fails requests still awaiting a
  response on the old connection. It does not resend any application request,
  including `chat.send` or `cron.run`.
- The pool returns only a connected client; failure of one slot can fall through
  to another slot.

OpenClaw cron queries distinguish absence from upstream failure: a successfully
decoded empty `cron.list` result can produce `404 Task not found`. Transport,
RPC and malformed-list failures propagate through the existing HTTP error path
(500), never as an empty result or a false 404. Other engine implementations and
successful response schemas are unchanged.

## Direct chat completion (OpenClaw 2026.5.12)

Generation still uses `chat.send`. For the validated 2026.5.12 protocol,
`chat_serialization.py` queues requests per Gateway URL and canonical session
across pool/token clients in one Adapter process. Different sessions remain
concurrent. An immediately dispatched request forwards its upstream ACK.
A queued request receives one Adapter admission ACK (`status=queued`), before
waiting for its slot, so BaaS's 120-second ACK deadline does not expire. This is
not a Gateway execution or completion acknowledgement. Later rejection, queue
timeout, or cached replay is reported through the stream, not a second ACK. `chat.abort`
cancels matching unsent requests and still forwards to Gateway for active runs.
`/stop` and `/abort` commands bypass this queue. Native collect/steer merging is replaced
by individually dispatched turns for these Adapter-managed requests.

`chat_dispatch.py` holds root lifecycle terminals and chat finals until a
read-only `agent.wait(runId, timeoutMs=0)` confirms the entire direct dispatch.
A positive wait is unsafe in this version: it can return an intermediate
lifecycle result before compaction/retry completes. Queries start at the first
terminal, retry transient query failures within the existing chat timeout, and
never resend generation. Subsequent same-run assistant text supplies the final
answer when the retry has no second chat final. Existing child/announce handling
still runs before the session slot is released.

Caller disconnect/ACK delivery failure drops delivery, not the execution
observer or session slot. Token-client retirement waits for its observers.
Explicit queued cancellation never sends that request. A queued request whose
admission was acknowledged survives consumer disconnect; an unacknowledged one
is removed when the consumer leaves. Admission is bounded to 1024
active/unconfirmed sessions and 32 requests per session; slow-consumer output
is capped at 1.5 MiB while the observer continues independently.

Limits:

- Only the validated version with advertised `agent.wait` enables this behavior;
  other versions retain the previous event handling, pending protocol validation.
- Another Adapter process, cron/channel/BCS writer, or Adapter restart bypasses
  the local queue. Native queued empty finals have no reliable next-run identity.
  They are reported as `DISPATCH_UNCONFIRMED`, never guessed from session/time.
- An unconfirmed dispatch blocks new sends for that session in this process.
  Inspect the original execution before operator recovery; restarting an Adapter
  clears only local state and does not cancel Gateway work. Do not blindly retry.
- Cached ACKs are not proof the original task ended. For queued requests already
  acknowledged locally, a cached upstream response becomes `CHAT_REQUEST_CACHED`
  rather than a second ACK or a fabricated successful answer.
  Only a recent completion verified in this process allows an ACK-only replay to
  release the slot (bounded 1024-entry, five-minute positive-result cache).

## Child run completion

Each chat stream registers accepted `sessions_spawn` results by exact child
run ID and session key. Matching child `chat` final/error/aborted frames update
that child once before parent-stream filtering; they are not parent replies.
Parent continuation never decrements the child count. This supports several
children completing in one parent run or one subsequent parent continuation.

Child completion alone never forwards a deferred parent final. A subsequent
parent continuation and final, or correlated v1 announce finals, must complete
the response. Announce IDs must match a registered child's full identity;
duplicate and foreign frames cannot settle another child. Direct children
covered by a subsequent parent final are not awaited again in mixed announce
fan-in. Bounded early terminal buffering handles fast children and ACK races.

These observations prove child execution completion and a later parent result,
not that the model consumed every queued result. After an observed child
completion, an unknown same-parent continuation is associated by session/time;
the Gateway protocol does not expose a causal child-delivery identity for that
path. Missing frames retain the existing explicit timeout behavior.
